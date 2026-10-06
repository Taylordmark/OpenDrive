#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODWeatherController.generated.h"

class AODForestManager;
class UCameraComponent;
class UInstancedStaticMeshComponent;
class UMaterialInstanceDynamic;

/** Weather states (next steps.md "Weather plan", W1). Order MUST match
 *  pipeline/sim_weather.py's STATES - it indexes the same weight/target
 *  tables. Tornado/Hurricane are event states: rolled and held like the
 *  others, plus their W4/W5 event layer (funnel, bands - sec 7.42). */
UENUM()
enum class EODWeatherState : uint8
{
	Clear,
	Overcast,
	Rain,
	HeavyRain,
	Sleet,
	Snow,
	Blizzard,
	Fog,
	DustStorm,
	Thunderstorm,
	Tornado,
	Hurricane,
};

/** One stage's climate.json (pipeline/fetch_climate.py, W0), staged as
 *  Content/OpenDrive/Masks/climate_<area>_test.json (or climate_<level>.json
 *  on a level with no AODRoadManager, e.g. Tail of the Dragon). Defaults = the mild
 *  fallback used when a stage has none. */
struct FODClimate
{
	float MonthlyTempC[12] = { 3, 4, 7, 11, 15, 19, 22, 22, 18, 13, 8, 4 };
	float MonthlyPrecipMm[12] = { 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70 };
	float Hurricane = 0.0f;
	float Tornado = 0.0f;
	float DustStorm = 0.0f;
};

/**
 * Weather W1 (2026-09-25, next steps.md sec 7.39): state + published scalars,
 * no visuals (W2 consumes the scalars). Same "find-or-spawn from
 * AODGameMode" singleton shape as AODSeasonController - see
 * AODGameMode::EnsureWeatherController.
 *
 * A weighted random walk: every HoldMin..HoldMax seconds the next state is
 * drawn from weights = stage climate (climate.json monthly temp/precip +
 * storm weights) at the season clock's month (AODSeasonController::
 * MonthOfYear - the garage Season lock freezes that month, so weather rolls
 * within the picked season) x the biome at the player (AODForestManager::
 * BiomeSampleAt). Temperature (month temp + lapse rate from the player's
 * elevation) splits precipitation into rain/sleet/snow. The weight table is
 * mirrored 1:1 in pipeline/sim_weather.py, which simulates it offline.
 *
 * Published scalars ramp to each new state's targets over RampSeconds with
 * a smoothstep, so nothing snaps. AODSeasonController reads
 * PrecipIntensity * PrecipType as its SnowfallIntensity (falling snow is
 * weather-owned; the season clock keeps ground cover/freeze/green-up).
 *
 * QA: console "od.Weather <State>" forces and holds a state, "od.Weather
 * Auto" resumes the walk. Every transition is logged.
 */
UCLASS()
class OPENDRIVEUE_API AODWeatherController : public AActor
{
	GENERATED_BODY()

public:
	AODWeatherController();
	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

	/** Smoothstep ramp length between two states' scalar targets. */
	UPROPERTY(EditAnywhere, Category = "Weather")
	float RampSeconds = 90.0f;

	/** Wetness 0 -> 1 time under PrecipIntensity 1 (scales with intensity). */
	UPROPERTY(EditAnywhere, Category = "Weather")
	float WetUpSeconds = 180.0f;

	/** Wetness 1 -> 0 time once precipitation stops - dries slowly. */
	UPROPERTY(EditAnywhere, Category = "Weather")
	float DrySeconds = 1200.0f;

	// ---- snow accumulation (2026-10-06, TickSnow; pipeline/check_snow_depth.py
	// mirrors the formula). Ambient depths below are the uncleared value; each
	// road chunk integrates RoadSnowRateCmS/SideSnowRateCmS on its own
	// (AODRoadManager::TickSnow) so a future plow can clear chunks.

