#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODBinSidecarUtil.h" // FRoadStation/FStationSpatialHash/ParseRoadStationSidecarFile
#include "ODForestManager.generated.h"

class UCapsuleComponent;
class UHierarchicalInstancedStaticMeshComponent;
class UMaterialInterface;
class UMaterialInstanceDynamic;
class UStaticMesh;

/** A named regional flora recipe (Arc 7.3 "per-region foliage recipes"),
 *  built from the existing 3-species/1-bush asset set - no new meshes
 *  required. BiomeAt() is the seam a future per-tile land-cover classifier
 *  (ESA WorldCover, see next steps.md sec 7.3) plugs into; until that
 *  exists every tile just uses DefaultBiome. Traction/audio stay collapsed
 *  to a road/off-road split (2026-07-20) - this only drives visuals. */
UENUM()
enum class EODBiome : uint8
{
	DeciduousForest,    // Blob-heavy, dense - Southeast/Northeast canopy
	EvergreenForest,    // Conifer-heavy, dense - Pacific NW/Rockies/north
	Chaparral,          // Olive+bush, medium - CA coastal scrub hills
	DesertScrub,        // Olive+bush, sparse - SW high desert
	PlainsGrassland,    // mostly bush, sparse trees - Midwest prairie
	Wetland,            // Blob+Conifer mix, medium-dense - SE swamp/marsh
	AlpineBare,         // near-barren - high mountains, badlands, rock
	ArcticTundra,       // near-barren, sparse low bush - far north, no trees
	Farmland,           // sparse bush (hedgerows/fencelines), open fields
	Savanna,            // sparse scattered trees over grass - E Africa feel
	Rainforest,         // Blob-heavy, densest - tropical jungle canopy
	CoastalDune,        // sparse bush only - beach/dune grass, no trees
	CanyonBadlands,     // near-barren, sparse olive scrub - Dragon-style rock cut
	RockyFoothill,      // Conifer+olive, sparse-medium - plains/alpine transition
};

/** One biome's recipe against the extended 7-species/2-shrub mesh set -
 *  actual per-biome values live in ODForestManager.cpp's BiomeRecipeTable
 *  (kept there, not here, since nothing outside this module needs it -
 *  only the TYPE needs to be header-visible, for RecipeAt()'s signature).
 *
 *  Extended 2026-08-27 (Taylor's live-drive report: "chaparral isn't
 *  working and all the trees are the same - the color and shape") from the
 *  original 3-species/1-bush set: every biome used to only modulate density
 *  and ground tint while spawning the identical 3 canopy meshes, so no
 *  biome had a distinct SILHOUETTE. SpeciesWeight now covers 7 tree/woody
 *  meshes (see make_tree_meshes.py's biome-distinct flora addition) and
 *  ShrubWeight picks between the 2 no-collision shrub-class meshes (plain
 *  bush vs. sagebrush) the same way SpeciesWeight already picked among
 *  trees. */
struct FBiomeRecipe
{
	// Blob, Olive, Conifer, ScrubOak, Manzanita, Yucca, CanyonOak - order
	// MUST match AODForestManager::TreeMeshes (loaded from
	// ExtendedTreeMeshPaths in ODForestManager.cpp). All 7 are "woody":
	// BuildTile() gives every one of them a trunk collision capsule, same as
	// the original 3.
	float SpeciesWeight[7] = {1.f, 0.f, 0.f, 0.f, 0.f, 0.f, 0.f};
	// Bush, Sagebrush - order MUST match AODForestManager::ShrubMeshes.
	// Neither gets trunk collision (same as the plain bush always has) -
	// sagebrush is shrub-class specifically so it never enters the
	// tree/capsule path.
	float ShrubWeight[2] = {1.f, 0.f};
	float TreeDensityScale = 1.f;
	float BushDensityScale = 1.f;
	// Ground cover (2026-08-26, Arc 7.3 extension): rocks/boulders + dense
	// low scrub, same tile-seeded scatter as trees/bushes above - see
	// RocksPerSqM/ScrubPerSqM for the per-m^2 baseline these multiply.
	float RockDensityScale = 0.f;
	float ScrubDensityScale = 0.f;
	// Ambient open-ground vegetation floor (2026-08-27, "no vegetation at
	// all in the desert" fix): BuildTile() normally gates every tree/bush
	// candidate's acceptance odds behind the per-tile ForestWeight read from
	// classify_biomes.py's imagery classification - a canopy-density signal
	// that correctly reads near-zero over genuine open desert/scrub ground
	// (no tree canopy detected), but was then wrongly treated as "spawn
	// nothing here at all": live logs showed CanyonBadlands tiles landing
	// ALL SEVEN tree species AND both shrub species at exactly zero, even
	// though BiomeRecipeTable's own SpeciesWeight/ShrubWeight/
	// TreeDensityScale/BushDensityScale for that biome are nonzero - only
	// the rock scatter (gated by slope, not ForestWeight) came through. This
	// floor is used as a MINIMUM acceptance probability alongside (not
	// instead of) the real per-tile ForestWeight (see BuildTile()'s
	// `FMath::Max(Recipe.AmbientVegFloor, Weight)`), so open-ground biomes
	// still get a low, deliberately sparse scatter of shrub (and via
	// SpeciesWeight, occasional yucca/canyon-oak) - a shrub every ~10-30 m,
	// not a forest - instead of literally nothing. Zero (default) preserves
	// the original canopy-gated behavior for every forest/wetland/etc biome,
	// where a true ForestWeight of 0 really does mean "no vegetation here"
	// (e.g. a lake, bare rock, town square).
	float AmbientVegFloor = 0.f;
};

/**
 * Runtime forest (replaces landscape-grass trees, 2026-07-15): deterministic
 * seeded scatter of trees + understory bushes on 96 m tiles around the player,
 * driven by a forest-weight + biome signal from one of two sources: a
 * Landscape-actor-bound raster mask (Tail of the Dragon, the original path)
 * or point-classified biome data with no Landscape required (open-world
 * levels, added 2026-07-20 - see BlendAt()). Owning the instances
 * fixes what landscape grass could not give us:
 *  - trunk collision (we know every transform; a capsule pool parks on the
 *    nearest trunks),
 *  - foliage in standalone/packaged builds (grass maps are editor-only),
 *  - regional flora recipes (per-tile species mix, inverse-distance-blended
 *    between classified points so driving between biomes reads as a
 *    gradual transition, not a hard cut at a tile boundary).
 * Zero per-instance storage: layout derives from tile-seeded RNG + the mask.
 * Each tile owns its HISM components, so retiring a tile is just component
 * teardown - no instance-id bookkeeping.
 */
