#include "ODWaterManager.h"
#include "ODVisualStyle.h"

#include "ODBinSidecarUtil.h"
#include "ODTileSource.h"
#include "Async/Async.h"
#include "Components/DynamicMeshComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/DynamicMeshOverlay.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInterface.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

using namespace UE::Geometry;

AODWaterManager::AODWaterManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODWaterManager::BeginPlay()
{
	Super::BeginPlay();

	WaterMat = ODVisualStyle::Pick(LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_Water.M_Water")));
	if (!WaterMat)
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODWaterManager: could not load /Game/OpenDrive/Materials/M_Water - "
			     "lakes will render with the engine default material until "
			     "unreal/apply_water_material.py has been run"));
	}
	else
	{
		// Seasons feature (2026-09-07): one shared MID every lake renders
		// with instead of the raw asset - AODSeasonController drives
		// FreezeBlend on this directly. See AODTerrainManager::TerrainMID's
		// header comment for the identical reasoning (one instance for every
		// resident component, not one per lake).
		WaterMID = UMaterialInstanceDynamic::Create(WaterMat, this);
	}
}

void AODWaterManager::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	if (PendingTiles.Num() > 0)
	{
		UE_LOG(LogTemp, Display,
			TEXT("ODWaterManager: EndPlay with %d tile load(s) still in flight - "
			     "their completions will no-op harmlessly when they land"),
			PendingTiles.Num());
	}
	Super::EndPlay(EndPlayReason);
}

bool AODWaterManager::ParseWaterTileFile(const FString& Path, FParsedWaterTile& Out)
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
	const uint32 LakeCount = ReadU32LE(Cur);
	Cur += 4;

	Out.Lakes.Reserve(LakeCount);
	for (uint32 l = 0; l < LakeCount; ++l)
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

		FParsedLake& Lake = Out.Lakes.AddDefaulted_GetRef();
		Lake.Verts.SetNumUninitialized(VCount * 3);
		for (uint32 v = 0; v < VCount; ++v)
		{
			Lake.Verts[v * 3 + 0] = ReadF32LE(VertsBase + v * 12 + 0);
			Lake.Verts[v * 3 + 1] = ReadF32LE(VertsBase + v * 12 + 4);
			Lake.Verts[v * 3 + 2] = ReadF32LE(VertsBase + v * 12 + 8);
		}
		Lake.Uvs.SetNumUninitialized(VCount * 2);
		for (uint32 v = 0; v < VCount; ++v)
		{
			Lake.Uvs[v * 2 + 0] = ReadF32LE(UvBase + v * 8 + 0);
			Lake.Uvs[v * 2 + 1] = ReadF32LE(UvBase + v * 8 + 4);
		}
		Lake.Tris.SetNumUninitialized(TCount * 3);
		for (uint32 t = 0; t < TCount; ++t)
		{
			Lake.Tris[t * 3 + 0] = ReadU32LE(TrisBase + t * 12 + 0);
			Lake.Tris[t * 3 + 1] = ReadU32LE(TrisBase + t * 12 + 4);
			Lake.Tris[t * 3 + 2] = ReadU32LE(TrisBase + t * 12 + 8);
		}
	}
	return true;
}

