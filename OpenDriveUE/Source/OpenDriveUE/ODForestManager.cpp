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
#include "PhysicalMaterials/PhysicalMaterial.h"
#include "UnrealClient.h"

namespace
{
// TreeMeshPaths now lives in ODBinSidecarUtil.h (2026-08-22) - was
// independently duplicated here and in ODBarrierManager.cpp, which collided
// under the standalone Game target's unity build (C2374/C2086).

/** Indexed by EODBiome - order MUST match the enum in ODForestManager.h.
 *  FBiomeRecipe itself lives in the header (RecipeAt()/BlendAt() need the
 *  type there); the actual per-biome values stay local to this file since
 *  nothing outside this module needs them. */
const FBiomeRecipe BiomeRecipeTable[] = {
	/* DeciduousForest */ {{0.85f, 0.05f, 0.10f}, 1.0f, 0.6f},
	/* EvergreenForest */ {{0.05f, 0.05f, 0.90f}, 1.0f, 0.6f},
	/* Chaparral        */ {{0.10f, 0.70f, 0.20f}, 0.5f, 1.0f},
	/* DesertScrub      */ {{0.05f, 0.85f, 0.10f}, 0.25f, 0.8f},
	/* PlainsGrassland  */ {{0.30f, 0.50f, 0.20f}, 0.15f, 1.3f},
	/* Wetland          */ {{0.55f, 0.05f, 0.40f}, 1.0f, 1.1f},
	/* AlpineBare       */ {{0.20f, 0.30f, 0.50f}, 0.05f, 0.1f},
	/* ArcticTundra     */ {{0.10f, 0.60f, 0.30f}, 0.02f, 0.3f},
	/* Farmland         */ {{0.20f, 0.60f, 0.20f}, 0.10f, 0.5f},
	/* Savanna          */ {{0.40f, 0.50f, 0.10f}, 0.20f, 0.9f},
	/* Rainforest       */ {{0.75f, 0.05f, 0.20f}, 1.2f, 0.9f},
	/* CoastalDune      */ {{0.10f, 0.80f, 0.10f}, 0.05f, 0.9f},
	/* CanyonBadlands   */ {{0.05f, 0.80f, 0.15f}, 0.10f, 0.3f},
	/* RockyFoothill    */ {{0.15f, 0.45f, 0.40f}, 0.5f, 0.6f},
};
static_assert(UE_ARRAY_COUNT(BiomeRecipeTable) == (uint8)EODBiome::RockyFoothill + 1,
	"BiomeRecipeTable must have one entry per EODBiome value, in enum order");

/** Maps an already-drawn [0,1) random value to a species index using the
 *  recipe's weights - takes R rather than an FRandomStream so it never
 *  consumes RNG itself (BuildTile()'s "RNG fully consumed above" ordering
 *  guarantee depends on that). */
int32 PickWeightedSpecies(float R, const float Weight[3])
{
	const float Sum = Weight[0] + Weight[1] + Weight[2];
	const float T0 = Weight[0] / Sum;
	const float T1 = T0 + Weight[1] / Sum;
	return R < T0 ? 0 : (R < T1 ? 1 : 2);
}
}

AODForestManager::AODForestManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODForestManager::BeginPlay()
{
	Super::BeginPlay();

	for (const TCHAR* Path : TreeMeshPaths)
	{
		TreeMeshes.Add(LoadObject<UStaticMesh>(nullptr, Path));
	}
	BushMesh = LoadObject<UStaticMesh>(
		nullptr, TEXT("/Game/OpenDrive/Trees/bush.bush"));

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
                                FBiomeRecipe& OutRecipe, float& OutForestWeight) const
{
	if (!bUsePointBiomes || BiomePoints.Num() == 0)
	{
		// Landscape-mask path (Tail of the Dragon), or no biome data loaded
		// at all: single fixed recipe, raster forest weight handled
		// separately by ForestWeightAt() per scatter candidate as before.
		OutRecipe = BiomeRecipeTable[(uint8)DefaultBiome];
		OutForestWeight = -1.0f;   // sentinel: BuildTile() falls back to ForestWeightAt()
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
	float WeightSum = 0.0f, SpeciesAcc[3] = {0.f, 0.f, 0.f};
	float TreeScaleAcc = 0.0f, BushScaleAcc = 0.0f, ForestWeightAcc = 0.0f;
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
		SpeciesAcc[0] += R.SpeciesWeight[0] * W;
		SpeciesAcc[1] += R.SpeciesWeight[1] * W;
		SpeciesAcc[2] += R.SpeciesWeight[2] * W;
		TreeScaleAcc += R.TreeDensityScale * W;
		BushScaleAcc += R.BushDensityScale * W;
		ForestWeightAcc += Pt.ForestWeight * W;
		WeightSum += W;
	}

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
	OutRecipe.SpeciesWeight[0] = SpeciesAcc[0] / WeightSum;
	OutRecipe.SpeciesWeight[1] = SpeciesAcc[1] / WeightSum;
	OutRecipe.SpeciesWeight[2] = SpeciesAcc[2] / WeightSum;
	OutRecipe.TreeDensityScale = TreeScaleAcc / WeightSum;
	OutRecipe.BushDensityScale = BushScaleAcc / WeightSum;
	// town-proximity dampening (2026-07-24) - see TownForestDampen()'s
	// comment: the biome classifier alone has no idea a populated area is
	// nearby, so this softly thins the approach into town instead of
	// leaving a solid tree wall right at BuildingExclusionCm's hard cutoff.
	OutForestWeight = (ForestWeightAcc / WeightSum) * TownForestDampen(WorldX, WorldY);
}

