#include "ODForestManager.h"

#include "Components/CapsuleComponent.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/Texture2D.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"
#include "LandscapeProxy.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "ODBinSidecarUtil.h"
#include "ODVehiclePawn.h"
#include "PhysicalMaterials/PhysicalMaterial.h"
#include "UnrealClient.h"

namespace
{
// TreeMeshPaths (the classic 3-species set) lives in ODBinSidecarUtil.h
// (2026-08-22) - was independently duplicated here and in
// ODBarrierManager.cpp, which collided under the standalone Game target's
// unity build (C2374/C2086). Nothing in THIS file uses TreeMeshPaths any
// more (BiomeRecipeTable below drives everything off the extended 7-species
// set instead) - it's kept alive purely as ODBarrierManager's own no-biome-
// data fallback (see that file's BiomeAt()).
//
// ExtendedTreeMeshPaths/NumExtendedTreeSpecies/PickWeightedIndex now ALSO
// live in ODBinSidecarUtil.h (2026-08-27) - were independently local to this
// file ("nothing else needs it yet") until ODBarrierManager.cpp became a
// second consumer for its own biome-aware windbreak species pick (Taylor's
// live-drive report: desert windbreak rows render as bright green trees);
// moved for the same unity-build ODR-collision reasoning that header's
// top-of-file comment already documents for TreeMeshPaths/ReadU32LE/
// ReadF32LE.
constexpr int32 NumTreeSpecies = NumExtendedTreeSpecies;   // local alias, keeps every call site below unchanged

/** Shrub-class meshes (2026-08-27) - never get trunk collision. Index 0 is
 *  the original plain bush; index 1 is sagebrush, added so biomes that
 *  should read as dry scrub (Chaparral, DesertScrub) don't just fall back
 *  to the same rounded green bush every other biome uses. */
const TCHAR* ShrubMeshPaths[] = {
	TEXT("/Game/OpenDrive/Trees/bush.bush"),
	TEXT("/Game/OpenDrive/Trees/sagebrush.sagebrush"),
};
constexpr int32 NumShrubSpecies = UE_ARRAY_COUNT(ShrubMeshPaths);

/** Indexed by EODBiome - order MUST match the enum in ODForestManager.h.
 *  FBiomeRecipe itself lives in the header (RecipeAt()/BlendAt() need the
 *  type there); the actual per-biome values stay local to this file since
 *  nothing outside this module needs them.
 *
 *  SpeciesWeight order: Blob, Olive, Conifer, ScrubOak, Manzanita, Yucca,
 *  CanyonOak. ShrubWeight order: Bush, Sagebrush. Species mix (2026-08-27,
 *  see FBiomeRecipe's comment for why this changed from a fixed 3-mesh
 *  set): DeciduousForest/EvergreenForest/Rainforest/Wetland keep their
 *  original blob/olive/conifer character basically unchanged (a sliver of
 *  the new woody species mixed in so biome boundaries don't look like a
 *  hard species swap). Chaparral is scrub-oak+manzanita dominant with
 *  sagebrush-heavy shrub and very few tall trees. DesertScrub is
 *  yucca-dominant with near-zero canopy species and sagebrush-heavy shrub.
 *  CanyonBadlands is canyon-oak dominant (sparse, via TreeDensityScale)
 *  with scrub-oak as the rest and sagebrush-heavy shrub. Everything else
 *  gets a plausible slice of the new species without changing its existing
 *  overall character.
 *
 * Trailing two values (added 2026-08-26) are RockDensityScale,
 * ScrubDensityScale - ground cover to fill hillsides (see FBiomeRecipe's
 * comment). Rocky/barren biomes (AlpineBare, CanyonBadlands, RockyFoothill)
 * get the heaviest rock density; chaparral/desert get rocks + scrub together;
 * dense-canopy forest biomes get few rocks (hidden under litter/duff anyway)
 * but heavier scrub as undergrowth fill.
 *
 * Final value (added 2026-08-27, see FBiomeRecipe::AmbientVegFloor's
 * comment) is only nonzero for the three open-ground biomes whose real-world
 * imagery reads as near-zero canopy ForestWeight despite having a genuine
 * (if sparse) shrub/scrub character: Chaparral, DesertScrub, CanyonBadlands.
 * Every other biome leaves it at the struct's own 0.f default (aggregate
 * init: trailing members not listed take their default member initializer),
 * preserving the original "ForestWeight 0 means truly nothing here" behavior
 * everywhere a bare ForestWeight of 0 is meaningful (lakes, town squares,
 * bare rock above treeline).
 */
const FBiomeRecipe BiomeRecipeTable[] = {
	/* DeciduousForest */ {{0.78f, 0.05f, 0.09f, 0.03f, 0.00f, 0.00f, 0.05f}, {0.90f, 0.10f}, 1.0f, 0.6f, 0.15f, 1.0f},
	/* EvergreenForest */ {{0.05f, 0.05f, 0.82f, 0.03f, 0.00f, 0.00f, 0.05f}, {0.90f, 0.10f}, 1.0f, 0.6f, 0.20f, 0.9f},
	/* Chaparral        */ {{0.02f, 0.02f, 0.02f, 0.55f, 0.37f, 0.00f, 0.02f}, {0.15f, 0.85f}, 0.5f, 1.0f, 1.00f, 1.4f, 0.15f},
	/* DesertScrub      */ {{0.01f, 0.01f, 0.01f, 0.05f, 0.05f, 0.85f, 0.02f}, {0.10f, 0.90f}, 0.25f, 0.8f, 1.10f, 1.2f, 0.16f},
	/* PlainsGrassland  */ {{0.28f, 0.28f, 0.05f, 0.14f, 0.10f, 0.00f, 0.15f}, {0.55f, 0.45f}, 0.15f, 1.3f, 0.10f, 0.8f},
	/* Wetland          */ {{0.50f, 0.05f, 0.38f, 0.02f, 0.00f, 0.00f, 0.05f}, {0.95f, 0.05f}, 1.0f, 1.1f, 0.05f, 0.6f},
	/* AlpineBare       */ {{0.15f, 0.20f, 0.35f, 0.10f, 0.05f, 0.00f, 0.15f}, {0.60f, 0.40f}, 0.05f, 0.1f, 2.00f, 0.3f},
	/* ArcticTundra     */ {{0.10f, 0.50f, 0.30f, 0.05f, 0.00f, 0.00f, 0.05f}, {0.30f, 0.70f}, 0.02f, 0.3f, 1.30f, 0.4f},
	/* Farmland         */ {{0.20f, 0.55f, 0.15f, 0.05f, 0.00f, 0.00f, 0.05f}, {0.80f, 0.20f}, 0.10f, 0.5f, 0.05f, 0.2f},
	/* Savanna          */ {{0.35f, 0.35f, 0.05f, 0.10f, 0.05f, 0.00f, 0.10f}, {0.60f, 0.40f}, 0.20f, 0.9f, 0.30f, 0.6f},
	/* Rainforest       */ {{0.65f, 0.05f, 0.15f, 0.05f, 0.00f, 0.00f, 0.10f}, {0.95f, 0.05f}, 1.2f, 0.9f, 0.10f, 1.3f},
	/* CoastalDune      */ {{0.05f, 0.55f, 0.05f, 0.05f, 0.15f, 0.00f, 0.15f}, {0.30f, 0.70f}, 0.05f, 0.9f, 0.20f, 0.7f},
	/* CanyonBadlands   */ {{0.02f, 0.03f, 0.03f, 0.35f, 0.05f, 0.03f, 0.49f}, {0.25f, 0.75f}, 0.10f, 0.3f, 2.20f, 0.2f, 0.20f},
	/* RockyFoothill    */ {{0.10f, 0.35f, 0.35f, 0.10f, 0.00f, 0.00f, 0.10f}, {0.70f, 0.30f}, 0.5f, 0.6f, 1.60f, 0.7f},
};
static_assert(UE_ARRAY_COUNT(BiomeRecipeTable) == (uint8)EODBiome::RockyFoothill + 1,
	"BiomeRecipeTable must have one entry per EODBiome value, in enum order");

// PickWeightedIndex (used both for the 7-way tree pick and the 2-way shrub
// pick below) now lives in ODBinSidecarUtil.h - see this file's
// ExtendedTreeMeshPaths comment above for why it moved.
}

AODForestManager::AODForestManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

const FBiomeRecipe& AODForestManager::RecipeForBiome(EODBiome Biome)
{
	// BiomeRecipeTable's static_assert already guarantees one entry per
	// EODBiome value, so this index is always in range.
	return BiomeRecipeTable[(uint8)Biome];
}

