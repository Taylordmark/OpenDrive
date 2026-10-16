#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODSeasonController.generated.h"

class UHierarchicalInstancedStaticMeshComponent;
class UMaterialInstanceDynamic;
class UMaterialInterface;
class AODTerrainManager;
class AODWaterManager;
class AODForestManager;
class AODWeatherController;
class AODRoadManager;

/**
 * Seasons feature, first half (2026-09-07): a continuous, looping season
 * CLOCK - not a picker - that drives five blend parameters (FallBlend,
 * SnowCover, SnowfallIntensity, FreezeBlend, SpringGreen) into the ground/
 * water/foliage MIDs those managers already own (AODTerrainManager::
 * GetGroundMID / AODWaterManager::GetWaterMID / AODForestManager::
 * GetFoliageSeasonMID), plus a simple runtime falling-snow effect this actor
 * owns directly (since Weather W1 its level comes from AODWeatherController,
 * not the clock - see SnowfallIntensity). Mirrors AODDayNightController/AODAtmosphereController's
 * "runtime-spawned singleton controller, find-or-spawn from AODGameMode"
 * shape - see AODGameMode::EnsureSeasonController.
 *
 * Full "year" = 4 real-time hours (14400 s), looping summer -> fall ->
 * winter -> spring -> summer: each season holds stable for HoldSeconds
 * (default 2700 s / 45 min), then transitions into the next over
 * TransitionSeconds (default 900 s / 15 min) - 4 * (2700 + 900) = 14400,
 * exact. Every parameter is a smoothstep-eased ramp across its transition
 * window (see ComputeParamsForClock) so nothing snaps - a driver watching
 * one spot should see change creep in continuously.
 *
 * Ground snow deliberately reuses M_GroundStylized's EXISTING
 * SnowLineLowZCm/SnowLineHighZCm scalar params (unreal/apply_ground_
 * material.py) rather than adding a new one: sweeping both down together
 * (same band width, so the material's own 40 m value-noise patchiness comes
 * along for free) from their baked defaults down to a floor well below the
 * area's real valley floor IS the winter ground-snow mechanic, and leaving
 * them at the baked defaults (SnowCover=0) is byte-for-byte the pre-existing
 * altitude snow band - the summer baseline this task requires to be an exact
 * zero-regression match.
 *
 * A MaterialParameterCollection was considered and rejected: none exists
 * anywhere in this project, every material already takes runtime tuning via
 * per-manager MIDs + C++ setters (the established convention this class
 * follows), and only three materials/one HISM batch need reaching - not
 * enough distinct materials to justify introducing the project's first MPC.
 */
UCLASS()
class OPENDRIVEUE_API AODSeasonController : public AActor
{
	GENERATED_BODY()

public:
	AODSeasonController();
	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

	/** Hide the falling precipitation now (garage, even with the world paused). */
	void HidePrecipFx();

	/** Real seconds each season holds stable before transitioning. */
	UPROPERTY(EditAnywhere, Category = "Season")
	float HoldSeconds = 2700.0f;

	/** Real seconds each season takes to gradually blend into the next. */
	UPROPERTY(EditAnywhere, Category = "Season")
	float TransitionSeconds = 900.0f;

	/** Clock time-scale - 1.0 = real time. -odseasonrate= QA override. */
	UPROPERTY(EditAnywhere, Category = "Season")
	float TimeScale = 1.0f;

	/** How often (Hz) the computed blends are actually pushed into the MIDs -
	 *  perf note (docs/perf_after_art_overhaul.md): volumetric fog + foliage
	 *  overdraw have the least headroom in this project's frame budget, so
	 *  this stays a cheap periodic push rather than a SetScalarParameterValue
	 *  call on 3+ materials every single frame. */
	UPROPERTY(EditAnywhere, Category = "Season")
	float ParamPushHz = 3.0f;

	/** Current position within the 14400 s loop, seconds. Frozen (not
	 *  advanced by Tick) once a -odseason= QA override has set it. */
	UPROPERTY(VisibleAnywhere, Transient, Category = "Season|Debug")
	float ClockSeconds = 0.0f;

	UPROPERTY(VisibleAnywhere, Transient, Category = "Season|Debug")
	float FallBlend = 0.0f;

	UPROPERTY(VisibleAnywhere, Transient, Category = "Season|Debug")
	float SnowCover = 0.0f;

	/** Falling-snow FX level. Weather-owned since W1 (next steps.md sec
	 *  7.39): AODWeatherController's PrecipIntensity * PrecipType, copied in
	 *  Tick - the clock no longer computes it. */
	UPROPERTY(VisibleAnywhere, Transient, Category = "Season|Debug")
	float SnowfallIntensity = 0.0f;

	UPROPERTY(VisibleAnywhere, Transient, Category = "Season|Debug")
	float FreezeBlend = 0.0f;

	UPROPERTY(VisibleAnywhere, Transient, Category = "Season|Debug")
	float SpringGreen = 0.0f;

	/** Deciduous-arc follow-up (2026-09, next steps.md sec 7.17): 0 = full
	 *  canopy, 1 = fully bare twigs. Ramps 0->1 across the fall->winter
	 *  transition (leaves - already orange, FallBlend=1 - progressively
	 *  dissolve off the canopy cards) and back 1->0 across winter->spring
	 *  (reverse dissolve = re-budding), in lockstep with FallBlend easing
	 *  back to 0 over the same span so regrown leaves render green, not
	 *  orange. Pushed to the deciduous-only foliage MID as "LeafDrop". */
	UPROPERTY(VisibleAnywhere, Transient, Category = "Season|Debug")
	float LeafDrop = 0.0f;

