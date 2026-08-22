#include "ODBuildingManager.h"

#include "ODBinSidecarUtil.h"
#include "Async/Async.h"
#include "Components/DynamicMeshComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/DynamicMeshOverlay.h"
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

void AODBuildingManager::FinalizeTile(const FIntPoint& Key, FParsedBuildingTile&& Parsed)
{
	FBuildingTile& Tile = Tiles.Add(Key);
	Tile.Buildings.Reserve(Parsed.Buildings.Num());

	for (const FParsedBuilding& B : Parsed.Buildings)
	{
		const uint32 VCount = B.Verts.Num() / 3;
		const uint32 TCount = B.Tris.Num() / 3;

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

	UE_LOG(LogTemp, Display,
		TEXT("ODBuildingManager: tile (%d,%d) built %d building(s)"),
		Key.X, Key.Y, Parsed.Buildings.Num());
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

void AODBuildingManager::Tick(float DeltaSeconds)
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
		int32 NumBuildings = 0;
		for (const auto& Pair : Tiles)
		{
			NumBuildings += Pair.Value.Buildings.Num();
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODBuildingManager: tiles=%d pending=%d missing=%d buildings=%d"),
			Tiles.Num(), PendingTiles.Num(), MissingTiles.Num(), NumBuildings);
	}
}
