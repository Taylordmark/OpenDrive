#include "ODFurnitureManager.h"
#include "ODVisualStyle.h"

#include "ODBinSidecarUtil.h"
#include "ODTileSource.h"
#include "ODDayNightController.h"
#include "Async/Async.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "EngineUtils.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

namespace
{
	/** Per type, in pipeline/build_road_furniture.TYPES order: mesh name
	 *  (/Game/OpenDrive/Furniture/SM_Furn_<Name>, pipeline/
	 *  make_furniture_meshes.py), collision pole height / width (cm, 0 = no
	 *  collision), cull end distance (cm). */
	struct FFurnitureType
	{
		const TCHAR* Name;
		float PoleHeightCm;
		float PoleWidthCm;
		float CullCm;
	};
	const FFurnitureType FurnitureTypes[] = {
		{TEXT("Signal"),        620.0f, 26.0f, 150000.0f},
		{TEXT("Stop"),          250.0f, 10.0f,  80000.0f},
		{TEXT("Yield"),         250.0f, 10.0f,  80000.0f},
		{TEXT("PedCrossing"),   250.0f, 10.0f,  80000.0f},
		{TEXT("RailCrossbuck"), 300.0f, 10.0f,  80000.0f},
		{TEXT("Speed"),         250.0f, 10.0f,  80000.0f},
		{TEXT("ChevronL"),      180.0f, 10.0f,  80000.0f},
		{TEXT("ChevronR"),      180.0f, 10.0f,  80000.0f},
		{TEXT("StreetLight"),   900.0f, 20.0f, 150000.0f},
		{TEXT("Delineator"),      0.0f,  0.0f,  40000.0f},
		{TEXT("StopBar"),         0.0f,  0.0f,  30000.0f},
	};
	constexpr int32 NumFurnitureTypes = UE_ARRAY_COUNT(FurnitureTypes);
	constexpr int32 StreetLightType = 8;
	constexpr int32 FurnitureRecordBytes = 21;   // <B5f
}

AODFurnitureManager::AODFurnitureManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODFurnitureManager::BeginPlay()
{
	Super::BeginPlay();

	int32 NumOk = 0;
	Meshes.SetNum(NumFurnitureTypes);
	for (int32 t = 0; t < NumFurnitureTypes; ++t)
	{
		const FString Path = FString::Printf(TEXT("/Game/OpenDrive/Furniture/SM_Furn_%s.SM_Furn_%s"),
			FurnitureTypes[t].Name, FurnitureTypes[t].Name);
		Meshes[t] = ODVisualStyle::Pick(LoadObject<UStaticMesh>(nullptr, *Path));
		NumOk += Meshes[t] != nullptr;
	}
	BoxMesh = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube"));
	if (UMaterialInterface* Lamp = ODVisualStyle::Pick(LoadObject<UMaterialInterface>(nullptr,
		TEXT("/Game/OpenDrive/Materials/MI_Furn_lamp.MI_Furn_lamp"))))
	{
		LampMid = UMaterialInstanceDynamic::Create(Lamp, this);
		LampMid->SetScalarParameterValue(TEXT("Glow"), 0.0f);
	}
	UE_LOG(LogTemp, Display, TEXT("ODFurnitureManager: %d/%d meshes, BoxMesh=%s LampMid=%s"),
		NumOk, NumFurnitureTypes, BoxMesh ? TEXT("OK") : TEXT("FAILED"), LampMid ? TEXT("OK") : TEXT("FAILED"));
}

