#pragma once

#include "CoreMinimal.h"
#include "Misc/FileHelper.h"

/** Manual little-endian parsing helpers shared by every binary sidecar
 *  reader in this project (ODT1/ODR2/ODW3/ODU1/etc.) - no struct cast, no
 *  packing/alignment assumption on disk. Consolidated 2026-07-28: four
 *  managers (Road/Terrain/Barrier/Building) each independently defined an
 *  identical anonymous-namespace copy of these two functions, which
 *  compiled fine under the Editor target's per-file (non-unity) build but
 *  hard-failed the standalone Game target's unity build - unity merges
 *  several of those .cpp files into one translation unit, and an anonymous
 *  namespace is unique per TU, not per source file, so the four identical
 *  ReadU32LE/ReadF32LE definitions collided as soon as they landed in the
 *  same unity blob. `inline` functions in a shared header are exempt from
 *  the one-definition-rule problem that caused that (safe to include from
 *  any number of translation units, unity-merged or not) - and it deletes
 *  three of the four duplicate copies as a bonus. */

inline uint32 ReadU32LE(const uint8* P)
{
	return (uint32)P[0] | (uint32)P[1] << 8 | (uint32)P[2] << 16 | (uint32)P[3] << 24;
}

inline float ReadF32LE(const uint8* P)
{
	float V;
	FMemory::Memcpy(&V, P, 4);
	return V;
}

/** Integer-lattice value noise, smoothstep-interpolated - a pure function of
 *  (X, Y, Seed), no engine RNG state, so two threads (or two runs) tinting
 *  the same vertex always agree. Deliberately NOT Perlin/Simplex - this only
 *  needs cheap large-scale patchiness, not gradient-continuity guarantees.
 *  Moved here from ODTerrainManager.cpp (2026-09-25) so AODForestManager's
 *  aspen-patch mask shares it - same inline/ODR reasoning as ReadU32LE. */
inline float HashLattice(int32 X, int32 Y, int32 Seed)
{
	uint32 H = (uint32)X * 374761393u + (uint32)Y * 668265263u + (uint32)Seed * 2246822519u;
	H = (H ^ (H >> 13)) * 1274126177u;
	H ^= H >> 16;
	return (float)(H & 0x00FFFFFFu) / (float)0x00FFFFFFu; // [0,1]
}

inline float ValueNoise2D(float X, float Y, float ScaleCm, int32 Seed)
{
	const float Gx = X / ScaleCm, Gy = Y / ScaleCm;
	const int32 Ix = FMath::FloorToInt(Gx), Iy = FMath::FloorToInt(Gy);
	const float Fx = Gx - Ix, Fy = Gy - Iy;
	const float V00 = HashLattice(Ix, Iy, Seed);
	const float V10 = HashLattice(Ix + 1, Iy, Seed);
	const float V01 = HashLattice(Ix, Iy + 1, Seed);
	const float V11 = HashLattice(Ix + 1, Iy + 1, Seed);
	const float Sx = Fx * Fx * (3.0f - 2.0f * Fx);
	const float Sy = Fy * Fy * (3.0f - 2.0f * Fy);
	return FMath::Lerp(FMath::Lerp(V00, V10, Sx), FMath::Lerp(V01, V11, Sx), Sy);
}

/** Fixed-width ASCII-hex generation-id field every sidecar header now
 *  carries immediately after its magic (pipeline/generation_id.py,
 *  2026-08-21, AUDIT-RELIABILITY.md SS3.1/SS5.1) - a short hash of
 *  road_stations.json's content plus the constants that affect derived
 *  geometry shape. Lets a tile manager notice when tiles it's streaming
 *  disagree about which pipeline run they came from (e.g. barrier tiles
 *  baked against a stale road_stations.json next to fresh terrain tiles -
 *  exactly SS2's mixed-bake bug) without making that mismatch fatal: see
 *  each manager's GenerationMismatchWarned set for the "log once per
 *  prefix, never block" policy this is meant to serve. */
inline constexpr int32 GenerationIdBytes = 16;

/** Reads the GenerationIdBytes ASCII bytes at P into a plain FString. Does
 *  NOT bounds-check P/P+GenerationIdBytes against the buffer - callers must
 *  confirm Remaining() >= GenerationIdBytes first, same convention as
 *  ReadU32LE/ReadF32LE above. */
inline FString ReadGenerationId(const uint8* P)
{
	return FString(GenerationIdBytes, reinterpret_cast<const ANSICHAR*>(P));
}

/** Shared tree mesh set (2026-08-22): ODForestManager.cpp and
 *  ODBarrierManager.cpp both need this same three-mesh list and used to each
 *  independently define an identical anonymous-namespace TreeMeshPaths array
 *  - the exact ReadU32LE/ReadF32LE unity-build collision this header's
 *  top-of-file comment already describes fixing once, just for a different
 *  symbol: fine under the Editor target's per-file build, hard C2374/C2086
 *  redefinition error under the standalone Game target's unity build once
 *  both .cpp files land in the same translation unit. `inline` in a shared
 *  header is exempt from ODR for the same reason the two functions above
 *  are. */
