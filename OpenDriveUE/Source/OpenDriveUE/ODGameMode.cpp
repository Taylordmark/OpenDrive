#include "ODGameMode.h"
#include "ODRoadTrip.h"
#include "ODTileSource.h"
#include "Async/Async.h"

#include "Dom/JsonObject.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "ImageUtils.h"
#include "Engine/Texture2D.h"
#include "Misc/Paths.h"
#include "ODAmbientSoundManager.h"
#include "ODAtmosphereController.h"
#include "ODBakeLink.h"
#include "ODBoundaryEffect.h"
#include "ODBarrierManager.h"
#include "ODBridgeManager.h"
#include "ODBuildingManager.h"
#include "ODEmbankmentManager.h"
#include "ODForestManager.h"
#include "ODFurnitureManager.h"
#include "ODGuardrailManager.h"
#include "ODRaceHUD.h"
#include "ODChallengeManager.h"
#include "ODRaceManager.h"
#include "ODRoadManager.h"
#include "ODSeasonController.h"
#include "ODTerrainManager.h"
#include "ODTrafficManager.h"
#include "ODVehiclePawn.h"
#include "ODWallManager.h"
#include "ODWeatherController.h"
#include "ODParkingManager.h"
#include "ODWaterManager.h"
#include "ODReplay.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

AODGameMode::AODGameMode()
{
	DefaultPawnClass = AODVehiclePawn::StaticClass();
	HUDClass = AODRaceHUD::StaticClass();
	// area-set streaming (2026-10-05, large world) - Tick() no-ops unless
	// worlds.json lists the home area, see LoadWorldManifest()
	PrimaryActorTick.bCanEverTick = true;
}

void AODGameMode::BeginPlay()
{
	Super::BeginPlay();

	// AODRoadManager is the same actor ODMainMenu's -odpick hook reads
	// (GetTownNodes()) and is placed once per open-world map with its
	// RoadTilePrefix already baked in for whichever area that map currently
	// represents (e.g. "road_green_valley_test") - the prefix IS the area
	// selection. No road manager means no open-world tile data at all
	// (a bare/menu-only map, or Tail of the Dragon), so nothing to spawn.
	if (UWorld* World = GetWorld())
	{
		for (TActorIterator<AODRoadManager> It(World); It; ++It)
		{
			// "road_<area>_test" -> "<area>" -> "biome_<area>_test"/etc. This
			// is exactly the naming convention unreal/build_openworld_test_level.py
			// uses for its level-placed managers, and matches the "road_"/
			// "terrain_"/"biome_" family on disk in Content/OpenDrive/Masks/.
			HomeArea = It->RoadTilePrefix;
			HomeArea.RemoveFromStart(TEXT("road_"));
			HomeArea.RemoveFromEnd(TEXT("_test"));
			if (HomeArea.IsEmpty())
			{
				UE_LOG(LogTemp, Warning,
					TEXT("ODGameMode: couldn't derive an area name from RoadTilePrefix "
					     "'%s' - skipping area manager spawns"), *It->RoadTilePrefix);
			}
			break;
		}
	}
	// ?odarea=<key> (2026-10-21): the area template level, no road manager of its own
	const TCHAR* DrawnKey = GetWorld() ? GetWorld()->URL.GetOption(TEXT("odarea="), nullptr) : nullptr;
	if (HomeArea.IsEmpty() && DrawnKey && *DrawnKey)
	{
		StartDrawnArea(DrawnKey);
	}
	else if (!HomeArea.IsEmpty())
	{
		SpawnAreaSet(HomeArea);
	}
	if (!HomeArea.IsEmpty())
	{
		// 3c ambient traffic: follows the nearest open-world area itself, off in races
		if (GetWorld() && !TActorIterator<AODTrafficManager>(GetWorld()))
		{
			GetWorld()->SpawnActor<AODTrafficManager>();
		}
	}
	EnsureAtmosphereController();
	EnsureSeasonController();
	EnsureWeatherController();
	if (GetWorld() && !AODReplay::Find(GetWorld()))
	{
		GetWorld()->SpawnActor<AODReplay>();   // #9 always-on 30 s replay buffer
	}
	// ponytail: one ambient-sound manager, the home area's zones only - a
	// streamed area set plays home's zone grid (silent outside it). Spawn one
	// per set (and crossfade) if the neighbouring areas sound wrong.
	EnsureAmbientSoundManager();
	LoadWorldManifest();

	// road trip (2026-10-22): ?oddest=<x>,<y> from the menu's Destination pick;
	// the trip routes there itself once the graph is loaded and the drive started
	FVector2D Dest;
	if (const TCHAR* D = GetWorld() ? GetWorld()->URL.GetOption(TEXT("oddest="), nullptr) : nullptr; D && AODRoadTrip::ParseDest(D, Dest))
	{
		GetWorld()->SpawnActor<AODRoadTrip>()->Dest = Dest;
	}
	else if (D)
	{
		UE_LOG(LogTemp, Warning, TEXT("ODGameMode: bad ?oddest=%s (want <x>,<y> cm) - no road trip"), D);
	}

	// custom track (pipeline/build_track.py): the race manager reads
	// -odtrack itself; open-world levels just don't place one
	FString Track;
	if (FParse::Value(FCommandLine::Get(), TEXT("odtrack="), Track) && GetWorld())
	{
		bool bHave = false;
		for (TActorIterator<AODRaceManager> It(GetWorld()); It; ++It)
		{
			bHave = true;
			break;
		}
		if (!bHave)
		{
			GetWorld()->SpawnActor<AODRaceManager>();
		}
	}
	// free roam: speed traps/zones and landmark hunting (fun-features #2/#5)
	if (!HomeArea.IsEmpty() && GetWorld() && !TActorIterator<AODRaceManager>(GetWorld()))
	{
		AODChallengeManager* Challenges = GetWorld()->SpawnActorDeferred<AODChallengeManager>(
			AODChallengeManager::StaticClass(), FTransform::Identity);
		Challenges->AreaKey = HomeArea;
		Challenges->FinishSpawning(FTransform::Identity);
	}
}

