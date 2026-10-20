#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "UObject/WeakObjectPtrTemplates.h"
#include "ODBuildingManager.generated.h"

class UDynamicMeshComponent;
class UMaterialInterface;

/** One parsed building - plain data, safe to build off the game thread.
 *  Mirrors the ODU4 sidecar's per-building payload layout
 *  (pipeline/build_building_mesh.py / export_building_mesh_bin.py). */
struct FParsedBuilding
{
	TArray<float> Verts;    // flat x,y,z (cm, UE world frame)
	TArray<float> Uvs;      // flat u,v
	TArray<uint32> Tris;    // flat i0,i1,i2
	uint8 WallSet = 0;      // index into apply_building_material*.py WALL_LAYERS
	uint8 FlatRoof = 0;     // 1 = flat roof cap, 0 = pitched (baked by the pipeline)
	uint8 RoofSet = 3;      // apply_building_material_real.py ROOF_LAYERS: tile, asphalt, metal, flat
	uint8 RoofRgb[3] = {87, 87, 85};   // roof albedo, linear 0..255
	uint8 Facade = 0;       // apply_building_material_real.py FACADE_FLOORS archetype (realism B2)
	// pitched roofs (building_roofs.py): vertices from here on are the gable
	// end walls, which get vertex alpha 0 (no windows)
	int32 NumSolidVerts = MAX_int32;
	// landmarks (pipeline/build_landmarks.py): the material seed byte (vertex
	// G, picks the paint) chosen by the pipeline; -1 = hashed from the centroid
	int16 SeedByte = -1;
};

/** One parsed tile file's worth of buildings - the off-thread output of
 *  AODBuildingManager::ParseBuildingTileFile, consumed on the game thread
 *  by OnTileParsed to build real components. */
struct FParsedBuildingTile
{
	TArray<FParsedBuilding> Buildings;

	// pipeline/generation_id.py's per-run id, read from the ODU4 header
	// (2026-08-21) - see AODBuildingManager::OnTileParsed for the
	// cross-tile mismatch check this feeds.
	FString GenerationId;
};

/** Live building geometry for one loaded tile - TWO merged components, not
 *  one per building (2026-09-06, AUDIT.md rec. 2 "merge per tile": a dense
 *  downtown tile used to spawn THOUSANDS of one-building components, the
 *  audit's measured ~25,000 draw calls; all buildings share
 *  M_BuildingFacade and per-building variation already travels in vertex
 *  color, so merging costs nothing visually). Split near/far by distance
 *  from the road corridor so collision cooking only pays for buildings the
 *  car can physically reach (see AODBuildingManager::CollisionBandCm). */
struct FBuildingTile
{
	TObjectPtr<UDynamicMeshComponent> NearMesh;   // within CollisionBandCm of a road - full collision
	TObjectPtr<UDynamicMeshComponent> FarMesh;    // out of driving reach - visual only, no collision
	int32 NumBuildings = 0;
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
 * system here already relies on. 2026-09-06: buildings within
 * CollisionBandCm of the road centerline get full collision; the rest are
 * visual-only (the car can't reach them - the out-of-bounds reset turns it
 * around long before), mirroring terrain's own near/far collision split.
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

	/** 2026-10-16: "Wetness" / "SnowCover" on the shared facade + roof MIDs
	 *  (AODSeasonController pushes them with the ground's). */
	void SetWeather(float Wetness, float SnowCover);

