#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODBinSidecarUtil.h" // FRoadStation/FStationSpatialHash/ParseRoadStationSidecarFile
#include "ODMenuVista.h"
#include "Async/Future.h"
#include "Templates/SharedPointer.h"
#include "UObject/WeakObjectPtrTemplates.h"
#include "ODTerrainManager.generated.h"

class UDynamicMeshComponent;
namespace UE::Geometry { class FDynamicMesh3; }
class UMaterialInterface;
class UMaterialInstanceDynamic;
class UTexture2D;

/** Per-area ground elevation bands (realism eval T1), area-local UE Z cm from
 *  Masks/groundbands_<area>_test.json (pipeline/build_ground_bands.py,
 *  climate-derived). Defaults (file missing) = "off": no treeline, snow or
 *  beach anywhere, same as the ground material's own param defaults. */
struct FODGroundBands
{
	static constexpr float Off = 1.0e7f;
	float SeaZCm = -Off;
	float TreelineLowCm = Off, TreelineHighCm = Off + 30000.0f;
	float ElevLowCm = Off, ElevHighCm = Off + 76500.0f;               // ApplyBiomeTint grey ramp
	float SnowSummerLowCm = Off, SnowSummerHighCm = Off + 45000.0f;   // AODSeasonController, SnowCover 0
	float SnowWinterLowCm = Off, SnowWinterHighCm = Off + 45000.0f;   // ... and SnowCover 1
	float LowGreenBias = 0.45f;   // ApplyBiomeTint's pull toward ElevLowBias (0 = summer-dry, T5)
};

/** One parsed terrain tile - plain data, safe to build off the game thread
 *  (no UObject/component touched). Mirrors the ODT1 sidecar payload layout
 *  (pipeline/build_terrain_mesh_tiles.py). */
struct FParsedTerrainTile
{
	bool bHasCollision = false;
	TArray<float> Verts;    // flat x,y,z (cm, UE world frame)
	TArray<float> Uvs;      // flat u,v
	TArray<uint32> Tris;    // flat i0,i1,i2

	// pipeline/generation_id.py's per-run id, read from the ODT1/ODT2 header
	// (2026-08-21) - see AODTerrainManager::OnTileParsed for the
	// cross-tile mismatch check this feeds.
	FString GenerationId;

	// Runtime carve (2026-08-26, Arc 7 rescope) output, Phase 2 only -
	// populated by AODTerrainManager::ApplyRoadCarve when
	// bEnableRuntimeCutWalls is on, empty otherwise. Flat x,y,z (cm) / tri
	// index triples for a vertical "cut face" ribbon quad strip - see
	// ApplyRoadCarve's comment for what this is (and, just as importantly,
	// what it is NOT - ODWallManager's own baked walls are a different
	// algorithm entirely).
	TArray<float> WallVerts;
	TArray<uint32> WallTris;

	// Runtime per-vertex ground tint (2026-08-26, terrain biome tint task) -
	// one sRGB FColor per Verts entry, populated by
	// AODTerrainManager::ApplyBiomeTint in the SAME async parse-thread pass
	// as ApplyRoadCarve above, when bEnableBiomeTint is on. Empty when the
	// tint pass is disabled or there was nothing nearby to tint against -
	// BuildTile leaves the mesh's color overlay unset in that case, which
	// reads as neutral (1,1,1,1) white to the material's vertex-color
	// multiply, i.e. a clean no-op back to the untinted look.
	TArray<FColor> VertColors;

	/** The render mesh, built on the parse thread after the tint (2026-10-09:
	 *  building it in BuildTile cost 20-60 ms per Near tile on the game
	 *  thread - Taylor's "frame skipping"). Null -> BuildTile builds it. */
	TSharedPtr<UE::Geometry::FDynamicMesh3> Mesh;
};

// FRoadStation/FStationSpatialHash now live in ODBinSidecarUtil.h
// (2026-08-26, carve-clamp/road-exclusion rescope) - AODForestManager builds
// its own copy of the same hash for road-exclusion, see that header's
// comment for why the type moved out of here.

/** One point-classified biome sample - mirrors AODForestManager's own
 *  FBiomePoint but carries only what the terrain vertex-tint pass needs.
 *  Biome is stored as a bare uint8 (the EODBiome ordinal) rather than
 *  including ODForestManager.h's enum in THIS header - ODTerrainManager.cpp
 *  casts it to EODBiome itself, next to its own ground-tint table, so this
 *  header doesn't pick up a cross-manager type dependency for one byte. */
