#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODAmbientSoundManager.generated.h"

class UAudioComponent;
class USoundBase;

/** Ambient zone class a grid cell can be baked as - see pipeline/
 *  build_ambient_zones.py's ODA1 format doc. Mirrors that script's
 *  ZONE_OPEN/ZONE_FOREST/ZONE_ALPINE/ZONE_SETTLEMENT constants exactly -
 *  values must stay in sync with the pipeline side, there is no shared
 *  enum between Python and C++ here (same convention every other binary
 *  sidecar in this project uses - the byte VALUE is the contract, not a
 *  named type). */
enum class EODAmbientZone : uint8
{
	Open = 0,
	Forest = 1,
	Alpine = 2,
	Settlement = 3,
};

/** One parsed ODA1 ambient-zone grid - see pipeline/build_ambient_zones.py's
 *  module docstring for the full format and design reasoning (why this is a
 *  single flat whole-map grid, not a streamed per-2km-tile format like the
 *  guardrail/embankment/bridge managers: the payload is small enough - a few
 *  hundred KB at 100 m cells - to just load once and index with arithmetic,
 *  no async load/unload plumbing earns its keep here). */
struct FParsedAmbientZoneGrid
{
	int32 OriginXCm = 0, OriginYCm = 0;
	int32 Cols = 0, Rows = 0;
	float CellSizeCm = 10000.0f;

	// Row-major, Cols*Rows entries each.
	TArray<uint8> Zone;
	TArray<uint8> Density255;
	TArray<uint16> WaterDistM;

	FString GenerationId;
	bool bValid = false;

	/** Nearest-cell lookup (no interpolation) - used for the zone-class /
	 *  settlement-density crossfade target, which only needs "which zone am
	 *  I in right now", not a smooth blend. */
	bool SampleNearest(float WorldXCm, float WorldYCm, EODAmbientZone& OutZone, uint8& OutDensity) const;

	/** Bilinearly-interpolated water distance (meters) - unlike SampleNearest
	 *  above, the water layer's fade needs a SMOOTH value across the ~60 m
	 *  fade window, not a cell-quantized step, so this interpolates across
	 *  the four surrounding cells instead of snapping to the nearest one. */
	float SampleWaterDistM(float WorldXCm, float WorldYCm) const;
};

/** One ambient loop layer the manager crossfades between zones - see
 *  AODAmbientSoundManager class comment for the overall design. */
USTRUCT()
struct FODAmbientLayer
{
	GENERATED_BODY()

	UPROPERTY()
	TObjectPtr<UAudioComponent> Comp = nullptr;

	UPROPERTY()
	TObjectPtr<USoundBase> Sound = nullptr;

	float TargetVolume = 0.0f;
	float CurrentVolume = 0.0f;
};

/**
 * Tile-keyed ambient sound (Arc 5.4 / next steps.md SS7.28, backlog item
 * asked 2026-07-27, built 2026-09-07): as the player drives, crossfaded
 * looping ambience follows the pipeline-baked ambient-zone grid (pipeline/
 * build_ambient_zones.py's ODA1 sidecar - see that module for the bake
 * itself, which reuses classify_biomes.py/classify_settlements.py's tile
 * data plus a water-proximity raster rather than inventing a new spatial
 * system, per the user brief).
 *
 * Same "find-or-spawn singleton" shape as AODSeasonController/
 * AODAtmosphereController (ODGameMode::EnsureAmbientSoundManager) - one
 * instance per level, no per-2km-tile actor/streaming shape at all: the
 * whole grid is loaded once in BeginPlay (see FParsedAmbientZoneGrid) and
 * sampled against the player's position at a low tick rate (SampleHz), not
 * every frame - ambient zone boundaries move at driving speed, not audio
 * rates, so there's no benefit to checking every tick.
 *
 * Four looping AudioComponents are held permanently (one per ambient
 * layer - Forest, Alpine/wind-bed, Settlement/town-hum, Water) and NEVER
 * destroyed/recreated on a zone change; only their volumes are crossfaded
 * (FadeIn/FadeOut over CrossfadeSeconds) between whichever zone the player
 * is currently in and whichever they were in a moment ago. This is
 * deliberately simpler than a "swap the active SoundBase" approach: with a
 * fixed 1:1 layer-to-purpose mapping (LayerForest/LayerAlpine/
 * LayerSettlement/LayerWater), a zone change is just "which layer(s) fade
 * up, which fade down" - Open zones fade all the way down to the Alpine
 * layer alone (a plain wind bed is the correct ambience for open/rocky/dry
 * terrain too, see pipeline/build_ambient_zones.py's ZONE_OPEN comment),
 * Forest/Settlement zones bring in their own layer ON TOP of that wind bed
 * rather than replacing it (a settlement or forest patch still has wind),
 * and the Water layer is an entirely independent additive layer driven by
 * SampleWaterDistM() rather than the zone class at all - it fades in
 * within WaterFadeInStartM of a creek/lake edge regardless of which zone
 * class that edge sits inside.
 *
 * Day/night or season coupling is explicitly NOT wired (per the brief,
 * "keep hooks simple for later") - SetTimeOfDayHint()/SetSeasonHint() below
 * are unused no-op seams for that later work, not a real implementation.
 */