inline const TCHAR* TreeMeshPaths[] = {
	TEXT("/Game/OpenDrive/Trees/tree_blob.tree_blob"),
	TEXT("/Game/OpenDrive/Trees/tree_olive.tree_olive"),
	TEXT("/Game/OpenDrive/Trees/tree_conifer.tree_conifer"),
};

/** Extended 7-species tree mesh set (2026-08-27, ODForestManager's
 *  "chaparral isn't working and all the trees are the same" fix) - the
 *  first 3 entries are IDENTICAL to TreeMeshPaths above (index parity with
 *  FBiomeRecipe::SpeciesWeight[0..2] matters), plus 4 new biome-distinct
 *  species from make_tree_meshes.py. Originally local to ODForestManager.cpp
 *  ("nothing else needs it yet"); moved here 2026-08-27 once
 *  ODBarrierManager.cpp became a second consumer (biome-aware windbreak
 *  species pick, Taylor's live-drive report: desert windbreak rows render as
 *  bright green trees) - same ODR-collision reasoning as TreeMeshPaths/
 *  ReadU32LE/ReadF32LE above: an identically-named anonymous-namespace copy
 *  in two .cpp files collides under the standalone Game target's unity
 *  build the moment both land in the same translation unit. */
inline const TCHAR* ExtendedTreeMeshPaths[] = {
	TEXT("/Game/OpenDrive/Trees/tree_blob.tree_blob"),
	TEXT("/Game/OpenDrive/Trees/tree_olive.tree_olive"),
	TEXT("/Game/OpenDrive/Trees/tree_conifer.tree_conifer"),
	TEXT("/Game/OpenDrive/Trees/tree_scrub_oak.tree_scrub_oak"),
	TEXT("/Game/OpenDrive/Trees/tree_manzanita.tree_manzanita"),
	TEXT("/Game/OpenDrive/Trees/tree_yucca.tree_yucca"),
	TEXT("/Game/OpenDrive/Trees/tree_canyon_oak.tree_canyon_oak"),
};
inline constexpr int32 NumExtendedTreeSpecies = UE_ARRAY_COUNT(ExtendedTreeMeshPaths);

/** Maps an already-drawn [0,1) random value to a weighted index (species or
 *  shrub choice) - takes R rather than an FRandomStream so it never
 *  consumes RNG itself (ODForestManager::BuildTile()'s "RNG fully consumed
 *  above" ordering guarantee depends on that). Moved here 2026-08-27
 *  alongside ExtendedTreeMeshPaths for the same second-consumer reason
 *  (ODBarrierManager.cpp's biome-aware species pick reuses this exact
 *  weighted-pick logic against AODForestManager::RecipeForBiome()'s
 *  SpeciesWeight table). */
inline int32 PickWeightedIndex(float R, const float* Weight, int32 Count)
{
	float Sum = 0.0f;
	for (int32 i = 0; i < Count; ++i)
	{
		Sum += Weight[i];
	}
	if (Sum <= 0.0f)
	{
		return 0;
	}
	float Acc = 0.0f;
	for (int32 i = 0; i < Count; ++i)
	{
		Acc += Weight[i];
		if (R < Acc / Sum)
		{
			return i;
		}
	}
	return Count - 1;
}

/** One road-station sample from the ODS1 station sidecar
 *  (roadstations_{area}.bin, pipeline export) - a single point along the
 *  baked road centerline, used purely as a nearest-station lookup target
 *  (carve elevation in AODTerrainManager, road-exclusion distance in
 *  AODForestManager). No segment/s-position identity is kept here, unlike
 *  the pipeline's own station arrays - both consumers only ever need
 *  "nearest point on the road (and its elevation)", same as the pipeline's
 *  carve_grid_m()'s own nearest-station search.
 *
 *  Lifted out of ODTerrainManager.h (2026-08-26, carve-clamp/road-exclusion
 *  rescope) so AODForestManager can build its own copy of the same spatial
 *  hash for road-exclusion without duplicating the struct or the parser -
 *  see AODForestManager::LoadRoadStations. */
struct FRoadStation
{
	float X = 0.0f, Y = 0.0f, Z = 0.0f; // cm, UE world frame
	// Pavement half-width at this station (cm) - per-station since ODS2
	// (pipeline lanes= tag width, 2026-09-06); ODS1 files fall back to the
	// classic constant 4.5 m footprint. The runtime carve uses this as its
	// clamp-radius floor so wide roads carve their full footprint.
	float HalfWidthCm = 450.0f;
};

/** Read-only spatial hash over every road station - built once at BeginPlay
 *  (AODTerrainManager::LoadRoadStationSidecar, AODForestManager::
 *  LoadRoadStations) and never mutated again. AODTerrainManager shares its
 *  copy read-only into its tile-parse thread pool via a thread-safe
 *  TSharedPtr (see FStationSpatialHash's original comment in
 *  ODTerrainManager.h's history); AODForestManager only ever queries its own
 *  copy from the game thread, so it just holds a plain value. Cell size is
 *  independent of (much smaller than) either manager's own tile grid, purely
 *  so one query only has to touch a handful of cells instead of every
 *  station bucketed anywhere near a whole tile/SpawnRadius disc. */
