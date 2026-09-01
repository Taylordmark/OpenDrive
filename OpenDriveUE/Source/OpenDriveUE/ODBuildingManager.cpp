#include "ODBuildingManager.h"

#include "ODBinSidecarUtil.h"
#include "ODTerrainManager.h"
#include "ODVehiclePawn.h"
#include "Async/Async.h"
#include "Components/DynamicMeshComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/DynamicMeshOverlay.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInterface.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

using namespace UE::Geometry;

AODBuildingManager::AODBuildingManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODBuildingManager::BeginPlay()
{
	Super::BeginPlay();

	FacadeMat = LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_BuildingFacade.M_BuildingFacade"));

	ClampSpawnRadiusToTerrainCollision();
}

void AODBuildingManager::ClampSpawnRadiusToTerrainCollision()
{
	const AODTerrainManager* Terrain = nullptr;
	for (TActorIterator<AODTerrainManager> It(GetWorld()); It; ++It)
	{
		Terrain = *It;
		break;
	}
	if (!Terrain)
	{
		return;   // no terrain manager in this level - nothing to clamp against
	}

	// Small safety margin below the terrain's own near-tier collision ring -
	// a building tile requested right at that exact boundary would otherwise
	// race the ring's own per-tick recentering around the player.
	constexpr float SafetyMarginCm = 20000.0f;   // 200 m
	const float MaxSafeRadius = FMath::Max(0.0f, Terrain->CollisionRadius - SafetyMarginCm);
	if (SpawnRadius > MaxSafeRadius)
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODBuildingManager: SpawnRadius %.0f m exceeds AODTerrainManager's "
			     "CollisionRadius %.0f m (minus a %.0f m safety margin) - buildings "
			     "out that far would never find real ground collision to trace "
			     "against (terrain only keeps collision within CollisionRadius of "
			     "the player, no matter how long placement waits), so every one of "
			     "them would just churn through the retry queue until it times out "
			     "and gets dropped. Clamping SpawnRadius down to %.0f m for this run."),
			SpawnRadius / 100.0, Terrain->CollisionRadius / 100.0, SafetyMarginCm / 100.0,
			MaxSafeRadius / 100.0);
		SpawnRadius = MaxSafeRadius;
	}
}

void AODBuildingManager::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	if (PendingTiles.Num() > 0)
	{
		UE_LOG(LogTemp, Display,
			TEXT("ODBuildingManager: EndPlay with %d tile load(s) still in flight - "
			     "their completions will no-op harmlessly when they land"),
			PendingTiles.Num());
	}
	Super::EndPlay(EndPlayReason);
}

bool AODBuildingManager::ParseBuildingTileFile(const FString& Path, FParsedBuildingTile& Out)
{
	TArray<uint8> Bin;
	if (!FFileHelper::LoadFileToArray(Bin, *Path) || Bin.Num() < 4 + GenerationIdBytes + 4
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'U' || Bin[3] != '1')
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

	if (Remaining() < 4)
	{
		return false;
	}
	const uint32 BuildingCount = ReadU32LE(Cur);
	Cur += 4;

	Out.Buildings.Reserve(BuildingCount);
	for (uint32 b = 0; b < BuildingCount; ++b)
	{
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
		Cur += NeedBytes;

		FParsedBuilding& Building = Out.Buildings.AddDefaulted_GetRef();
		Building.Verts.SetNumUninitialized(VCount * 3);
		for (uint32 v = 0; v < VCount; ++v)
		{
			Building.Verts[v * 3 + 0] = ReadF32LE(VertsBase + v * 12 + 0);
			Building.Verts[v * 3 + 1] = ReadF32LE(VertsBase + v * 12 + 4);
			Building.Verts[v * 3 + 2] = ReadF32LE(VertsBase + v * 12 + 8);
		}
		Building.Uvs.SetNumUninitialized(VCount * 2);
		for (uint32 v = 0; v < VCount; ++v)
		{
			Building.Uvs[v * 2 + 0] = ReadF32LE(UvBase + v * 8 + 0);
			Building.Uvs[v * 2 + 1] = ReadF32LE(UvBase + v * 8 + 4);
		}
		Building.Tris.SetNumUninitialized(TCount * 3);
		for (uint32 t = 0; t < TCount; ++t)
		{
			Building.Tris[t * 3 + 0] = ReadU32LE(TrisBase + t * 12 + 0);
			Building.Tris[t * 3 + 1] = ReadU32LE(TrisBase + t * 12 + 4);
			Building.Tris[t * 3 + 2] = ReadU32LE(TrisBase + t * 12 + 8);
		}
	}
	return true;
}

