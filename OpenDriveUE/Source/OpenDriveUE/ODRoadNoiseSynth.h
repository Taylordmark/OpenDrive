#pragma once

#include "CoreMinimal.h"
#include "Components/SynthComponent.h"
#include "ODRoadNoiseSynth.generated.h"

/**
 * Surface + wind + impact noise voice (Arc 5.1): filtered noise whose color
 * follows the contact surface (asphalt hiss, dirt/grass rumble), wind rising
 * with speed^2, exponentially decaying impact thumps from collisions, and
 * a gravel crunch (random stone ticks, rate ~ speed) on unpaved roads.
 */
UCLASS(ClassGroup = Audio, meta = (BlueprintSpawnableComponent))
class OPENDRIVEUE_API UODRoadNoiseSynth : public USynthComponent
{
	GENERATED_BODY()

public:
	UODRoadNoiseSynth(const FObjectInitializer& ObjectInitializer);

	/** Speed01: fraction of ~top speed. SurfaceCutoffHz colors the tire bed.
	 *  Crunch01: gravel crunch layer level (PM_Gravel 1, PM_DirtRoad less). */
	void SetRoadState(float Speed01, float SurfaceCutoffHz, bool bGrounded,
		float Crunch01 = 0.0f);

	void TriggerImpact(float Intensity01);

	UPROPERTY(EditAnywhere, Category = "Road", meta = (ClampMin = 0, ClampMax = 1))
	float Volume = 0.55f;

	/** Gravel crunch: stone ticks per second at Speed01 = 1, and their level. */
	UPROPERTY(EditAnywhere, Category = "Road", meta = (ClampMin = 0))
	float CrunchRateHz = 900.0f;

	UPROPERTY(EditAnywhere, Category = "Road", meta = (ClampMin = 0, ClampMax = 2))
	float CrunchGain = 0.6f;

	/** Master*Vehicle volume from the settings shell (Arc 4.4) - multiplied
	 *  in alongside Volume above at OnGenerateAudio()'s single output write,
	 *  same convention as UODEngineSynth::VolumeScale. Default 1.0 = no
	 *  change from today's behavior. */
	UPROPERTY(EditAnywhere, Category = "Audio", meta = (ClampMin = 0, ClampMax = 1))
	float VolumeScale = 1.0f;
	void SetVolumeScale(float Scale) { VolumeScale = FMath::Clamp(Scale, 0.0f, 1.0f); }

protected:
	virtual bool Init(int32& SampleRate) override;
	virtual int32 OnGenerateAudio(float* OutAudio, int32 NumSamples) override;

private:
	std::atomic<float> TargetSpeed{0.0f};
	std::atomic<float> TargetCutoff{3000.0f};
	std::atomic<float> TargetGrounded{1.0f};
	std::atomic<float> TargetCrunch{0.0f};
	std::atomic<float> PendingImpact{0.0f};

	float SampleRateHz = 48000.0f;
	float SmSpeed = 0.0f, SmCutoff = 3000.0f, SmGrounded = 1.0f, SmCrunch = 0.0f;
	float CrunchEnv = 0.0f, CrunchLp = 0.0f;
	float TireLp = 0.0f, WindLp = 0.0f, ImpactLp = 0.0f;
	float ImpactEnv = 0.0f;
	float ImpactPhase = 0.0f;
	uint32 NoiseSeed = 0x0715;
};
