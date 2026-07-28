#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "UObject/WeakObjectPtrTemplates.h"
#include "ODBarrierManager.generated.h"

class UDynamicMeshComponent;
class UHierarchicalInstancedStaticMeshComponent;
class UMaterialInterface;
class UStaticMesh;

/** One parsed barrier point - plain data, safe to build off the game
 *  thread. Visual placement only (HISM tree/bush instance) - collision no
 *  longer lives here, see FParsedWallChunk. */
struct FParsedBarrierPoint
{
	FVector Pos = FVector::ZeroVector;   // world XYZ, cm
	uint32 Seed = 0;
};

/** One parsed wall RUN - just the offset station centerline points (ground
 *  level, one per station) for a contiguous stretch of wall. Mirrors the
 *  ODW3 sidecar payload (pipeline/build_road_barrier.py /
 *  export_road_barrier_bin.py). 2026-07-28 (AUDIT.md 5.1/rec. 3): this used
 *  to be a fully pre-triangulated mesh (verts/uvs/tris) baked offline; now
 *  AODBarrierManager extrudes the vertical ribbon (2 verts + 2 tris per
 *  station step) from these points itself, since that extrusion is
 *  mechanically derivable from the polyline and was costing ~5x the disk
 *  space to ship pre-baked for no benefit. */
struct FParsedWallRun
{
	TArray<FVector> Stations;   // world XYZ, cm, ground level
};

struct FParsedBarrierTile
{
	TArray<FParsedBarrierPoint> Points;
	TArray<FParsedWallRun> WallRuns;
};

/** Live components for one loaded tile: per-species HISM for the visual
 *  trees/bushes (cheap even at thousands of instances, same reasoning
 *  AODForestManager already relies on), plus one UDynamicMeshComponent per
 *  wall chunk - a genuinely CONTINUOUS collision surface, not discrete
 *  per-point objects (see build_road_barrier.py's docstring for why a row
 *  of discrete capsules doesn't actually guarantee a solid wall). */
struct FBarrierTile
{
	TArray<TObjectPtr<UHierarchicalInstancedStaticMeshComponent>> Hisms;   // one per species/bush
	TArray<TObjectPtr<UDynamicMeshComponent>> WallRuns;
};

/**
 * Arc 7.0 follow-up (2026-07-24): a guaranteed, CONTINUOUS "wall of
 * scenery" just outside AODForestManager's shoulder clearance
 * (PavementExclusionCm, 11.5 m) - found necessary live when a hard
 * cornering slide let the car reach open space and fall through within a
 * few meters, since the organic forest scatter is intentionally
 * probabilistic (tile-level biome density) and can leave real gaps. A
 * first version of this actor used discrete per-point capsules, which
 * turned out to have the same problem in a different form (real gaps
 * between capsules a car can slip through) - fixed by building a real
 * continuous collision mesh (thin vertical ribbon following the road,
 * same "thin sheet with working collision" pattern the road's own skirt
 * cross-section already relies on) instead of point obstacles. The
 * deterministic tree/bush points are still placed for the visual look,
 * they just no longer carry any collision responsibility themselves.
 */
UCLASS()
class OPENDRIVEUE_API AODBarrierManager : public AActor
{
	GENERATED_BODY()

public:
	AODBarrierManager();
	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
	virtual void Tick(float DeltaSeconds) override;

	/** Sidecar filename prefix under Content/OpenDrive/Masks/; tile files
	 *  are "<prefix>_tile_<tx>_<ty>.bin". */
	UPROPERTY(EditAnywhere, Category = "Barrier")
	FString BarrierTilePrefix = TEXT("barrier_oahu_test");

	/** World-space grid size, cm - MUST match pipeline/test_areas.py's
	 *  TERRAIN_TILE_SIZE_CM (barrier reuses the terrain tile grid). */
	UPROPERTY(EditAnywhere, Category = "Barrier")
	float TileSizeCm = 200000.0f;

	/** Modest by design - barrier points are only ever ~13 m from the
	 *  road, so this only needs to reach the current + immediately
	 *  neighboring tiles, never anywhere the player couldn't otherwise be. */
	UPROPERTY(EditAnywhere, Category = "Barrier")
	float SpawnRadius = 150000.0f;

	UPROPERTY(EditAnywhere, Category = "Barrier")
	float DespawnMarginCm = 100000.0f;

	UPROPERTY(EditAnywhere, Category = "Barrier")
	int32 MaxNewLoadsPerTick = 1;

	/** Vertical extrusion height (cm) for the runtime-built collision
	 *  ribbon - MUST match what build_road_barrier.py's docstring documents
	 *  (4 m), since the pipeline no longer bakes this itself. */
	UPROPERTY(EditAnywhere, Category = "Barrier")
	float WallHeightCm = 400.0f;

private:
	static bool ParseBarrierTileFile(const FString& Path, FParsedBarrierTile& Out);

	void RequestTileLoad(const FIntPoint& Key);
	void OnTileParsed(const FIntPoint& Key, bool bOk, FParsedBarrierTile&& Parsed);
	void FinalizeTile(const FIntPoint& Key, FParsedBarrierTile&& Parsed);
	void ReleaseTile(const FIntPoint& Key);

	TMap<FIntPoint, FBarrierTile> Tiles;
	TSet<FIntPoint> PendingTiles;
	TSet<FIntPoint> MissingTiles;

	UPROPERTY()
	TArray<TObjectPtr<UStaticMesh>> TreeMeshes;

	UPROPERTY()
	TObjectPtr<UStaticMesh> BushMesh;

	double LastDiagTime = 0.0;
};
