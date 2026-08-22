#include "ODBarrierManager.h"

#include "ODBinSidecarUtil.h"
#include "Async/Async.h"
#include "Components/DynamicMeshComponent.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/DynamicMeshOverlay.h"
#include "Engine/StaticMesh.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

using namespace UE::Geometry;

namespace
{
// same mesh set/paths as ODForestManager.cpp's TreeMeshPaths
const TCHAR* TreeMeshPaths[] = {
	TEXT("/Game/OpenDrive/Trees/tree_blob.tree_blob"),
	TEXT("/Game/OpenDrive/Trees/tree_olive.tree_olive"),
	TEXT("/Game/OpenDrive/Trees/tree_conifer.tree_conifer"),
};
}

AODBarrierManager::AODBarrierManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODBarrierManager::BeginPlay()
{
	Super::BeginPlay();

	for (const TCHAR* Path : TreeMeshPaths)
	{
		TreeMeshes.Add(LoadObject<UStaticMesh>(nullptr, Path));
	}
	BushMesh = LoadObject<UStaticMesh>(nullptr, TEXT("/Game/OpenDrive/Trees/bush.bush"));
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

	// one HISM per species (3 trees + bush) - visual only, no collision
	// (the wall chunks below do the actual blocking)
	Tile.Hisms.SetNum(4);
	for (int32 m = 0; m < 4; ++m)
	{
		UStaticMesh* Mesh = m == 3 ? BushMesh.Get() : TreeMeshes[m].Get();
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

	for (const FParsedBarrierPoint& Pt : Parsed.Points)
	{
		FRandomStream Rng(Pt.Seed);
		const int32 Species = Rng.RandRange(0, 3);   // 0-2 tree species, 3 = bush
		const float Scale = Rng.FRandRange(0.85f, 1.3f);
		if (UHierarchicalInstancedStaticMeshComponent* Hism = Tile.Hisms[Species])
		{
			const FTransform InstXf(FRotator(0, Rng.FRandRange(0.0f, 360.0f), 0),
				Pt.Pos, FVector(Scale));
			Hism->AddInstance(InstXf);
		}
	}

	// the actual guaranteed collision: a continuous mesh, not discrete
	// per-point objects (see class comment for why the first version of
	// this actor didn't actually work). 2026-07-28: the ribbon is now
	// extruded HERE from the baked station polyline (2 verts + 2 tris per
	// station step) instead of parsing a pre-triangulated mesh off disk -
	// mechanically the same geometry build_wall_chunks() used to do in
	// Python, just moved to load time (see AUDIT.md 5.1/rec. 3).
	Tile.WallRuns.Reserve(Parsed.WallRuns.Num());
	for (const FParsedWallRun& Run : Parsed.WallRuns)
	{
		const int32 SCount = Run.Stations.Num();
		if (SCount < 2)
		{
			continue;
		}

		auto* Comp = NewObject<UDynamicMeshComponent>(this);
		Comp->SetupAttachment(RootComponent);
		Comp->RegisterComponent();

		Comp->EditMesh([&](FDynamicMesh3& Mesh)
		{
			Mesh.EnableAttributes();
			FDynamicMeshUVOverlay* UV = Mesh.Attributes()->PrimaryUV();

			TArray<int32> BotIds, TopIds, BotUvIds, TopUvIds;
			BotIds.SetNumUninitialized(SCount);
			TopIds.SetNumUninitialized(SCount);
			BotUvIds.SetNumUninitialized(SCount);
			TopUvIds.SetNumUninitialized(SCount);
			for (int32 s = 0; s < SCount; ++s)
			{
				const FVector& P = Run.Stations[s];
				BotIds[s] = Mesh.AppendVertex(FVector3d(P.X, P.Y, P.Z));
				TopIds[s] = Mesh.AppendVertex(FVector3d(P.X, P.Y, P.Z + WallHeightCm));
				const float V = s * 2.0f;   // same along-wall UV convention build_wall_chunks() used
				BotUvIds[s] = UV->AppendElement(FVector2f(V, 0.0f));
				TopUvIds[s] = UV->AppendElement(FVector2f(V, 1.0f));
			}
			for (int32 s = 0; s < SCount - 1; ++s)
			{
				const int32 T0 = Mesh.AppendTriangle(BotIds[s], BotIds[s + 1], TopIds[s]);
				if (T0 >= 0)
				{
					UV->SetTriangle(T0, FIndex3i(BotUvIds[s], BotUvIds[s + 1], TopUvIds[s]));
				}
				const int32 T1 = Mesh.AppendTriangle(BotIds[s + 1], TopIds[s + 1], TopIds[s]);
				if (T1 >= 0)
				{
					UV->SetTriangle(T1, FIndex3i(BotUvIds[s + 1], TopUvIds[s + 1], TopUvIds[s]));
				}
			}
		});

		// invisible - the HISM trees/bushes above are the visual "wall of
		// scenery"; this is purely the guaranteed collision surface
		Comp->SetVisibility(false);
		Comp->EnableComplexAsSimpleCollision();
		Comp->SetCollisionProfileName(TEXT("BlockAll"));

		Tile.WallRuns.Add(Comp);
	}

	UE_LOG(LogTemp, Display,
		TEXT("ODBarrierManager: tile (%d,%d) built %d point(s), %d wall run(s)"),
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
		for (const auto& Comp : Tile->WallRuns)
		{
			if (Comp)
			{
				Comp->DestroyComponent();
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
		int32 NumRuns = 0;
		for (const auto& Pair : Tiles)
		{
			NumRuns += Pair.Value.WallRuns.Num();
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODBarrierManager: tiles=%d pending=%d missing=%d wallRuns=%d"),
			Tiles.Num(), PendingTiles.Num(), MissingTiles.Num(), NumRuns);
	}
}