void AODBuildingManager::RequestTileLoad(const FIntPoint& Key)
{
	PendingTiles.Add(Key);
	const FString Path = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ FString::Printf(TEXT("%s_tile_%d_%d.bin"), *BuildingTilePrefix, Key.X, Key.Y);

	TWeakObjectPtr<AODBuildingManager> WeakThis(this);
	Async(EAsyncExecution::ThreadPool, [WeakThis, Key, Path]()
	{
		FParsedBuildingTile Parsed;
		const bool bOk = ParseBuildingTileFile(Path, Parsed);
		AsyncTask(ENamedThreads::GameThread,
			[WeakThis, Key, bOk, Parsed = MoveTemp(Parsed)]() mutable
			{
				if (!WeakThis.IsValid() || WeakThis->GetWorld() == nullptr)
				{
					return;
				}
				WeakThis->OnTileParsed(Key, bOk, MoveTemp(Parsed));
			});
	});
}

void AODBuildingManager::OnTileParsed(const FIntPoint& Key, bool bOk, FParsedBuildingTile&& Parsed)
{
	PendingTiles.Remove(Key);
	if (!bOk)
	{
		// expected for the vast majority of tiles - most of the map has no
		// buildings at all, same "no hole" semantics as every other
		// tile-streamed system here
		MissingTiles.Add(Key);
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
			TEXT("ODBuildingManager: tile (%d,%d) generation id %s does not match "
			     "%s's first-loaded generation id %s - tiles from different pipeline "
			     "runs may be mixed (AUDIT-RELIABILITY.md SS3.1/SS5.1)"),
			Key.X, Key.Y, *Parsed.GenerationId, *BuildingTilePrefix, *FirstGenerationId);
	}

	FinalizeTile(Key, MoveTemp(Parsed));
}

bool AODBuildingManager::GroundAt(double WorldX, double WorldY, const AActor* Ignore, float& OutZ) const
{
	FHitResult Hit;
	const FVector Start(WorldX, WorldY, PlayerZHint + GroundTraceMarginCm);
	const FVector End(WorldX, WorldY, PlayerZHint - GroundTraceMarginCm);
	FCollisionQueryParams Params;
	Params.AddIgnoredActor(this);          // our own building collision
	if (Ignore)
	{
		Params.AddIgnoredActor(Ignore);    // the player's car
	}
	if (!GetWorld()->LineTraceSingleByChannel(Hit, Start, End, ECC_WorldStatic, Params))
	{
		++NoHitRejectCount;
		return false;
	}
	// Same suspected-phantom-surface guard as AODForestManager::GroundAt() -
	// see this class's header comment on GroundSanityMaxDeltaCm: local relief
	// within one tile shouldn't plausibly exceed this from the player's own
	// settled height, so a hit further off is almost certainly the known
	// phantom collision surface at old-origin height, not real ground.
	if (FMath::Abs(Hit.ImpactPoint.Z - PlayerZHint) > GroundSanityMaxDeltaCm)
	{
		++PhantomRejectCount;
		return false;
	}
	OutZ = Hit.ImpactPoint.Z;
	return true;
}

