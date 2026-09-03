#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODAtmosphereController.generated.h"

class ASkyAtmosphere;
class AExponentialHeightFog;
class AVolumetricCloud;
class APostProcessVolume;

/**
 * Art-direction Phase 1 (2026-09-01, "look toward The Crew 2" pass): finds or
 * spawns the SkyAtmosphere/ExponentialHeightFog/VolumetricCloud/
 * PostProcessVolume actors this project needs for a grounded, hazy
 * high-altitude mountain look (Summit County, CO - valley floor ~2700 m),
 * and tunes them from C++ rather than depending on whatever a given level's
 * editor-authored bake happened to carry (some open-world test levels, e.g.
 * OpenWorldTest via unreal/build_openworld_test_level.py, spawn a bare
 * SkyAtmosphere/SkyLight with no fog or clouds at all, and no level has a
 * volumetric cloud layer yet).
 *
 * "Find or spawn" (not "always spawn") mirrors AODGameMode::EnsureForestManager/
 * EnsureWaterManager's existing convention: a hand-tuned, editor-placed actor
 * of a given class wins over a fresh runtime one, but nothing needs to exist
 * ahead of time for this to work.
 *
 * The PostProcessVolume is the one exception - this always spawns its OWN
 * volume rather than reusing "Grade" (build_openworld_test_level.py's
 * existing whitewash/exposure fix), because both need to stay authoritative
 * for different things: Grade's exposure-bias fix stays as the metering
 * floor, and this actor's higher-Priority unbound volume overrides only the
 * specific grading/exposure/bloom/vignette fields this pass cares about, so
 * per-property blending (higher Priority wins per overridden field, see
 * FPostProcessSettings blending) lets both coexist instead of one replacing
 * the other outright.
 */
UCLASS()
class OPENDRIVEUE_API AODAtmosphereController : public AActor
{
	GENERATED_BODY()

public:
	AODAtmosphereController();
	virtual void BeginPlay() override;

private:
	void EnsureSkyAtmosphere();
	void EnsureHeightFog();
	void EnsureVolumetricCloud();
	void SpawnGradeVolume();

	TWeakObjectPtr<ASkyAtmosphere> Atmosphere;
	TWeakObjectPtr<AExponentialHeightFog> Fog;
	TWeakObjectPtr<AVolumetricCloud> Cloud;
	TWeakObjectPtr<APostProcessVolume> GradeVolume;
};