struct FStationSpatialHash
{
	static constexpr float CellSizeCm = 10000.0f; // 100 m

	TArray<FRoadStation> Stations;
	TMap<FIntPoint, TArray<int32>> Cells;

	void Build(TArray<FRoadStation>&& InStations)
	{
		Stations = MoveTemp(InStations);
		Cells.Reset();
		for (int32 i = 0; i < Stations.Num(); ++i)
		{
			const FIntPoint Cell(FMath::FloorToInt(Stations[i].X / CellSizeCm),
			                      FMath::FloorToInt(Stations[i].Y / CellSizeCm));
			Cells.FindOrAdd(Cell).Add(i);
		}
	}

	/** Appends the index of every station whose cell overlaps
	 *  [MinX,MinY]-[MaxX,MaxY] into OutIndices - a coarse cell-overlap test,
	 *  not an exact circle/box test. Caller is expected to have already
	 *  padded the box by whatever exact-distance radius it cares about. */
	void QueryBox(float MinX, float MinY, float MaxX, float MaxY, TArray<int32>& OutIndices) const
	{
		const int32 CxMin = FMath::FloorToInt(MinX / CellSizeCm);
		const int32 CxMax = FMath::FloorToInt(MaxX / CellSizeCm);
		const int32 CyMin = FMath::FloorToInt(MinY / CellSizeCm);
		const int32 CyMax = FMath::FloorToInt(MaxY / CellSizeCm);
		for (int32 cy = CyMin; cy <= CyMax; ++cy)
		{
			for (int32 cx = CxMin; cx <= CxMax; ++cx)
			{
				if (const TArray<int32>* Bucket = Cells.Find(FIntPoint(cx, cy)))
				{
					OutIndices.Append(*Bucket);
				}
			}
		}
	}
};

/** PINNED format (roadstations_{area}.bin): "ODS2" magic (the "ODS1" prefix
 *  was reused from ODWallManager's walls_*.bin magic bytes - harmless, the
 *  two are never read by the same code path, callers key entirely off
 *  filename), then a
 *  LENGTH-PREFIXED UTF-8 generation id (NOT the fixed 16-byte ASCII-hex field
 *  ReadGenerationId() above expects - deliberately a different, simpler
 *  header shape, so this is parsed by hand rather than reusing that helper),
 *  then a station count and N flat (x,y,z,halfwidth) float32 quads in cm, UE
 *  world frame. The older "ODS1" (x,y,z) triple layout is still accepted with
 *  HalfWidthCm left at its 450 cm default (pre-lanes-tag pipeline output).
 *  Shared (2026-08-26) between AODTerrainManager (runtime carve) and
 *  AODForestManager (road-exclusion) - see FStationSpatialHash's comment for
 *  why each manager still builds its own hash rather than sharing one
 *  instance. */
inline bool ParseRoadStationSidecarFile(const FString& Path, TArray<FRoadStation>& OutStations, FString& OutGenId)
{
	TArray<uint8> Bin;
	if (!FFileHelper::LoadFileToArray(Bin, *Path) || Bin.Num() < 8
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'S'
		|| (Bin[3] != '1' && Bin[3] != '2'))
	{
		return false;
	}
	const bool bHasHalfWidth = (Bin[3] == '2');
	const int64 Stride = bHasHalfWidth ? 16 : 12;

	const uint8* Cur = Bin.GetData() + 4;
	const uint8* End = Bin.GetData() + Bin.Num();
	auto Remaining = [&]() -> int64 { return (int64)(End - Cur); };

	if (Remaining() < 4)
	{
		return false;
	}
	const uint32 GenIdLen = ReadU32LE(Cur); Cur += 4;
	if (Remaining() < (int64)GenIdLen)
	{
		return false;
	}
	FUTF8ToTCHAR Converter(reinterpret_cast<const ANSICHAR*>(Cur), GenIdLen);
	OutGenId = FString(Converter.Length(), Converter.Get());
	Cur += GenIdLen;

	if (Remaining() < 4)
	{
		return false;
	}
	const uint32 N = ReadU32LE(Cur); Cur += 4;
	const int64 NeedBytes = (int64)N * Stride;
	if (Remaining() < NeedBytes)
	{
		return false;
	}

	OutStations.SetNum(N); // NOT Uninitialized - ODS1 relies on the 450 cm default
	for (uint32 i = 0; i < N; ++i)
	{
		OutStations[i].X = ReadF32LE(Cur + i * Stride + 0);
		OutStations[i].Y = ReadF32LE(Cur + i * Stride + 4);
		OutStations[i].Z = ReadF32LE(Cur + i * Stride + 8);
		if (bHasHalfWidth)
		{
			OutStations[i].HalfWidthCm = ReadF32LE(Cur + i * Stride + 12);
		}
	}
	return true;
}