	/** Loading-gate readiness (2026-10-14, AODMainMenu's loading screen): every
	 *  tile within SpawnRadius of AroundPos is built or known-missing and no
	 *  building is still waiting on ground (those give up after
	 *  PendingGiveUpAfterSec, so this can't hold forever). */
	bool IsInitialStreamingSettled(const FVector& AroundPos) const
	{
		if (PendingBuildings.Num() > 0)
		{
			return false;
		}
		const FIntPoint Center(FMath::FloorToInt(AroundPos.X / TileSizeCm), FMath::FloorToInt(AroundPos.Y / TileSizeCm));
		const int32 Reach = FMath::CeilToInt(SpawnRadius / TileSizeCm);
		for (int32 dy = -Reach; dy <= Reach; ++dy)
		{
			for (int32 dx = -Reach; dx <= Reach; ++dx)
			{
				const FIntPoint Key = Center + FIntPoint(dx, dy);
				if (!Tiles.Contains(Key) && !MissingTiles.Contains(Key))
				{
					return false;
				}
			}
		}
		return true;
	}

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
	 *  components in one downtown tile) and only matter close up.
	 *
	 *  Brought down from 250000 (2026-08-27, live-drive placement-bug fix):
	 *  a building beyond AODTerrainManager::CollisionRadius (200000 by that
	 *  manager's own default) has no terrain collision to trace against AT
	 *  ALL - not "not yet streamed in", but structurally never, since
	 *  terrain only keeps near-tier collision within CollisionRadius of the
	 *  player no matter how long placement waits (the same
	 *  no-collision-outside-this-ring behavior AODForestManager's own
	 *  SpawnRadius comment already documents working around). Left at
	 *  250000 this cost every distant tile's buildings a full
	 *  PendingGiveUpAfterSec (10 s) round trip through the retry queue for
	 *  nothing. BeginPlay() also clamps this at runtime against whatever
	 *  AODTerrainManager::CollisionRadius the level actually has configured
	 *  (see ClampSpawnRadiusToTerrainCollision()), so a level-placed
	 *  instance's own serialized override gets the same protection this
	 *  default does. */
	UPROPERTY(EditAnywhere, Category = "Buildings")
	float SpawnRadius = 180000.0f;

	UPROPERTY(EditAnywhere, Category = "Buildings")
	float DespawnMarginCm = 300000.0f;

	UPROPERTY(EditAnywhere, Category = "Buildings")
	int32 MaxNewLoadsPerTick = 1;

	/** Buildings whose footprint centroid is within this distance (cm) of
	 *  the road centerline go into the tile's NearMesh (full complex
	 *  collision); everything further goes into FarMesh (visual only).
	 *  2026-09-06, AUDIT.md rec. 2: collision cooking is the expensive half
	 *  of a merged tile mesh, and the car can only reach buildings near the
	 *  corridor - AODVehiclePawn's out-of-bounds reset turns it around at
	 *  OobDistCm (60 m), so 120 m is a 2x safety margin over the furthest
	 *  legally reachable point. If no AODRoadManager/centerline exists the
	 *  split fails OPEN (everything collides - old behavior). */
	UPROPERTY(EditAnywhere, Category = "Buildings")
	float CollisionBandCm = 12000.0f;

private:
	/** Clamps SpawnRadius down to (AODTerrainManager::CollisionRadius - a
	 *  small safety margin) if it's currently set higher - see SpawnRadius's
	 *  own comment for why: a building tile requested beyond the terrain's
	 *  own near-tier collision ring has no ground to trace against, full
	 *  stop, regardless of how long ProcessPendingBuildings() retries it.
	 *  Called once from BeginPlay(); no-ops if no AODTerrainManager exists in
	 *  the level (matches every other Load*()-style optional-dependency here
	 *  - see e.g. AODForestManager::LoadMask()'s identical
	 *  "missing dependency is not fatal" convention). Reads
	 *  AODTerrainManager's public CollisionRadius only - never modifies that
	 *  actor or its file. */
	void ClampSpawnRadiusToTerrainCollision();

	static bool ParseBuildingTileFile(const FString& Path, FParsedBuildingTile& Out);

	void RequestTileLoad(const FIntPoint& Key);
	void OnTileParsed(const FIntPoint& Key, bool bOk, FParsedBuildingTile&& Parsed);
	void FinalizeTile(const FIntPoint& Key, FParsedBuildingTile&& Parsed);
	void ReleaseTile(const FIntPoint& Key);

	/** Ground-height trace at (WorldX, WorldY), relative to PlayerZHint rather
	 *  than a hardcoded absolute Z band - same fix AODForestManager applied
	 *  (see its PlayerZHint comment) for the identical "trust the baked
	 *  height" bug class: terrain is runtime-carved (AODTerrainManager::
	 *  ApplyRoadCarve) and streamed asynchronously, so a baked building Z can
	 *  disagree with the real runtime ground by an arbitrary amount, and a
	 *  fixed absolute trace band can miss entirely (or hit an unrelated
	 *  phantom surface at old-origin height) on tall terrain. Rejects hits
	 *  further than GroundSanityMaxDeltaCm from PlayerZHint as suspected
	 *  phantom geometry rather than real ground. Returns false (no log spam
	 *  here - callers aggregate into the throttled summary counters) if the
	 *  trace misses or is rejected. */
	bool GroundAt(double WorldX, double WorldY, const AActor* Ignore, float& OutZ) const;

	/** Computes the single Z offset to apply to one building's whole mesh (a
	 *  pure vertical translation - never a rotation, so the building never
	 *  tilts off the terrain normal) so it sits upright on real ground: traces
	 *  every distinct footprint-base corner of B via GroundAt(), takes the
	 *  LOWEST successful hit (so a footprint spanning a slope has its base at
	 *  the low corner, minus EmbedMarginCm, rather than floating any corner),
	 *  and returns (LowestGroundZ - EmbedMarginCm) - BakedBaseZ. Returns false
	 *  if every footprint-corner trace failed/was rejected (building is
	 *  skipped for this tile's lifetime, same fallback AODForestManager's
	 *  GroundAt() candidates use on trace failure - the tile isn't revisited,
	 *  so this only bites if terrain genuinely isn't streamed in at finalize
	 *  time, which the ground-snap gate in Tick() is meant to prevent). */
	bool ComputeBuildingZOffset(const FParsedBuilding& B, const AActor* Ignore, float& OutOffsetCm) const;

	/** Appends a batch of already-ground-resolved buildings into ONE of the
	 *  tile's two merged mesh components (2026-09-06, AUDIT.md rec. 2 -
	 *  replaces the old one-component-per-building CreateBuildingComponent).
	 *  Creates the component lazily on first use (bNear picks NearMesh with
	 *  full collision vs FarMesh visual-only); the per-building Z offset is
	 *  baked into the vertices since a merged mesh can't carry a
	 *  per-building component translation. One EditMesh per batch, so
	 *  FinalizeTile pays a single mesh update/collision cook per tile, not
	 *  one per building - retried pendings arrive as single-entry batches. */
	void AppendBuildingsToTileMesh(FBuildingTile& Tile, bool bNear,
		const TArray<TPair<const FParsedBuilding*, float>>& Batch);

	/** True if (WorldX, WorldY) lies within CollisionBandCm of the road
	 *  centerline. Lazily copies AODRoadManager's already-loaded
	 *  RoadCenterline into a bucketed spatial hash on first call (same
	 *  pattern as AODForestManager's CenterlineBuckets). Fails OPEN (true)
	 *  when no centerline is available. */
	bool IsNearRoadCorridor(double WorldX, double WorldY);

	/** One building whose ground trace failed at FinalizeTile time and is
	 *  waiting to be retried - see ProcessPendingBuildings()'s comment for why
	 *  this exists instead of permanently dropping it like AODForestManager's
	 *  per-candidate trace failures do: a tree/bush candidate that fails is
	 *  just one of thousands of interchangeable scatter points, but every
	 *  building is a specific, visible OSM footprint, so silently losing one
	 *  because its tile happened to finalize a frame before nearby terrain
	 *  streamed in is a much more noticeable regression. Holds a full copy of
	 *  the parsed geometry (small relative to the mesh build itself) since the
	 *  original FParsedBuildingTile is moved-from and destroyed once
	 *  FinalizeTile returns. */
	struct FPendingBuilding
	{
		FIntPoint TileKey;
		FParsedBuilding Building;
		double FirstAttemptTime = 0.0;
		double NextRetryTime = 0.0;
	};

	/** Retries every FPendingBuilding whose NextRetryTime has elapsed - called
	 *  once per Tick() (2026-08-27). Found necessary alongside the ground-snap
	 *  gate: that gate only proves the PLAYER'S own local ground is real: it
	 *  says nothing about whether AODTerrainManager has finished streaming
	 *  collision under every OTHER building tile inside SpawnRadius, and in a
	 *  headless/high-framerate run many building tiles can dispatch and finish
	 *  parsing within the same fraction of a second the gate first opens -
	 *  well before terrain has caught up across the whole disc. Retrying
	 *  (instead of FinalizeTile's original "skip forever, tile never
	 *  revisited" behavior) gives that terrain time to arrive. A pending
	 *  building whose owning tile was released (player drove away) before it
	 *  ever resolved is dropped silently - it's about to be out of range
	 *  anyway. One that still hasn't resolved after
	 *  PendingGiveUpAfterSec is dropped with a one-time warning per building
	 *  (genuinely missing/broken terrain data, not just a slow stream). */
	void ProcessPendingBuildings();

	TArray<FPendingBuilding> PendingBuildings;
	// Wall-clock-independent (GetWorld()->GetTimeSeconds()) retry cadence -
	// short enough that a building placed shortly after nearby terrain
	// streams in doesn't read as a late "pop-in", long enough not to spam
	// retraces every single tick while genuinely waiting.
	static constexpr double PendingRetryIntervalSec = 0.5;
	// Generous relative to AODTerrainManager's own near-tier tile build
	// budget - by 10 s after a building tile itself finalized, any terrain
	// tile inside the same SpawnRadius disc should long since have streamed
	// in; a building still failing past this is treated as a genuine data
	// gap, not a streaming race.
	static constexpr double PendingGiveUpAfterSec = 10.0;
	mutable int32 GivenUpCount = 0;

	TMap<FIntPoint, FBuildingTile> Tiles;
	TSet<FIntPoint> PendingTiles;
	TSet<FIntPoint> MissingTiles;

	// Corridor-distance spatial hash for IsNearRoadCorridor() (2026-09-06) -
	// a copy of AODRoadManager::RoadCenterline bucketed once on first query.
	TArray<FVector2D> CorridorCenterline;
	TMap<FIntPoint, TArray<int32>> CorridorBuckets;
	bool bCorridorSearched = false;
	static constexpr float CorridorCellCm = 12000.0f;   // = default CollisionBandCm

	// Player's last-known settled world Z (2026-08-27, mirrors
	// AODForestManager::PlayerZHint - see that header's comment for the
	// live-drive bug class this fixes). Set once per Tick() from the pawn's
	// own location, AFTER confirming AODVehiclePawn::IsGroundSnapped() -
	// GroundAt()'s trace is relative to this instead of a hardcoded absolute
	// Z band.
	float PlayerZHint = 0.0f;

	// Same sanity-check/trace-range constants as AODForestManager's (see
	// there for the derivation) - kept as independent constants rather than a
	// shared header symbol since nothing else needs them, matching this
	// project's "only lift to ODBinSidecarUtil.h what's actually shared"
	// convention (see that header's own comment).
	static constexpr float GroundSanityMaxDeltaCm = 50000.0f;   // 500 m
	static constexpr float GroundTraceMarginCm = 300000.0f;     // 3000 m
	// Small sink below the traced lowest footprint corner so no corner ever
	// floats due to trace/collision-surface quantization - buildings always
	// collide (see this class's own top comment), so a few cm of embed is
	// invisible but a few cm of float over a rounding error is not.
	static constexpr float EmbedMarginCm = 5.0f;
	// ComputeBuildingZOffset ignores corner hits this far below the highest one.
	static constexpr float CornerPitMaxDropCm = 5000.0f;   // 50 m

	// Throttled visibility into the ground-snap gate (Tick()) and the
	// per-building placement outcome - temporary QA instrumentation for the
	// "grey slabs misplaced vertically" live-drive bug this fix targets, same
	// 5 s cadence convention as the existing tile diag log below.
	bool bLoggedGroundSnapWait = false;
	bool bLoggedGroundSnapConfirmed = false;
	mutable int32 PlacedCount = 0;
	mutable int32 PhantomRejectCount = 0;
	mutable int32 NoHitRejectCount = 0;
	mutable float MinPlacementDeltaCm = TNumericLimits<float>::Max();
	mutable float MaxPlacementDeltaCm = TNumericLimits<float>::Lowest();

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

	/** Realistic only (PLAN-VISUALS Track G): M_BuildingRoof_real. When set,
	 *  roof triangles go in mesh section 1 with this material and walls keep
	 *  section 0, so neither material samples the other's textures. Null in
	 *  Stylized (or if the asset is missing): one section, FacadeMat, as before. */
	UPROPERTY()
	TObjectPtr<UMaterialInterface> RoofMat;

	double LastDiagTime = 0.0;
};
