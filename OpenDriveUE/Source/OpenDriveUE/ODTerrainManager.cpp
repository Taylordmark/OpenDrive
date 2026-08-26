#include "ODTerrainManager.h"

#include "ODBinSidecarUtil.h"
#include "Async/Async.h"
#include "Components/DynamicMeshComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/DynamicMeshOverlay.h"
#include "DynamicMesh/MeshNormals.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInterface.h"
#include "HAL/PlatformFileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

using namespace UE::Geometry;

AODTerrainManager::AODTerrainManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODTerrainManager::BeginPlay()
{
	Super::BeginPlay();

	// Placeholder stylized ground material (unreal/apply_ground_material.py) -
	// slope-blended grass/rock plus a sand/beach blend near sea level, no
	// PM_Asphalt so 1.4's road/off-road grip split reads this as off-road,
	// which is correct (this is the ground beside the road, not the
	// driving surface). LoadObject failing is a silent no-op, same
	// null-safety convention as AODRoadManager::RoadMat.
	TerrainMat = LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_GroundStylized.M_GroundStylized"));
	UE_LOG(LogTemp, Display, TEXT("ODTerrainManager: TerrainMat load %s"),
		TerrainMat ? TEXT("OK") : TEXT("FAILED (terrain will use engine default material)"));

	// Diagnostic-only override (2026-08-25): lets a headless test bisect
	// how many resident near-fidelity (collision-enabled) tiles trigger the
	// open Chaos/UE5.8 collision-registration bug without a recompile per
	// step - see AODRoadManager's matching -odspawnradius= override.
	float OverrideRadius = 0.0f;
	if (FParse::Value(FCommandLine::Get(), TEXT("odcollisionradius="), OverrideRadius))
	{
		CollisionRadius = OverrideRadius;
		UE_LOG(LogTemp, Display, TEXT("ODTerrainManager: odcollisionradius override -> %.0f"), CollisionRadius);
	}
}

void AODTerrainManager::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	const int32 NumInFlight = PendingCoarseTiles.Num() + PendingNearTiles.Num();
	if (NumInFlight > 0)
	{
		UE_LOG(LogTemp, Display,
			TEXT("ODTerrainManager: EndPlay with %d tile load(s) still in flight - "
			     "their completions will no-op harmlessly when they land"),
			NumInFlight);
	}
	Super::EndPlay(EndPlayReason);
}

bool AODTerrainManager::ParseTerrainTileFile(const FString& Path, FParsedTerrainTile& Out)
{
	TArray<uint8> Bin;
	if (!FFileHelper::LoadFileToArray(Bin, *Path) || Bin.Num() < 4 + GenerationIdBytes + 9
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'T' || Bin[3] != '1')
	{
		return false;
	}

	const uint8* Cur = Bin.GetData() + 4;
	const uint8* End = Bin.GetData() + Bin.Num();
	auto Remaining = [&]() -> int64 { return (int64)(End - Cur); };

	if (Remaining() < GenerationIdBytes)
	{
		return false;
	}
	Out.GenerationId = ReadGenerationId(Cur);
	Cur += GenerationIdBytes;

	if (Remaining() < 1)
	{
		return false;
	}
	Out.bHasCollision = (*Cur != 0);
	Cur += 1;

	if (Remaining() < 8)
	{
		return false;
	}
	const uint32 VCount = ReadU32LE(Cur); Cur += 4;
	const uint32 TCount = ReadU32LE(Cur); Cur += 4;
	const int64 NeedBytes = (int64)VCount * 12 + (int64)VCount * 8 + (int64)TCount * 12;
	if (Remaining() < NeedBytes)
	{
		return false;
	}

	const uint8* VertsBase = Cur;
	const uint8* UvBase = Cur + (int64)VCount * 12;
	const uint8* TrisBase = Cur + (int64)VCount * 12 + (int64)VCount * 8;

	Out.Verts.SetNumUninitialized(VCount * 3);
	for (uint32 v = 0; v < VCount; ++v)
	{
		Out.Verts[v * 3 + 0] = ReadF32LE(VertsBase + v * 12 + 0);
		Out.Verts[v * 3 + 1] = ReadF32LE(VertsBase + v * 12 + 4);
		Out.Verts[v * 3 + 2] = ReadF32LE(VertsBase + v * 12 + 8);
	}
	Out.Uvs.SetNumUninitialized(VCount * 2);
	for (uint32 v = 0; v < VCount; ++v)
	{
		Out.Uvs[v * 2 + 0] = ReadF32LE(UvBase + v * 8 + 0);
		Out.Uvs[v * 2 + 1] = ReadF32LE(UvBase + v * 8 + 4);
	}
	Out.Tris.SetNumUninitialized(TCount * 3);
	for (uint32 t = 0; t < TCount; ++t)
	{
		Out.Tris[t * 3 + 0] = ReadU32LE(TrisBase + t * 12 + 0);
		Out.Tris[t * 3 + 1] = ReadU32LE(TrisBase + t * 12 + 4);
		Out.Tris[t * 3 + 2] = ReadU32LE(TrisBase + t * 12 + 8);
	}
	return true;
}

