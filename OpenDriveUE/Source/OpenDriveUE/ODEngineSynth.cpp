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

	// per-buffer smoothing avoids zipper noise on telemetry steps
	const float Slew = 0.15f;
	SmRpm += (Rpm - SmRpm) * Slew;
	SmThrottle += (Thr - SmThrottle) * Slew;
	SmLoad += (Load - SmLoad) * Slew;

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

		// harmonic stack of the firing frequency (saw-ish), plus half-order
		// "crank rumble" for V8 unevenness
		const float Fp = (float)Phase * FiringsPerCrank;    // firing-cycle phase
		float S = 0.0f;
		float HGain = 1.0f;
		for (int32 h = 1; h <= 6; ++h)
		{
			// skip harmonics above ~nyquist/3 at high rpm
			if (FiringHz * h > SampleRateHz * 0.33f)
			{
				break;
			}
			S += FMath::Sin(2.0f * PI * Fp * h) * HGain;
			HGain *= 0.62f;
		}
		S += FMath::Sin(2.0f * PI * (float)Phase * 2.0f) * 0.5f;   // 2nd crank order
		S += FMath::Sin(2.0f * PI * (float)Phase) * 0.35f;         // 1st crank order

		// intake/mechanical hash, louder with load
		NoiseSeed = NoiseSeed * 1664525u + 1013904223u;
		const float Noise = ((int32)(NoiseSeed >> 9) / 4194304.0f - 1.0f)
		                  * (0.06f + 0.22f * SmLoad);

		// waveshape (rasp/growl), then muffler low-pass
		const float Shaped = FMath::Tanh((S * 0.35f + Noise) * Drive);
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
