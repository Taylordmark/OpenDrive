#include "ODGuardrailManager.h"
#include "ODVisualStyle.h"

#include "ODBinSidecarUtil.h"
#include "ODTileSource.h"
#include "Async/Async.h"
#include "Components/DynamicMeshComponent.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/DynamicMeshOverlay.h"
#include "DynamicMesh/MeshNormals.h"
#include "Engine/StaticMesh.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "PhysicalMaterials/PhysicalMaterial.h"

using namespace UE::Geometry;

AODGuardrailManager::AODGuardrailManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODGuardrailManager::BeginPlay()
{
	Super::BeginPlay();

	WBeamMat = ODVisualStyle::Pick(LoadObject<UMaterialInterface>(nullptr,
		TEXT("/Game/OpenDrive/Materials/M_Guardrail_WBeam.M_Guardrail_WBeam")));
	JerseyMat = ODVisualStyle::Pick(LoadObject<UMaterialInterface>(nullptr,
		TEXT("/Game/OpenDrive/Materials/M_Guardrail_Jersey.M_Guardrail_Jersey")));
	SlickPhysMat = LoadObject<UPhysicalMaterial>(nullptr,
		TEXT("/Game/OpenDrive/Physics/PM_GuardrailSlick.PM_GuardrailSlick"));
	PostMesh = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cylinder.Cylinder"));
	// Realistic: a W6x9 post + blockout + delineator (pipeline/make_guardrail_post_mesh.py,
	// unreal/apply_guardrail_assets_real.py) instead of the cylinder. Not a Pick() twin:
	// it's placed differently (yawed, behind the rail, unscaled), see FinalizeTile.
	bRealistic = ODVisualStyle::IsRealistic();
	bRealPosts = false;
	if (bRealistic)
	{
		if (UStaticMesh* Real = LoadObject<UStaticMesh>(nullptr,
			TEXT("/Game/OpenDrive/Realistic/Guardrails/SM_GuardrailPost_real.SM_GuardrailPost_real"),
			nullptr, LOAD_NoWarn | LOAD_Quiet))
		{
			PostMesh = Real;
			bRealPosts = true;
		}
	}

	UE_LOG(LogTemp, Display,
		TEXT("ODGuardrailManager: WBeamMat=%s JerseyMat=%s SlickPhysMat=%s PostMesh=%s"),
		WBeamMat ? *WBeamMat->GetName() : TEXT("FAILED"), JerseyMat ? *JerseyMat->GetName() : TEXT("FAILED"),
		SlickPhysMat ? TEXT("OK") : TEXT("FAILED"), PostMesh ? *PostMesh->GetName() : TEXT("FAILED"));
}

void AODGuardrailManager::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	if (PendingTiles.Num() > 0)
	{
		UE_LOG(LogTemp, Display,
			TEXT("ODGuardrailManager: EndPlay with %d tile load(s) still in flight - "
			     "their completions will no-op harmlessly when they land"),
			PendingTiles.Num());
	}
	Super::EndPlay(EndPlayReason);
}

bool AODGuardrailManager::ParseGuardrailTileFile(const FString& Path, FParsedGuardrailTile& Out)
{
	TArray<uint8> Bin;
	if (!FFileHelper::LoadFileToArray(Bin, *Path) || Bin.Num() < 4 + GenerationIdBytes + 4
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'G' || Bin[3] != '1')
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
		const uint8 ClassId = Cur[1];
		const uint32 SCount = ReadU32LE(Cur + 2);
		Cur += 6;
		const int64 NeedBytes = (int64)SCount * 12;
		if (Remaining() < NeedBytes)
		{
			return false;
		}
		FParsedGuardrailRun& Run = Out.Runs.AddDefaulted_GetRef();
		Run.Side = Side01;
		Run.ClassId = ClassId;
		Run.Stations.SetNumUninitialized(SCount);
		for (uint32 s = 0; s < SCount; ++s, Cur += 12)
		{
			Run.Stations[s] = FVector(ReadF32LE(Cur), ReadF32LE(Cur + 4), ReadF32LE(Cur + 8));
		}
	}
	return true;
}

