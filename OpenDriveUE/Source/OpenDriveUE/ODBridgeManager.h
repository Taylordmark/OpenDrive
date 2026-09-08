#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "UObject/WeakObjectPtrTemplates.h"
#include "ODBridgeManager.generated.h"

class UDynamicMeshComponent;
class UHierarchicalInstancedStaticMeshComponent;
class UMaterialInterface;
class UStaticMesh;

/** One parsed bridge RUN - the pavement-edge seat points (one per station)
 *  for a contiguous stretch classified "bridge" (a GENUINE short span, not a
 *  demoted sidehill cut - see pipeline/build_road_embankments.py's §7.21
 *  sub-classification docstring) by pipeline/build_road_bridges.py, plus the
 *  road-above-terrain DROP at each station (cm) and a sparse list of PIER
 *  placements. Mirrors FParsedEmbankmentRun (ODEmbankmentManager.h) almost
 *  exactly - see build_road_bridges.py's docstring for why only points are
 *  shipped, never a pre-triangulated mesh. */
struct FParsedBridgeRun
{
	TArray<FVector> Stations;   // world XYZ, cm, pavement-edge seat point (deck top)
	TArray<float> DropCm;       // road-above-terrain drop at each station, cm
	uint8 Side = 0;             // 0 = side -1, 1 = side +1 (outward-direction sign,
	                             // same convention as ODEmbankmentManager)

	/** Sparse pier placements along this run - only where pipeline/
	 *  build_road_bridges.py found real clearance (see that module's
	 *  MIN_PIER_CLEARANCE_M). */
	TArray<FVector> PierTops;   // world XYZ, cm - pier TOP (deck underside point)
	TArray<float> PierHeightCm; // pier's own vertical extent down to terrain, cm
};

struct FParsedBridgeTile
{
	TArray<FParsedBridgeRun> Runs;
	FString GenerationId;
};

/** Live components for one loaded tile: one UDynamicMeshComponent per run
 *  (the deck fascia + underside slab ribbon) plus one HISM of pier column
 *  instances per run. */
struct FBridgeTile
{
	TArray<TObjectPtr<UDynamicMeshComponent>> DeckRuns;
	TArray<TObjectPtr<UHierarchicalInstancedStaticMeshComponent>> PierHisms;
};

/**
 * Bridge decks (Arc 7.21, follow-on to visible guardrails/embankment skirts,
 * user-approved design, look target The Crew 2 - stylized-realistic): where a
 * road crosses a real gap - a ravine, a reservoir arm, another road - short
 * enough and drop-on-both-sides enough to read as a genuine span rather than
 * a mountainside sidehill cut (pipeline/build_road_embankments.py's own
 * §7.21 sub-classification, NOT the raw >7m drop threshold alone - see that
 * module's docstring for the full discriminator and why most of the raw
 * "bridge" classification was actually benched mountain terrain, not a real
 * structure), this manager grows a concrete deck fascia (side wall + a short
 * underside-slab flap hanging below the pavement edge) and a sparse row of
 * rectangular piers down to terrain.
 *
 * Same "pipeline ships points, C++ extrudes at runtime" split as every other
 * streamed layer in this project, and the same tile-streaming shape (copied
 * nearly verbatim from AODEmbankmentManager - see that class for the shared
 * reasoning). Two things carried over from that class on purpose: visual-
 * only, NO collision on the deck mesh (the road ribbon itself already
 * provides the drivable collision surface at the same XY footprint - a
 * second collision mesh directly under/beside it would be redundant, exactly
 * AODEmbankmentManager's own reasoning for its skirt); and piers use
 * instanced static meshes (the engine's own BasicShapes/Cylinder, scaled per
 * instance) rather than a custom DynamicMesh column, mirroring
 * AODGuardrailManager's own W-beam post HISM - concrete piers read fine as
 * simple cylinders at this scale and it's far cheaper than hand-building a
 * box mesh per pier.
 */
UCLASS()
class OPENDRIVEUE_API AODBridgeManager : public AActor
{
	GENERATED_BODY()

public:
	AODBridgeManager();
	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
	virtual void Tick(float DeltaSeconds) override;