void AODForestManager::BeginPlay()
{
	Super::BeginPlay();

	// ExtendedTreeMeshPaths, not the shared TreeMeshPaths[3] - see that
	// array's own comment for why ODForestManager keeps its own superset.
	for (const TCHAR* Path : ExtendedTreeMeshPaths)
	{
		TreeMeshes.Add(LoadObject<UStaticMesh>(nullptr, Path));
	}
	for (const TCHAR* Path : ShrubMeshPaths)
	{
		ShrubMeshes.Add(LoadObject<UStaticMesh>(nullptr, Path));
	}

	// Ground cover (2026-08-26) - see the RockMeshes/ScrubMesh declarations
	// in ODForestManager.h for why these two engine basic shapes stand in
	// for a dedicated rock mesh.
	RockMeshes.Add(LoadObject<UStaticMesh>(
		nullptr, TEXT("/Engine/BasicShapes/Cube.Cube")));
	RockMeshes.Add(LoadObject<UStaticMesh>(
		nullptr, TEXT("/Engine/BasicShapes/Sphere.Sphere")));
	ScrubMesh = LoadObject<UStaticMesh>(
		nullptr, TEXT("/Game/OpenDrive/Trees/grass_tuft.grass_tuft"));

	// Base-pivot offsets for ground cover (2026-08-27) - see
	// RockMeshBaseOffsetCm/ScrubMeshBaseOffsetCm's declaration in
	// ODForestManager.h. GetBoundingBox().Min.Z is the mesh's lowest local
	// vertex relative to its own pivot (<= 0 for anything whose pivot isn't
	// already at its base); BuildTile() subtracts this (scaled) from the
	// traced ground Z so the mesh's actual geometric base lands there instead
	// of its pivot.
	for (int32 i = 0; i < RockMeshes.Num(); ++i)
	{
		if (const UStaticMesh* Mesh = RockMeshes[i].Get())
		{
			RockMeshBaseOffsetCm[i] = Mesh->GetBoundingBox().Min.Z;
		}
	}
	if (const UStaticMesh* Mesh = ScrubMesh.Get())
	{
		ScrubMeshBaseOffsetCm = Mesh->GetBoundingBox().Min.Z;
	}

	Pool.Reserve(PoolSize);
	for (int32 i = 0; i < PoolSize; ++i)
	{
		UCapsuleComponent* Cap = NewObject<UCapsuleComponent>(this);
		Cap->SetupAttachment(RootComponent);
		Cap->SetMobility(EComponentMobility::Movable);
		Cap->SetCapsuleSize(TrunkRadius, TrunkHalfHeight);
		Cap->SetCollisionProfileName(TEXT("BlockAll"));
		Cap->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		Cap->SetVisibility(false);
		Cap->RegisterComponent();
		Pool.Add(Cap);
	}

	// ground-truth elevation grid + road centerline (open-world levels,
	// 2026-07-20) - independent of the biome/mask path below, loaded
	// whenever the level builder script set a prefix. Empty prefixes (Tail
	// of the Dragon) leave both false and GroundAt() falls back to its
	// original raycast behavior unchanged.
	bUseElevationGrid = LoadElevationGrid();
	bUseCenterlineExclusion = LoadCenterline();
	bUseBuildingExclusion = LoadBuildingCentroids();
	bUseStationExclusion = LoadRoadStations();

	// point-classified biome data (open-world/streamed-terrain, no Landscape needed)
	// tried first; empty BiomeFilePrefix (Tail of the Dragon's actor
	// instance) skips straight to the Landscape-mask path unchanged.
	bUsePointBiomes = LoadBiomePoints();
	bReady = bUsePointBiomes || LoadMask();
	if (!bReady)
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODForestManager: no biome data (mask or points) available - "
			     "forest disabled"));
	}
}

bool AODForestManager::LoadMask()
{
	// mask footprint == landscape footprint (same square the material uses)
	const ALandscapeProxy* Landscape = nullptr;
	for (TActorIterator<ALandscapeProxy> It(GetWorld()); It; ++It)
	{
		Landscape = *It;
		break;
	}
	if (!Landscape)
	{
		return false;
	}
	const FVector LsLoc = Landscape->GetActorLocation();
	MaskCorner = FVector2D(LsLoc.X, LsLoc.Y);
	FVector Origin, Extent;
	Landscape->GetActorBounds(false, Origin, Extent);
	MaskSideCm = 2.0 * FMath::Max(Extent.X, Extent.Y);

	// `-odmaskbin` forces the cooked path so it's testable in editor builds
	const bool bForceBin =
		FParse::Param(FCommandLine::Get(), TEXT("odmaskbin"));

#if WITH_EDITORONLY_DATA
	UTexture2D* Tex = bForceBin ? nullptr : LoadObject<UTexture2D>(
		nullptr, TEXT("/Game/OpenDrive/Textures/T_TerrainMask.T_TerrainMask"));
	if (Tex && Tex->Source.IsValid())
	{
		TArray64<uint8> Raw;
		if (Tex->Source.GetMipData(Raw, 0))
		{
			const int32 SrcRes = Tex->Source.GetSizeX();
			const int32 Step = FMath::Max(1, SrcRes / 1024);   // ~9 m/px is plenty
			MaskRes = SrcRes / Step;
			MaskR.SetNumUninitialized(MaskRes * MaskRes);
			const int64 Bpp = Raw.Num() / ((int64)SrcRes * SrcRes);   // BGRA8 = 4
			for (int32 y = 0; y < MaskRes; ++y)
			{
				for (int32 x = 0; x < MaskRes; ++x)
				{
					const int64 Src =
						((int64)y * Step * SrcRes + (int64)x * Step) * Bpp;
					MaskR[y * MaskRes + x] = Raw[Src + 2];     // BGRA -> R
				}
			}
			UE_LOG(LogTemp, Display,
				TEXT("ODForestManager: mask %dx%d (from %d), corner (%.0f, %.0f), "
				     "side %.0f m"),
				MaskRes, MaskRes, SrcRes, MaskCorner.X, MaskCorner.Y,
				MaskSideCm / 100.0);
			return true;
		}
	}
#endif

	// cooked builds: raw sidecar written by pipeline/make_forest_mask_bin.py,
	// staged into the pak via DirectoriesToAlwaysStageAsUFS ('ODM1', uint32 LE
	// res, res*res forest-weight bytes)
	TArray<uint8> Bin;
	const FString BinPath =
		FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks/terrain_mask.bin");
	if (!FFileHelper::LoadFileToArray(Bin, *BinPath) || Bin.Num() < 8
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'M' || Bin[3] != '1')
	{
		return false;
	}
	const int32 Res = (int32)Bin[4] | (int32)Bin[5] << 8
		| (int32)Bin[6] << 16 | (int32)Bin[7] << 24;
	if (Res <= 0 || Res > 8192 || Bin.Num() < 8 + (int64)Res * Res)
	{
		return false;
	}
	MaskRes = Res;
	MaskR.SetNumUninitialized(Res * Res);
	FMemory::Memcpy(MaskR.GetData(), Bin.GetData() + 8, (int64)Res * Res);
	UE_LOG(LogTemp, Display,
		TEXT("ODForestManager: mask bin %dx%d, corner (%.0f, %.0f), side %.0f m"),
		MaskRes, MaskRes, MaskCorner.X, MaskCorner.Y, MaskSideCm / 100.0);
	return true;
}

bool AODForestManager::LoadBiomePoints()
{
	if (BiomeFilePrefix.IsEmpty())
	{
		return false;   // Tail of the Dragon's actor instance - unaffected
	}
	// 'ODB1', uint32 LE N, N * (float32 X_cm, float32 Y_cm, uint8 Biome,
	// uint8 ForestWeight255) - pipeline/classify_biomes.py + export_biome_bin.py
	TArray<uint8> Bin;
	const FString BinPath = FPaths::ProjectContentDir()
		/ TEXT("OpenDrive/Masks") / (BiomeFilePrefix + TEXT(".bin"));
	if (!FFileHelper::LoadFileToArray(Bin, *BinPath) || Bin.Num() < 8
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'B' || Bin[3] != '1')
	{
		return false;
	}
	const int32 N = (int32)Bin[4] | (int32)Bin[5] << 8
		| (int32)Bin[6] << 16 | (int32)Bin[7] << 24;
	constexpr int32 RecordBytes = 4 + 4 + 1 + 1;   // X_cm, Y_cm, Biome, ForestWeight255
	if (N <= 0 || Bin.Num() < 8 + (int64)N * RecordBytes)
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODForestManager: %s truncated/corrupt (N=%d, %lld bytes)"),
			*BinPath, N, Bin.Num());
		return false;
	}
	BiomePoints.SetNumUninitialized(N);
	const uint8* P = Bin.GetData() + 8;
	for (int32 i = 0; i < N; ++i, P += RecordBytes)
	{
		float X, Y;
		FMemory::Memcpy(&X, P, 4);
		FMemory::Memcpy(&Y, P + 4, 4);
		BiomePoints[i].WorldXY = FVector2D(X, Y);
		BiomePoints[i].Biome = (EODBiome)P[8];
		BiomePoints[i].ForestWeight = P[9] / 255.0f;
	}
	UE_LOG(LogTemp, Display,
		TEXT("ODForestManager: loaded %d biome point(s) from %s, blend radius %.0f m"),
		N, *BinPath, BiomeBlendRadiusCm / 100.0);
	return true;
}

bool AODForestManager::LoadElevationGrid()
{
	if (ElevationFilePrefix.IsEmpty())
	{
		return false;   // Tail of the Dragon's actor instance - unaffected
	}
	// 'ODE1', uint32 LE N, N * (float32 X_cm, float32 Y_cm, float32 Z_cm) -
	// pipeline/build_forest_elevation.py
	TArray<uint8> Bin;
	const FString BinPath = FPaths::ProjectContentDir()
		/ TEXT("OpenDrive/Masks") / (ElevationFilePrefix + TEXT(".bin"));
	if (!FFileHelper::LoadFileToArray(Bin, *BinPath) || Bin.Num() < 8
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'E' || Bin[3] != '1')
	{
		return false;
	}
	const int32 N = (int32)Bin[4] | (int32)Bin[5] << 8
		| (int32)Bin[6] << 16 | (int32)Bin[7] << 24;
	constexpr int32 RecordBytes = 4 + 4 + 4;   // X_cm, Y_cm, Z_cm
	if (N <= 0 || Bin.Num() < 8 + (int64)N * RecordBytes)
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODForestManager: %s truncated/corrupt (N=%d, %lld bytes)"),
			*BinPath, N, Bin.Num());
		return false;
	}
	ElevationGrid.SetNumUninitialized(N);
	const uint8* P = Bin.GetData() + 8;
	for (int32 i = 0; i < N; ++i, P += RecordBytes)
	{
		float X, Y, Z;
		FMemory::Memcpy(&X, P, 4);
		FMemory::Memcpy(&Y, P + 4, 4);
		FMemory::Memcpy(&Z, P + 8, 4);
		ElevationGrid[i].WorldXY = FVector2D(X, Y);
		ElevationGrid[i].Z = Z;
		const int32 CellX = FMath::FloorToInt(X / ElevationCellCm);
		const int32 CellY = FMath::FloorToInt(Y / ElevationCellCm);
		ElevationBuckets.FindOrAdd(FIntPoint(CellX, CellY)).Add(i);
	}
	UE_LOG(LogTemp, Display,
		TEXT("ODForestManager: loaded %d elevation grid point(s) from %s"),
		N, *BinPath);
	return true;
}

