#include "ODBuildingManager.h"
#include "ODVisualStyle.h"

#include "ODBinSidecarUtil.h"
#include "ODTileSource.h"
#include "ODRoadManager.h"
#include "ODTerrainManager.h"
#include "ODVehiclePawn.h"
#include "Async/Async.h"
#include "Components/DynamicMeshComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/DynamicMeshOverlay.h"
#include "DynamicMesh/MeshNormals.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInterface.h"
#include "Misc/Crc.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

using namespace UE::Geometry;

namespace
{
	// Combine two 32-bit hash values (Boost-style hash_combine) - used below
	// to build a handful of decorrelated per-building pseudo-random channels
	// from the building's own baked footprint, with no new pipeline/sidecar
	// data needed (see BuildingVertexColorForFacade()'s own comment for why
	// this lives here instead of a shared header - it's a one-file need).
	uint32 HashCombine32(uint32 A, uint32 B)
	{
		return A ^ (B + 0x9e3779b9u + (A << 6) + (A >> 2));
	}

	float HashToUnitFloat(uint32 H)
	{
		return (float)(H % 1000000u) / 1000000.0f;
	}

	/** Per-building facade-material selectors, packed into vertex color and
	 *  read back by M_BuildingFacade (unreal/apply_building_material.py) -
	 *  the material-variation channel this streaming architecture can
	 *  actually afford: buildings are ONE UDynamicMeshComponent EACH (not
	 *  HISM-instanced, see FBuildingTile's header comment), so there's no
	 *  per-instance custom data to write into, and creating ~1800 separate
	 *  MIDs (one per building, live in the field) would multiply that
	 *  already-heavy per-building draw-call count by real per-MID overhead
	 *  for nothing - a single shared material reading a per-vertex value is
	 *  the same "avoid per-building MIDs" convention AODTerrainManager's own
	 *  vertex-color biome tint already established, just carrying different
	 *  payload channels:
	 *    R - wall texture set, baked per building from OSM type + height
	 *        (build_building_mesh.py pick_facade(), ODU2 WallSet byte),
	 *        encoded as (WallSet + 0.5) / NumWallSets so M_BuildingFacade's
	 *        floor(R*N) lands mid-bucket - a HARD per-building pick, not a
	 *        blend, so one building always reads as one material)
	 *    G - window-lit-fraction seed offset (decorrelates which windows
	 *        read as lit between buildings that would otherwise share the
	 *        same per-window hash pattern from a coincidentally similar
	 *        facade UV phase)
	 *    B - Stylized: roof type, 1 = concrete flat roof, 0 = shingle (the
	 *        material lerps its roof set on this); Realistic (roofs in their
	 *        own section): facade archetype, (Facade + 0.5) / NumFacades
	 *        (realism B2, apply_building_material_real.py FACADE_FLOORS)
	 *  G is hashed from the footprint's own baked centroid (deterministic,
	 *  unique per building's location) rather than any RNG, so
	 *  re-finalizing the same building (e.g. after a pending-retry) always
	 *  yields the exact same seed. */
	FVector4f BuildingVertexColorForFacade(const FParsedBuilding& B, bool bRealistic)
	{
		const uint32 VCount = B.Verts.Num() / 3;
		double CentroidX = 0.0, CentroidY = 0.0;
		for (uint32 v = 0; v < VCount; ++v)
		{
			CentroidX += B.Verts[v * 3 + 0];
			CentroidY += B.Verts[v * 3 + 1];
		}
		if (VCount > 0)
		{
			CentroidX /= VCount;
			CentroidY /= VCount;
		}

		const uint32 Hx = FCrc::MemCrc32(&CentroidX, sizeof(CentroidX));
		const uint32 Hy = FCrc::MemCrc32(&CentroidY, sizeof(CentroidY));
		const uint32 H2 = HashCombine32(Hy, HashCombine32(Hx, 0x2545F491u));

		// MUST match len(WALL_LAYERS) in unreal/apply_building_material*.py
		// (pipeline/tests/test_building_facades.py checks).
		constexpr uint8 NumWallSets = 7;
		const float R = (FMath::Min<uint8>(B.WallSet, NumWallSets - 1) + 0.5f) / NumWallSets;
		// MUST match len(FACADE_FLOORS) (test_building_facades.py checks)
		constexpr uint8 NumFacades = 5;
		const float Bch = bRealistic ? (FMath::Min<uint8>(B.Facade, NumFacades - 1) + 0.5f) / NumFacades
			: (B.FlatRoof ? 1.0f : 0.0f);
		return FVector4f(R, HashToUnitFloat(H2), Bch, 1.0f);
	}

