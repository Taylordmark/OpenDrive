#include "ODBridgeManager.h"
#include "ODVisualStyle.h"

#include "ODBinSidecarUtil.h"
#include "Async/Async.h"
#include "Components/DynamicMeshComponent.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/DynamicMeshOverlay.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

using namespace UE::Geometry;

AODBridgeManager::AODBridgeManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODBridgeManager::BeginPlay()
{
	Super::BeginPlay();

	BridgeMat = ODVisualStyle::Pick(LoadObject<UMaterialInterface>(nullptr,
		TEXT("/Game/OpenDrive/Materials/M_Bridge.M_Bridge")));
	PierMesh = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cylinder.Cylinder"));

	UE_LOG(LogTemp, Display, TEXT("ODBridgeManager: BridgeMat=%s PierMesh=%s"),
		BridgeMat ? TEXT("OK") : TEXT("FAILED"), PierMesh ? TEXT("OK") : TEXT("FAILED"));
}

void AODBridgeManager::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	if (PendingTiles.Num() > 0)
	{
		UE_LOG(LogTemp, Display,
			TEXT("ODBridgeManager: EndPlay with %d tile load(s) still in flight - "
			     "their completions will no-op harmlessly when they land"),
			PendingTiles.Num());
	}
	Super::EndPlay(EndPlayReason);
}

bool AODBridgeManager::ParseBridgeTileFile(const FString& Path, FParsedBridgeTile& Out)
{
	TArray<uint8> Bin;
	if (!FFileHelper::LoadFileToArray(Bin, *Path) || Bin.Num() < 4 + GenerationIdBytes + 4
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'B' || Bin[3] != '1')
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
	const uint32 RunCount = ReadU32LE(Cur); Cur += 4;
	Out.Runs.Reserve(RunCount);
	for (uint32 r = 0; r < RunCount; ++r)
	{
		if (Remaining() < 6)
		{
			return false;
		}
		const uint8 Side01 = Cur[0];
		// Cur[1] is a reserved byte (see export_road_bridge_bin.py's ODB1
		// format doc) - intentionally unread.
		const uint32 SCount = ReadU32LE(Cur + 2);
		Cur += 6;
		const int64 StationBytes = (int64)SCount * 16;
		if (Remaining() < StationBytes)
		{
			return false;
		}
		FParsedBridgeRun& Run = Out.Runs.AddDefaulted_GetRef();
		Run.Side = Side01;
		Run.Stations.SetNumUninitialized(SCount);
		Run.DropCm.SetNumUninitialized(SCount);
		for (uint32 s = 0; s < SCount; ++s, Cur += 16)
		{
			Run.Stations[s] = FVector(ReadF32LE(Cur), ReadF32LE(Cur + 4), ReadF32LE(Cur + 8));
			Run.DropCm[s] = ReadF32LE(Cur + 12);
		}

		if (Remaining() < 4)
		{
			return false;
		}
		const uint32 PCount = ReadU32LE(Cur); Cur += 4;
		const int64 PierBytes = (int64)PCount * 16;
		if (Remaining() < PierBytes)
		{
			return false;
		}
		Run.PierTops.SetNumUninitialized(PCount);
		Run.PierHeightCm.SetNumUninitialized(PCount);
		for (uint32 p = 0; p < PCount; ++p, Cur += 16)
		{
			Run.PierTops[p] = FVector(ReadF32LE(Cur), ReadF32LE(Cur + 4), ReadF32LE(Cur + 8));
			Run.PierHeightCm[p] = ReadF32LE(Cur + 12);
		}
	}
	return true;
}