bool AODForestManager::LoadCenterline()
{
	if (CenterlineFilePrefix.IsEmpty())
	{
		return false;   // Tail of the Dragon's actor instance - unaffected
	}
	// 'ODC1', uint32 LE N, N * (float32 X_cm, float32 Y_cm) -
	// pipeline/export_road_centerline_bin.py
	TArray<uint8> Bin;
	const FString BinPath = FPaths::ProjectContentDir()
		/ TEXT("OpenDrive/Masks") / (CenterlineFilePrefix + TEXT(".bin"));
	if (!FFileHelper::LoadFileToArray(Bin, *BinPath) || Bin.Num() < 8
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'C' || Bin[3] != '1')
	{
		return false;
	}
	const int32 N = (int32)Bin[4] | (int32)Bin[5] << 8
		| (int32)Bin[6] << 16 | (int32)Bin[7] << 24;
	constexpr int32 RecordBytes = 4 + 4;   // X_cm, Y_cm
	if (N <= 0 || Bin.Num() < 8 + (int64)N * RecordBytes)
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODForestManager: %s truncated/corrupt (N=%d, %lld bytes)"),
			*BinPath, N, Bin.Num());
		return false;
	}
	RoadCenterline.SetNumUninitialized(N);
	const uint8* P = Bin.GetData() + 8;
	for (int32 i = 0; i < N; ++i, P += RecordBytes)
	{
		float X, Y;
		FMemory::Memcpy(&X, P, 4);
		FMemory::Memcpy(&Y, P + 4, 4);
		RoadCenterline[i] = FVector2D(X, Y);
		const int32 CellX = FMath::FloorToInt(X / CenterlineCellCm);
		const int32 CellY = FMath::FloorToInt(Y / CenterlineCellCm);
		CenterlineBuckets.FindOrAdd(FIntPoint(CellX, CellY)).Add(i);
	}
	UE_LOG(LogTemp, Display,
		TEXT("ODForestManager: loaded %d centerline point(s) from %s, exclusion %.1f m"),
		N, *BinPath, PavementExclusionCm / 100.0);
	return true;
}

bool AODForestManager::LoadBuildingCentroids()
{
	if (BuildingCentroidFilePrefix.IsEmpty())
	{
		return false;   // no buildings on this level (or step 3 pipeline
		                // hasn't run for this area) - unaffected
	}
	// 'ODC1', uint32 LE N, N * (float32 X_cm, float32 Y_cm) - same point-list
	// format as LoadCenterline(), just a different sidecar
	// (pipeline/export_building_centroids_bin.py)
	TArray<uint8> Bin;
	const FString BinPath = FPaths::ProjectContentDir()
		/ TEXT("OpenDrive/Masks") / (BuildingCentroidFilePrefix + TEXT(".bin"));
	if (!FFileHelper::LoadFileToArray(Bin, *BinPath) || Bin.Num() < 8
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'C' || Bin[3] != '1')
	{
		return false;
	}
	const int32 N = (int32)Bin[4] | (int32)Bin[5] << 8
		| (int32)Bin[6] << 16 | (int32)Bin[7] << 24;
	constexpr int32 RecordBytes = 4 + 4;
	if (N <= 0 || Bin.Num() < 8 + (int64)N * RecordBytes)
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODForestManager: %s truncated/corrupt (N=%d, %lld bytes)"),
			*BinPath, N, Bin.Num());
		return false;
	}
	BuildingCentroids.SetNumUninitialized(N);
	const uint8* P = Bin.GetData() + 8;
	for (int32 i = 0; i < N; ++i, P += RecordBytes)
	{
		float X, Y;
		FMemory::Memcpy(&X, P, 4);
		FMemory::Memcpy(&Y, P + 4, 4);
		BuildingCentroids[i] = FVector2D(X, Y);
		const int32 CellX = FMath::FloorToInt(X / BuildingCellCm);
		const int32 CellY = FMath::FloorToInt(Y / BuildingCellCm);
		BuildingBuckets.FindOrAdd(FIntPoint(CellX, CellY)).Add(i);
	}
	UE_LOG(LogTemp, Display,
		TEXT("ODForestManager: loaded %d building centroid(s) from %s, exclusion %.1f m"),
		N, *BinPath, BuildingExclusionCm / 100.0);
	return true;
}

bool AODForestManager::LoadRoadStations()
{
	if (RoadStationFilePrefix.IsEmpty())
	{
		return false;   // no station data configured for this level - unaffected
	}
	// Same ODS1 sidecar/parser AODTerrainManager::LoadRoadStationSidecar
	// uses (see RoadStationFilePrefix's comment) - lifted to ODBinSidecarUtil.h
	// so both managers call the exact same code rather than keeping two
	// copies in sync.
	const FString BinPath = FPaths::ProjectContentDir()
		/ TEXT("OpenDrive/Masks") / (RoadStationFilePrefix + TEXT(".bin"));
	TArray<FRoadStation> Loaded;
	FString GenId;
	if (!ParseRoadStationSidecarFile(BinPath, Loaded, GenId))
	{
		// Missing/corrupt sidecar is not fatal - same "optional feature"
		// convention every other Load*() here follows. GroundAt()'s
		// PM_Asphalt physmat check remains as the belt-and-braces fallback.
		UE_LOG(LogTemp, Warning,
			TEXT("ODForestManager: road-station sidecar %s missing or corrupt - "
			     "road-exclusion check disabled (PM_Asphalt trace check only)"),
			*BinPath);
		return false;
	}
	const int32 NumStations = Loaded.Num();
	RoadStationHash.Build(MoveTemp(Loaded));
	UE_LOG(LogTemp, Display,
		TEXT("ODForestManager: loaded %d road station(s) from %s (gen %s), "
		     "road-exclusion %.1f m"),
		NumStations, *BinPath, *GenId, RoadStationExclusionCm / 100.0);
	return true;
}

bool AODForestManager::ElevationGridHeightAt(double WorldX, double WorldY, float& OutZ) const
{
	const FVector2D Here(WorldX, WorldY);
	const int32 CellX = FMath::FloorToInt(WorldX / ElevationCellCm);
	const int32 CellY = FMath::FloorToInt(WorldY / ElevationCellCm);
	float WeightSum = 0.0f, ZAcc = 0.0f;
	float BestD2 = TNumericLimits<float>::Max();
	float NearestZ = 0.0f;
	bool bFoundAny = false;
	for (int32 dy = -1; dy <= 1; ++dy)
	{
		for (int32 dx = -1; dx <= 1; ++dx)
		{
			const TArray<int32>* Bucket = ElevationBuckets.Find(FIntPoint(CellX + dx, CellY + dy));
			if (!Bucket)
			{
				continue;
			}
			for (int32 Idx : *Bucket)
			{
				const FGroundPoint& Pt = ElevationGrid[Idx];
				const float D2 = FVector2D::DistSquared(Pt.WorldXY, Here);
				bFoundAny = true;
				if (D2 < BestD2)
				{
					BestD2 = D2;
					NearestZ = Pt.Z;
				}
				const float W = 1.0f / FMath::Max(1.0f, D2);
				ZAcc += Pt.Z * W;
				WeightSum += W;
			}
		}
	}
	if (!bFoundAny)
	{
		// 3x3 neighborhood came up empty - shouldn't happen within
		// SpawnRadius given build_forest_elevation.py's 550 m coverage
		// margin, but a candidate right at the very edge of the network
		// could miss. Caller treats this as "can't place here".
		return false;
	}
	OutZ = WeightSum > 0.0f ? ZAcc / WeightSum : NearestZ;
	return true;
}

bool AODForestManager::IsNearCenterline(double WorldX, double WorldY) const
{
	const FVector2D Here(WorldX, WorldY);
	const int32 CellX = FMath::FloorToInt(WorldX / CenterlineCellCm);
	const int32 CellY = FMath::FloorToInt(WorldY / CenterlineCellCm);
	const float ExclusionSq = PavementExclusionCm * PavementExclusionCm;
	for (int32 dy = -1; dy <= 1; ++dy)
	{
		for (int32 dx = -1; dx <= 1; ++dx)
		{
			const TArray<int32>* Bucket = CenterlineBuckets.Find(FIntPoint(CellX + dx, CellY + dy));
			if (!Bucket)
			{
				continue;
			}
			for (int32 Idx : *Bucket)
			{
				if (FVector2D::DistSquared(RoadCenterline[Idx], Here) < ExclusionSq)
				{
					return true;
				}
			}
		}
	}
	return false;
}

bool AODForestManager::WithinCenterlineBand(double WorldX, double WorldY) const
{
	// True if the nearest road-centerline point is within ForestBandCm - the
	// upper bound complementing IsNearCenterline()'s lower bound (pavement
	// exclusion). ForestBandCm (120 m) is far larger than CenterlineCellCm
	// (30 m), so unlike IsNearCenterline's fixed 3x3 this scales the bucket
	// reach with the band, or a point one cell out of range would read as
	// "beyond band" and wrongly cull roadside forest.
	const FVector2D Here(WorldX, WorldY);
	const int32 CellX = FMath::FloorToInt(WorldX / CenterlineCellCm);
	const int32 CellY = FMath::FloorToInt(WorldY / CenterlineCellCm);
	const int32 Reach = FMath::CeilToInt(ForestBandCm / CenterlineCellCm) + 1;
	const float BandSq = ForestBandCm * ForestBandCm;
	for (int32 dy = -Reach; dy <= Reach; ++dy)
	{
		for (int32 dx = -Reach; dx <= Reach; ++dx)
		{
			const TArray<int32>* Bucket = CenterlineBuckets.Find(FIntPoint(CellX + dx, CellY + dy));
			if (!Bucket)
			{
				continue;
			}
			for (int32 Idx : *Bucket)
			{
				if (FVector2D::DistSquared(RoadCenterline[Idx], Here) <= BandSq)
				{
					return true;
				}
			}
		}
	}
	return false;
}