	/** Realistic roof triangles (their own section, M_BuildingRoof_real):
	 *  RGB = the roof's linear albedo, A = roof set bucket ((set + 0.5) / 4:
	 *  tile, asphalt, metal, flat - apply_building_material_real.py
	 *  ROOF_LAYERS), both baked per building by build_building_mesh.py
	 *  roof_look (realism B3). A pitched roof's leftover flat cap (L/T/U
	 *  footprints, building_roofs.cover_roof) reads as a plain flat roof. */
	FVector4f RoofVertexColor(const FParsedBuilding& B, bool bLevelFace)
	{
		constexpr float NumRoofSets = 4.0f;
		if (bLevelFace && !B.FlatRoof)
		{
			return FVector4f(0.30f, 0.30f, 0.29f, 3.5f / NumRoofSets);
		}
		return FVector4f(B.RoofRgb[0] / 255.0f, B.RoofRgb[1] / 255.0f, B.RoofRgb[2] / 255.0f,
			(FMath::Min<uint8>(B.RoofSet, 3) + 0.5f) / NumRoofSets);
	}
}

AODBuildingManager::AODBuildingManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODBuildingManager::BeginPlay()
{
	Super::BeginPlay();

	FacadeMat = ODVisualStyle::Pick(LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_BuildingFacade.M_BuildingFacade")));
	// Realistic splits roofs into their own section (see RoofMat); the roof
	// material has no stylized twin, so it is loaded directly, not Picked.
	RoofMat = ODVisualStyle::IsRealistic()
		? LoadObject<UMaterialInterface>(nullptr,
			TEXT("/Game/OpenDrive/Materials/M_BuildingRoof_real.M_BuildingRoof_real"),
			nullptr, LOAD_NoWarn | LOAD_Quiet)
		: nullptr;

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
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'U' || Bin[3] < '2' || Bin[3] > '4')
	{
		return false;
	}
	// ODU3 (realism B3) adds the baked roof's look and end-wall split, ODU4
	// (B2) the facade archetype; an old tile still loads, with defaults
	const bool bOdu3 = Bin[3] >= '3';
	const bool bOdu4 = Bin[3] == '4';
	const int64 HeaderBytes = bOdu4 ? 19 : bOdu3 ? 18 : 10;

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
		if (Remaining() < HeaderBytes)
		{
			return false;
		}
		const uint32 VCount = ReadU32LE(Cur); Cur += 4;
		const uint32 TCount = ReadU32LE(Cur); Cur += 4;
		const uint8 WallSet = *Cur++;
		const uint8 FlatRoof = *Cur++;
		uint8 RoofSet = FlatRoof ? 3 : 1;
		uint8 RoofRgb[3] = {87, 87, 85};   // ~0.34 linear concrete
		uint32 NumSolid = MAX_uint32;
		uint8 Facade = 0;
		if (bOdu3)
		{
			RoofSet = *Cur++;
			RoofRgb[0] = *Cur++; RoofRgb[1] = *Cur++; RoofRgb[2] = *Cur++;
			Facade = bOdu4 ? *Cur++ : 0;
			NumSolid = ReadU32LE(Cur); Cur += 4;
		}
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
		Building.WallSet = WallSet;
		Building.FlatRoof = FlatRoof;
		Building.RoofSet = RoofSet;
		Building.Facade = Facade;
		FMemory::Memcpy(Building.RoofRgb, RoofRgb, 3);
		Building.NumSolidVerts = (int32)FMath::Min<uint32>(NumSolid, MAX_int32);
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
		FString ReadPath;
		const EODTileSource Src = ODTileSource::Resolve(Path, ReadPath);
		const bool bOk = Src == EODTileSource::Ready && ParseBuildingTileFile(ReadPath, Parsed);
		const bool bPending = Src == EODTileSource::Pending;
		AsyncTask(ENamedThreads::GameThread,
			[WeakThis, Key, bOk, bPending, Parsed = MoveTemp(Parsed)]() mutable
			{
				if (!WeakThis.IsValid() || WeakThis->GetWorld() == nullptr)
				{
					return;
				}
				if (bPending)
				{
					ODTileSource::RetryLater(WeakThis.Get(), WeakThis->PendingTiles, Key);
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
	// Corners more than CornerPitMaxDropCm below the building's highest hit
	// are ignored: one footprint can't span that much real relief, so it's a
	// terrain spike (e.g. a DEM-void pit) that would sink the whole building.
	TArray<float, TInlineAllocator<MaxCorners>> HitZs;
	float HighestGroundZ = -TNumericLimits<float>::Max();
	for (const FVector2D& XY : Corners)
	{
		float Z;
		if (GroundAt(XY.X, XY.Y, Ignore, Z))
		{
			HitZs.Add(Z);
			HighestGroundZ = FMath::Max(HighestGroundZ, Z);
		}
	}
	if (HitZs.Num() == 0)
	{
		return false;
	}
	float LowestGroundZ = HighestGroundZ;
	for (const float Z : HitZs)
	{
		if (Z >= HighestGroundZ - CornerPitMaxDropCm)
		{
			LowestGroundZ = FMath::Min(LowestGroundZ, Z);
		}
	}

	OutOffsetCm = (LowestGroundZ - EmbedMarginCm) - BakedBaseZ;
	return true;
}

bool AODBuildingManager::IsNearRoadCorridor(double WorldX, double WorldY)
{
	if (!bCorridorSearched)
	{
		bCorridorSearched = true;
		// this area's own road manager, matched by prefix (2026-10-05, large
		// world: several area sets can be live, first-found may be another's)
		FString Area = BuildingTilePrefix;
		Area.RemoveFromStart(TEXT("buildings_"));
		for (TActorIterator<AODRoadManager> It(GetWorld()); It; ++It)
		{
			if (It->RoadTilePrefix == TEXT("road_") + Area)
			{
				CorridorCenterline = It->RoadCenterline;   // world cm, already loaded
				break;
			}
		}
		for (int32 i = 0; i < CorridorCenterline.Num(); ++i)
		{
			const int32 CellX = FMath::FloorToInt(CorridorCenterline[i].X / CorridorCellCm);
			const int32 CellY = FMath::FloorToInt(CorridorCenterline[i].Y / CorridorCellCm);
			CorridorBuckets.FindOrAdd(FIntPoint(CellX, CellY)).Add(i);
		}
	}
	if (CorridorCenterline.Num() == 0)
	{
		return true;   // no centerline data - fail open, everything collides
	}

	const double BandSq = (double)CollisionBandCm * CollisionBandCm;
	const int32 CellX = FMath::FloorToInt(WorldX / CorridorCellCm);
	const int32 CellY = FMath::FloorToInt(WorldY / CorridorCellCm);
	// CollisionBandCm can exceed CorridorCellCm if a level tunes it up -
	// widen the ring accordingly instead of silently under-searching.
	const int32 Reach = FMath::Max(1, FMath::CeilToInt(CollisionBandCm / CorridorCellCm));
	for (int32 dy = -Reach; dy <= Reach; ++dy)
	{
		for (int32 dx = -Reach; dx <= Reach; ++dx)
		{
			const TArray<int32>* Bucket = CorridorBuckets.Find(FIntPoint(CellX + dx, CellY + dy));
			if (!Bucket)
			{
				continue;
			}
			for (const int32 i : *Bucket)
			{
				const double Dx = CorridorCenterline[i].X - WorldX;
				const double Dy = CorridorCenterline[i].Y - WorldY;
				if (Dx * Dx + Dy * Dy <= BandSq)
				{
					return true;
				}
			}
		}
	}
	return false;
}

void AODBuildingManager::AppendBuildingsToTileMesh(FBuildingTile& Tile, bool bNear,
	const TArray<TPair<const FParsedBuilding*, float>>& Batch)
{
	if (Batch.Num() == 0)
	{
		return;
	}

	TObjectPtr<UDynamicMeshComponent>& CompSlot = bNear ? Tile.NearMesh : Tile.FarMesh;
	if (!CompSlot)
	{
		auto* Comp = NewObject<UDynamicMeshComponent>(this);
		Comp->SetMeshDrawPath(EDynamicMeshDrawPath::StaticDraw);   // built once: cached draw commands, not re-gathered every frame per view/shadow view
		Comp->SetupAttachment(RootComponent);
		const TArray<float>& V0 = Batch[0].Key->Verts;
		if (V0.Num() >= 3)
		{
			Comp->SetRelativeLocation(FVector(V0[0], V0[1], 0.0));   // local origin: see AODRoadManager::CreateChunkMeshComponent (large-world precision)
		}
		Comp->RegisterComponent();
		if (bNear)
		{
			// Async cook (2026-09-07 frame-drop pass): a whole tile's merged
			// building geometry cooked synchronously on the game thread
			// otherwise - same flag the road/terrain/wall/guardrail managers
			// use. Buildings sit off the pavement, so a body that's a frame
			// or two late is unreachable anyway.
			Comp->bUseAsyncCooking = true;
			Comp->EnableComplexAsSimpleCollision();
			Comp->SetCollisionProfileName(TEXT("BlockAll"));
		}
		else
		{
			// out of driving reach - collision cooking is the expensive half
			// of a merged tile mesh, and it would never be touched
			Comp->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		}
		if (FacadeMat)
		{
			Comp->SetMaterial(0, FacadeMat);
		}
		if (RoofMat)
		{
			Comp->SetMaterial(1, RoofMat);
		}
		CompSlot = Comp;
	}

	const FVector3d O = CompSlot->GetRelativeLocation();
	CompSlot->EditMesh([&](FDynamicMesh3& Mesh)
	{
		Mesh.EnableAttributes();
		FDynamicMeshUVOverlay* UV = Mesh.Attributes()->PrimaryUV();
		Mesh.Attributes()->EnablePrimaryColors();
		FDynamicMeshColorOverlay* Color = Mesh.Attributes()->PrimaryColors();
		FDynamicMeshMaterialAttribute* MatId = nullptr;
		if (RoofMat)
		{
			Mesh.Attributes()->EnableMaterialID();
			MatId = Mesh.Attributes()->GetMaterialID();
		}

		for (const auto& Entry : Batch)
		{
			const FParsedBuilding& B = *Entry.Key;
			// Pure vertical translation only - never a rotation - so the
			// building stays upright regardless of local terrain slope
			// (task spec: "no tilt from terrain normal"). Baked into the
			// vertices now: a merged mesh has no per-building transform.
			const float ZOffsetCm = Entry.Value;
			const uint32 VCount = B.Verts.Num() / 3;
			const uint32 TCount = B.Tris.Num() / 3;

			// Same per-building facade selectors on every vertex (see
			// BuildingVertexColorForFacade()'s comment for why vertex
			// color, not a per-building MID, is this streaming
			// architecture's instancing-safe variation channel - and why
			// merging costs nothing: the variation already travels
			// per-vertex, not per-component).
			const FVector4f FacadeColor = BuildingVertexColorForFacade(B, MatId != nullptr);

			TArray<int32> VertIds; VertIds.SetNumUninitialized(VCount);
			TArray<int32> UvIds; UvIds.SetNumUninitialized(VCount);
			TArray<int32> ColorIds; ColorIds.SetNumUninitialized(VCount);
			TArray<int32> RoofColorIds; RoofColorIds.Init(INDEX_NONE, MatId ? VCount : 0);
			TArray<int32> LevelColorIds; LevelColorIds.Init(INDEX_NONE, MatId ? VCount : 0);

			for (uint32 v = 0; v < VCount; ++v)
			{
				VertIds[v] = Mesh.AppendVertex(FVector3d(
					B.Verts[v * 3 + 0] - O.X, B.Verts[v * 3 + 1] - O.Y,
					B.Verts[v * 3 + 2] + ZOffsetCm));
			}
			for (uint32 v = 0; v < VCount; ++v)
			{
				UvIds[v] = UV->AppendElement(
					FVector2f(B.Uvs[v * 2 + 0], B.Uvs[v * 2 + 1]));
			}
			for (uint32 v = 0; v < VCount; ++v)
			{
				// Vertex color here is NOT sRGB display color - see
				// BuildingVertexColorForFacade()'s comment, these are raw
				// [0,1) selector/seed values the material reads back
				// directly (default linear-in/linear-out, no
				// SetVertexColorSpaceTransformMode).
				// A = 0 on gable end walls: no windows there
				ColorIds[v] = Color->AppendElement(FVector4f(FacadeColor.X, FacadeColor.Y, FacadeColor.Z,
					(int32)v < B.NumSolidVerts ? 1.0f : 0.0f));
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
					// roof = faces up, the same 0.55 cut as the stylized
					// material's normal-Z roof mask (ROOF_MASK_THRESHOLD)
					const double Nz = Mesh.GetTriNormal(Tid).Z;
					if (MatId && Nz > 0.55)
					{
						MatId->SetValue(Tid, 1);
						// the roof's own colour elements (an overlay element
						// belongs to one vertex, so one per roof vertex, made
						// on first use); a level face (flat cap) gets its own
						const bool bLevel = Nz > 0.999;
						TArray<int32>& Ids = bLevel ? LevelColorIds : RoofColorIds;
						const FVector4f RoofColor = RoofVertexColor(B, bLevel);
						for (const uint32 I : {I0, I1, I2})
						{
							if (Ids[I] == INDEX_NONE)
							{
								Ids[I] = Color->AppendElement(RoofColor);
							}
						}
						Color->SetTriangle(Tid, FIndex3i(Ids[I0], Ids[I1], Ids[I2]));
					}
					else
					{
						if (MatId)
						{
							MatId->SetValue(Tid, 0);
						}
						Color->SetTriangle(Tid, FIndex3i(ColorIds[I0], ColorIds[I1], ColorIds[I2]));
					}
				}
			}
		}

		// Per-vertex normals, averaged only over triangles sharing the same
		// vertex ID - extrude_footprint() never shares a vertex between two
		// wall quads (or between wall top ring and roof fan), and the merge
		// never welds vertices between buildings, so this still yields
		// crisp flat-per-quad walls and flat roofs across the whole merged
		// mesh (M_BuildingFacade's wall-vs-roof mask keys off normal Z).
		// Recomputing over already-appended buildings on a retry batch is
		// redundant but yields identical values, so it's only a little
		// wasted work on the rare deferred-retry path.
		FMeshNormals::InitializeOverlayToPerVertexNormals(Mesh.Attributes()->PrimaryNormals(), false);
	});

	Tile.NumBuildings += Batch.Num();
}

void AODBuildingManager::FinalizeTile(const FIntPoint& Key, FParsedBuildingTile&& Parsed)
{
	FBuildingTile& Tile = Tiles.Add(Key);

	const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0);
	const double Now = GetWorld()->GetTimeSeconds();
	int32 NumDeferred = 0;

	// 2026-09-06 (AUDIT.md rec. 2): partition into the tile's two merged
	// meshes - near-corridor (collision) vs far (visual only) - and append
	// each side as ONE batch, so the whole tile costs two mesh builds and
	// at most one collision cook instead of one per building.
	TArray<TPair<const FParsedBuilding*, float>> NearBatch, FarBatch;
	NearBatch.Reserve(Parsed.Buildings.Num());

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

		double CentroidX = 0.0, CentroidY = 0.0;
		const uint32 VCount = B.Verts.Num() / 3;
		for (uint32 v = 0; v < VCount; ++v)
		{
			CentroidX += B.Verts[v * 3 + 0];
			CentroidY += B.Verts[v * 3 + 1];
		}
		if (VCount > 0)
		{
			CentroidX /= VCount;
			CentroidY /= VCount;
		}
		(IsNearRoadCorridor(CentroidX, CentroidY) ? NearBatch : FarBatch)
			.Emplace(&B, ZOffsetCm);
	}

	AppendBuildingsToTileMesh(Tile, true, NearBatch);
	AppendBuildingsToTileMesh(Tile, false, FarBatch);

	UE_LOG(LogTemp, Display,
		TEXT("ODBuildingManager: tile (%d,%d) parsed %d building(s), %d placed "
		     "(%d collidable near-corridor + %d visual-only far) into %d merged "
		     "component(s), %d deferred pending ground trace retry"),
		Key.X, Key.Y, Parsed.Buildings.Num(), Parsed.Buildings.Num() - NumDeferred,
		NearBatch.Num(), FarBatch.Num(),
		(Tile.NearMesh ? 1 : 0) + (Tile.FarMesh ? 1 : 0), NumDeferred);
}