UCLASS()
class OPENDRIVEUE_API AODForestManager : public AActor
{
	GENERATED_BODY()

public:
	AODForestManager();
	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

	/** Read-only accessor into BiomeRecipeTable (ODForestManager.cpp,
	 *  anonymous-namespace, not header-visible) - added 2026-08-27 so
	 *  ODBarrierManager's own biome-aware windbreak species pick reuses the
	 *  SAME per-biome species-weight tuning this manager's own BuildTile()
	 *  uses, instead of duplicating (and inevitably drifting from) that
	 *  table. Index is bounds-checked the same way BuildTile()'s own lookups
	 *  are, via BiomeRecipeTable's static_assert against EODBiome's range. */
	static const FBiomeRecipe& RecipeForBiome(EODBiome Biome);

	/** Tiles fully populated inside this radius of the player (cm). Trimmed
	 *  480 m -> 300 m (2026-07-25): with ForestBandCm now bounding trees to a
	 *  corridor around the road, the outer disk was empty scatter attempts
	 *  anyway, and the smaller radius keeps any far background terrain (whose
	 *  smoothed height disagrees with the tree elevation grid) out of frame.
	 *  Raised 300 m -> 500 m (2026-08-24, Taylor: "no flora at all outside of
	 *  the immediate vicinity. looks barren.") - since ForestBandCm keeps
	 *  trees corridor-bound regardless of SpawnRadius, the extra radius is
	 *  cheap (still mostly empty scatter attempts off the corridor) and only
	 *  buys visibility further down the road. Kept comfortably under
	 *  build_forest_elevation.py's FOREST_RADIUS_M=550 m ground-truth
	 *  coverage so height lookups near the new edge still land inside real
	 *  baked data instead of falling back to guesses. */
	UPROPERTY(EditAnywhere, Category = "Forest")
	float SpawnRadius = 50000.0f;

	/** Trees per m^2 at forest weight 1. Thinned 0.075 -> 0.05 (2026-07-25) so
	 *  the roadside canopy reads as forest, not a wall right over the camera. */
	UPROPERTY(EditAnywhere, Category = "Forest")
	float TreesPerSqM = 0.05f;

	/** Max lateral distance (cm) from the road centerline a tree/bush may
	 *  plant (2026-07-25). Trees are placed at their true height from the
	 *  dense ground-truth elevation grid, which matches the carved near-tier
	 *  terrain in the road corridor but NOT the smoothed/coarse far-tier
	 *  background terrain further out - so anything scattered far from the road
	 *  visibly floats above (or sinks into) the low-detail terrain drawn there.
	 *  Bounding scatter to the accurate corridor fixes that and opens up the
	 *  view above/beyond the roadside. Only used when CenterlineFilePrefix is
	 *  set (open-world levels); Tail of the Dragon leaves it unset and is
	 *  unaffected. */
	UPROPERTY(EditAnywhere, Category = "Forest")
	float ForestBandCm = 12000.0f;   // 120 m

	/** Understory bushes per m^2 at forest weight 1. */
	UPROPERTY(EditAnywhere, Category = "Forest")
	float BushesPerSqM = 0.03f;

	// --- ground cover (2026-08-26): rocks/boulders + dense low scrub, added
	// so hillsides read as natural terrain instead of empty green - same
	// tile-seeded scatter pattern as trees/bushes, spawned alongside them in
	// BuildTile(). Zero per-instance storage, same as everything else here. ---

	/** Master switch - false disables ground cover scatter entirely (trees/
	 *  bushes are unaffected). */
	UPROPERTY(EditAnywhere, Category = "Ground Cover")
	bool bEnableGroundCover = true;

	/** Global multiplier over every biome's RockDensityScale/ScrubDensityScale
	 *  - a single tunable to back off ground cover overall without touching
	 *  the per-biome table. */
	UPROPERTY(EditAnywhere, Category = "Ground Cover")
	float GroundCoverDensityScale = 1.0f;

	/** Rocks per m^2 at RockDensityScale 1. Rocks are gated by local slope
	 *  (see RockSlopeMinRatio/RockSlopeMaxRatio), not forest weight - they
	 *  belong on exposed, steeper ground where trees naturally thin out. */
	UPROPERTY(EditAnywhere, Category = "Ground Cover")
	float RocksPerSqM = 0.02f;

	/** Extra low scrub (grass_tuft) per m^2 at ScrubDensityScale 1 - denser,
	 *  shorter ground fill than understory bushes, gated the same way bushes
	 *  are (forest weight). */
	UPROPERTY(EditAnywhere, Category = "Ground Cover")
	float ScrubPerSqM = 0.06f;

	/** Rock uniform-scale range before per-axis jitter is applied (see
	 *  BuildTile()) - kept wide since the mesh set is just two engine basic
	 *  shapes (no rock mesh exists in Content/, see BeginPlay()) and variety
	 *  has to come entirely from scale/rotation. */
	UPROPERTY(EditAnywhere, Category = "Ground Cover")
	float RockMinScale = 0.35f;

	UPROPERTY(EditAnywhere, Category = "Ground Cover")
	float RockMaxScale = 2.2f;

	/** Local slope (rise/run ratio, e.g. 0.3 = ~17 deg) below which a rock
	 *  candidate only gets RockFlatChance's acceptance odds - flat ground
	 *  still gets occasional scattered boulders, just far fewer than a
	 *  hillside. */
	UPROPERTY(EditAnywhere, Category = "Ground Cover")
	float RockSlopeMinRatio = 0.04f;

	/** Local slope at/above which a rock candidate's acceptance odds reach
	 *  1.0 - between RockSlopeMinRatio and this, odds ramp smoothly. */
	UPROPERTY(EditAnywhere, Category = "Ground Cover")
	float RockSlopeMaxRatio = 0.35f;

