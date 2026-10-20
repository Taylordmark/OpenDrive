#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "UObject/WeakObjectPtrTemplates.h"
#include "ODMenuVista.h"
#include "ODRoadManager.generated.h"

class UDynamicMeshComponent;
class UPrimitiveComponent;
class UMaterialInstanceDynamic;
class UMaterialInterface;
class UTexture2D;
class UODRoadDecals;

/** One parsed road-mesh chunk, plain data only - safe to build off the game
 *  thread (no UObject/component touched). Mirrors the per-chunk payload
 *  layout of the ODR2 sidecar format. */
struct FParsedChunk
{
	TArray<float> Verts;    // flat x,y,z (cm, UE world frame)
	TArray<float> Uvs;      // flat u0,v0 (pavement-normalized lateral / meters along centerline)
	TArray<uint32> Tris;    // flat i0,i1,i2

	// ODR2 (2026-07-25): true for a one-way carriageway (e.g. a divided
	// highway's own direction of travel) - FinalizeTile() picks RoadMatOneWay
	// instead of RoadMat for these, since the double-yellow centerline baked
	// into RoadMat's texture only belongs on a genuine two-way road (see
	// pipeline/build_road_stations.py's is_oneway_way()).
	bool bOneway = false;

	// ODR3 flags bits1-2 (2026-10-06, PLAN-REALISM Phase 2): OSM surface=
	// class, 0 paved / 1 gravel / 2 dirt - CreateChunkMeshComponent picks
	// RoadMatGravel / RoadMatDirt (PM_Gravel / PM_DirtRoad grip and audio).
	uint8 SurfaceClass = 0;

	// ODR3 (2026-09-03, Phase 2a road-visual overhaul - see
	// pipeline/export_road_mesh_bin_v2.py's docstring for the wire format).
	// Empty/default when this chunk came from an ODR2 fallback file (no
	// MasksV2 tile for this key) - CreateChunkMeshComponent treats that the
	// same as "unknown road, use plain worn asphalt, no markings".
	TArray<float> Uv1;         // flat u1,v1 - u1 = road HALF-width in meters
	                            // (constant per chunk today), v1 reserved (0)
	uint8 RoadClassId = 6;     // 0=motorway..6=service/unknown, see
	                            // pipeline/road_mesh_common.py's ROAD_CLASS_ID
	uint8 LaneCount = 2;
	bool bHasV3Data = false;   // true only for chunks parsed from an actual
	                            // ODR3 sidecar - gates the UV1 overlay/lane-
	                            // marking vertex-color bake in
	                            // CreateChunkMeshComponent so an ODR2 fallback
	                            // chunk doesn't fabricate class/lane data it
	                            // doesn't have.

	// Realistic only (PLAN-VISUALS J): UV3.y road-edge code per triangle
	// corner (Tris order) - junction pad edge distance / ribbon-in-pad, see
	// ComputeEdgeCodes in the .cpp. Empty = all 0 (Stylized, no pads near).
	TArray<float> EdgeCode;
};

/** One parsed tile file's worth of chunks - the off-thread output of
 *  AODRoadManager::ParseRoadTileFile, consumed on the game thread by
 *  OnTileParsed to build real components. */
struct FParsedRoadTile
{
	TArray<FParsedChunk> Chunks;

	// pipeline/generation_id.py's per-run id, read from the ODR2 header
	// (2026-08-21) - see AODRoadManager::OnTileParsed for the cross-tile
	// mismatch check this feeds.
	FString GenerationId;
};

/** Live road-mesh components for one loaded tile. Plain struct (not a
 *  USTRUCT) - same convention as AODForestManager's FTile: the components
 *  are kept alive by actor ownership (NewObject with Outer=this,
 *  RegisterComponent), not by TMap reflection. */
struct FRoadTile
{
	TArray<TObjectPtr<UDynamicMeshComponent>> Chunks;
};

/** One tile's worth of chunk components still being built, a few per tick
 *  (see AODRoadManager::ProcessPendingChunkBuilds) instead of all at once -
 *  see that function's header comment on OnTileParsed for why. */