static FString TerrainTileFilePath(const FString& Prefix, const FIntPoint& Key)
{
	return FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ FString::Printf(TEXT("%s_tile_%d_%d.bin"), *Prefix, Key.X, Key.Y);
}

void AODTerrainManager::RequestTileLoad(const FIntPoint& Key, ETileFidelity Fidelity)
{
	TSet<FIntPoint>& Pending = (Fidelity == ETileFidelity::Near) ? PendingNearTiles : PendingCoarseTiles;
	Pending.Add(Key);

	// Near always reads TerrainTilePrefix's own file. Coarse prefers the
	// "terrain_coarse_<area>_..." variant baked alongside every NEAR tile
	// (pipeline/build_terrain_mesh_tiles.py's main()) so a route tile has
	// something cheap to show before it's close enough to warrant the
	// full build; a plain far tile never got that separate file - it only
	// ever had the one file, under the ordinary prefix, which already IS
	// the coarse content - so Coarse falls back to that same path when the
	// "_coarse_" one doesn't exist (2026-08-25: this fallback was missing
	// in the initial cut and silently starved every far/background tile).
	FString PrimaryPath = TerrainTileFilePath(TerrainTilePrefix, Key);
	FString FallbackPath;
	if (Fidelity == ETileFidelity::Coarse)
	{
		checkf(TerrainTilePrefix.StartsWith(TEXT("terrain_")),
			TEXT("TerrainTilePrefix %s must start with \"terrain_\""), *TerrainTilePrefix);
		const FString CoarsePrefix = TEXT("terrain_coarse_") + TerrainTilePrefix.Mid(8);
		FallbackPath = MoveTemp(PrimaryPath);
		PrimaryPath = TerrainTileFilePath(CoarsePrefix, Key);
	}

	TWeakObjectPtr<AODTerrainManager> WeakThis(this);
	Async(EAsyncExecution::ThreadPool, [WeakThis, Key, Fidelity, PrimaryPath, FallbackPath]()
	{
		FParsedTerrainTile Parsed;
		// Check existence first rather than just trying-then-falling-back -
		// every plain far tile is a guaranteed miss on the "_coarse_"
		// prefix, and that's most of the tiles in VisualRadius, so
		// avoiding LoadFileToArray's own "failed to read file" warning
		// spam here (LogStreaming) actually matters.
		const bool bUseFallback = !FallbackPath.IsEmpty()
			&& !FPlatformFileManager::Get().GetPlatformFile().FileExists(*PrimaryPath);
		const bool bOk = ParseTerrainTileFile(bUseFallback ? FallbackPath : PrimaryPath, Parsed);
		AsyncTask(ENamedThreads::GameThread,
			[WeakThis, Key, Fidelity, bOk, Parsed = MoveTemp(Parsed)]() mutable
			{
				if (!WeakThis.IsValid() || WeakThis->GetWorld() == nullptr)
				{
					return;
				}
				WeakThis->OnTileParsed(Key, Fidelity, bOk, MoveTemp(Parsed));
			});
	});
}