// the families with a vista mode (FODVistaRegion), in SpawnAreaSet's order
static const TArray<UClass*>& VistaClasses()
{
	static const TArray<UClass*> Classes = {AODTerrainManager::StaticClass(), AODRoadManager::StaticClass(),
		AODForestManager::StaticClass(), AODWaterManager::StaticClass(), AODGuardrailManager::StaticClass(),
		AODFurnitureManager::StaticClass()};
	return Classes;
}

TArray<AActor*> AODGameMode::SpawnVistaSet(const FString& Area, const FVector& Offset, const FODVistaRegion& Region, int32 Step)
{
	return VistaClasses().IsValidIndex(Step)
		? SpawnAreaSet(Area, FVector(Offset.X, Offset.Y, 0.0), &Region, VistaClasses()[Step]) : TArray<AActor*>();
}

int32 AODGameMode::NumVistaSteps()
{
	return VistaClasses().Num();
}

bool AODGameMode::PrewarmArea(const FString& Area)
{
	// both run every call: each starts its workers on the first one
	const bool bTerrain = AODTerrainManager::PrewarmArea(FString::Printf(TEXT("terrain_%s_test"), *Area));
	const bool bForest = AODForestManager::PrewarmArea(FString::Printf(TEXT("water_%s_test"), *Area),
		FString::Printf(TEXT("elev_%s_test"), *Area));
	return bTerrain && bForest;
}

