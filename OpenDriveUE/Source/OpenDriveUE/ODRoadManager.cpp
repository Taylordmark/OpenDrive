#include "ODRoadManager.h"

#include "ODBinSidecarUtil.h"
#include "Async/Async.h"
#include "Components/DynamicMeshComponent.h"
#include "Containers/StringConv.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/DynamicMeshOverlay.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInterface.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

using namespace UE::Geometry;

namespace
{
// manual little-endian parsing (no struct cast) - same convention as
// ODForestManager::LoadMask / ODRaceManager::LoadCenterline's binary
// sidecars, and avoids any packing/alignment assumption on disk.
// ReadU32LE/ReadF32LE moved to the shared ODBinSidecarUtil.h (2026-07-28) -
// this file's own ReadU16LE/ReadPascalUtf8 below are unique to the ODL1
// place-label format, so they stay local.
uint16 ReadU16LE(const uint8* P)
{
	return (uint16)P[0] | (uint16)P[1] << 8;
}

// Reads a uint16-length-prefixed UTF-8 string, advancing Cur past it.
// Returns false (leaving Out untouched) if the declared length would run
// past End - the same "truncated file" bail-out every other parser in this
// project uses, just for a variable-length field instead of a fixed one.
bool ReadPascalUtf8(const uint8*& Cur, const uint8* End, FString& Out)
{
	if (End - Cur < 2)
	{
		return false;
	}
	const uint16 Len = ReadU16LE(Cur);
	Cur += 2;
	if (End - Cur < Len)
	{
		return false;
	}
	FUTF8ToTCHAR Converter(reinterpret_cast<const ANSICHAR*>(Cur), Len);
	Out = FString(Converter.Length(), Converter.Get());
	Cur += Len;
	return true;
}
}

AODRoadManager::AODRoadManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODRoadManager::BeginPlay()
{
	Super::BeginPlay();

	RoadMat = LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_Road.M_Road"));
	RoadMatOneWay = LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_Road_OneWay.M_Road_OneWay"));
	LoadCenterline();
	LoadMapCenterline();
	LoadPlaceLabels();
	BuildTownNodes();

	// Tiles/PendingTiles/MissingTiles start empty - the first Tick()
	// discovers what's needed around the player/spawn point (slice 1 used
	// to eagerly load the whole network here instead).
}

void AODRoadManager::LoadCenterline()
{
	if (CenterlineFilePrefix.IsEmpty())
	{
		return;   // no route data on this actor instance - e.g. Tail of the
		          // Dragon, which draws its map off AODRaceManager's own copy
	}
	TArray<uint8> Bin;
	const FString BinPath = FPaths::ProjectContentDir()
		/ TEXT("OpenDrive/Masks") / (CenterlineFilePrefix + TEXT(".bin"));
	if (!FFileHelper::LoadFileToArray(Bin, *BinPath) || Bin.Num() < 8
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'C' || Bin[3] != '1')
	{
		return;
	}
	const int32 N = (int32)ReadU32LE(Bin.GetData() + 4);
	constexpr int32 RecordBytes = 4 + 4;   // X_cm, Y_cm
	if (N <= 0 || Bin.Num() < 8 + (int64)N * RecordBytes)
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODRoadManager: %s truncated/corrupt (N=%d, %lld bytes)"),
			*BinPath, N, Bin.Num());
		return;
	}
	RoadCenterline.SetNumUninitialized(N);
	const uint8* P = Bin.GetData() + 8;
	for (int32 i = 0; i < N; ++i, P += RecordBytes)
	{
		// float32 pairs on disk, FVector2D components are double (UE5 LWC) -
		// widen per-element via ReadF32LE, not a bulk memcpy (silently
		// corrupts every point - see ODRaceManager::LoadCenterline's comment).
		RoadCenterline[i] = FVector2D(ReadF32LE(P), ReadF32LE(P + 4));
	}
	UE_LOG(LogTemp, Display,
		TEXT("ODRoadManager: loaded %d centerline point(s) from %s (HUD route fallback)"),
		N, *BinPath);
}

