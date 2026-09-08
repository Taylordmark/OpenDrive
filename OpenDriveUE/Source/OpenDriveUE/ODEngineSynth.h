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
/**
 * Per-car/per-preset exhaust character (Arc 5.2, 2026-09-08). Gathers the
 * garage-facing exhaust params in one place: the three already exposed on
 * the synth (Loudness/Rasp/MufflerCutoff) plus the new lift-off pop knobs.
 * UODVehicleDef carries no audio fields today (checked - engine params live
 * on the pause/main-menu Engine table instead, see ODMainMenu.cpp's
 * Engines[]), so this is preset-level rather than per-car; AODMainMenu owns
 * the live preset table (ExhaustPresets[]) and per-slider overrides.
 */
USTRUCT(BlueprintType)
struct FODExhaustConfig
{
	GENERATED_BODY()

	UPROPERTY(EditAnywhere)
	float Loudness = 0.7f;

	UPROPERTY(EditAnywhere)
	float Rasp = 0.45f;

	UPROPERTY(EditAnywhere)
	float MufflerCutoff = 2600.0f;

	/** Probability [0,1] that a given firing is skipped (misfire) while a
	 *  lift-off pop window is active - the overrun crackle. 0 = none
	 *  (Stock), higher = denser pops (Open). */
	UPROPERTY(EditAnywhere)
	float PopProbability = 0.0f;

	/** How long a pop window lasts after a throttle lift at rpm, seconds.
	 *  Preset-level tuning constant (not a garage slider), same tier as
	 *  LimiterBounceHz/CutRpmSag on UODVehicleMovement - Taylor's ear, not a
	 *  player-facing dial. */
	UPROPERTY(EditAnywhere)
	float PopWindowSec = 0.4f;

	/** Shape of the pop-probability decay across the window (higher = drops
	 *  off faster after the lift). Same tuning-constant tier as PopWindowSec. */
	UPROPERTY(EditAnywhere)
	float PopDecay = 1.5f;
};

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

	/** Garage-facing (Arc 5.2): push a whole exhaust config at once (preset
	 *  pick or slider edit). Plain-float writes, same convention as the
	 *  existing Loudness/Rasp/MufflerCutoff/TimingJitter/CutRpmSag properties
	 *  below - game thread writes at menu-adjust rate (not per-buffer), audio
	 *  thread reads each buffer; no atomics, matching that existing pattern. */
	void SetExhaustConfig(const FODExhaustConfig& Config);
	FODExhaustConfig GetExhaustConfig() const;

	/** Settings-shell-facing (Arc 4.4): Master*Vehicle volume, multiplied
	 *  into the synth's single final-sample write in OnGenerateAudio() (same
	 *  plain-float-write convention as Loudness/Rasp/MufflerCutoff above -
	 *  game thread writes at menu-adjust rate, audio thread reads each
	 *  buffer, no atomics needed). Default 1.0 = no change from today's
	 *  behavior. */
	void SetVolumeScale(float Scale) { VolumeScale = FMath::Clamp(Scale, 0.0f, 1.0f); }

	// --- exhaust character (garage sliders later) ---
	UPROPERTY(EditAnywhere, Category = "Exhaust", meta = (ClampMin = 0, ClampMax = 1))
	float Loudness = 0.7f;

	/** Master*Vehicle volume from the settings shell - see SetVolumeScale(). */
	UPROPERTY(EditAnywhere, Category = "Audio", meta = (ClampMin = 0, ClampMax = 1))
	float VolumeScale = 1.0f;

	UPROPERTY(EditAnywhere, Category = "Exhaust", meta = (ClampMin = 0, ClampMax = 1))
	float Rasp = 0.45f;

	UPROPERTY(EditAnywhere, Category = "Exhaust", meta = (ClampMin = 200, ClampMax = 8000))
	float MufflerCutoff = 2600.0f;

	/** Lift-off pop/burble (Arc 5.2): probability [0,1] a firing is skipped
	 *  while a pop window is open. Reuses SetIgnitionCut()'s relight-bang
	 *  machinery (ResumeBoost/NoiseBurst, 1.9x amplitude pop) per skipped
	 *  firing instead of a parallel mechanism - see OnGenerateAudio(). */
	UPROPERTY(EditAnywhere, Category = "Exhaust", meta = (ClampMin = 0, ClampMax = 1))
	float PopProbability = 0.0f;

	/** Pop-window length after a detected throttle lift, seconds. Preset-
	 *  level tuning constant, not a garage slider (see FODExhaustConfig). */
	UPROPERTY(EditAnywhere, Category = "Exhaust", meta = (ClampMin = 0.05, ClampMax = 3))
	float PopWindowSec = 0.4f;

	/** Decay shape exponent for pop probability across the window. Preset-
	 *  level tuning constant, not a garage slider. */
	UPROPERTY(EditAnywhere, Category = "Exhaust", meta = (ClampMin = 0.1, ClampMax = 5))
	float PopDecay = 1.5f;

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

	// lift-off pop/burble (Arc 5.2): audio-thread-only state, detected from
	// the raw (unsmoothed) throttle target so a real snap-off registers even
	// though SmThrottle itself glides. Design constants below match the
	// "overrun crackle" window this is meant to model, same tier as the
	// pulse/muffler constants above them - not garage-exposed.
	float PrevThrottleForPop = 0.0f;
	float PopWindowRemainSec = 0.0f;
	static constexpr float PopLiftHighThrottle = 0.35f;  // was above this...
	static constexpr float PopLiftLowThrottle = 0.15f;   // ...now below this = a lift
	static constexpr float PopMinRpm = 2200.0f;          // floor so idle blips don't pop

	/** Running count of pop-mechanism skips in a row (NOT counting real
	 *  ignition-cut silence, which is a separate deliberate mechanism - see
	 *  bCutNow). Reset to 0 by any firing that actually sounds. Post-mortem
	 *  fix (2026-09-08, corrects 5d1e594): defense in depth alongside the R1
	 *  range-bug fix below - caps how long the pop roll can go the audio-thread
	 *  RNG's way. */
	int32 ConsecutivePopSkips = 0;
	/** Hard ceiling on ConsecutivePopSkips: after this many skipped firings in
	 *  a row, the next firing always fires (with its relight bang), so no
	 *  probability setting can produce a sustained engine dropout. Design
	 *  constant, same tier as PopMinRpm above - not garage-exposed. */
	static constexpr int32 MaxConsecutivePopSkips = 3;
};