void AODGameMode::FreezeStreaming(bool bFreeze)
{
	bStreamingFrozen = bFreeze;
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}
	for (TActorIterator<AActor> It(World); It; ++It)
	{
		AActor* A = *It;
		if (!A->ActorHasTag(ODVistaTag)
			&& (A->IsA<AODTerrainManager>() || A->IsA<AODRoadManager>() || A->IsA<AODBuildingManager>()
				|| A->IsA<AODWallManager>() || A->IsA<AODBarrierManager>() || A->IsA<AODForestManager>()
				|| A->IsA<AODWaterManager>() || A->IsA<AODParkingManager>() || A->IsA<AODGuardrailManager>()
				|| A->IsA<AODEmbankmentManager>() || A->IsA<AODBridgeManager>() || A->IsA<AODFurnitureManager>()))
		{
			A->SetActorTickEnabled(!bFreeze);
		}
	}
}

// worlds.json "terrain_tile_m" of Area's entry (2026-10-21, area picker: drawn
// areas bake 1 km terrain tiles, hand areas 2 km and don't list it), then
// data/user_areas.json for a drawn area the manifest doesn't list yet.
static float TerrainTileCmFor(const FString& Area, float Default)
{
	static TMap<FString, float> Cache;   // ponytail: read once per run - a re-bake mid-run keeps its tile size anyway
	static bool bRead = false;
	if (!bRead)
	{
		bRead = true;
		FString Text;
		TSharedPtr<FJsonObject> Root;
		if (FFileHelper::LoadFileToString(Text, *ODTileSource::Fetch(FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks/worlds.json")))
			&& FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Root) && Root.IsValid())
		{
			for (const TPair<FString, TSharedPtr<FJsonValue>>& World : Root->Values)
			{
				const TArray<TSharedPtr<FJsonValue>>* Members = nullptr;
				if (!World.Value.IsValid() || !World.Value->TryGetArray(Members))
				{
					continue;
				}
				for (const TSharedPtr<FJsonValue>& V : *Members)
				{
					const TSharedPtr<FJsonObject>* Obj = nullptr;
					FString Name;
					double TileM = 0.0;
					if (V.IsValid() && V->TryGetObject(Obj) && (*Obj)->TryGetStringField(TEXT("name"), Name)
						&& (*Obj)->TryGetNumberField(TEXT("terrain_tile_m"), TileM) && TileM > 0.0)
					{
						Cache.Add(Name, (float)(TileM * 100.0));
					}
				}
			}
		}
	}
	if (const float* Cm = Cache.Find(Area))
	{
		return *Cm;
	}
	ODBakeLink::FUserArea User;
	return ODBakeLink::LoadUserArea(Area, User) ? User.TerrainTileCm : Default;
}

void AODGameMode::StartDrawnArea(const FString& Key)
{
	ODBakeLink::FUserArea Area;
	if (!ODBakeLink::LoadUserArea(Key, Area))
	{
		UE_LOG(LogTemp, Warning, TEXT("ODGameMode: ?odarea=%s isn't in data/user_areas.json - nothing to spawn"), *Key);
		return;
	}
	ODBakeLink::SetActiveArea(&Area);
	// spawned while HomeArea is still "" so SpawnAreaSet builds the whole set
	// (the template places none of the level-placed families); home from here on
	TArray<AActor*> Spawned = SpawnAreaSet(Key);
	HomeArea = Key;
	UE_LOG(LogTemp, Display, TEXT("ODGameMode: drawn area '%s' (%s) - %d manager(s), outline %d pts, terrain tile %.0f m"),
		*Key, *Area.Name, Spawned.Num(), Area.OutlineCm.Num(), Area.TerrainTileCm / 100.0f);

	AODRoadManager* Road = nullptr;
	for (AActor* A : Spawned)
	{
		Road = Road ? Road : Cast<AODRoadManager>(A);
	}
	// runtime map texture: what build_openworld_test_level.py imports as an
	// asset for hand areas, read straight from data/road_tiles/ here
	const FString TexDir = ODBakeLink::RepoRoot() / TEXT("data/road_tiles") / (Key + TEXT("_test"));
	FString MetaText;
	TSharedPtr<FJsonObject> Meta;
	if (Road && FFileHelper::LoadFileToString(MetaText, *(TexDir / TEXT("map_texture_meta.json")))
		&& FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(MetaText), Meta) && Meta.IsValid())
	{
		if (UTexture2D* Tex = FImageUtils::ImportFileAsTexture2D(TexDir / TEXT("map_texture.png")))
		{
			Road->MapTexture = Tex;
			Road->MapTextureCenterCm = FVector2D(Meta->GetNumberField(TEXT("center_x_cm")), Meta->GetNumberField(TEXT("center_y_cm")));
			Road->MapTextureSpanCm = (float)Meta->GetNumberField(TEXT("span_cm"));
		}
	}

	if (Area.OutlineCm.Num() >= 3)
	{
		GetWorld()->SpawnActor<AODBoundaryEffect>();
	}

	// the car to the runner's road-snapped start (else the drawn one); the
	// menu captures this as its default "Drive" spot
	FVector2D Start = Area.StartCm;
	if (!ODBakeLink::SnappedStartCm(Start) && !Area.bHasStart)
	{
		return;
	}
	float Yaw = 0.0f;
	FVector2D Snap;
	float SnapYaw;
	if (Road && Road->NearestRoadPoint(Start, Snap, SnapYaw))
	{
		Start = Snap;
		Yaw = SnapYaw;
	}
	if (AODVehiclePawn* Pawn = Cast<AODVehiclePawn>(UGameplayStatics::GetPlayerPawn(this, 0)))
	{
		Pawn->TeleportToSpawn(FVector(Start.X, Start.Y, 0.0), Yaw);
	}
}

