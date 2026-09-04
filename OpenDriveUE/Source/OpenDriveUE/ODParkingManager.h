#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODParkingManager.generated.h"

class UDynamicMeshComponent;
class UMaterialInterface;

/** One parsed parking lot - plain data, safe to build off the game thread.
 *  Mirrors the ODL1 sidecar's per-feature payload layout
 *  (pipeline/build_parking_tiles.py), the SAME wire format
 *  pipeline/build_water_tiles.py's lake tiles already use. */
struct FParsedParkingLot
{
	TArray<float> Verts;    // flat x,y,z (cm, UE world frame) - already baked
	                         // at the lot's own flat ground Z (nearest road
	                         // station elevation + a small anti-z-fight bias,
	                         // see build_parking_tiles.py's module docstring)
	TArray<float> Uvs;      // flat u,v
	TArray<uint32> Tris;    // flat i0,i1,i2
};

/** One parsed tile file's worth of parking lots. */
struct FParsedParkingTile
{
	TArray<FParsedParkingLot> Lots;
	FString GenerationId;   // pipeline/generation_id.py's per-run id
};

/** Live parking-lot components for one loaded tile - ONE UDynamicMeshComponent
 *  PER LOT (mirrors AODWaterManager's one-per-feature convention). */
struct FParkingTile
{
	TArray<TObjectPtr<UDynamicMeshComponent>> Lots;
};

/**
 * PARKING feature (art-direction §7.15 "paved town context" pass,
 * 2026-09-03): streams baked, flat, terrain-conforming paved-lot surfaces
 * in the same world-space tiles AODTerrainManager/AODBuildingManager/
 * AODWaterManager already use (TERRAIN_TILE_SIZE_CM) - a separate actor per
 * this project's one-actor-per-data-domain convention.
 *
 * Deliberately a near-verbatim copy of AODWaterManager (same ODL1 wire
 * format, same off-thread parse/game-thread finalize shape, same ring-
 * search tile streaming in Tick()) - every difference from that class is
 * called out inline below. Duplicated rather than shared/refactored, same
 * "each tile-streamed manager is a self-contained copy" convention this
 * project already uses across AODWaterManager/AODBuildingManager/
 * AODForestManager (see e.g. AODWaterManager.h's own docstring).
 *
 * Visual-only, NO collision (same reasoning as water): the underlying
 * terrain mesh already carries real drivable collision at this location -
 * this is a thin painted-on pavement decal a few cm above it, not a new
 * driving surface: adding a second BlockAll collision plane here would risk
 * the car settling on whichever of the two surfaces' collision resolves
 * first, an unnecessary physics-stability risk this pass has no reason to
 * take on for a purely cosmetic ground-context layer.
 */
UCLASS()
class OPENDRIVEUE_API AODParkingManager : public AActor
{
	GENERATED_BODY()

public:
	AODParkingManager();
	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
	virtual void Tick(float DeltaSeconds) override;

	/** Sidecar filename prefix under Content/OpenDrive/Masks/; tile files
	 *  are "<prefix>_tile_<tx>_<ty>.bin". */
	UPROPERTY(EditAnywhere, Category = "Parking")
	FString ParkingTilePrefix = TEXT("parking_summit_county_test");

	/** World-space grid size, cm. MUST match pipeline/test_areas.py's
	 *  TERRAIN_TILE_SIZE_CM. */
	UPROPERTY(EditAnywhere, Category = "Parking")
	float TileSizeCm = 200000.0f;

	/** Tiles fully populated inside this radius of the player (cm). Kept
	 *  modest (well below AODWaterManager's 400,000 landmark-visibility
	 *  radius) - a parking lot is a close-range ground-context detail, not a
	 *  landmark that needs to read from far away. */
	UPROPERTY(EditAnywhere, Category = "Parking")
	float SpawnRadius = 150000.0f;

	UPROPERTY(EditAnywhere, Category = "Parking")
	float DespawnMarginCm = 100000.0f;

	UPROPERTY(EditAnywhere, Category = "Parking")
	int32 MaxNewLoadsPerTick = 2;

private:
	static bool ParseParkingTileFile(const FString& Path, FParsedParkingTile& Out);

	void RequestTileLoad(const FIntPoint& Key);
	void OnTileParsed(const FIntPoint& Key, bool bOk, FParsedParkingTile&& Parsed);
	void FinalizeTile(const FIntPoint& Key, FParsedParkingTile&& Parsed);
	void ReleaseTile(const FIntPoint& Key);

	/** Builds the actual UDynamicMeshComponent for one already-baked lot. No
	 *  collision, no Z resolution step - FParsedParkingLot's verts already
	 *  carry their final world Z from the pipeline bake. */
	void CreateLotComponent(FParkingTile& Tile, const FParsedParkingLot& Lot);

	TMap<FIntPoint, FParkingTile> Tiles;
	TSet<FIntPoint> PendingTiles;
	TSet<FIntPoint> MissingTiles;

	// generation-id cross-tile check (AUDIT-RELIABILITY.md SS3.1/SS5.1
	// convention, same as every other tile-streamed manager here).
	FString FirstGenerationId;
	bool bHasFirstGenerationId = false;
	bool bGenerationMismatchWarned = false;

	UPROPERTY()
	TObjectPtr<UMaterialInterface> ParkingMat;

	double LastDiagTime = 0.0;
};
