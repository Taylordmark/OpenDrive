#pragma once

#include "CoreMinimal.h"
#include "Components/SynthComponent.h"
#include "ODEngineSynth.generated.h"

/**
 * Procedural engine audio v2 (Arc 5.1 upgrade, 2026-07-18): pulse-train
 * synthesis. Each cylinder firing (once per 720 deg of crank) triggers a
 * damped exhaust-pulse resonator at its crank angle. Character comes from
 * physical-ish parameters instead of a fixed harmonic recipe:
 *
 *  - firing table: per-cylinder crank angle + pulse strength. A cross-plane
 *    V8's uneven bank sequence gets per-pulse strength variation (the
 *    burble); a V12 fires nearly perfectly even (the silk).
 *  - displacement: liters spread over the cylinders sets each pulse's
 *    resonant frequency and ring time - big cylinders thump low and ring
 *    long, small ones bark high and die fast.
 *  - timing jitter: cycle-to-cycle combustion variability (angle + strength
 *    noise), strongest at idle/low load - the idle lope.
 *
 * The v1 crank-order low stack (block shake), load-shaped tanh rasp, and
 * throttle-opened muffler low-pass survive unchanged downstream of the pulse
 * train. Exposed params stay the future garage sliders (Arc 5.2): Loudness,
 * Rasp, MufflerCutoff (+ engine params for the engine swap menu).
 */
UCLASS(ClassGroup = Audio, meta = (BlueprintSpawnableComponent))
class OPENDRIVEUE_API UODEngineSynth : public USynthComponent
{
	GENERATED_BODY()

public:
	UODEngineSynth(const FObjectInitializer& ObjectInitializer);

	/** Game-thread telemetry in; audio thread reads atomics. */
	void SetEngineState(float Rpm, float Throttle, float Load);

	/** Raw ignition cut (limiter bounce / shift cut) - gates firings HARD:
	 *  silence between braps, and a resume pop when combustion returns. */
	void SetIgnitionCut(bool bCut) { TargetCut.store(bCut); }

	/** Rebuild the firing table + pulse voicing (engine swap menu). */
	void ConfigureEngine(int32 InCylinders, float InDisplacementL);

	// --- exhaust character (garage sliders later) ---
	UPROPERTY(EditAnywhere, Category = "Exhaust", meta = (ClampMin = 0, ClampMax = 1))
	float Loudness = 0.7f;

	UPROPERTY(EditAnywhere, Category = "Exhaust", meta = (ClampMin = 0, ClampMax = 1))
	float Rasp = 0.45f;

	UPROPERTY(EditAnywhere, Category = "Exhaust", meta = (ClampMin = 200, ClampMax = 8000))
	float MufflerCutoff = 2600.0f;

	// --- engine character ---
	UPROPERTY(EditAnywhere, Category = "Engine", meta = (ClampMin = 2, ClampMax = 12))
	int32 Cylinders = 8;

	UPROPERTY(EditAnywhere, Category = "Engine", meta = (ClampMin = 0.6, ClampMax = 9))
	float DisplacementL = 6.2f;

	/** Combustion variability: 0 = metronome, 1 = cammy idle lope. */
	UPROPERTY(EditAnywhere, Category = "Engine", meta = (ClampMin = 0, ClampMax = 1))
	float TimingJitter = 0.35f;

	/** Audible rpm droop while ignition is cut (limiter bounce pitch dip). */
	UPROPERTY(EditAnywhere, Category = "Engine", meta = (ClampMin = 0, ClampMax = 0.2))
	float CutRpmSag = 0.08f;

protected:
	virtual bool Init(int32& SampleRate) override;
	virtual int32 OnGenerateAudio(float* OutAudio, int32 NumSamples) override;

private:
	std::atomic<float> TargetRpm{0.0f};
	std::atomic<float> TargetThrottle{0.0f};
	std::atomic<float> TargetLoad{0.0f};
	std::atomic<bool> TargetCut{false};

public:
	/** Diagnostics (read by AODVehicleAudio heartbeat log). */
	std::atomic<int32> GenCalls{0};
	std::atomic<float> LastRms{0.0f};

private:
	static constexpr int32 MaxCyl = 12;
	static constexpr int32 PulsePool = 8;

	// firing-table config: game thread stages under ConfigLock and bumps
	// ConfigVersion; the audio thread copies when it notices (rare, tiny)
	FCriticalSection ConfigLock;
	std::atomic<int32> ConfigVersion{0};
	int32 AppliedConfigVersion = -1;
	float StagedAngle[MaxCyl];      // firing angles, fraction of 720 [0,1)
	float StagedAmp[MaxCyl];        // per-cylinder pulse strength
	int32 StagedCount = 0;
	float StagedResHz = 480.0f;     // pulse resonant frequency
	float StagedDecay = 0.0035f;    // pulse ring time constant, seconds

	// audio-thread copies
	float FireAngle[MaxCyl];
	float FireAmp[MaxCyl];
	int32 FireCount = 0;
	float PulseResHz = 480.0f;
	float PulseDecaySec = 0.0035f;

	// audio-thread state
	float SampleRateHz = 48000.0f;
	float SmRpm = 0.0f, SmThrottle = 0.0f, SmLoad = 0.0f;
	double Phase720 = 0.0;          // 720-deg engine cycle phase [0,1)
	int32 NextFire = 0;             // next firing-table index
	float JitterAngle = 0.0f;       // this-firing jitter (frac of 720)
	float PulseRe[PulsePool] = {};  // complex one-pole resonator pool
	float PulseIm[PulsePool] = {};
	int32 PulseSlot = 0;
	int32 ResumeBoost = 0;          // firings left to over-fire after a cut
	float NoiseBurst = 0.0f;        // resume-pop noise envelope
	float CutSag = 0.0f;            // current rpm droop fraction [0,CutRpmSag]
	float LpState = 0.0f;           // muffler one-pole
	uint32 NoiseSeed = 0x2026;
};
