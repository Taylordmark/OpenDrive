#include "ODEngineSynth.h"

UODEngineSynth::UODEngineSynth(const FObjectInitializer& ObjectInitializer)
	: Super(ObjectInitializer)
{
	NumChannels = 1;
	ConfigureEngine(Cylinders, DisplacementL);
}

void UODEngineSynth::SetEngineState(float Rpm, float Throttle, float Load)
{
	TargetRpm.store(Rpm);
	TargetThrottle.store(FMath::Clamp(Throttle, 0.0f, 1.0f));
	TargetLoad.store(FMath::Clamp(Load, 0.0f, 1.0f));
}

void UODEngineSynth::ConfigureEngine(int32 InCylinders, float InDisplacementL)
{
	Cylinders = FMath::Clamp(InCylinders, 2, (int32)MaxCyl);
	DisplacementL = FMath::Clamp(InDisplacementL, 0.6f, 9.0f);

	// per-cylinder pulse strength patterns: firing intervals are even on all
	// our engines, but exhaust pulse strength alternates with the bank
	// sequence - that unevenness IS the character (half-order sidebands)
	static const float V8Cross[8] =    // cross-plane bank run L R L L R L R R
		{1.0f, 0.86f, 1.08f, 0.93f, 1.03f, 0.85f, 1.06f, 0.95f};
	static const float V6Amps[6] = {1.0f, 0.95f, 1.03f, 0.97f, 1.02f, 0.94f};
	static const float V10Amps[10] =
		{1.0f, 0.96f, 1.02f, 0.97f, 1.01f, 0.98f, 1.03f, 0.95f, 1.02f, 0.97f};

	FScopeLock Lock(&ConfigLock);
	StagedCount = Cylinders;               // 4-stroke: every cylinder per 720
	for (int32 i = 0; i < StagedCount; ++i)
	{
		StagedAngle[i] = (float)i / StagedCount;   // even spacing, frac of 720
		StagedAmp[i] =
			Cylinders == 8 ? V8Cross[i]
			: Cylinders == 6 ? V6Amps[i]
			: Cylinders == 10 ? V10Amps[i]
			: Cylinders == 12 ? 1.0f + ((i & 1) ? -0.015f : 0.015f)  // silk
			: 1.0f + ((i & 1) ? -0.05f : 0.05f);
	}
	// pulse voicing from cylinder size: big cylinders thump low + ring long
	const float CylL = DisplacementL / Cylinders;              // liters
	StagedResHz = FMath::Clamp(430.0f / FMath::Sqrt(CylL / 0.775f), 260.0f, 900.0f);
	StagedDecay = 0.0035f * FMath::Sqrt(CylL / 0.775f);
	ConfigVersion.fetch_add(1);
}

bool UODEngineSynth::Init(int32& SampleRate)
{
	SampleRateHz = (float)SampleRate;
	return true;
}