void AODRoadManager::LoadMapCenterline()
{
	if (MapCenterlineFilePrefix.IsEmpty())
	{
		return;   // unset - DrawStageMap falls back to RoadCenterline
	}
	TArray<uint8> Bin;
	const FString BinPath = FPaths::ProjectContentDir()
		/ TEXT("OpenDrive/Masks") / (MapCenterlineFilePrefix + TEXT(".bin"));
	if (!FFileHelper::LoadFileToArray(Bin, *BinPath) || Bin.Num() < 8
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'M' || Bin[3] != '1')
	{
		return;
	}
	const int32 N = (int32)ReadU32LE(Bin.GetData() + 4);
	constexpr int32 RecordBytes = 4 + 4;   // X_cm, Y_cm
	if (N <= 0 || Bin.Num() < 8 + (int64)N * RecordBytes)
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODRoadManager: %s truncated/corrupt (N=%d, %lld bytes)"),
			*BinPath, N, Bin.Num());
		return;
	}
	MapCenterline.SetNumUninitialized(N);
	const uint8* P = Bin.GetData() + 8;
	for (int32 i = 0; i < N; ++i, P += RecordBytes)
	{
		// NAN sentinel pairs round-trip fine through per-element ReadF32LE -
		// same double-widen-per-component convention as LoadCenterline above,
		// just that some "points" here are deliberately NAN (segment breaks).
		MapCenterline[i] = FVector2D(ReadF32LE(P), ReadF32LE(P + 4));
	}
	UE_LOG(LogTemp, Display,
		TEXT("ODRoadManager: loaded %d map-centerline point(s) (incl. breaks) from %s"),
		N, *BinPath);
}

void AODRoadManager::LoadPlaceLabels()
{
	if (PlaceLabelFilePrefix.IsEmpty())
	{
		return;   // feature off for this level actor instance
	}
	TArray<uint8> Bin;
	const FString BinPath = FPaths::ProjectContentDir()
		/ TEXT("OpenDrive/Masks") / (PlaceLabelFilePrefix + TEXT(".bin"));
	if (!FFileHelper::LoadFileToArray(Bin, *BinPath) || Bin.Num() < 8
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'L' || Bin[3] != '1')
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODRoadManager: place-label sidecar %s missing/unreadable - "
			     "labels will stay blank"), *BinPath);
		return;
	}
	const int32 N = (int32)ReadU32LE(Bin.GetData() + 4);
	const uint8* Cur = Bin.GetData() + 8;
	const uint8* End = Bin.GetData() + Bin.Num();
	PlaceLabels.Reserve(N);
	for (int32 i = 0; i < N; ++i)
	{
		if (End - Cur < 8)
		{
			UE_LOG(LogTemp, Warning,
				TEXT("ODRoadManager: %s truncated at record %d/%d"), *BinPath, i, N);
			break;
		}
		FPlaceLabel Label;
		Label.Pos = FVector2D(ReadF32LE(Cur), ReadF32LE(Cur + 4));
		Cur += 8;
		if (!ReadPascalUtf8(Cur, End, Label.RoadLabel)
			|| !ReadPascalUtf8(Cur, End, Label.CityLabel))
		{
			UE_LOG(LogTemp, Warning,
				TEXT("ODRoadManager: %s truncated mid-string at record %d/%d"),
				*BinPath, i, N);
			break;
		}
		PlaceLabels.Add(MoveTemp(Label));
	}
	UE_LOG(LogTemp, Display,
		TEXT("ODRoadManager: loaded %d place label(s) from %s"),
		PlaceLabels.Num(), *BinPath);
}