bool AODForestManager::IsNearBuilding(double WorldX, double WorldY) const
{
	const FVector2D Here(WorldX, WorldY);
	const int32 CellX = FMath::FloorToInt(WorldX / BuildingCellCm);
	const int32 CellY = FMath::FloorToInt(WorldY / BuildingCellCm);
	const float ExclusionSq = BuildingExclusionCm * BuildingExclusionCm;
	for (int32 dy = -1; dy <= 1; ++dy)
	{
		for (int32 dx = -1; dx <= 1; ++dx)
		{
			const TArray<int32>* Bucket = BuildingBuckets.Find(FIntPoint(CellX + dx, CellY + dy));
			if (!Bucket)
			{
				continue;
			}
			for (int32 Idx : *Bucket)
			{
				if (FVector2D::DistSquared(BuildingCentroids[Idx], Here) < ExclusionSq)
				{
					return true;
				}
			}
		}
	}
	return false;
}

bool AODForestManager::IsNearRoadStation(double WorldX, double WorldY) const
{
	// Junction-cluster widening (2026-08-27, see JunctionClusterExclusionCm's
	// header comment): a candidate whose 100 m station-hash cell holds an
	// unusually high station count - multiple road centerlines converging,
	// i.e. a junction - gets the wider JunctionClusterExclusionCm instead of
	// the ordinary RoadStationExclusionCm. Junction pads extend well past any
	// individual station's own footprint, and their collision is the known
	// flaky Chaos same-frame-registration bug (see BuildOneChunk()'s comment
	// in ODRoadManager.cpp), which can silently defeat GroundAt()'s
	// PM_Asphalt raycast check - this purely data-driven distance check is
	// the fallback that still works even when that physics bug bites.
	// FStationSpatialHash's own Cells map (not QueryBox) is used here since
	// we want the RAW single-cell count as the density signal, not a
	// radius-padded multi-cell candidate list.
	float EffectiveExclusionCm = RoadStationExclusionCm;
	bool bJunctionCluster = false;
	{
		const int32 CellX = FMath::FloorToInt((float)WorldX / FStationSpatialHash::CellSizeCm);
		const int32 CellY = FMath::FloorToInt((float)WorldY / FStationSpatialHash::CellSizeCm);
		if (const TArray<int32>* Cell = RoadStationHash.Cells.Find(FIntPoint(CellX, CellY));
			Cell && Cell->Num() >= JunctionClusterStationCountThreshold)
		{
			EffectiveExclusionCm = FMath::Max(EffectiveExclusionCm, JunctionClusterExclusionCm);
			bJunctionCluster = true;
		}
	}

	// FStationSpatialHash::QueryBox is a coarse cell-overlap test (see its
	// own comment) - pad the box by EffectiveExclusionCm so a station
	// sitting just across a cell boundary from (WorldX, WorldY) isn't missed,
	// same convention ApplyRoadCarve's own StationQueryMarginCm follows.
	const float MinX = (float)WorldX - EffectiveExclusionCm;
	const float MaxX = (float)WorldX + EffectiveExclusionCm;
	const float MinY = (float)WorldY - EffectiveExclusionCm;
	const float MaxY = (float)WorldY + EffectiveExclusionCm;
	TArray<int32> Candidates;
	RoadStationHash.QueryBox(MinX, MinY, MaxX, MaxY, Candidates);
	if (Candidates.Num() == 0)
	{
		return false;
	}
	const float ExclusionSq = EffectiveExclusionCm * EffectiveExclusionCm;
	const float BaseExclusionSq = RoadStationExclusionCm * RoadStationExclusionCm;
	const FVector2D Here((float)WorldX, (float)WorldY);
	for (int32 Idx : Candidates)
	{
		const FRoadStation& St = RoadStationHash.Stations[Idx];
		const float D2 = FVector2D::DistSquared(FVector2D(St.X, St.Y), Here);
		if (D2 < ExclusionSq)
		{
			if (bJunctionCluster && D2 >= BaseExclusionSq)
			{
				// would have PASSED the ordinary 10 m radius - counted
				// separately so the widened-radius path's own effect is
				// visible in the log (see JunctionClusterRejectCount).
				++JunctionClusterRejectCount;
			}
			return true;
		}
	}
	return false;
}

float AODForestManager::TownForestDampen(double WorldX, double WorldY) const
{
	if (!bUseBuildingExclusion)
	{
		return 1.0f;
	}
	const FVector2D Here(WorldX, WorldY);
	const int32 CellX = FMath::FloorToInt(WorldX / BuildingCellCm);
	const int32 CellY = FMath::FloorToInt(WorldY / BuildingCellCm);
	const int32 Reach = FMath::CeilToInt(TownDampenRadiusCm / BuildingCellCm);
	float BestD2 = TNumericLimits<float>::Max();
	for (int32 dy = -Reach; dy <= Reach; ++dy)
	{
		for (int32 dx = -Reach; dx <= Reach; ++dx)
		{
			const TArray<int32>* Bucket = BuildingBuckets.Find(FIntPoint(CellX + dx, CellY + dy));
			if (!Bucket)
			{
				continue;
			}
			for (int32 Idx : *Bucket)
			{
				BestD2 = FMath::Min(BestD2, (float)FVector2D::DistSquared(BuildingCentroids[Idx], Here));
			}
		}
	}
	if (BestD2 >= TownDampenRadiusCm * TownDampenRadiusCm)
	{
		return 1.0f;   // nothing built-up within range - full forest density
	}
	const float Dist = FMath::Sqrt(BestD2);
	const float T = FMath::Clamp(
		(Dist - BuildingExclusionCm) / FMath::Max(1.0f, TownDampenRadiusCm - BuildingExclusionCm),
		0.0f, 1.0f);
	const float Smooth = T * T * (3.0f - 2.0f * T);
	return FMath::Lerp(TownDampenMinFactor, 1.0f, Smooth);
}

float AODForestManager::ForestWeightAt(double WorldX, double WorldY) const
{
	const double U = (WorldX - MaskCorner.X) / MaskSideCm;
	const double V = (WorldY - MaskCorner.Y) / MaskSideCm;
	if (U < 0.0 || U >= 1.0 || V < 0.0 || V >= 1.0)
	{
		return 0.0f;
	}
	const int32 X = FMath::Clamp((int32)(U * MaskRes), 0, MaskRes - 1);
	const int32 Y = FMath::Clamp((int32)(V * MaskRes), 0, MaskRes - 1);
	return MaskR[Y * MaskRes + X] / 255.0f;
}

void AODForestManager::BlendAt(double WorldX, double WorldY,
                                FBiomeRecipe& OutRecipe, float& OutForestWeight,
                                EODBiome& OutNearestBiome) const
{
	if (!bUsePointBiomes || BiomePoints.Num() == 0)
	{
		// Landscape-mask path (Tail of the Dragon), or no biome data loaded
		// at all: single fixed recipe, raster forest weight handled
		// separately by ForestWeightAt() per scatter candidate as before.
		OutRecipe = BiomeRecipeTable[(uint8)DefaultBiome];
		OutForestWeight = -1.0f;   // sentinel: BuildTile() falls back to ForestWeightAt()
		OutNearestBiome = DefaultBiome;
		return;
	}

	// Inverse-square-distance-weighted blend over every classified point
	// within BiomeBlendRadiusCm - this is what makes driving between
	// biomes read as a gradual transition instead of a hard cut at some
	// tile boundary (Taylor, 2026-07-20: "auto switch/blend"). Called once
	// per BuildTile(), not per scatter candidate - biome data is far
	// coarser (road-station spacing) than the 96 m tile grid, so a
	// tile-level sample doesn't lose meaningful variation, and this keeps
	// a full point-array scan out of BuildTile()'s hot per-candidate loop.
	const FVector2D Here(WorldX, WorldY);
	// SpeciesAcc/ShrubAcc widened 2026-08-27 (7-species/2-shrub mix, see
	// FBiomeRecipe's comment) from the original {3} - this used to only
	// accumulate the classic blob/olive/conifer weights and never touched
	// ShrubWeight at all, so every blended tile silently fell back to
	// OutRecipe's default-constructed SpeciesWeight[3..6]=0 (no scrub
	// oak/manzanita/yucca/canyon oak ever got picked) and default
	// ShrubWeight={bush=1,sagebrush=0} (sagebrush never got picked either) -
	// found via BuildTile()'s species-mix verification log showing
	// scrubOak/manzanita/yucca/sagebrush stuck at 0 in every Chaparral/
	// DesertScrub tile despite BiomeRecipeTable specifying otherwise.
	float WeightSum = 0.0f, SpeciesAcc[7] = {};
	float ShrubAcc[2] = {};
	float TreeScaleAcc = 0.0f, BushScaleAcc = 0.0f, ForestWeightAcc = 0.0f;
	// ground cover (2026-08-26): accumulated the same inverse-square-distance
	// way as tree/bush density above, so rocks/scrub blend smoothly across a
	// biome boundary too instead of popping at the tile that crosses it.
	float RockScaleAcc = 0.0f, ScrubScaleAcc = 0.0f;
	float BestD2 = TNumericLimits<float>::Max();
	EODBiome NearestBiome = DefaultBiome;
	float NearestForestWeight = 0.0f;
	const float RadiusSq = BiomeBlendRadiusCm * BiomeBlendRadiusCm;

	for (const FBiomePoint& Pt : BiomePoints)
	{
		const float D2 = FVector2D::DistSquared(Pt.WorldXY, Here);
		if (D2 < BestD2)
		{
			BestD2 = D2;
			NearestBiome = Pt.Biome;
			NearestForestWeight = Pt.ForestWeight;
		}
		if (D2 > RadiusSq)
		{
			continue;
		}
		const float W = 1.0f / FMath::Max(1.0f, D2);
		const FBiomeRecipe& R = BiomeRecipeTable[(uint8)Pt.Biome];
		for (int32 s = 0; s < UE_ARRAY_COUNT(SpeciesAcc); ++s)
		{
			SpeciesAcc[s] += R.SpeciesWeight[s] * W;
		}
		for (int32 s = 0; s < UE_ARRAY_COUNT(ShrubAcc); ++s)
		{
			ShrubAcc[s] += R.ShrubWeight[s] * W;
		}
		TreeScaleAcc += R.TreeDensityScale * W;
		BushScaleAcc += R.BushDensityScale * W;
		RockScaleAcc += R.RockDensityScale * W;
		ScrubScaleAcc += R.ScrubDensityScale * W;
		ForestWeightAcc += Pt.ForestWeight * W;
		WeightSum += W;
	}

	OutNearestBiome = NearestBiome;
	if (WeightSum <= 0.0f)
	{
		// nothing within range - too far from any classified data to
		// blend, so just use the nearest point outright rather than
		// silently falling back to DefaultBiome (which could be a totally
		// different region's default).
		OutRecipe = BiomeRecipeTable[(uint8)NearestBiome];
		OutForestWeight = NearestForestWeight * TownForestDampen(WorldX, WorldY);
		return;
	}
	for (int32 s = 0; s < UE_ARRAY_COUNT(SpeciesAcc); ++s)
	{
		OutRecipe.SpeciesWeight[s] = SpeciesAcc[s] / WeightSum;
	}
	for (int32 s = 0; s < UE_ARRAY_COUNT(ShrubAcc); ++s)
	{
		OutRecipe.ShrubWeight[s] = ShrubAcc[s] / WeightSum;
	}
	OutRecipe.TreeDensityScale = TreeScaleAcc / WeightSum;
	OutRecipe.BushDensityScale = BushScaleAcc / WeightSum;
	OutRecipe.RockDensityScale = RockScaleAcc / WeightSum;
	OutRecipe.ScrubDensityScale = ScrubScaleAcc / WeightSum;
	// town-proximity dampening (2026-07-24) - see TownForestDampen()'s
	// comment: the biome classifier alone has no idea a populated area is
	// nearby, so this softly thins the approach into town instead of
	// leaving a solid tree wall right at BuildingExclusionCm's hard cutoff.
	OutForestWeight = (ForestWeightAcc / WeightSum) * TownForestDampen(WorldX, WorldY);
}