	/** Acceptance odds for a rock candidate on ground at/below
	 *  RockSlopeMinRatio - not zero, so open fields/plains still show the
	 *  occasional glacial erratic instead of a hard cutoff at the slope
	 *  threshold. */
	UPROPERTY(EditAnywhere, Category = "Ground Cover")
	float RockFlatChance = 0.12f;

	UPROPERTY(EditAnywhere, Category = "Forest")
	int32 RandomSeed = 20260715;

	/** Used for every tile until BiomeAt() has a real per-tile classifier
	 *  (Arc 7.3) to read instead. DeciduousForest, not the open-world test
	 *  area's Chaparral-like character - this manager currently only runs
	 *  on Tail of the Dragon (LoadMask() requires an ALandscapeProxy, which
	 *  the open-world test level doesn't have), and the Dragon's real
	 *  location (US-129, Deals Gap, TN/NC) is Southeast Appalachian mixed
	 *  forest. */
	UPROPERTY(EditAnywhere, Category = "Forest")
	EODBiome DefaultBiome = EODBiome::DeciduousForest;

	/** Content/OpenDrive/Masks/<prefix>.bin - the point-classified biome
	 *  sidecar (pipeline/classify_biomes.py + export_biome_bin.py), the
	 *  open-world path that doesn't need an ALandscapeProxy. Empty
	 *  (default) means "no point data, use the Landscape-mask path" -
	 *  Tail of the Dragon's actor instance leaves this blank and is
	 *  unaffected by anything below. Set by the level-builder script for
	 *  open-world levels (mirrors AODRoadManager::RoadTilePrefix's
	 *  convention). */
	UPROPERTY(EditAnywhere, Category = "Forest")
	FString BiomeFilePrefix;

	/** Points beyond this radius don't contribute to a query's
	 *  inverse-distance blend - without a cutoff, one classified point
	 *  hundreds of km away would still nudge every query's weighted
	 *  average by a nonzero (if tiny) amount. */
	UPROPERTY(EditAnywhere, Category = "Forest")
	float BiomeBlendRadiusCm = 30000.0f;   // 300 m

	/** Content/OpenDrive/Masks/<prefix>.bin - a dense ground-truth
	 *  elevation grid (pipeline/build_forest_elevation.py) baked offline
	 *  from the same verified DEM data the road/terrain pipeline already
	 *  uses, covering SpawnRadius around the whole corridor. Replaces a
	 *  physics raycast against whatever streamed terrain LOD happens to be
	 *  resident (found 2026-07-20: floating tree PATCHES - a raycast can
	 *  hit a stale/coarse LOD, or genuinely wrong low-zoom source data far
	 *  from the road, and once a tree's transform is baked it never moves
	 *  even after the terrain visually catches up). Empty means "no grid,
	 *  use the raycast path" - Tail of the Dragon's actor instance leaves
	 *  this blank and is unaffected. */
	UPROPERTY(EditAnywhere, Category = "Forest")
	FString ElevationFilePrefix;

	/** Content/OpenDrive/Masks/<prefix>.bin - the road's precise centerline
	 *  (pipeline/export_road_centerline_bin.py, full 5 m station spacing),
	 *  used to reject tree candidates by geometric distance instead of a
	 *  physics raycast against the road mesh's PM_Asphalt tag. Empty means
	 *  "no centerline, use the raycast path" - same fallback convention as
	 *  ElevationFilePrefix. */
	UPROPERTY(EditAnywhere, Category = "Forest")
	FString CenterlineFilePrefix;

	/** Total clearance (cm) from the road centerline before a tree/bush is
	 *  allowed to plant - covers the physical pavement+skirt half-width
	 *  (road_mesh_common.py's ROAD_HALF_WIDTH + SKIRT_EXTRA, 7.5 m) plus a
	 *  visual buffer so trees don't crowd the shoulder (4 m). Only used
	 *  when CenterlineFilePrefix is set. */
	UPROPERTY(EditAnywhere, Category = "Forest")
	float PavementExclusionCm = 1150.0f;   // 11.5 m

	/** Content/OpenDrive/Masks/<prefix>.bin - the SAME road-station sidecar
	 *  AODTerrainManager's runtime carve reads (roadstations_{area}.bin,
	 *  ODS1 format, 5 m station spacing along every road centerline,
	 *  pipeline/build_road_stations.py) - a deterministic, data-driven
	 *  road-exclusion check independent of CenterlineFilePrefix above and of
	 *  GroundAt()'s PM_Asphalt physmat trace (see IsNearRoadStation()).
	 *
	 *  Added 2026-08-26 (Taylor's live-drive report: "trees sometimes spawn
	 *  ON TOP of the road"): the PM_Asphalt check only fires on the
	 *  raycast-path branch of GroundAt(), and only if the trace actually
	 *  hits pavement first - AODTerrainManager's own carve-clamp bug (see
	 *  its RoadCarveClampMarginCm comment) let terrain poke up ABOVE the
	 *  road in places, so the trace hit grass instead and a tree got planted
	 *  mid-road. CenterlineFilePrefix's own exclusion (IsNearCenterline)
	 *  would have caught this too, but it's gated behind bUseElevationGrid
	 *  (see GroundAt()) and Green Valley has no elev_green_valley_test.bin
	 *  baked yet - see ODGameMode.cpp's own comment on that gap - so that
	 *  path is currently dead for this area's raycast fallback. This check
	 *  runs unconditionally in GroundAt() regardless of which ground-height
	 *  path is active, so it isn't blocked by the same gap. Empty (default)
	 *  means "no station data, skip this check" - set by ODGameMode.cpp the
	 *  same way BiomeFilePrefix/CenterlineFilePrefix are. */
	UPROPERTY(EditAnywhere, Category = "Forest")
	FString RoadStationFilePrefix;

	/** Total clearance (cm) from the nearest road station's XY before a
	 *  tree/bush/rock/scrub candidate is rejected - see RoadStationFilePrefix.
	 *  750 (ROAD_HALF_WIDTH+SKIRT_EXTRA, matches AODTerrainManager's own
	 *  HardClampRadiusCm floor) + 250 cm visual buffer = 10 m total, per the
	 *  task spec ("road half-width + skirt + margin, ~10m total"). */
	UPROPERTY(EditAnywhere, Category = "Forest")
	float RoadStationExclusionCm = 1000.0f;   // 10 m

