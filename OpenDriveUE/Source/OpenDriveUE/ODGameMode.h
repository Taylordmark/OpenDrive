#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "ODGameMode.generated.h"

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

	virtual void BeginPlay() override;

private:
	/** Open-world forest bootstrap (2026-08-26): Tail of the Dragon has its
	 *  AODForestManager hand-placed in the .umap (Landscape-mask path,
	 *  DefaultBiome=DeciduousForest); OpenWorldTest never got one, so
	 *  open-world drives showed zero trees/vegetation. Editing a .umap
	 *  headlessly is fragile, and this is the one GameMode class every level
	 *  in the project already shares (GlobalDefaultGameMode in
	 *  DefaultEngine.ini) - runtime-spawning here, guarded by "does a forest
	 *  manager already exist", is the same shape as everything else that
	 *  varies by level (AODRoadManager/AODTerrainManager are placed once per
	 *  map with their tile-prefix UPROPERTYs already baked in) without
	 *  needing a second hand-authored placement for every open-world area. */
	void EnsureForestManager();

	/** Open-world water bootstrap (2026-08-27, WATER feature) - same shape
	 *  as EnsureForestManager() above: no open-world level has an
	 *  AODWaterManager hand-placed yet, so this runtime-spawns one, guarded
	 *  by "does a water manager already exist" the same way forest is,
	 *  deriving its tile prefix from the level's own AODRoadManager rather
	 *  than requiring a second hand-authored placement per area. */
	void EnsureWaterManager();

	/** Paved town-context bootstrap (art-direction §7.15, 2026-09-03) - same
	 *  "find or spawn" shape as EnsureWaterManager() above, deriving
	 *  AODParkingManager's tile prefix from the level's own AODRoadManager
	 *  the same way. Streams baked OSM parking-lot polygons as flat paved
	 *  ground patches so towns read as towns (buildings on paved context,
	 *  not bare terrain) - see pipeline/build_parking_tiles.py. */
	void EnsureParkingManager();

	/** Art-direction Phase 1 (2026-09-01): same "find or spawn" shape as
	 *  EnsureForestManager/EnsureWaterManager above, guarding on
	 *  AODAtmosphereController itself rather than any one of the several
	 *  actor classes it in turn finds-or-spawns (SkyAtmosphere/
	 *  ExponentialHeightFog/VolumetricCloud/PostProcessVolume) - see that
	 *  class for why. */
	void EnsureAtmosphereController();

	/** Visible guardrails (2026-09-04) - same "find or spawn" shape as
	 *  EnsureParkingManager above, deriving AODGuardrailManager's tile
	 *  prefix from the level's own AODRoadManager the same way. Streams
	 *  runtime-extruded W-beam/Jersey-barrier ribbons wherever the road
	 *  rides high above the surrounding terrain - see
	 *  pipeline/build_road_guardrails.py. */
	void EnsureGuardrailManager();

	/** Embankment skirts (2026-09-07) - same "find or spawn" shape as
	 *  EnsureGuardrailManager above, deriving AODEmbankmentManager's tile
	 *  prefix from the level's own AODRoadManager the same way. Streams
	 *  runtime-extruded sloped earthen skirts wherever the road rides
	 *  2.5-7m above the surrounding terrain (below that the terrain meets
	 *  the road on its own; above that is the later bridge-deck task's
	 *  job) - see pipeline/build_road_embankments.py. */
	void EnsureEmbankmentManager();

	/** Bridge decks (2026-09-07, §7.21) - same "find or spawn" shape as
	 *  EnsureEmbankmentManager above, deriving AODBridgeManager's tile prefix
	 *  from the level's own AODRoadManager the same way. Streams runtime-
	 *  extruded concrete deck fascia + pier columns wherever pipeline/
	 *  build_road_embankments.py's own §7.21 sub-classification pass kept a
	 *  "bridge"-class run as a genuine short span (most of the raw >7m-drop
	 *  classification is actually a mountainside sidehill cut and gets
	 *  demoted to the embankment skirt above instead - see that module's
	 *  docstring). */
	void EnsureBridgeManager();

	/** Seasons feature (2026-09-07) - same "find or spawn" shape as
	 *  EnsureAtmosphereController above (no per-level tile-prefix needed,
	 *  applies identically everywhere): spawns AODSeasonController, which in
	 *  turn finds AODTerrainManager/AODWaterManager/AODForestManager (spawned
	 *  by the Ensure* calls above, in order, earlier in this same BeginPlay)
	 *  and drives their ground/water/foliage MIDs through a looping season
	 *  clock. */
	void EnsureSeasonController();

	/** Weather W1 (next steps.md sec 7.39) - same shape as
	 *  EnsureSeasonController above, spawned right after it: AODWeatherController
	 *  reads the season clock's month and loads its own climate_<area>_test.json
	 *  from the level's AODRoadManager. */
	void EnsureWeatherController();

	/** Ambient biome/city audio (Arc 5.4 / next steps.md SS7.28, 2026-09-07) -
	 *  same "find or spawn" shape as EnsureEmbankmentManager above, deriving
	 *  AODAmbientSoundManager's zone-file name from the level's own
	 *  AODRoadManager the same way. Crossfades wind/forest/settlement/water
	 *  ambient loops against pipeline/build_ambient_zones.py's baked zone
	 *  grid as the player drives - see that class for the full design. */
	void EnsureAmbientSoundManager();
};