void AODGuardrailManager::RequestTileLoad(const FIntPoint& Key)
{
	PendingTiles.Add(Key);
	const FString Path = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ FString::Printf(TEXT("%s_tile_%d_%d.bin"), *GuardrailTilePrefix, Key.X, Key.Y);

	TWeakObjectPtr<AODGuardrailManager> WeakThis(this);
	Async(EAsyncExecution::ThreadPool, [WeakThis, Key, Path]()
	{
		FParsedGuardrailTile Parsed;
		FString ReadPath;
		const EODTileSource Src = ODTileSource::Resolve(Path, ReadPath);
		const bool bOk = Src == EODTileSource::Ready && ParseGuardrailTileFile(ReadPath, Parsed);
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

void AODGuardrailManager::OnTileParsed(const FIntPoint& Key, bool bOk, FParsedGuardrailTile&& Parsed)
{
	PendingTiles.Remove(Key);
	if (!bOk)
	{
		MissingTiles.Add(Key);   // no guardrails here - expected almost everywhere
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
			TEXT("ODGuardrailManager: tile (%d,%d) generation id %s does not match "
			     "%s's first-loaded generation id %s - tiles from different pipeline "
			     "runs may be mixed (AUDIT-RELIABILITY.md SS3.1/SS5.1)"),
			Key.X, Key.Y, *Parsed.GenerationId, *GuardrailTilePrefix, *FirstGenerationId);
	}

	FinalizeTile(Key, MoveTemp(Parsed));
}

void AODGuardrailManager::BuildRailMesh(UDynamicMeshComponent* Comp, const FParsedGuardrailRun& Run,
                                         const TArray<FVector2D>& Profile) const
{
	const int32 SCount = Run.Stations.Num();
	const int32 M = Profile.Num();
	if (SCount < 2 || M < 2)
	{
		return;
	}

	// Per-station outward direction (2026-09-04): Right = rotate90CCW(local
	// tangent) computed from consecutive STATION points (already offset to
	// one side of the road by the pipeline) - a parallel-curve tangent
	// matches the centerline tangent closely enough for gentle road curves.
	// Multiplying by Run's stored side sign flips WHICH way is "outward"
	// (away from the pavement), since Right alone only encodes "the
	// vehicle's right hand at this point along the direction of increasing
	// station index," not which side of the road this run is on - see
	// pipeline/build_road_guardrails.py's rx,ry convention this mirrors
	// (side multiplies the same rotate90CCW(tangent) vector there).
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

		// [station][profile layer] -> vertex id
		TArray<int32> VertIds, UvIds;
		VertIds.SetNumUninitialized(SCount * M);
		UvIds.SetNumUninitialized(SCount * M);
		for (int32 s = 0; s < SCount; ++s)
		{
			const float U = s * 20.0f;   // along-rail UV, matches wall ribbon's per-station convention
			for (int32 k = 0; k < M; ++k)
			{
				const FVector P = Run.Stations[s]
					+ Outward[s] * Profile[k].X
					+ FVector(0, 0, Profile[k].Y);
				const int32 Vid = Mesh.AppendVertex(FVector3d(P.X - O.X, P.Y - O.Y, P.Z));
				VertIds[s * M + k] = Vid;
				UvIds[s * M + k] = UV->AppendElement(FVector2f(U, (float)k / (M - 1)));
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
		// Realistic lights the rail with real normals (the _real materials
		// read them for triplanar mapping); Stylized keeps its flat look.
		if (bRealistic)
		{
			FMeshNormals::InitializeOverlayToPerVertexNormals(Mesh.Attributes()->PrimaryNormals(), false);
		}
	});
}

void AODGuardrailManager::FinalizeTile(const FIntPoint& Key, FParsedGuardrailTile&& Parsed)
{
	FGuardrailTile& Tile = Tiles.Add(Key);

	int32 NumWBeam = 0, NumJersey = 0, NumPosts = 0;
	for (const FParsedGuardrailRun& Run : Parsed.Runs)
	{
		if (Run.Stations.Num() < 2)
		{
			continue;
		}
		// motorway/motorway_link (road_mesh_common.ROAD_CLASS_ID id 0) gets
		// the concrete Jersey barrier; every other class gets W-beam - same
		// split pipeline/build_road_guardrails.py's JERSEY_CLASS_IDS decides.
		const bool bJersey = Run.ClassId == 0;
		const TArray<FVector2D>& Profile = bJersey ? JerseyProfile : WBeamProfile;
		bJersey ? ++NumJersey : ++NumWBeam;

		auto* Comp = NewObject<UDynamicMeshComponent>(this);
		Comp->SetMeshDrawPath(EDynamicMeshDrawPath::StaticDraw);   // built once: cached draw commands, not re-gathered every frame per view/shadow view
		Comp->SetupAttachment(RootComponent);
		Comp->SetRelativeLocation(FVector(Run.Stations[0].X, Run.Stations[0].Y, 0.0));   // local origin: see AODRoadManager::CreateChunkMeshComponent (large-world precision)
		Comp->RegisterComponent();
		BuildRailMesh(Comp, Run, Profile);

		if (UMaterialInterface* Mat = bJersey ? JerseyMat.Get() : WBeamMat.Get())
		{
			Comp->SetMaterial(0, Mat);
		}
		// Real, continuous collision (same "thin sheet, EnableComplexAsSimple"
		// pattern as AODBarrierManager's wall ribbon) - but a LOW-FRICTION
		// physical material override so a car that clips the rail scrapes/
		// slides along it instead of catching and stopping dead ("Crew 2
		// deflect" feel, Taylor's design call) - see PM_GuardrailSlick
		// (unreal/apply_guardrail_assets.py), same low-friction-override
		// mechanism ODVehicleMovement::SurfaceGrip's own PM_Asphalt-name
		// check coexists with (that check only ever governs WHEEL raycast
		// grip; this is the actual Chaos body-vs-body contact friction the
		// car's chassis feels when it physically touches the rail).
		// Async cook (2026-09-07 frame-drop pass): without this the whole
		// triangle-mesh cook for every run in a 2 km tile ran synchronously
		// on the game thread in one frame - the same cost AODRoadManager/
		// AODTerrainManager already moved off-thread with this exact flag.
		// Queries against a still-cooking body miss for a frame or two,
		// which is fine here: rails load at SpawnRadius (1.5 km), long
		// before the car can reach them.
		Comp->bUseAsyncCooking = true;
		Comp->EnableComplexAsSimpleCollision();
		Comp->SetCollisionProfileName(TEXT("BlockAll"));
		if (SlickPhysMat)
		{
			Comp->SetPhysMaterialOverride(SlickPhysMat);
		}
		Tile.RailRuns.Add(Comp);

		// Instanced posts every PostSpacingCm along a W-beam run only -
		// Jersey barrier is a solid concrete prism, no discrete posts.
		if (!bJersey && PostMesh)
		{
			auto* Hism = NewObject<UHierarchicalInstancedStaticMeshComponent>(this);
			Hism->SetupAttachment(RootComponent);
			Hism->SetStaticMesh(PostMesh);
			if (WBeamMat)
			{
				Hism->SetMaterial(0, WBeamMat);
			}
			Hism->SetCollisionEnabled(ECollisionEnabled::NoCollision);   // visual only - the ribbon above is the collision
			Hism->SetAffectDynamicIndirectLighting(false);
			Hism->RegisterComponent();

			// Arc length resample of the run's own station polyline at
			// PostSpacingCm - same "walk cumulative distance" idiom as every
			// other per-distance placement in this project, just inline
			// (short arrays, no need for a shared helper).
			double Accum = 0.0;
			double NextPost = 0.0;
			const TArray<FVector>& St = Run.Stations;
			// recompute the same outward-tangent convention BuildRailMesh used,
			// so posts sit at the rail's own seat line (station position IS
			// the seat - see build_road_guardrails.py's GUARDRAIL_RAIL_EXTRA_M).
			// Batched into one AddInstances call (2026-09-07 frame-drop pass) -
			// per-instance AddInstance dirties the HISM tree every call.
			TArray<FTransform> PostXfs;
			const float SideSign = Run.Side > 0 ? 1.0f : -1.0f;
			for (int32 s = 0; s < St.Num() - 1; ++s)
			{
				const double SegLen = FVector::Dist(St[s], St[s + 1]);
				while (NextPost >= Accum && NextPost < Accum + SegLen)
				{
					const double T = SegLen > 1e-3 ? (NextPost - Accum) / SegLen : 0.0;
					const FVector P = FMath::Lerp(St[s], St[s + 1], (double)T);
					if (bRealPosts)
					{
						// Real post: origin = the seat, +X yawed onto BuildRailMesh's
						// outward direction, so it stands behind the rail, unscaled.
						FVector Tangent = St[s + 1] - St[s];
						Tangent.Z = 0.0;
						if (!Tangent.Normalize())
						{
							Tangent = FVector(1, 0, 0);
						}
						const FVector Outward = FVector(-Tangent.Y, Tangent.X, 0.0) * SideSign;
						PostXfs.Emplace(Outward.Rotation(), P, FVector::OneVector);
					}
					else
					{
						PostXfs.Emplace(FRotator::ZeroRotator,
							P - FVector(0, 0, PostHeightCm * 0.15),   // embed a little below the seat
							FVector(PostRadiusCm / 50.0f, PostRadiusCm / 50.0f, PostHeightCm / 100.0f));
					}
					++NumPosts;
					NextPost += PostSpacingCm;
				}
				Accum += SegLen;
			}
			Hism->AddInstances(PostXfs, false, /*bWorldSpace*/false);   // relative to the actor (2026-10-05, menu vistas are offset copies)
			Tile.PostHisms.Add(Hism);
		}
	}

	UE_LOG(LogTemp, Verbose,
		TEXT("ODGuardrailManager: tile (%d,%d) built %d W-beam run(s), %d Jersey run(s), %d post(s)"),
		Key.X, Key.Y, NumWBeam, NumJersey, NumPosts);
}

void AODGuardrailManager::ReleaseTile(const FIntPoint& Key)
{
	if (FGuardrailTile* Tile = Tiles.Find(Key))
	{
		for (const auto& Comp : Tile->RailRuns)
		{
			if (Comp) { Comp->DestroyComponent(); }
		}
		for (const auto& Hism : Tile->PostHisms)
		{
			if (Hism) { Hism->DestroyComponent(); }
		}
		Tiles.Remove(Key);
	}
}

void AODGuardrailManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	FVector Loc;
	if (!Vista.StreamCenter(this, Loc))
	{
		return;
	}
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
		int32 NumRuns = 0, NumPosts = 0;
		for (const auto& Pair : Tiles)
		{
			NumRuns += Pair.Value.RailRuns.Num();
			for (const auto& Hism : Pair.Value.PostHisms)
			{
				if (Hism) { NumPosts += Hism->GetInstanceCount(); }
			}
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODGuardrailManager: tiles=%d pending=%d missing=%d railRuns=%d posts=%d"),
			Tiles.Num(), PendingTiles.Num(), MissingTiles.Num(), NumRuns, NumPosts);
	}
}
