#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "UObject/WeakObjectPtrTemplates.h"
#include "ODRoadManager.generated.h"

class UDynamicMeshComponent;
class UMaterialInterface;
class UTexture2D;

/** One parsed road-mesh chunk, plain data only - safe to build off the game
 *  thread (no UObject/component touched). Mirrors the per-chunk payload
 *  layout of the ODR2 sidecar format. */
struct FParsedChunk
{
	TArray<float> Verts;    // flat x,y,z (cm, UE world frame)
	TArray<float> Uvs;      // flat u,v
	TArray<uint32> Tris;    // flat i0,i1,i2

	// ODR2 (2026-07-25): true for a one-way carriageway (e.g. a divided
	// highway's own direction of travel) - FinalizeTile() picks RoadMatOneWay
	// instead of RoadMat for these, since the double-yellow centerline baked
	// into RoadMat's texture only belongs on a genuine two-way road (see
	// pipeline/build_road_stations.py's is_oneway_way()).
	bool bOneway = false;
};

/** One parsed tile file's worth of chunks - the off-thread output of
 *  AODRoadManager::ParseRoadTileFile, consumed on the game thread by
 *  OnTileParsed to build real components. */
struct FParsedRoadTile
{
	TArray<FParsedChunk> Chunks;
};

/** Live road-mesh components for one loaded tile. Plain struct (not a
 *  USTRUCT) - same convention as AODForestManager's FTile: the components
 *  are kept alive by actor ownership (NewObject with Outer=this,
 *  RegisterComponent), not by TMap reflection. */
struct FRoadTile
{
	TArray<TObjectPtr<UDynamicMeshComponent>> Chunks;
};

/** One sparse road-name/city-name point (pipeline/export_place_labels_bin.py's
 *  ODL1 sidecar) - loaded whole at BeginPlay (a few thousand points at most,
 *  unlike the road/terrain tiles) and nearest-nooked at runtime, same
 *  pattern as AODForestManager::BlendAt's classified biome points. */
struct FPlaceLabel
{
	FVector2D Pos = FVector2D::ZeroVector;   // world XY, cm
	FString RoadLabel;
	FString CityLabel;
};

/** One clickable spawn destination on the map (2026-07-25): a real named
 *  town/place, deduplicated from the place-label points and positioned at its
 *  label-point centroid snapped to the nearest road-centerline point, so
 *  picking it spawns the car on the road at that town. Empty on levels without
 *  place-label data (Tail of the Dragon), where the map-select just falls back
 *  to the level's PlayerStart. */
struct FTownNode
{
	FString Name;
	FVector2D Pos = FVector2D::ZeroVector;   // world XY, cm (on the centerline)
};

/**
 * Arc 7.2 slice 2: streams the OSM road-network mesh in fixed-size
 * world-space tiles (pipeline/export_road_mesh_bin.py buckets chunks by
 * centroid into TileSizeCm-sided grid cells, one ODR2 sidecar per non-empty
 * cell) instead of slice 1's single whole-network load. Tick() copies
 * AODForestManager's ring-iteration/hysteresis spawn-despawn pattern, but
 * tile *building* happens off the game thread (Async + AsyncTask) since a
 * synchronous file-read-and-parse per tile would hitch exactly when the car
 * crosses a tile boundary at speed - the first async/background-task code
 * in this module. HTTP fetch + a disk cache are deliberately NOT part of
 * this slice (tiles still load from local packaged Content, same model as
 * AODForestManager's mask); that's a later slice once this core loop is
 * proven.
 */
UCLASS()
class OPENDRIVEUE_API AODRoadManager : public AActor
{
	GENERATED_BODY()

public:
	AODRoadManager();
	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
	virtual void Tick(float DeltaSeconds) override;

	/** Sidecar filename prefix under Content/OpenDrive/Masks/; tile files
	 *  are "<prefix>_tile_<tx>_<ty>.bin". */
	UPROPERTY(EditAnywhere, Category = "Road")
	FString RoadTilePrefix = TEXT("road_oahu_test");

	/** World-space grid size, cm. MUST match pipeline/test_areas.py's
	 *  ROAD_TILE_SIZE_CM - a mismatch doesn't crash, it just probes
	 *  filenames that don't exist and silently loads no road. */
	UPROPERTY(EditAnywhere, Category = "Road")
	float TileSizeCm = 200000.0f;