struct FBiomeSample
{
	float X = 0.0f, Y = 0.0f; // cm, UE world frame
	uint8 Biome = 0;          // EODBiome ordinal (ODForestManager.h)
	// ODB2 (2026-09-25, next steps.md sec 7.35 V2) - ODB1 files leave these
	// at the neutral defaults. Northness: +1 steep north-facing, -1 steep
	// south-facing; Moisture: 1 = valley bottom, 0 = >=150 m above it.
	float Northness = 0.0f;
	float Moisture = 0.0f;
};

/** Read-only spatial hash over every classified biome point
 *  (Content/OpenDrive/Masks/biome_{area}_test.bin - pipeline/classify_biomes.py
 *  + export_biome_bin.py, the SAME sidecar AODForestManager's BlendAt() reads),
 *  built once in AODTerrainManager::LoadBiomeSidecar (BeginPlay) and shared
 *  read-only into the tile-parse thread pool exactly like FStationSpatialHash
 *  below - see that struct's comment, the reasoning is identical. Kept as a
 *  SEPARATE copy from AODForestManager's own linear-scan BiomePoints array:
 *  that one is only ever queried once per 96 m forest tile (BlendAt()), cheap
 *  even unbucketed, but this manager calls its own blend once per terrain
 *  VERTEX - up to NEAR_GRID_N^2 (257^2) per tile - so it needs the same
 *  cell-bucketed lookup FStationSpatialHash already uses for that reason. */
struct FBiomeSpatialHash
{
	// 200 m: biome points are sampled ~10x sparser than road stations
	// (classify_biomes.py samples every 10th road station) - a cell this
	// size still gives a query box a handful of candidates rather than an
	// empty or single-point bucket.
	static constexpr float CellSizeCm = 20000.0f;

	TArray<FBiomeSample> Points;
	TMap<FIntPoint, TArray<int32>> Cells;

	void Build(TArray<FBiomeSample>&& InPoints)
	{
		Points = MoveTemp(InPoints);
		Cells.Reset();
		for (int32 i = 0; i < Points.Num(); ++i)
		{
			const FIntPoint Cell(FMath::FloorToInt(Points[i].X / CellSizeCm),
			                      FMath::FloorToInt(Points[i].Y / CellSizeCm));
			Cells.FindOrAdd(Cell).Add(i);
		}
	}

	/** Same coarse cell-overlap semantics as FStationSpatialHash::QueryBox -
	 *  see that comment. */
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

/** Coarse = the cheap, no-collision "always there so there's no hole"
 *  variant; Near = full-res + collision, only worth paying for once the
 *  player is actually close enough to drive on it. A tile can be resident
 *  at either fidelity, upgraded/downgraded in place as the player's
 *  distance to it crosses AODTerrainManager::CollisionRadius - see
 *  AODTerrainManager::Tick. */
enum class ETileFidelity : uint8
{
	Coarse,
	Near,
};

/** Live terrain component for one loaded tile - a single regular-grid mesh,
 *  unlike AODRoadManager's per-tile chunk list (terrain tiles are one flat
 *  grid, not several independently-chunked road segments). */
struct FTerrainTile
{
	TObjectPtr<UDynamicMeshComponent> Comp;
	ETileFidelity Fidelity = ETileFidelity::Coarse;

	// Phase 2 runtime cut-wall ribbon (bEnableRuntimeCutWalls), null unless
	// that's on AND ApplyRoadCarve actually found a wall-worthy hard-clamped
	// edge on this tile - most tiles never populate this.
	TObjectPtr<UDynamicMeshComponent> WallComp;
};

/** A tile whose file has parsed and is waiting for its mesh/collision to
 *  actually be built, a few tiles per tick (see
 *  AODTerrainManager::ProcessPendingTileBuilds) instead of all at once -
 *  mirrors AODRoadManager's FPendingTileBuild/ProcessPendingChunkBuilds,
 *  added for the same reason (2026-08-25: avoid bursting dozens of
 *  same-frame UDynamicMeshComponent collision registrations). */
struct FPendingTerrainBuild
{
	FIntPoint Key = FIntPoint::ZeroValue;
	FParsedTerrainTile Parsed;
	ETileFidelity TargetFidelity = ETileFidelity::Coarse;
};

/**
 * Arc 7.0 pivot (2026-07-23): replaces the old live-streamed-terrain
 * plugin's approach with offline-baked, world-space-tiled terrain
 * geometry, streamed the same way AODRoadManager already streams road
 * tiles - ring-iteration/hysteresis spawn-despawn, tile parse off the game
 * thread via Async + AsyncTask. Every tile is baked once, offline, already
 * carved to the road's elevation profile and distance-gated (full-res +
 * collision near the route, coarse no-collision background farther out,
 * nothing built beyond render distance) - there is nothing to reconcile at
 * runtime, matching Tail of the Dragon's one-static-dataset model instead
 * of racing a live tileset's own async refinement the way the old
 * live-streaming plugin had to.
 */
UCLASS()
class OPENDRIVEUE_API AODTerrainManager : public AActor
{
	GENERATED_BODY()

public:
	AODTerrainManager();
	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
	virtual void Tick(float DeltaSeconds) override;

