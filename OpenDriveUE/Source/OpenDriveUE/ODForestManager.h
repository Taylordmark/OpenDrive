#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODForestManager.generated.h"

class UCapsuleComponent;
class UHierarchicalInstancedStaticMeshComponent;
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

/** One biome's recipe against the fixed 3-species/1-bush mesh set - actual
 *  per-biome values live in ODForestManager.cpp's BiomeRecipeTable (kept
 *  there, not here, since nothing outside this module needs it - only the
 *  TYPE needs to be header-visible, for RecipeAt()'s signature). */
struct FBiomeRecipe
{
	float SpeciesWeight[3] = {1.f, 0.f, 0.f};   // Blob, Olive, Conifer
	float TreeDensityScale = 1.f;
	float BushDensityScale = 1.f;
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

	/** Tiles fully populated inside this radius of the player (cm). Trimmed
	 *  480 m -> 300 m (2026-07-25): with ForestBandCm now bounding trees to a
	 *  corridor around the road, the outer disk was empty scatter attempts
	 *  anyway, and the smaller radius keeps any far background terrain (whose
	 *  smoothed height disagrees with the tree elevation grid) out of frame. */
	UPROPERTY(EditAnywhere, Category = "Forest")
	float SpawnRadius = 30000.0f;

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
	float ForestWeightAt(double WorldX, double WorldY) const;
	void BlendAt(double WorldX, double WorldY, FBiomeRecipe& OutRecipe,
	             float& OutForestWeight) const;
	bool ElevationGridHeightAt(double WorldX, double WorldY, float& OutZ) const;
	bool IsNearCenterline(double WorldX, double WorldY) const;
	bool WithinCenterlineBand(double WorldX, double WorldY) const;
	bool IsNearBuilding(double WorldX, double WorldY) const;
	float TownForestDampen(double WorldX, double WorldY) const;
	bool GroundAt(double WorldX, double WorldY, const AActor* Ignore,
	              FVector& OutHit) const;
	void BuildTile(const FIntPoint& Key, const AActor* Player);
	void ReleaseTile(const FIntPoint& Key);
	void UpdateCollision(const FVector& PlayerLoc);

	UPROPERTY()
	TArray<TObjectPtr<UStaticMesh>> TreeMeshes;

	UPROPERTY()
	TObjectPtr<UStaticMesh> BushMesh;

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

	double LastDiagTime = 0.0;
	bool bReady = false;

	static constexpr float TileSizeCm = 9600.0f;    // 96 m
	static constexpr int32 TilesPerTick = 1;
};