	/** Tiles fully populated inside this radius of the player (cm). */
	UPROPERTY(EditAnywhere, Category = "Road")
	float SpawnRadius = 400000.0f;

	/** Fixed world-space margin (cm) beyond the load radius before a tile is
	 *  released - a world-space hysteresis band, not a bare tile-count
	 *  constant (which wouldn't scale correctly across different
	 *  TileSizeCm values). */
	UPROPERTY(EditAnywhere, Category = "Road")
	float DespawnMarginCm = 500000.0f;

	/** Cap on new tile-load DISPATCHES per tick (not completions - the I/O
	 *  itself is async) so a big SpawnRadius or a fast ring-crossing can't
	 *  thundering-herd the thread pool with simultaneous file opens. */
	UPROPERTY(EditAnywhere, Category = "Road")
	int32 MaxNewLoadsPerTick = 2;

	/** Content/OpenDrive/Masks/<prefix>.bin - full-density road centerline
	 *  (pipeline/export_road_centerline_bin.py, same ODC1 sidecar format
	 *  ODRaceManager/ODForestManager already load). Lets ODRaceHUD draw the
	 *  mini-map/full map from THIS actor when no AODRaceManager exists on
	 *  the level - checkpoint races don't apply to open-world levels
	 *  (see AODRoadManager's own class comment), but they still have a road
	 *  worth drawing a route for. Empty means no route to draw here; the
	 *  HUD falls back to AODRaceManager's copy if that actor exists instead. */
	UPROPERTY(EditAnywhere, Category = "Road")
	FString CenterlineFilePrefix;

	/** Parsed from CenterlineFilePrefix at BeginPlay; empty if unset or the
	 *  sidecar is missing. World XY, cm. */
	UPROPERTY()
	TArray<FVector2D> RoadCenterline;

	/** Content/OpenDrive/Masks/<prefix>.bin - map-drawing-only centerline
	 *  (pipeline/export_road_centerline_bin.py's ODM1 sidecar, 2026-07-28).
	 *  Unlike RoadCenterline/CenterlineFilePrefix (one flat array, every
	 *  segment concatenated with no boundary marker - fine for a nearest-
	 *  point distance query, wrong for drawing a connected line), this is
	 *  per-segment-simplified with an FVector2D(NAN, NAN) sentinel between
	 *  segments that DrawStageMap must treat as "don't connect across this
	 *  gap". Found necessary on Oahu's 8,848-segment network: the old draw
	 *  path connected the last point of one street straight to the first
	 *  point of an unrelated one thousands of times, AND drew every one of
	 *  ~780,000 unsimplified 5 m stations every frame (the actual map-lag
	 *  report). Empty means unset - DrawStageMap falls back to
	 *  RoadCenterline (see AODRaceHUD::DrawStageMap). */
	UPROPERTY(EditAnywhere, Category = "Road")
	FString MapCenterlineFilePrefix;

	/** Parsed from MapCenterlineFilePrefix at BeginPlay; empty if unset or
	 *  the sidecar is missing. World XY, cm, with NAN-sentinel breaks
	 *  between segments - see MapCenterlineFilePrefix's comment. */
	UPROPERTY()
	TArray<FVector2D> MapCenterline;

	/** Content/OpenDrive/Masks/<prefix>.bin - sparse road-name/city-name
	 *  points (pipeline/export_place_labels_bin.py's ODL1 format). Empty
	 *  means the feature is off for this level (same convention as
	 *  CenterlineFilePrefix) - CurrentRoadLabel/CurrentCityLabel just stay
	 *  empty strings, which ODRaceHUD already treats as "draw nothing". */
	UPROPERTY(EditAnywhere, Category = "Road")
	FString PlaceLabelFilePrefix;

	/** Nearest-point lookup result as of the last throttled update (see
	 *  Tick()) - ODRaceHUD reads these directly rather than re-scanning
	 *  PlaceLabels itself every frame. Empty if no data/labels nearby. */
	UPROPERTY()
	FString CurrentRoadLabel;

	UPROPERTY()
	FString CurrentCityLabel;

	/** Stylized terrain-color map background (2026-07-26, pipeline/
	 *  build_map_texture.py + unreal/apply_map_texture.py) - drawn behind the
	 *  road line on the mini-map/full map so it reads as real terrain
	 *  (water/grass/forest/rock) instead of a bare line on flat black. Null
	 *  means unset - ODRaceHUD falls back to its original solid-color
	 *  background, same optional-feature convention as CenterlineFilePrefix. */
	UPROPERTY(EditAnywhere, Category = "Road")
	TObjectPtr<UTexture2D> MapTexture;

