#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "UObject/WeakObjectPtrTemplates.h"
#include "ODWallManager.generated.h"

class UDynamicMeshComponent;
class UMaterialInterface;

/** One parsed wall chunk - plain data, safe to build off the game thread.
 *  Mirrors the ODS1 sidecar's per-wall payload layout
 *  (pipeline/build_retaining_walls.py / export_retaining_wall_bin.py). */
struct FParsedWall
{
	TArray<float> Verts;    // flat x,y,z (cm, UE world frame)
	TArray<float> Uvs;      // flat u,v
	TArray<uint32> Tris;    // flat i0,i1,i2
};

/** One parsed tile file's worth of wall chunks - the off-thread output of
 *  AODWallManager::ParseWallTileFile, consumed on the game thread by
 *  OnTileParsed to build real components. */
struct FParsedWallTile
{
	TArray<FParsedWall> Walls;
};

/** Live wall components for one loaded tile - ONE UDynamicMeshComponent PER
 *  WALL CHUNK (mirrors AODBuildingManager, not AODTerrainManager's single
 *  grid mesh per tile), since a wall chunk needs independent collision the
 *  same way a building does. */
struct FWallTile
{
	TArray<TObjectPtr<UDynamicMeshComponent>> Walls;
};

/**
 * Phase 1 switchback retaining-wall geometry (2026-07-30): streams
 * procedurally-generated cut-face/retaining-wall meshes (real elevation-
 * conflict pairs classified by road_mesh_common.classify_wall_stations -
 * see that function and pipeline/build_retaining_walls.py for how the
 * geometry is built) in the same world-space tiles AODTerrainManager
 * already defines (TERRAIN_TILE_SIZE_CM) - a separate actor per this
 * project's one-actor-per-data-domain convention (AODRoadManager/
 * AODTerrainManager/AODBuildingManager/AODBarrierManager each own their own
 * tile-streaming loop independently), not a bolt-on to an existing
 * manager's tile struct. Walls only exist where a genuine switchback/
 * hairpin conflict was flagged - a tile with no walls.bin file is just an
 * ordinary tile with no such conflict nearby, same "no hole, just a missing
 * file" semantics every other tile-streamed system here already relies on.
 * Walls always have full collision - no far/no-collision tier, since a wall
 * only ever exists directly on the drivable corridor by construction (it
 * fills a gap the road's own carve couldn't otherwise resolve).
 */
UCLASS()
class OPENDRIVEUE_API AODWallManager : public AActor
{
	GENERATED_BODY()

public:
	AODWallManager();
	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
	virtual void Tick(float DeltaSeconds) override;

	/** Sidecar filename prefix under Content/OpenDrive/Masks/; tile files
	 *  are "<prefix>_tile_<tx>_<ty>.bin". */
	UPROPERTY(EditAnywhere, Category = "Walls")
	FString WallTilePrefix = TEXT("walls_oahu_test");

	/** World-space grid size, cm. MUST match pipeline/test_areas.py's
	 *  TERRAIN_TILE_SIZE_CM (walls reuse the terrain tile grid). */
	UPROPERTY(EditAnywhere, Category = "Walls")
	float TileSizeCm = 200000.0f;

	UPROPERTY(EditAnywhere, Category = "Walls")
	float SpawnRadius = 250000.0f;

	UPROPERTY(EditAnywhere, Category = "Walls")
	float DespawnMarginCm = 300000.0f;

	UPROPERTY(EditAnywhere, Category = "Walls")
	int32 MaxNewLoadsPerTick = 1;

private:
	static bool ParseWallTileFile(const FString& Path, FParsedWallTile& Out);

	void RequestTileLoad(const FIntPoint& Key);
	void OnTileParsed(const FIntPoint& Key, bool bOk, FParsedWallTile&& Parsed);
	void FinalizeTile(const FIntPoint& Key, FParsedWallTile&& Parsed);
	void ReleaseTile(const FIntPoint& Key);

	TMap<FIntPoint, FWallTile> Tiles;
	TSet<FIntPoint> PendingTiles;
	TSet<FIntPoint> MissingTiles;

	UPROPERTY()
	TObjectPtr<UMaterialInterface> WallMat;

	double LastDiagTime = 0.0;
};