	/** Sidecar filename prefix under Content/OpenDrive/Masks/; tile files
	 *  are "<prefix>_tile_<tx>_<ty>.bin". */
	UPROPERTY(EditAnywhere, Category = "Bridge")
	FString BridgeTilePrefix = TEXT("bridge_summit_county_test");

	/** World-space grid size, cm - MUST match pipeline/test_areas.py's
	 *  TERRAIN_TILE_SIZE_CM (same terrain tile grid every other streamed
	 *  layer in this project reuses). */
	UPROPERTY(EditAnywhere, Category = "Bridge")
	float TileSizeCm = 200000.0f;

	/** Genuine spans are short (pipeline/build_road_embankments.py's own
	 *  BRIDGE_GENUINE_MAX_LEN_M = 300m cap on the "short + both-sided" rule),
	 *  so a modest spawn radius is plenty - same reasoning as
	 *  AODEmbankmentManager::SpawnRadius. */
	UPROPERTY(EditAnywhere, Category = "Bridge")
	float SpawnRadius = 150000.0f;

	UPROPERTY(EditAnywhere, Category = "Bridge")
	float DespawnMarginCm = 100000.0f;

	UPROPERTY(EditAnywhere, Category = "Bridge")
	int32 MaxNewLoadsPerTick = 1;

	/** Deck fascia depth below the pavement-edge seat point, cm - MUST match
	 *  pipeline/build_road_bridges.py's DECK_DEPTH_M (that script already
	 *  baked pier tops DECK_DEPTH_M below the seat, so this and that constant
	 *  need to agree for a pier top to actually meet the fascia underside). */
	UPROPERTY(EditAnywhere, Category = "Bridge|Profile")
	float DeckDepthCm = 125.0f;

	/** Underside slab flap - how far INWARD (toward the road centerline,
	 *  opposite the skirt's own outward convention) the fascia's bottom edge
	 *  extends at the same depth, so the deck reads as having real slab
	 *  thickness from below/the side rather than a bare vertical curtain.
	 *  Deliberately modest and NOT a full across-the-road slab: each side's
	 *  run is built independently from its own shipped points (same one-
	 *  side-at-a-time convention as every other layer here), so there's no
	 *  guaranteed matching data from the opposite edge to close a full-width
	 *  underside - a several-meter flap sells the look from the angles a
	 *  driving game actually shows without needing that. */
	UPROPERTY(EditAnywhere, Category = "Bridge|Profile")
	float UndersideFlapCm = 250.0f;

	UPROPERTY(EditAnywhere, Category = "Bridge|Profile")
	float PierWidthCm = 150.0f;

private:
	static bool ParseBridgeTileFile(const FString& Path, FParsedBridgeTile& Out);

	void RequestTileLoad(const FIntPoint& Key);
	void OnTileParsed(const FIntPoint& Key, bool bOk, FParsedBridgeTile&& Parsed);
	void FinalizeTile(const FIntPoint& Key, FParsedBridgeTile&& Parsed);
	void ReleaseTile(const FIntPoint& Key);

	/** Extrudes one run's deck fascia + underside-flap ribbon into Comp -
	 *  three layer points per station (Top at the shipped seat point,
	 *  BottomOuter DeckDepthCm straight down, BottomInner a further
	 *  UndersideFlapCm INWARD at the same depth) - same 3-layer-per-station
	 *  quad-strip shape as AODEmbankmentManager::BuildSkirtMesh, just a
	 *  vertical-then-inward profile instead of a diagonal-outward one. */
	void BuildDeckMesh(UDynamicMeshComponent* Comp, const FParsedBridgeRun& Run) const;

	TMap<FIntPoint, FBridgeTile> Tiles;
	TSet<FIntPoint> PendingTiles;
	TSet<FIntPoint> MissingTiles;

	FString FirstGenerationId;
	bool bHasFirstGenerationId = false;
	bool bGenerationMismatchWarned = false;

	UPROPERTY()
	TObjectPtr<UMaterialInterface> BridgeMat;

	UPROPERTY()
	TObjectPtr<UStaticMesh> PierMesh;

	double LastDiagTime = 0.0;
};