UCLASS()
class OPENDRIVEUE_API AODAmbientSoundManager : public AActor
{
	GENERATED_BODY()

public:
	AODAmbientSoundManager();
	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

	/** Sidecar filename; single whole-map file under Content/OpenDrive/Masks/,
	 *  "ambient_zones_<AreaTilePrefix>.bin" - same AODRoadManager-derived
	 *  area-name convention every other Ensure*Manager in ODGameMode.cpp
	 *  uses, just without the per-tile "_tile_<tx>_<ty>" suffix since this
	 *  format isn't tiled (see class comment). */
	UPROPERTY(EditAnywhere, Category = "Ambient")
	FString AmbientZoneFile = TEXT("ambient_zones_summit_county_test");

	/** How often (Hz) the player position is re-sampled against the zone
	 *  grid - ambient zone boundaries move at driving speed, not audio
	 *  rates, so this is deliberately low. */
	UPROPERTY(EditAnywhere, Category = "Ambient")
	float SampleHz = 2.0f;

	UPROPERTY(EditAnywhere, Category = "Ambient")
	float CrossfadeSeconds = 4.0f;

	/** Water layer fades linearly from 0 (at/beyond this distance) to full
	 *  volume at 0 m (the ribbon/lake edge itself) - see pipeline/
	 *  build_ambient_zones.py's WATER_DIST_CAP_M for the grid's own storage
	 *  cap (500 m, comfortably above this). */
	UPROPERTY(EditAnywhere, Category = "Ambient")
	float WaterFadeInStartM = 60.0f;

	/** Sound assets - defaulted in BeginPlay() via LoadObject() against the
	 *  fixed /Game/OpenDrive/Audio/Ambient/SW_*.SW_* paths that
	 *  unreal/import_ambient_audio.py imports to (same LoadObject-in-
	 *  BeginPlay convention as AODEmbankmentManager::EmbankMat), but still
	 *  EditAnywhere so a hand-placed level instance can override any of
	 *  them. Left as plain USoundBase (not USoundCue) so either a bare
	 *  imported SoundWave (loop-flagged on import) or a SoundCue wrapping
	 *  one works without a code change - every other engine-audio asset
	 *  reference in this project (none exist yet; ODVehicleAudio's engine/
	 *  road noise are procedural synths, not sample playback) would follow
	 *  the same convention if one existed. */
	UPROPERTY(EditAnywhere, Category = "Ambient|Sounds")
	TObjectPtr<USoundBase> ForestSound;

	UPROPERTY(EditAnywhere, Category = "Ambient|Sounds")
	TObjectPtr<USoundBase> AlpineWindSound;

	UPROPERTY(EditAnywhere, Category = "Ambient|Sounds")
	TObjectPtr<USoundBase> SettlementHumSound;

	UPROPERTY(EditAnywhere, Category = "Ambient|Sounds")
	TObjectPtr<USoundBase> WaterSound;

	/** Seams for later coupling (season/day-night) - deliberately unused
	 *  no-ops for now, see class comment. */
	void SetTimeOfDayHint(float Hour01) { /* not wired yet */ }
	void SetSeasonHint(int32 SeasonIndex) { /* not wired yet */ }

private:
	bool LoadZoneGrid();
	void EnsureLayers();
	void SampleAndRetarget();

	FParsedAmbientZoneGrid Grid;
	bool bGridLoaded = false;

	UPROPERTY()
	FODAmbientLayer LayerForest;
	UPROPERTY()
	FODAmbientLayer LayerAlpine;
	UPROPERTY()
	FODAmbientLayer LayerSettlement;
	UPROPERTY()
	FODAmbientLayer LayerWater;

	float TimeSinceSample = 0.0f;
	double LastDiagTime = 0.0;
};