	/** Garage-menu vista copy (2026-10-05): set before FinishSpawning by
	 *  AODGameMode::SpawnVistaSet; streams around Vista.Focus, wedge only. */
	FODVistaRegion Vista;

	/** Sidecar filename prefix under Content/OpenDrive/Masks/; tile files
	 *  are "<prefix>_tile_<tx>_<ty>.bin". */
	UPROPERTY(EditAnywhere, Category = "Terrain")
	FString TerrainTilePrefix = TEXT("terrain_oahu_test");

	/** World-space grid size, cm. MUST match pipeline/test_areas.py's
	 *  TERRAIN_TILE_SIZE_CM - a mismatch doesn't crash, it just probes
	 *  filenames that don't exist and silently loads no terrain. */
	UPROPERTY(EditAnywhere, Category = "Terrain")
	float TileSizeCm = 200000.0f;

	/** Tiles built at Coarse fidelity (cheap, no collision) out to this
	 *  radius of the player (cm) - this is the "always something there, no
	 *  holes" visual horizon, not a driveable-surface guarantee. */
	UPROPERTY(EditAnywhere, Category = "Terrain")
	float VisualRadius = 600000.0f;

	/** Tiles inside this radius (cm) of the player are upgraded in place to
	 *  Near fidelity (full-res + collision); tiles built Near that fall
	 *  back outside it are downgraded back to Coarse. Deliberately much
	 *  smaller than VisualRadius - Arc 7 rescope (2026-08-25): only the
	 *  ground the player is actually about to drive on should pay for
	 *  full-res geometry + collision registration, not everything the
	 *  route happens to pass near within a multi-km radius.
	 *
	 *  Brought down from 300000 to 200000 (2026-08-25, second pass): the
	 *  near-tile upgrade ring is CeilToInt(CollisionRadius / TileSizeCm),
	 *  same guaranteed-minimum-ring math as AODRoadManager's TileSizeCm -
	 *  at 300000 against a 200000 TileSizeCm that's ceil(1.5)=2, a 5x5=25
	 *  tile guaranteed-minimum block. Terrain's own near-tile collision
	 *  components turned out to count toward the same open Chaos/UE5.8
	 *  collision-registration bug as road chunks (bisected against road
	 *  chunks alone, see ROAD_TILE_SIZE_CM's comment in test_areas.py, but
	 *  a live full-map retest after that fix still failed with near=25
	 *  terrain tiles + 73 road chunks resident) - so terrain's contribution
	 *  needed shrinking too, not just roads'. Setting CollisionRadius equal
	 *  to TileSizeCm instead gives ceil(1.0)=1, the true floor (3x3=9
	 *  tiles) for this fixed 200000 TileSizeCm (terrain isn't being
	 *  rebaked at a finer grid - unlike roads, it doesn't need to be once
	 *  its own ring drops to the 1-ring floor). */
	UPROPERTY(EditAnywhere, Category = "Terrain")
	float CollisionRadius = 200000.0f;

	/** Fixed world-space margin (cm) applied as hysteresis on both
	 *  VisualRadius (full unload) and CollisionRadius (fidelity
	 *  downgrade), so a tile right at a boundary doesn't thrash. */
	UPROPERTY(EditAnywhere, Category = "Terrain")
	float DespawnMarginCm = 100000.0f;

	/** Cap on new tile-load DISPATCHES per tick (not completions). */
	UPROPERTY(EditAnywhere, Category = "Terrain")
	int32 MaxNewLoadsPerTick = 2;