bool AODFurnitureManager::ParseFurnitureTileFile(const FString& Path, FParsedFurnitureTile& Out)
{
	TArray<uint8> Bin;
	if (!FFileHelper::LoadFileToArray(Bin, *Path) || Bin.Num() < 4 + GenerationIdBytes + 4
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'F' || Bin[3] != '1')
	{
		return false;
	}
	const uint8* Cur = Bin.GetData() + 4;
	Out.GenerationId = ReadGenerationId(Cur);
	Cur += GenerationIdBytes;
	const uint32 Count = ReadU32LE(Cur); Cur += 4;
	if ((int64)(Bin.GetData() + Bin.Num() - Cur) < (int64)Count * FurnitureRecordBytes)
	{
		return false;
	}
	Out.Props.Reserve(Count);
	for (uint32 i = 0; i < Count; ++i, Cur += FurnitureRecordBytes)
	{
		FParsedFurnitureProp& P = Out.Props.AddDefaulted_GetRef();
		P.Type = Cur[0];
		P.Pos = FVector(ReadF32LE(Cur + 1), ReadF32LE(Cur + 5), ReadF32LE(Cur + 9));
		P.YawDeg = ReadF32LE(Cur + 13);
		P.ScaleY = ReadF32LE(Cur + 17);
	}
	return true;
}