	/** Wider clearance (cm) used instead of RoadStationExclusionCm when a
	 *  candidate's 100 m station-hash cell looks like a junction cluster (see
	 *  JunctionClusterStationCountThreshold) - added 2026-08-27 (Taylor's
	 *  live-drive report on the freshly-rebaked "green_valley" expansion:
	 *  trees on/against junction pads). Junction pads
	 *  (pipeline/experimental_junction_pads.py) are baked well beyond any
	 *  individual station's 10 m footprint, AND their collision is the known
	 *  flaky Chaos/UE5.8 same-frame-registration bug this file's
	 *  BuildOneChunk()/OnTileParsed() comments already document at length -
	 *  when a pad chunk's physics body silently fails to register, GroundAt()'s
	 *  PM_Asphalt raycast check can't fire at all (the trace passes through to
	 *  whatever is underneath), so station-distance is the only exclusion that
	 *  still works. This purely data-driven radius bump doesn't depend on
	 *  physics registration succeeding at all. Only 5 m past the base radius -
	 *  "modestly larger", not a blanket widening that would thin ordinary
	 *  roadside canopy. */
	UPROPERTY(EditAnywhere, Category = "Forest")
	float JunctionClusterExclusionCm = 1500.0f;   // 15 m

	/** Station count within one FStationSpatialHash 100 m cell (StationHash's
	 *  fixed CellSizeCm) at/above which that cell is treated as a junction
	 *  cluster rather than an ordinary single-road pass-through, and
	 *  JunctionClusterExclusionCm applies instead of RoadStationExclusionCm.
	 *  Baseline: stations sit every 5 m along one centerline, so a single road
	 *  crossing a 100 m cell contributes roughly 20-28 stations (more on a
	 *  diagonal/curved pass) even with no junction nearby - set comfortably
	 *  above that (2+ roads' worth) so ordinary roads never trip this, while a
	 *  junction (2+ centerlines converging in the same cell) reliably does. */
	UPROPERTY(EditAnywhere, Category = "Forest")
	int32 JunctionClusterStationCountThreshold = 36;

	/** Content/OpenDrive/Masks/<prefix>.bin - building centroid points
	 *  (pipeline/export_building_centroids_bin.py, same ODC1 point-list
	 *  format as CenterlineFilePrefix - it's just X/Y points either way).
	 *  Found necessary 2026-07-24 (Arc 7.0 step 3): the biome classifier has
	 *  no concept of "this is a town" and was scattering max-density forest
	 *  straight through Mount Vernon, completely burying every building
	 *  behind a wall of trees. Empty means no exclusion (every level before
	 *  step 3 had no buildings to avoid, so this is unset for them). */
	UPROPERTY(EditAnywhere, Category = "Forest")
	FString BuildingCentroidFilePrefix;

	/** Clearance (cm) from a building centroid before a tree/bush is
	 *  allowed to plant - a broad-brush per-centroid radius, not exact
	 *  per-footprint polygon containment (matches this project's "coarse
	 *  first pass" convention elsewhere, e.g. classify_biomes.py). Sized
	 *  generously above a typical single-building footprint so a few
	 *  adjacent downtown buildings' radii overlap into one continuous
	 *  tree-free patch instead of leaving trees standing in the gaps
	 *  between them. */
	UPROPERTY(EditAnywhere, Category = "Forest")
	float BuildingExclusionCm = 1800.0f;   // 18 m

	/** Beyond this distance (cm) from the nearest building, forest density
	 *  is unaffected. Found necessary 2026-07-24 alongside
	 *  BuildingExclusionCm: that hard exclusion alone still left a solid,
	 *  visually-blocking treeline between the road (only PavementExclusionCm,
	 *  11.5 m, clear) and buildings 30-100+ m back from it - the biome
	 *  classifier's forest_weight has no "this general area is a town"
	 *  concept at all, just a per-point imagery read. This is a wider,
	 *  SOFT dampening (not a hard exclusion) applied once per 96 m tile in
	 *  BlendAt(), not per scatter candidate - cheap even at this radius. */
	UPROPERTY(EditAnywhere, Category = "Forest")
	float TownDampenRadiusCm = 25000.0f;   // 250 m

	/** Forest-weight multiplier at/inside BuildingExclusionCm, ramping
	 *  smoothly back up to 1.0 (no effect) at TownDampenRadiusCm - thins
	 *  the approach into town instead of leaving a solid tree wall right
	 *  at the exclusion boundary. */
	UPROPERTY(EditAnywhere, Category = "Forest")
	float TownDampenMinFactor = 0.08f;

	/** Content/OpenDrive/Masks/<prefix>_tile_<tx>_<ty>.bin - the SAME baked
	 *  lake sidecar tiles AODWaterManager streams (ODL1 format,
	 *  pipeline/build_water_tiles.py) - reused read-only here purely for
	 *  their polygon geometry, no water rendering. Added 2026-09-03
	 *  (reviewer defect: a whole stand of trees/bushes found standing in
	 *  open water at Dillon Reservoir) - this manager's ForestWeightAt()
	 *  gate is an imagery-classified canopy signal (classify_biomes.py) with
	 *  no concept of a lake baked in afterward, so a reservoir added later
	 *  than the classifier's training imagery scatters straight through it
	 *  exactly like BuildingCentroidFilePrefix's town gap did before that
	 *  exclusion existed. Empty (default) means "no lake data, no
	 *  exclusion" - unaffected areas/actor instances with no baked water are
	 *  unaffected. See LoadWaterLakes()/IsInsideLake(). */
	UPROPERTY(EditAnywhere, Category = "Forest")
	FString WaterTilePrefix;

	// --- trunk collision (moved from the retired ODForestCollisionManager) ---
	UPROPERTY(EditAnywhere, Category = "Collision")
	int32 PoolSize = 48;

	UPROPERTY(EditAnywhere, Category = "Collision")
	float CollisionRadius = 6000.0f;

	UPROPERTY(EditAnywhere, Category = "Collision")
	float TrunkRadius = 26.0f;