bool AODForestManager::GroundAt(double WorldX, double WorldY,
                                const AActor* Ignore, FVector& OutHit,
                                bool* bOutDeferred) const
{
	if (bOutDeferred)
	{
		*bOutDeferred = false;
	}
	// Open-world/streamed-terrain path (2026-07-20): fully data-driven, no physics
	// query at all. GroundAt() used to trust a raycast against whatever
	// terrain LOD happened to be resident - fine on a real Landscape, but
	// on live-streamed terrain a raycast can hit a stale/coarse LOD (or genuinely wrong
	// low-zoom source data far from the road) and the tree's transform,
	// once baked, never updates even after the terrain visually catches
	// up. Both the elevation grid and centerline are pre-verified offline
	// against the same DEM data the road/terrain pipeline itself uses.
	// Road-station exclusion (2026-08-26) - see RoadStationFilePrefix's
	// comment. Checked FIRST and unconditionally (both branches below), unlike
	// the centerline exclusion just under it which only runs on the
	// elevation-grid path - this is the one road-exclusion check that isn't
	// gated behind bUseElevationGrid, precisely so an area with no elevation
	// grid baked yet (Green Valley, as of this writing) still gets a real
	// data-driven pavement guarantee instead of relying solely on whatever
	// physmat the raycast happens to hit.
	if (bUseStationExclusion && IsNearRoadStation(WorldX, WorldY))
	{
		++RoadExclusionRejectCount;
		MaybeLogGroundSummary();
		return false;
	}

	if (bUseElevationGrid)
	{
		if (bUseCenterlineExclusion && IsNearCenterline(WorldX, WorldY))
		{
			return false;   // too close - on the pavement/shoulder
		}
		if (bUseCenterlineExclusion && !WithinCenterlineBand(WorldX, WorldY))
		{
			return false;   // too far - beyond the accurate road corridor
		}
		if (bUseBuildingExclusion && IsNearBuilding(WorldX, WorldY))
		{
			return false;
		}
		float GridZ;
		if (!ElevationGridHeightAt(WorldX, WorldY, GridZ))
		{
			return false;
		}
		// Runtime-carve correction (2026-08-27, live-drive bug: "vegetation
		// aligned not with the ground but with the road" - most trees found
		// levitating). build_forest_elevation.py's grid is baked offline
		// straight from raw DEM data - it never runs carve_grid_m() (that
		// pipeline step only touches build_terrain_mesh_tiles.py's OWN grid,
		// and even that offline carve is now superseded at runtime by
		// AODTerrainManager::ApplyRoadCarve, made grade-aware 2026-08-27).
		// So GridZ is the UNCARVED height, while the actual streamed terrain
		// under it has been carved down (or, on a hillside cut, blended
		// toward the cut face) by ApplyRoadCarve - anywhere within its ~150 m
		// taper radius, which covers nearly all of ForestBandCm (120 m), the
		// two heights disagree, and every tree/bush/rock/scrub placed
		// straight from GridZ visibly floats above (or sinks into) the real
		// ground. Correct GridZ against a real raycast into whatever terrain
		// collision is actually resident, same phantom-hit-rejection
		// RaycastRuntimeGround already applies for the non-grid path below -
		// if that raycast can't resolve real ground yet (collision not
		// streamed in under this exact candidate), skip this candidate
		// rather than plant it at the stale offline height; the tile isn't
		// revisited, same "just don't plant" convention every other
		// exclusion check in this function already follows.
		FVector Corrected;
		if (!RaycastRuntimeGround(WorldX, WorldY, Ignore, Corrected))
		{
			if (bOutDeferred)
			{
				*bOutDeferred = true;
			}
			++ElevationDeferredCount;
			if (const double Now = GetWorld()->GetTimeSeconds();
				Now - LastElevationCorrectionLogTime > 5.0)
			{
				LastElevationCorrectionLogTime = Now;
				UE_LOG(LogTemp, Display,
					TEXT("ODForestManager: elevation-grid correction (last 5s) - "
					     "corrected=%d deferred=%d maxDelta=%.1f cm"),
					ElevationCorrectedCount, ElevationDeferredCount, ElevationMaxCorrectionCm);
				ElevationCorrectedCount = 0;
				ElevationDeferredCount = 0;
				ElevationMaxCorrectionCm = 0.0f;
			}
			return false;
		}
		++ElevationCorrectedCount;
		ElevationMaxCorrectionCm = FMath::Max(ElevationMaxCorrectionCm,
			FMath::Abs(Corrected.Z - GridZ));
		if (const double Now = GetWorld()->GetTimeSeconds();
			Now - LastElevationCorrectionLogTime > 5.0)
		{
			LastElevationCorrectionLogTime = Now;
			UE_LOG(LogTemp, Display,
				TEXT("ODForestManager: elevation-grid correction (last 5s) - "
				     "corrected=%d deferred=%d maxDelta=%.1f cm"),
				ElevationCorrectedCount, ElevationDeferredCount, ElevationMaxCorrectionCm);
			ElevationCorrectedCount = 0;
			ElevationDeferredCount = 0;
			ElevationMaxCorrectionCm = 0.0f;
		}
		OutHit = Corrected;
		++GroundAcceptCount;
		MaybeLogGroundSummary();
		return true;
	}

	// Raycast path (Tail of the Dragon's Landscape, or open-world levels with
	// no elevation grid baked yet)
	FVector Hit;
	if (!RaycastRuntimeGround(WorldX, WorldY, Ignore, Hit))
	{
		return false;
	}
	OutHit = Hit;
	++GroundAcceptCount;
	MaybeLogGroundSummary();
	return true;
}

bool AODForestManager::RaycastRuntimeGround(double WorldX, double WorldY,
                                             const AActor* Ignore, FVector& OutHit) const
{
	// Traced relative to PlayerZHint, not a hardcoded absolute Z (2026-08-26
	// fix: see PlayerZHint's declaration comment for the live-drive bug this
	// replaced - a fixed +-1000 m band put the trace's own start point
	// underground on Green Valley's higher terrain and could register a hit
	// against unrelated lower geometry).
	FHitResult Hit;
	const FVector Start(WorldX, WorldY, PlayerZHint + GroundTraceMarginCm);
	const FVector End(WorldX, WorldY, PlayerZHint - GroundTraceMarginCm);
	FCollisionQueryParams Params;
	Params.bReturnPhysicalMaterial = true;
	Params.AddIgnoredActor(this);          // our own trunk capsules
	if (Ignore)
	{
		Params.AddIgnoredActor(Ignore);    // the player's car
	}
	if (!GetWorld()->LineTraceSingleByChannel(Hit, Start, End,
			ECC_WorldStatic, Params))
	{
		return false;
	}
	// never plant a tree on pavement (road meshes, including fused junction
	// pads - see ODRoadManager.cpp's bridge_pad_to_host comments - carry
	// PM_Asphalt through the shared RoadMat material)
	if (const UPhysicalMaterial* Pm = Hit.PhysMaterial.Get();
		Pm && Pm->GetName().Contains(TEXT("Asphalt")))
	{
		++PhysMatRejectCount;
		MaybeLogGroundSummary();
		return false;
	}
	// Sanity check (temporary QA instrumentation, 2026-08-26): local relief
	// within one SpawnRadius shouldn't plausibly exceed GroundSanityMaxDeltaCm
	// from the player's own height - a hit further off than that is far more
	// likely stale/unrelated geometry the wide trace band above crossed than
	// real ground (this is also what rejects the known phantom surface at
	// world Z ~= -829.9 m - anywhere in Green Valley's real 0-2200 m
	// elevation range that's already >500 m from PlayerZHint), so reject it
	// instead of planting a tree at a wildly wrong height (the exact "trees
	// rendered far below the terrain surface" bug this whole fix targets -
	// belt-and-suspenders alongside the trace-range fix itself, in case some
	// other bad hit slips through).
	if (FMath::Abs(Hit.ImpactPoint.Z - PlayerZHint) > GroundSanityMaxDeltaCm)
	{
		++GroundRejectCount;
		if (const double Now = GetWorld()->GetTimeSeconds();
			Now - LastGroundRejectLogTime > 5.0)
		{
			LastGroundRejectLogTime = Now;
			UE_LOG(LogTemp, Warning,
				TEXT("ODForestManager: rejected %d ground hit(s) >%.0f m from "
				     "player height in the last 5s (last: hit Z=%.0f cm vs "
				     "player Z=%.0f cm at world (%.0f, %.0f)) - suspected bad "
				     "raycast, not planting"),
				GroundRejectCount, GroundSanityMaxDeltaCm / 100.0,
				Hit.ImpactPoint.Z, PlayerZHint, WorldX, WorldY);
			GroundRejectCount = 0;
		}
		return false;
	}
	OutHit = Hit.ImpactPoint;
	return true;
}

