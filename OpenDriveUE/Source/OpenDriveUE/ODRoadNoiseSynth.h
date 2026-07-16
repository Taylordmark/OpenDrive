#pragma once

#include "CoreMinimal.h"
#include "Components/SynthComponent.h"
#include "ODRoadNoiseSynth.generated.h"

/**
 * Surface + wind + impact noise voice (Arc 5.1): filtered noise whose color
 * follows the contact surface (asphalt hiss, dirt/grass rumble), wind rising
 * with speed^2, and exponentially decaying impact thumps from collisions.
 */
UCLASS(ClassGroup = Audio, meta = (BlueprintSpawnableComponent))
class OPENDRIVEUE_API UODRoadNoiseSynth : public USynthComponent
{
	GENERATED_BODY()

public:
	UODRoadNoiseSynth(const FObjectInitializer& ObjectInitializer);

	/** Speed01: fraction of ~top speed. SurfaceCutoffHz colors the tire bed. */
	void SetRoadState(float Speed01, float SurfaceCutoffHz, bool bGrounded);

	void TriggerImpact(float Intensity01);

	UPROPERTY(EditAnywhere, Category = "Road", meta = (ClampMin = 0, ClampMax = 1))
	float Volume = 0.55f;

protected:
	virtual bool Init(int32& SampleRate) override;
	virtual int32 OnGenerateAudio(float* OutAudio, int32 NumSamples) override;

private:
	std::atomic<float> TargetSpeed{0.0f};
	std::atomic<float> TargetCutoff{3000.0f};
	std::atomic<float> TargetGrounded{1.0f};
	std::atomic<float> PendingImpact{0.0f};

	float SampleRateHz = 48000.0f;
	float SmSpeed = 0.0f, SmCutoff = 3000.0f, SmGrounded = 1.0f;
	float TireLp = 0.0f, WindLp = 0.0f, ImpactLp = 0.0f;
	float ImpactEnv = 0.0f;
	float ImpactPhase = 0.0f;
	uint32 NoiseSeed = 0x0715;
};