	/** Millisecond budget for tile BUILDS (mesh + collision) per tick, once
	 *  parsed - see ProcessPendingTileBuilds. Mirrors
	 *  AODRoadManager::ChunkBuildMsBudget's reasoning: bulk same-frame
	 *  UDynamicMeshComponent collision creation is the suspected trigger
	 *  for a still-open Chaos/UE5.8 physics-registration bug, so builds
	 *  (including fidelity upgrades/downgrades) are spread across ticks
	 *  even though load dispatch already is.
	 *
	 *  2026-08-27 (fast-drive hitch investigation): converted from a bare
	 *  tile-COUNT budget (was TileBuildsPerFrameBudget=2) to a time budget -
	 *  a fixed count doesn't actually bound frame time: a Near tile's dense
	 *  ~66k-vert adaptive mesh costs vastly more game-thread time to build
	 *  than a ~441-vert Coarse tile, so "2 tiles/frame" could mean anywhere
	 *  from under a millisecond to a real hitch depending on which 2
	 *  happened to be queued. ProcessPendingTileBuilds now keeps building
	 *  queued tiles while accumulated stage time stays under this budget,
	 *  always finishing at least one tile per tick so the queue can never
	 *  fully stall. Collision cook itself is additionally moved off this
	 *  budget's critical path via bUseAsyncCooking (see BuildTile) - see
	 *  that comment for why a bare ms cap on the synchronous portion still
	 *  wasn't enough on its own. */
	UPROPERTY(EditAnywhere, Category = "Terrain")
	float TileBuildMsBudget = 3.0f;

	/** Phase 2 (2026-08-26, runtime carve rescope): experimental, DEFAULT-OFF
	 *  runtime approximation of ODWallManager's offline-baked retaining
	 *  walls - emits a vertical "cut face" ribbon wherever this manager's
	 *  own runtime carve (ApplyRoadCarve) hard-clamps a vertex down by more
	 *  than RuntimeCutWallThresholdCm. This is deliberately NOT a port of
	 *  ODWallManager's real algorithm (road_mesh_common.classify_wall_
	 *  stations + build_retaining_walls.py's union-find/2-means/PCA-axis
	 *  ruled-surface mesh, driven by genuine cross-road/switchback elevation
	 *  CONFLICTS between two DIFFERENT roads' stations) - it's a much
	 *  simpler "does this tile's OWN carve cut in hard enough to look like a
	 *  cliff" heuristic against the SAME per-vertex carve this manager
	 *  already computes. Most ordinary road cuts don't trip it and are
	 *  correctly left as bare sloped terrain, matching the pipeline's own
	 *  "only genuine conflicts get a wall" behavior, but the two mechanisms
	 *  can and will occasionally disagree at the margins. Left off by
	 *  default so it can't visibly double up with (or clutter around)
	 *  ODWallManager's own tiles until someone's actually driven both side
	 *  by side - see the runtime-carve task report for the full reasoning. */
	UPROPERTY(EditAnywhere, Category = "Terrain|RuntimeWalls (experimental)")
	bool bEnableRuntimeCutWalls = false;

	/** Minimum (raw terrain Z - carved Z) at a hard-clamped vertex, cm,
	 *  before it's considered "wall-worthy" - see bEnableRuntimeCutWalls.
	 *  500 cm (5 m) default: comfortably above HARD_CLAMP_MARGIN_M's own
	 *  15 cm bias so ordinary shoulder carving never trips it. */
	UPROPERTY(EditAnywhere, Category = "Terrain|RuntimeWalls (experimental)")
	float RuntimeCutWallThresholdCm = 500.0f;

	/** How far BELOW the nearest station's elevation a hard-clamped vertex is
	 *  pinned, cm (2026-08-26, carve-clamp guarantee fix). Was a fixed 15 cm
	 *  (HARD_CLAMP_MARGIN_M, matching the pipeline's own offline bake) - bumped
	 *  up and made tunable because Taylor's live drive still found green
	 *  terrain patches poking through the pavement at that margin: 15 cm is
	 *  thin enough that render-target/collision float precision and the
	 *  vertex-normal-driven material blend could still read as "at/above
	 *  road" right at the footprint edge. Default 40 cm (~0.4 m) - tune by
	 *  feel from here rather than a recompile. */
	UPROPERTY(EditAnywhere, Category = "Terrain")
	float RoadCarveClampMarginCm = 40.0f;

	/** Re-carve every loaded tile against the station sidecar
	 *  (ApplyRoadCarve). Default OFF since 2026-09-28: the offline bake now
	 *  carves every non-structure road on the fine grid before martini
	 *  simplification, so a runtime re-carve only DISAGREED with it - it
	 *  lifted river/lake beds the bake had depressed (up to 27 m), pulled
	 *  terrain up toward bridge decks the bake skips, and could only move
	 *  vertices of big simplified triangles (the "terrain shards in the
	 *  road"). verify_road_fit.py now gates the bake itself at every
	 *  non-structure station. Kept as a toggle for A/B comparison. */
	UPROPERTY(EditAnywhere, Category = "Terrain")
	bool bEnableRuntimeRoadCarve = false;