struct FPendingTileBuild
{
	FIntPoint Key = FIntPoint::ZeroValue;
	FParsedRoadTile Parsed;
	int32 NextChunkIndex = 0;
	FRoadTile InProgress;
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

/** One clickable spawn destination on the map: a Turnout (2026-10-14) -
 *  a named place snapped to a parkable road point, read from menu_spots.json
 *  "starts" (pipeline/build_menu_spots.py turnouts), the menu's own list.
 *  Empty on levels without a menu spot (Tail of the Dragon), where the
 *  map-select just falls back to the level's PlayerStart. */
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

	/** Garage-menu vista copy (2026-10-05), see AODGameMode::SpawnVistaSet. */
	FODVistaRegion Vista;

	/** Weather W2: push "Wetness" to the shared road MIDs (paved, gravel, dirt);
	 *  2026-10-16 also "Rain" (ripple rate) and "Puddles" (standing water). */
	void SetWetness(float Wetness, float Rain = 0.0f, float Puddles = 0.0f);

	/** Road snow depth (cm) on Comp if it's one of this class's chunks
	 *  (Custom Primitive Data 0, see TickSnow), else Fallback. */
	static float ChunkSnowCm(const UPrimitiveComponent* Comp, float Fallback);

	/** Sidecar filename prefix under Content/OpenDrive/Masks/; tile files
	 *  are "<prefix>_tile_<tx>_<ty>.bin" (ODR2 format).
	 *
	 *  2026-09-03 (Phase 2a road-visual overhaul): RequestTileLoad ALSO
	 *  probes Content/OpenDrive/MasksV2/roadv3_<suffix>_tile_<tx>_<ty>.bin
	 *  first, where <suffix> is this prefix with a leading "road_" stripped
	 *  (matching pipeline/export_road_mesh_bin_v2.py's AREA_NAME-based
	 *  naming) - e.g. "road_summit_county_test" -> "roadv3_summit_county_test".
	 *  ODR3 carries the worn-asphalt/lane-marking material's per-vertex
	 *  half-width and per-chunk road_class_id/lane_count data; when that
	 *  file exists it's preferred, otherwise this falls straight back to
	 *  the plain ODR2 file at the path above (see ParseRoadTileFileV2/V3) -
	 *  areas not yet re-baked through export_road_mesh_bin_v2.py just keep
	 *  working with plain asphalt/no markings, nothing else changes. */
	UPROPERTY(EditAnywhere, Category = "Road")
	FString RoadTilePrefix = TEXT("road_oahu_test");

	/** World-space grid size, cm. MUST match pipeline/test_areas.py's
	 *  ROAD_TILE_SIZE_CM - a mismatch doesn't crash, it just probes
	 *  filenames that don't exist and silently loads no road.
	 *  Shrunk from 200000 (2km) 2026-08-25 to fix the open Chaos/UE5.8
	 *  collision-registration bug (junction pads unreachable by a
	 *  ground-snap trace): Tick()'s ring-load math guarantees at least a
	 *  3x3-tile neighborhood resident around the player no matter how
	 *  small SpawnRadius is, and at the old tile size that guaranteed
	 *  minimum footprint already exceeded the bug's bisected resident-
	 *  chunk-count failure threshold at some real dense junction clusters.
	 *  A finer grid shrinks that guaranteed-minimum footprint instead -
	 *  see pipeline/test_areas.py's ROAD_TILE_SIZE_CM comment for the full
	 *  bisection numbers. */
	UPROPERTY(EditAnywhere, Category = "Road")
	float TileSizeCm = 50000.0f;

