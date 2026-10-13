#include "ODBarrierManager.h"
#include "ODAssetDetail.h"
#include "ODVisualStyle.h"

#include "ODBinSidecarUtil.h"
#include "ODRoadManager.h"
#include "Async/Async.h"
#include "Components/DynamicMeshComponent.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/DynamicMeshOverlay.h"
#include "Engine/StaticMesh.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

using namespace UE::Geometry;

// TreeMeshPaths/ExtendedTreeMeshPaths/NumExtendedTreeSpecies/
// PickWeightedIndex all live in ODBinSidecarUtil.h (2026-08-22/2026-08-27) -
// were independently duplicated across this file and ODForestManager.cpp,
// which collided under the standalone Game target's unity build
// (C2374/C2086). This manager now scatters from the SAME extended 7-species
// set ODForestManager uses (2026-08-27 biome-aware windbreak fix, see
// BiomeAt()/FinalizeTile()) rather than the classic 3 - TreeMeshPaths itself
// is unused here now, kept imported only because ODBinSidecarUtil.h pulls it
// in as part of the same header.

AODBarrierManager::AODBarrierManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODBarrierManager::BeginPlay()
{
	Super::BeginPlay();

	for (const TCHAR* Path : ExtendedTreeMeshPaths)
	{
		TreeMeshes.Add(ODAssetDetail::Upgrade(ODVisualStyle::Pick(LoadObject<UStaticMesh>(nullptr, Path))));
	}
	BushMesh = ODAssetDetail::Upgrade(ODVisualStyle::Pick(LoadObject<UStaticMesh>(nullptr, TEXT("/Game/OpenDrive/Trees/bush.bush"))));

	// Auto-derive BiomeFilePrefix from the level's own AODRoadManager if the
	// level (or a designer) hasn't already set one explicitly - see
	// BiomeFilePrefix's own header comment for why this is resolved locally
	// instead of via AODGameMode setting the property after spawn.
	if (BiomeFilePrefix.IsEmpty())
	{
		if (UWorld* World = GetWorld())
		{
			// this area's own road manager, matched by prefix (2026-10-05,
			// large world: several area sets can be live at once)
			FString OwnArea = BarrierTilePrefix;
			OwnArea.RemoveFromStart(TEXT("barrier_"));
			for (TActorIterator<AODRoadManager> It(World); It; ++It)
			{
				if (It->RoadTilePrefix != TEXT("road_") + OwnArea)
				{
					continue;
				}
				FString Area = It->RoadTilePrefix;
				Area.RemoveFromStart(TEXT("road_"));
				Area.RemoveFromEnd(TEXT("_test"));
				if (!Area.IsEmpty())
				{
					BiomeFilePrefix = FString::Printf(TEXT("biome_%s_test"), *Area);
				}
				break;
			}
		}
	}
	LoadBiomePoints();
}

bool AODBarrierManager::LoadBiomePoints()
{
	if (BiomeFilePrefix.IsEmpty())
	{
		return false;   // no RoadManager found (or it had no derivable area name) - use the classic-3 fallback
	}
	// ODB1 format - X_cm f32, Y_cm f32, Biome u8, ForestWeight255 u8 - see
	// AODForestManager::LoadBiomePoints() for the identical parse this
	// mirrors (same sidecar file, same format). The ForestWeight byte isn't
	// needed here (only the classified biome id is), read past but discarded.
	TArray<uint8> Bin;
	const FString BinPath = FPaths::ProjectContentDir()
		/ TEXT("OpenDrive/Masks") / (BiomeFilePrefix + TEXT(".bin"));
	if (!FFileHelper::LoadFileToArray(Bin, *BinPath) || Bin.Num() < 8
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'B' || (Bin[3] != '1' && Bin[3] != '2'))
	{
		return false;
	}
	const int32 N = (int32)Bin[4] | (int32)Bin[5] << 8
		| (int32)Bin[6] << 16 | (int32)Bin[7] << 24;
	// ODB2 (2026-09-25) appends 4 bytes (Northness/Moisture/ElevM) this
	// manager doesn't need - just a wider stride.
	const int32 RecordBytes = Bin[3] == '2' ? 14 : 10;
	if (N <= 0 || Bin.Num() < 8 + (int64)N * RecordBytes)
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODBarrierManager: %s truncated/corrupt (N=%d, %lld bytes)"),
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
		const FIntPoint Cell(FMath::FloorToInt(X / BiomeCellCm), FMath::FloorToInt(Y / BiomeCellCm));
		BiomeBuckets.FindOrAdd(Cell).Add(i);
	}
	bUseBiome = true;
	UE_LOG(LogTemp, Display,
		TEXT("ODBarrierManager: loaded %d biome point(s) from %s for windbreak species pick"),
		N, *BinPath);
	return true;
}

