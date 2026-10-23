#include "ODWaterManager.h"
#include "ODTickMeter.h"
#include "ODVisualStyle.h"

#include "ODBinSidecarUtil.h"
#include "ODTileSource.h"
#include "Async/Async.h"
#include "Components/AudioComponent.h"
#include "Components/DynamicMeshComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/MeshNormals.h"
#include "DynamicMesh/DynamicMeshOverlay.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInterface.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Sound/SoundBase.h"

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

	// Flowing rivers (2026-10-23): no fallback - without them ribbons/falls
	// keep the lake look and mist renders with the default material.
	RiverMat = ODVisualStyle::Pick(LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_WaterRiver.M_WaterRiver")));
	MistMat = LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_WaterMist.M_WaterMist"));
	if (!RushSound)
	{
		RushSound = LoadObject<USoundBase>(
			nullptr, TEXT("/Game/OpenDrive/Audio/Ambient/SW_RiverRush.SW_RiverRush"));
	}
	if (!RiverMat || !MistMat || !RushSound)
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODWaterManager: missing M_WaterRiver=%d M_WaterMist=%d SW_RiverRush=%d - run "
			     "unreal/apply_water_material.py / import_ambient_audio.py"),
			RiverMat != nullptr, MistMat != nullptr, RushSound != nullptr);
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

	// ODF1 flow trailer (build_water_tiles.py write_tile): optional - an
	// older tile ends here and stays all-lake. A short/bad trailer drops
	// only the flow data, never the tile.
	if (Remaining() >= 4 && Cur[0] == 'O' && Cur[1] == 'D' && Cur[2] == 'F' && Cur[3] == '1')
	{
		Cur += 4;
		TArray<uint8> Kinds;
		TArray<TArray<float>> Flows;
		for (const FParsedLake& Lake : Out.Lakes)
		{
			const int32 VCount = Lake.Verts.Num() / 3;
			if (Remaining() < 1 + (int64)VCount * 8)
			{
				UE_LOG(LogTemp, Warning, TEXT("ODWaterManager: %s has a truncated ODF1 trailer - flow ignored"), *Path);
				return true;
			}
			Kinds.Add(*Cur++);
			TArray<float>& F = Flows.AddDefaulted_GetRef();
			F.SetNumUninitialized(VCount * 2);
			for (int32 i = 0; i < VCount * 2; ++i)
			{
				F[i] = ReadF32LE(Cur + i * 4);
			}
			Cur += (int64)VCount * 8;
		}
		for (int32 l = 0; l < Out.Lakes.Num(); ++l)
		{
			Out.Lakes[l].Kind = Kinds[l];
			Out.Lakes[l].Flow = MoveTemp(Flows[l]);
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
		// UV1 = (speed m/s, whitewater) for M_WaterRiver / (intensity, 0)
		// for M_WaterMist; slot 0 lake+sea, 1 river+waterfall, 2 mist
		Mesh.Attributes()->SetNumUVLayers(2);
		FDynamicMeshUVOverlay* FlowUV = Mesh.Attributes()->GetUVLayer(1);
		Mesh.Attributes()->EnableMaterialID();
		FDynamicMeshMaterialAttribute* MatId = Mesh.Attributes()->GetMaterialID();

		for (const FParsedLake& Lk : InLakes)
		{
			const int32 Slot = (Lk.Kind == 1 || Lk.Kind == 3) ? 1 : Lk.Kind == 4 ? 2 : 0;
			const bool bHasFlow = Lk.Flow.Num() == Lk.Verts.Num() / 3 * 2;
			const uint32 VCount = Lk.Verts.Num() / 3;
			const uint32 TCount = Lk.Tris.Num() / 3;
			TArray<int32> VertIds; VertIds.SetNumUninitialized(VCount);
			TArray<int32> UvIds; UvIds.SetNumUninitialized(VCount);
			TArray<int32> FlowIds; FlowIds.SetNumUninitialized(VCount);

			for (uint32 v = 0; v < VCount; ++v)
			{
				VertIds[v] = Mesh.AppendVertex(FVector3d(
					Lk.Verts[v * 3 + 0] - Origin.X, Lk.Verts[v * 3 + 1] - Origin.Y, Lk.Verts[v * 3 + 2]));
				UvIds[v] = UV->AppendElement(
					FVector2f(Lk.Uvs[v * 2 + 0], Lk.Uvs[v * 2 + 1]));
				FlowIds[v] = FlowUV->AppendElement(bHasFlow
					? FVector2f(Lk.Flow[v * 2 + 0], Lk.Flow[v * 2 + 1]) : FVector2f::ZeroVector);
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
					FlowUV->SetTriangle(Tid, FIndex3i(FlowIds[I0], FlowIds[I1], FlowIds[I2]));
					MatId->SetValue(Tid, Slot);
				}
			}
		}
		// No normal overlay = every vert renders with a default (0,1,0) normal:
		// lit by sun azimuth, and headlights cut off along the lamp plane.
		FMeshNormals::InitializeOverlayToPerVertexNormals(Mesh.Attributes()->PrimaryNormals(), false);
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
	Comp->SetMaterial(1, RiverMat ? RiverMat.Get() : WaterMID.Get());
	Comp->SetMaterial(2, MistMat);

	Tile.Lakes.Add(Comp);
}

