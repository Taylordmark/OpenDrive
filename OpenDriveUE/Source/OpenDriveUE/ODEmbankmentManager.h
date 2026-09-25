#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "UObject/WeakObjectPtrTemplates.h"
#include "ODEmbankmentManager.generated.h"

class UDynamicMeshComponent;
class UMaterialInterface;

/** One parsed embankment RUN - the pavement-edge seat points (one per
 *  station) for a contiguous stretch classified "embankment" by
 *  pipeline/build_road_embankments.py, plus the road-above-terrain DROP at
 *  each station (cm) the runtime uses to size the 1.5:1 slope down to
 *  terrain. Mirrors FParsedGuardrailRun (ODGuardrailManager.h) - see
 *  build_road_embankments.py's docstring for why only points+drop are
 *  shipped, never a pre-triangulated mesh. */
struct FParsedEmbankmentRun
{
	TArray<FVector> Stations;   // world XYZ, cm, pavement-edge seat height
	TArray<float> DropCm;       // road-above-terrain drop at each station, cm
	uint8 Side = 0;             // 0 = side -1, 1 = side +1 (only affects which
	                             // outward direction is derived from the
	                             // per-station tangent - see .cpp)
};

struct FParsedEmbankmentTile
{
	TArray<FParsedEmbankmentRun> Runs;
	FString GenerationId;
};

/** Live components for one loaded tile: one UDynamicMeshComponent per run
 *  (the sloped skirt ribbon). */
struct FEmbankmentTile
{
	TArray<TObjectPtr<UDynamicMeshComponent>> SkirtRuns;
};

/**
 * Embankment skirts (Arc 7.19 follow-up to visible guardrails, user-approved
 * design, look target The Crew 2 - stylized-realistic): where the road rides
 * moderately above the surrounding terrain (2.5-7 m drop - pipeline/
 * build_road_embankments.py's classification; below that the terrain meets
 * the road acceptably on its own, above that a bridge deck is the later
 * task's job instead), this manager grows a sloped earthen skirt from the
 * pavement edge down to terrain, ~1.5:1 (horizontal:vertical), so the road
 * ribbon's raw side-extrusion face never shows as a bare beveled wall.
 *
 * Same "pipeline ships points, C++ extrudes at runtime" split as
 * AODGuardrailManager/AODBarrierManager, and the same tile-streaming shape
 * (copied nearly verbatim from AODGuardrailManager - see that class for the
 * shared reasoning). Two things are deliberately DIFFERENT from the
 * guardrail manager this was cloned from:
 *   - the cross-section is NOT a fixed profile table - the skirt's own
 *     height varies per station (the shipped DropCm value), so BuildSkirtMesh
 *     computes each station's toe/apron points on the fly instead of
 *     sampling a static FVector2D array the way BuildRailMesh does.
 *   - visual-only, NO collision - the guardrail's own thin-sheet ribbon
 *     already handles containment at every station this skirt also covers
 *     (an embankment run and a guardrail run are the same physical drop,
 *     just two different treatments of it), so giving the skirt mesh its
 *     own collision would be redundant collision geometry for no gameplay
 *     benefit.
 */
UCLASS()
class OPENDRIVEUE_API AODEmbankmentManager : public AActor
{
	GENERATED_BODY()

public:
	AODEmbankmentManager();
	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
	virtual void Tick(float DeltaSeconds) override;

	/** Sidecar filename prefix under Content/OpenDrive/Masks/; tile files
	 *  are "<prefix>_tile_<tx>_<ty>.bin". */
	UPROPERTY(EditAnywhere, Category = "Embankment")
	FString EmbankmentTilePrefix = TEXT("embankment_summit_county_test");

	/** World-space grid size, cm - MUST match pipeline/test_areas.py's
	 *  TERRAIN_TILE_SIZE_CM (same terrain tile grid every other streamed
	 *  layer in this project reuses). */
	UPROPERTY(EditAnywhere, Category = "Embankment")
	float TileSizeCm = 200000.0f;