void AODForestManager::MaybeLogGroundSummary() const
{
	// Unified scatter-candidate summary (2026-08-27, junction-pad tree-
	// exclusion task) - one throttled line covering all three outcomes so a
	// live/headless verification run can grep a single log line instead of
	// piecing together the road-station and physmat checks' separate
	// (formerly separate) counters. JunctionClusterRejectCount is the subset
	// of the station-exclusion count that ONLY rejected because of the
	// widened junction-cluster radius (see IsNearRoadStation()) - visibility
	// into whether that specific fix is actually firing.
	if (const double Now = GetWorld()->GetTimeSeconds(); Now - LastGroundSummaryLogTime > 5.0)
	{
		LastGroundSummaryLogTime = Now;
		UE_LOG(LogTemp, Display,
			TEXT("ODForestManager: scatter candidate summary (last 5s) - accepted=%d, "
			     "rejected by physmat=%d, rejected by station exclusion=%d "
			     "(of which junction-cluster widened-radius=%d)"),
			GroundAcceptCount, PhysMatRejectCount, RoadExclusionRejectCount,
			JunctionClusterRejectCount);
		GroundAcceptCount = 0;
		PhysMatRejectCount = 0;
		RoadExclusionRejectCount = 0;
		JunctionClusterRejectCount = 0;
	}
}

bool AODForestManager::HeightOnlyAt(double WorldX, double WorldY, float& OutZ) const
{
	// Ground-height-only variant of GroundAt() for slope sampling (2026-08-26)
	// - reuses the same two data sources (dense elevation grid on open-world
	// levels, Landscape raycast on Tail of the Dragon) but skips GroundAt()'s
	// road/building exclusion checks, since those only matter for the final
	// placement point (already checked separately) and would otherwise make
	// slope sampling near the road silently fail.
	if (bUseElevationGrid)
	{
		return ElevationGridHeightAt(WorldX, WorldY, OutZ);
	}
	// Same PlayerZHint-relative trace range as GroundAt()'s raycast branch -
	// see PlayerZHint's declaration comment (ODForestManager.h) for why a
	// fixed absolute +-1000 m band was wrong on Green Valley's terrain.
	FHitResult Hit;
	const FVector Start(WorldX, WorldY, PlayerZHint + GroundTraceMarginCm);
	const FVector End(WorldX, WorldY, PlayerZHint - GroundTraceMarginCm);
	FCollisionQueryParams Params;
	Params.AddIgnoredActor(this);
	if (!GetWorld()->LineTraceSingleByChannel(Hit, Start, End, ECC_WorldStatic, Params))
	{
		return false;
	}
	if (FMath::Abs(Hit.ImpactPoint.Z - PlayerZHint) > GroundSanityMaxDeltaCm)
	{
		return false;   // same suspected-bad-hit guard as GroundAt() - see there for the log
	}
	OutZ = Hit.ImpactPoint.Z;
	return true;
}

bool AODForestManager::SlopeAt(double WorldX, double WorldY, float& OutSlopeRatio) const
{
	// Central-difference slope estimate (rise/run) over a small 2 m offset -
	// cheap even on the elevation-grid path (each HeightOnlyAt() call is a
	// bucketed 3x3-neighborhood lookup, not a full scan). Used to gate rock
	// placement toward steeper, tree-thinned ground (BuildTile()).
	constexpr float D = 200.0f;   // 2 m
	float Zx0, Zx1, Zy0, Zy1;
	if (!HeightOnlyAt(WorldX - D, WorldY, Zx0) || !HeightOnlyAt(WorldX + D, WorldY, Zx1)
		|| !HeightOnlyAt(WorldX, WorldY - D, Zy0) || !HeightOnlyAt(WorldX, WorldY + D, Zy1))
	{
		return false;
	}
	const float DzDx = (Zx1 - Zx0) / (2.0f * D);
	const float DzDy = (Zy1 - Zy0) / (2.0f * D);
	OutSlopeRatio = FMath::Sqrt(DzDx * DzDx + DzDy * DzDy);
	return true;
}