void AODWaterManager::CreateRushSounds(FWaterTile& Tile, const TArray<FParsedLake>& InLakes)
{
	if (!RushSound)
	{
		return;
	}
	struct FEmitter { FVector Loc; float Scale; };
	TArray<FEmitter> Emitters;
	auto Centroid = [](const FParsedLake& Lk)
	{
		FVector C = FVector::ZeroVector;
		const int32 N = Lk.Verts.Num() / 3;
		for (int32 v = 0; v < N; ++v)
		{
			C += FVector(Lk.Verts[v * 3 + 0], Lk.Verts[v * 3 + 1], Lk.Verts[v * 3 + 2]);
		}
		return C / FMath::Max(N, 1);
	};
	// falls first (their mist), so the spacing rule thins rapids around them
	for (const FParsedLake& Lk : InLakes)
	{
		if (Lk.Kind == 4 && Lk.Flow.Num() >= 2)
		{
			Emitters.Add({ Centroid(Lk), 1.0f + Lk.Flow[0] });
		}
	}
	for (const FParsedLake& Lk : InLakes)
	{
		if (Lk.Kind != 1 || Lk.Flow.Num() < 2 || Emitters.Num() >= MaxRushSoundsPerTile)
		{
			continue;
		}
		float White = 0.0f;
		for (int32 v = 1; v < Lk.Flow.Num(); v += 2)
		{
			White += Lk.Flow[v];
		}
		White /= Lk.Flow.Num() / 2;
		const FVector C = Centroid(Lk);
		if (White >= RapidsWhiteMin && !Emitters.ContainsByPredicate([&](const FEmitter& E)
			{ return FVector::DistSquared(E.Loc, C) < FMath::Square(RushEmitterSpacingCm); }))
		{
			Emitters.Add({ C, White });
		}
	}
	for (int32 i = 0; i < FMath::Min(Emitters.Num(), MaxRushSoundsPerTile); ++i)
	{
		const FEmitter& E = Emitters[i];
		auto* Audio = NewObject<UAudioComponent>(this);
		Audio->SetupAttachment(RootComponent);
		Audio->SetWorldLocation(E.Loc);
		Audio->SetSound(RushSound);
		Audio->bAutoActivate = false;
		Audio->bOverrideAttenuation = true;
		Audio->AttenuationOverrides.bAttenuate = true;
		Audio->AttenuationOverrides.bSpatialize = true;
		Audio->AttenuationOverrides.AttenuationShape = EAttenuationShape::Sphere;
		Audio->AttenuationOverrides.AttenuationShapeExtents = FVector(RushInnerRadiusCm * FMath::Min(E.Scale, 2.0f), 0, 0);
		Audio->AttenuationOverrides.FalloffDistance = RushFalloffCm * FMath::Min(E.Scale, 2.0f);
		Audio->SetVolumeMultiplier(FMath::Clamp(0.5f + 0.4f * E.Scale, 0.5f, 1.3f));
		// bigger falls rumble lower; emitters de-phased so neighbours don't comb
		Audio->SetPitchMultiplier(FMath::Clamp(1.1f - 0.12f * E.Scale, 0.8f, 1.05f));
		Audio->RegisterComponent();
		Audio->Play(FMath::FRandRange(0.0f, 20.0f));
		Tile.Sounds.Add(Audio);
	}
}

void AODWaterManager::FinalizeTile(const FIntPoint& Key, FParsedWaterTile&& Parsed)
{
	FWaterTile& Tile = Tiles.Add(Key);
	Tile.NumLakes = Parsed.Lakes.Num();
	CreateLakeComponent(Tile, Parsed.Lakes);
	CreateRushSounds(Tile, Parsed.Lakes);

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
		for (const auto& Audio : Tile->Sounds)
		{
			if (Audio)
			{
				Audio->DestroyComponent();
			}
		}
		Tiles.Remove(Key);
	}
}

void AODWaterManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	static FODTickMeter Meter(TEXT("ODWaterManager"));   // see ODTickMeter.h
	FODTickMeter::FScope MeterScope(Meter);

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

	bSettled = Dispatched == 0 && PendingTiles.IsEmpty();

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
