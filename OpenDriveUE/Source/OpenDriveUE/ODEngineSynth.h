#pragma once

#include "CoreMinimal.h"
#include "Components/SynthComponent.h"
#include "ODEngineSynth.generated.h"

/**
 * Procedural engine audio v1 (wishlist #12, Arc 5.1): harmonics of the
 * cylinder firing frequency, load-shaped waveshaping ("rasp"), and a one-pole
 * muffler low-pass that opens with throttle. All DSP in C++ per the standing
 * decision; the exposed parameters are the future garage exhaust sliders
 * (Arc 5.2): Loudness, Rasp, MufflerCutoff.
 */
UCLASS(ClassGroup = Audio, meta = (BlueprintSpawnableComponent))
class OPENDRIVEUE_API UODEngineSynth : public USynthComponent
{
	GENERATED_BODY()

public:
	UODEngineSynth(const FObjectInitializer& ObjectInitializer);

	/** Game-thread telemetry in; audio thread reads atomics. */
	void SetEngineState(float Rpm, float Throttle, float Load);

	// --- exhaust character (garage sliders later) ---
	UPROPERTY(EditAnywhere, Category = "Exhaust", meta = (ClampMin = 0, ClampMax = 1))
	float Loudness = 0.7f;

	UPROPERTY(EditAnywhere, Category = "Exhaust", meta = (ClampMin = 0, ClampMax = 1))
	float Rasp = 0.45f;

	UPROPERTY(EditAnywhere, Category = "Exhaust", meta = (ClampMin = 200, ClampMax = 8000))
	float MufflerCutoff = 2600.0f;

	UPROPERTY(EditAnywhere, Category = "Engine")
	int32 Cylinders = 8;

protected:
	virtual bool Init(int32& SampleRate) override;
	virtual int32 OnGenerateAudio(float* OutAudio, int32 NumSamples) override;

private:
	std::atomic<float> TargetRpm{0.0f};
	std::atomic<float> TargetThrottle{0.0f};
	std::atomic<float> TargetLoad{0.0f};

public:
	/** Diagnostics (read by AODVehicleAudio heartbeat log). */
	std::atomic<int32> GenCalls{0};
	std::atomic<float> LastRms{0.0f};

private:

	// audio-thread state
	float SampleRateHz = 48000.0f;
	float SmRpm = 0.0f, SmThrottle = 0.0f, SmLoad = 0.0f;
	double Phase = 0.0;          // crank cycle phase [0,1)
	float LpState = 0.0f;        // muffler one-pole
	uint32 NoiseSeed = 0x2026;
};