bool AODForestManager::BuildTile(const FIntPoint& Key, const AActor* Player)
{
	// Local until we decide to commit - see this function's header comment:
	// a tile whose scatter mostly deferred (real terrain not resolvable yet)
	// is discarded rather than added to Tiles, so it isn't permanently
	// stuck sparse.
	FTile Tile;
	FRandomStream Rng(HashCombine(GetTypeHash(Key), (uint32)RandomSeed));
	int32 GroundAttempts = 0, GroundDeferred = 0;

	const double X0 = Key.X * (double)TileSizeCm;
	const double Y0 = Key.Y * (double)TileSizeCm;
	FBiomeRecipe Recipe;
	float TileForestWeight;   // -1 = no point data for this tile; fall back to raster ForestWeightAt() per candidate
	EODBiome DominantBiome;   // nearest classified point (or DefaultBiome) - for the ground-cover stat log only
	BlendAt(X0 + TileSizeCm * 0.5, Y0 + TileSizeCm * 0.5, Recipe, TileForestWeight, DominantBiome);
	const float TileSqM = FMath::Square(TileSizeCm / 100.0f);
	const int32 NTrees =
		FMath::RoundToInt(TileSqM * TreesPerSqM * Recipe.TreeDensityScale);
	const int32 NBushes =
		FMath::RoundToInt(TileSqM * BushesPerSqM * Recipe.BushDensityScale);

	// one transform batch per mesh: NumTreeSpecies trees + NumShrubSpecies
	// shrubs (bush, sagebrush) + 2 rock variants (cube, sphere - see
	// RockMeshes' declaration for why) + scrub (grass_tuft). Extended
	// 2026-08-27 from the original fixed 7 (3 trees + bush + 2 rock + scrub)
	// alongside FBiomeRecipe's species-mix extension.
	constexpr int32 BushBatchBase = NumTreeSpecies;                 // shrub batches start here
	constexpr int32 RockBatchBase = BushBatchBase + NumShrubSpecies; // then rock variants
	constexpr int32 ScrubBatchIdx = RockBatchBase + 2;              // then ground scrub
	constexpr int32 NumBatches = ScrubBatchIdx + 1;
	TArray<FTransform> Batches[NumBatches];

	for (int32 i = 0; i < NTrees + NBushes; ++i)
	{
		const bool bTree = i < NTrees;
		const double Px = X0 + Rng.FRand() * TileSizeCm;
		const double Py = Y0 + Rng.FRand() * TileSizeCm;
		const float Accept = Rng.FRand();
		const float Yaw = Rng.FRand() * 360.0f;
		// tree heights follow a growth-curve distribution: full-grown most
		// common, log-style tail down to saplings. Ceiling trimmed 3.0 -> 2.2
		// (2026-07-25) so the mature canopy stops sitting right on the camera
		// (Taylor: "always buried in trees") while keeping the growth-curve
		// shape (bigger = more common) from the original §2.2 request.
		const float Scale = bTree
			? 2.2f * FMath::Exp(-1.8f * FMath::Square(1.0f - Rng.FRand()))
			: 1.0f + Rng.FRand() * 1.0f;
		const float SpeciesPick = Rng.FRand();
		// RNG fully consumed above -> layout independent of accept/trace results
		//
		// Ambient-floor fix (2026-08-27, "no vegetation at all in the
		// desert"): the raw canopy ForestWeight alone used to gate BOTH
		// trees and bushes to zero on every open-ground biome (CanyonBadlands/
		// DesertScrub/Chaparral) whose imagery has no detectable canopy - see
		// FBiomeRecipe::AmbientVegFloor's comment. Max(), not a replacement,
		// so real forest tiles (AmbientVegFloor == 0) are completely
		// unaffected.
		const float CanopyWeight = TileForestWeight >= 0.0f ? TileForestWeight : ForestWeightAt(Px, Py);
		const float Weight = FMath::Max(CanopyWeight, Recipe.AmbientVegFloor);
		if (Accept >= Weight)
		{
			continue;
		}
		FVector Ground;
		bool bDeferred = false;
		++GroundAttempts;
		if (!GroundAt(Px, Py, Player, Ground, &bDeferred))
		{
			if (bDeferred)
			{
				++GroundDeferred;
			}
			continue;
		}
		int32 MeshIdx;
		if (bTree)
		{
			MeshIdx = PickWeightedIndex(SpeciesPick, Recipe.SpeciesWeight, NumTreeSpecies);
			Tile.Trunks.Add(Ground);
			Tile.TrunkScales.Add(Scale);
		}
		else
		{
			// shrub-class: no trunk collision, same as the original plain
			// bush - sagebrush (index 1) rides along on this same pick
			MeshIdx = BushBatchBase + PickWeightedIndex(SpeciesPick, Recipe.ShrubWeight, NumShrubSpecies);
		}
		Batches[MeshIdx].Emplace(FRotator(0, Yaw, 0), Ground, FVector(Scale));
	}

	// Ground cover (2026-08-26): rocks (slope-gated, same RNG stream
	// continuing on from the tree/bush loop above - still fully
	// tile-seeded/deterministic) + extra low scrub (forest-weight-gated,
	// same convention as bushes). Runs alongside trees/bushes in the same
	// BuildTile() call, so it shares the tile's lifetime/teardown for free.
	int32 NRocksPlaced = 0, NScrubPlaced = 0;
	if (bEnableGroundCover)
	{
		const int32 NRocks = FMath::RoundToInt(
			TileSqM * RocksPerSqM * Recipe.RockDensityScale * GroundCoverDensityScale);
		const int32 NScrub = FMath::RoundToInt(
			TileSqM * ScrubPerSqM * Recipe.ScrubDensityScale * GroundCoverDensityScale);

		for (int32 i = 0; i < NRocks + NScrub; ++i)
		{
			const bool bRock = i < NRocks;
			const double Px = X0 + Rng.FRand() * TileSizeCm;
			const double Py = Y0 + Rng.FRand() * TileSizeCm;
			const float Accept = Rng.FRand();
			const float Yaw = Rng.FRand() * 360.0f;
			// rocks get a slight tilt so they read as resting/embedded rather
			// than machine-placed; scrub stays upright like bushes/trees
			const float Pitch = bRock ? (Rng.FRand() - 0.5f) * 20.0f : 0.0f;
			const float Roll = bRock ? (Rng.FRand() - 0.5f) * 20.0f : 0.0f;
			const float ScaleBase = bRock
				? FMath::Lerp(RockMinScale, RockMaxScale, Rng.FRand())
				: 0.6f + Rng.FRand() * 0.8f;   // scrub: shorter/denser than bushes
			// per-axis jitter is what actually sells "several rock variants"
			// out of just two base meshes (cube, sphere) - no two rocks end
			// up the same silhouette even within one HISM
			const float JitterX = bRock ? 0.75f + Rng.FRand() * 0.5f : 1.0f;
			const float JitterY = bRock ? 0.75f + Rng.FRand() * 0.5f : 1.0f;
			const float JitterZ = bRock ? 0.6f + Rng.FRand() * 0.5f : 1.0f;
			const float VariantPick = Rng.FRand();
			// RNG fully consumed above -> layout independent of accept/trace/slope results
			float Weight;
			if (bRock)
			{
				float Slope;
				const float SlopeT = SlopeAt(Px, Py, Slope)
					? FMath::Clamp((Slope - RockSlopeMinRatio)
						/ FMath::Max(0.001f, RockSlopeMaxRatio - RockSlopeMinRatio), 0.0f, 1.0f)
					: 0.0f;   // couldn't sample slope -> treat as flat, not "reject"
				Weight = FMath::Lerp(RockFlatChance, 1.0f, SlopeT);
			}
			else
			{
				// same ambient-floor fix as the tree/bush loop above, and for
				// the same reason: this extra low-scrub (grass_tuft) pass is
				// also canopy-ForestWeight-gated, so desert/chaparral/badlands
				// tiles were losing this ground fill too, not just trees/bush.
				const float CanopyWeight = TileForestWeight >= 0.0f ? TileForestWeight : ForestWeightAt(Px, Py);
				Weight = FMath::Max(CanopyWeight, Recipe.AmbientVegFloor);
			}
			if (Accept >= Weight)
			{
				continue;
			}
			FVector Ground;
			bool bDeferred = false;
			++GroundAttempts;
			if (!GroundAt(Px, Py, Player, Ground, &bDeferred))
			{
				if (bDeferred)
				{
					++GroundDeferred;
				}
				continue;
			}
			// Base-pivot correction (2026-08-27, see RockMeshBaseOffsetCm's
			// header comment): GroundAt() returns the traced SURFACE point,
			// but a center-pivoted mesh (the engine BasicShapes cube/sphere)
			// placed with its pivot AT that point buries half its geometry
			// and floats the rest above the surface - shift the placement Z
			// so the mesh's own base (not its pivot) lands on Ground instead.
			FVector PlacementLoc = Ground;
			const int32 RockVariantIdx = (VariantPick < 0.5f ? 0 : 1);
			const float ScaleZ = bRock ? ScaleBase * JitterZ : ScaleBase;
			const float BaseOffsetCm = bRock
				? RockMeshBaseOffsetCm[RockVariantIdx]
				: ScrubMeshBaseOffsetCm;
			PlacementLoc.Z -= BaseOffsetCm * ScaleZ;   // BaseOffsetCm <= 0, so this raises/lowers as needed
			++GroundCoverOffsetInstanceCount;
			GroundCoverMaxOffsetAppliedCm = FMath::Max(GroundCoverMaxOffsetAppliedCm,
				FMath::Abs(PlacementLoc.Z - Ground.Z));
			if (bRock)
			{
				const int32 MeshIdx = RockBatchBase + RockVariantIdx;
				Batches[MeshIdx].Emplace(FRotator(Pitch, Yaw, Roll), PlacementLoc,
					FVector(ScaleBase * JitterX, ScaleBase * JitterY, ScaleBase * JitterZ));
				// No collision: these are small stylized rocks (see
				// RockMinScale/RockMaxScale) - deliberately NOT added to the
				// trunk-capsule-pool pattern. That pool is sized/shaped
				// (vertical capsule) for tree trunks specifically; giving
				// rocks real collision would mean a second pool plus
				// per-rock capsule orientation to match the random
				// pitch/roll above, for an object the player mostly just
				// drives past rather than into. Skipped entirely per the
				// task's "otherwise skip collision" fallback.
				++NRocksPlaced;
			}
			else
			{
				Batches[ScrubBatchIdx].Emplace(FRotator(0, Yaw, 0), PlacementLoc, FVector(ScaleBase));
				++NScrubPlaced;
			}
		}
	}

	// Retry-until-terrain-ready gate (2026-08-27, see this function's header
	// comment) - only meaningful on the elevation-grid path, since that's
	// the only source of "deferred" (the raycast-only path's GroundAt()
	// failures are never marked deferred, just permanently excluded/missed
	// the way this whole function has always tolerated). No components have
	// been created yet at this point (Batches/Tile.Trunks are still plain
	// arrays) - discarding is free.
	if (bUseElevationGrid && GroundAttempts > 0
		&& (float)GroundDeferred / GroundAttempts > TileRetryDeferralRatio)
	{
		const double Now = GetWorld()->GetTimeSeconds();
		const double FirstAttempt = TileFirstBuildAttemptTime.FindOrAdd(Key, Now);
		if (Now - FirstAttempt < TileBuildGiveUpAfterSec)
		{
			++TileRetryCount;
			return false;   // key stays out of Tiles - Tick() retries it next frame
		}
		++TileGiveUpCount;
		UE_LOG(LogTemp, Warning,
			TEXT("ODForestManager: tile (%d,%d) gave up waiting for terrain "
			     "after %.1f s (%d/%d candidates still deferred) - committing "
			     "as-is"),
			Key.X, Key.Y, Now - FirstAttempt, GroundDeferred, GroundAttempts);
	}
	TileFirstBuildAttemptTime.Remove(Key);

	// Per-species instance counts (2026-08-27 verification instrumentation,
	// see the gated stat log below) - filled in as each batch is turned into
	// a HISM so the log can show the actual species mix a tile spawned, not
	// just the recipe's target weights.
	int32 SpeciesInstanceCounts[NumTreeSpecies] = {};
	int32 ShrubInstanceCounts[NumShrubSpecies] = {};

	for (int32 m = 0; m < NumBatches; ++m)
	{
		if (Batches[m].Num() == 0)
		{
			continue;
		}
		UStaticMesh* Mesh;
		const bool bIsTree = m < NumTreeSpecies;
		const bool bIsShrub = !bIsTree && m < RockBatchBase;
		if (bIsTree) Mesh = TreeMeshes[m].Get();
		else if (bIsShrub) Mesh = ShrubMeshes[m - BushBatchBase].Get();
		else if (m == ScrubBatchIdx) Mesh = ScrubMesh.Get();
		else Mesh = RockMeshes[m - RockBatchBase].Get();
		if (!Mesh)
		{
			continue;
		}
		if (bIsTree) SpeciesInstanceCounts[m] = Batches[m].Num();
		else if (bIsShrub) ShrubInstanceCounts[m - BushBatchBase] = Batches[m].Num();
		const bool bLowVeg = bIsShrub || m == ScrubBatchIdx;   // bush/sagebrush/scrub: closer cull, no shadow
		auto* Hism = NewObject<UHierarchicalInstancedStaticMeshComponent>(this);
		Hism->SetupAttachment(RootComponent);
		Hism->SetStaticMesh(Mesh);
		Hism->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		Hism->SetCullDistances(bLowVeg ? 20000 : 45000, bLowVeg ? 45000 : 60000);
		Hism->SetCastShadow(!bLowVeg);
		Hism->SetAffectDynamicIndirectLighting(false);
		Hism->RegisterComponent();
		Hism->AddInstances(Batches[m], false, true);
		Tile.Hisms.Add(Hism);

		// One-time proof-of-fix readback (2026-08-27, "hitting a bush stops/
		// damages the car" live-drive report) - see bLoggedBushCollisionCheck's
		// header comment.
		if (bIsShrub && !bLoggedBushCollisionCheck)
		{
			bLoggedBushCollisionCheck = true;
			UE_LOG(LogTemp, Display,
				TEXT("ODForestManager: shrub HISM collision readback = %d "
				     "(0=NoCollision) - trees get real collision separately via "
				     "the trunk capsule pool, shrubs/rocks/scrub never do"),
				(int32)Hism->GetCollisionEnabled());
		}
		else if (m == ScrubBatchIdx && !bLoggedScrubCollisionCheck)
		{
			bLoggedScrubCollisionCheck = true;
			UE_LOG(LogTemp, Display,
				TEXT("ODForestManager: scrub HISM collision readback = %d "
				     "(0=NoCollision)"),
				(int32)Hism->GetCollisionEnabled());
		}
	}

	// Per-biome species-mix verification log (2026-08-27, "chaparral isn't
	// working and all the trees are the same" fix) - deliberately
	// UNGATED (unlike the ground-cover stat log just below): almost every
	// tile in the initial fill burst builds within the same
	// GetTimeSeconds() frame (up to TilesPerTick=40 per Tick()), so a 5 s
	// time-gate here would only ever capture one tile's mix out of the
	// whole disc - useless for confirming per-biome variation across tiles.
	// BuildTile() is only called for NEW tiles entering the radius (a
	// committed tile is never rebuilt), so this stays cheap after the
	// initial fill settles. Instance counts are per-TILE, not a running
	// total.
	{
		UE_LOG(LogTemp, Display,
			TEXT("ODForestManager: tile (%d,%d) biome=%s species blob=%d olive=%d "
			     "conifer=%d scrubOak=%d manzanita=%d yucca=%d canyonOak=%d | "
			     "shrub bush=%d sagebrush=%d"),
			Key.X, Key.Y, *StaticEnum<EODBiome>()->GetNameStringByValue((int64)DominantBiome),
			SpeciesInstanceCounts[0], SpeciesInstanceCounts[1], SpeciesInstanceCounts[2],
			SpeciesInstanceCounts[3], SpeciesInstanceCounts[4], SpeciesInstanceCounts[5],
			SpeciesInstanceCounts[6], ShrubInstanceCounts[0], ShrubInstanceCounts[1]);
	}

	// gated stat log (2026-08-26) - same 5 s cadence as UpdateCollision()'s
	// diag log, proving the scatter runs with real per-biome variation as
	// the player crosses biomes (rock/scrub counts should visibly shift with
	// dominant biome, e.g. near-zero rocks in DeciduousForest vs heavy in
	// RockyFoothill/AlpineBare/CanyonBadlands).
	if (bEnableGroundCover)
	{
		if (const double Now = GetWorld()->GetTimeSeconds(); Now - LastGroundCoverLogTime > 5.0)
		{
			LastGroundCoverLogTime = Now;
			UE_LOG(LogTemp, Display,
				TEXT("ODForestManager: tile (%d,%d) biome=%s groundcover rocks=%d "
				     "scrub=%d rockDensity=%.2f scrubDensity=%.2f"),
				Key.X, Key.Y,
				*StaticEnum<EODBiome>()->GetNameStringByValue((int64)DominantBiome),
				NRocksPlaced, NScrubPlaced,
				Recipe.RockDensityScale, Recipe.ScrubDensityScale);
		}
	}

	// Base-pivot correction summary (2026-08-27, see RockMeshBaseOffsetCm's
	// header comment) - confirms the correction is actually doing something
	// (nonzero max offset) rather than a silently-zero bounding-box read, and
	// gives a rough sense of scale (should track ~half the largest scaled
	// rock's height, given RockMinScale/RockMaxScale).
	if (const double Now = GetWorld()->GetTimeSeconds(); Now - LastGroundCoverOffsetLogTime > 5.0)
	{
		LastGroundCoverOffsetLogTime = Now;
		UE_LOG(LogTemp, Display,
			TEXT("ODForestManager: groundcover base-pivot correction - %d instance(s) "
			     "placed, max |Z correction| = %.1f cm in the last 5s"),
			GroundCoverOffsetInstanceCount, GroundCoverMaxOffsetAppliedCm);
		GroundCoverOffsetInstanceCount = 0;
		GroundCoverMaxOffsetAppliedCm = 0.0f;
	}

	if (const double Now = GetWorld()->GetTimeSeconds(); Now - LastElevationCorrectionLogTime > 5.0
		&& (TileRetryCount > 0 || TileGiveUpCount > 0))
	{
		// piggybacks on the same 5s gate as the per-candidate elevation log
		// above rather than a new timer - both describe the same fix.
		UE_LOG(LogTemp, Display,
			TEXT("ODForestManager: tile retry-until-terrain-ready - %d tile(s) "
			     "held back for retry, %d gave up and committed as-is (last 5s)"),
			TileRetryCount, TileGiveUpCount);
		TileRetryCount = 0;
		TileGiveUpCount = 0;
	}

	Tiles.Add(Key, MoveTemp(Tile));
	return true;
}