	/** Temporary QA instrumentation (2026-08-26, carve-clamp guarantee fix):
	 *  when on, every NEAR-fidelity tile that carves against at least one
	 *  in-bounds road station gets a post-carve spot-check - see
	 *  VerifyCarveGuarantee's definition - that samples the just-carved
	 *  surface directly above a handful of that tile's own stations and logs
	 *  how many sit above (station elevation - RoadCarveClampMarginCm), i.e.
	 *  how many would still poke through the pavement. Should read zero
	 *  violations once the dynamic clamp-radius fix (see ApplyRoadCarve) is
	 *  working; left default-off since the brute-force per-triangle point
	 *  lookup it does isn't worth paying for once that's confirmed - flip on
	 *  with -odverifycarve on the command line instead of a recompile (see
	 *  BeginPlay), same convention as -odcollisionradius=. */
	UPROPERTY(EditAnywhere, Category = "Terrain")
	bool bVerifyCarveGuarantee = false;

	/** Runtime per-vertex ground tint (2026-08-26): biome + elevation +
	 *  road-distance + deterministic noise, blended into a per-vertex color
	 *  in the same async parse-thread pass as ApplyRoadCarve - see
	 *  ApplyBiomeTint. Default ON; M_GroundStylized (unreal/
	 *  apply_ground_material.py) was reworked to multiply its existing
	 *  grass/rock blend by this. Turning it off leaves every tile's color
	 *  overlay unpopulated, which the material's vertex-color multiply reads
	 *  as neutral white (no-op) - a clean A/B toggle against the old flat
	 *  look with no material swap needed. */
	UPROPERTY(EditAnywhere, Category = "Terrain|Tint")
	bool bEnableBiomeTint = true;

	/** Lerp factor between neutral white (0 - tint pass fully invisible even
	 *  though the color overlay IS populated) and the fully-computed tint
	 *  (1) - a finer-grained knob than the bool above for tuning how strong
	 *  the effect reads once it's confirmed to be running at all. */
	UPROPERTY(EditAnywhere, Category = "Terrain|Tint")
	float BiomeTintStrength = 1.0f;

	/** Loading-gate readiness check (2026-08-27, spawn-loading-screen task):
	 *  true once there's no parse/build work queued AND every tile key within
	 *  CollisionRadius of AroundPos is resident at Near fidelity - a tile
	 *  whose file simply doesn't exist on disk (MissingCoarseTiles/
	 *  MissingNearTiles) doesn't count against readiness, it's the expected
	 *  "nothing there" outcome for anything off the route corridor. Read-only,
	 *  cheap (bounded by the same small CollisionRadius/TileSizeCm box
	 *  Tick()'s own near-upgrade pass iterates), safe to poll every frame from
	 *  AODMainMenu while the loading screen is up. */
	bool IsInitialStreamingSettled(const FVector& AroundPos) const;

	/** Progress numbers for the loading screen's "terrain N/M" line - within
	 *  the same CollisionRadius box IsInitialStreamingSettled checks, M is the
	 *  box's tile count and N is how many of those are already resolved
	 *  (Near-resident, or confirmed to have no file at that fidelity). */
	void GetStreamingProgress(const FVector& AroundPos, int32& OutReady, int32& OutTotal) const;

	/** Seasons feature (2026-09-07): the ground material instance every tile's
	 *  Comp/WallComp actually renders with - see TerrainMID's own comment for
	 *  why this is a MID and not the raw TerrainMat asset. AODSeasonController
	 *  drives SnowLineLowZCm/SnowLineHighZCm/SpringGreen on this directly
	 *  rather than this manager owning any season logic itself (same
	 *  "controller pushes params into a manager-owned MID" split
	 *  AODDayNightController/AODAtmosphereController use for their own
	 *  targets). Null until BeginPlay has run (or if TerrainMat failed to
	 *  load) - callers must null-check. */
	UMaterialInstanceDynamic* GetGroundMID() const { return TerrainMID; }

	/** This area's ground bands in WORLD Z (actor offset applied) - what
	 *  AODSeasonController sweeps the snow line between. */
	const FODGroundBands& GetGroundBandsWorld() const { return GroundBandsWorld; }

	/** ParseTerrainTileFile's in-memory half: an ODT1 or ODT2 tile (2026-10-21,
	 *  ODT2 = zlib payload + derived UVs) into the same FParsedTerrainTile.
	 *  Public only so the OpenDrive.Terrain.ODT2 automation test can feed it
	 *  a buffer. */
	static bool ParseTerrainTileBytes(const TArray<uint8>& Bin, FParsedTerrainTile& Out);

private:
	static bool ParseTerrainTileFile(const FString& Path, FParsedTerrainTile& Out);
	// ParseRoadStationSidecarFile moved to ODBinSidecarUtil.h (2026-08-26) -
	// shared free function, AODForestManager now calls the same one.
	static bool ParseBiomeSidecarFile(const FString& Path, TArray<FBiomeSample>& OutPoints);