	/** Tiles fully populated inside this radius of the player (cm).
	 *  Shrunk from 400000 (4km) to 200000 (2km) as part of the Arc 7
	 *  dynamic-streaming rescope (2026-08-25, see
	 *  AODTerrainManager::VisualRadius/CollisionRadius for the terrain
	 *  side of the same change): roads don't need a coarse/full fidelity
	 *  split the way terrain does - they're already just chunk geometry
	 *  along the actual route, throttled to build a few chunks/tick via
	 *  ChunksPerFrameBudget - so the only remaining boot-burst contributor
	 *  here was simply loading too large a radius at once. 200000cm was
	 *  MaxChunkSpanCm (75000cm) plus a full tile of margin, comfortably
	 *  more than a player can cross before the next ring dispatches even
	 *  at unrealistic speeds.
	 *  Brought down to 50000cm alongside TileSizeCm's matching shrink
	 *  (2026-08-25, see that field's comment) to match the new tile size
	 *  1:1 - radius tuning alone couldn't clear the bug's bisected
	 *  threshold at some dense junctions (the ring-load minimum footprint
	 *  is independent of SpawnRadius, see TileSizeCm), but keeping it in
	 *  step with the smaller tile still avoids dispatching load requests
	 *  needlessly far ahead of the player. */
	UPROPERTY(EditAnywhere, Category = "Road")
	float SpawnRadius = 50000.0f;

	/** Fixed world-space margin (cm) beyond the load radius before a tile is
	 *  released - a world-space hysteresis band, not a bare tile-count
	 *  constant (which wouldn't scale correctly across different
	 *  TileSizeCm values). Brought down alongside SpawnRadius (2026-08-25)
	 *  to keep the despawn ring proportionally close, not 2.5x SpawnRadius.
	 *  Shrunk again in step with TileSizeCm/SpawnRadius's 2026-08-25 drop
	 *  to 50000cm, preserving the same ~1.25x ratio to SpawnRadius. */
	UPROPERTY(EditAnywhere, Category = "Road")
	float DespawnMarginCm = 62500.0f;

	/** Cap on new tile-load DISPATCHES per tick (not completions - the I/O
	 *  itself is async) so a big SpawnRadius or a fast ring-crossing can't
	 *  thundering-herd the thread pool with simultaneous file opens. */
	UPROPERTY(EditAnywhere, Category = "Road")
	int32 MaxNewLoadsPerTick = 2;

	/** Millisecond budget for chunk *components* (NewObject/RegisterComponent/
	 *  collision-enable) built per tick, across however many tiles are
	 *  mid-build at once (2026-08-25, Taylor: full-map junction-pad collision
	 *  investigation) - see ProcessPendingChunkBuilds's header comment on
	 *  OnTileParsed for why this exists: building every chunk in a big
	 *  same-frame burst (a full map's ~260 near-tile chunks) left some
	 *  components' physics state reporting itself valid yet never actually
	 *  queryable in the world's physics scene, while the identical geometry
	 *  built in a small burst (--solo, 5 chunks) worked immediately - a
	 *  Chaos/UE5.8 bulk-registration issue, not a bug in this class's own
	 *  bookkeeping. Spreading the same total work across more frames avoids
	 *  ever hitting that many same-frame registrations.
	 *
	 *  2026-08-27 (fast-drive hitch investigation): converted from a bare
	 *  chunk-COUNT budget (was ChunksPerFrameBudget=8) to a time budget, same
	 *  reasoning as AODTerrainManager::TileBuildMsBudget - chunk vertex counts
	 *  vary a lot (a merged host+junction-pad chunk vs. a short ordinary
	 *  ribbon segment), so a fixed count per tick doesn't actually bound frame
	 *  time. ProcessPendingChunkBuilds keeps building queued chunks while
	 *  accumulated stage time stays under this budget, always finishing at
	 *  least one chunk per tick. Collision cook cost is additionally moved off
	 *  this budget's critical path via bUseAsyncCooking (see
	 *  CreateChunkMeshComponent) without changing how many chunks actually
	 *  REGISTER per frame - see that function's comment for why that
	 *  distinction matters against the bulk-registration bug above. */
	UPROPERTY(EditAnywhere, Category = "Road")
	float ChunkBuildMsBudget = 3.0f;

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

	/** The area's Turnouts for the map (built once at BeginPlay from
	 *  menu_spots.json). Empty on levels without a menu spot. */
	const TArray<FTownNode>& GetTownNodes() const { return TownNodes; }

	/** Nearest road-centerline point to a world-XY query, plus the road
	 *  heading there (yaw, deg) - used to snap a map-spawn pick onto the road
	 *  and face the car along it. False if no centerline is loaded. */
	bool NearestRoadPoint(const FVector2D& Query, FVector2D& OutPoint,
		float& OutYawDeg) const;

