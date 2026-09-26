#include "ODGameMode.h"

#include "EngineUtils.h"
#include "ODAmbientSoundManager.h"
#include "ODAtmosphereController.h"
#include "ODBridgeManager.h"
#include "ODEmbankmentManager.h"
#include "ODForestManager.h"
#include "ODGuardrailManager.h"
#include "ODRaceHUD.h"
#include "ODRoadManager.h"
#include "ODSeasonController.h"
#include "ODVehiclePawn.h"
#include "ODWeatherController.h"
#include "ODParkingManager.h"
#include "ODWaterManager.h"

AODGameMode::AODGameMode()
{
	DefaultPawnClass = AODVehiclePawn::StaticClass();
	HUDClass = AODRaceHUD::StaticClass();
}

void AODGameMode::BeginPlay()
{
	Super::BeginPlay();
	EnsureForestManager();
	EnsureWaterManager();
	EnsureParkingManager();
	EnsureGuardrailManager();
	EnsureEmbankmentManager();
	EnsureBridgeManager();
	EnsureAtmosphereController();
	EnsureSeasonController();
	EnsureWeatherController();
	EnsureAmbientSoundManager();
}

void AODGameMode::EnsureForestManager()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	// Tail of the Dragon already has one hand-placed (Landscape-mask path) -
	// leave it alone. Only open-world levels (no forest manager in the
	// .umap yet) fall through to the runtime spawn below.
	for (TActorIterator<AODForestManager> It(World); It; ++It)
	{
		return;
	}

	// AODRoadManager is the same actor ODMainMenu's -odpick hook reads
	// (GetTownNodes()) and is placed once per open-world map with its
	// RoadTilePrefix already baked in for whichever area that map currently
	// represents (e.g. "road_green_valley_test") - no runtime area-name
	// parsing exists anywhere in this project, the prefix IS the area
	// selection. No road manager means no open-world tile data at all
	// (a bare/menu-only map), so there's nothing to grow a forest on.
	AODRoadManager* Road = nullptr;
	for (TActorIterator<AODRoadManager> It(World); It; ++It)
	{
		Road = *It;
		break;
	}
	if (!Road)
	{
		return;
	}

	// "road_<area>_test" -> "<area>" -> "biome_<area>_test"/etc. This is
	// exactly the naming convention unreal/build_openworld_test_level.py
	// already uses for its own (currently FOREST_ENABLED=False,
	// bake-into-the-umap) ForestManager spawn - mirrored here rather than
	// invented fresh, and matches the "road_"/"terrain_"/"biome_" family
	// already on disk in Content/OpenDrive/Masks/ for every baked area.
	FString Area = Road->RoadTilePrefix;
	Area.RemoveFromStart(TEXT("road_"));
	Area.RemoveFromEnd(TEXT("_test"));
	if (Area.IsEmpty())
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODGameMode: couldn't derive an area name from RoadTilePrefix "
			     "'%s' - skipping forest manager spawn"), *Road->RoadTilePrefix);
		return;
	}

	// Deferred spawn (2026-08-26 fix, found via -odshot QA): a plain
	// SpawnActor() runs BeginPlay() - and therefore LoadBiomePoints() -
	// synchronously before this function gets a chance to set
	// BiomeFilePrefix on the result, so the manager always loaded with an
	// empty prefix and logged "no biome data available - forest disabled"
	// regardless of what got assigned right after. SpawnActorDeferred() lets
	// BiomeFilePrefix land before BeginPlay ever runs, same as how a
	// hand-placed .umap instance's properties are already set before its
	// own BeginPlay fires.
	AODForestManager* Forest = World->SpawnActorDeferred<AODForestManager>(
		AODForestManager::StaticClass(), FTransform::Identity);
	if (!Forest)
	{
		return;
	}
	// DefaultBiome is left at its DeciduousForest default (Tail of the
	// Dragon's own character) deliberately - BiomeFilePrefix below activates
	// LoadBiomePoints()'s point-classified path, and BlendAt() only falls
	// back to DefaultBiome per-tile when a tile has no classified point
	// within BiomeBlendRadiusCm at all. Elevation/centerline/building-centroid
	// prefixes are set the same as biome even though not every area has
	// those sidecars baked yet (e.g. no elev_green_valley_test.bin on disk
	// as of this writing) - Load*() each independently no-op false on a
	// missing file (same "optional feature" convention every one of these
	// prefixes already documents in ODForestManager.h), so this just
	// activates them automatically once/if the pipeline bakes them, with no
	// further code change. Until then GroundAt() falls back to its
	// physics-raycast path unchanged, which works fine here since
	// AODTerrainManager's near-fidelity tiles carry real BlockAll collision
	// out to CollisionRadius (2 km), well past the ~96 m tiles/120 m
	// ForestBandCm this manager ever scatters into.
	Forest->BiomeFilePrefix = FString::Printf(TEXT("biome_%s_test"), *Area);
	Forest->ElevationFilePrefix = FString::Printf(TEXT("elev_%s_test"), *Area);
	Forest->CenterlineFilePrefix = FString::Printf(TEXT("centerline_%s_test"), *Area);
	Forest->BuildingCentroidFilePrefix = FString::Printf(TEXT("building_centroids_%s_test"), *Area);
	// roadstations_{area}.bin - NO "_test" suffix, unlike every prefix above
	// (matches AODTerrainManager::LoadRoadStationSidecar's own area-name
	// derivation; confirmed against the real exported file on disk,
	// Content/OpenDrive/Masks/roadstations_green_valley.bin). See
	// AODForestManager::RoadStationFilePrefix's comment for why this
	// data-driven road exclusion was added alongside CenterlineFilePrefix
	// above (2026-08-26, Taylor's live-drive "trees on the road" report).
	Forest->RoadStationFilePrefix = FString::Printf(TEXT("roadstations_%s"), *Area);
	// water_{area}_test_tile_*.bin - the SAME baked lake tiles
	// EnsureWaterManager() below points AODWaterManager at, reused here
	// purely for polygon geometry (2026-09-03, reviewer defect: trees/bushes
	// found standing in open water at Dillon Reservoir). See
	// AODForestManager::WaterTilePrefix's header comment.
	Forest->WaterTilePrefix = FString::Printf(TEXT("water_%s_test"), *Area);
	Forest->FinishSpawning(FTransform::Identity);
	UE_LOG(LogTemp, Display,
		TEXT("ODGameMode: spawned AODForestManager for open-world area '%s' "
		     "(BiomeFilePrefix='%s')"), *Area, *Forest->BiomeFilePrefix);
}

