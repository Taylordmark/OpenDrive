#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODMenuVista.h"
#include "ODFurnitureManager.generated.h"

class AODDayNightController;
class UHierarchicalInstancedStaticMeshComponent;
class UMaterialInstanceDynamic;
class UStaticMesh;

/** One parsed ODF1 prop (pipeline/export_road_furniture_bin.py). */
struct FParsedFurnitureProp
{
	uint8 Type = 0;      // build_road_furniture.TYPES index
	FVector Pos;         // area-local cm, base of the prop
	float YawDeg = 0.0f; // mesh +X faces this way
	float ScaleY = 1.0f; // stop-bar width, else 1
};

struct FParsedFurnitureTile
{
	TArray<FParsedFurnitureProp> Props;
	FString GenerationId;
};

struct FFurnitureTile
{
	TArray<TObjectPtr<UHierarchicalInstancedStaticMeshComponent>> Hisms;
};

/**
 * Roadside furniture (PLAN-REALISM Phase 1, 2026-10-06): signals, signs,
 * chevrons, street lights, delineators and stop bars from pipeline/
 * build_road_furniture.py, streamed per 2 km tile exactly like
 * AODGuardrailManager (same grid, async parse, vista mode). One visual HISM
 * per type per tile (no collision) plus one hidden box-collision HISM for
 * every pole (delineators and stop bars have none). Street-light heads glow
 * while AODDayNightController has the headlights on.
 */
UCLASS()
class OPENDRIVEUE_API AODFurnitureManager : public AActor
{
	GENERATED_BODY()

public:
	AODFurnitureManager();
	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

	/** Garage-menu vista copy, see AODGameMode::SpawnVistaSet. */
	FODVistaRegion Vista;

	/** Tile files are "<prefix>_tile_<tx>_<ty>.bin" under Content/OpenDrive/Masks/. */
	UPROPERTY(EditAnywhere, Category = "Furniture")
	FString FurnitureTilePrefix = TEXT("furniture_summit_county_test");

	/** MUST match pipeline/test_areas.py's TERRAIN_TILE_SIZE_CM. */
	UPROPERTY(EditAnywhere, Category = "Furniture")
	float TileSizeCm = 200000.0f;

	UPROPERTY(EditAnywhere, Category = "Furniture")
	float SpawnRadius = 150000.0f;

	UPROPERTY(EditAnywhere, Category = "Furniture")
	float DespawnMarginCm = 100000.0f;

	UPROPERTY(EditAnywhere, Category = "Furniture")
	int32 MaxNewLoadsPerTick = 1;

	/** Emissive "Glow" on the lamp slot while the headlights are on. */
	UPROPERTY(EditAnywhere, Category = "Furniture")
	float LampGlow = 20.0f;

private:
	static bool ParseFurnitureTileFile(const FString& Path, FParsedFurnitureTile& Out);

	void RequestTileLoad(const FIntPoint& Key);
	void OnTileParsed(const FIntPoint& Key, bool bOk, FParsedFurnitureTile&& Parsed);
	void FinalizeTile(const FIntPoint& Key, FParsedFurnitureTile&& Parsed);
	void ReleaseTile(const FIntPoint& Key);
	void UpdateLamps();

	TMap<FIntPoint, FFurnitureTile> Tiles;
	TSet<FIntPoint> PendingTiles;
	TSet<FIntPoint> MissingTiles;

	FString FirstGenerationId;
	bool bHasFirstGenerationId = false;
	bool bGenerationMismatchWarned = false;

	UPROPERTY()
	TArray<TObjectPtr<UStaticMesh>> Meshes;   // by type id

	UPROPERTY()
	TObjectPtr<UStaticMesh> BoxMesh;

	UPROPERTY()
	TObjectPtr<UMaterialInstanceDynamic> LampMid;

	TWeakObjectPtr<AODDayNightController> DayNight;
	bool bLampsOn = false;
	double LastLampCheck = -1.0;
	double LastDiagTime = 0.0;
};