	UPROPERTY(EditAnywhere, Category = "Collision")
	float TrunkHalfHeight = 380.0f;

private:
	struct FTile
	{
		TArray<TObjectPtr<UHierarchicalInstancedStaticMeshComponent>> Hisms;
		TArray<FVector> Trunks;        // world tree positions (collision)
		TArray<float> TrunkScales;
	};

	bool LoadMask();
	bool LoadBiomePoints();
	bool LoadElevationGrid();
	bool LoadCenterline();
	bool LoadBuildingCentroids();
	bool LoadRoadStations();
	bool LoadWaterLakes();
	bool IsInsideLake(double WorldX, double WorldY) const;
	float ForestWeightAt(double WorldX, double WorldY) const;
	void BlendAt(double WorldX, double WorldY, FBiomeRecipe& OutRecipe,
	             float& OutForestWeight, EODBiome& OutNearestBiome) const;
	bool ElevationGridHeightAt(double WorldX, double WorldY, float& OutZ) const;
	bool IsNearCenterline(double WorldX, double WorldY) const;
	bool WithinCenterlineBand(double WorldX, double WorldY) const;
	bool IsNearBuilding(double WorldX, double WorldY) const;
	bool IsNearRoadStation(double WorldX, double WorldY) const;
	float TownForestDampen(double WorldX, double WorldY) const;
	void MaybeLogGroundSummary() const;
	bool GroundAt(double WorldX, double WorldY, const AActor* Ignore,
	              FVector& OutHit, bool* bOutDeferred = nullptr) const;
	/** Shared raycast-against-actual-runtime-collision helper (2026-08-27,
	 *  levitating-vegetation fix) - traced relative to PlayerZHint exactly
	 *  like GroundAt()'s old raycast-only branch always has (see
	 *  PlayerZHint's own comment), rejecting PM_Asphalt hits and hits more
	 *  than GroundSanityMaxDeltaCm from PlayerZHint (the known phantom
	 *  surface at world Z ~= -829.9 m is comfortably outside that band from
	 *  anywhere in Green Valley's real 0-2200 m elevation range, so this
	 *  same guard rejects it too). Factored out of GroundAt()'s raycast
	 *  branch so ElevationGridHeightAt()'s result can be corrected against
	 *  the SAME real collision query - see GroundAt()'s elevation-grid
	 *  branch for why: build_forest_elevation.py bakes offline from raw DEM
	 *  with no knowledge of AODTerrainManager::ApplyRoadCarve's runtime,
	 *  grade-aware carve, so a tree/bush/rock/scrub placed at the grid's
	 *  raw height sits above (or occasionally below) the real carved
	 *  surface anywhere within the carve's ~150 m taper radius - which
	 *  covers nearly all of ForestBandCm (120 m). */
	bool RaycastRuntimeGround(double WorldX, double WorldY, const AActor* Ignore,
	                          FVector& OutHit) const;
	bool HeightOnlyAt(double WorldX, double WorldY, float& OutZ) const;
	bool SlopeAt(double WorldX, double WorldY, float& OutSlopeRatio) const;
	/** Returns false if the tile's own scatter was mostly deferred (real
	 *  terrain collision not resolvable yet at most candidate points - see
	 *  RaycastRuntimeGround) rather than committed sparse/empty - Tick()
	 *  leaves the key out of Tiles in that case so its own `!Tiles.Contains`
	 *  check naturally retries it next frame, up to TileBuildGiveUpAfterSec
	 *  after which the tile is committed as-is (see TileFirstBuildAttemptTime).
	 *  Found necessary 2026-08-27 alongside the elevation-grid runtime
	 *  correction: Tick()'s TilesPerTick=40 fill burst dispatches up to 169
	 *  tiles' worth of scatter candidates within the first few frames after
	 *  the player's own ground-snap gate opens, well before
	 *  AODTerrainManager has streamed real collision under every one of
	 *  those tiles (proven live: the very first 5 s window deferred 5284 of
	 *  5285 elevation-grid candidates) - without this, the tiles NEAREST the
	 *  player (built first, in ring order) would each get permanently
	 *  committed near-empty, since a committed tile is never revisited. */
	bool BuildTile(const FIntPoint& Key, const AActor* Player);
	void ReleaseTile(const FIntPoint& Key);
	void UpdateCollision(const FVector& PlayerLoc);

	// 7 entries, order = FBiomeRecipe::SpeciesWeight (Blob, Olive, Conifer,
	// ScrubOak, Manzanita, Yucca, CanyonOak) - loaded from
	// ExtendedTreeMeshPaths in ODForestManager.cpp, NOT the shared
	// TreeMeshPaths[3] in ODBinSidecarUtil.h (that array stays the classic 3
	// so ODBarrierManager.cpp, its other consumer, is unaffected).
	UPROPERTY()
	TArray<TObjectPtr<UStaticMesh>> TreeMeshes;

	// 2 entries, order = FBiomeRecipe::ShrubWeight (plain bush, sagebrush) -
	// shrub-class: BuildTile() never adds either to Tile.Trunks, so neither
	// gets a trunk collision capsule, same as the original single BushMesh
	// never did.
	UPROPERTY()
	TArray<TObjectPtr<UStaticMesh>> ShrubMeshes;

	// Ground cover meshes. Originally (2026-08-26) two /Engine/BasicShapes
	// primitives (Cube, Sphere) with aggressive per-instance nonuniform scale
	// - cheap, but read exactly as reported: literal sharp-cornered cubes and
	// perfect spheres littering hillsides, not weathered boulders. Replaced
	// 2026-09-03 (reviewer defect fix) with 4 real procedural boulder meshes
	// (pipeline/make_rock_meshes.py: icosphere base + per-vertex noise
	// displacement + flattened bottom, imported by
	// unreal/import_rock_meshes.py as SM_RockBoulder{A,B,C,D}) - still
	// low-poly/stylized (20-80 tris each) to match the game's existing art
	// direction, just no longer literal engine primitives. RockMeshes.Num()
	// (4) now drives ScrubBatchIdx's offset in BuildTile() - see that
	// constant's own comment.
	// ScrubMesh reuses grass_tuft, an existing tree-set mesh that was never
	// actually wired up to any scatter pass before this.
	UPROPERTY()
	TArray<TObjectPtr<UStaticMesh>> RockMeshes;

