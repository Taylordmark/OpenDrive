#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "UObject/WeakObjectPtrTemplates.h"
#include "ODBuildingManager.generated.h"

class UDynamicMeshComponent;
class UMaterialInterface;

/** One parsed building - plain data, safe to build off the game thread.
 *  Mirrors the ODU1 sidecar's per-building payload layout
 *  (pipeline/build_building_mesh.py / export_building_mesh_bin.py). */
struct FParsedBuilding
{
	TArray<float> Verts;    // flat x,y,z (cm, UE world frame)
	TArray<float> Uvs;      // flat u,v
	TArray<uint32> Tris;    // flat i0,i1,i2
};

/** One parsed tile file's worth of buildings - the off-thread output of
 *  AODBuildingManager::ParseBuildingTileFile, consumed on the game thread
 *  by OnTileParsed to build real components. */
struct FParsedBuildingTile
{
	TArray<FParsedBuilding> Buildings;

	// pipeline/generation_id.py's per-run id, read from the ODU1 header
	// (2026-08-21) - see AODBuildingManager::OnTileParsed for the
	// cross-tile mismatch check this feeds.
	FString GenerationId;
};

/** Live building components for one loaded tile - ONE UDynamicMeshComponent
 *  PER BUILDING (unlike AODTerrainManager's single grid mesh per tile):
 *  buildings need independent per-building collision/culling, and a tile
 *  can hold thousands of them (dense downtown blocks), so each gets its
 *  own component the same way AODRoadManager's per-chunk components do. */
struct FBuildingTile
{
	TArray<TObjectPtr<UDynamicMeshComponent>> Buildings;
};

/**
 * Arc 7.0 step 3 (2026-07-24): streams procedurally-extruded OSM building
 * footprints in the same world-space tiles AODTerrainManager already
 * defines (TERRAIN_TILE_SIZE_CM) - a separate actor per this project's
 * one-actor-per-data-domain convention (AODRoadManager/AODTerrainManager/
 * AODForestManager each own their own tile-streaming loop independently),
 * not a bolt-on to AODTerrainManager's tile struct. Buildings only exist
 * in populated tiles (pipeline/classify_settlements.py) - a tile with no
 * buildings.bin file is just an ordinary, unpopulated tile, same
 * "no hole, just a missing file" semantics every other tile-streamed
 * system here already relies on. Buildings always have full collision -
 * there's no far/no-collision tier the way terrain has, since a building
 * only ever exists near the route.
 */
UCLASS()
class OPENDRIVEUE_API AODBuildingManager : public AActor
{
	GENERATED_BODY()

public:
	AODBuildingManager();
	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
	virtual void Tick(float DeltaSeconds) override;

	/** Sidecar filename prefix under Content/OpenDrive/Masks/; tile files
	 *  are "<prefix>_tile_<tx>_<ty>.bin". */
	UPROPERTY(EditAnywhere, Category = "Buildings")
	FString BuildingTilePrefix = TEXT("buildings_oahu_test");

	/** World-space grid size, cm. MUST match pipeline/test_areas.py's
	 *  TERRAIN_TILE_SIZE_CM (buildings reuse the terrain tile grid). */
	UPROPERTY(EditAnywhere, Category = "Buildings")
	float TileSizeCm = 200000.0f;

	/** Tiles fully populated inside this radius of the player (cm). Smaller
	 *  than terrain/road's default - buildings are dense (thousands of
	 *  components in one downtown tile) and only matter close up. */
	UPROPERTY(EditAnywhere, Category = "Buildings")
	float SpawnRadius = 250000.0f;

	UPROPERTY(EditAnywhere, Category = "Buildings")
	float DespawnMarginCm = 300000.0f;

	UPROPERTY(EditAnywhere, Category = "Buildings")
	int32 MaxNewLoadsPerTick = 1;

private:
	static bool ParseBuildingTileFile(const FString& Path, FParsedBuildingTile& Out);

	void RequestTileLoad(const FIntPoint& Key);
	void OnTileParsed(const FIntPoint& Key, bool bOk, FParsedBuildingTile&& Parsed);
	void FinalizeTile(const FIntPoint& Key, FParsedBuildingTile&& Parsed);
	void ReleaseTile(const FIntPoint& Key);

	TMap<FIntPoint, FBuildingTile> Tiles;
	TSet<FIntPoint> PendingTiles;
	TSet<FIntPoint> MissingTiles;

	// generation-id cross-tile check (2026-08-21, AUDIT-RELIABILITY.md
	// SS3.1/SS5.1): the id of the first tile this manager successfully
	// parsed for BuildingTilePrefix - every later tile is compared against
	// it, and a mismatch (tiles from two different pipeline runs streaming
	// side by side) is logged once, not blocked.
	FString FirstGenerationId;
	bool bHasFirstGenerationId = false;
	bool bGenerationMismatchWarned = false;

	UPROPERTY()
	TObjectPtr<UMaterialInterface> FacadeMat;

	double LastDiagTime = 0.0;
};
