#include "ODWallManager.h"

#include "ODBinSidecarUtil.h"
#include "Async/Async.h"
#include "Components/DynamicMeshComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/DynamicMeshOverlay.h"
#include "DynamicMesh/MeshNormals.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInterface.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

using namespace UE::Geometry;

AODWallManager::AODWallManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODWallManager::BeginPlay()
{
	Super::BeginPlay();

	// M_GroundStylized (slope-based grass/rock blend) reused as-is for v1 -
	// a near-vertical wall face reads as "rock" by its own slope test, a
	// free plausible cliff look with no new asset work. Needs real
	// per-vertex normals to shade correctly (see the InitializeOverlayTo
	// PerVertexNormals call in FinalizeTile below) - unlike
	// AODBuildingManager's M_BuildingFacade, which doesn't read normals at
	// all and so never needed that call.
	WallMat = LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_GroundStylized.M_GroundStylized"));
}

void AODWallManager::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	if (PendingTiles.Num() > 0)
	{
		UE_LOG(LogTemp, Display,
			TEXT("ODWallManager: EndPlay with %d tile load(s) still in flight - "
			     "their completions will no-op harmlessly when they land"),
			PendingTiles.Num());
	}
	Super::EndPlay(EndPlayReason);
}

bool AODWallManager::ParseWallTileFile(const FString& Path, FParsedWallTile& Out)
{
	TArray<uint8> Bin;
	if (!FFileHelper::LoadFileToArray(Bin, *Path) || Bin.Num() < 8
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'S' || Bin[3] != '1')
	{
		return false;
	}

	const uint8* Cur = Bin.GetData() + 4;
	const uint8* End = Bin.GetData() + Bin.Num();
	auto Remaining = [&]() -> int64 { return (int64)(End - Cur); };

	if (Remaining() < 4)
	{
		return false;
	}
	const uint32 WallCount = ReadU32LE(Cur);
	Cur += 4;

	Out.Walls.Reserve(WallCount);
	for (uint32 w = 0; w < WallCount; ++w)
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

		FParsedWall& Wall = Out.Walls.AddDefaulted_GetRef();
		Wall.Verts.SetNumUninitialized(VCount * 3);
		for (uint32 v = 0; v < VCount; ++v)
		{
			Wall.Verts[v * 3 + 0] = ReadF32LE(VertsBase + v * 12 + 0);
			Wall.Verts[v * 3 + 1] = ReadF32LE(VertsBase + v * 12 + 4);
			Wall.Verts[v * 3 + 2] = ReadF32LE(VertsBase + v * 12 + 8);
		}
		Wall.Uvs.SetNumUninitialized(VCount * 2);
		for (uint32 v = 0; v < VCount; ++v)
		{
			Wall.Uvs[v * 2 + 0] = ReadF32LE(UvBase + v * 8 + 0);
			Wall.Uvs[v * 2 + 1] = ReadF32LE(UvBase + v * 8 + 4);
		}
		Wall.Tris.SetNumUninitialized(TCount * 3);
		for (uint32 t = 0; t < TCount; ++t)
		{
			Wall.Tris[t * 3 + 0] = ReadU32LE(TrisBase + t * 12 + 0);
			Wall.Tris[t * 3 + 1] = ReadU32LE(TrisBase + t * 12 + 4);
			Wall.Tris[t * 3 + 2] = ReadU32LE(TrisBase + t * 12 + 8);
		}
	}
	return true;
}

void AODWallManager::RequestTileLoad(const FIntPoint& Key)
{
	PendingTiles.Add(Key);
	const FString Path = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ FString::Printf(TEXT("%s_tile_%d_%d.bin"), *WallTilePrefix, Key.X, Key.Y);

	TWeakObjectPtr<AODWallManager> WeakThis(this);
	Async(EAsyncExecution::ThreadPool, [WeakThis, Key, Path]()
	{
		FParsedWallTile Parsed;
		const bool bOk = ParseWallTileFile(Path, Parsed);
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

void AODWallManager::OnTileParsed(const FIntPoint& Key, bool bOk, FParsedWallTile&& Parsed)
{
	PendingTiles.Remove(Key);
	if (!bOk)
	{
		// expected for the vast majority of tiles - most of the map has no
		// switchback conflict at all, same "no hole" semantics as every
		// other tile-streamed system here
		MissingTiles.Add(Key);
		return;
	}
	FinalizeTile(Key, MoveTemp(Parsed));
}

void AODWallManager::FinalizeTile(const FIntPoint& Key, FParsedWallTile&& Parsed)
{
	FWallTile& Tile = Tiles.Add(Key);
	Tile.Walls.Reserve(Parsed.Walls.Num());

	for (const FParsedWall& W : Parsed.Walls)
	{
		const uint32 VCount = W.Verts.Num() / 3;
		const uint32 TCount = W.Tris.Num() / 3;

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
				VertIds[v] = Mesh.AppendVertex(FVector3d(
					W.Verts[v * 3 + 0], W.Verts[v * 3 + 1], W.Verts[v * 3 + 2]));
			}
			for (uint32 v = 0; v < VCount; ++v)
			{
				UvIds[v] = UV->AppendElement(
					FVector2f(W.Uvs[v * 2 + 0], W.Uvs[v * 2 + 1]));
			}
			for (uint32 t = 0; t < TCount; ++t)
			{
				const uint32 I0 = W.Tris[t * 3 + 0];
				const uint32 I1 = W.Tris[t * 3 + 1];
				const uint32 I2 = W.Tris[t * 3 + 2];
				const int32 Tid = Mesh.AppendTriangle(VertIds[I0], VertIds[I1], VertIds[I2]);
				if (Tid >= 0)
				{
					UV->SetTriangle(Tid, FIndex3i(UvIds[I0], UvIds[I1], UvIds[I2]));
				}
			}

			// M_GroundStylized reads per-vertex normals for its slope-based
			// grass/rock blend - AODBuildingManager's FinalizeTile doesn't
			// need this (M_BuildingFacade doesn't read normals at all), so
			// don't drop this call when comparing the two side by side.
			FMeshNormals::InitializeOverlayToPerVertexNormals(Mesh.Attributes()->PrimaryNormals(), false);
		});

		// walls always collide - they exist specifically to fill a gap on
		// the drivable corridor a bare terrain carve couldn't resolve
		Comp->EnableComplexAsSimpleCollision();
		Comp->SetCollisionProfileName(TEXT("BlockAll"));
		if (WallMat)
		{
			Comp->SetMaterial(0, WallMat);
		}

		Tile.Walls.Add(Comp);
	}

	UE_LOG(LogTemp, Display,
		TEXT("ODWallManager: tile (%d,%d) built %d wall chunk(s)"),
		Key.X, Key.Y, Parsed.Walls.Num());
}

void AODWallManager::ReleaseTile(const FIntPoint& Key)
{
	if (FWallTile* Tile = Tiles.Find(Key))
	{
		for (const auto& Comp : Tile->Walls)
		{
			if (Comp)
			{
				Comp->DestroyComponent();
			}
		}
		Tiles.Remove(Key);
	}
}

void AODWallManager::Tick(float DeltaSeconds)
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
		int32 NumWalls = 0;
		for (const auto& Pair : Tiles)
		{
			NumWalls += Pair.Value.Walls.Num();
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODWallManager: tiles=%d pending=%d missing=%d walls=%d"),
			Tiles.Num(), PendingTiles.Num(), MissingTiles.Num(), NumWalls);
	}
}