void AODBuildingManager::ReleaseTile(const FIntPoint& Key)
{
	if (FBuildingTile* Tile = Tiles.Find(Key))
	{
		if (Tile->NearMesh)
		{
			Tile->NearMesh->DestroyComponent();
		}
		if (Tile->FarMesh)
		{
			Tile->FarMesh->DestroyComponent();
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

	// 2026-09-07 frame-drop pass: this used to call AppendBuildingsToTileMesh
	// with a batch of ONE inside the loop - each resolved building triggered
	// a full EditMesh + render-state rebuild + collision re-cook of the
	// entire tile's merged near mesh, several times per frame while the
	// deferral queue drained. Now: pass 1 resolves ground offsets (capped -
	// each attempt is up to 32 line traces), grouping resolved entries by
	// (tile, near/far); pass 2 appends each group with ONE mesh edit; pass 3
	// removes resolved/expired entries. Indices collected in pass 1 stay
	// valid through pass 2 because nothing is removed until pass 3, and
	// pass 3 removes in descending index order so RemoveAtSwap never
	// disturbs a lower pending index.
	constexpr int32 MaxResolveAttemptsPerTick = 8;
	int32 Attempts = 0;
	TMap<TPair<FIntPoint, bool>, TArray<TPair<const FParsedBuilding*, float>>> Resolved;
	TArray<int32, TInlineAllocator<16>> DoneIndices;

	for (int32 i = 0; i < PendingBuildings.Num(); ++i)
	{
		FPendingBuilding& Pending = PendingBuildings[i];

		if (!Tiles.Contains(Pending.TileKey))
		{
			// Tile was released (player drove out of range) before this
			// building ever resolved - about to be out of despawn range
			// anyway, so just drop it rather than place something that would
			// be torn down again almost immediately.
			DoneIndices.Add(i);
			continue;
		}
		if (Now < Pending.NextRetryTime)
		{
			continue;
		}
		if (Attempts >= MaxResolveAttemptsPerTick)
		{
			break;   // trace budget spent - the rest keep their NextRetryTime
		}
		++Attempts;

		float ZOffsetCm = 0.0f;
		if (ComputeBuildingZOffset(Pending.Building, Player, ZOffsetCm))
		{
			MinPlacementDeltaCm = FMath::Min(MinPlacementDeltaCm, ZOffsetCm);
			MaxPlacementDeltaCm = FMath::Max(MaxPlacementDeltaCm, ZOffsetCm);
			++PlacedCount;
			double CentroidX = 0.0, CentroidY = 0.0;
			const uint32 VCount = Pending.Building.Verts.Num() / 3;
			for (uint32 v = 0; v < VCount; ++v)
			{
				CentroidX += Pending.Building.Verts[v * 3 + 0];
				CentroidY += Pending.Building.Verts[v * 3 + 1];
			}
			if (VCount > 0)
			{
				CentroidX /= VCount;
				CentroidY /= VCount;
			}
			Resolved.FindOrAdd({Pending.TileKey, IsNearRoadCorridor(CentroidX, CentroidY)})
				.Emplace(&Pending.Building, ZOffsetCm);
			DoneIndices.Add(i);
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
			DoneIndices.Add(i);
			continue;
		}

		Pending.NextRetryTime = Now + PendingRetryIntervalSec;
	}

	for (auto& Group : Resolved)
	{
		if (FBuildingTile* Tile = Tiles.Find(Group.Key.Key))
		{
			AppendBuildingsToTileMesh(*Tile, Group.Key.Value, Group.Value);
		}
	}
	for (int32 d = DoneIndices.Num() - 1; d >= 0; --d)
	{
		PendingBuildings.RemoveAtSwap(DoneIndices[d]);
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
		VehiclePawn && !VehiclePawn->IsGroundResolved())
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
		int32 NumComponents = 0;
		for (const auto& Pair : Tiles)
		{
			NumBuildings += Pair.Value.NumBuildings;
			NumComponents += (Pair.Value.NearMesh ? 1 : 0) + (Pair.Value.FarMesh ? 1 : 0);
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODBuildingManager: tiles=%d pending=%d missing=%d buildings=%d components=%d"),
			Tiles.Num(), PendingTiles.Num(), MissingTiles.Num(), NumBuildings, NumComponents);
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