	/** Garage "Season" row (next steps.md sec 7.38): a season name locks the
	 *  clock at that season's hold midpoint via the same bFrozen path as
	 *  -odseason=; empty = Auto (unfreeze, keep looping from here). No-op
	 *  when an -odseason= QA flag is on the command line - QA always wins. */
	void SetSeasonLock(const FString& SeasonName);

	/** Weather W1 (next steps.md sec 7.39): the clock as a calendar month,
	 *  [0, 12) with Jan = [0, 1). Season hold midpoints land mid-month:
	 *  summer = mid-July (6.5), fall = mid-Oct, winter = mid-Jan, spring =
	 *  mid-Apr - pipeline/sim_weather.py's month_of_clock() mirrors this. */
	float MonthOfYear() const
	{
		return FMath::Fmod(6.5f + (ClockSeconds - 0.5f * HoldSeconds) / LoopSeconds() * 12.0f + 12.0f, 12.0f);
	}

	/** True while a season lock (garage row or -odseason=) holds the clock on one month. */
	bool IsFrozen() const { return bFrozen; }

private:
	/** Pure function of a loop-position clock (seconds, [0, LoopSeconds)) to
	 *  the five season blends - see the .cpp definition for the segment
	 *  breakdown. Static/const-only so it's trivially unit-testable and so
	 *  the -odseason=/-odseasonphase= QA path and the normal ticking path
	 *  can share the exact same math. */
	void ComputeParamsForClock(float Clock, float& OutFall, float& OutSnow,
		float& OutFreeze, float& OutSpringGreen, float& OutLeafDrop) const;

	/** loop-position seconds where season X's HOLD begins - see the .cpp's
	 *  QA-flag parsing for how -odseason=/-odseasonphase= use these. */
	float SeasonHoldStartSeconds(const FString& SeasonName) const;

	float LoopSeconds() const { return 4.0f * (HoldSeconds + TransitionSeconds); }

	void FindManagers();
	void PushParamsToMIDs();
	void UpdateSnowfallFX(float DeltaSeconds);
	void EnsureSnowfallComponent();

	bool bFrozen = false;   // true once a -odseason= QA flag has fixed the clock
	float PushAccumSeconds = 0.0f;
	bool bLoggedSnowDebugOnce = false;   // one-shot -odtrace ground-snow diagnostic (see PushParamsToMIDs)

	TWeakObjectPtr<AODTerrainManager> TerrainMgr;
	TWeakObjectPtr<AODWaterManager> WaterMgr;
	TWeakObjectPtr<AODForestManager> ForestMgr;
	TWeakObjectPtr<AODWeatherController> WeatherCtl;
	TWeakObjectPtr<AODRoadManager> RoadMgr;   // Weather W2 road "Wetness"; absent on Tail of the Dragon

	/** Wet weather (2026-10-09): Tail of the Dragon's road is plain
	 *  StaticMeshActors on the M_Road asset, not AODRoadManager chunks, so it
	 *  gets one shared MID here (BindStaticRoadMID) for "Wetness". Its OBJ
	 *  meshes carry only UV0, so PuddleAmount = 0 (M_Road's UV3 sag read
	 *  would clamp to UV0). */
	void BindStaticRoadMID();
	bool bStaticRoadBound = false;
	UPROPERTY()
	TObjectPtr<UMaterialInstanceDynamic> StaticRoadMID;

	/** Simple runtime falling-snow effect (2026-09-07): a pool of tiny sphere
	 *  instances in a world-aligned box that follows the camera (2026-10-06 -
	 *  it used to ride the pawn root and twist with the car's roll/pitch),
	 *  animated by hand in Tick - NOT Niagara.
	 *  Niagara-asset creation from python is finicky in UE 5.8 (per this
	 *  task's own architecture note) and a from-scratch C++ Niagara system
	 *  still needs an authored data asset to be useful; a plain HISM the
	 *  controller owns and animates directly is the "simplest robust option"
	 *  call for "falling snow must be visible" - cheap (a few hundred tiny
	 *  instances, updated once per tick, not per-material-param-push rate)
	 *  and needs no editor session or asset pipeline step to exist.
	 *
	 *  Weather W2 (next steps.md sec 7.40): the same pool is all precipitation
	 *  - visible count scales with PrecipIntensity, each instance is snow when
	 *  its fixed roll < PrecipType (so sleet is a mix) else a thin fast rain
	 *  streak slanted along the wind; per-instance custom data 0 (1 snow, 0
	 *  rain) picks M_SnowFlake's look. */
	UPROPERTY()
	TObjectPtr<UHierarchicalInstancedStaticMeshComponent> SnowFxHism;

	TArray<FVector> SnowFxLocalOffsets;   // world-axis offsets from SnowFxCenter
	TArray<float> PrecipFxSnowRoll;   // fixed [0,1) per instance: snow if < PrecipType
	TArray<uint8> PrecipFxIsSnow;     // last custom-data value pushed per instance
	TArray<FVector3f> PrecipFxRoll;   // fixed per instance: size x, fall-speed x, flutter phase (rad)
	int32 SnowFxNumNear = 0;          // instances [0, N) are the near box, the rest the far layer
	FVector SnowFxCenter = FVector::ZeroVector;   // box center last tick
	FVector SnowFxPrevCam = FVector::ZeroVector;  // camera location last tick (box re-center + teleport test)
	bool bSnowFxCentered = false;                 // false = next tick re-centers without shifting flakes
};