TArray<AActor*> AODGameMode::SpawnAreaSet(const FString& Area, const FVector& Offset, const FODVistaRegion* Vista, UClass* Only)
{
	TArray<AActor*> Out;
	UWorld* World = GetWorld();
	if (!World || Area.IsEmpty())
	{
		return Out;
	}
	// Home area (2026-10-05): exactly the old Ensure*Manager behaviour - its
	// terrain/road/building/wall/barrier managers are level-placed, and each
	// other class is skipped when one already exists in the .umap (Tail of
	// the Dragon's hand-placed forest, OpenWorldTest's baked-in one).
	// Every other area gets the whole set, level-placed families included,
	// with the same properties unreal/build_openworld_test_level.py bakes.
	const bool bHome = !Vista && (Area == HomeArea);
	// Menu vista copy (2026-10-05): only the families with a vista mode
	// (FODVistaRegion) - terrain, road, forest, water, guardrail, furniture.
	// ponytail: no buildings/walls/barriers/parking/embankments/bridges in
	// vistas; each needs its Tick filtered and (buildings) its ground trace
	// offset like the forest's. Add per class if a vista looks bare.
	const FTransform Xf(Offset);

	// Deferred spawn everywhere (2026-08-26 fix, found via -odshot QA): a
	// plain SpawnActor() runs BeginPlay() synchronously before the caller
	// can set any tile/file prefix, and the forest (LoadBiomePoints), road
	// (LoadCenterline/route graph/labels) and terrain (road-station/biome
	// sidecars) managers all read theirs in BeginPlay. Deferring lets the
	// prefixes land first, same as a hand-placed .umap instance's properties.
	// Water/parking/guardrail/embankment/bridge only read theirs from Tick(),
	// so for them this is equivalent to the old SpawnActor-then-assign.
	auto Begin = [&](UClass* Cls, bool bLevelPlaced) -> AActor*
	{
		if ((Vista && !VistaClasses().Contains(Cls)) || (Only && Cls != Only))
		{
			return nullptr;
		}
		if (bHome)
		{
			if (bLevelPlaced)
			{
				return nullptr;
			}
			for (TActorIterator<AActor> It(World, Cls); It; ++It)
			{
				return nullptr;   // already hand-placed - leave it alone
			}
		}
		return World->SpawnActorDeferred<AActor>(Cls, Xf);
	};
	auto Finish = [&](AActor* A)
	{
		if (Vista)
		{
			A->Tags.Add(ODVistaTag);
		}
		const double T0 = FPlatformTime::Seconds();
		A->FinishSpawning(Xf);
		Out.Add(A);
		UE_LOG(LogTemp, Display, TEXT("ODGameMode: spawned %s for area '%s' in %.0f ms"),
			*A->GetClass()->GetName(), *Area, (FPlatformTime::Seconds() - T0) * 1000.0);
	};

	if (AODTerrainManager* Terrain = Cast<AODTerrainManager>(Begin(AODTerrainManager::StaticClass(), true)))
	{
		Terrain->TerrainTilePrefix = FString::Printf(TEXT("terrain_%s_test"), *Area);
		// drawn areas bake 1 km tiles (2026-10-21): worlds.json terrain_tile_m
		Terrain->TileSizeCm = TerrainTileCmFor(Area, Terrain->TileSizeCm);
		// build_openworld_test_level.py's 6.2 km: past the 6 km far-tile bake
		Terrain->VisualRadius = 620000.0f;
		if (Vista)
		{
			Terrain->Vista = *Vista;   // wedge-filtered; default CollisionRadius gives its tiles Near
			// ring reach must cover the panorama wedge (coarse, 2026-10-05 trial)
			Terrain->VisualRadius = FMath::Max(Terrain->VisualRadius, (float)Vista->PanoFarCm);
		}
		Finish(Terrain);
	}
	// road before barrier: AODBarrierManager::BeginPlay binds to its area's
	// road manager by prefix
	if (AODRoadManager* Road = Cast<AODRoadManager>(Begin(AODRoadManager::StaticClass(), true)))
	{
		Road->RoadTilePrefix = FString::Printf(TEXT("road_%s_test"), *Area);
		if (Vista)
		{
			Road->Vista = *Vista;   // chunks only: no route graph/map/labels to load
		}
		else
		{
			Road->CenterlineFilePrefix = FString::Printf(TEXT("centerline_%s_test"), *Area);
			Road->MapCenterlineFilePrefix = FString::Printf(TEXT("centerline_map_%s_test"), *Area);
			Road->PlaceLabelFilePrefix = FString::Printf(TEXT("labels_%s_test"), *Area);
		}
		// MapTexture stays home-only (ODRaceHUD draws it from the home manager)
		Finish(Road);
	}
	if (AODBuildingManager* Building = Cast<AODBuildingManager>(Begin(AODBuildingManager::StaticClass(), true)))
	{
		Building->BuildingTilePrefix = FString::Printf(TEXT("buildings_%s_test"), *Area);
		Finish(Building);
	}
	if (AODWallManager* Wall = Cast<AODWallManager>(Begin(AODWallManager::StaticClass(), true)))
	{
		Wall->WallTilePrefix = FString::Printf(TEXT("walls_%s_test"), *Area);
		Finish(Wall);
	}
	if (AODBarrierManager* Barrier = Cast<AODBarrierManager>(Begin(AODBarrierManager::StaticClass(), true)))
	{
		Barrier->BarrierTilePrefix = FString::Printf(TEXT("barrier_%s_test"), *Area);
		Finish(Barrier);
	}

	if (AODForestManager* Forest = Cast<AODForestManager>(Begin(AODForestManager::StaticClass(), false)))
	{
		// DefaultBiome is left at its DeciduousForest default deliberately -
		// BiomeFilePrefix activates LoadBiomePoints()'s point-classified path,
		// and BlendAt() only falls back to DefaultBiome per-tile when a tile
		// has no classified point within BiomeBlendRadiusCm at all. Each
		// Load*() below independently no-ops false on a missing file (the
		// "optional feature" convention ODForestManager.h documents), so an
		// area without a given sidecar baked yet just runs without it.
		Forest->BiomeFilePrefix = FString::Printf(TEXT("biome_%s_test"), *Area);
		Forest->ElevationFilePrefix = FString::Printf(TEXT("elev_%s_test"), *Area);
		Forest->CenterlineFilePrefix = FString::Printf(TEXT("centerline_%s_test"), *Area);
		Forest->BuildingCentroidFilePrefix = FString::Printf(TEXT("building_centroids_%s_test"), *Area);
		// roadstations_{area}.bin - NO "_test" suffix, unlike every prefix above
		// (matches AODTerrainManager::LoadRoadStationSidecar's own area-name
		// derivation). See AODForestManager::RoadStationFilePrefix's comment
		// (2026-08-26, Taylor's live-drive "trees on the road" report).
		Forest->RoadStationFilePrefix = FString::Printf(TEXT("roadstations_%s"), *Area);
		// water_{area}_test_tile_*.bin - the SAME baked lake tiles the water
		// manager below streams, reused here purely for polygon geometry
		// (2026-09-03, trees found standing in open water at Dillon Reservoir).
		Forest->WaterTilePrefix = FString::Printf(TEXT("water_%s_test"), *Area);
		if (Vista)
		{
			Forest->Vista = *Vista;
			Forest->SpawnRadius = 60000.0f;   // = the tree HISM far cull
		}
		Finish(Forest);
	}
	if (AODWaterManager* Water = Cast<AODWaterManager>(Begin(AODWaterManager::StaticClass(), false)))
	{
		Water->WaterTilePrefix = FString::Printf(TEXT("water_%s_test"), *Area);
		if (Vista)
		{
			Water->Vista = *Vista;
		}
		Finish(Water);
	}
	if (AODParkingManager* Parking = Cast<AODParkingManager>(Begin(AODParkingManager::StaticClass(), false)))
	{
		Parking->ParkingTilePrefix = FString::Printf(TEXT("parking_%s_test"), *Area);
		Finish(Parking);
	}
	if (AODGuardrailManager* Guardrail = Cast<AODGuardrailManager>(Begin(AODGuardrailManager::StaticClass(), false)))
	{
		Guardrail->GuardrailTilePrefix = FString::Printf(TEXT("guardrail_%s_test"), *Area);
		if (Vista)
		{
			Guardrail->Vista = *Vista;
		}
		Finish(Guardrail);
	}
	// Roadside furniture (PLAN-REALISM Phase 1, 2026-10-06): same tile grid
	// and vista mode as the guardrails, spawned wherever they are.
	if (AODFurnitureManager* Furniture = Cast<AODFurnitureManager>(Begin(AODFurnitureManager::StaticClass(), false)))
	{
		Furniture->FurnitureTilePrefix = FString::Printf(TEXT("furniture_%s_test"), *Area);
		if (Vista)
		{
			Furniture->Vista = *Vista;
		}
		Finish(Furniture);
	}
	if (AODEmbankmentManager* Embankment = Cast<AODEmbankmentManager>(Begin(AODEmbankmentManager::StaticClass(), false)))
	{
		Embankment->EmbankmentTilePrefix = FString::Printf(TEXT("embankment_%s_test"), *Area);
		Finish(Embankment);
	}
	if (AODBridgeManager* Bridge = Cast<AODBridgeManager>(Begin(AODBridgeManager::StaticClass(), false)))
	{
		Bridge->BridgeTilePrefix = FString::Printf(TEXT("bridge_%s_test"), *Area);
		Finish(Bridge);
	}
	// Tunnel bores + light strips (pipeline/build_tunnels.py) and sidewalks +
	// driveway stubs (pipeline/build_sidewalks.py): more wall-manager
	// instances, spawned directly since the home level already places one
	// AODWallManager (Begin() would skip the class).
	if (!Vista)
	{
		for (const TCHAR* Kind : {TEXT("tunnel"), TEXT("tunnellights"), TEXT("sidewalk")})
		{
			AODWallManager* T = World->SpawnActorDeferred<AODWallManager>(AODWallManager::StaticClass(), Xf);
			T->WallTilePrefix = FString::Printf(TEXT("%s_%s_test"), Kind, *Area);
			const bool bLights = FCString::Strcmp(Kind, TEXT("tunnellights")) == 0;
			T->MaterialPath = bLights ? TEXT("/Game/OpenDrive/Materials/MI_Furn_lamp.MI_Furn_lamp")
				: TEXT("/Game/OpenDrive/Materials/M_Bridge.M_Bridge");
			T->Glow = bLights ? 20.0f : -1.0f;
			Finish(T);
		}
	}
	return Out;
}

