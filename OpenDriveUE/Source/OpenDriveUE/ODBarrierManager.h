#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODForestManager.h" // EODBiome, AODForestManager::RecipeForBiome() - 2026-08-27 biome-aware windbreak species
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

	// pipeline/generation_id.py's per-run id, read from the ODW3 header
	// (2026-08-21) - see AODBarrierManager::OnTileParsed for the
	// cross-tile mismatch check this feeds.
	FString GenerationId;
};

/** Live components for one loaded tile: per-species HISM for the visual
 *  trees/bushes (cheap even at thousands of instances, same reasoning
 *  AODForestManager already relies on). */
struct FBarrierTile
{
	TArray<TObjectPtr<UHierarchicalInstancedStaticMeshComponent>> Hisms;   // one per species/bush
	// 2026-09-06: no WallRuns components any more - the invisible collision
	// ribbon is retired (AODVehiclePawn::TickOutOfBounds owns the guarantee).
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
 *
 * 2026-09-06 (AUDIT.md 5.4/rec. 5): the collision ribbon itself is retired
 * too - this manager is now VISUAL-ONLY (the boundary trees/bushes). The
 * escape guarantee moved to AODVehiclePawn::TickOutOfBounds(): sustained
 * off-corridor travel or a void fall soft-resets the car to the nearest
 * road point, which removes the invisible-wall feel the audit called the
 * most-criticized-in-genre element of the project.
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

	/** Content/OpenDrive/Masks/<prefix>.bin - the SAME point-classified
	 *  biome sidecar AODForestManager reads (pipeline/classify_biomes.py +
	 *  export_biome_bin.py, ODB1 format). Added 2026-08-27 (Taylor's
	 *  live-drive report: dense rows of identical bright-green trees lining
	 *  the road through open desert) so a windbreak run through a desert/
	 *  scrub biome picks drought-looking species (see BiomeAt()/FinalizeTile())
	 *  instead of always the classic blob/olive/conifer trio. Empty (default)
	 *  is auto-derived from the level's own AODRoadManager::RoadTilePrefix in
	 *  BeginPlay() (mirrors AODGameMode::EnsureForestManager's identical
	 *  "road_<area>_test" -> "biome_<area>_test" derivation for
	 *  AODForestManager::BiomeFilePrefix) - resolved locally here rather than
	 *  having ODGameMode set it, since this actor is placed directly in the
	 *  map by unreal/build_openworld_test_level.py (BARRIER_ENABLED) and its
	 *  BeginPlay() isn't guaranteed to run after ODGameMode's own (same class
	 *  of ordering hazard AODForestManager::BiomeFilePrefix's
	 *  SpawnActorDeferred() comment documents, just the consumer's BeginPlay
	 *  racing ahead instead of the setter's). Still EditAnywhere so a level
	 *  can override it explicitly if the derivation ever guesses wrong. */
	UPROPERTY(EditAnywhere, Category = "Barrier")
	FString BiomeFilePrefix;

	/** Radius (cm) within which a biome point must fall to count as "found" -
	 *  matches AODForestManager::BiomeBlendRadiusCm's role, but simpler:
	 *  this manager only ever needs the SINGLE nearest classified point's
	 *  biome for a coarse per-instance species pick, not a smooth
	 *  inverse-distance blend. */
	UPROPERTY(EditAnywhere, Category = "Barrier")
	float BiomeSearchRadiusCm = 30000.0f;   // 300 m

private:
	static bool ParseBarrierTileFile(const FString& Path, FParsedBarrierTile& Out);

	void RequestTileLoad(const FIntPoint& Key);
	void OnTileParsed(const FIntPoint& Key, bool bOk, FParsedBarrierTile&& Parsed);
	void FinalizeTile(const FIntPoint& Key, FParsedBarrierTile&& Parsed);
	void ReleaseTile(const FIntPoint& Key);

	/** Loads BiomeFilePrefix into BiomePoints/BiomeBuckets - mirrors
	 *  AODForestManager::LoadBiomePoints()'s ODB1 parsing exactly (same
	 *  sidecar format, same file), just without that manager's
	 *  inverse-distance blend machinery (see BiomeAt()). False (no-op) if
	 *  BiomeFilePrefix is empty or the file doesn't exist - same "optional
	 *  feature" convention as every other manager's prefix. */
	bool LoadBiomePoints();

	/** Nearest classified biome point to (WorldX, WorldY), or
	 *  EODBiome::DeciduousForest (the classic-3-species trio's dominant
	 *  look) if no biome data is loaded or nothing is within
	 *  BiomeSearchRadiusCm - same graceful fallback as every other optional
	 *  data source in this project. */
	EODBiome BiomeAt(double WorldX, double WorldY) const;

	TMap<FIntPoint, FBarrierTile> Tiles;
	TSet<FIntPoint> PendingTiles;
	TSet<FIntPoint> MissingTiles;

	// Point-classified biome data for BiomeAt() (2026-08-27) - bucketed the
	// same way AODForestManager buckets its BuildingCentroids/RoadCenterline
	// (large point arrays queried per-candidate, not once per tile).
	struct FBarrierBiomePoint
	{
		FVector2D WorldXY = FVector2D::ZeroVector;   // cm
		EODBiome Biome = EODBiome::DeciduousForest;
	};
	TArray<FBarrierBiomePoint> BiomePoints;
	TMap<FIntPoint, TArray<int32>> BiomeBuckets;
	bool bUseBiome = false;
	static constexpr float BiomeCellCm = 6000.0f;   // 60 m

	// generation-id cross-tile check (2026-08-21, AUDIT-RELIABILITY.md
	// SS3.1/SS5.1): the id of the first tile this manager successfully
	// parsed for BarrierTilePrefix - every later tile is compared against
	// it, and a mismatch (tiles from two different pipeline runs streaming
	// side by side) is logged once, not blocked.
	FString FirstGenerationId;
	bool bHasFirstGenerationId = false;
	bool bGenerationMismatchWarned = false;

	UPROPERTY()
	TArray<TObjectPtr<UStaticMesh>> TreeMeshes;

	UPROPERTY()
	TObjectPtr<UStaticMesh> BushMesh;

	double LastDiagTime = 0.0;
};
