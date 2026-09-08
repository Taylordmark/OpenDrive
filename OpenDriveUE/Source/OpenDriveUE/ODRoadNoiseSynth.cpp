#include "ODRoadNoiseSynth.h"

UODRoadNoiseSynth::UODRoadNoiseSynth(const FObjectInitializer& ObjectInitializer)
	: Super(ObjectInitializer)
{
	NumChannels = 1;
}

void UODRoadNoiseSynth::SetRoadState(float Speed01, float SurfaceCutoffHz,
                                     bool bGrounded)
{
	TargetSpeed.store(FMath::Clamp(Speed01, 0.0f, 1.0f));
	TargetCutoff.store(SurfaceCutoffHz);
	TargetGrounded.store(bGrounded ? 1.0f : 0.0f);
}

void UODRoadNoiseSynth::TriggerImpact(float Intensity01)
{
	// accumulate (a second hit during a thump makes a bigger thump)
	float Cur = PendingImpact.load();
	PendingImpact.store(FMath::Min(Cur + FMath::Clamp(Intensity01, 0.0f, 1.0f), 1.5f));
}

bool UODRoadNoiseSynth::Init(int32& SampleRate)
{
	SampleRateHz = (float)SampleRate;
	return true;
}

int32 UODRoadNoiseSynth::OnGenerateAudio(float* OutAudio, int32 NumSamples)
{
	const float Slew = 0.1f;
	SmSpeed += (TargetSpeed.load() - SmSpeed) * Slew;
	SmCutoff += (TargetCutoff.load() - SmCutoff) * Slew;
	SmGrounded += (TargetGrounded.load() - SmGrounded) * Slew;

	if (const float Imp = PendingImpact.exchange(0.0f); Imp > 0.0f)
	{
		ImpactEnv = FMath::Min(ImpactEnv + Imp, 1.5f);
		ImpactPhase = 0.0f;
	}

	const float TireCoeff = FMath::Clamp(
		1.0f - FMath::Exp(-2.0f * PI * SmCutoff / SampleRateHz), 0.0f, 0.99f);
	const float WindCoeff = FMath::Clamp(
		1.0f - FMath::Exp(-2.0f * PI * 900.0f / SampleRateHz), 0.0f, 0.99f);
	const float ImpCoeff = FMath::Clamp(
		1.0f - FMath::Exp(-2.0f * PI * 240.0f / SampleRateHz), 0.0f, 0.99f);
	const float TireGain = SmSpeed * SmGrounded * 0.5f;
	const float WindGain = SmSpeed * SmSpeed * 0.4f;
	const float ImpactDecay = FMath::Exp(-6.0f / SampleRateHz);   // ~170 ms

	for (int32 i = 0; i < NumSamples; ++i)
	{
		NoiseSeed = NoiseSeed * 1664525u + 1013904223u;
		const float White = (int32)(NoiseSeed >> 9) / 4194304.0f - 1.0f;

		// tire bed: low-passed noise colored by surface
		TireLp += (White - TireLp) * TireCoeff;

		// wind: high-passed noise (white minus its low-pass)
		WindLp += (White - WindLp) * WindCoeff;
		const float Wind = White - WindLp;

		// impact: decaying low thump (filtered noise + 70 Hz body knock)
		float Impact = 0.0f;
		if (ImpactEnv > 0.001f)
		{
			ImpactLp += (White - ImpactLp) * ImpCoeff;
			ImpactPhase += 70.0f / SampleRateHz;
			Impact = (ImpactLp * 0.7f
				+ FMath::Sin(2.0f * PI * ImpactPhase) * 0.5f) * ImpactEnv;
			ImpactEnv *= ImpactDecay;
		}

		OutAudio[i] = (TireLp * TireGain + Wind * WindGain + Impact) * Volume * VolumeScale;
	}
	return NumSamples;
}
