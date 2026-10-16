#include "ODEmbankmentManager.h"
#include "ODVisualStyle.h"

#include "ODBinSidecarUtil.h"
#include "ODTileSource.h"
#include "Async/Async.h"
#include "Components/DynamicMeshComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/DynamicMeshOverlay.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

using namespace UE::Geometry;

AODEmbankmentManager::AODEmbankmentManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODEmbankmentManager::BeginPlay()
{
	Super::BeginPlay();

	EmbankMat = ODVisualStyle::Pick(LoadObject<UMaterialInterface>(nullptr,
		TEXT("/Game/OpenDrive/Materials/M_Embankment.M_Embankment")));

	UE_LOG(LogTemp, Display, TEXT("ODEmbankmentManager: EmbankMat=%s"),
		EmbankMat ? TEXT("OK") : TEXT("FAILED"));
}

void AODEmbankmentManager::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	if (PendingTiles.Num() > 0)
	{
		UE_LOG(LogTemp, Display,
			TEXT("ODEmbankmentManager: EndPlay with %d tile load(s) still in flight - "
			     "their completions will no-op harmlessly when they land"),
			PendingTiles.Num());
	}
	Super::EndPlay(EndPlayReason);
}

bool AODEmbankmentManager::ParseEmbankmentTileFile(const FString& Path, FParsedEmbankmentTile& Out)
{
	TArray<uint8> Bin;
	if (!FFileHelper::LoadFileToArray(Bin, *Path) || Bin.Num() < 4 + GenerationIdBytes + 4
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'E' || Bin[3] != '1')
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
		// Cur[1] is a reserved byte (see export_road_embankment_bin.py's ODE1
		// format doc) - intentionally unread.
		const uint32 SCount = ReadU32LE(Cur + 2);
		Cur += 6;
		const int64 NeedBytes = (int64)SCount * 16;
		if (Remaining() < NeedBytes)
		{
			return false;
		}
		FParsedEmbankmentRun& Run = Out.Runs.AddDefaulted_GetRef();
		Run.Side = Side01;
		Run.Stations.SetNumUninitialized(SCount);
		Run.DropCm.SetNumUninitialized(SCount);
		for (uint32 s = 0; s < SCount; ++s, Cur += 16)
		{
			Run.Stations[s] = FVector(ReadF32LE(Cur), ReadF32LE(Cur + 4), ReadF32LE(Cur + 8));
			Run.DropCm[s] = ReadF32LE(Cur + 12);
		}
	}
	return true;
}