	/** Multiplies every snow time constant's speed (2 = twice as fast). -odsnowrate=N. */
	UPROPERTY(EditAnywhere, Category = "Weather|Snow")
	float SnowTimeScale = 1.0f;

	/** Warm road melts snow on contact this long at full snowfall and -2 C
	 *  before any builds (longer when lighter/warmer, shorter when colder). */
	UPROPERTY(EditAnywhere, Category = "Weather|Snow")
	float SnowContactMeltSeconds = 90.0f;

	/** Road build rate at snowfall 1 (blizzard): 30 cm in ~8.6 min. */
	UPROPERTY(EditAnywhere, Category = "Weather|Snow")
	float RoadSnowCmPerMin = 3.5f;

	UPROPERTY(EditAnywhere, Category = "Weather|Snow")
	float RoadSnowCapCm = 30.0f;

	/** Roadside builds this many times faster (no contact melt, drifting). */
	UPROPERTY(EditAnywhere, Category = "Weather|Snow")
	float SideSnowMult = 2.0f;

	UPROPERTY(EditAnywhere, Category = "Weather|Snow")
	float SideSnowCapCm = 90.0f;

	/** Melt once snow stops, at -2 C (faster warmer, slower colder). */
	UPROPERTY(EditAnywhere, Category = "Weather|Snow")
	float SnowMeltCmPerMin = 0.5f;

	/** Extra melt under rain intensity 1. */
	UPROPERTY(EditAnywhere, Category = "Weather|Snow")
	float RainMeltCmPerMin = 4.0f;

	// ---- on-lens precipitation (2026-10-06, TickLens): drops/flakes land on
	// the player camera, airflow pushes them out toward the screen edges,
	// flakes stick, then melt into a small water smear. Sizes/positions are in
	// screen half-widths (1 = center to the left/right edge).

	/** Landings per second at PrecipIntensity 1, parked; x (1 + speed / 20 m/s). */
	UPROPERTY(EditAnywhere, Category = "Weather|Lens")
	float LensDropsPerSecond = 5.0f;

	/** Pool size (read once, when the layer is created). */
	UPROPERTY(EditAnywhere, Category = "Weather|Lens")
	int32 LensMaxDrops = 40;

	/** Mean drop radius, screen half-widths (each drop 0.5..1.4x). */
	UPROPERTY(EditAnywhere, Category = "Weather|Lens")
	float LensDropSize = 0.025f;

	/** Outward airflow drift rate, 1/s per m/s of camera speed: a drop moves
	 *  out at (distance from center) x this x speed, so it creeps near the
	 *  center and speeds up toward the edge. */
	UPROPERTY(EditAnywhere, Category = "Weather|Lens")
	float LensDriftPerMs = 0.012f;

	/** Sideways drift, half-widths/s per m/s of crosswind (wind across the view). */
	UPROPERTY(EditAnywhere, Category = "Weather|Lens")
	float LensWindDrift = 0.01f;

	/** Water drop lifetime (each 0.7..1.3x) unless it drifts off-screen first. */
	UPROPERTY(EditAnywhere, Category = "Weather|Lens")
	float LensDropLifeSeconds = 5.0f;

	/** A landed flake sits (barely moving) this long before it starts melting. */
	UPROPERTY(EditAnywhere, Category = "Weather|Lens")
	float LensSnowHoldSeconds = 1.2f;

	/** Flake -> water smear melt time; the smear then lives half a water drop's life. */
	UPROPERTY(EditAnywhere, Category = "Weather|Lens")
	float LensMeltSeconds = 2.5f;

	// ---- published scalars (W2/W3 consumers read these) ----
	UPROPERTY(VisibleAnywhere, Transient, Category = "Weather|State")
	EODWeatherState State = EODWeatherState::Clear;

	/** 0 dry .. 1 downpour/blizzard. */
	UPROPERTY(VisibleAnywhere, Transient, Category = "Weather|State")
	float PrecipIntensity = 0.0f;

