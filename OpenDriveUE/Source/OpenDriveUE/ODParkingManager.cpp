#include "ODParkingManager.h"

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

AODParkingManager::AODParkingManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODParkingManager::BeginPlay()
{
	Super::BeginPlay();

	ParkingMat = LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_Parking.M_Parking"));
	if (!ParkingMat)
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODParkingManager: could not load /Game/OpenDrive/Materials/M_Parking - "
			     "lots will render with the engine default material until "
			     "unreal/apply_parking_material.py has been run"));
	}
}

void AODParkingManager::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	if (PendingTiles.Num() > 0)
	{
		UE_LOG(LogTemp, Display,
			TEXT("ODParkingManager: EndPlay with %d tile load(s) still in flight - "
			     "their completions will no-op harmlessly when they land"),
			PendingTiles.Num());
	}
	Super::EndPlay(EndPlayReason);
}

bool AODParkingManager::ParseParkingTileFile(const FString& Path, FParsedParkingTile& Out)
{
	TArray<uint8> Bin;
	if (!FFileHelper::LoadFileToArray(Bin, *Path) || Bin.Num() < 4 + GenerationIdBytes + 4
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'L' || Bin[3] != '1')
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
	const uint32 LotCount = ReadU32LE(Cur);
	Cur += 4;

	Out.Lots.Reserve(LotCount);
	for (uint32 l = 0; l < LotCount; ++l)
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

		FParsedParkingLot& Lot = Out.Lots.AddDefaulted_GetRef();
		Lot.Verts.SetNumUninitialized(VCount * 3);
		for (uint32 v = 0; v < VCount; ++v)
		{
			Lot.Verts[v * 3 + 0] = ReadF32LE(VertsBase + v * 12 + 0);
			Lot.Verts[v * 3 + 1] = ReadF32LE(VertsBase + v * 12 + 4);
			Lot.Verts[v * 3 + 2] = ReadF32LE(VertsBase + v * 12 + 8);
		}
		Lot.Uvs.SetNumUninitialized(VCount * 2);
		for (uint32 v = 0; v < VCount; ++v)
		{
			Lot.Uvs[v * 2 + 0] = ReadF32LE(UvBase + v * 8 + 0);
			Lot.Uvs[v * 2 + 1] = ReadF32LE(UvBase + v * 8 + 4);
		}
		Lot.Tris.SetNumUninitialized(TCount * 3);
		for (uint32 t = 0; t < TCount; ++t)
		{
			Lot.Tris[t * 3 + 0] = ReadU32LE(TrisBase + t * 12 + 0);
			Lot.Tris[t * 3 + 1] = ReadU32LE(TrisBase + t * 12 + 4);
			Lot.Tris[t * 3 + 2] = ReadU32LE(TrisBase + t * 12 + 8);
		}
	}
	return true;
}

void AODParkingManager::RequestTileLoad(const FIntPoint& Key)
{
	PendingTiles.Add(Key);
	const FString Path = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ FString::Printf(TEXT("%s_tile_%d_%d.bin"), *ParkingTilePrefix, Key.X, Key.Y);

	TWeakObjectPtr<AODParkingManager> WeakThis(this);
	Async(EAsyncExecution::ThreadPool, [WeakThis, Key, Path]()
	{
		FParsedParkingTile Parsed;
		const bool bOk = ParseParkingTileFile(Path, Parsed);
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

void AODParkingManager::OnTileParsed(const FIntPoint& Key, bool bOk, FParsedParkingTile&& Parsed)
{
	PendingTiles.Remove(Key);
	if (!bOk)
	{
		// expected for most tiles - most of the map has no parking lot at all
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
			TEXT("ODParkingManager: tile (%d,%d) generation id %s does not match "
			     "%s's first-loaded generation id %s - tiles from different pipeline "
			     "runs may be mixed (AUDIT-RELIABILITY.md SS3.1/SS5.1)"),
			Key.X, Key.Y, *Parsed.GenerationId, *ParkingTilePrefix, *FirstGenerationId);
	}

	FinalizeTile(Key, MoveTemp(Parsed));
}

void AODParkingManager::CreateLotComponent(FParkingTile& Tile, const FParsedParkingLot& Lot)
{
	const uint32 VCount = Lot.Verts.Num() / 3;
	const uint32 TCount = Lot.Tris.Num() / 3;

	auto* Comp = NewObject<UDynamicMeshComponent>(this);
	Comp->SetupAttachment(RootComponent);
	const FVector3d Origin = VCount ? FVector3d(Lot.Verts[0], Lot.Verts[1], 0.0) : FVector3d::ZeroVector;
	Comp->SetRelativeLocation(Origin);   // local origin: see AODRoadManager::CreateChunkMeshComponent (large-world precision)
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
				Lot.Verts[v * 3 + 0] - Origin.X, Lot.Verts[v * 3 + 1] - Origin.Y, Lot.Verts[v * 3 + 2]));
		}
		for (uint32 v = 0; v < VCount; ++v)
		{
			UvIds[v] = UV->AppendElement(
				FVector2f(Lot.Uvs[v * 2 + 0], Lot.Uvs[v * 2 + 1]));
		}
		for (uint32 t = 0; t < TCount; ++t)
		{
			const uint32 I0 = Lot.Tris[t * 3 + 0];
			const uint32 I1 = Lot.Tris[t * 3 + 1];
			const uint32 I2 = Lot.Tris[t * 3 + 2];
			const int32 Tid = Mesh.AppendTriangle(VertIds[I0], VertIds[I1], VertIds[I2]);
			if (Tid >= 0)
			{
				UV->SetTriangle(Tid, FIndex3i(UvIds[I0], UvIds[I1], UvIds[I2]));
			}
		}
	});

	// NO collision (see class comment) - purely a cosmetic overlay on top of
	// terrain's own driving collision.
	Comp->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	if (ParkingMat)
	{
		Comp->SetMaterial(0, ParkingMat);
	}

	Tile.Lots.Add(Comp);
}

void AODParkingManager::FinalizeTile(const FIntPoint& Key, FParsedParkingTile&& Parsed)
{
	FParkingTile& Tile = Tiles.Add(Key);
	Tile.Lots.Reserve(Parsed.Lots.Num());

	for (const FParsedParkingLot& Lot : Parsed.Lots)
	{
		CreateLotComponent(Tile, Lot);
	}

	UE_LOG(LogTemp, Display,
		TEXT("ODParkingManager: tile (%d,%d) built %d parking lot(s)"),
		Key.X, Key.Y, Parsed.Lots.Num());
}

void AODParkingManager::ReleaseTile(const FIntPoint& Key)
{
	if (FParkingTile* Tile = Tiles.Find(Key))
	{
		for (const auto& Comp : Tile->Lots)
		{
			if (Comp)
			{
				Comp->DestroyComponent();
			}
		}
		Tiles.Remove(Key);
	}
}

void AODParkingManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Player)
	{
		return;
	}

	// No ground-snap gate here (same reasoning as AODWaterManager) - a lot's
	// placement Z is a pure pipeline-time bake, nothing to wait on.
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
		int32 NumLots = 0;
		for (const auto& Pair : Tiles)
		{
			NumLots += Pair.Value.Lots.Num();
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODParkingManager: tiles=%d pending=%d missing=%d lots=%d"),
			Tiles.Num(), PendingTiles.Num(), MissingTiles.Num(), NumLots);
	}
}