EODBiome AODBarrierManager::BiomeAt(double WorldX, double WorldY) const
{
	if (!bUseBiome)
	{
		return EODBiome::DeciduousForest;
	}
	const int32 Cx = FMath::FloorToInt(WorldX / BiomeCellCm);
	const int32 Cy = FMath::FloorToInt(WorldY / BiomeCellCm);
	const int32 MaxRing = FMath::CeilToInt(BiomeSearchRadiusCm / BiomeCellCm);
	int32 BestIdx = INDEX_NONE;
	double BestDistSq = TNumericLimits<double>::Max();
	for (int32 Ring = 0; Ring <= MaxRing; ++Ring)
	{
		for (int32 dy = -Ring; dy <= Ring; ++dy)
		{
			for (int32 dx = -Ring; dx <= Ring; ++dx)
			{
				if (FMath::Max(FMath::Abs(dx), FMath::Abs(dy)) != Ring)
				{
					continue;   // interior cells already visited on an earlier ring
				}
				if (const TArray<int32>* Bucket = BiomeBuckets.Find(FIntPoint(Cx + dx, Cy + dy)))
				{
					for (int32 Idx : *Bucket)
					{
						const double DistSq = FVector2D::DistSquared(
							BiomePoints[Idx].WorldXY, FVector2D(WorldX, WorldY));
						if (DistSq < BestDistSq)
						{
							BestDistSq = DistSq;
							BestIdx = Idx;
						}
					}
				}
			}
		}
		// Found something, and no farther ring could possibly be closer
		// (every point in ring R+1 is at least R*BiomeCellCm away) - stop early.
		if (BestIdx != INDEX_NONE && BestDistSq <= FMath::Square((double)Ring * BiomeCellCm))
		{
			break;
		}
	}
	if (BestIdx == INDEX_NONE || BestDistSq > FMath::Square((double)BiomeSearchRadiusCm))
	{
		return EODBiome::DeciduousForest;
	}
	return BiomePoints[BestIdx].Biome;
}

void AODBarrierManager::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	if (PendingTiles.Num() > 0)
	{
		UE_LOG(LogTemp, Display,
			TEXT("ODBarrierManager: EndPlay with %d tile load(s) still in flight - "
			     "their completions will no-op harmlessly when they land"),
			PendingTiles.Num());
	}
	Super::EndPlay(EndPlayReason);
}

bool AODBarrierManager::ParseBarrierTileFile(const FString& Path, FParsedBarrierTile& Out)
{
	TArray<uint8> Bin;
	if (!FFileHelper::LoadFileToArray(Bin, *Path) || Bin.Num() < 4 + GenerationIdBytes + 4
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'W' || Bin[3] != '3')
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
	const uint32 PointCount = ReadU32LE(Cur); Cur += 4;
	constexpr int32 PointBytes = 4 + 4 + 4 + 4;
	if (Remaining() < (int64)PointCount * PointBytes)
	{
		return false;
	}
	Out.Points.SetNumUninitialized(PointCount);
	for (uint32 i = 0; i < PointCount; ++i, Cur += PointBytes)
	{
		Out.Points[i].Pos = FVector(ReadF32LE(Cur), ReadF32LE(Cur + 4), ReadF32LE(Cur + 8));
		Out.Points[i].Seed = ReadU32LE(Cur + 12);
	}

	if (Remaining() < 4)
	{
		return false;
	}
	const uint32 RunCount = ReadU32LE(Cur); Cur += 4;
	Out.WallRuns.Reserve(RunCount);
	for (uint32 r = 0; r < RunCount; ++r)
	{
		if (Remaining() < 4)
		{
			return false;
		}
		const uint32 SCount = ReadU32LE(Cur); Cur += 4;
		const int64 NeedBytes = (int64)SCount * 12;
		if (Remaining() < NeedBytes)
		{
			return false;
		}
		FParsedWallRun& Run = Out.WallRuns.AddDefaulted_GetRef();
		Run.Stations.SetNumUninitialized(SCount);
		for (uint32 s = 0; s < SCount; ++s, Cur += 12)
		{
			Run.Stations[s] = FVector(ReadF32LE(Cur), ReadF32LE(Cur + 4), ReadF32LE(Cur + 8));
		}
	}
	return true;
}