void AODBridgeManager::RequestTileLoad(const FIntPoint& Key)
{
	PendingTiles.Add(Key);
	const FString Path = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ FString::Printf(TEXT("%s_tile_%d_%d.bin"), *BridgeTilePrefix, Key.X, Key.Y);

	TWeakObjectPtr<AODBridgeManager> WeakThis(this);
	Async(EAsyncExecution::ThreadPool, [WeakThis, Key, Path]()
	{
		FParsedBridgeTile Parsed;
		const bool bOk = ParseBridgeTileFile(Path, Parsed);
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

void AODBridgeManager::OnTileParsed(const FIntPoint& Key, bool bOk, FParsedBridgeTile&& Parsed)
{
	PendingTiles.Remove(Key);
	if (!bOk)
	{
		MissingTiles.Add(Key);   // no bridges here - expected almost everywhere
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
			TEXT("ODBridgeManager: tile (%d,%d) generation id %s does not match "
			     "%s's first-loaded generation id %s - tiles from different pipeline "
			     "runs may be mixed (AUDIT-RELIABILITY.md SS3.1/SS5.1)"),
			Key.X, Key.Y, *Parsed.GenerationId, *BridgeTilePrefix, *FirstGenerationId);
	}

	FinalizeTile(Key, MoveTemp(Parsed));
}

void AODBridgeManager::BuildDeckMesh(UDynamicMeshComponent* Comp, const FParsedBridgeRun& Run) const
{
	const int32 SCount = Run.Stations.Num();
	static constexpr int32 M = 3;   // Top (pavement edge) / BottomOuter (fascia face) / BottomInner (underside flap)
	if (SCount < 2)
	{
		return;
	}

	// Same per-station outward-direction convention as
	// AODEmbankmentManager::BuildSkirtMesh - a parallel-curve tangent from
	// consecutive STATION points, rotated 90deg, flipped by the run's own
	// side sign so "outward" always means "away from the pavement."  The
	// underside flap goes the OPPOSITE way (inward, toward the road) - see
	// header comment on UndersideFlapCm.
	const float SideSign = Run.Side > 0 ? 1.0f : -1.0f;

	TArray<FVector> Outward;
	Outward.SetNumUninitialized(SCount);
	for (int32 s = 0; s < SCount; ++s)
	{
		const int32 s0 = FMath::Max(0, s - 1);
		const int32 s1 = FMath::Min(SCount - 1, s + 1);
		FVector Tangent = Run.Stations[s1] - Run.Stations[s0];
		Tangent.Z = 0.0;
		if (!Tangent.Normalize())
		{
			Tangent = FVector(1, 0, 0);
		}
		const FVector Right(-Tangent.Y, Tangent.X, 0.0);
		Outward[s] = Right * SideSign;
	}

	Comp->EditMesh([&](FDynamicMesh3& Mesh)
	{
		const FVector3d O = Comp->GetRelativeLocation();
		Mesh.EnableAttributes();
		FDynamicMeshUVOverlay* UV = Mesh.Attributes()->PrimaryUV();

		TArray<int32> VertIds, UvIds;
		VertIds.SetNumUninitialized(SCount * M);
		UvIds.SetNumUninitialized(SCount * M);
		for (int32 s = 0; s < SCount; ++s)
		{
			const float U = s * 20.0f;   // along-deck UV, matches every other ribbon's convention

			// Layer 0: Top, seated exactly at the shipped pavement-edge point (deck top).
			// Layer 1: BottomOuter, straight down DeckDepthCm - the visible fascia face.
			// Layer 2: BottomInner, a further UndersideFlapCm INWARD at the same depth -
			//   the underside slab flap (see header comment).
			const FVector Top = Run.Stations[s];
			const FVector BottomOuter = Top - FVector(0, 0, DeckDepthCm);
			const FVector BottomInner = BottomOuter - Outward[s] * UndersideFlapCm;

			const FVector Layers[M] = { Top, BottomOuter, BottomInner };
			const float VCoord[M] = { 0.0f, 0.7f, 1.0f };   // shading hook for the material (darken toward underside)

			for (int32 k = 0; k < M; ++k)
			{
				const int32 Vid = Mesh.AppendVertex(FVector3d(Layers[k].X - O.X, Layers[k].Y - O.Y, Layers[k].Z));
				VertIds[s * M + k] = Vid;
				UvIds[s * M + k] = UV->AppendElement(FVector2f(U, VCoord[k]));
			}
		}

		auto AddQuad = [&](int32 A, int32 B, int32 C, int32 D,
		                    int32 Au, int32 Bu, int32 Cu, int32 Du)
		{
			const int32 T0 = Mesh.AppendTriangle(A, B, C);
			if (T0 >= 0) { UV->SetTriangle(T0, FIndex3i(Au, Bu, Cu)); }
			const int32 T1 = Mesh.AppendTriangle(A, C, D);
			if (T1 >= 0) { UV->SetTriangle(T1, FIndex3i(Au, Cu, Du)); }
		};

		for (int32 s = 0; s < SCount - 1; ++s)
		{
			for (int32 k = 0; k < M - 1; ++k)
			{
				const int32 A = VertIds[s * M + k],       Au = UvIds[s * M + k];
				const int32 B = VertIds[(s + 1) * M + k], Bu = UvIds[(s + 1) * M + k];
				const int32 C = VertIds[(s + 1) * M + k + 1], Cu = UvIds[(s + 1) * M + k + 1];
				const int32 D = VertIds[s * M + k + 1],   Du = UvIds[s * M + k + 1];
				AddQuad(A, B, C, D, Au, Bu, Cu, Du);
			}
		}
	});
}

void AODBridgeManager::FinalizeTile(const FIntPoint& Key, FParsedBridgeTile&& Parsed)
{
	FBridgeTile& Tile = Tiles.Add(Key);

	int32 NumDecks = 0, NumPiers = 0;
	for (const FParsedBridgeRun& Run : Parsed.Runs)
	{
		if (Run.Stations.Num() < 2)
		{
			continue;
		}

		auto* Comp = NewObject<UDynamicMeshComponent>(this);
		Comp->SetupAttachment(RootComponent);
		Comp->SetRelativeLocation(FVector(Run.Stations[0].X, Run.Stations[0].Y, 0.0));   // local origin: see AODRoadManager::CreateChunkMeshComponent (large-world precision)
		Comp->RegisterComponent();
		BuildDeckMesh(Comp, Run);

		if (BridgeMat)
		{
			Comp->SetMaterial(0, BridgeMat);
		}
		// Visual-only, no collision - see class header comment: the road
		// ribbon mesh itself already provides the drivable collision surface
		// at this same XY footprint.
		Comp->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		Tile.DeckRuns.Add(Comp);
		++NumDecks;

		if (Run.PierTops.Num() > 0 && PierMesh)
		{
			auto* Hism = NewObject<UHierarchicalInstancedStaticMeshComponent>(this);
			Hism->SetupAttachment(RootComponent);
			Hism->SetStaticMesh(PierMesh);
			if (BridgeMat)
			{
				Hism->SetMaterial(0, BridgeMat);
			}
			Hism->SetCollisionEnabled(ECollisionEnabled::NoCollision);   // visual only, same reasoning as the deck
			Hism->SetAffectDynamicIndirectLighting(false);
			Hism->RegisterComponent();

			// /Engine/BasicShapes/Cylinder is a unit primitive (radius 50cm,
			// height 100cm, pivot at its own center) - place each instance at
			// the pier's own vertical midpoint (between its shipped TOP point
			// and TOP - HeightCm) and scale Z by HeightCm/100 so the cylinder
			// spans exactly from the deck underside down to terrain.
			for (int32 p = 0; p < Run.PierTops.Num(); ++p)
			{
				const FVector PierTop = Run.PierTops[p];
				const float HeightCm = FMath::Max(1.0f, Run.PierHeightCm[p]);
				const FVector Center = PierTop - FVector(0, 0, HeightCm * 0.5f);
				const FTransform Xf(FRotator::ZeroRotator, Center,
					FVector(PierWidthCm / 100.0f, PierWidthCm / 100.0f, HeightCm / 100.0f));
				Hism->AddInstance(Xf);
				++NumPiers;
			}
			Tile.PierHisms.Add(Hism);
		}
	}

	UE_LOG(LogTemp, Display, TEXT("ODBridgeManager: tile (%d,%d) built %d deck run(s), %d pier(s)"),
		Key.X, Key.Y, NumDecks, NumPiers);
}

void AODBridgeManager::ReleaseTile(const FIntPoint& Key)
{
	if (FBridgeTile* Tile = Tiles.Find(Key))
	{
		for (const auto& Comp : Tile->DeckRuns)
		{
			if (Comp) { Comp->DestroyComponent(); }
		}
		for (const auto& Hism : Tile->PierHisms)
		{
			if (Hism) { Hism->DestroyComponent(); }
		}
		Tiles.Remove(Key);
	}
}

void AODBridgeManager::Tick(float DeltaSeconds)
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
		int32 NumRuns = 0, NumPiers = 0;
		for (const auto& Pair : Tiles)
		{
			NumRuns += Pair.Value.DeckRuns.Num();
			for (const auto& Hism : Pair.Value.PierHisms)
			{
				if (Hism) { NumPiers += Hism->GetInstanceCount(); }
			}
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODBridgeManager: tiles=%d pending=%d missing=%d deckRuns=%d piers=%d"),
			Tiles.Num(), PendingTiles.Num(), MissingTiles.Num(), NumRuns, NumPiers);
	}
}
