#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "UObject/WeakObjectPtrTemplates.h"
#include "ODMenuVista.h"
#include "ODGuardrailManager.generated.h"

class UDynamicMeshComponent;
class UHierarchicalInstancedStaticMeshComponent;
class UMaterialInterface;
class UPhysicalMaterial;
class UStaticMesh;

/** One parsed guardrail RUN - the offset station centerline points (ground/
 *  pavement-edge level, one per station) for a contiguous stretch of rail,
 *  plus which side of the road it's on and which road class it belongs to.
 *  Mirrors FParsedWallRun (ODBarrierManager.h) - see pipeline/
 *  build_road_guardrails.py's docstring for why only points are shipped,
 *  never a pre-triangulated mesh. */
struct FParsedGuardrailRun
{
	TArray<FVector> Stations;   // world XYZ, cm, rail seat height (pavement edge + 30cm)
	uint8 Side = 0;             // 0 = side -1, 1 = side +1 (unused at runtime - geometry
	                             // is built purely from consecutive station tangents)
	uint8 ClassId = 6;          // road_mesh_common.ROAD_CLASS_ID
};

struct FParsedGuardrailTile
{
	TArray<FParsedGuardrailRun> Runs;
	FString GenerationId;
};

/** Live components for one loaded tile: one UDynamicMeshComponent per run
 *  (the actual collision ribbon, W-beam or Jersey cross-section per the
 *  run's class), plus one HISM of post instances per W-beam run (Jersey
 *  barrier is a solid concrete prism, no discrete posts). */
struct FGuardrailTile
{
	TArray<TObjectPtr<UDynamicMeshComponent>> RailRuns;
	TArray<TObjectPtr<UHierarchicalInstancedStaticMeshComponent>> PostHisms;
};

/**
 * Visible guardrails (Arc 7 follow-up, 2026-09-04, user-approved design):
 * where the road rides GUARDRAIL_DROP_THRESHOLD_M+ above the surrounding
 * terrain (pipeline/build_road_guardrails.py's detection - dam causeways,
 * mountain switchback outer edges), a real rail grows at the pavement edge.
 * Two profiles by road class: concrete Jersey barrier for motorway/
 * interstate, galvanized metal W-beam (with instanced posts) for everything
 * else. Same "pipeline ships points, C++ extrudes the ribbon at runtime"
 * split as AODBarrierManager - see that class's own comment for the
 * "discrete objects don't guarantee a solid wall" reasoning this reuses
 * (here for a multi-layer cross-section rather than a flat vertical sheet).
 *
 * Collision is a real, continuous thin-sheet ribbon (EnableComplexAsSimple,
 * BlockAll) - same proven pattern as the barrier wall - but tagged with a
 * LOW-FRICTION physical material (PM_GuardrailSlick) so a car that clips it
 * scrapes/slides along the rail instead of catching and stopping dead
 * ("Crew 2 deflect" feel, Taylor's design call).
 */
UCLASS()
class OPENDRIVEUE_API AODGuardrailManager : public AActor
{
	GENERATED_BODY()

public:
	AODGuardrailManager();
	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
	virtual void Tick(float DeltaSeconds) override;

	/** Garage-menu vista copy (2026-10-05), see AODGameMode::SpawnVistaSet. */
	FODVistaRegion Vista;

	/** Sidecar filename prefix under Content/OpenDrive/Masks/; tile files
	 *  are "<prefix>_tile_<tx>_<ty>.bin". */
	UPROPERTY(EditAnywhere, Category = "Guardrail")
	FString GuardrailTilePrefix = TEXT("guardrail_summit_county_test");

	/** World-space grid size, cm - MUST match pipeline/test_areas.py's
	 *  TERRAIN_TILE_SIZE_CM (guardrails reuse the terrain tile grid, same as
	 *  AODBarrierManager). */
	UPROPERTY(EditAnywhere, Category = "Guardrail")
	float TileSizeCm = 200000.0f;

