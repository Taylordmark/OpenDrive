#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "ODMenuVista.h"
#include "ODGameMode.generated.h"

/** One streamable neighbour of the home area (2026-10-05, large world). */
USTRUCT()
struct FODStreamArea
{
	GENERATED_BODY()

	FString Name;
	FBox2D Rect = FBox2D(ForceInit);   // UE cm, from worlds.json
	bool bLive = false;
	// -ODTileServer (live tile streaming): worlds.json "files", fetched into
	// the tile cache before the area's first spawn - see PrefetchArea
	TArray<FString> Files;
	bool bPrefetching = false;
	bool bPrefetched = false;
	// UPROPERTY so GC sees the spawned set (ULevel::Actors roots it anyway)
	UPROPERTY()
	TArray<TObjectPtr<AActor>> Actors;
};

/**
 * OD game mode (template exorcism, 2026-07-17): spawns AODVehiclePawn and
 * wires ODRaceHUD. Replaces the VehicleTemplate BP game mode entirely.
 */
UCLASS()
class OPENDRIVEUE_API AODGameMode : public AGameModeBase
{
	GENERATED_BODY()

public:
	AODGameMode();

	virtual void StartPlay() override;   // HomeArea + area prewarm, before the level's BeginPlays
	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

	/** Garage-menu vista (2026-10-05): one area's terrain/road/forest/water/
	 *  guardrail managers, translated by Offset (XY only) and streaming just
	 *  Region (area-local). Tagged ODVistaTag. Caller owns/destroys them.
	 *  Step (2026-10-22) spawns only the Step-th of NumVistaSteps() families,
	 *  so the menu can spread the set over frames. */
	TArray<AActor*> SpawnVistaSet(const FString& Area, const FVector& Offset, const FODVistaRegion& Region, int32 Step);
	static int32 NumVistaSteps();

	/** Game thread (2026-10-22): starts Area's heavy manager setup on workers
	 *  (terrain rasters + horizon mesh, forest lakes), cached for the vista
	 *  and then the level's managers. True once ready; call again to poll. */
	static bool PrewarmArea(const FString& Area);

	/** Pauses every non-vista area manager's Tick and the area-set streamer
	 *  while the menu parks the car at a far vista offset, so the real
	 *  managers neither unload around the garage nor probe tiles there. */
	void FreezeStreaming(bool bFreeze);

private:
	/** Spawns one area's manager set (2026-10-05, large world; folds the
	 *  old EnsureForest/Water/Parking/Guardrail/Embankment/BridgeManager).
	 *  Prefixes follow "<family>_<area>_test", same table as unreal/
	 *  build_openworld_test_level.py. For HomeArea it does exactly what the
	 *  old Ensure* calls did: the level-placed terrain/road/building/wall/
	 *  barrier managers are left alone, and each other class is skipped if
	 *  one already exists. Any other area gets the whole set. Returns what
	 *  it spawned. */
	TArray<AActor*> SpawnAreaSet(const FString& Area, const FVector& Offset = FVector::ZeroVector,
		const FODVistaRegion* Vista = nullptr, UClass* Only = nullptr);

	/** Reads Content/OpenDrive/Masks/worlds.json once into StreamAreas: the
	 *  other members of the world HomeArea belongs to. Absent file or home
	 *  not listed = StreamAreas stays empty and Tick() does nothing. */
	void LoadWorldManifest();

	/** -ODTileServer only: fetches A.Files on a worker, then flags A
	 *  bPrefetched so Tick spawns it - managers read those at BeginPlay. */
	void PrefetchArea(FODStreamArea& A);

	/** Art-direction Phase 1 (2026-09-01): same "find or spawn" shape as
	 *  SpawnAreaSet above, guarding on
	 *  AODAtmosphereController itself rather than any one of the several
	 *  actor classes it in turn finds-or-spawns (SkyAtmosphere/
	 *  ExponentialHeightFog/VolumetricCloud/PostProcessVolume) - see that
	 *  class for why. */
	void EnsureAtmosphereController();

	/** Seasons feature (2026-09-07) - same "find or spawn" shape as
	 *  EnsureAtmosphereController above (no per-level tile-prefix needed,
	 *  applies identically everywhere): spawns AODSeasonController, which in
	 *  turn finds AODTerrainManager/AODWaterManager/AODForestManager (spawned
	 *  by SpawnAreaSet above, earlier in this same BeginPlay) and drives
	 *  their ground/water/foliage MIDs through a looping season clock. */
	void EnsureSeasonController();

	/** Weather W1 (next steps.md sec 7.39) - same shape as
	 *  EnsureSeasonController above, spawned right after it: AODWeatherController
	 *  reads the season clock's month and loads its own climate_<area>_test.json
	 *  from the level's AODRoadManager. */
	void EnsureWeatherController();

	/** Ambient biome/city audio (Arc 5.4 / next steps.md SS7.28, 2026-09-07) -
	 *  same "find or spawn" shape as SpawnAreaSet above, deriving
	 *  AODAmbientSoundManager's zone-file name from the level's own
	 *  AODRoadManager the same way. Crossfades wind/forest/settlement/water
	 *  ambient loops against pipeline/build_ambient_zones.py's baked zone
	 *  grid as the player drives - see that class for the full design. */
	void EnsureAmbientSoundManager();

	/** Drawn area (2026-10-21, area picker): the /Game/Areas/AreaTemplate
	 *  level opened with ?odarea=<key> has no area managers of its own - spawn
	 *  the key's whole set as home, its runtime map texture, the boundary
	 *  effect, and drop the car at the (runner-snapped) start. */
	void StartDrawnArea(const FString& Key);

	/** "<area>" from the level's own road manager's "road_<area>_test", "" with none. */
	FString HomeArea;
	UPROPERTY()
	TArray<FODStreamArea> StreamAreas;
	double LastStreamCheckTime = -1.0e9;
	bool bStreamingFrozen = false;
};