	/** Runtime port of pipeline/build_terrain_mesh_tiles.py's carve_grid_m()
	 *  - see the .cpp definition for the full port notes. Static + free of
	 *  any UObject/actor state (only Hash, a read-only thread-safe struct,
	 *  is touched) so it's safe to call from the same ThreadPool task that
	 *  already runs ParseTerrainTileFile, before the parsed tile is ever
	 *  handed back to the game thread.
	 *
	 *  Fidelity (2026-08-27, mid-distance road-float fix) widens the dynamic
	 *  hard-clamp radius further on Coarse tiles than on Near - Coarse's own
	 *  ~100 m vertex spacing (FAR_GRID_N=21 over a 2000 m tile) means the
	 *  1.5x-adjacent-edge multiplier that's already proven sufficient on
	 *  Near's dense, martini-adaptive grid (via -odverifycarve) can still
	 *  leave a coarse vertex the nearest-in-mesh candidate to a road corridor
	 *  stretch without actually landing inside the clamp radius - see the
	 *  .cpp definition's DynamicClampRadiusCm comment. OutMaxLiftCm is the
	 *  new sibling to OutMaxDropCm: the carve now also has a hard-clamp
	 *  RAISE (not just the pre-existing lower/cut clamp), which is what
	 *  actually closes daylight under a road sitting above too-low terrain
	 *  ("the road floats") rather than just guaranteeing terrain never pokes
	 *  up through the road. */
	static void ApplyRoadCarve(FParsedTerrainTile& Tile, const FIntPoint& Key, float TileSizeCm,
		const FStationSpatialHash& Hash, ETileFidelity Fidelity, bool bBuildWalls, float WallThresholdCm,
		float ClampMarginCm, int32& OutVertsTouched, float& OutMaxDropCm, float& OutMaxLiftCm,
		int32& OutHardClampedVerts);

	/** Statistically-impossible-vertex safety net (2026-08-27, phantom-shard
	 *  investigation): a vertex whose Z differs from EVERY one of its own
	 *  mesh-adjacent neighbors (via Tile.Tris, same "actual triangulation
	 *  adjacency" approach ApplyRoadCarve's MaxAdjEdgeCm already uses, since
	 *  the ODT1 vertex order is NOT documented as row-major - see
	 *  VerifyCarveGuarantee's comment) by more than ThresholdCm is repaired
	 *  in place to the average of those neighbor Zs, and logged (tile key,
	 *  vertex index, original Z, neighbor Zs). Investigated as the suspected
	 *  cause of a live-reported floating inverted-pyramid terrain shard near
	 *  a known open Z ~= -829.9 m phantom-geometry issue (see
	 *  AODForestManager's GroundSanityMaxDeltaCm comment) - an offline-bin
	 *  audit against the actual live tile data found ZERO such vertices, so
	 *  this is a safety net against a currently-unreproduced (data is clean
	 *  today) but plausible future/runtime cause, not a fix for a confirmed
	 *  data defect. Runs on the RAW parsed tile, before ApplyRoadCarve, so a
	 *  future corrupt vertex can't poison the carve's own neighbor-distance
	 *  math (MaxAdjEdgeCm) or its taper search. */
	static void RepairOutlierVertices(FParsedTerrainTile& Tile, const FIntPoint& Key, float ThresholdCm,
		int32& OutNumRepaired, float& OutMaxDeltaCm);

	/** Temporary QA spot-check (2026-08-26) - see bVerifyCarveGuarantee's
	 *  comment. Static + free of any UObject/actor state, same reasoning as
	 *  ApplyRoadCarve above, called right after it from the same ThreadPool
	 *  task on the ALREADY-CARVED Tile. Brute-force point-in-triangle search
	 *  (no assumption about the grid's row/col vertex ordering, unlike a
	 *  direct index computation) - fine for a handful of spot-check samples
	 *  per tile load, not something to run every tick. */
	static void VerifyCarveGuarantee(const FParsedTerrainTile& Tile, const FIntPoint& Key, float TileSizeCm,
		const FStationSpatialHash& Hash, float ClampMarginCm,
		int32& OutSampled, int32& OutViolations,
		float& OutMaxGapCm, float& OutSumGapCm);