int32 UODEngineSynth::OnGenerateAudio(float* OutAudio, int32 NumSamples)
{
	GenCalls.fetch_add(1);

	if (ConfigVersion.load() != AppliedConfigVersion)
	{
		FScopeLock Lock(&ConfigLock);      // rare + tiny: engine-swap only
		AppliedConfigVersion = ConfigVersion.load();
		FireCount = StagedCount;
		FMemory::Memcpy(FireAngle, StagedAngle, sizeof(FireAngle));
		FMemory::Memcpy(FireAmp, StagedAmp, sizeof(FireAmp));
		PulseResHz = StagedResHz;
		PulseDecaySec = StagedDecay;
		NextFire = 0;
	}

	const float Rpm = TargetRpm.load();
	const float Thr = TargetThrottle.load();
	const float Load = TargetLoad.load();

	// per-buffer smoothing avoids zipper noise on telemetry steps; RPM tracks
	// tighter (0.4 ~= 50 ms) so gear shifts sound crisp instead of glided
	SmRpm += (Rpm - SmRpm) * 0.4f;
	SmThrottle += (Thr - SmThrottle) * 0.15f;
	SmLoad += (Load - SmLoad) * 0.15f;

	if (SmRpm < 100.0f || FireCount == 0)     // engine effectively off
	{
		FMemory::Memzero(OutAudio, NumSamples * sizeof(float));
		return NumSamples;
	}

	// limiter-cut pitch dip: revs audibly fall during each cut window and
	// climb back on relight (per-buffer glide, ~50 ms buffers)
	const bool bCutForSag = TargetCut.load();
	CutSag += ((bCutForSag ? CutRpmSag : 0.0f) - CutSag)
		* (bCutForSag ? 0.35f : 0.6f);
	const float CrankHz = SmRpm * (1.0f - CutSag) / 60.0f;
	const double PhaseInc = CrankHz * 0.5 / SampleRateHz;   // 720-deg cycles/sample
	const float FiringHz = CrankHz * FireCount * 0.5f;

	const float Drive = 1.0f + Rasp * 4.0f + SmLoad * 2.0f;
	const float Cutoff = MufflerCutoff * (0.45f + 0.55f * SmThrottle)
	                   * (1.0f + Rasp * 0.6f);
	const float LpCoeff = FMath::Clamp(
		1.0f - FMath::Exp(-2.0f * PI * Cutoff / SampleRateHz), 0.0f, 0.99f);
	const float Amp = Loudness * (0.22f + 0.78f * SmLoad);

	// pulse resonator coefficients (complex one-pole: decay + rotation)
	const float R = FMath::Exp(-1.0f / (PulseDecaySec * SampleRateHz));
	const float W = 2.0f * PI * PulseResHz / SampleRateHz;
	const float RotRe = R * FMath::Cos(W), RotIm = R * FMath::Sin(W);
	// combustion variability: strongest at idle/low load (the lope)
	const float JitterAmt = TimingJitter * (1.2f - SmLoad) * 0.5f;
	// pulse energy follows cylinder filling; keep some at closed throttle
	const float PulseGain = 1.7f * (0.35f + 0.65f * SmLoad);
	// ignition cut: NOT smoothed - the whole point is the hard edge. While
	// cut, firings are skipped outright (silence between braps, existing
	// pulses just ring out) and the low stack drops to a hollow turn-over.
	const bool bCutNow = TargetCut.load();
	if (bCutNow)
	{
		ResumeBoost = 2;   // first firings back get the unburnt-fuel bang
	}
	const float NoiseBurstDecay =
		FMath::Exp(-1.0f / (0.008f * SampleRateHz));   // ~8 ms pop tail

	for (int32 i = 0; i < NumSamples; ++i)
	{
		Phase720 += PhaseInc;
		if (Phase720 >= 1.0)
		{
			Phase720 -= 1.0;
			NextFire = 0;
		}

		// firing events: trigger a pulse when the crank passes each cylinder's
		// (jittered) angle - unless ignition is cut (misfire = silence)
		while (NextFire < FireCount
			&& Phase720 >= FireAngle[NextFire] + JitterAngle)
		{
			NoiseSeed = NoiseSeed * 1664525u + 1013904223u;
			const float R1 = (NoiseSeed >> 9) / 8388608.0f - 1.0f;   // [-1,1)
			NoiseSeed = NoiseSeed * 1664525u + 1013904223u;
			const float R2 = (NoiseSeed >> 9) / 8388608.0f - 1.0f;
			const float AmpJit = 1.0f + R1 * JitterAmt * 0.6f;
			JitterAngle = R2 * JitterAmt * 0.004f;   // frac of 720 (~1.7 deg max)
			if (!bCutNow)
			{
				float Boost = 1.0f;
				if (ResumeBoost > 0)
				{
					--ResumeBoost;
					Boost = 1.9f;          // the BANG on relight
					NoiseBurst = 0.4f;
				}
				PulseRe[PulseSlot] +=
					PulseGain * FireAmp[NextFire] * AmpJit * Boost;
				PulseSlot = (PulseSlot + 1) % PulsePool;
			}
			++NextFire;
		}

		// ring the pulse pool (damped resonators; sum of real parts is the
		// exhaust pulse train - firing harmonics emerge on their own)
		float S = 0.0f;
		for (int32 p = 0; p < PulsePool; ++p)
		{
			const float Re = PulseRe[p] * RotRe - PulseIm[p] * RotIm;
			PulseIm[p] = PulseRe[p] * RotIm + PulseIm[p] * RotRe;
			PulseRe[p] = Re;
			S += PulseIm[p];      // sine-phase part: pulses start at zero
		}

		// crank-order stack BELOW the firing order carries the block shake:
		// half-order exhaust unevenness + strong 1st/2nd-order, load-swelled
		const float Cp = (float)(Phase720 * 2.0);           // crank-cycle phase
		const float LowGain =
			(0.55f + 0.45f * SmLoad) * (bCutNow ? 0.4f : 1.0f);
		S += FMath::Sin(2.0f * PI * Cp * 0.5f) * 0.25f * LowGain;
		S += FMath::Sin(2.0f * PI * Cp * 1.0f) * 0.60f * LowGain;
		S += FMath::Sin(2.0f * PI * Cp * 1.5f) * 0.30f * LowGain;
		S += FMath::Sin(2.0f * PI * Cp * 2.0f) * 0.70f * LowGain;
		S += FMath::Sin(2.0f * PI * Cp * 3.0f) * 0.40f * LowGain;

		// intake/mechanical hash, louder with load; resume pops ride on top
		NoiseBurst *= NoiseBurstDecay;
		NoiseSeed = NoiseSeed * 1664525u + 1013904223u;
		const float Noise = ((int32)(NoiseSeed >> 9) / 4194304.0f - 1.0f)
		                  * (0.06f + 0.22f * SmLoad + NoiseBurst);

		// waveshape (rasp/growl), then muffler low-pass
		const float Shaped = FMath::Tanh((S * 0.28f + Noise) * Drive);
		LpState += (Shaped - LpState) * LpCoeff;

		OutAudio[i] = LpState * Amp;
	}
	(void)FiringHz;

	float Sum = 0.0f;
	for (int32 i = 0; i < NumSamples; ++i)
	{
		Sum += OutAudio[i] * OutAudio[i];
	}
	LastRms.store(FMath::Sqrt(Sum / FMath::Max(NumSamples, 1)));
	return NumSamples;
}
