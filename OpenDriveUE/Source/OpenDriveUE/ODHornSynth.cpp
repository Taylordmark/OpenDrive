#include "ODHornSynth.h"

UODHornSynth::UODHornSynth(const FObjectInitializer& ObjectInitializer)
	: Super(ObjectInitializer)
{
	NumChannels = 1;
}

void UODHornSynth::SetVoice(uint32 Hash)
{
	auto Unit = [&Hash]() { Hash = Hash * 1664525u + 1013904223u; return (Hash >> 8) / 16777216.0f; };
	BaseHz.store(340.0f + 160.0f * Unit());        // 340..500 Hz low note
	Ratio.store(Unit() < 0.7f ? 1.25f : 1.19f);     // major or minor third
	CutoffHz.store(1600.0f + 2000.0f * Unit());     // dull .. brassy
}

bool UODHornSynth::Init(int32& SampleRate)
{
	SampleRateHz = (float)SampleRate;
	return true;
}

int32 UODHornSynth::OnGenerateAudio(float* OutAudio, int32 NumSamples)
{
	const float Target = bTargetOn.load() ? 1.0f : 0.0f;
	if (Target == 0.0f && Env < 1.0e-4f)
	{
		FMemory::Memzero(OutAudio, NumSamples * sizeof(float));
		Env = Lp = 0.0f;
		return NumSamples;
	}
	// ~6 ms attack, ~40 ms release
	const float Rate = 1.0f - FMath::Exp(-1.0f / ((Target > Env ? 0.006f : 0.04f) * SampleRateHz));
	const float F1 = BaseHz.load() / SampleRateHz, F2 = F1 * Ratio.load() * 1.004f;   // slight beat
	const float LpCoeff = 1.0f - FMath::Exp(-2.0f * PI * CutoffHz.load() / SampleRateHz);
	const float Gain = 0.35f * VolumeScale.load();
	for (int32 i = 0; i < NumSamples; ++i)
	{
		Env += (Target - Env) * Rate;
		Ph1 = FMath::Frac(Ph1 + F1);
		Ph2 = FMath::Frac(Ph2 + F2);
		const float Saw = (Ph1 * 2.0f - 1.0f) + (Ph2 * 2.0f - 1.0f);
		Lp += (FMath::Tanh(Saw * 1.5f) - Lp) * LpCoeff;   // the horn's driver honk, then its trumpet
		OutAudio[i] = Lp * Env * Gain;
	}
	return NumSamples;
}