bool AODBuildingManager::ComputeBuildingZOffset(const FParsedBuilding& B, const AActor* Ignore, float& OutOffsetCm) const
{
	const uint32 VCount = B.Verts.Num() / 3;
	if (VCount == 0)
	{
		return false;
	}

	// footprint base = the lowest baked Z among the building's own verts (the
	// extrusion's bottom ring) - everything else (walls, roof) sits above it
	// at a fixed baked offset, so translating the whole mesh by one Z delta
	// keeps that shape intact (no tilt, no per-vertex distortion).
	float BakedBaseZ = TNumericLimits<float>::Max();
	for (uint32 v = 0; v < VCount; ++v)
	{
		BakedBaseZ = FMath::Min(BakedBaseZ, B.Verts[v * 3 + 2]);
	}

	// Distinct XY corners at (approximately) the base ring - dedup so a dense
	// footprint polygon doesn't turn into dozens of redundant traces at the
	// same point. Capped so a pathological high-vertex-count footprint can't
	// blow up finalize-time cost.
	constexpr float ZEpsilonCm = 2.0f;
	constexpr float DedupEpsilonSqCm = 25.0f;   // 5 cm
	constexpr int32 MaxCorners = 32;
	TArray<FVector2D, TInlineAllocator<MaxCorners>> Corners;
	for (uint32 v = 0; v < VCount && Corners.Num() < MaxCorners; ++v)
	{
		if (FMath::Abs(B.Verts[v * 3 + 2] - BakedBaseZ) > ZEpsilonCm)
		{
			continue;
		}
		const FVector2D XY(B.Verts[v * 3 + 0], B.Verts[v * 3 + 1]);
		bool bDup = false;
		for (const FVector2D& Existing : Corners)
		{
			if (FVector2D::DistSquared(Existing, XY) < DedupEpsilonSqCm)
			{
				bDup = true;
				break;
			}
		}
		if (!bDup)
		{
			Corners.Add(XY);
		}
	}

	// Lowest successfully-traced corner - so a footprint spanning a slope
	// gets its base planted at the low side (minus EmbedMarginCm) rather than
	// averaging (which would float the low corner) or using any single
	// arbitrary corner (which could float the others).
	float LowestGroundZ = TNumericLimits<float>::Max();
	bool bFoundAny = false;
	for (const FVector2D& XY : Corners)
	{
		float Z;
		if (GroundAt(XY.X, XY.Y, Ignore, Z))
		{
			LowestGroundZ = FMath::Min(LowestGroundZ, Z);
			bFoundAny = true;
		}
	}
	if (!bFoundAny)
	{
		return false;
	}

	OutOffsetCm = (LowestGroundZ - EmbedMarginCm) - BakedBaseZ;
	return true;
}

void AODBuildingManager::CreateBuildingComponent(FBuildingTile& Tile, const FParsedBuilding& B, float ZOffsetCm)
{
	const uint32 VCount = B.Verts.Num() / 3;
	const uint32 TCount = B.Tris.Num() / 3;

	auto* Comp = NewObject<UDynamicMeshComponent>(this);
	Comp->SetupAttachment(RootComponent);
	Comp->RegisterComponent();
	// Pure vertical translation only - never a rotation - so the building
	// stays upright regardless of local terrain slope/normal (task spec:
	// "no tilt from terrain normal").
	Comp->SetRelativeLocation(FVector(0.0, 0.0, ZOffsetCm));

	Comp->EditMesh([&](FDynamicMesh3& Mesh)
	{
		Mesh.EnableAttributes();
		FDynamicMeshUVOverlay* UV = Mesh.Attributes()->PrimaryUV();

		TArray<int32> VertIds; VertIds.SetNumUninitialized(VCount);
		TArray<int32> UvIds; UvIds.SetNumUninitialized(VCount);

		for (uint32 v = 0; v < VCount; ++v)
		{
			VertIds[v] = Mesh.AppendVertex(FVector3d(
				B.Verts[v * 3 + 0], B.Verts[v * 3 + 1], B.Verts[v * 3 + 2]));
		}
		for (uint32 v = 0; v < VCount; ++v)
		{
			UvIds[v] = UV->AppendElement(
				FVector2f(B.Uvs[v * 2 + 0], B.Uvs[v * 2 + 1]));
		}
		for (uint32 t = 0; t < TCount; ++t)
		{
			const uint32 I0 = B.Tris[t * 3 + 0];
			const uint32 I1 = B.Tris[t * 3 + 1];
			const uint32 I2 = B.Tris[t * 3 + 2];
			const int32 Tid = Mesh.AppendTriangle(VertIds[I0], VertIds[I1], VertIds[I2]);
			if (Tid >= 0)
			{
				UV->SetTriangle(Tid, FIndex3i(UvIds[I0], UvIds[I1], UvIds[I2]));
			}
		}
	});

	// buildings always collide - unlike terrain's near/far split, a
	// building only ever exists close to the route at all
	Comp->EnableComplexAsSimpleCollision();
	Comp->SetCollisionProfileName(TEXT("BlockAll"));
	if (FacadeMat)
	{
		Comp->SetMaterial(0, FacadeMat);
	}

	Tile.Buildings.Add(Comp);
}

