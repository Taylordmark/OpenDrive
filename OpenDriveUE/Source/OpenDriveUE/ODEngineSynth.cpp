#include "ODEngineSynth.h"

UODEngineSynth::UODEngineSynth(const FObjectInitializer& ObjectInitializer)
	: Super(ObjectInitializer)
{
	NumChannels = 1;
}

void UODEngineSynth::SetEngineState(float Rpm, float Throttle, float Load)
{
	TargetRpm.store(Rpm);
	TargetThrottle.store(FMath::Clamp(Throttle, 0.0f, 1.0f));
	TargetLoad.store(FMath::Clamp(Load, 0.0f, 1.0f));
}

bool UODEngineSynth::Init(int32& SampleRate)
{
	SampleRateHz = (float)SampleRate;
	return true;
}

int32 UODEngineSynth::OnGenerateAudio(float* OutAudio, int32 NumSamples)
{
	GenCalls.fetch_add(1);
	const float Rpm = TargetRpm.load();
	const float Thr = TargetThrottle.load();
	const float Load = TargetLoad.load();

	// per-buffer smoothing avoids zipper noise on telemetry steps; RPM tracks
	// tighter (0.4 ~= 50 ms) so gear shifts sound crisp instead of glided
	SmRpm += (Rpm - SmRpm) * 0.4f;
	SmThrottle += (Thr - SmThrottle) * 0.15f;
	SmLoad += (Load - SmLoad) * 0.15f;

	if (SmRpm < 100.0f)     // engine effectively off
	{
		FMemory::Memzero(OutAudio, NumSamples * sizeof(float));
		return NumSamples;
	}

	// 4-stroke: Cylinders/2 firings per crank revolution
	const float CrankHz = SmRpm / 60.0f;
	const float FiringHz = CrankHz * Cylinders * 0.5f;
	const double PhaseInc = CrankHz / SampleRateHz;         // crank cycles/sample

	const float Drive = 1.0f + Rasp * 4.0f + SmLoad * 2.0f;
	const float Cutoff = MufflerCutoff * (0.45f + 0.55f * SmThrottle)
	                   * (1.0f + Rasp * 0.6f);
	const float LpCoeff = FMath::Clamp(
		1.0f - FMath::Exp(-2.0f * PI * Cutoff / SampleRateHz), 0.0f, 0.99f);
	const float Amp = Loudness * (0.22f + 0.78f * SmLoad);
	const float FiringsPerCrank = Cylinders * 0.5f;

	for (int32 i = 0; i < NumSamples; ++i)
	{
		Phase += PhaseInc;
		if (Phase >= 1.0)
		{
			Phase -= 1.0;
		}

		// crank-order stack BELOW the firing order carries the V8 chest:
		// half-order exhaust unevenness + strong 1st/2nd-order shake, all
		// swelling with load so cruise stays smooth and WOT gets deep
		const float Cp = (float)Phase;                      // crank-cycle phase
		const float LowGain = 0.55f + 0.45f * SmLoad;
		float S = 0.0f;
		S += FMath::Sin(2.0f * PI * Cp * 0.5f) * 0.25f * LowGain;
		S += FMath::Sin(2.0f * PI * Cp * 1.0f) * 0.60f * LowGain;
		S += FMath::Sin(2.0f * PI * Cp * 1.5f) * 0.30f * LowGain;
		S += FMath::Sin(2.0f * PI * Cp * 2.0f) * 0.70f * LowGain;
		S += FMath::Sin(2.0f * PI * Cp * 3.0f) * 0.40f * LowGain;

		// harmonic stack of the firing frequency (the "note"), decay tightened
		// 0.62 -> 0.55 so the top end buzzes less
		const float Fp = Cp * FiringsPerCrank;              // firing-cycle phase
		float HGain = 1.0f;
		for (int32 h = 1; h <= 6; ++h)
		{
			// skip harmonics above ~nyquist/3 at high rpm
			if (FiringHz * h > SampleRateHz * 0.33f)
			{
				break;
			}
			S += FMath::Sin(2.0f * PI * Fp * h) * HGain;
			HGain *= 0.55f;
		}

		// intake/mechanical hash, louder with load
		NoiseSeed = NoiseSeed * 1664525u + 1013904223u;
		const float Noise = ((int32)(NoiseSeed >> 9) / 4194304.0f - 1.0f)
		                  * (0.06f + 0.22f * SmLoad);

		// waveshape (rasp/growl), then muffler low-pass; 0.28 keeps the tanh
		// drive where it was before the low-order stack fattened S
		const float Shaped = FMath::Tanh((S * 0.28f + Noise) * Drive);
		LpState += (Shaped - LpState) * LpCoeff;

		OutAudio[i] = LpState * Amp;
	}

	float Sum = 0.0f;
	for (int32 i = 0; i < NumSamples; ++i)
	{
		Sum += OutAudio[i] * OutAudio[i];
	}
	LastRms.store(FMath::Sqrt(Sum / FMath::Max(NumSamples, 1)));
	return NumSamples;
}
