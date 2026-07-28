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
};

/** Live terrain component for one loaded tile - a single regular-grid mesh,
 *  unlike AODRoadManager's per-tile chunk list (terrain tiles are one flat
 *  grid, not several independently-chunked road segments). */
struct FTerrainTile
{
	TObjectPtr<UDynamicMeshComponent> Comp;
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

	/** Tiles fully populated inside this radius of the player (cm). */
	UPROPERTY(EditAnywhere, Category = "Terrain")
	float SpawnRadius = 400000.0f;

	/** Fixed world-space margin (cm) beyond the load radius before a tile is
	 *  released. */
	UPROPERTY(EditAnywhere, Category = "Terrain")
	float DespawnMarginCm = 500000.0f;

	/** Cap on new tile-load DISPATCHES per tick (not completions). */
	UPROPERTY(EditAnywhere, Category = "Terrain")
	int32 MaxNewLoadsPerTick = 2;

private:
	static bool ParseTerrainTileFile(const FString& Path, FParsedTerrainTile& Out);

	void RequestTileLoad(const FIntPoint& Key);
	void OnTileParsed(const FIntPoint& Key, bool bOk, FParsedTerrainTile&& Parsed);
	void FinalizeTile(const FIntPoint& Key, FParsedTerrainTile&& Parsed);
	void ReleaseTile(const FIntPoint& Key);

	TMap<FIntPoint, FTerrainTile> Tiles;

	// dispatched, awaiting the game-thread OnTileParsed callback
	TSet<FIntPoint> PendingTiles;
	// confirmed no file at this key (ENOENT or bad header) - don't retry;
	// also the natural "no hole, just don't build it" outcome for any tile
	// beyond the pipeline's render-distance cutoff
	TSet<FIntPoint> MissingTiles;

	UPROPERTY()
	TObjectPtr<UMaterialInterface> TerrainMat;

	double LastDiagTime = 0.0;
};