void AODBuildingManager::FinalizeTile(const FIntPoint& Key, FParsedBuildingTile&& Parsed)
{
	FBuildingTile& Tile = Tiles.Add(Key);
	Tile.Buildings.Reserve(Parsed.Buildings.Num());

	const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0);
	const double Now = GetWorld()->GetTimeSeconds();
	int32 NumDeferred = 0;

	for (const FParsedBuilding& B : Parsed.Buildings)
	{
		float ZOffsetCm = 0.0f;
		if (!ComputeBuildingZOffset(B, Player, ZOffsetCm))
		{
			// No usable ground trace anywhere on this building's footprint YET
			// - most likely AODTerrainManager hasn't finished streaming
			// collision under this specific spot even though the ground-snap
			// gate in Tick() confirmed the PLAYER'S own local ground is real
			// (that gate says nothing about the rest of the SpawnRadius disc,
			// and in a fast/headless run many building tiles can finalize
			// within the same fraction of a second the gate opens). Queue for
			// retry (see ProcessPendingBuildings()) instead of dropping it -
			// unlike a forest scatter candidate, a specific OSM building
			// footprint isn't interchangeable with any other, so silently
			// losing one to a streaming race is a much more visible
			// regression than one fewer tree in a dense stand.
			PendingBuildings.Add({Key, B, Now, Now});
			++NumDeferred;
			continue;
		}
		MinPlacementDeltaCm = FMath::Min(MinPlacementDeltaCm, ZOffsetCm);
		MaxPlacementDeltaCm = FMath::Max(MaxPlacementDeltaCm, ZOffsetCm);
		++PlacedCount;
		CreateBuildingComponent(Tile, B, ZOffsetCm);
	}

	UE_LOG(LogTemp, Display,
		TEXT("ODBuildingManager: tile (%d,%d) parsed %d building(s), %d placed, "
		     "%d deferred pending ground trace retry"),
		Key.X, Key.Y, Parsed.Buildings.Num(), Parsed.Buildings.Num() - NumDeferred, NumDeferred);
}

void AODBuildingManager::ReleaseTile(const FIntPoint& Key)
{
	if (FBuildingTile* Tile = Tiles.Find(Key))
	{
		for (const auto& Comp : Tile->Buildings)
		{
			if (Comp)
			{
				Comp->DestroyComponent();
			}
		}
		Tiles.Remove(Key);
	}
}

void AODBuildingManager::ProcessPendingBuildings()
{
	if (PendingBuildings.Num() == 0)
	{
		return;
	}
	const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0);
	const double Now = GetWorld()->GetTimeSeconds();

	// Iterate backwards so RemoveAtSwap (used for both the "resolved" and
	// "give up"/"tile gone" removals below) never skips an unvisited entry.
	for (int32 i = PendingBuildings.Num() - 1; i >= 0; --i)
	{
		FPendingBuilding& Pending = PendingBuildings[i];

		FBuildingTile* Tile = Tiles.Find(Pending.TileKey);
		if (!Tile)
		{
			// Tile was released (player drove out of range) before this
			// building ever resolved - about to be out of despawn range
			// anyway, so just drop it rather than place something that would
			// be torn down again almost immediately.
			PendingBuildings.RemoveAtSwap(i);
			continue;
		}
		if (Now < Pending.NextRetryTime)
		{
			continue;
		}

		float ZOffsetCm = 0.0f;
		if (ComputeBuildingZOffset(Pending.Building, Player, ZOffsetCm))
		{
			MinPlacementDeltaCm = FMath::Min(MinPlacementDeltaCm, ZOffsetCm);
			MaxPlacementDeltaCm = FMath::Max(MaxPlacementDeltaCm, ZOffsetCm);
			++PlacedCount;
			CreateBuildingComponent(*Tile, Pending.Building, ZOffsetCm);
			PendingBuildings.RemoveAtSwap(i);
			continue;
		}

		if (Now - Pending.FirstAttemptTime > PendingGiveUpAfterSec)
		{
			++GivenUpCount;
			UE_LOG(LogTemp, Warning,
				TEXT("ODBuildingManager: gave up placing a building in tile (%d,%d) "
				     "after %.1f s - no valid ground trace anywhere on its footprint "
				     "(genuine data gap, not just a streaming race)"),
				Pending.TileKey.X, Pending.TileKey.Y, PendingGiveUpAfterSec);
			PendingBuildings.RemoveAtSwap(i);
			continue;
		}

		Pending.NextRetryTime = Now + PendingRetryIntervalSec;
	}
}

void AODBuildingManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Player)
	{
		return;
	}

	// Ground-snap gate (2026-08-27, mirrors AODForestManager::Tick's identical
	// fix - see its own comment for the live-drive bug class this prevents):
	// don't dispatch any tile loads - and therefore don't finalize/place any
	// buildings - until the vehicle pawn has actually settled onto real
	// streamed terrain. Before this gate, a tile requested (and its async
	// parse completed) while the pawn was still parked at TeleportToSpawn's
	// fixed interim Z meant ComputeBuildingZOffset()'s ground trace ran
	// against terrain that either hadn't streamed in yet or was about to be
	// carved out from under it by AODTerrainManager::ApplyRoadCarve -
	// producing exactly the "grey slabs half-buried/tilted/floating" bug this
	// whole fix targets. Fails open for any pawn that isn't an
	// AODVehiclePawn, matching AODForestManager's identical gate.
	if (const AODVehiclePawn* VehiclePawn = Cast<AODVehiclePawn>(Player);
		VehiclePawn && !VehiclePawn->IsGroundSnapped())
	{
		if (!bLoggedGroundSnapWait)
		{
			bLoggedGroundSnapWait = true;
			UE_LOG(LogTemp, Display,
				TEXT("ODBuildingManager: waiting for vehicle ground-snap before "
				     "streaming any building tiles"));
		}
		return;
	}
	if (!bLoggedGroundSnapConfirmed)
	{
		bLoggedGroundSnapConfirmed = true;
		UE_LOG(LogTemp, Display,
			TEXT("ODBuildingManager: ground-snap confirmed - beginning building "
			     "tile streaming"));
	}

	const FVector Loc = Player->GetActorLocation();
	// see PlayerZHint's declaration comment (ODBuildingManager.h) -
	// GroundAt()'s trace is relative to this instead of a hardcoded absolute Z.
	PlayerZHint = Loc.Z;
	const FIntPoint Center(FMath::FloorToInt(Loc.X / TileSizeCm),
	                       FMath::FloorToInt(Loc.Y / TileSizeCm));

	const int32 LoadReach = FMath::CeilToInt(SpawnRadius / TileSizeCm);
	const int32 DespawnReach = FMath::CeilToInt((SpawnRadius + DespawnMarginCm) / TileSizeCm);

	int32 Dispatched = 0;
	for (int32 Ring = 0; Ring <= LoadReach && Dispatched < MaxNewLoadsPerTick; ++Ring)
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
				if (Tiles.Contains(Key) || PendingTiles.Contains(Key) || MissingTiles.Contains(Key))
				{
					continue;
				}
				RequestTileLoad(Key);
				++Dispatched;
			}
		}
	}

	TArray<FIntPoint> Gone;
	for (const auto& Pair : Tiles)
	{
		if (FMath::Max(FMath::Abs(Pair.Key.X - Center.X),
		               FMath::Abs(Pair.Key.Y - Center.Y)) > DespawnReach)
		{
			Gone.Add(Pair.Key);
		}
	}
	for (const FIntPoint& Key : Gone)
	{
		ReleaseTile(Key);
	}

	// Retry any building whose ground trace failed at its tile's finalize
	// time - see FinalizeTile()/ProcessPendingBuildings()'s own comments for
	// why this exists (the ground-snap gate above only proves the player's
	// own local ground is real, not that terrain has streamed under every
	// OTHER building tile inside SpawnRadius yet).
	ProcessPendingBuildings();

	if (const double Now = GetWorld()->GetTimeSeconds(); Now - LastDiagTime > 5.0)
	{
		LastDiagTime = Now;
		int32 NumBuildings = 0;
		for (const auto& Pair : Tiles)
		{
			NumBuildings += Pair.Value.Buildings.Num();
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODBuildingManager: tiles=%d pending=%d missing=%d buildings=%d"),
			Tiles.Num(), PendingTiles.Num(), MissingTiles.Num(), NumBuildings);
		// Placement-fix QA summary (2026-08-27, temporary instrumentation for
		// the "grey slabs misplaced vertically" live-drive bug) - proves every
		// placed building went through a ground-snap-gated trace, how many
		// were rejected as phantom-surface/no-hit, and the resulting Z
		// correction range vs. the untouched baked height. minDelta/maxDelta
		// stay at the sentinel MAX/Lowest until at least one building has
		// been placed.
		UE_LOG(LogTemp, Display,
			TEXT("ODBuildingManager: placed=%d phantomRejects=%d noHitRejects=%d "
			     "pendingRetry=%d givenUp=%d minDeltaCm=%.1f maxDeltaCm=%.1f"),
			PlacedCount, PhantomRejectCount, NoHitRejectCount,
			PendingBuildings.Num(), GivenUpCount,
			PlacedCount > 0 ? MinPlacementDeltaCm : 0.0f,
			PlacedCount > 0 ? MaxPlacementDeltaCm : 0.0f);
	}
}