void AODGameMode::EnsureWaterManager()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	// Same "already hand-placed, leave it alone" guard as EnsureForestManager.
	for (TActorIterator<AODWaterManager> It(World); It; ++It)
	{
		return;
	}

	// Same AODRoadManager-derived area-name convention as EnsureForestManager
	// above - the road manager's own RoadTilePrefix IS the area selection,
	// no separate runtime area-name source exists anywhere in this project.
	AODRoadManager* Road = nullptr;
	for (TActorIterator<AODRoadManager> It(World); It; ++It)
	{
		Road = *It;
		break;
	}
	if (!Road)
	{
		return;
	}

	FString Area = Road->RoadTilePrefix;
	Area.RemoveFromStart(TEXT("road_"));
	Area.RemoveFromEnd(TEXT("_test"));
	if (Area.IsEmpty())
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODGameMode: couldn't derive an area name from RoadTilePrefix "
			     "'%s' - skipping water manager spawn"), *Road->RoadTilePrefix);
		return;
	}

	// Plain SpawnActor is fine here (unlike EnsureForestManager's
	// SpawnActorDeferred) - AODWaterManager::BeginPlay() only loads its own
	// material by a fixed path, it never reads WaterTilePrefix synchronously
	// during BeginPlay (that's only read later, from Tick()'s tile-streaming
	// loop), so there's no property-must-land-before-BeginPlay race to guard
	// against the way AODForestManager's LoadBiomePoints() has.
	AODWaterManager* Water = World->SpawnActor<AODWaterManager>();
	if (!Water)
	{
		return;
	}
	Water->WaterTilePrefix = FString::Printf(TEXT("water_%s_test"), *Area);
	UE_LOG(LogTemp, Display,
		TEXT("ODGameMode: spawned AODWaterManager for open-world area '%s' "
		     "(WaterTilePrefix='%s')"), *Area, *Water->WaterTilePrefix);
}