void AODWaterManager::RequestTileLoad(const FIntPoint& Key)
{
	PendingTiles.Add(Key);
	const FString Path = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ FString::Printf(TEXT("%s_tile_%d_%d.bin"), *WaterTilePrefix, Key.X, Key.Y);

	TWeakObjectPtr<AODWaterManager> WeakThis(this);
	Async(EAsyncExecution::ThreadPool, [WeakThis, Key, Path]()
	{
		FParsedWaterTile Parsed;
		FString ReadPath;
		const EODTileSource Src = ODTileSource::Resolve(Path, ReadPath);
		const bool bOk = Src == EODTileSource::Ready && ParseWaterTileFile(ReadPath, Parsed);
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

void AODWaterManager::OnTileParsed(const FIntPoint& Key, bool bOk, FParsedWaterTile&& Parsed)
{
	PendingTiles.Remove(Key);
	if (!bOk)
	{
		// expected for the vast majority of tiles - most of the map has no
		// water at all, same "no hole, just a missing file" semantics every
		// other tile-streamed system here relies on
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
			TEXT("ODWaterManager: tile (%d,%d) generation id %s does not match "
			     "%s's first-loaded generation id %s - tiles from different pipeline "
			     "runs may be mixed (AUDIT-RELIABILITY.md SS3.1/SS5.1)"),
			Key.X, Key.Y, *Parsed.GenerationId, *WaterTilePrefix, *FirstGenerationId);
	}

	FinalizeTile(Key, MoveTemp(Parsed));
}

void AODWaterManager::CreateLakeComponent(FWaterTile& Tile, const TArray<FParsedLake>& InLakes)
{
	const FParsedLake* First = InLakes.FindByPredicate([](const FParsedLake& Lk) { return Lk.Verts.Num() >= 3; });
	if (!First)
	{
		return;
	}

	auto* Comp = NewObject<UDynamicMeshComponent>(this);
	Comp->SetupAttachment(RootComponent);
	const FVector3d Origin(First->Verts[0], First->Verts[1], 0.0);
	Comp->SetRelativeLocation(Origin);   // local origin: see AODRoadManager::CreateChunkMeshComponent (large-world precision)
	// a flat lid over the lake bed: its shadow only lands under itself
	Comp->SetCastShadow(false);
	Comp->RegisterComponent();
	// No relative offset needed - unlike AODBuildingManager, FParsedLake's
	// verts already carry their final baked world Z (pipeline-time DEM
	// percentile, see build_water_tiles.py); there is no runtime ground
	// trace to resolve here at all.

	Comp->EditMesh([&](FDynamicMesh3& Mesh)
	{
		Mesh.EnableAttributes();
		FDynamicMeshUVOverlay* UV = Mesh.Attributes()->PrimaryUV();

		for (const FParsedLake& Lk : InLakes)
		{
			const uint32 VCount = Lk.Verts.Num() / 3;
			const uint32 TCount = Lk.Tris.Num() / 3;
			TArray<int32> VertIds; VertIds.SetNumUninitialized(VCount);
			TArray<int32> UvIds; UvIds.SetNumUninitialized(VCount);

			for (uint32 v = 0; v < VCount; ++v)
			{
				VertIds[v] = Mesh.AppendVertex(FVector3d(
					Lk.Verts[v * 3 + 0] - Origin.X, Lk.Verts[v * 3 + 1] - Origin.Y, Lk.Verts[v * 3 + 2]));
				UvIds[v] = UV->AppendElement(
					FVector2f(Lk.Uvs[v * 2 + 0], Lk.Uvs[v * 2 + 1]));
			}
			for (uint32 t = 0; t < TCount; ++t)
			{
				const uint32 I0 = Lk.Tris[t * 3 + 0];
				const uint32 I1 = Lk.Tris[t * 3 + 1];
				const uint32 I2 = Lk.Tris[t * 3 + 2];
				const int32 Tid = Mesh.AppendTriangle(VertIds[I0], VertIds[I1], VertIds[I2]);
				if (Tid >= 0)
				{
					UV->SetTriangle(Tid, FIndex3i(UvIds[I0], UvIds[I1], UvIds[I2]));
				}
			}
		}
	});

	// NO collision (task scope, 2026-08-27): driving INTO a lake is a future
	// feature, not this pass - water is visual-only. Explicitly disabled
	// rather than left at the DynamicMeshComponent default so this reads as
	// a deliberate choice, not an oversight, if the default ever changes.
	Comp->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	if (WaterMID)
	{
		Comp->SetMaterial(0, WaterMID);
	}

	Tile.Lakes.Add(Comp);
}

void AODWaterManager::FinalizeTile(const FIntPoint& Key, FParsedWaterTile&& Parsed)
{
	FWaterTile& Tile = Tiles.Add(Key);
	Tile.NumLakes = Parsed.Lakes.Num();
	CreateLakeComponent(Tile, Parsed.Lakes);

	// Per-tile surface-Z log (verification evidence for the headless bake
	// check - "confirm the three named lakes produce meshes at plausible
	// elevations") - one line per lake, not just a tile summary, since a
	// tile can hold several unrelated lakes at different elevations.
	for (const FParsedLake& Lk : Parsed.Lakes)
	{
		const float SurfaceZCm = Lk.Verts.Num() >= 3 ? Lk.Verts[2] : 0.0f;
		UE_LOG(LogTemp, Verbose,
			TEXT("ODWaterManager: tile (%d,%d) lake verts=%d tris=%d surfaceZ=%.1f cm"),
			Key.X, Key.Y, Lk.Verts.Num() / 3, Lk.Tris.Num() / 3, SurfaceZCm);
	}

	UE_LOG(LogTemp, Verbose,
		TEXT("ODWaterManager: tile (%d,%d) built %d lake(s)"),
		Key.X, Key.Y, Parsed.Lakes.Num());
}

void AODWaterManager::ReleaseTile(const FIntPoint& Key)
{
	if (FWaterTile* Tile = Tiles.Find(Key))
	{
		for (const auto& Comp : Tile->Lakes)
		{
			if (Comp)
			{
				Comp->DestroyComponent();
			}
		}
		Tiles.Remove(Key);
	}
}

void AODWaterManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	FVector Loc;
	if (!Vista.StreamCenter(this, Loc))
	{
		return;
	}

	// No ground-snap gate here (unlike AODBuildingManager/AODForestManager) -
	// lake placement never depends on the player's own settled height or on
	// AODTerrainManager's streamed collision, so there's nothing to wait on;
	// water tiles can start streaming from the very first Tick().
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
				if (Tiles.Contains(Key) || PendingTiles.Contains(Key) || MissingTiles.Contains(Key)
					|| !Vista.WantsTile(Key, TileSizeCm))
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
		int32 NumLakes = 0;
		for (const auto& Pair : Tiles)
		{
			NumLakes += Pair.Value.NumLakes;
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODWaterManager: tiles=%d pending=%d missing=%d lakes=%d"),
			Tiles.Num(), PendingTiles.Num(), MissingTiles.Num(), NumLakes);
	}
}
