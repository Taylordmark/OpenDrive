#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "UObject/WeakObjectPtrTemplates.h"
#include "ODTerrainManager.generated.h"

class UDynamicMeshComponent;
class UMaterialInterface;

/** One parsed terrain tile - plain data, safe to build off the game thread
 *  (no UObject/component touched). Mirrors the ODT1 sidecar payload layout
 *  (pipeline/build_terrain_mesh_tiles.py). */
struct FParsedTerrainTile
{
	bool bHasCollision = false;
	TArray<float> Verts;    // flat x,y,z (cm, UE world frame)
	TArray<float> Uvs;      // flat u,v
	TArray<uint32> Tris;    // flat i0,i1,i2

	// pipeline/generation_id.py's per-run id, read from the ODT1 header
	// (2026-08-21) - see AODTerrainManager::OnTileParsed for the
	// cross-tile mismatch check this feeds.
	FString GenerationId;
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

	/** Cap on tiles actually BUILT (mesh + collision) per tick, once
	 *  parsed - see ProcessPendingTileBuilds. Mirrors
	 *  AODRoadManager::ChunksPerFrameBudget's reasoning: bulk same-frame
	 *  UDynamicMeshComponent collision creation is the suspected trigger
	 *  for a still-open Chaos/UE5.8 physics-registration bug, so builds
	 *  (including fidelity upgrades/downgrades) are spread across ticks
	 *  even though load dispatch already is. */
	UPROPERTY(EditAnywhere, Category = "Terrain")
	int32 TileBuildsPerFrameBudget = 2;

private:
	static bool ParseTerrainTileFile(const FString& Path, FParsedTerrainTile& Out);

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

	double LastDiagTime = 0.0;
};