	UPROPERTY()
	TObjectPtr<UStaticMesh> ScrubMesh;

	// Real CC0 rock texture (bonus scope, building-facade art-direction pass,
	// 2026-09-03 - see unreal/apply_rock_material.py) - RockMeshes' /Engine/
	// BasicShapes Cube/Sphere otherwise render with the engine's own plain
	// grey default material (no SetMaterial call existed anywhere in this
	// file before this), which is exactly the flat untextured boulder look
	// reported live. Applied to each rock HISM in BuildTile() -
	// used_with_instanced_static_meshes MUST be set on this material asset
	// (apply_rock_material.py sets it) or it silently falls back to the same
	// grey default on an HISM at runtime, same gotcha AODForestManager's own
	// tree bark material already hit once (see next steps.md §7.15).
	UPROPERTY()
	TObjectPtr<UMaterialInterface> RockMat;

public:
	/** Seasons feature (2026-09-07) - see FoliageSeasonMID's own comment
	 *  at its declaration below. AODSeasonController drives SnowCover/
	 *  FallBlend on this. Null until BeginPlay has run (or if M_Foliage
	 *  failed to load) - callers must null-check. */
	UMaterialInstanceDynamic* GetFoliageSeasonMID() const { return FoliageSeasonMID; }

	/** Deciduous-arc follow-up (2026-09, next steps.md sec 7.17) - the
	 *  Blob/tree_blob-only counterpart to GetFoliageSeasonMID() above.
	 *  AODSeasonController drives FallBlend/LeafDrop/SnowCover on this one;
	 *  a separate MID instance so those pushes never reach the Conifer
	 *  batch. Null until BeginPlay has run - callers must null-check. */
	UMaterialInstanceDynamic* GetDeciduousFoliageMID() const { return DeciduousFoliageMID; }

	/** Deciduous-arc follow-up (2026-09) - MID off M_Bark_Aspen for the same
	 *  Blob/tree_blob batch's trunk+branch slot (twig snow, SnowCover-driven
	 *  - see build_flora_materials.py). Null until BeginPlay has run. */
	UMaterialInstanceDynamic* GetDeciduousBarkMID() const { return DeciduousBarkMID; }

private:
	// One shared MID (evergreen/Conifer canopy only, see ConiferSpeciesIndex
	// in ODForestManager.cpp) every such HISM's SetMaterial() call in
	// BuildTile() uses instead of M_Foliage's own baked-in material.
	UPROPERTY()
	TObjectPtr<UMaterialInstanceDynamic> FoliageSeasonMID;

	// Deciduous (Blob/tree_blob) counterpart - see GetDeciduousFoliageMID()/
	// GetDeciduousBarkMID() above.
	UPROPERTY()
	TObjectPtr<UMaterialInstanceDynamic> DeciduousFoliageMID;

	UPROPERTY()
	TObjectPtr<UMaterialInstanceDynamic> DeciduousBarkMID;

	// Per-mesh "how far below its own pivot does the mesh's geometry extend"
	// (local, unscaled cm; <= 0) - added 2026-08-27 (live-drive report:
	// rocks/scrub floating above the terrain surface). RockMeshes are
	// /Engine/BasicShapes Cube/Sphere, both PIVOTED AT THEIR GEOMETRIC CENTER
	// (unlike TreeMeshes/BushMesh, which make_tree_meshes.py exports
	// base-pivoted) - placing one's pivot directly at GroundAt()'s trace hit,
	// as BuildTile() always has, buries half the mesh and floats the other
	// half above the surface by up to (mesh half-height * scale), same
	// direction of error as the reported bug and worse the bigger
	// RockMaxScale/JitterZ scales an instance up. grass_tuft (ScrubMesh) is
	// checked the same way rather than assumed base-pivoted, since it was
	// "never actually wired up to any scatter pass before this" (see
	// ScrubMesh's own comment) and nothing upstream ever verified its pivot.
	// Computed once in BeginPlay() from each mesh's own bounding box (cheap,
	// one-time) and applied per-instance in BuildTile() so a rock/scrub's
	// GEOMETRY BASE - not its pivot - sits on the traced ground point.
	float RockMeshBaseOffsetCm[4] = {0.0f, 0.0f, 0.0f, 0.0f};
	float ScrubMeshBaseOffsetCm = 0.0f;
	// Throttled visibility into the base-offset correction above (temporary
	// QA instrumentation, same 5 s cadence convention as the other counters
	// in this file) - reports how many ground-cover instances were placed and
	// the largest per-instance Z correction actually applied, so a live/
	// headless run can confirm the fix is doing something rather than
	// silently computing a zero offset.
	mutable double LastGroundCoverOffsetLogTime = 0.0;
	mutable int32 GroundCoverOffsetInstanceCount = 0;
	mutable float GroundCoverMaxOffsetAppliedCm = 0.0f;

	UPROPERTY()
	TArray<TObjectPtr<UCapsuleComponent>> Pool;

	TMap<FIntPoint, FTile> Tiles;

	// terrain mask CPU copy (downsampled R channel = forest weight) - the
	// Landscape-actor path (Tail of the Dragon)
	TArray<uint8> MaskR;
	int32 MaskRes = 0;
	FVector2D MaskCorner = FVector2D::ZeroVector;   // world cm
	double MaskSideCm = 0.0;

	// point-classified biome data (pipeline/classify_biomes.py) - the
	// open-world path, no Landscape actor required. Tried before
	// LoadMask() in BeginPlay; empty BiomeFilePrefix skips straight to the
	// Landscape path unchanged.
	struct FBiomePoint
	{
		FVector2D WorldXY;   // cm
		EODBiome Biome = EODBiome::DeciduousForest;
		float ForestWeight = 0.0f;
	};
	TArray<FBiomePoint> BiomePoints;
	bool bUsePointBiomes = false;

	// ground-truth elevation grid (pipeline/build_forest_elevation.py) and
	// road centerline (pipeline/export_road_centerline_bin.py) - the
	// open-world path's replacement for raycast-based ground height
	// + pavement exclusion. Bucketed spatial hashes (built once at load)
	// since these arrays are large (hundreds of thousands of points) and
	// queried per scatter candidate, not once per tile.
	struct FGroundPoint
	{
		FVector2D WorldXY;   // cm
		float Z = 0.0f;      // cm
	};
	TArray<FGroundPoint> ElevationGrid;
	TMap<FIntPoint, TArray<int32>> ElevationBuckets;
	bool bUseElevationGrid = false;
	static constexpr float ElevationCellCm = 6000.0f;   // 60 m, > the grid's own ~30 m spacing

