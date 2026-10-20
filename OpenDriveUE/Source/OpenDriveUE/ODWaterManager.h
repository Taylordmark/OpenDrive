#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODMenuVista.h"
#include "ODWaterManager.generated.h"

class UDynamicMeshComponent;
class UMaterialInterface;
class UMaterialInstanceDynamic;

/** One parsed lake - plain data, safe to build off the game thread. Mirrors
 *  the ODL1 sidecar's per-lake payload layout (pipeline/build_water_tiles.py). */
struct FParsedLake
{
	TArray<float> Verts;    // flat x,y,z (cm, UE world frame) - already baked
	                         // at the lake's own flat surface Z (DEM 10th
	                         // percentile across the polygon interior, see
	                         // build_water_tiles.py's module docstring) -
	                         // unlike AODBuildingManager, there is NO runtime
	                         // ground trace here: a lake's surface height is
	                         // a pipeline-time decision, not something that
	                         // depends on where AODTerrainManager's async
	                         // collision has streamed in yet.
	TArray<float> Uvs;      // flat u,v
	TArray<uint32> Tris;    // flat i0,i1,i2
};

/** One parsed tile file's worth of lakes - the off-thread output of
 *  AODWaterManager::ParseWaterTileFile, consumed on the game thread by
 *  OnTileParsed to build real components. */
struct FParsedWaterTile
{
	TArray<FParsedLake> Lakes;

	// pipeline/generation_id.py's per-run id, read from the ODL1 header -
	// see AODWaterManager::OnTileParsed for the cross-tile mismatch check
	// this feeds (same convention as every other tile-streamed manager here).
	FString GenerationId;
};

/** Live water components for one loaded tile - ONE UDynamicMeshComponent
 *  holding every lake in the tile (2026-10-16 perf pass: one component per
 *  lake was 1841 draws per pass on summit_county for mostly tiny ponds). */
struct FWaterTile
{
	TArray<TObjectPtr<UDynamicMeshComponent>> Lakes;
	int32 NumLakes = 0;
};

/**
 * WATER feature (2026-08-27): streams baked, flat, stylized lake surfaces
 * in the same world-space tiles AODTerrainManager/AODBuildingManager already
 * use (TERRAIN_TILE_SIZE_CM) - a separate actor per this project's
 * one-actor-per-data-domain convention. Lakes only (OSM natural=water/
 * landuse=reservoir CLOSED WAYS) - linear waterways (rivers/streams,
 * OSM waterway=*) are out of scope for this pass, see build_water_tiles.py.
 *
 * Deliberately the SIMPLEST tile-streaming manager in this project:
 * AODBuildingManager's ground-trace/retry-queue machinery exists because a
 * building's placement Z depends on runtime-streamed terrain collision that
 * may not have arrived yet. A lake's surface Z is a pure pipeline-time bake
 * (DEM sample, see build_water_tiles.py) - the mesh is built directly from
 * the sidecar's own baked vertex Z the moment the async parse completes, no
 * ground trace, no pending-retry queue, no player-ground-snap gate needed.
 * Water has NO collision (task scope: driving INTO a lake is a future
 * feature, not this pass) - components are visual-only.
 */
UCLASS()
class OPENDRIVEUE_API AODWaterManager : public AActor
{
	GENERATED_BODY()

public:
	AODWaterManager();
	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
	virtual void Tick(float DeltaSeconds) override;

	/** Garage-menu vista copy (2026-10-05), see AODGameMode::SpawnVistaSet. */
	FODVistaRegion Vista;

	/** Sidecar filename prefix under Content/OpenDrive/Masks/; tile files
	 *  are "<prefix>_tile_<tx>_<ty>.bin". */
	UPROPERTY(EditAnywhere, Category = "Water")
	FString WaterTilePrefix = TEXT("water_green_valley_test");

	/** World-space grid size, cm. MUST match pipeline/test_areas.py's
	 *  TERRAIN_TILE_SIZE_CM (water reuses the terrain/building tile grid). */
	UPROPERTY(EditAnywhere, Category = "Water")
	float TileSizeCm = 200000.0f;

	/** Tiles fully populated inside this radius of the player (cm). Larger
	 *  than AODBuildingManager's default - lakes are sparse (a handful per
	 *  map, not thousands of components per tile) and, unlike buildings,
	 *  never need to wait on terrain collision, so there's no reason to keep
	 *  this tight; a lake should be visible (a real landmark on approach)
	 *  well before the player is anywhere near its shore. */
	UPROPERTY(EditAnywhere, Category = "Water")
	float SpawnRadius = 400000.0f;

	UPROPERTY(EditAnywhere, Category = "Water")
	float DespawnMarginCm = 300000.0f;

	UPROPERTY(EditAnywhere, Category = "Water")
	int32 MaxNewLoadsPerTick = 2;

	/** Seasons feature (2026-09-07) - see AODTerrainManager::GetGroundMID's
	 *  identical reasoning. AODSeasonController drives FreezeBlend on this. */
	UMaterialInstanceDynamic* GetWaterMID() const { return WaterMID; }

	/** Every wanted tile in reach loaded (or known missing) - the menu's start
	 *  vista waits on it (2026-10-16). */
	bool IsInitialStreamingSettled() const { return bSettled; }

private:
	bool bSettled = false;

	static bool ParseWaterTileFile(const FString& Path, FParsedWaterTile& Out);

	void RequestTileLoad(const FIntPoint& Key);
	void OnTileParsed(const FIntPoint& Key, bool bOk, FParsedWaterTile&& Parsed);
	void FinalizeTile(const FIntPoint& Key, FParsedWaterTile&& Parsed);
	void ReleaseTile(const FIntPoint& Key);

	/** Builds the actual UDynamicMeshComponent for one already-baked lake and
	 *  appends it to Tile. No collision (task scope - see class comment) and
	 *  no Z resolution step: FParsedLake's verts already carry their final
	 *  world Z from the pipeline bake. */
	void CreateLakeComponent(FWaterTile& Tile, const TArray<FParsedLake>& InLakes);

	TMap<FIntPoint, FWaterTile> Tiles;
	TSet<FIntPoint> PendingTiles;
	TSet<FIntPoint> MissingTiles;

	// generation-id cross-tile check (AUDIT-RELIABILITY.md SS3.1/SS5.1
	// convention, same as every other tile-streamed manager here): the id of
	// the first tile this manager successfully parsed for WaterTilePrefix -
	// every later tile is compared against it, and a mismatch (tiles from two
	// different pipeline runs streaming side by side) is logged once, not
	// blocked.
	FString FirstGenerationId;
	bool bHasFirstGenerationId = false;
	bool bGenerationMismatchWarned = false;

	UPROPERTY()
	TObjectPtr<UMaterialInterface> WaterMat;

	UPROPERTY()
	TObjectPtr<UMaterialInstanceDynamic> WaterMID;

	double LastDiagTime = 0.0;
};
