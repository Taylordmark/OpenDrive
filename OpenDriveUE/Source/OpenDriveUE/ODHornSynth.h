#pragma once

#include "CoreMinimal.h"
#include "Components/SynthComponent.h"
#include "ODHornSynth.generated.h"

/**
 * Procedural car horn (fun-features #8a): two detuned saw tones a major third
 * apart (~400/500 Hz), soft-clipped and through a one-pole low-pass, with a
 * short attack/release so a tap still sounds like a beep. No audio asset.
 * SetVoice() picks pitch, interval and brightness from a hash (the vehicle
 * def's name), so each car model sounds its own.
 */
UCLASS(ClassGroup = Audio)
class OPENDRIVEUE_API UODHornSynth : public USynthComponent
{
	GENERATED_BODY()

public:
	UODHornSynth(const FObjectInitializer& ObjectInitializer);

	void SetOn(bool bOn) { bTargetOn.store(bOn); }
	void SetVoice(uint32 Hash);
	void SetVolumeScale(float Scale) { VolumeScale.store(FMath::Clamp(Scale, 0.0f, 1.0f)); }

protected:
	virtual bool Init(int32& SampleRate) override;
	virtual int32 OnGenerateAudio(float* OutAudio, int32 NumSamples) override;

private:
	std::atomic<bool> bTargetOn{false};
	std::atomic<float> VolumeScale{1.0f};
	std::atomic<float> BaseHz{410.0f}, Ratio{1.25f}, CutoffHz{2400.0f};

	float SampleRateHz = 48000.0f;
	float Env = 0.0f, Ph1 = 0.0f, Ph2 = 0.0f, Lp = 0.0f;
};