	/** Modest by design - guardrail runs only ever hug the pavement edge
	 *  (< 5 m from the road), same reasoning as AODBarrierManager::SpawnRadius. */
	UPROPERTY(EditAnywhere, Category = "Guardrail")
	float SpawnRadius = 150000.0f;

	UPROPERTY(EditAnywhere, Category = "Guardrail")
	float DespawnMarginCm = 100000.0f;

	UPROPERTY(EditAnywhere, Category = "Guardrail")
	int32 MaxNewLoadsPerTick = 1;

	/** W-beam cross-section: (lateral offset outward from the rail seat
	 *  line, cm; height above the seat, cm), bottom to top - 3 quads, angled
	 *  face bulging outward with a top lip curling back in. ~92cm tall -
	 *  deliberately taller than a real W-beam's ~70cm (2026-09-07, Taylor's
	 *  drive-test: rails read too low from the driver camera and were too
	 *  easy to hop at speed; heights scaled ~1.28x, lateral shape kept). */
	UPROPERTY(EditAnywhere, Category = "Guardrail|Profile")
	TArray<FVector2D> WBeamProfile = {
		FVector2D(0.0, 0.0), FVector2D(0.0, 58.0),
		FVector2D(-14.0, 74.0), FVector2D(-6.0, 92.0)
	};

	/** Concrete Jersey barrier cross-section - simple sloped-face prism,
	 *  wide base narrowing to a vertical top section. ~100cm tall (tall
	 *  F-shape-barrier territory rather than the standard 81cm Jersey -
	 *  same 2026-09-07 raise as WBeamProfile above). */
	UPROPERTY(EditAnywhere, Category = "Guardrail|Profile")
	TArray<FVector2D> JerseyProfile = {
		FVector2D(0.0, 0.0), FVector2D(-18.0, 0.0),
		FVector2D(-9.0, 62.0), FVector2D(-4.0, 100.0), FVector2D(0.0, 100.0)
	};

	/** Post instance spacing along a W-beam run, cm. */
	UPROPERTY(EditAnywhere, Category = "Guardrail|Profile")
	float PostSpacingCm = 400.0f;

	UPROPERTY(EditAnywhere, Category = "Guardrail|Profile")
	float PostRadiusCm = 6.0f;

	UPROPERTY(EditAnywhere, Category = "Guardrail|Profile")
	float PostHeightCm = 105.0f;  // includes ~15cm embedded below the rail seat
	                              // (raised with the 2026-09-07 profile bump so
	                              // posts still reach just under the beam top)

private:
	static bool ParseGuardrailTileFile(const FString& Path, FParsedGuardrailTile& Out);

	void RequestTileLoad(const FIntPoint& Key);
	void OnTileParsed(const FIntPoint& Key, bool bOk, FParsedGuardrailTile&& Parsed);
	void FinalizeTile(const FIntPoint& Key, FParsedGuardrailTile&& Parsed);
	void ReleaseTile(const FIntPoint& Key);

	/** Extrudes one run's ribbon mesh (multi-layer cross-section, per
	 *  WBeamProfile/JerseyProfile) into Comp, seated on the run's own
	 *  station polyline with a per-station outward direction derived from
	 *  consecutive-station tangents (see .cpp for the sign convention). */
	void BuildRailMesh(UDynamicMeshComponent* Comp, const FParsedGuardrailRun& Run,
	                    const TArray<FVector2D>& Profile) const;

	TMap<FIntPoint, FGuardrailTile> Tiles;
	TSet<FIntPoint> PendingTiles;
	TSet<FIntPoint> MissingTiles;

	FString FirstGenerationId;
	bool bHasFirstGenerationId = false;
	bool bGenerationMismatchWarned = false;

	UPROPERTY()
	TObjectPtr<UMaterialInterface> WBeamMat;

	UPROPERTY()
	TObjectPtr<UMaterialInterface> JerseyMat;

	UPROPERTY()
	TObjectPtr<UPhysicalMaterial> SlickPhysMat;

	UPROPERTY()
	TObjectPtr<UStaticMesh> PostMesh;

	double LastDiagTime = 0.0;
};