void AODGameMode::LoadWorldManifest()
{
	// Content/OpenDrive/Masks/worlds.json (pipeline/build_world_manifest.py):
	// {"<world>": [{"name": "<area>", "rect_cm": [xmin, ymin, xmax, ymax]}, ...]}.
	// Absent file / home area not listed = no streaming at all (Summit, Tail
	// of the Dragon, every non-world area behave exactly as before).
	if (HomeArea.IsEmpty())
	{
		return;
	}
	const FString Path = ODTileSource::Fetch(FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks/worlds.json"));
	FString Text;
	if (!FFileHelper::LoadFileToString(Text, *Path))
	{
		return;
	}
	TSharedPtr<FJsonObject> Root;
	if (!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Root) || !Root.IsValid())
	{
		UE_LOG(LogTemp, Warning, TEXT("ODGameMode: couldn't parse %s - no area streaming"), *Path);
		return;
	}
	for (const TPair<FString, TSharedPtr<FJsonValue>>& World : Root->Values)
	{
		const TArray<TSharedPtr<FJsonValue>>* Members = nullptr;
		if (!World.Value.IsValid() || !World.Value->TryGetArray(Members))
		{
			continue;
		}
		TArray<FODStreamArea> Found;
		bool bHasHome = false;
		for (const TSharedPtr<FJsonValue>& V : *Members)
		{
			const TSharedPtr<FJsonObject>* Obj = nullptr;
			const TArray<TSharedPtr<FJsonValue>>* R = nullptr;
			FString Name;
			if (!V.IsValid() || !V->TryGetObject(Obj) || !(*Obj)->TryGetStringField(TEXT("name"), Name)
				|| !(*Obj)->TryGetArrayField(TEXT("rect_cm"), R) || R->Num() != 4)
			{
				continue;
			}
			if (Name == HomeArea)
			{
				bHasHome = true;   // home's level-placed set is never streamed
				continue;
			}
			FODStreamArea& A = Found.AddDefaulted_GetRef();
			A.Name = Name;
			(*Obj)->TryGetStringArrayField(TEXT("files"), A.Files);
			A.Rect = FBox2D(FVector2D((*R)[0]->AsNumber(), (*R)[1]->AsNumber()),
				FVector2D((*R)[2]->AsNumber(), (*R)[3]->AsNumber()));
		}
		if (bHasHome)
		{
			StreamAreas = MoveTemp(Found);
			UE_LOG(LogTemp, Display, TEXT("ODGameMode: world '%s' - home '%s', %d other area(s) to stream"),
				*World.Key, *HomeArea, StreamAreas.Num());
			return;
		}
	}
}