void AODForestManager::ReleaseTile(const FIntPoint& Key)
{
	if (FTile* Tile = Tiles.Find(Key))
	{
		for (const auto& Hism : Tile->Hisms)
		{
			if (Hism)
			{
				Hism->DestroyComponent();
			}
		}
		Tiles.Remove(Key);
	}
}

void AODForestManager::UpdateCollision(const FVector& PlayerLoc)
{
	struct FNear { FVector Pos; float Scale; float DistSq; };
	TArray<FNear> Near;
	const float R2 = CollisionRadius * CollisionRadius;
	const FIntPoint Center(FMath::FloorToInt(PlayerLoc.X / TileSizeCm),
	                       FMath::FloorToInt(PlayerLoc.Y / TileSizeCm));
	const int32 Reach = FMath::CeilToInt(CollisionRadius / TileSizeCm);
	for (int32 dy = -Reach; dy <= Reach; ++dy)
	{
		for (int32 dx = -Reach; dx <= Reach; ++dx)
		{
			if (const FTile* Tile = Tiles.Find(Center + FIntPoint(dx, dy)))
			{
				for (int32 i = 0; i < Tile->Trunks.Num(); ++i)
				{
					const float D2 = FVector::DistSquared2D(Tile->Trunks[i], PlayerLoc);
					if (D2 < R2)
					{
						Near.Add({Tile->Trunks[i], Tile->TrunkScales[i], D2});
					}
				}
			}
		}
	}
	Near.Sort([](const FNear& A, const FNear& B) { return A.DistSq < B.DistSq; });

	for (int32 i = 0; i < Pool.Num(); ++i)
	{
		UCapsuleComponent* Cap = Pool[i];
		if (i < Near.Num())
		{
			Cap->SetWorldLocation(Near[i].Pos
				+ FVector(0, 0, TrunkHalfHeight * Near[i].Scale));
			Cap->SetWorldScale3D(FVector(Near[i].Scale));
			Cap->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
		}
		else
		{
			Cap->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		}
	}

	if (const double Now = GetWorld()->GetTimeSeconds(); Now - LastDiagTime > 5.0)
	{
		LastDiagTime = Now;
		int32 NumInst = 0;
		for (const auto& Pair : Tiles)
		{
			for (const auto& H : Pair.Value.Hisms)
			{
				NumInst += H ? H->GetInstanceCount() : 0;
			}
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODForestManager: tiles=%d instances=%d trunksNear=%d capsulesOn=%d"),
			Tiles.Num(), NumInst, Near.Num(), FMath::Min(Near.Num(), Pool.Num()));
	}
}

void AODForestManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	if (!bReady)
	{
		return;
	}
	const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Player)
	{
		return;
	}
	// Wait for the pawn to actually settle onto real ground before building
	// any tiles (2026-08-26, live-drive bug: PlayerZHint below is exactly
	// this settled height, but AODVehiclePawn::TeleportToSpawn parks the
	// pawn at a fixed interim Z - see its own comment - for the handful of
	// frames before TrySnapToGround() finds the real surface; building
	// tiles against THAT height planted trees far below the real terrain,
	// and because a built tile is never revisited (see the `!Tiles.Contains`
	// check below), the damage was permanent for that tile's lifetime, not
	// just a one-frame glitch. Fails open (proceeds anyway) for any pawn
	// that isn't an AODVehiclePawn, matching this function's behavior
	// before this gate existed.
	if (const AODVehiclePawn* VehiclePawn = Cast<AODVehiclePawn>(Player);
		VehiclePawn && !VehiclePawn->IsGroundSnapped())
	{
		return;
	}
	const FVector Loc = Player->GetActorLocation();
	// see PlayerZHint's declaration comment (ODForestManager.h) - GroundAt()/
	// HeightOnlyAt()'s raycast fallback traces relative to this instead of a
	// hardcoded absolute Z.
	PlayerZHint = Loc.Z;

	// spawn missing tiles nearest-first, a few per tick
	const FIntPoint Center(FMath::FloorToInt(Loc.X / TileSizeCm),
	                       FMath::FloorToInt(Loc.Y / TileSizeCm));
	const int32 Reach = FMath::CeilToInt(SpawnRadius / TileSizeCm);
	int32 Built = 0;
	for (int32 Ring = 0; Ring <= Reach && Built < TilesPerTick; ++Ring)
	{
		for (int32 dy = -Ring; dy <= Ring && Built < TilesPerTick; ++dy)
		{
			for (int32 dx = -Ring; dx <= Ring && Built < TilesPerTick; ++dx)
			{
				if (FMath::Max(FMath::Abs(dx), FMath::Abs(dy)) != Ring)
				{
					continue;
				}
				const FIntPoint Key = Center + FIntPoint(dx, dy);
				if (!Tiles.Contains(Key))
				{
					// BuildTile() returning false (see its own comment) leaves
					// Key out of Tiles - it's still "missing" next Tick(), so
					// this same nearest-first loop retries it automatically
					// with no extra bookkeeping here.
					BuildTile(Key, Player);
					++Built;
				}
			}
		}
	}

	// retire tiles far outside the radius (hysteresis: +2 tiles)
	TArray<FIntPoint> Gone;
	for (const auto& Pair : Tiles)
	{
		if (FMath::Max(FMath::Abs(Pair.Key.X - Center.X),
		               FMath::Abs(Pair.Key.Y - Center.Y)) > Reach + 2)
		{
			Gone.Add(Pair.Key);
		}
	}
	for (const FIntPoint& Key : Gone)
	{
		ReleaseTile(Key);
	}

	UpdateCollision(Loc);

	// headless visual QA: `-odshot=25` captures a screenshot 25 s after boot.
	// ShotAt/bShotParsed/bShotDone are instance members (see ODForestManager.h)
	// rather than function-local `static` - a new AODForestManager instance
	// is spawned fresh every PIE session, so this resets naturally per run
	// instead of persisting for the life of the editor process.
	if (!bShotParsed)
	{
		bShotParsed = true;
		FParse::Value(FCommandLine::Get(), TEXT("odshot="), ShotAt);
	}
	if (ShotAt > 0.0f && !bShotDone && GetWorld()->GetTimeSeconds() > ShotAt)
	{
		bShotDone = true;
		FScreenshotRequest::RequestScreenshot(false);
		UE_LOG(LogTemp, Display, TEXT("ODForestManager: screenshot requested"));
	}
}