void AODRoadManager::UpdateCurrentLabels(const FVector& PlayerLoc)
{
	if (PlaceLabels.Num() == 0)
	{
		return;   // leave CurrentRoadLabel/CurrentCityLabel blank - HUD
		          // already treats that as "draw nothing"
	}
	const FVector2D Loc2D(PlayerLoc);
	int32 BestIdx = 0;
	double BestDistSq = FVector2D::DistSquared(Loc2D, PlaceLabels[0].Pos);
	for (int32 i = 1; i < PlaceLabels.Num(); ++i)
	{
		const double DistSq = FVector2D::DistSquared(Loc2D, PlaceLabels[i].Pos);
		if (DistSq < BestDistSq)
		{
			BestDistSq = DistSq;
			BestIdx = i;
		}
	}
	CurrentRoadLabel = PlaceLabels[BestIdx].RoadLabel;
	CurrentCityLabel = PlaceLabels[BestIdx].CityLabel;
}

bool AODRoadManager::NearestRoadPoint(const FVector2D& Query, FVector2D& OutPoint,
	float& OutYawDeg) const
{
	const int32 N = RoadCenterline.Num();
	if (N == 0)
	{
		return false;
	}
	int32 Best = 0;
	double BestSq = FVector2D::DistSquared(Query, RoadCenterline[0]);
	for (int32 i = 1; i < N; ++i)
	{
		const double D = FVector2D::DistSquared(Query, RoadCenterline[i]);
		if (D < BestSq)
		{
			BestSq = D;
			Best = i;
		}
	}
	OutPoint = RoadCenterline[Best];
	// road heading from the local tangent (forward neighbor, or backward at the
	// very end). World frame X=east, Y=south, so yaw = atan2(dy,dx) maps
	// straight through - same convention the HUD player arrow uses.
	const FVector2D Tan = (Best + 1 < N)
		? RoadCenterline[Best + 1] - RoadCenterline[Best]
		: RoadCenterline[Best] - RoadCenterline[Best - 1];
	OutYawDeg = FMath::RadiansToDegrees(FMath::Atan2(Tan.Y, Tan.X));
	return true;
}

void AODRoadManager::BuildTownNodes()
{
	TownNodes.Reset();
	if (PlaceLabels.Num() == 0)
	{
		return;   // no place data (e.g. Tail of the Dragon) - map-select falls
		          // back to PlayerStart
	}
	// Dedup the ~50 m-spaced label points into one node per unique town: the
	// representative position is the centroid of that town's label points
	// (they already sit on/near the road), then snapped to the exact nearest
	// centerline point so a spawn there lands on pavement.
	struct FAccum { FVector2D Sum = FVector2D::ZeroVector; int32 Count = 0; };
	TMap<FString, FAccum> ByName;
	for (const FPlaceLabel& L : PlaceLabels)
	{
		if (L.CityLabel.IsEmpty())
		{
			continue;
		}
		FAccum& A = ByName.FindOrAdd(L.CityLabel);
		A.Sum += L.Pos;
		++A.Count;
	}
	for (const TPair<FString, FAccum>& Pair : ByName)
	{
		const FVector2D Centroid = Pair.Value.Sum / FMath::Max(1, Pair.Value.Count);
		FTownNode Node;
		Node.Name = Pair.Key;
		FVector2D Snapped;
		float Yaw;
		Node.Pos = NearestRoadPoint(Centroid, Snapped, Yaw) ? Snapped : Centroid;
		TownNodes.Add(MoveTemp(Node));
	}
	// stable, human-friendly order for the map labels
	TownNodes.Sort([](const FTownNode& A, const FTownNode& B)
	{
		return A.Name < B.Name;
	});
	UE_LOG(LogTemp, Display, TEXT("AODRoadManager: %d town node(s) for the menu map"),
		TownNodes.Num());
}