void AODGameMode::EnsureParkingManager()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	// Same "already hand-placed, leave it alone" guard as EnsureWaterManager.
	for (TActorIterator<AODParkingManager> It(World); It; ++It)
	{
		return;
	}

	// Same AODRoadManager-derived area-name convention as EnsureWaterManager
	// above.
	AODRoadManager* Road = nullptr;
	for (TActorIterator<AODRoadManager> It(World); It; ++It)
	{
		Road = *It;
		break;
	}
	if (!Road)
	{
		return;
	}

	FString Area = Road->RoadTilePrefix;
	Area.RemoveFromStart(TEXT("road_"));
	Area.RemoveFromEnd(TEXT("_test"));
	if (Area.IsEmpty())
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODGameMode: couldn't derive an area name from RoadTilePrefix "
			     "'%s' - skipping parking manager spawn"), *Road->RoadTilePrefix);
		return;
	}

	// Plain SpawnActor is fine here - same reasoning as EnsureWaterManager's
	// AODWaterManager spawn (BeginPlay only loads a fixed-path material, the
	// tile prefix is only read later from Tick()'s streaming loop).
	AODParkingManager* Parking = World->SpawnActor<AODParkingManager>();
	if (!Parking)
	{
		return;
	}
	Parking->ParkingTilePrefix = FString::Printf(TEXT("parking_%s_test"), *Area);
	UE_LOG(LogTemp, Display,
		TEXT("ODGameMode: spawned AODParkingManager for open-world area '%s' "
		     "(ParkingTilePrefix='%s')"), *Area, *Parking->ParkingTilePrefix);
}

void AODGameMode::EnsureGuardrailManager()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	// Same "already hand-placed, leave it alone" guard as EnsureParkingManager.
	for (TActorIterator<AODGuardrailManager> It(World); It; ++It)
	{
		return;
	}

	// Same AODRoadManager-derived area-name convention as EnsureParkingManager.
	AODRoadManager* Road = nullptr;
	for (TActorIterator<AODRoadManager> It(World); It; ++It)
	{
		Road = *It;
		break;
	}
	if (!Road)
	{
		return;
	}

	FString Area = Road->RoadTilePrefix;
	Area.RemoveFromStart(TEXT("road_"));
	Area.RemoveFromEnd(TEXT("_test"));
	if (Area.IsEmpty())
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODGameMode: couldn't derive an area name from RoadTilePrefix "
			     "'%s' - skipping guardrail manager spawn"), *Road->RoadTilePrefix);
		return;
	}

	AODGuardrailManager* Guardrail = World->SpawnActor<AODGuardrailManager>();
	if (!Guardrail)
	{
		return;
	}
	Guardrail->GuardrailTilePrefix = FString::Printf(TEXT("guardrail_%s_test"), *Area);
	UE_LOG(LogTemp, Display,
		TEXT("ODGameMode: spawned AODGuardrailManager for open-world area '%s' "
		     "(GuardrailTilePrefix='%s')"), *Area, *Guardrail->GuardrailTilePrefix);
}

void AODGameMode::EnsureEmbankmentManager()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	// Same "already hand-placed, leave it alone" guard as EnsureGuardrailManager.
	for (TActorIterator<AODEmbankmentManager> It(World); It; ++It)
	{
		return;
	}

	// Same AODRoadManager-derived area-name convention as EnsureGuardrailManager.
	AODRoadManager* Road = nullptr;
	for (TActorIterator<AODRoadManager> It(World); It; ++It)
	{
		Road = *It;
		break;
	}
	if (!Road)
	{
		return;
	}

	FString Area = Road->RoadTilePrefix;
	Area.RemoveFromStart(TEXT("road_"));
	Area.RemoveFromEnd(TEXT("_test"));
	if (Area.IsEmpty())
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODGameMode: couldn't derive an area name from RoadTilePrefix "
			     "'%s' - skipping embankment manager spawn"), *Road->RoadTilePrefix);
		return;
	}

	AODEmbankmentManager* Embankment = World->SpawnActor<AODEmbankmentManager>();
	if (!Embankment)
	{
		return;
	}
	Embankment->EmbankmentTilePrefix = FString::Printf(TEXT("embankment_%s_test"), *Area);
	UE_LOG(LogTemp, Display,
		TEXT("ODGameMode: spawned AODEmbankmentManager for open-world area '%s' "
		     "(EmbankmentTilePrefix='%s')"), *Area, *Embankment->EmbankmentTilePrefix);
}