void AODTerrainManager::OnTileParsed(const FIntPoint& Key, ETileFidelity Fidelity, bool bOk, FParsedTerrainTile&& Parsed)
{
	TSet<FIntPoint>& Pending = (Fidelity == ETileFidelity::Near) ? PendingNearTiles : PendingCoarseTiles;
	Pending.Remove(Key);
	if (!bOk)
	{
		// For Coarse, also the expected outcome for any tile beyond the
		// pipeline's render-distance cutoff (build_terrain_mesh_tiles.py
		// never writes a file for those) - not a bug, same "no hole"
		// semantics AODRoadManager relies on for its own MissingTiles. For
		// Near, the normal, expected state for every plain far tile (they
		// never had a near variant) - upgrade attempts for those keys
		// simply stop here forever, which is correct.
		TSet<FIntPoint>& Missing = (Fidelity == ETileFidelity::Near) ? MissingNearTiles : MissingCoarseTiles;
		Missing.Add(Key);
		return;
	}

	if (!bHasFirstGenerationId)
	{
		FirstGenerationId = Parsed.GenerationId;
		bHasFirstGenerationId = true;
	}
	else if (!bGenerationMismatchWarned && Parsed.GenerationId != FirstGenerationId)
	{
		bGenerationMismatchWarned = true;
		UE_LOG(LogTemp, Warning,
			TEXT("ODTerrainManager: tile (%d,%d) generation id %s does not match "
			     "%s's first-loaded generation id %s - tiles from different pipeline "
			     "runs may be mixed (AUDIT-RELIABILITY.md SS3.1/SS5.1)"),
			Key.X, Key.Y, *Parsed.GenerationId, *TerrainTilePrefix, *FirstGenerationId);
	}

	// Actual mesh/collision build is deferred and budgeted (see
	// ProcessPendingTileBuilds) rather than done here synchronously -
	// 2026-08-25 Arc 7 rescope, same reasoning as
	// AODRoadManager::OnTileParsed's PendingChunkBuilds queue.
	FPendingTerrainBuild& Build = PendingTileBuilds.AddDefaulted_GetRef();
	Build.Key = Key;
	Build.Parsed = MoveTemp(Parsed);
	Build.TargetFidelity = Fidelity;
}

void AODTerrainManager::ProcessPendingTileBuilds()
{
	int32 Budget = TileBuildsPerFrameBudget;
	while (Budget > 0 && PendingTileBuilds.Num() > 0)
	{
		FPendingTerrainBuild Build = MoveTemp(PendingTileBuilds[0]);
		PendingTileBuilds.RemoveAt(0);
		--Budget;

		// A downgrade/upgrade target may have gone stale (tile fully
		// released, or the opposite fidelity already won a race) - only
		// act on it if still relevant.
		FTerrainTile* Existing = Tiles.Find(Build.Key);
		if (Existing && Existing->Fidelity == Build.TargetFidelity)
		{
			continue;
		}

		if (Existing && Existing->Comp)
		{
			Existing->Comp->DestroyComponent();
			Tiles.Remove(Build.Key);
		}

		FTerrainTile NewTile;
		BuildTile(NewTile, Build.Parsed, Build.TargetFidelity);
		Tiles.Add(Build.Key, MoveTemp(NewTile));
	}
}