void AODBarrierManager::RequestTileLoad(const FIntPoint& Key)
{
	PendingTiles.Add(Key);
	const FString Path = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ FString::Printf(TEXT("%s_tile_%d_%d.bin"), *BarrierTilePrefix, Key.X, Key.Y);

	TWeakObjectPtr<AODBarrierManager> WeakThis(this);
	Async(EAsyncExecution::ThreadPool, [WeakThis, Key, Path]()
	{
		FParsedBarrierTile Parsed;
		const bool bOk = ParseBarrierTileFile(Path, Parsed);
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

void AODBarrierManager::OnTileParsed(const FIntPoint& Key, bool bOk, FParsedBarrierTile&& Parsed)
{
	PendingTiles.Remove(Key);
	if (!bOk)
	{
		MissingTiles.Add(Key);   // no barrier here (bridge/town gap) - expected in places
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
			TEXT("ODBarrierManager: tile (%d,%d) generation id %s does not match "
			     "%s's first-loaded generation id %s - tiles from different pipeline "
			     "runs may be mixed (AUDIT-RELIABILITY.md SS3.1/SS5.1)"),
			Key.X, Key.Y, *Parsed.GenerationId, *BarrierTilePrefix, *FirstGenerationId);
	}

	FinalizeTile(Key, MoveTemp(Parsed));
}

void AODBarrierManager::FinalizeTile(const FIntPoint& Key, FParsedBarrierTile&& Parsed)
{
	FBarrierTile& Tile = Tiles.Add(Key);

	// one HISM per species (NumExtendedTreeSpecies trees + bush) - visual
	// only, no collision (the wall chunks below do the actual blocking).
	// Extended 2026-08-27 from the original fixed 4 (3 classic trees + bush)
	// alongside the biome-aware species pick below - see BiomeFilePrefix's
	// header comment.
	constexpr int32 BushHismIdx = NumExtendedTreeSpecies;
	Tile.Hisms.SetNum(NumExtendedTreeSpecies + 1);
	for (int32 m = 0; m < NumExtendedTreeSpecies + 1; ++m)
	{
		UStaticMesh* Mesh = m == BushHismIdx ? BushMesh.Get() : TreeMeshes[m].Get();
		if (!Mesh)
		{
			continue;
		}
		auto* Hism = NewObject<UHierarchicalInstancedStaticMeshComponent>(this);
		Hism->SetupAttachment(RootComponent);
		Hism->SetStaticMesh(Mesh);
		Hism->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		Hism->SetAffectDynamicIndirectLighting(false);   // same Lumen atlas-overload fix as AODForestManager
		Hism->RegisterComponent();
		Tile.Hisms[m] = Hism;
	}

	// Per-instance species pick (2026-08-27, biome-aware windbreak fix) -
	// each barrier point independently samples the biome at its own position
	// (finer-grained than "the run's location" the task described, since a
	// long run can cross a biome boundary) and picks from that biome's own
	// SpeciesWeight mix (AODForestManager::RecipeForBiome(), the SAME table
	// BuildTile() uses) instead of always uniform-random among the classic 3.
	// Tree-vs-bush odds (25% bush) match the original RandRange(0,3)==3 case
	// exactly, just decided before the species roll instead of folded into
	// one 4-way roll, since bush isn't part of the 7-way SpeciesWeight table.
	int32 SpeciesInstanceCounts[NumExtendedTreeSpecies] = {};
	int32 BushInstanceCount = 0;
	// Batched per-HISM (2026-09-07 frame-drop pass): AddInstance one at a
	// time dirtied each HISM's cluster tree once per barrier point - a tile
	// holds hundreds of points, all landing in the same frame. Collect per
	// species, then one AddInstances per HISM below.
	TArray<TArray<FTransform>> PendingXfs;
	PendingXfs.SetNum(NumExtendedTreeSpecies + 1);
	for (const FParsedBarrierPoint& Pt : Parsed.Points)
	{
		FRandomStream Rng(Pt.Seed);
		const bool bBush = Rng.FRand() < 0.25f;
		const float SpeciesPick = Rng.FRand();
		const float Scale = Rng.FRandRange(0.85f, 1.3f);
		const float Yaw = Rng.FRandRange(0.0f, 360.0f);

		int32 HismIdx;
		if (bBush)
		{
			HismIdx = BushHismIdx;
			++BushInstanceCount;
		}
		else
		{
			const EODBiome Biome = BiomeAt(Pt.Pos.X, Pt.Pos.Y);
			HismIdx = PickWeightedIndex(SpeciesPick,
				AODForestManager::RecipeForBiome(Biome).SpeciesWeight, NumExtendedTreeSpecies);
			++SpeciesInstanceCounts[HismIdx];
		}
		if (Tile.Hisms[HismIdx])
		{
			PendingXfs[HismIdx].Emplace(FRotator(0, Yaw, 0), Pt.Pos, FVector(Scale));
		}
	}
	for (int32 m = 0; m < PendingXfs.Num(); ++m)
	{
		if (PendingXfs[m].Num() > 0)
		{
			Tile.Hisms[m]->AddInstances(PendingXfs[m], false, true);
		}
	}

	// Per-tile biome/species verification log (2026-08-27) - mirrors
	// AODForestManager::BuildTile()'s own per-tile species-mix Display log so
	// a headless/live run can confirm windbreak rows through desert/scrub
	// biomes actually pick drought-looking species instead of the classic
	// blob/olive/conifer trio. Sampled once at the tile's center rather than
	// per-point (a single representative biome reads better than a
	// point-by-point dump for a whole tile's worth of instances).
	{
		const EODBiome TileBiome = BiomeAt(
			(Key.X + 0.5) * (double)TileSizeCm, (Key.Y + 0.5) * (double)TileSizeCm);
		UE_LOG(LogTemp, Verbose,
			TEXT("ODBarrierManager: tile (%d,%d) biome=%s windbreak species blob=%d olive=%d "
			     "conifer=%d scrubOak=%d manzanita=%d yucca=%d canyonOak=%d | bush=%d"),
			Key.X, Key.Y, *StaticEnum<EODBiome>()->GetNameStringByValue((int64)TileBiome),
			SpeciesInstanceCounts[0], SpeciesInstanceCounts[1], SpeciesInstanceCounts[2],
			SpeciesInstanceCounts[3], SpeciesInstanceCounts[4], SpeciesInstanceCounts[5],
			SpeciesInstanceCounts[6], BushInstanceCount);
	}

	// 2026-09-06 (AUDIT.md 5.4/rec. 5, direction confirmed): the invisible
	// collision ribbon is GONE. The wall-run polylines still ship in the ODW3
	// sidecar and still parse (no format change), but no collision mesh is
	// built from them any more - the HISM trees/bushes above remain as the
	// visible boundary cue, and AODVehiclePawn::TickOutOfBounds() now owns
	// the guarantee the wall used to provide (sustained off-corridor travel
	// or a void fall teleports the car back to the nearest road point,
	// instead of the car bouncing off collision it cannot see).

	UE_LOG(LogTemp, Verbose,
		TEXT("ODBarrierManager: tile (%d,%d) built %d point(s) "
		     "(%d wall run(s) parsed, collision disabled - OOB reset owns it)"),
		Key.X, Key.Y, Parsed.Points.Num(), Parsed.WallRuns.Num());
}

void AODBarrierManager::ReleaseTile(const FIntPoint& Key)
{
	if (FBarrierTile* Tile = Tiles.Find(Key))
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

void AODBarrierManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Player)
	{
		return;
	}
	const FVector Loc = Player->GetActorLocation();
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

	if (const double Now = GetWorld()->GetTimeSeconds(); Now - LastDiagTime > 5.0)
	{
		LastDiagTime = Now;
		UE_LOG(LogTemp, Display,
			TEXT("ODBarrierManager: tiles=%d pending=%d missing=%d"),
			Tiles.Num(), PendingTiles.Num(), MissingTiles.Num());
	}
}