void AODGameMode::EnsureBridgeManager()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	// Same "already hand-placed, leave it alone" guard as EnsureEmbankmentManager.
	for (TActorIterator<AODBridgeManager> It(World); It; ++It)
	{
		return;
	}

	// Same AODRoadManager-derived area-name convention as EnsureEmbankmentManager.
	AODRoadManager* Road = nullptr;
	for (TActorIterator<AODRoadManager> It(World); It; ++It)
	{
		Road = *It;
		break;
	}
	if (!Road)
	{
		return;
	}

	FString Area = Road->RoadTilePrefix;
	Area.RemoveFromStart(TEXT("road_"));
	Area.RemoveFromEnd(TEXT("_test"));
	if (Area.IsEmpty())
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODGameMode: couldn't derive an area name from RoadTilePrefix "
			     "'%s' - skipping bridge manager spawn"), *Road->RoadTilePrefix);
		return;
	}

	AODBridgeManager* Bridge = World->SpawnActor<AODBridgeManager>();
	if (!Bridge)
	{
		return;
	}
	Bridge->BridgeTilePrefix = FString::Printf(TEXT("bridge_%s_test"), *Area);
	UE_LOG(LogTemp, Display,
		TEXT("ODGameMode: spawned AODBridgeManager for open-world area '%s' "
		     "(BridgeTilePrefix='%s')"), *Area, *Bridge->BridgeTilePrefix);
}

void AODGameMode::EnsureAtmosphereController()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	// Unlike Forest/Water above, this doesn't need an AODRoadManager/area
	// name - it isn't tile-streamed, and applies identically to every level.
	for (TActorIterator<AODAtmosphereController> It(World); It; ++It)
	{
		return;
	}

	World->SpawnActor<AODAtmosphereController>();
	UE_LOG(LogTemp, Display, TEXT("ODGameMode: spawned AODAtmosphereController"));
}

void AODGameMode::EnsureSeasonController()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	for (TActorIterator<AODSeasonController> It(World); It; ++It)
	{
		return;
	}

	World->SpawnActor<AODSeasonController>();
	UE_LOG(LogTemp, Display, TEXT("ODGameMode: spawned AODSeasonController"));
}

void AODGameMode::EnsureWeatherController()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	for (TActorIterator<AODWeatherController> It(World); It; ++It)
	{
		return;
	}

	World->SpawnActor<AODWeatherController>();
	UE_LOG(LogTemp, Display, TEXT("ODGameMode: spawned AODWeatherController"));
}

void AODGameMode::EnsureAmbientSoundManager()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	// Same "already hand-placed, leave it alone" guard as EnsureEmbankmentManager.
	for (TActorIterator<AODAmbientSoundManager> It(World); It; ++It)
	{
		return;
	}

	// Same AODRoadManager-derived area-name convention as EnsureEmbankmentManager.
	AODRoadManager* Road = nullptr;
	for (TActorIterator<AODRoadManager> It(World); It; ++It)
	{
		Road = *It;
		break;
	}
	if (!Road)
	{
		return;
	}

	FString Area = Road->RoadTilePrefix;
	Area.RemoveFromStart(TEXT("road_"));
	Area.RemoveFromEnd(TEXT("_test"));
	if (Area.IsEmpty())
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODGameMode: couldn't derive an area name from RoadTilePrefix "
			     "'%s' - skipping ambient sound manager spawn"), *Road->RoadTilePrefix);
		return;
	}

	AODAmbientSoundManager* Ambient = World->SpawnActor<AODAmbientSoundManager>();
	if (!Ambient)
	{
		return;
	}
	Ambient->AmbientZoneFile = FString::Printf(TEXT("ambient_zones_%s_test"), *Area);
	UE_LOG(LogTemp, Display,
		TEXT("ODGameMode: spawned AODAmbientSoundManager for open-world area '%s' "
		     "(AmbientZoneFile='%s')"), *Area, *Ambient->AmbientZoneFile);
}