void AODEmbankmentManager::RequestTileLoad(const FIntPoint& Key)
{
	PendingTiles.Add(Key);
	const FString Path = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ FString::Printf(TEXT("%s_tile_%d_%d.bin"), *EmbankmentTilePrefix, Key.X, Key.Y);

	TWeakObjectPtr<AODEmbankmentManager> WeakThis(this);
	Async(EAsyncExecution::ThreadPool, [WeakThis, Key, Path]()
	{
		FParsedEmbankmentTile Parsed;
		FString ReadPath;
		const EODTileSource Src = ODTileSource::Resolve(Path, ReadPath);
		const bool bOk = Src == EODTileSource::Ready && ParseEmbankmentTileFile(ReadPath, Parsed);
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

void AODEmbankmentManager::OnTileParsed(const FIntPoint& Key, bool bOk, FParsedEmbankmentTile&& Parsed)
{
	PendingTiles.Remove(Key);
	if (!bOk)
	{
		MissingTiles.Add(Key);   // no embankments here - expected almost everywhere
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
			TEXT("ODEmbankmentManager: tile (%d,%d) generation id %s does not match "
			     "%s's first-loaded generation id %s - tiles from different pipeline "
			     "runs may be mixed (AUDIT-RELIABILITY.md SS3.1/SS5.1)"),
			Key.X, Key.Y, *Parsed.GenerationId, *EmbankmentTilePrefix, *FirstGenerationId);
	}

	FinalizeTile(Key, MoveTemp(Parsed));
}

void AODEmbankmentManager::BuildSkirtMesh(UDynamicMeshComponent* Comp, const FParsedEmbankmentRun& Run) const
{
	const int32 SCount = Run.Stations.Num();
	static constexpr int32 M = 3;   // top (pavement edge) / toe / apron
	if (SCount < 2)
	{
		return;
	}

	// Same per-station outward-direction convention as
	// AODGuardrailManager::BuildRailMesh - a parallel-curve tangent from
	// consecutive STATION points (already offset to the pavement edge by
	// the pipeline), rotated 90deg, flipped by the run's own side sign so
	// "outward" always means "away from the pavement" regardless of which
	// side of the road this run is on.
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
			const float U = s * 20.0f;   // along-skirt UV, matches the guardrail ribbon's convention
			const float DropCm = FMath::Max(0.0f, Run.DropCm[s]);
			const float HorizRunCm = DropCm * SlopeRatio;

			// Layer 0: top, seated exactly at the shipped pavement-edge point.
			// Layer 1: toe, HorizRunCm further out and DropCm lower - the
			//   idealized straight 1.5:1 slope's own contact point with terrain.
			// Layer 2: apron, ApronOverlapCm further out again and ApronEmbedCm
			//   lower still - overlaps into the terrain mesh so no hairline gap
			//   shows if the real (carved-heightmap) terrain doesn't land
			//   exactly on the idealized slope's toe height (see header comment).
			const FVector Top = Run.Stations[s];
			const FVector Toe = Top + Outward[s] * HorizRunCm - FVector(0, 0, DropCm);
			const FVector Apron = Toe + Outward[s] * ApronOverlapCm - FVector(0, 0, ApronEmbedCm);

			const FVector Layers[M] = { Top, Toe, Apron };
			const float VCoord[M] = { 0.0f, 0.6f, 1.0f };   // toe-darkening hook for the material

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

void AODEmbankmentManager::FinalizeTile(const FIntPoint& Key, FParsedEmbankmentTile&& Parsed)
{
	FEmbankmentTile& Tile = Tiles.Add(Key);

	int32 NumSkirts = 0;
	for (const FParsedEmbankmentRun& Run : Parsed.Runs)
	{
		if (Run.Stations.Num() < 2)
		{
			continue;
		}

		auto* Comp = NewObject<UDynamicMeshComponent>(this);
		Comp->SetMeshDrawPath(EDynamicMeshDrawPath::StaticDraw);   // built once: cached draw commands, not re-gathered every frame per view/shadow view
		Comp->SetupAttachment(RootComponent);
		Comp->SetRelativeLocation(FVector(Run.Stations[0].X, Run.Stations[0].Y, 0.0));   // local origin: see AODRoadManager::CreateChunkMeshComponent (large-world precision)
		Comp->RegisterComponent();
		BuildSkirtMesh(Comp, Run);

		if (EmbankMat)
		{
			Comp->SetMaterial(0, EmbankMat);
		}
		// Visual-only, no collision - see class header comment: the
		// guardrail ribbon (AODGuardrailManager) already provides real
		// containment at the same physical drop this skirt only dresses up.
		Comp->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		++NumSkirts;
		Tile.SkirtRuns.Add(Comp);
	}

	UE_LOG(LogTemp, Display, TEXT("ODEmbankmentManager: tile (%d,%d) built %d skirt run(s)"),
		Key.X, Key.Y, NumSkirts);
}

void AODEmbankmentManager::ReleaseTile(const FIntPoint& Key)
{
	if (FEmbankmentTile* Tile = Tiles.Find(Key))
	{
		for (const auto& Comp : Tile->SkirtRuns)
		{
			if (Comp) { Comp->DestroyComponent(); }
		}
		Tiles.Remove(Key);
	}
}

void AODEmbankmentManager::Tick(float DeltaSeconds)
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
			NumRuns += Pair.Value.SkirtRuns.Num();
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODEmbankmentManager: tiles=%d pending=%d missing=%d skirtRuns=%d"),
			Tiles.Num(), PendingTiles.Num(), MissingTiles.Num(), NumRuns);
	}
}