	TArray<FVector2D> RoadCenterline;
	TMap<FIntPoint, TArray<int32>> CenterlineBuckets;
	bool bUseCenterlineExclusion = false;
	static constexpr float CenterlineCellCm = 3000.0f;   // 30 m, >> 5 m station spacing

	TArray<FVector2D> BuildingCentroids;
	TMap<FIntPoint, TArray<int32>> BuildingBuckets;
	bool bUseBuildingExclusion = false;
	static constexpr float BuildingCellCm = 3000.0f;   // 30 m, similar reasoning to CenterlineCellCm

	// Baked lake polygons (2026-09-03) - see WaterTilePrefix's header comment.
	// One entry per lake OUTER boundary ring, recovered from the water bake's
	// triangle soup the same way build_terrain_mesh_tiles.py's
	// _extract_boundary_rings() does (any edge shared by exactly one
	// triangle is a boundary edge, chained into a closed ring) - works
	// regardless of ear-clip/fan-fallback triangulation and naturally
	// ignores interior island vertices. Small array (a handful of lakes per
	// map), checked with a cheap bbox test before the exact point-in-polygon
	// ray-cast, so no spatial hash needed unlike the point-cloud sidecars
	// above.
	struct FLakeRing
	{
		TArray<FVector2D> Ring;   // world cm, closed (last point != first, IsInsideLake wraps)
		double MinX = 0.0, MaxX = 0.0, MinY = 0.0, MaxY = 0.0;
	};
	TArray<FLakeRing> LakeRings;
	bool bUseWaterExclusion = false;

	// Road-station exclusion (2026-08-26) - see RoadStationFilePrefix's
	// comment for why this exists alongside RoadCenterline/CenterlineBuckets
	// above rather than reusing that path. FStationSpatialHash/FRoadStation
	// come from ODBinSidecarUtil.h - the SAME types AODTerrainManager builds
	// its own copy of for the runtime carve (see that header's comment on why
	// each manager keeps its own hash instead of sharing one instance). This
	// manager only ever queries it from the game thread (GroundAt() runs
	// synchronously per scatter candidate in BuildTile()), so a plain value
	// is enough - no TSharedPtr/thread-safety needed here.
	FStationSpatialHash RoadStationHash;
	bool bUseStationExclusion = false;

	double LastDiagTime = 0.0;
	// gate for the per-tile ground-cover stat log (BuildTile()) - same
	// 5-second cadence as LastDiagTime above, kept as a separate member since
	// BuildTile() and UpdateCollision() log at different points in Tick().
	double LastGroundCoverLogTime = 0.0;
	bool bReady = false;

	// Player's last-known world Z (2026-08-26, live-drive bug: trees found
	// rendered far below the terrain surface on Green Valley). GroundAt()'s/
	// HeightOnlyAt()'s raycast fallback (no elevation grid baked for this
	// area yet) used to trace a hardcoded absolute Z range (+-1000 m) - fine
	// for Tail of the Dragon's low-lying terrain, but Green Valley's own
	// plausible elevation range is 0-2200 m (see ODTerrainManager.cpp's
	// ElevLowCm/ElevHighCm comment), so a hill above ~1000 m put the trace's
	// own start point already underground: the ray never crossed the real
	// surface from above and could instead register a hit against whatever
	// unrelated lower geometry happened to fall inside that band, planting
	// the tree at a wildly wrong (too-low) height. Set once per Tick() from
	// the player pawn's own location (mirrors AODVehiclePawn::
	// TrySnapToGround's already-established "trace relative to current
	// position, not an absolute Z" fix for the same class of bug) so the
	// trace bounds scale with wherever the player actually is instead of
	// assuming any particular area's elevation band.
	float PlayerZHint = 0.0f;
	// Reject a raycast hit whose height differs from PlayerZHint by more
	// than this - local relief within one SpawnRadius (500 m) shouldn't
	// plausibly exceed it, so a hit this far off is almost certainly wrong
	// geometry (stale/unrelated tile, or a hit crossing GroundTraceMarginCm's
	// own search band) rather than real ground - reject rather than plant.
	static constexpr float GroundSanityMaxDeltaCm = 50000.0f;   // 500 m
	// Trace half-range around PlayerZHint (see above) - wide enough to
	// bracket Green Valley's full 2200 m elevation swing from anywhere in
	// it, cheap for a single line trace either way.
	static constexpr float GroundTraceMarginCm = 300000.0f;   // 3000 m
	// Throttled visibility into GroundSanityMaxDeltaCm rejections (temporary
	// QA instrumentation for the live-drive bug above) - same 5 s cadence
	// convention as LastDiagTime/LastGroundCoverLogTime.
	mutable double LastGroundRejectLogTime = 0.0;
	mutable int32 GroundRejectCount = 0;

	// Throttled visibility into road-station exclusion rejects (2026-08-26,
	// see RoadStationFilePrefix/IsNearRoadStation) - same 5 s cadence
	// convention as the sanity-check counters just above, proving the check
	// actually fires (and roughly how often) rather than silently no-op'ing
	// on an empty/misconfigured prefix.
	mutable double LastRoadExclusionLogTime = 0.0;
	mutable int32 RoadExclusionRejectCount = 0;
	// Of RoadExclusionRejectCount above, how many were rejected ONLY because
	// the candidate's cell tripped JunctionClusterStationCountThreshold (i.e.
	// would have PASSED the ordinary RoadStationExclusionCm radius) - added
	// 2026-08-27 alongside JunctionClusterExclusionCm, purely to make the new
	// widened-radius path's own effect visible in the log rather than folding
	// invisibly into the existing station-exclusion count.
	mutable int32 JunctionClusterRejectCount = 0;