void AODRoadManager::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	if (PendingTiles.Num() > 0)
	{
		// New to this module: nothing else here has had a lifetime-crossing
		// async op to clean up before. Thread-pool work in flight can't be
		// cancelled - OnTileParsed's WeakThis/GetWorld() guard makes a
		// post-teardown completion a safe no-op, so this is expected, not a
		// bug, if it fires (e.g. PIE stopped mid-drive with loads in flight).
		UE_LOG(LogTemp, Display,
			TEXT("ODRoadManager: EndPlay with %d tile load(s) still in flight - "
			     "their completions will no-op harmlessly when they land"),
			PendingTiles.Num());
	}
	Super::EndPlay(EndPlayReason);
}

bool AODRoadManager::ParseRoadTileFile(const FString& Path, FParsedRoadTile& Out)
{
	// ODR2 magic+LE-header+payload sidecar convention (2026-07-25: was ODR1,
	// bumped when the per-chunk oneway byte was added - reader/writer are
	// always regenerated together in this pipeline, no back-compat needed,
	// the bump just makes a stale/mismatched pair fail loudly via this magic
	// check instead of silently misreading the new byte as vertex data).
	// Pure data parse, no UObject/actor state touched, safe off the game
	// thread.
	TArray<uint8> Bin;
	if (!FFileHelper::LoadFileToArray(Bin, *Path) || Bin.Num() < 8
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'R' || Bin[3] != '2')
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
	const uint32 ChunkCount = ReadU32LE(Cur);
	Cur += 4;

	Out.Chunks.Reserve(ChunkCount);
	for (uint32 c = 0; c < ChunkCount; ++c)
	{
		if (Remaining() < 9)
		{
			return false;
		}
		const uint32 VCount = ReadU32LE(Cur); Cur += 4;
		const uint32 TCount = ReadU32LE(Cur); Cur += 4;
		const bool bOneway = *Cur != 0; Cur += 1;
		const int64 NeedBytes = (int64)VCount * 12 + (int64)VCount * 8 + (int64)TCount * 12;
		if (Remaining() < NeedBytes)
		{
			return false;
		}

		const uint8* VertsBase = Cur;
		const uint8* UvBase = Cur + (int64)VCount * 12;
		const uint8* TrisBase = Cur + (int64)VCount * 12 + (int64)VCount * 8;
		Cur += NeedBytes;

		FParsedChunk& Chunk = Out.Chunks.AddDefaulted_GetRef();
		Chunk.bOneway = bOneway;
		Chunk.Verts.SetNumUninitialized(VCount * 3);
		for (uint32 v = 0; v < VCount; ++v)
		{
			Chunk.Verts[v * 3 + 0] = ReadF32LE(VertsBase + v * 12 + 0);
			Chunk.Verts[v * 3 + 1] = ReadF32LE(VertsBase + v * 12 + 4);
			Chunk.Verts[v * 3 + 2] = ReadF32LE(VertsBase + v * 12 + 8);
		}
		Chunk.Uvs.SetNumUninitialized(VCount * 2);
		for (uint32 v = 0; v < VCount; ++v)
		{
			Chunk.Uvs[v * 2 + 0] = ReadF32LE(UvBase + v * 8 + 0);
			Chunk.Uvs[v * 2 + 1] = ReadF32LE(UvBase + v * 8 + 4);
		}
		Chunk.Tris.SetNumUninitialized(TCount * 3);
		for (uint32 t = 0; t < TCount; ++t)
		{
			Chunk.Tris[t * 3 + 0] = ReadU32LE(TrisBase + t * 12 + 0);
			Chunk.Tris[t * 3 + 1] = ReadU32LE(TrisBase + t * 12 + 4);
			Chunk.Tris[t * 3 + 2] = ReadU32LE(TrisBase + t * 12 + 8);
		}
	}
	return true;
}