	/** Loading-gate readiness check (2026-08-27, spawn-loading-screen task):
	 *  true once there are no chunk builds queued and no tile parses in
	 *  flight. Unlike AODTerrainManager's version this isn't keyed to a
	 *  position/radius - roads only ever load the ring around the player to
	 *  begin with (SpawnRadius), so "nothing pending or in-flight" already
	 *  means everything currently in reach is fully built. */
	bool IsInitialStreamingSettled() const { return PendingChunkBuilds.Num() == 0 && PendingTiles.Num() == 0; }

	/** Progress numbers for the loading screen's "roads N/M" line - N is
	 *  fully-resident tiles, M adds in-flight ones (parsing or queued for
	 *  their chunk build) so the count grows as the ring dispatches and
	 *  catches back up as each tile finishes. */
	void GetStreamingProgress(int32& OutBuilt, int32& OutTotal) const
	{
		OutBuilt = Tiles.Num();
		OutTotal = Tiles.Num() + PendingTiles.Num();
	}

	// ---- Arc 4.2 v3 navigation (next steps.md SS7.37) ----
	// Road graph = routegraph_<area>_test.bin (ODG1, pipeline/
	// export_road_centerline_bin.py's build_route_graph), path derived from
	// CenterlineFilePrefix (centerline_ -> routegraph_). A* over it from the
	// player's nearest graph node; pipeline/checks/verify_route_graph.py is the
	// Python mirror of FindRoutePath/BuildRouteTurns - keep in sync.

	bool HasRouteGraph() const { return RouteNodes.Num() > 0; }
	/** Nearest route-graph node to Query (the "snap waypoint to road" step). */
	bool SnapToRouteGraph(const FVector2D& Query, FVector2D& OutPoint) const;
	/** Routes from the player to Dest (snapped to the graph). False (and the
	 *  old route is kept) if there's no graph/player or Dest is unreachable. */
	bool SetRouteDestination(const FVector2D& Dest, const FString& Name);
	void ClearRoute();
	bool HasRoute() const { return RoutePts.Num() >= 2; }
	const TArray<FVector2D>& GetRoutePoints() const { return RoutePts; }
	const FString& GetRouteName() const { return RouteName; }
	float GetRouteRemainingCm() const { return RouteRemainingCm; }
	/** Next junction turn ahead: distance (cm) and signed angle (deg, + = right).
	 *  False if the rest of the route is straight-through. */
	bool GetNextTurn(float& OutDistCm, float& OutAngleDeg) const;
	/** World time of the last arrival (route auto-clears on arrival), <0 = never. */
	double GetArrivedTime() const { return ArrivedTime; }
	const FString& GetArrivedName() const { return ArrivedName; }
	/** Track editor (2026-10-13): the road route through Waypoints in order
	 *  (back to the first when bLoop) as XY + Z cm - pipeline/build_track.py's
	 *  build_route. Each snap faces the next waypoint; false with OutFailLeg
	 *  = the waypoint whose leg has no road, or INDEX_NONE (no graph / Z). */
	bool RouteThrough(const TArray<FVector2D>& Waypoints, bool bLoop,
		TArray<FVector2D>& OutXY, TArray<float>& OutZ, int32& OutFailLeg) const;
	/** "<area>" from CenterlineFilePrefix (centerline_<area>_test), as in tracks.json */
	FString GetAreaKey() const { return CenterlineFilePrefix.Mid(11).LeftChop(5); }

private:
	void LoadCenterline();
	void LoadMapCenterline();
	void LoadPlaceLabels();
	void BuildTownNodes();
	void UpdateCurrentLabels(const FVector& PlayerLoc);
	void LoadRouteGraph();
	/** Nearest route node; with a Heading, the nearest one with a legal arc
	 *  leaving along it (within StartSnapSlackCm of the nearest) - route start. */
	int32 NearestRouteNode(const FVector2D& Q, const FVector2D& Heading = FVector2D::ZeroVector) const;
	bool FindRoutePath(int32 From, int32 To, TArray<int32>& OutNodes) const;
	bool RecomputeRoute(const FVector2D& From);
	void UpdateRouteProgress(const FVector2D& PlayerXY);

