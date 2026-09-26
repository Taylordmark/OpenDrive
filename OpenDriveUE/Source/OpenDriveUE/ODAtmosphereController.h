#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODAtmosphereController.generated.h"

class ASkyAtmosphere;
class AExponentialHeightFog;
class AVolumetricCloud;
class APostProcessVolume;
class AODWeatherController;
class AODAmbientSoundManager;
class UDirectionalLightComponent;
class UExponentialHeightFogComponent;
class UMaterialInstanceDynamic;
class USkyAtmosphereComponent;
class USkyLightComponent;

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
 *
 * Weather W2 (2026-09-25, next steps.md sec 7.40): Tick reads
 * AODWeatherController's VisibilityM/CloudCover/DustAmount and drives fog
 * density/opacity (Koschmieder: density = 39.12 / visibility m), a tan dust
 * inscatter/albedo, VolumetricCloud coverage (a MID of the engine cloud
 * material), and a darker sky + sun/sky light under overcast. Each input
 * becomes a 0..1 weight snapped to 0 below 1e-3, and every value is
 * Lerp(baseline captured at BeginPlay, weather target, weight) - so Clear
 * writes back the exact pre-weather values. Thunderstorm adds lightning: a
 * short sun/sky light flash, thunder queued on AODAmbientSoundManager
 * delayed by distance / 343 m/s. W4 (sec 7.42): StormTint greens the sky
 * luminance + fog inscatter during a tornado.
 */
UCLASS()
class OPENDRIVEUE_API AODAtmosphereController : public AActor
{
	GENERATED_BODY()

public:
	AODAtmosphereController();
	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

private:
	void EnsureSkyAtmosphere();
	void EnsureHeightFog();
	void EnsureVolumetricCloud();
	void SpawnGradeVolume();

	TWeakObjectPtr<ASkyAtmosphere> Atmosphere;
	TWeakObjectPtr<AExponentialHeightFog> Fog;
	TWeakObjectPtr<AVolumetricCloud> Cloud;
	TWeakObjectPtr<APostProcessVolume> GradeVolume;

	// ---- Weather W2 ----
	float UpdateLightning(float DeltaSeconds, const AODWeatherController& Weather);
	void FindLights();

	TWeakObjectPtr<AODWeatherController> WeatherCtl;
	TWeakObjectPtr<AODAmbientSoundManager> AmbientMgr;
	TWeakObjectPtr<USkyAtmosphereComponent> AtmoComp;
	TWeakObjectPtr<UExponentialHeightFogComponent> FogComp;
	TWeakObjectPtr<UDirectionalLightComponent> SunComp;
	TWeakObjectPtr<USkyLightComponent> SkyLightComp;
	UPROPERTY()
	TObjectPtr<UMaterialInstanceDynamic> CloudMID;

	// Clear-weather baselines (what BeginPlay's tuning / the level left set).
	float BaseFogDensity = 0.0f, BaseFogFalloff = 0.0f, BaseFogOpacity = 0.0f, BaseFogStart = 0.0f;
	FLinearColor BaseFogAmbientScale = FLinearColor::White;
	FColor BaseFogAlbedo = FColor::White;
	FLinearColor BaseSkyLuminance = FLinearColor::White;
	float BaseCloudCoverage = 0.0f, BaseStormClouds = 0.0f;
	float BaseSunIntensity = 0.0f, BaseSkyLightIntensity = 0.0f;

	float LastFogW = -1.0f, LastCloudW = -1.0f, LastDustW = -1.0f, LastTintW = -1.0f, LastFlash = -1.0f;
	float StrikeCountdown = 5.0f;
	float FlashAge = -1.0f;   // < 0 = no flash running
	float FlashPeak = 0.0f;
};
