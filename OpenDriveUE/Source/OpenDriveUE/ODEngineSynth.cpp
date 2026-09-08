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

void UODEngineSynth::SetExhaustConfig(const FODExhaustConfig& Config)
{
	// plain-float writes, matching the existing Loudness/Rasp/MufflerCutoff/
	// TimingJitter/CutRpmSag convention: menu-adjust-rate, not per-buffer, so
	// no atomics/ConfigLock needed (same reasoning as those properties).
	Loudness = FMath::Clamp(Config.Loudness, 0.0f, 1.0f);
	Rasp = FMath::Clamp(Config.Rasp, 0.0f, 1.0f);
	MufflerCutoff = FMath::Clamp(Config.MufflerCutoff, 200.0f, 8000.0f);
	PopProbability = FMath::Clamp(Config.PopProbability, 0.0f, 1.0f);
	PopWindowSec = FMath::Clamp(Config.PopWindowSec, 0.05f, 3.0f);
	PopDecay = FMath::Clamp(Config.PopDecay, 0.1f, 5.0f);
}

FODExhaustConfig UODEngineSynth::GetExhaustConfig() const
{
	FODExhaustConfig Config;
	Config.Loudness = Loudness;
	Config.Rasp = Rasp;
	Config.MufflerCutoff = MufflerCutoff;
	Config.PopProbability = PopProbability;
	Config.PopWindowSec = PopWindowSec;
	Config.PopDecay = PopDecay;
	return Config;
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

	// lift-off pop/burble (Arc 5.2): detect a snap-off-the-throttle at revs
	// using the RAW (unsmoothed) throttle target, so a quick lift registers
	// even though SmThrottle itself glides - same "hard edge on the trigger"
	// idea as bCutNow above. A real ignition cut (limiter/shift) takes
	// priority and doesn't also open a pop window. Once open, the window
	// decays per-buffer (buffers are short, ~tens of ms, same granularity as
	// CutSag's per-buffer glide above).
	if (PrevThrottleForPop > PopLiftHighThrottle && Thr < PopLiftLowThrottle
		&& SmRpm > PopMinRpm && !bCutNow)
	{
		PopWindowRemainSec = PopWindowSec;
	}
	PrevThrottleForPop = Thr;
	PopWindowRemainSec = FMath::Max(0.0f, PopWindowRemainSec - NumSamples / SampleRateHz);

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
			// NOTE (post-mortem fix, 2026-09-08): the >>9 shift leaves a
			// 23-bit value in [0, 2^23-1], so the correct divisor to map that
			// onto [0,1) is 2^22 = 4194304.0f (as the Noise line below
			// already does with its (int32) cast). 5d1e594 divided by
			// 8388608.0f = 2^23 instead, so R1/R2 were silently confined to
			// [-1, 0) - never positive. That is the actual root cause of the
			// Arc 5.2 exhaust-pop dropout bug (see MaxConsecutivePopSkips
			// comment below for the mechanism), not RNG "correlation": with
			// PopRoll = (R1+1)*0.5 always < 0.5, any EffP >= 0.5 (Open starts
			// at PopProbability=0.75) skipped every firing with certainty
			// until the decaying EffP fell under 0.5, producing a long
			// deterministic all-skip run - not a probabilistic one.
			NoiseSeed = NoiseSeed * 1664525u + 1013904223u;
			const float R1 = (NoiseSeed >> 9) / 4194304.0f - 1.0f;   // [-1,1)
			NoiseSeed = NoiseSeed * 1664525u + 1013904223u;
			const float R2 = (NoiseSeed >> 9) / 4194304.0f - 1.0f;
			const float AmpJit = 1.0f + R1 * JitterAmt * 0.6f;
			JitterAngle = R2 * JitterAmt * 0.004f;   // frac of 720 (~1.7 deg max)
			// lift-off pop/burble (Arc 5.2): while a pop window is open,
			// probabilistically skip this firing outright too - the same
			// "misfire = silence" treatment as bCutNow, just per-firing
			// instead of for the whole cut. Post-mortem fix: draws its OWN
			// LCG advance (R3) instead of reusing R1 - even with R1's range
			// now fixed above, sharing a draw across two unrelated uses is
			// bad practice (a future change to one consumer silently biases
			// the other), and it keeps this roll's distribution independent
			// of AmpJit/JitterAngle placement in the stream.
			bool bSkipFiring = bCutNow;
			if (!bSkipFiring && PopWindowRemainSec > 0.0f && PopProbability > 0.0f)
			{
				const float Frac = FMath::Clamp(PopWindowRemainSec / PopWindowSec, 0.0f, 1.0f);
				const float EffP = PopProbability * FMath::Pow(Frac, PopDecay);
				NoiseSeed = NoiseSeed * 1664525u + 1013904223u;
				const float R3 = (NoiseSeed >> 9) / 4194304.0f - 1.0f;   // [-1,1)
				const float PopRoll = (R3 + 1.0f) * 0.5f;   // [-1,1) -> [0,1)
				// MaxConsecutivePopSkips (ODEngineSynth.h): hard cap,
				// independent of the RNG fix above - defense in depth so no
				// probability/decay setting can ever produce a sustained
				// dropout instead of crackle, regardless of what the RNG
				// does. Only pop-mechanism skips count; a real ignition cut
				// (bCutNow, handled above) is deliberate silence and doesn't
				// touch this counter.
				if (PopRoll < EffP && ConsecutivePopSkips < MaxConsecutivePopSkips)
				{
					bSkipFiring = true;
					++ConsecutivePopSkips;
					// next real firing gets the relight bang - THAT bang, on
					// an otherwise-normal-throttle firing, IS the audible pop.
					ResumeBoost = FMath::Max(ResumeBoost, 1);
				}
			}
			if (!bSkipFiring)
			{
				// any firing that actually sounds - whether the pop roll
				// missed, the cap forced it, or no pop window was even open -
				// clears the streak. Deliberately NOT scoped to the window-
				// open branch above: a window closing mid-streak must not
				// carry a partial count into the next lift's window.
				ConsecutivePopSkips = 0;
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

		OutAudio[i] = LpState * Amp * VolumeScale;
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