void AODGameMode::PrefetchArea(FODStreamArea& A)
{
	if (A.bPrefetching)
	{
		return;
	}
	A.bPrefetching = true;
	TArray<FString> Paths;
	for (const FString& F : A.Files)
	{
		Paths.Add(FPaths::ProjectContentDir() / TEXT("OpenDrive") / F);
	}
	TWeakObjectPtr<AODGameMode> WeakThis(this);
	Async(EAsyncExecution::ThreadPool, [WeakThis, Name = A.Name, Paths]()
	{
		// ponytail: one worker, files in order, a file still pending after ~1 min
		// is skipped (the area spawns without it) - parallelize if areas get big.
		constexpr int32 MaxTries = 30;
		int32 NumGaveUp = 0;
		for (const FString& P : Paths)
		{
			FString Unused;
			int32 Tries = 0;
			while (ODTileSource::Resolve(P, Unused) == EODTileSource::Pending && ++Tries < MaxTries)
			{
				FPlatformProcess::Sleep(ODTileSource::RetryDelaySeconds);
			}
			NumGaveUp += (Tries == MaxTries);
		}
		AsyncTask(ENamedThreads::GameThread, [WeakThis, Name, NumFiles = Paths.Num(), NumGaveUp]()
		{
			if (!WeakThis.IsValid())
			{
				return;
			}
			for (FODStreamArea& A : WeakThis->StreamAreas)
			{
				if (A.Name == Name)
				{
					A.bPrefetching = false;
					A.bPrefetched = true;
				}
			}
			UE_LOG(LogTemp, Display, TEXT("ODGameMode: area '%s' prefetched %d file(s), %d still pending (skipped)"),
				*Name, NumFiles, NumGaveUp);
		});
	});
}