	// Throttled visibility into GroundAt()'s PM_Asphalt raycast-path rejects
	// (2026-08-27, junction-pad tree-exclusion task) - the belt-and-braces
	// physmat check (see GroundAt()'s "never plant a tree on pavement"
	// comment) only fires on the raycast branch when the trace actually hits
	// pavement; counted separately from RoadExclusionRejectCount above so a
	// combined summary line can show the split between the two independent
	// exclusion mechanisms plus how many candidates were ultimately accepted.
	mutable double LastGroundSummaryLogTime = 0.0;
	mutable int32 PhysMatRejectCount = 0;
	mutable int32 GroundAcceptCount = 0;
	// Diagnostic-only (2026-09-03, "nearly all vegetation vanished" regression
	// investigation): how many scatter candidates GroundAt() rejected via
	// IsInsideLake() specifically, folded into the same throttled summary line
	// - lets a headless run directly show whether water exclusion is the
	// dominant rejection source (vs. road-station exclusion/physmat) instead
	// of only being inferable from a total absence of downstream log activity.
	mutable int32 WaterExclusionRejectCount = 0;

	// Throttled visibility into the elevation-grid runtime-correction step
	// (2026-08-27, levitating-vegetation fix - see RaycastRuntimeGround's
	// comment) - proves the correction is actually running and roughly how
	// large the offline-grid-vs-runtime-carve disagreement is, plus how
	// often a candidate had to be skipped (deferred) because no real
	// terrain collision was resolvable there yet.
	mutable double LastElevationCorrectionLogTime = 0.0;
	mutable int32 ElevationCorrectedCount = 0;
	mutable int32 ElevationDeferredCount = 0;
	mutable float ElevationMaxCorrectionCm = 0.0f;

	// Tile-level retry-until-terrain-ready state (2026-08-27, see BuildTile()'s
	// own comment) - a tile whose scatter mostly deferred isn't added to
	// Tiles yet, so this tracks how long we've been retrying it (wall-clock
	// via GetWorld()->GetTimeSeconds(), same convention as
	// ODBuildingManager::PendingBuildings) to bound the retry with a
	// give-up: past TileBuildGiveUpAfterSec the tile commits with whatever
	// it managed to place rather than retrying forever (matches
	// ODBuildingManager::PendingGiveUpAfterSec's identical reasoning).
	TMap<FIntPoint, double> TileFirstBuildAttemptTime;
	// Above this fraction of a tile's ground-check candidates deferred (real
	// terrain not resolvable yet), the tile is held back rather than
	// committed - the observed first-burst rate was ~100% deferred, so even
	// a modest fraction here reliably catches "this tile ran way too early".
	static constexpr float TileRetryDeferralRatio = 0.5f;
	static constexpr double TileBuildGiveUpAfterSec = 10.0;
	mutable int32 TileRetryCount = 0;
	mutable int32 TileGiveUpCount = 0;

	// One-time proof-of-fix log for the "hitting a bush stops/damages the
	// car" live-drive report (2026-08-27) - BuildTile() already gives every
	// HISM (trees, bush, rocks, scrub alike) ECollisionEnabled::NoCollision
	// unconditionally, with only trees separately getting real collision via
	// the trunk capsule pool above; logs the actual runtime
	// GetCollisionEnabled() readback for the bush/scrub components the first
	// time each has at least one instance, so a headless/live run can
	// confirm in the log that bush/scrub genuinely carry no collision rather
	// than trusting the source read alone.
	mutable bool bLoggedBushCollisionCheck = false;
	mutable bool bLoggedScrubCollisionCheck = false;

	// -odshot=N QA hook state (2026-08-22: moved off function-local `static`
	// into instance members - a `static` inside Tick() only initializes once
	// per PROCESS, not once per PIE session, so triggering -odshot, stopping
	// PIE, then starting a new PIE run in the same editor process left
	// bShotDone still true from the previous run and the screenshot never
	// fired again. An instance member is naturally fresh every PIE session
	// since PIE spawns a brand-new AODForestManager each time - same fix
	// applied to AODScreenshotHook/AODRoadManager's identical pattern).
	float ShotAt = -1.0f;
	bool bShotParsed = false;
	bool bShotDone = false;

	static constexpr float TileSizeCm = 9600.0f;    // 96 m
	// SpawnRadius / TileSizeCm -> Reach in tile-rings, i.e. up to
	// (2*Reach+1)^2 tiles to fully cover the disc around the player (at the
	// current 500 m SpawnRadius, Reach ~= 6 -> up to 169 tiles; it was ~4
	// rings / 81 tiles back when this was found at SpawnRadius=300m). At the
	// old TilesPerTick=1 that's tens of seconds to finish just ONE
	// stationary fill, and Tick() re-centers on the player every frame while
	// driving - the fill front never catches up to a moving car at all.
	// Found 2026-08-24 (Taylor: "a single line of trees at the edge of the
	// road... a tiny see-through line, not a whole collection") - what
	// looked like a placement/density bug was the ring-by-ring BFS fill
	// (Tick(), nearest-first) permanently stuck only a few rings out
	// from wherever the player has been, which reads as a thin, spotty band
	// rather than the full biome-density forest. 40/tick cleared the whole
	// disc in a handful of ticks.
	//
	// 2026-09-07 (Taylor: "frames are frequently dropping... while loading
	// new tiles"): the "BuildTile() itself is cheap, plenty of headroom"
	// assumption above did NOT survive the tile audit. One tile scatters
	// ~1470 candidates (each a GroundAt() spatial-hash/trace query) and
	// registers up to 13 fresh HISM components - 40 tiles in one frame is
	// tens of thousands of queries plus hundreds of component
	// registrations, and it all lands at once whenever the fill gate opens
	// (boot, respawn) or the drive-front outruns the filled disc. So the
	// burst is now additionally capped by TileBuildMsBudget per frame,
	// same pattern as AODTerrainManager/AODRoadManager's ms budgets:
	// TilesPerTick stays as a hard count ceiling, but the loop stops early
	// once the frame has spent its budget (always completing at least one
	// tile so the fill can never stall). A stationary fill now spreads
	// over ~1-2 s instead of hitching a handful of frames; the moving-car
	// fill front keeps up fine (a car crosses one 96 m tile ring in ~2 s
	// even at highway speed, and the budget clears several tiles per
	// SECOND at 60 fps).
	static constexpr int32 TilesPerTick = 40;
	static constexpr double TileBuildMsBudget = 2.5;
};