void AODRoadManager::RequestTileLoad(const FIntPoint& Key)
{
	PendingTiles.Add(Key);
	const FString Path = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ FString::Printf(TEXT("%s_tile_%d_%d.bin"), *RoadTilePrefix, Key.X, Key.Y);

	// worker-thread lambda captures ONLY plain copied-by-value inputs (path,
	// key) and a weak ptr for the eventual game-thread hop - it must never
	// touch Tiles/PendingTiles/MissingTiles or any other actor state, which
	// isn't synchronized for cross-thread access.
	TWeakObjectPtr<AODRoadManager> WeakThis(this);
	Async(EAsyncExecution::ThreadPool, [WeakThis, Key, Path]()
	{
		FParsedRoadTile Parsed;
		const bool bOk = ParseRoadTileFile(Path, Parsed);
		AsyncTask(ENamedThreads::GameThread,
			[WeakThis, Key, bOk, Parsed = MoveTemp(Parsed)]() mutable
			{
				// a queued GameThread task can in principle run a frame or
				// two after world teardown starts even if the actor object
				// itself hasn't been GC'd yet - check both.
				if (!WeakThis.IsValid() || WeakThis->GetWorld() == nullptr)
				{
					return;
				}
				WeakThis->OnTileParsed(Key, bOk, MoveTemp(Parsed));
			});
	});
}

void AODRoadManager::OnTileParsed(const FIntPoint& Key, bool bOk, FParsedRoadTile&& Parsed)
{
	// cleared on every path: success below, or the failure return right here.
	PendingTiles.Remove(Key);

	if (!bOk)
	{
		MissingTiles.Add(Key);
		return;
	}

	// Builds straight away - no runtime ground-snap/wait-for-terrain step
	// needed (Arc 7.2 restructure, 2026-07-20, superseded by the Arc 7.0
	// baked-terrain pivot on 2026-07-23): the pipeline now carves the
	// offline-baked terrain tiles to the road's elevation profile offline
	// (pipeline/build_terrain_mesh_tiles.py + road_mesh_common.carve_heightmap),
	// verified by pipeline/verify_terrain_fit.py against the ACTUAL
	// simplified mesh before it ever reaches the engine, so station Z values
	// arriving here are already correct - there is no live terrain stream to
	// reconcile against at runtime at all anymore. See next steps.md Arc 7.2
	// for why the old runtime-snap approach was removed (it raced the old
	// live-streaming plugin's async terrain streaming and could still
	// disagree with it after simplification).
	FinalizeTile(Key, MoveTemp(Parsed));
}