void AODGameMode::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	if (StreamAreas.Num() == 0 || bStreamingFrozen)
	{
		return;
	}
	const double Now = GetWorld()->GetTimeSeconds();
	if (Now - LastStreamCheckTime < 0.5)
	{
		return;
	}
	LastStreamCheckTime = Now;
	const APawn* Pawn = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Pawn)
	{
		return;
	}
	// Spawn an area's set under 8 km from its rect (0 inside), destroy it
	// past 16 km - the gap is hysteresis so a set doesn't thrash on the edge.
	// Mirrored by pipeline/tests/test_world_manifest.py; change both together.
	const FVector2D P(Pawn->GetActorLocation().X, Pawn->GetActorLocation().Y);
	for (FODStreamArea& A : StreamAreas)
	{
		const double D = FMath::Sqrt(A.Rect.ComputeSquaredDistanceToPoint(P));
		if (!A.bLive && D < 800000.0)
		{
			if (ODTileSource::IsEnabled() && !A.bPrefetched)
			{
				PrefetchArea(A);   // spawns on a later check, once its files are cached
				continue;
			}
			A.Actors.Reset();
			for (AActor* Actor : SpawnAreaSet(A.Name))
			{
				A.Actors.Add(Actor);
			}
			A.bLive = true;
			UE_LOG(LogTemp, Display, TEXT("ODGameMode: area '%s' in range (%.1f km) - spawned %d actors"),
				*A.Name, D / 100000.0, A.Actors.Num());
		}
		else if (A.bLive && D > 1600000.0)
		{
			// In-flight async tile loads are safe: every manager's worker only
			// parses into by-value data, and its game-thread completion checks
			// its WeakThis, which Destroy() (EndPlay + MarkAsGarbage) invalidates.
			for (AActor* Actor : A.Actors)
			{
				if (IsValid(Actor))
				{
					Actor->Destroy();
				}
			}
			A.Actors.Reset();
			A.bLive = false;
			UE_LOG(LogTemp, Display, TEXT("ODGameMode: area '%s' out of range (%.1f km) - destroyed"),
				*A.Name, D / 100000.0);
		}
	}
}

void AODGameMode::EnsureAtmosphereController()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	// Unlike the area managers above, this doesn't need an AODRoadManager/
	// area name - it isn't tile-streamed, and applies identically to every level.
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

	// Same "already hand-placed, leave it alone" guard as SpawnAreaSet's.
	for (TActorIterator<AODAmbientSoundManager> It(World); It; ++It)
	{
		return;
	}

	// Same AODRoadManager-derived area-name convention as SpawnAreaSet.
	AODRoadManager* Road = nullptr;
	for (TActorIterator<AODRoadManager> It(World); It; ++It)
	{
		Road = *It;
		break;
	}
	if (!Road)
	{
		// Weather W2 (sec 7.40): no road (e.g. Tail of the Dragon) still gets
		// the manager for its weather layers (rain/wind/thunder); an empty
		// AmbientZoneFile just leaves the zone layers silent.
		if (AODAmbientSoundManager* Ambient = World->SpawnActor<AODAmbientSoundManager>())
		{
			Ambient->AmbientZoneFile.Empty();   // not the summit_county default
		}
		UE_LOG(LogTemp, Display, TEXT("ODGameMode: spawned AODAmbientSoundManager (no road - weather layers only)"));
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