void AODTerrainManager::BuildTile(FTerrainTile& Tile, const FParsedTerrainTile& Parsed, ETileFidelity Fidelity)
{
	const uint32 VCount = Parsed.Verts.Num() / 3;
	const uint32 TCount = Parsed.Tris.Num() / 3;

	auto* Comp = NewObject<UDynamicMeshComponent>(this);
	Comp->SetupAttachment(RootComponent);
	Comp->RegisterComponent();

	Comp->EditMesh([&](FDynamicMesh3& Mesh)
	{
		Mesh.EnableAttributes();
		FDynamicMeshUVOverlay* UV = Mesh.Attributes()->PrimaryUV();

		TArray<int32> VertIds; VertIds.SetNumUninitialized(VCount);
		TArray<int32> UvIds; UvIds.SetNumUninitialized(VCount);

		for (uint32 v = 0; v < VCount; ++v)
		{
			const float X = Parsed.Verts[v * 3 + 0];
			const float Y = Parsed.Verts[v * 3 + 1];
			const float Z = Parsed.Verts[v * 3 + 2];
			VertIds[v] = Mesh.AppendVertex(FVector3d(X, Y, Z));
		}
		for (uint32 v = 0; v < VCount; ++v)
		{
			UvIds[v] = UV->AppendElement(
				FVector2f(Parsed.Uvs[v * 2 + 0], Parsed.Uvs[v * 2 + 1]));
		}
		for (uint32 t = 0; t < TCount; ++t)
		{
			const uint32 I0 = Parsed.Tris[t * 3 + 0];
			const uint32 I1 = Parsed.Tris[t * 3 + 1];
			const uint32 I2 = Parsed.Tris[t * 3 + 2];
			const int32 Tid = Mesh.AppendTriangle(VertIds[I0], VertIds[I1], VertIds[I2]);
			if (Tid >= 0)
			{
				UV->SetTriangle(Tid, FIndex3i(UvIds[I0], UvIds[I1], UvIds[I2]));
			}
		}

		// M_GroundStylized's slope-based grass/rock blend reads
		// MaterialExpressionVertexNormalWS - without this, the normal
		// overlay is left empty and the material shades every tile as flat
		// black/near-black regardless of actual slope (the root cause of
		// the "no grass, no floor" bug: exposure/material-load were both
		// fine, the mesh just never had real normals).
		FMeshNormals::InitializeOverlayToPerVertexNormals(Mesh.Attributes()->PrimaryNormals(), false);
	});

	// Fidelity, not the file's own baked bHasCollision byte, decides
	// collision here: a near tile's coarse fallback carries bHasCollision
	// false same as a plain far tile, but we still want the Near-fidelity
	// *build* of that same tile key to get collision even though the file
	// read for the Coarse request never will.
	if (Fidelity == ETileFidelity::Near && Parsed.bHasCollision)
	{
		Comp->EnableComplexAsSimpleCollision();
		Comp->SetCollisionProfileName(TEXT("BlockAll"));
	}
	else
	{
		// far/background/not-yet-approached tile - paying for collision
		// setup here is pure waste until (if ever) the player gets close.
		Comp->SetCollisionProfileName(TEXT("NoCollision"));
	}
	if (TerrainMat)
	{
		Comp->SetMaterial(0, TerrainMat);
	}

	Tile.Comp = Comp;
	Tile.Fidelity = Fidelity;
}

void AODTerrainManager::ReleaseTile(const FIntPoint& Key)
{
	if (FTerrainTile* Tile = Tiles.Find(Key))
	{
		if (Tile->Comp)
		{
			Tile->Comp->DestroyComponent();
		}
		Tiles.Remove(Key);
	}
}

void AODTerrainManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	ProcessPendingTileBuilds();

	const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Player)
	{
		return;
	}
	const FVector Loc = Player->GetActorLocation();
	const FIntPoint Center(FMath::FloorToInt(Loc.X / TileSizeCm),
	                       FMath::FloorToInt(Loc.Y / TileSizeCm));

	// unlike road chunks, a terrain tile's geometry never extends beyond
	// its own tile bounds (build_terrain_mesh_tiles.py builds a plain grid
	// clipped exactly to [tx*TileSizeCm, (tx+1)*TileSizeCm)) - no
	// chunk-span padding needed on the load reach.
	const int32 VisualLoadReach = FMath::CeilToInt(VisualRadius / TileSizeCm);
	const int32 VisualDespawnReach = FMath::CeilToInt((VisualRadius + DespawnMarginCm) / TileSizeCm);
	const int32 CollisionUpgradeReach = FMath::CeilToInt(CollisionRadius / TileSizeCm);
	const int32 CollisionDowngradeReach = FMath::CeilToInt((CollisionRadius + DespawnMarginCm) / TileSizeCm);

	int32 Dispatched = 0;

	// Pass 1: coarse "always something there" load, out to VisualRadius -
	// this is the one that used to be the whole story (as SpawnRadius).
	for (int32 Ring = 0; Ring <= VisualLoadReach && Dispatched < MaxNewLoadsPerTick; ++Ring)
	{
		for (int32 dy = -Ring; dy <= Ring && Dispatched < MaxNewLoadsPerTick; ++dy)
		{
			for (int32 dx = -Ring; dx <= Ring && Dispatched < MaxNewLoadsPerTick; ++dx)
			{
				if (FMath::Max(FMath::Abs(dx), FMath::Abs(dy)) != Ring)
				{
					continue;
				}
				const FIntPoint Key = Center + FIntPoint(dx, dy);
				if (Tiles.Contains(Key) || PendingCoarseTiles.Contains(Key) || MissingCoarseTiles.Contains(Key))
				{
					continue;
				}
				RequestTileLoad(Key, ETileFidelity::Coarse);
				++Dispatched;
			}
		}
	}

	// Pass 2: upgrade coarse -> near for whatever's within the much
	// smaller CollisionRadius (Arc 7 rescope, 2026-08-25) - only tiles
	// that are actually near-corridor route tiles have a near variant to
	// upgrade to; everything else just no-ops into MissingNearTiles once.
	for (int32 Ring = 0; Ring <= CollisionUpgradeReach && Dispatched < MaxNewLoadsPerTick; ++Ring)
	{
		for (int32 dy = -Ring; dy <= Ring && Dispatched < MaxNewLoadsPerTick; ++dy)
		{
			for (int32 dx = -Ring; dx <= Ring && Dispatched < MaxNewLoadsPerTick; ++dx)
			{
				if (FMath::Max(FMath::Abs(dx), FMath::Abs(dy)) != Ring)
				{
					continue;
				}
				const FIntPoint Key = Center + FIntPoint(dx, dy);
				const FTerrainTile* Existing = Tiles.Find(Key);
				if (!Existing || Existing->Fidelity == ETileFidelity::Near
					|| PendingNearTiles.Contains(Key) || MissingNearTiles.Contains(Key))
				{
					continue;
				}
				RequestTileLoad(Key, ETileFidelity::Near);
				++Dispatched;
			}
		}
	}

	// Downgrade near -> coarse for anything that's fallen back outside
	// CollisionRadius (+hysteresis) - re-requests the coarse variant
	// through the normal budgeted path rather than swapping instantly.
	for (const auto& Pair : Tiles)
	{
		if (Pair.Value.Fidelity != ETileFidelity::Near)
		{
			continue;
		}
		const int32 Dist = FMath::Max(FMath::Abs(Pair.Key.X - Center.X), FMath::Abs(Pair.Key.Y - Center.Y));
		if (Dist > CollisionDowngradeReach && !PendingCoarseTiles.Contains(Pair.Key))
		{
			RequestTileLoad(Pair.Key, ETileFidelity::Coarse);
		}
	}

	TArray<FIntPoint> Gone;
	for (const auto& Pair : Tiles)
	{
		if (FMath::Max(FMath::Abs(Pair.Key.X - Center.X),
		               FMath::Abs(Pair.Key.Y - Center.Y)) > VisualDespawnReach)
		{
			Gone.Add(Pair.Key);
		}
	}
	for (const FIntPoint& Key : Gone)
	{
		ReleaseTile(Key);
	}

	if (const double Now = GetWorld()->GetTimeSeconds(); Now - LastDiagTime > 5.0)
	{
		LastDiagTime = Now;
		int32 NumNear = 0, NumCoarse = 0;
		for (const auto& Pair : Tiles)
		{
			(Pair.Value.Fidelity == ETileFidelity::Near ? NumNear : NumCoarse)++;
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODTerrainManager: tiles=%d (near=%d coarse=%d) pending=%d buildQueue=%d missing=%d"),
			Tiles.Num(), NumNear, NumCoarse,
			PendingCoarseTiles.Num() + PendingNearTiles.Num(), PendingTileBuilds.Num(),
			MissingCoarseTiles.Num() + MissingNearTiles.Num());
	}
}