void AODRoadManager::FinalizeTile(const FIntPoint& Key, FParsedRoadTile&& Parsed)
{
	FRoadTile& Tile = Tiles.Add(Key);
	Tile.Chunks.Reserve(Parsed.Chunks.Num());
	const FTransform CompXf = GetActorTransform();   // identity today (actor
	                                                  // always spawned at world
	                                                  // origin) - kept explicit
	                                                  // rather than assumed.

	for (const FParsedChunk& Chunk : Parsed.Chunks)
	{
		const uint32 VCount = Chunk.Verts.Num() / 3;
		const uint32 TCount = Chunk.Tris.Num() / 3;

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
				const float X = Chunk.Verts[v * 3 + 0];
				const float Y = Chunk.Verts[v * 3 + 1];
				const float Z = Chunk.Verts[v * 3 + 2];
				VertIds[v] = Mesh.AppendVertex(FVector3d(X, Y, Z));

				// skirt-bottom sample for the -odseam terrain check: every
				// 10th station's outer two verts (index 0/3 of the
				// skirt-L,pave-L,pave-R,skirt-R layout build_road_chunks
				// always emits, 4 verts per station) - same convention as
				// slice 1, just filled in incrementally as tiles load now.
				const uint32 StationIdx = v / 4;
				const uint32 InStation = v % 4;
				if ((StationIdx % 10) == 0 && (InStation == 0 || InStation == 3))
				{
					SkirtSamplePoints.Add(CompXf.TransformPosition(FVector(X, Y, Z)));
				}
			}
			for (uint32 v = 0; v < VCount; ++v)
			{
				UvIds[v] = UV->AppendElement(
					FVector2f(Chunk.Uvs[v * 2 + 0], Chunk.Uvs[v * 2 + 1]));
			}
			for (uint32 t = 0; t < TCount; ++t)
			{
				const uint32 I0 = Chunk.Tris[t * 3 + 0];
				const uint32 I1 = Chunk.Tris[t * 3 + 1];
				const uint32 I2 = Chunk.Tris[t * 3 + 2];
				const int32 Tid = Mesh.AppendTriangle(VertIds[I0], VertIds[I1], VertIds[I2]);
				if (Tid >= 0)
				{
					UV->SetTriangle(Tid, FIndex3i(UvIds[I0], UvIds[I1], UvIds[I2]));
				}
			}
		});

		Comp->EnableComplexAsSimpleCollision();
		Comp->SetCollisionProfileName(TEXT("BlockAll"));
		// one-way carriageways (e.g. a divided highway's own direction) get
		// the no-center-yellow variant - RoadMat's baked centerline only
		// belongs on a genuine two-way road. Falls back to RoadMat if the
		// one-way variant failed to load, rather than rendering nothing.
		UMaterialInterface* ChunkMat = (Chunk.bOneway && RoadMatOneWay) ? RoadMatOneWay : RoadMat;
		if (ChunkMat)
		{
			Comp->SetMaterial(0, ChunkMat);
		}

		Tile.Chunks.Add(Comp);
	}

	UE_LOG(LogTemp, Display,
		TEXT("ODRoadManager: tile (%d,%d) built %d chunk(s)"),
		Key.X, Key.Y, Parsed.Chunks.Num());
}

void AODRoadManager::ReleaseTile(const FIntPoint& Key)
{
	if (FRoadTile* Tile = Tiles.Find(Key))
	{
		for (const auto& Comp : Tile->Chunks)
		{
			if (Comp)
			{
				Comp->DestroyComponent();
			}
		}
		Tiles.Remove(Key);
	}
}

void AODRoadManager::RunSeamCheck()
{
	// The old live-streaming plugin only streamed terrain tiles near the
	// camera/pawn (proven in Arc 7.1 - it's view-driven LOD, not "load the
	// whole georeferenced bbox").
	// SkirtSamplePoints spans only whatever ROAD tiles have streamed in so
	// far (now incremental, slice 2), so a trace far from the anchor
	// legitimately hits nothing - that's not a seam problem, it's untested
	// terrain. Anchor on the captured spawn location, not the live pawn -
	// by the time a scripted drive has run long enough for terrain to have
	// streamed in, the pawn itself may have driven off this small test area
	// entirely, which would wrongly skip everything near the actual area
	// under test. (Was SkirtSamplePoints[0] - the first-ARRIVED skirt
	// sample, not the first-DISPATCHED one; thread-pool completion order
	// isn't ring order, so that landed 3.4 km from spawn on this network's
	// 18-tile load and reported a false n=0 - see the header comment.)
	const FVector Anchor = bSpawnAnchorCaptured ? SpawnAnchor
		: (SkirtSamplePoints.Num() > 0 ? SkirtSamplePoints[0] : FVector::ZeroVector);
	constexpr double MaxCheckDistCm = 200000.0;   // 2 km
	UE_LOG(LogTemp, Display,
		TEXT("ODRoadManager: seam check anchor=(%.0f,%.0f,%.0f) samples=%d"),
		Anchor.X, Anchor.Y, Anchor.Z, SkirtSamplePoints.Num());

	int32 N = 0, Over = 0, Under = 0, Skipped = 0;
	double SumAbs = 0.0, MaxAbs = 0.0;
	for (const FVector& P : SkirtSamplePoints)
	{
		if (FVector::DistSquared2D(P, Anchor) > MaxCheckDistCm * MaxCheckDistCm)
		{
			++Skipped;
			continue;
		}
		FHitResult Hit;
		const FVector Start = P + FVector(0, 0, 10000.0);
		const FVector End = P - FVector(0, 0, 10000.0);
		FCollisionQueryParams Params;
		Params.AddIgnoredActor(this);
		if (const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0))
		{
			Params.AddIgnoredActor(Player);
		}
		if (!GetWorld()->LineTraceSingleByChannel(Hit, Start, End, ECC_Visibility, Params))
		{
			continue;
		}
		const double Dz = (Hit.ImpactPoint.Z - P.Z) / 100.0;   // m; terrain - skirt
		++N;
		SumAbs += FMath::Abs(Dz);
		MaxAbs = FMath::Max(MaxAbs, FMath::Abs(Dz));
		if (Dz > 0.0) { ++Over; } else { ++Under; }
	}
	const double Mean = N > 0 ? SumAbs / N : 0.0;
	UE_LOG(LogTemp, Display,
		TEXT("ODRoadManager: seam n=%d mean|dz|=%.2f m max|dz|=%.2f m over=%d under=%d (skipped=%d far from player)"),
		N, Mean, MaxAbs, Over, Under, Skipped);
}

void AODRoadManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Player)
	{
		return;
	}
	const FVector Loc = Player->GetActorLocation();
	if (!bSpawnAnchorCaptured)
	{
		SpawnAnchor = Loc;
		bSpawnAnchorCaptured = true;
	}

	// throttled (~2 Hz, not every frame) nearest-point scan for the HUD's
	// road-name/city label - a brute-force O(N) scan over a few thousand
	// points is trivial at this rate, no need for a spatial index
	if (const double Now = GetWorld()->GetTimeSeconds(); Now - LastLabelUpdateTime > 0.5)
	{
		LastLabelUpdateTime = Now;
		UpdateCurrentLabels(Loc);
	}

	const FIntPoint Center(FMath::FloorToInt(Loc.X / TileSizeCm),
	                       FMath::FloorToInt(Loc.Y / TileSizeCm));

	// world-space-correct reach, not a bare tile-count constant: a chunk's
	// centroid can bucket into a tile up to MaxChunkSpanCm/2 farther out
	// than where its geometry actually starts, so the load radius is padded
	// by that before converting to a tile count. Despawn adds a further
	// fixed world-space margin so tiles don't flicker in/out right at the
	// load boundary.
	const int32 LoadReach =
		FMath::CeilToInt((SpawnRadius + MaxChunkSpanCm * 0.5f) / TileSizeCm);
	const int32 DespawnReach = FMath::CeilToInt(
		(SpawnRadius + MaxChunkSpanCm * 0.5f + DespawnMarginCm) / TileSizeCm);

	// spawn missing tiles nearest-first, capped dispatches per tick (the
	// load itself is async, but this caps how many simultaneous file
	// opens/thread-pool jobs a big ring can trigger at once)
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

	// retire tiles beyond the despawn radius (hysteresis vs. the load radius)
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
		int32 NumChunks = 0;
		for (const auto& Pair : Tiles)
		{
			NumChunks += Pair.Value.Chunks.Num();
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODRoadManager: tiles=%d pending=%d missing=%d chunks=%d"),
			Tiles.Num(), PendingTiles.Num(), MissingTiles.Num(), NumChunks);
	}

	// headless verification: `-odseam=N` runs the terrain-seam check N
	// seconds after boot, once (same delayed-trigger pattern as -odshot)
	static float SeamAt = -1.0f;
	static bool bSeamParsed = false, bSeamDone = false;
	if (!bSeamParsed)
	{
		bSeamParsed = true;
		FParse::Value(FCommandLine::Get(), TEXT("odseam="), SeamAt);
	}
	if (SeamAt > 0.0f && !bSeamDone && GetWorld()->GetTimeSeconds() > SeamAt)
	{
		bSeamDone = true;
		RunSeamCheck();
	}
}