bool AODForestManager::GroundAt(double WorldX, double WorldY,
                                const AActor* Ignore, FVector& OutHit) const
{
	// Open-world/streamed-terrain path (2026-07-20): fully data-driven, no physics
	// query at all. GroundAt() used to trust a raycast against whatever
	// terrain LOD happened to be resident - fine on a real Landscape, but
	// on live-streamed terrain a raycast can hit a stale/coarse LOD (or genuinely wrong
	// low-zoom source data far from the road) and the tree's transform,
	// once baked, never updates even after the terrain visually catches
	// up. Both the elevation grid and centerline are pre-verified offline
	// against the same DEM data the road/terrain pipeline itself uses.
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
		float Z;
		if (!ElevationGridHeightAt(WorldX, WorldY, Z))
		{
			return false;
		}
		OutHit = FVector(WorldX, WorldY, Z);
		return true;
	}

	// Landscape path (Tail of the Dragon) - unchanged raycast behavior.
	FHitResult Hit;
	const FVector Start(WorldX, WorldY, 100000.0);
	const FVector End(WorldX, WorldY, -100000.0);
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
	// never plant a tree on pavement (road meshes carry PM_Asphalt)
	if (const UPhysicalMaterial* Pm = Hit.PhysMaterial.Get();
		Pm && Pm->GetName().Contains(TEXT("Asphalt")))
	{
		return false;
	}
	OutHit = Hit.ImpactPoint;
	return true;
}

void AODForestManager::BuildTile(const FIntPoint& Key, const AActor* Player)
{
	FTile& Tile = Tiles.Add(Key);
	FRandomStream Rng(HashCombine(GetTypeHash(Key), (uint32)RandomSeed));

	const double X0 = Key.X * (double)TileSizeCm;
	const double Y0 = Key.Y * (double)TileSizeCm;
	FBiomeRecipe Recipe;
	float TileForestWeight;   // -1 = no point data for this tile; fall back to raster ForestWeightAt() per candidate
	BlendAt(X0 + TileSizeCm * 0.5, Y0 + TileSizeCm * 0.5, Recipe, TileForestWeight);
	const float TileSqM = FMath::Square(TileSizeCm / 100.0f);
	const int32 NTrees =
		FMath::RoundToInt(TileSqM * TreesPerSqM * Recipe.TreeDensityScale);
	const int32 NBushes =
		FMath::RoundToInt(TileSqM * BushesPerSqM * Recipe.BushDensityScale);

	// one transform batch per mesh (3 trees + bush)
	TArray<FTransform> Batches[4];

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
		const float Weight = TileForestWeight >= 0.0f ? TileForestWeight : ForestWeightAt(Px, Py);
		if (Accept >= Weight)
		{
			continue;
		}
		FVector Ground;
		if (!GroundAt(Px, Py, Player, Ground))
		{
			continue;
		}
		int32 MeshIdx = 3;                             // bush batch
		if (bTree)
		{
			MeshIdx = PickWeightedSpecies(SpeciesPick, Recipe.SpeciesWeight);
			Tile.Trunks.Add(Ground);
			Tile.TrunkScales.Add(Scale);
		}
		Batches[MeshIdx].Emplace(FRotator(0, Yaw, 0), Ground, FVector(Scale));
	}

	for (int32 m = 0; m < 4; ++m)
	{
		if (Batches[m].Num() == 0)
		{
			continue;
		}
		UStaticMesh* Mesh = m == 3 ? BushMesh.Get() : TreeMeshes[m].Get();
		if (!Mesh)
		{
			continue;
		}
		auto* Hism = NewObject<UHierarchicalInstancedStaticMeshComponent>(this);
		Hism->SetupAttachment(RootComponent);
		Hism->SetStaticMesh(Mesh);
		Hism->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		Hism->SetCullDistances(m == 3 ? 20000 : 45000, m == 3 ? 45000 : 60000);
		Hism->SetCastShadow(m != 3);
		Hism->SetAffectDynamicIndirectLighting(false);
		Hism->RegisterComponent();
		Hism->AddInstances(Batches[m], false, true);
		Tile.Hisms.Add(Hism);
	}
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
	const FVector Loc = Player->GetActorLocation();

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

	// headless visual QA: `-odshot=25` captures a screenshot 25 s after boot
	static float ShotAt = -1.0f;
	static bool bShotParsed = false, bShotDone = false;
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