	/** Runtime per-vertex ground tint (2026-08-26) - see FParsedTerrainTile::
	 *  VertColors and bEnableBiomeTint. Static + free of any UObject/actor
	 *  state (only the two read-only hash structs are touched) so it's safe
	 *  to call from the same ThreadPool task that already runs
	 *  ParseTerrainTileFile/ApplyRoadCarve. StationHash is reused rather than
	 *  re-queried by the carve step - same station data, a second cheap
	 *  QueryBox call, purely for the "how far is this vertex from a road"
	 *  signal the carve itself has no reason to expose. Out* stats feed a
	 *  gated per-tile log line proving the pass runs with plausible
	 *  variation - see RequestTileLoad's call site. */
	static void ApplyBiomeTint(FParsedTerrainTile& Tile, const FIntPoint& Key, float TileSizeCm,
		const FBiomeSpatialHash& BiomeHash, const FStationSpatialHash& StationHash,
		float TintStrength, const FODGroundBands& Bands, int32& OutVertsTinted, int32& OutDistinctBiomes,
		float& OutMinLuma, float& OutMaxLuma);

	void LoadRoadStationSidecar();
	void LoadBiomeSidecar();

	/** horizon_<area>_test.bin (pipeline/build_horizon.py, realism eval T4):
	 *  one low-res mesh from the baked tile extent out to +80 km, sunk under
	 *  every streamed tile - the world past VisualRadius. No collision/shadow. */
	void LoadHorizon();
	UPROPERTY()
	TObjectPtr<UDynamicMeshComponent> HorizonComp;

	/** Drawn-area boundary look (2026-10-21, area picker; ODBakeLink.h):
	 *  M_ODUnsynced (unreal/apply_boundary_materials.py) on material slot 1 -
	 *  near tiles' triangles outside the outline (Masks/outline_* SDF), the
	 *  horizon's outside the outline or in a cell still baking. Tiles that came
	 *  back Pending get a dissolving copy of themselves when they land. Null
	 *  material (script not run) = none of it, the old look. */
	UPROPERTY()
	TObjectPtr<UMaterialInterface> UnsyncedMat;
	TSet<FIntPoint> WasPendingTiles;
	uint32 HorizonStatusVersion = 0;
	double NextHorizonCheck = 0.0;
	void RetagHorizon();
	void SpawnSyncIn(const UDynamicMeshComponent* Src);

	/** groundbands_<area>_test.json -> GroundBands (tile-local, for the tint)
	 *  + GroundBandsWorld, and the TreelineLow/HighZCm + SeaLevelZCm params on TerrainMID. */
	void LoadGroundBands();
	FODGroundBands GroundBands;
	FODGroundBands GroundBandsWorld;

	/** PLAN-VISUALS Track L (Realistic only): Sentinel-2 ground albedo for the
	 *  whole area, sat_<area>_test.png + .json in Masks/ (pipeline/
	 *  build_sat_albedo.py), bound once on TerrainMID as SatTex/SatOrigin/
	 *  SatSizeCm and SatAmount = 1. One texture over the baked extent, so
	 *  tile borders are seamless; missing files leave SatAmount at its 0
	 *  default = the old look.
	 *  Realism eval T6: same contract for pave_<area>_test.* (pipeline/
	 *  build_pave_mask.py) -> PaveTex/PaveOrigin/PaveSizeCm/PaveAmount, the
	 *  material's paved-ground layer; dem_<area>_test.* (pipeline/
	 *  build_terrain_relief.py) -> DemTex/..., slope detail + gully/ridge.
	 *  Returns the bound texture or null. */
	UTexture2D* BindAreaTexture(const TCHAR* Kind, const TCHAR* Param);
	UPROPERTY()
	TObjectPtr<UTexture2D> SatTex;
	UPROPERTY()
	TObjectPtr<UTexture2D> PaveTex;
	UPROPERTY()
	TObjectPtr<UTexture2D> DemTex;

	void RequestTileLoad(const FIntPoint& Key, ETileFidelity Fidelity);
	void OnTileParsed(const FIntPoint& Key, ETileFidelity Fidelity, bool bOk, FParsedTerrainTile&& Parsed);
	void ProcessPendingTileBuilds();
	void BuildTile(FTerrainTile& Tile, const FParsedTerrainTile& Parsed, ETileFidelity Fidelity);
	void ReleaseTile(const FIntPoint& Key);

	TMap<FIntPoint, FTerrainTile> Tiles;

	// tiles whose file has parsed and are queued for their actual
	// mesh/collision build, a few per tick - see ProcessPendingTileBuilds
	TArray<FPendingTerrainBuild> PendingTileBuilds;