	/** World XY (cm), same local east/north frame as RoadCenterline/
	 *  PlaceLabels, at MapTexture's center. */
	UPROPERTY(EditAnywhere, Category = "Road")
	FVector2D MapTextureCenterCm = FVector2D::ZeroVector;

	/** World-space width/height (cm) MapTexture covers - one value for both
	 *  axes since the pipeline crops the source imagery to a real-world
	 *  square before downsampling specifically so there's no aspect ratio to
	 *  track on this side. <= 0 means unset (same as MapTexture == nullptr). */
	UPROPERTY(EditAnywhere, Category = "Road")
	float MapTextureSpanCm = 0.0f;

	/** Clickable town destinations for the main-menu map (built once at
	 *  BeginPlay from PlaceLabels). Empty on levels without place data. */
	const TArray<FTownNode>& GetTownNodes() const { return TownNodes; }

	/** Nearest road-centerline point to a world-XY query, plus the road
	 *  heading there (yaw, deg) - used to snap a map-spawn pick onto the road
	 *  and face the car along it. False if no centerline is loaded. */
	bool NearestRoadPoint(const FVector2D& Query, FVector2D& OutPoint,
		float& OutYawDeg) const;

private:
	void LoadCenterline();
	void LoadMapCenterline();
	void LoadPlaceLabels();
	void BuildTownNodes();
	void UpdateCurrentLabels(const FVector& PlayerLoc);

	TArray<FTownNode> TownNodes;

	// mirrors pipeline/road_mesh_common.py's STATION_SPACING (5.0 m) *
	// CHUNK_STATIONS (150) - the worst-case along-route span a single
	// chunk can cover, used to pad the load radius so a chunk whose
	// centroid buckets into a farther tile can't have geometry the player
	// reaches before that tile is requested.
	static constexpr float StationSpacingCm = 500.0f;
	static constexpr int32 ChunkStations = 150;
	static constexpr float MaxChunkSpanCm = ChunkStations * StationSpacingCm;

	static bool ParseRoadTileFile(const FString& Path, FParsedRoadTile& Out);

	void RequestTileLoad(const FIntPoint& Key);
	void OnTileParsed(const FIntPoint& Key, bool bOk, FParsedRoadTile&& Parsed);
	void FinalizeTile(const FIntPoint& Key, FParsedRoadTile&& Parsed);
	void ReleaseTile(const FIntPoint& Key);
	void RunSeamCheck();

	TMap<FIntPoint, FRoadTile> Tiles;

	// dispatched, awaiting the game-thread OnTileParsed callback
	TSet<FIntPoint> PendingTiles;
	// confirmed no file at this key (ENOENT or bad header) - don't retry
	TSet<FIntPoint> MissingTiles;

	UPROPERTY()
	TObjectPtr<UMaterialInterface> RoadMat;

	// one-way carriageway variant (no double-yellow centerline) - see
	// FParsedChunk::bOneway.
	UPROPERTY()
	TObjectPtr<UMaterialInterface> RoadMatOneWay;

	// skirt-bottom world positions sampled every 10th station as tiles
	// load, for -odseam
	TArray<FVector> SkirtSamplePoints;

	// captured on the first Tick() - a stable -odseam anchor near where the
	// player actually starts. Found 2026-07-20: using SkirtSamplePoints[0]
	// (the first-arrived skirt sample) as the anchor assumed tiles finish
	// loading in ring/dispatch order, but thread-pool completions don't -
	// on this network's 18-tile spawn-radius load, the first-ARRIVED sample
	// landed 3.4 km from spawn in an entirely different tile, so every
	// "nearby" (within MaxCheckDistCm) trace missed real terrain that just
	// hadn't streamed in that far away, reporting a false n=0. SpawnAnchor
	// keeps the original intent (don't anchor on the LIVE pawn - a scripted
	// drive can carry it far from the tested area by the time the check
	// runs) while actually being near the road that got tested.
	FVector SpawnAnchor = FVector::ZeroVector;
	bool bSpawnAnchorCaptured = false;

	double LastDiagTime = 0.0;

	TArray<FPlaceLabel> PlaceLabels;
	double LastLabelUpdateTime = 0.0;
};