void AODFurnitureManager::RequestTileLoad(const FIntPoint& Key)
{
	PendingTiles.Add(Key);
	const FString Path = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ FString::Printf(TEXT("%s_tile_%d_%d.bin"), *FurnitureTilePrefix, Key.X, Key.Y);

	TWeakObjectPtr<AODFurnitureManager> WeakThis(this);
	Async(EAsyncExecution::ThreadPool, [WeakThis, Key, Path]()
	{
		FParsedFurnitureTile Parsed;
		FString ReadPath;
		const EODTileSource Src = ODTileSource::Resolve(Path, ReadPath);
		const bool bOk = Src == EODTileSource::Ready && ParseFurnitureTileFile(ReadPath, Parsed);
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

void AODFurnitureManager::OnTileParsed(const FIntPoint& Key, bool bOk, FParsedFurnitureTile&& Parsed)
{
	PendingTiles.Remove(Key);
	if (!bOk)
	{
		MissingTiles.Add(Key);   // no roads in this tile
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
			TEXT("ODFurnitureManager: tile (%d,%d) generation id %s does not match %s's first-loaded "
			     "generation id %s - tiles from different pipeline runs may be mixed"),
			Key.X, Key.Y, *Parsed.GenerationId, *FurnitureTilePrefix, *FirstGenerationId);
	}
	FinalizeTile(Key, MoveTemp(Parsed));
}

void AODFurnitureManager::FinalizeTile(const FIntPoint& Key, FParsedFurnitureTile&& Parsed)
{
	TArray<FTransform> Xfs[NumFurnitureTypes];
	TArray<FTransform> Boxes;
	for (const FParsedFurnitureProp& P : Parsed.Props)
	{
		if (P.Type >= NumFurnitureTypes)
		{
			continue;
		}
		const FFurnitureType& T = FurnitureTypes[P.Type];
		const FRotator Rot(0.0f, P.YawDeg, 0.0f);
		Xfs[P.Type].Emplace(Rot, P.Pos, FVector(1.0, P.ScaleY, 1.0));
		if (T.PoleHeightCm > 0.0f)
		{
			// /Engine/BasicShapes/Cube is 100 cm, pivot at its centre
			Boxes.Emplace(Rot, P.Pos + FVector(0.0, 0.0, T.PoleHeightCm * 0.5),
				FVector(T.PoleWidthCm / 100.0, T.PoleWidthCm / 100.0, T.PoleHeightCm / 100.0));
		}
	}

	FFurnitureTile& Tile = Tiles.Add(Key);
	auto MakeHism = [&](UStaticMesh* Mesh, const TArray<FTransform>& Instances) -> UHierarchicalInstancedStaticMeshComponent*
	{
		auto* Hism = NewObject<UHierarchicalInstancedStaticMeshComponent>(this);
		Hism->SetupAttachment(RootComponent);
		Hism->SetStaticMesh(Mesh);
		Hism->SetAffectDynamicIndirectLighting(false);
		Hism->RegisterComponent();
		Hism->AddInstances(Instances, false, /*bWorldSpace*/false);   // relative to the actor (vista copies are offset)
		Tile.Hisms.Add(Hism);
		return Hism;
	};
	for (int32 t = 0; t < NumFurnitureTypes; ++t)
	{
		if (Xfs[t].Num() == 0 || !Meshes[t])
		{
			continue;
		}
		UHierarchicalInstancedStaticMeshComponent* Hism = MakeHism(Meshes[t], Xfs[t]);
		Hism->SetCollisionEnabled(ECollisionEnabled::NoCollision);   // the box HISM below collides
		const float Cull = FurnitureTypes[t].CullCm;
		Hism->SetCullDistances((int32)(Cull * 0.8f), (int32)Cull);
		Hism->SetCastShadow(FurnitureTypes[t].PoleHeightCm > 0.0f);
		if (t == StreetLightType && LampMid)
		{
			const int32 Slot = Meshes[t]->GetMaterialIndex(TEXT("lamp"));
			if (Slot != INDEX_NONE)
			{
				Hism->SetMaterial(Slot, LampMid);
			}
		}
	}
	if (Boxes.Num() > 0 && BoxMesh)
	{
		UHierarchicalInstancedStaticMeshComponent* Hism = MakeHism(BoxMesh, Boxes);
		Hism->SetHiddenInGame(true);
		Hism->SetCastShadow(false);
		Hism->SetCollisionProfileName(TEXT("BlockAll"));
	}
}

void AODFurnitureManager::ReleaseTile(const FIntPoint& Key)
{
	if (FFurnitureTile* Tile = Tiles.Find(Key))
	{
		for (const auto& Hism : Tile->Hisms)
		{
			if (Hism) { Hism->DestroyComponent(); }
		}
		Tiles.Remove(Key);
	}
}

void AODFurnitureManager::UpdateLamps()
{
	if (!DayNight.IsValid())
	{
		for (TActorIterator<AODDayNightController> It(GetWorld()); It; ++It)
		{
			DayNight = *It;
			break;
		}
	}
	const bool bOn = DayNight.IsValid() && DayNight->bHeadlightsOn;
	if (LampMid && bOn != bLampsOn)
	{
		bLampsOn = bOn;
		LampMid->SetScalarParameterValue(TEXT("Glow"), bOn ? LampGlow : 0.0f);
	}
}

void AODFurnitureManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	const double Now = GetWorld()->GetTimeSeconds();
	if (Now - LastLampCheck > 1.0)
	{
		LastLampCheck = Now;
		UpdateLamps();
	}

	FVector Loc;
	if (!Vista.StreamCenter(this, Loc))
	{
		return;
	}
	const FIntPoint Center(FMath::FloorToInt(Loc.X / TileSizeCm), FMath::FloorToInt(Loc.Y / TileSizeCm));
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
		if (FMath::Max(FMath::Abs(Pair.Key.X - Center.X), FMath::Abs(Pair.Key.Y - Center.Y)) > DespawnReach)
		{
			Gone.Add(Pair.Key);
		}
	}
	for (const FIntPoint& Key : Gone)
	{
		ReleaseTile(Key);
	}

	if (Now - LastDiagTime > 5.0)
	{
		LastDiagTime = Now;
		int32 NumProps = 0;
		for (const auto& Pair : Tiles)
		{
			for (const auto& Hism : Pair.Value.Hisms)
			{
				if (Hism && Hism->GetStaticMesh() != BoxMesh) { NumProps += Hism->GetInstanceCount(); }
			}
		}
		UE_LOG(LogTemp, Display, TEXT("ODFurnitureManager: tiles=%d pending=%d missing=%d props=%d lamps=%s"),
			Tiles.Num(), PendingTiles.Num(), MissingTiles.Num(), NumProps, bLampsOn ? TEXT("on") : TEXT("off"));
	}
}