	// CSR adjacency (both directions per undirected ODG1 edge)
	TArray<FVector2D> RouteNodes;
	TArray<float> RouteNodeZ;      // lane block node Z, cm; empty on older files
	TArray<int32> RouteAdjStart;   // RouteNodes.Num()+1
	TArray<int32> RouteAdjNode;
	TArray<float> RouteAdjLen;     // A* cost, cm (wrong-way arcs x WrongWayCostMult)
	TBitArray<> RouteAdjWrongWay;  // arc runs against a one-way edge

	TArray<FVector2D> RoutePts;
	TArray<float> RouteCum;        // arc length (cm) from RoutePts[0]
	struct FRouteTurn { float Cum; float AngleDeg; };
	TArray<FRouteTurn> RouteTurns;
	FVector2D RouteDest = FVector2D::ZeroVector;
	FString RouteName;
	int32 RouteSeg = 0;            // RoutePts segment the player is on
	float RouteProgressCm = 0.0f;
	float RouteRemainingCm = 0.0f;
	double LastRerouteTime = -100.0;
	double ArrivedTime = -1.0;
	FString ArrivedName;

	TArray<FTownNode> TownNodes;

	// mirrors pipeline/road_mesh_common.py's STATION_SPACING (5.0 m) *
	// CHUNK_STATIONS (150) - the worst-case along-route span a single
	// chunk can cover, used to pad the load radius so a chunk whose
	// centroid buckets into a farther tile can't have geometry the player
	// reaches before that tile is requested.
	static constexpr float StationSpacingCm = 500.0f;
	static constexpr int32 ChunkStations = 150;
	static constexpr float MaxChunkSpanCm = ChunkStations * StationSpacingCm;

	// ODR2 (Content/OpenDrive/Masks/) - original per-vertex position+UV0 layout,
	// no class/lane metadata. Still the only format most areas have baked.
	static bool ParseRoadTileFileV2(const FString& Path, FParsedRoadTile& Out);

	// ODR3 (Content/OpenDrive/MasksV2/roadv3_*) - adds the UV1 (half-width)
	// channel and per-chunk road_class_id/lane_count bytes consumed by the
	// worn-asphalt/lane-marking material (2026-09-03, Phase 2a road-visual
	// overhaul). See pipeline/export_road_mesh_bin_v2.py's docstring for the
	// wire format this mirrors byte-for-byte.
	static bool ParseRoadTileFileV3(const FString& Path, FParsedRoadTile& Out);

	void RequestTileLoad(const FIntPoint& Key);
	void OnTileParsed(const FIntPoint& Key, bool bOk, FParsedRoadTile&& Parsed);
	void ProcessPendingChunkBuilds();
	void BuildOneChunk(const FIntPoint& Key, FRoadTile& Tile, const FParsedChunk& Chunk, const FTransform& CompXf);
	UDynamicMeshComponent* CreateChunkMeshComponent(const FParsedChunk& Chunk, const FTransform& CompXf, bool bRecordSkirtSamples);
	void ReleaseTile(const FIntPoint& Key);
	void RunSeamCheck();

	TMap<FIntPoint, FRoadTile> Tiles;

	// parsed tiles awaiting their chunk components, a few per tick - see
	// ProcessPendingChunkBuilds
	TArray<FPendingTileBuild> PendingChunkBuilds;

	// dispatched (parse in flight, OR parsed and queued in PendingChunkBuilds
	// awaiting its incremental build) - stays populated until the tile's last
	// chunk actually finishes in ProcessPendingChunkBuilds
	TSet<FIntPoint> PendingTiles;
	// confirmed no file at this key (ENOENT or bad header) - don't retry
	TSet<FIntPoint> MissingTiles;