	/** 0 rain .. 0.5 sleet .. 1 snow. */
	UPROPERTY(VisibleAnywhere, Transient, Category = "Weather|State")
	float PrecipType = 0.0f;

	UPROPERTY(VisibleAnywhere, Transient, Category = "Weather|State")
	float WindSpeedMs = 3.0f;

	/** Direction the wind blows toward, degrees (world yaw). */
	UPROPERTY(VisibleAnywhere, Transient, Category = "Weather|State")
	float WindDirDeg = 0.0f;

	UPROPERTY(VisibleAnywhere, Transient, Category = "Weather|State")
	float VisibilityM = 20000.0f;

	/** Surface wetness: accumulates under precipitation, dries slowly. */
	UPROPERTY(VisibleAnywhere, Transient, Category = "Weather|State")
	float Wetness = 0.0f;

	/** Air temperature from the last roll (month temp + lapse), C. */
	UPROPERTY(VisibleAnywhere, Transient, Category = "Weather|State")
	float AirTempC = -2.0f;

	/** 0 warm road (melts snow on contact) .. 1 chilled (snow sticks). */
	UPROPERTY(VisibleAnywhere, Transient, Category = "Weather|State")
	float RoadChill = 0.0f;

	/** Ambient (unplowed) snow depth on the road / beside it, cm. */
	UPROPERTY(VisibleAnywhere, Transient, Category = "Weather|State")
	float RoadSnowCm = 0.0f;

	UPROPERTY(VisibleAnywhere, Transient, Category = "Weather|State")
	float SideSnowCm = 0.0f;

	/** This tick's depth change rates, cm/s - road chunks integrate these. */
	float RoadSnowRateCmS = 0.0f;
	float SideSnowRateCmS = 0.0f;

	/** Bumped when depth is forced; chunks then reset to the ambient depth. */
	int32 SnowDepthSerial = 0;

	/** QA: force and hold road snow depth (roadside scales to its cap);
	 *  negative releases the hold. -odsnowdepth=cm, "od.Weather Depth <cm|Auto>". */
	void SetSnowDepth(float Cm);

	UPROPERTY(VisibleAnywhere, Transient, Category = "Weather|State")
	float CloudCover = 0.1f;

	UPROPERTY(VisibleAnywhere, Transient, Category = "Weather|State")
	float DustAmount = 0.0f;

	/** 0..1 green tornado-sky tint (W4) - ODAtmosphereController. */
	UPROPERTY(VisibleAnywhere, Transient, Category = "Weather|State")
	float StormTint = 0.0f;

	/** Wind at a world position, m/s in world XY: the published
	 *  WindSpeedMs/WindDirDeg plus the tornado vortex (W4) within
	 *  TORNADO_REACH_M of the funnel. UODVehicleMovement's crosswind reads it. */
	FVector2D WindAtMs(const FVector& WorldPos) const;

	/** HUD banner (W4/W5 onset) - ODRaceHUD draws it for a few seconds after
	 *  WarningShownAt (world time). */
	FString WarningText;
	float WarningShownAt = -100.0f;

	/** QA: a state name forces and holds it; "Auto" (or empty) resumes the
	 *  random walk with an immediate roll. False on an unknown name.
	 *  bInstant (garage menu, 2026-10-05): skip the ramp, road wetness
	 *  jumps to match. */
	bool ForceState(const FString& Name, bool bInstant = false);

	/** Immediate re-roll (garage season lock changed) - no-op while forced. */
	void Reroll();

	/** Garage Weather row (2026-10-05): true if S has weight > 0 under the
	 *  Auto walk's weighting (StateWeights) in at least one month of the
	 *  active climate, at the active biome/elevation sample (SampleBiome). */
	bool CanProduce(EODWeatherState S) const;

	/** Garage-menu vista (2026-10-05): the Auto walk and CanProduce use
	 *  InClimate, with biome/elevation from Forest (the vista's own copy) at
	 *  SpotCm (area-local), instead of the home climate at the player. Null =
	 *  back to home. No re-roll - the caller re-forces/re-rolls. */
	void SetVistaClimate(const FODClimate* InClimate, AODForestManager* Forest, const FVector2D& SpotCm);