	/** Modest by design - embankment runs only ever hug the pavement edge
	 *  (a few meters to a couple dozen meters wide at most, per the 1.5:1
	 *  slope over a <=7m drop), same reasoning as AODGuardrailManager::SpawnRadius. */
	UPROPERTY(EditAnywhere, Category = "Embankment")
	float SpawnRadius = 150000.0f;

	UPROPERTY(EditAnywhere, Category = "Embankment")
	float DespawnMarginCm = 100000.0f;

	UPROPERTY(EditAnywhere, Category = "Embankment")
	int32 MaxNewLoadsPerTick = 1;

	/** Horizontal:vertical slope ratio - 1.5 means 1.5 m of horizontal run
	 *  per 1 m of vertical drop, matching the user-approved design brief. */
	UPROPERTY(EditAnywhere, Category = "Embankment|Profile")
	float SlopeRatio = 1.5f;

	/** Toe apron: a short extra stretch beyond the idealized slope's own toe
	 *  point, sunk below the sampled terrain height, so the skirt mesh always
	 *  overlaps into the terrain mesh rather than risking a hairline gap
	 *  where the two don't land on exactly the same height (different
	 *  sampling/geometry paths - the terrain mesh is a carved heightmap,
	 *  this skirt is an idealized straight slope from a single probe-offset
	 *  drop sample, not a full terrain profile).
	 *
	 *  2026-09-07 offline verification (verify_embank_profile.png, mid-drop
	 *  ~5m case): comparing the idealized toe against the REAL DEM profile
	 *  along the same line found the two don't always agree - real terrain
	 *  can sit up to ~1m below where the straight-line 1.5:1 slope predicts
	 *  (the DEM profile isn't perfectly linear between the pavement edge and
	 *  the probe point the way the idealized slope assumes). The original
	 *  30cm/100cm embed/overlap was sized assuming near-exact agreement and
	 *  left a sub-meter sliver gap possible in that case - raised to
	 *  150cm/200cm here, cheaply: the apron is buried by construction
	 *  (nothing ever renders below the terrain surface it embeds into), so
	 *  embedding deeper than strictly necessary costs nothing visually and
	 *  buys real margin against exactly this kind of idealized-vs-real
	 *  terrain-profile mismatch. Not a substitute for a real terrain-follow
	 *  toe (out of scope for this pass - see pipeline/build_road_
	 *  embankments.py's docstring for why an idealized straight slope was
	 *  the chosen tradeoff), just a bigger safety margin around it. */
	UPROPERTY(EditAnywhere, Category = "Embankment|Profile")
	float ApronOverlapCm = 200.0f;

	UPROPERTY(EditAnywhere, Category = "Embankment|Profile")
	float ApronEmbedCm = 150.0f;

private:
	static bool ParseEmbankmentTileFile(const FString& Path, FParsedEmbankmentTile& Out);

	void RequestTileLoad(const FIntPoint& Key);
	void OnTileParsed(const FIntPoint& Key, bool bOk, FParsedEmbankmentTile&& Parsed);
	void FinalizeTile(const FIntPoint& Key, FParsedEmbankmentTile&& Parsed);
	void ReleaseTile(const FIntPoint& Key);

	/** Extrudes one run's sloped skirt ribbon into Comp: per station, three
	 *  layer points (edge/top at the shipped seat point, toe at
	 *  SlopeRatio*drop out and drop down, apron a further ApronOverlapCm out
	 *  and ApronEmbedCm below the toe) - see .cpp for the outward-direction
	 *  convention (mirrors AODGuardrailManager::BuildRailMesh's). UV.Y
	 *  carries 0 (top) / 0.6 (toe) / 1 (apron) so the material can darken
	 *  the albedo toward the toe without a second vertex-color channel. */
	void BuildSkirtMesh(UDynamicMeshComponent* Comp, const FParsedEmbankmentRun& Run) const;

	TMap<FIntPoint, FEmbankmentTile> Tiles;
	TSet<FIntPoint> PendingTiles;
	TSet<FIntPoint> MissingTiles;

	FString FirstGenerationId;
	bool bHasFirstGenerationId = false;
	bool bGenerationMismatchWarned = false;

	UPROPERTY()
	TObjectPtr<UMaterialInterface> EmbankMat;

	double LastDiagTime = 0.0;
};