	// 2026-08-26: RecreatePhysicsState() alone (tried first) did NOT fix the
	// bug - retested with live per-second tracking (DiagTargetComp below)
	// and the affected component stayed registered=1/visible=1/collision
	// valid/bodyValid=1/renderStateCreated=1 for 30+ seconds straight, never
	// destroyed or replaced, while an independent trace fired from this
	// actor (not just the vehicle's own trace) still missed it every time.
	// That rules out both a stale/never-refreshed state AND a timing/tile-
	// churn explanation - the component's own bookkeeping is correct but
	// something in the engine's internal registration of it isn't. The
	// stronger mitigation: don't just refresh the existing component's
	// physics state, fully DESTROY it and construct a brand-new
	// UDynamicMeshComponent with identical geometry one tick later, so
	// whatever internal registration table entry is actually broken gets
	// replaced by a fresh one rather than patched in place.
	struct FPendingPhysicsRebuild
	{
		FIntPoint TileKey = FIntPoint::ZeroValue;
		TWeakObjectPtr<UDynamicMeshComponent> OldComp;
		FParsedChunk Chunk;   // copy - needed to rebuild the mesh from scratch
		FTransform CompXf;
	};
	TArray<FPendingPhysicsRebuild> PendingPhysicsRefresh;
	void ProcessPendingPhysicsRefresh();

	// 2026-08-26 diagnostic: RecreatePhysicsState() (above) did NOT fix the
	// test junction - onRoad=0 still, identical to before. BuildOneChunk's
	// per-chunk log only proves the component's state was valid AT BUILD
	// TIME; the ground-snap failure happens 2-20s later. Track the specific
	// small pad chunk found near the test point across time to see whether
	// it's actually torn down/rebuilt in between (tile despawn churn, a
	// duplicate-key collision) rather than staying registered-but-ignored
	// the whole time.
	TWeakObjectPtr<UDynamicMeshComponent> DiagTargetComp;
	bool bDiagTargetEverSet = false;
	double LastDiagTargetLogTime = 0.0;

	// generation-id cross-tile check (2026-08-21, AUDIT-RELIABILITY.md
	// SS3.1/SS5.1): the id of the first tile this manager successfully
	// parsed for RoadTilePrefix - every later tile is compared against it,
	// and a mismatch (tiles from two different pipeline runs streaming
	// side by side) is logged once, not blocked.
	FString FirstGenerationId;
	bool bHasFirstGenerationId = false;
	bool bGenerationMismatchWarned = false;

	UPROPERTY()
	TObjectPtr<UMaterialInterface> RoadMat;

	// one-way carriageway variant (no double-yellow centerline) - see
	// FParsedChunk::bOneway.
	UPROPERTY()
	TObjectPtr<UMaterialInterface> RoadMatOneWay;

	// unpaved roads (FParsedChunk::SurfaceClass 1/2) - unreal/apply_unpaved_road_materials.py
	UPROPERTY()
	TObjectPtr<UMaterialInterface> RoadMatGravel;

	UPROPERTY()
	TObjectPtr<UMaterialInterface> RoadMatDirt;

	// Realistic-only wear decals (PLAN-VISUALS Track C), null in Stylized
	UPROPERTY()
	TObjectPtr<UODRoadDecals> Decals;

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

	// -odseam=N QA hook state (2026-08-22: moved off function-local `static`
	// into instance members - a `static` inside Tick() only gets initialized
	// once per PROCESS, not once per PIE session, so triggering -odseam,
	// stopping PIE, then starting a new PIE run in the same editor process
	// left bSeamDone still true from the previous run and the check never
	// fired again. An instance member is naturally fresh every PIE session
	// because PIE spawns a brand-new AODRoadManager each time - same fix
	// already used for AODMainMenu's -odpick/-odgo, see ODMainMenu.h).
	float SeamAt = -1.0f;
	bool bSeamParsed = false;
	bool bSeamDone = false;

	TArray<FPlaceLabel> PlaceLabels;
	double LastLabelUpdateTime = 0.0;

	/** Snow accumulation (2026-10-06): ~2 Hz, each chunk's CPD depth +=
	 *  AODWeatherController's road/roadside rate (reset to ambient when QA
	 *  forces a depth). Per chunk so a future plow can clear one. */
	void TickSnow();
	TWeakObjectPtr<class AODWeatherController> SnowWeather;
	double LastSnowTime = 0.0;
	int32 SnowSerialSeen = 0;
};