	/** Content/OpenDrive/Masks/climate_<Area>.json into Out (Area = "<area>_test"
	 *  or a level's snake-case name). False + Out untouched when missing/bad. */
	static bool LoadClimateFile(const FString& Area, FODClimate& Out);

private:
	struct FTargets
	{
		float Precip = 0.0f, PrecipType = 0.0f, Wind = 0.0f, WindDir = 0.0f,
			Visibility = 0.0f, Cloud = 0.0f, Dust = 0.0f, Tint = 0.0f;
	};

	void LoadClimate();
	void Roll();
	/** Biome multipliers (BIOME_MULT row) + elevation delta for the lapse: the
	 *  vista's forest at its spot, else the nearest forest manager's point to
	 *  the player. Neutral/0 without point-biome data. */
	void SampleBiome(const float*& OutMult, float& OutElevDeltaM, int32& OutBiomeIdx) const;
	void EnterState(EODWeatherState NewState, float HoldSeconds);
	FTargets Published() const;
	void Warn(const FString& Text);

	// ---- W4 tornado: funnel at FunnelPos (world XY cm) walking a gently
	// curving path; visuals = two ISMs of engine basic shapes (M_TornadoFunnel).
	void SpawnFunnel();
	void TickTornado(float DeltaSeconds);

	FVector2D FunnelPos = FVector2D::ZeroVector;
	float FunnelHeadingDeg = 0.0f, FunnelTurnDegS = 0.0f, FunnelGroundZ = 0.0f;
	float TornadoFade = 0.0f;   // 0..1, funnel opacity + vortex strength
	float FunnelSpin = 0.0f;
	UPROPERTY()
	TObjectPtr<UInstancedStaticMeshComponent> FunnelISM;
	UPROPERTY()
	TObjectPtr<UInstancedStaticMeshComponent> DebrisISM;
	UPROPERTY()
	TObjectPtr<UMaterialInstanceDynamic> FunnelMID;

	// ---- on-lens precipitation: an ISM of /Engine/BasicShapes/Plane quads
	// (M_LensDrop) attached to the player pawn's UCameraComponent, so it
	// follows the view (crash shake included), never the car body.
	struct FLensDrop
	{
		FVector2D Pos = FVector2D::ZeroVector;    // screen half-widths, +Y up
		FVector2D Bias = FVector2D::ZeroVector;   // small outward-flow offset so center drops still move
		float Radius = 0.0f, Age = 0.0f, Life = 0.0f;
		float Kind = 0.0f;   // 1 flake .. 0 water (melting)
		bool bSnow = false;
	};
	void TickLens(float DeltaSeconds);
	void TickSnow(float DeltaSeconds);
	bool bSnowHeld = false;

	TArray<FLensDrop> LensDrops;
	UPROPERTY()
	TObjectPtr<UInstancedStaticMeshComponent> LensISM;
	TWeakObjectPtr<UCameraComponent> LensCam;
	FVector LensPrevCamLoc = FVector::ZeroVector;
	float LensSpeedMs = 0.0f;     // smoothed camera speed
	float LensSpawnDebt = 0.0f;   // fractional landings carried between ticks

	// ---- W5 hurricane: band envelope over the hold (HurricaneEnvelope) ----
	float HurricaneAge = 0.0f, HurricaneLen = 900.0f;
	bool bHurricaneEye = false;

	FODClimate Climate;       // active: HomeClimate, or the shown vista's (SetVistaClimate)
	FODClimate HomeClimate;   // this level's, from LoadClimate()
	bool bVista = false;
	TWeakObjectPtr<AODForestManager> VistaForest;
	FVector2D VistaSpotCm = FVector2D::ZeroVector;
	FTargets From, To;
	float RampT = 1.0f;
	float HoldLeft = 0.0f;
	bool bForced = false;
};