	// dispatched (parse in flight, OR parsed and sitting in
	// PendingTileBuilds awaiting its build) at this fidelity - a key can
	// be pending at one fidelity while already resident at the other
	// (e.g. Near-resident tile with a Coarse downgrade in flight)
	TSet<FIntPoint> PendingCoarseTiles;
	TSet<FIntPoint> PendingNearTiles;

	// confirmed no file at this key+fidelity (ENOENT or bad header) - don't
	// retry. MissingCoarseTiles is the natural "no hole, just don't build
	// it" outcome for any tile beyond the pipeline's render-distance
	// cutoff; MissingNearTiles is the normal, expected state for every
	// plain far tile (they never had a near variant to begin with), so a
	// key can sit in both, one, or neither of these sets.
	TSet<FIntPoint> MissingCoarseTiles;
	TSet<FIntPoint> MissingNearTiles;

	// generation-id cross-tile check (2026-08-21, AUDIT-RELIABILITY.md
	// SS3.1/SS5.1): the id of the first tile this manager successfully
	// parsed for TerrainTilePrefix - every later tile is compared against
	// it, and a mismatch (tiles from two different pipeline runs streaming
	// side by side) is logged once, not blocked.
	FString FirstGenerationId;
	bool bHasFirstGenerationId = false;
	bool bGenerationMismatchWarned = false;

	UPROPERTY()
	TObjectPtr<UMaterialInterface> TerrainMat;

	/** Seasons feature (2026-09-07): a single MID created once from TerrainMat
	 *  in BeginPlay and applied to every tile's Comp/WallComp INSTEAD OF the
	 *  raw TerrainMat asset (SetMaterial still called per-component the same
	 *  as before, just with this shared instance) - a plain UMaterialInterface
	 *  asset reference can't take a runtime SetScalarParameterValue() call,
	 *  and every tile sharing ONE dynamic instance (rather than one MID per
	 *  tile) keeps the seasonal snow-line sweep a single cheap call per tick
	 *  from AODSeasonController instead of one per resident tile. Null if
	 *  TerrainMat failed to load (same silent-no-op convention as TerrainMat
	 *  itself). */
	UPROPERTY()
	TObjectPtr<UMaterialInstanceDynamic> TerrainMID;

	// Runtime terrain carve (2026-08-26, Arc 7 rescope): loaded once in
	// BeginPlay from roadstations_{area}.bin, never mutated again - shared
	// (thread-safe, read-only) into every tile-parse ThreadPool task via
	// ApplyRoadCarve. Null if the sidecar was missing/corrupt at startup -
	// RequestTileLoad's carve step is a no-op in that case (terrain still
	// streams, just uncarved beyond whatever the offline bake already did).
	TSharedPtr<FStationSpatialHash, ESPMode::ThreadSafe> StationHash;

	// Shoulder strip (2026-10-07): pavement-edge distance field around the
	// car, RoadDistTexels^2 at RoadDistTexelCm, rebuilt on the thread pool
	// whenever the car drifts RoadDistRecenterCm from its center and pushed
	// into TerrainMID as RoadDistTex/RoadDistOrigin - M_GroundStylized draws
	// the gravel band from it (apply_ground_material.py SHOULDER_*). Linear
	// distance / RoadDistRangeCm, sRGB-encoded so the sampler hands the
	// material the linear value back (ROAD_DIST_RANGE_M there).
	static constexpr int32 RoadDistTexels = 1024;
	static constexpr float RoadDistTexelCm = 50.0f;      // 512 m coverage
	static constexpr float RoadDistRangeCm = 1600.0f;
	static constexpr float RoadDistRecenterCm = 3200.0f;
	UPROPERTY()
	TObjectPtr<UTexture2D> RoadDistTex;
	FVector2D RoadDistCenter = FVector2D(1e12, 1e12);
	FVector2D RoadDistJobCenter = FVector2D::ZeroVector;
	TFuture<TArray<FColor>> RoadDistJob;
	void UpdateRoadDistTex(const FVector& Loc);
	static TArray<FColor> BuildRoadDistTexels(const FStationSpatialHash& Hash, const FVector2D& Center);

	// Runtime ground tint (2026-08-26): loaded once in BeginPlay from
	// biome_{area}_test.bin, never mutated again - shared (thread-safe,
	// read-only) into every tile-parse ThreadPool task via ApplyBiomeTint.
	// Null if the sidecar was missing/corrupt at startup - the tint pass
	// still runs in that case (elevation/road/noise signals only), just
	// without a real per-point biome color to blend.
	TSharedPtr<FBiomeSpatialHash, ESPMode::ThreadSafe> BiomeHash;

	double LastDiagTime = 0.0;
};
