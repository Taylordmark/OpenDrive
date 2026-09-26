#include "ODAtmosphereController.h"

#include "Components/DirectionalLightComponent.h"
#include "Components/ExponentialHeightFogComponent.h"
#include "Components/SkyAtmosphereComponent.h"
#include "Components/SkyLightComponent.h"
#include "Components/VolumetricCloudComponent.h"
#include "Engine/DirectionalLight.h"
#include "Engine/ExponentialHeightFog.h"
#include "Engine/PostProcessVolume.h"
#include "Engine/SkyLight.h"
#include "EngineUtils.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/MaterialInterface.h"
#include "ODAmbientSoundManager.h"
#include "ODWeatherController.h"
#include "UObject/ConstructorHelpers.h"

namespace
{
// Engine content shipped for exactly this purpose (UE_5.8\Engine\Content\
// EngineSky\VolumetricClouds\m_SimpleVolumetricCloud_Inst.uasset) - the same
// "just point at the engine's own default" approach ODGarageEnvironment uses
// for /Engine/BasicShapes/Cube. No project-authored cloud material exists
// yet, and this instance already ships with plausible default cumulus-ish
// noise params, so it's a reasonable Phase 1 cloudscape without needing a
// new content asset.
const TCHAR* CloudMaterialPath =
	TEXT("/Engine/EngineSky/VolumetricClouds/m_SimpleVolumetricCloud_Inst.m_SimpleVolumetricCloud_Inst");

// V4 (2026-09-25, next steps.md §7.36) - THE knob for the Silverthorne
// pale-hills veil (item 6: ShowFlag.Atmosphere 0 stripped (-20,-24,-36) off
// distant hills). Scales view->surface distance for aerial perspective on
// geometry only (not the sky dome), so haze on a hill at D km looks like it
// did at D*scale km. Engine default 1.0; lower = clearer distant terrain,
// too low flattens depth cueing. Tune in small steps (0.05-0.1) live -
// the r.SkyAtmosphere.AerialPerspective.StartDepth cvar is a proven no-op.
constexpr float AerialPerspectiveViewDistanceScale = 0.8f;

// ---- Weather W2 (sec 7.40) targets at full weight ----
const FLinearColor DustTan(1.0f, 0.72f, 0.42f);
constexpr float FullCloudCoverage = 0.5f;     // m_SimpleVolumetricCloud_Inst Cloud_GlobalCoverage (baseline -0.2)
constexpr float OvercastSkyLuminance = 0.35f; // x SkyLuminanceFactor
constexpr float OvercastSun = 0.25f;          // x sun intensity
constexpr float OvercastSkyLight = 0.6f;      // x sky light intensity
constexpr float FlashSeconds = 0.35f;
}

AODAtmosphereController::AODAtmosphereController()
{
	PrimaryActorTick.bCanEverTick = true;   // Weather W2
}

void AODAtmosphereController::BeginPlay()
{
	Super::BeginPlay();
	EnsureSkyAtmosphere();
	EnsureHeightFog();
	EnsureVolumetricCloud();
	SpawnGradeVolume();
}

void AODAtmosphereController::EnsureSkyAtmosphere()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	for (TActorIterator<ASkyAtmosphere> It(World); It; ++It)
	{
		Atmosphere = *It;
		break;
	}
	if (!Atmosphere.IsValid())
	{
		Atmosphere = World->SpawnActor<ASkyAtmosphere>();
		UE_LOG(LogTemp, Display, TEXT("ODAtmosphereController: spawned SkyAtmosphere (none found in level)"));
	}

	USkyAtmosphereComponent* Comp = Atmosphere.IsValid()
		? Atmosphere->FindComponentByClass<USkyAtmosphereComponent>() : nullptr;
	if (!Comp)
	{
		return;
	}

	// High-altitude mountain haze tuning (Summit County, CO - valley floor
	// ~2700 m). Thinner air up here means weaker Rayleigh (less of the deep
	// saturated sea-level blue) but a stronger, whiter Mie haze band that
	// hugs distant ridgelines - the classic "mountains fade to pale blue-grey,
	// not a crisp horizon" look this pass is chasing, as opposed to the
	// engine's sea-level defaults which read too clean/contrasty at range for
	// The Crew 2-style aerial perspective.
	//
	// IMPORTANT: these are absolute per-kilometer scattering coefficients,
	// NOT "1.0 = neutral" multipliers - the engine's own real-Earth defaults
	// are RayleighScatteringScale=0.0331, MieScatteringScale=0.004 (see
	// USkyAtmosphereComponent's constructor). A first pass here used 0.85/3.0
	// thinking those were reasonable "slightly stronger than default"
	// values - actually 25x/750x the real coefficients - and produced a
	// completely opaque orange whiteout in the -odshot=25 QA screenshot
	// (every pixel saturated by inscattering). Corrected to modest multiples
	// of the real defaults instead.
	Comp->SetRayleighScatteringScale(0.028f);   // slightly below 0.0331 default
	Comp->SetMieScatteringScale(0.009f);        // ~2.25x the 0.004 default
	Comp->SetMieAnisotropy(0.82f);              // close to 0.8 default, mild bump
	Comp->SetAerialPerspectiveStartDepth(0.07f);   // vs 0.1 default - haze builds a bit sooner
	Comp->SetHeightFogContribution(1.0f);
	Comp->SetAerialPespectiveViewDistanceScale(AerialPerspectiveViewDistanceScale); // sic - engine's own spelling
	AtmoComp = Comp;
	BaseSkyLuminance = Comp->SkyLuminanceFactor;

	UE_LOG(LogTemp, Display, TEXT("ODAtmosphereController: tuned SkyAtmosphere for high-altitude haze"));
}

void AODAtmosphereController::EnsureHeightFog()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	for (TActorIterator<AExponentialHeightFog> It(World); It; ++It)
	{
		Fog = *It;
		break;
	}
	if (!Fog.IsValid())
	{
		Fog = World->SpawnActor<AExponentialHeightFog>();
		UE_LOG(LogTemp, Display, TEXT("ODAtmosphereController: spawned ExponentialHeightFog (none found in level)"));
	}

	UExponentialHeightFogComponent* Comp = Fog.IsValid()
		? Fog->FindComponentByClass<UExponentialHeightFogComponent>() : nullptr;
	if (!Comp)
	{
		return;
	}

	// Thin, mostly-volumetric ground haze - the SkyAtmosphere above already
	// carries most of the distant aerial-perspective look; this height fog is
	// deliberately light so it reads as "thin mountain air catching light near
	// the valley floor" rather than a low-lying blanket that would swallow
	// nearby terrain/roads.
	// Second pass (still 2026-09-01, same -odshot QA loop that caught the
	// scattering-scale mistake above): 0.006/0.6/5000 was STILL a total
	// close-range whiteout - a stock, completely UN-configured
	// AExponentialHeightFog defaults to FogDensity=0.02, FogMaxOpacity=1.0,
	// StartDistance=0 (the "why does UE fog look awful out of the box"
	// everyone hits), and 0.006/0.6 wasn't nearly far enough below that to
	// read as thin high-altitude haze instead of pea soup. Cut density by
	// another ~6x and pushed StartDistance out so nearby road/car geometry
	// stays clean and only distance haze builds.
	Comp->SetFogDensity(0.001f);
	Comp->SetFogHeightFalloff(0.1f);
	Comp->SetFogMaxOpacity(0.45f);
	Comp->SetStartDistance(20000.0f);
	// Let the SkyAtmosphere drive inscattering color (default when a
	// SkyAtmosphere is present) so the haze tint tracks the sun through the
	// day/night cycle automatically instead of being pinned to one baked
	// color - this is what keeps deliverable 4 (sun angle -> fog/atmosphere
	// consistency) true with no extra per-tick code needed here.
	Comp->bOverrideLightColorsWithFogInscatteringColors = false;

	Comp->SetVolumetricFog(true);
	Comp->SetVolumetricFogScatteringDistribution(0.7f);
	Comp->SetVolumetricFogExtinctionScale(0.5f);
	Comp->SetVolumetricFogDistance(6000.0f);
	Comp->SetVolumetricFogStartDistance(0.0f);
	// Deliberately NOT touching the volumetric fog froxel grid resolution/
	// density CVars (r.VolumetricFog.GridPixelSize etc.) - perf note from the
	// task: "keep volumetric fog grid at defaults."

	FogComp = Comp;
	BaseFogDensity = Comp->FogDensity;
	BaseFogFalloff = Comp->FogHeightFalloff;
	BaseFogOpacity = Comp->FogMaxOpacity;
	BaseFogStart = Comp->StartDistance;
	BaseFogAmbientScale = Comp->SkyAtmosphereAmbientContributionColorScale;
	BaseFogAlbedo = Comp->VolumetricFogAlbedo;

	UE_LOG(LogTemp, Display, TEXT("ODAtmosphereController: tuned ExponentialHeightFog for ground haze"));
}

void AODAtmosphereController::EnsureVolumetricCloud()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	for (TActorIterator<AVolumetricCloud> It(World); It; ++It)
	{
		Cloud = *It;
		break;
	}
	if (!Cloud.IsValid())
	{
		Cloud = World->SpawnActor<AVolumetricCloud>();
		UE_LOG(LogTemp, Display, TEXT("ODAtmosphereController: spawned VolumetricCloud (none found in level)"));
	}

	UVolumetricCloudComponent* Comp = Cloud.IsValid()
		? Cloud->FindComponentByClass<UVolumetricCloudComponent>() : nullptr;
	if (!Comp)
	{
		return;
	}

	if (UMaterialInterface* CloudMat = LoadObject<UMaterialInterface>(nullptr, CloudMaterialPath))
	{
		// MID so Weather W2 can drive coverage (Tick); baselines read back
		// from the instance, so Clear restores them exactly.
		CloudMID = UMaterialInstanceDynamic::Create(CloudMat, this);
		BaseCloudCoverage = CloudMID->K2_GetScalarParameterValue(TEXT("Cloud_GlobalCoverage"));
		BaseStormClouds = CloudMID->K2_GetScalarParameterValue(TEXT("StormClouds"));
		Comp->SetMaterial(CloudMID);
	}
	else
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODAtmosphereController: couldn't load default cloud material '%s' - "
			     "VolumetricCloud actor will exist but render nothing"), CloudMaterialPath);
	}

	// A plausible default cumulus deck: base a couple km up (well above any
	// terrain/road geometry, which tops out well under 1 km of local relief),
	// a few hundred meters thick.
	Comp->LayerBottomAltitude = 1.5f;   // km above ground
	Comp->LayerHeight = 3.5f;           // km
	Comp->TracingStartMaxDistance = 350.0f;
	Comp->TracingMaxDistance = 50.0f;

	UE_LOG(LogTemp, Display, TEXT("ODAtmosphereController: configured VolumetricCloud layer"));
}

void AODAtmosphereController::SpawnGradeVolume()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	// Always spawns its own volume rather than searching for/reusing an
	// existing "Grade" PostProcessVolume some levels already bake in (see
	// class comment) - both stay unbound and both blend, but this one uses a
	// much higher Priority so its overridden fields win per-property.
	FActorSpawnParameters SpawnParams;
	GradeVolume = World->SpawnActor<APostProcessVolume>();
	if (!GradeVolume.IsValid())
	{
		return;
	}
	GradeVolume->bUnbound = true;
	GradeVolume->BlendWeight = 1.0f;
	GradeVolume->Priority = 100.0f;

	FPostProcessSettings& S = GradeVolume->Settings;

	// Filmic contrast, restrained (not cartoon-vivid) saturation - the whole
	// point of Phase 1's "grounded, semi-photoreal" direction. Slightly BELOW
	// 1.0 on saturation deliberately undercuts any existing per-level "Grade"
	// volume pushing saturation up (e.g. build_openworld_test_level.py's own
	// Grade at 1.22, tuned for a different problem - whitewashed terrain -
	// not for overall look direction); this volume's higher Priority makes
	// this value win.
	S.bOverride_ColorSaturation = true;
	S.ColorSaturation = FVector4(0.92, 0.92, 0.92, 1.0);
	S.bOverride_ColorContrast = true;
	S.ColorContrast = FVector4(1.08, 1.08, 1.08, 1.0);
	S.bOverride_ColorGamma = true;
	S.ColorGamma = FVector4(1.0, 1.0, 1.0, 1.0);

	// Gentle bloom - present, not glowy.
	S.bOverride_BloomIntensity = true;
	S.BloomIntensity = 0.5f;
	S.bOverride_BloomThreshold = true;
	S.BloomThreshold = 1.0f;

	// Subtle vignette.
	S.bOverride_VignetteIntensity = true;
	S.VignetteIntensity = 0.25f;

	// Auto-exposure range wide enough to cover full daylight down to
	// AODDayNightController's moonlit night (MoonIntensity=0.3 lux key light,
	// added specifically so night has something to expose against instead of
	// clipping to black - see that class's comment). EV100 units apply here
	// because DefaultEngine.ini already sets
	// r.DefaultFeature.AutoExposure.ExtendDefaultLuminanceRange=True project-wide.
	S.bOverride_AutoExposureMinBrightness = true;
	S.AutoExposureMinBrightness = -4.0f;
	S.bOverride_AutoExposureMaxBrightness = true;
	S.AutoExposureMaxBrightness = 12.0f;
	// 2026-09-04 FIX: this volume's higher Priority (100) means its
	// AutoExposureBias wins over build_openworld_test_level.py's own Grade
	// volume for this field - and this had been silently set back to 0.0
	// (neutral), un-doing that other volume's documented -1.0 EV fix for the
	// exact "roads/terrain look whitewashed" symptom this comment block
	// already describes (same root problem this session's ground-material
	// bleaching bug turned out to be: a wide-open, near-horizontal, mostly
	// sky-visible ground plane meters disproportionately bright next to
	// smaller/more self-occluded geometry like the car or tree trunks, so
	// auto-exposure alone needs a real negative bias to compensate - a
	// diagnostic render with M_GroundStylized's BaseColor hardcoded to a
	// flat dark green (0.05,0.15,0.05), well below any texture/vertex-tint
	// path, still rendered as a bright saturated mint green, proving this
	// is an exposure/grade issue, not a material-graph or vertex-color one).
	// Restored to match that proven value rather than re-derived from
	// scratch, then confirmed by re-render at all three §7.15 vantages.
	S.bOverride_AutoExposureBias = true;
	S.AutoExposureBias = -1.0f;
	S.bOverride_AutoExposureSpeedUp = true;
	S.AutoExposureSpeedUp = 3.0f;
	S.bOverride_AutoExposureSpeedDown = true;
	S.AutoExposureSpeedDown = 1.0f;

	UE_LOG(LogTemp, Display, TEXT("ODAtmosphereController: spawned Grade post-process volume "
		"(Priority=%.0f, saturation=%.2f, contrast=%.2f)"),
		GradeVolume->Priority, S.ColorSaturation.X, S.ColorContrast.X);
}

void AODAtmosphereController::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	if (!WeatherCtl.IsValid())
	{
		for (TActorIterator<AODWeatherController> It(GetWorld()); It; ++It) { WeatherCtl = *It; break; }
	}
	const AODWeatherController* Weather = WeatherCtl.Get();
	if (!Weather)
	{
		return;
	}
	if (BaseSunIntensity <= 0.0f || BaseSkyLightIntensity <= 0.0f)
	{
		FindLights();
	}

	// 0..1 weights, snapped to exactly 0 near Clear so every Lerp below
	// returns its baseline bit-for-bit.
	auto Weight = [](float X) { X = FMath::Clamp(X, 0.0f, 1.0f); return X < 1e-3f ? 0.0f : X; };
	const float FogW = Weight((20000.0f - Weather->VisibilityM) / 15000.0f);
	const float CloudW = Weight((Weather->CloudCover - 0.1f) / 0.9f);
	const float DustW = Weight(Weather->DustAmount);
	const float Flash = UpdateLightning(DeltaSeconds, *Weather);

	// Weather scalars ramp over ~90 s - skip the render-state writes when
	// nothing moved (most frames).
	if (FMath::IsNearlyEqual(FogW, LastFogW, 1e-4f) && FMath::IsNearlyEqual(CloudW, LastCloudW, 1e-4f)
		&& FMath::IsNearlyEqual(DustW, LastDustW, 1e-4f) && Flash == LastFlash)
	{
		return;
	}
	LastFogW = FogW; LastCloudW = CloudW; LastDustW = DustW; LastFlash = Flash;

	if (UExponentialHeightFogComponent* F = FogComp.Get())
	{
		// Koschmieder: visibility V (m) = 3.912 / extinction. The renderer
		// uses FogDensity / 1000 as extinction per cm (SceneCore.cpp), so
		// FogDensity = 39.12 / V. Falloff flattens so the fog is uniform in
		// height, not a valley layer.
		const float TargetDensity = 39.12f / FMath::Max(Weather->VisibilityM, 100.0f);
		F->SetFogDensity(FMath::Lerp(BaseFogDensity, TargetDensity, FogW));
		F->SetFogHeightFalloff(FMath::Lerp(BaseFogFalloff, 0.001f, FogW));
		F->SetFogMaxOpacity(FMath::Lerp(BaseFogOpacity, 1.0f, FogW));
		F->SetStartDistance(FMath::Lerp(BaseFogStart, 0.0f, FogW));
		// Dust: tint the sky-atmosphere-lit inscatter (r.SupportSky
		// AtmosphereAffectsHeightFog, default on) and the volumetric albedo tan.
		F->SetSkyAtmosphereAmbientContributionColorScale(
			FMath::Lerp(BaseFogAmbientScale, BaseFogAmbientScale * DustTan, DustW));
		F->SetVolumetricFogAlbedo(DustW > 0.0f
			? FMath::Lerp(FLinearColor(BaseFogAlbedo), DustTan, DustW).ToFColor(true) : BaseFogAlbedo);
	}
	if (USkyAtmosphereComponent* A = AtmoComp.Get())
	{
		const FLinearColor Tint = FMath::Lerp(FLinearColor::White, DustTan, 0.7f * DustW);
		A->SetSkyLuminanceFactor(BaseSkyLuminance * FMath::Lerp(1.0f, OvercastSkyLuminance, CloudW) * Tint);
	}
	if (CloudMID)
	{
		CloudMID->SetScalarParameterValue(TEXT("Cloud_GlobalCoverage"), FMath::Lerp(BaseCloudCoverage, FullCloudCoverage, CloudW));
		CloudMID->SetScalarParameterValue(TEXT("StormClouds"),
			FMath::Lerp(BaseStormClouds, 1.0f, FMath::Clamp((Weather->CloudCover - 0.85f) / 0.15f, 0.0f, 1.0f)));
	}
	// ponytail: a light at Intensity 0 is AODGarageEnvironment's menu
	// blackout - left alone, so the garage stays dark; it restores the value
	// it cached and the next change here overwrites it from our baseline.
	if (UDirectionalLightComponent* Sun = SunComp.Get(); Sun && BaseSunIntensity > 0.0f && Sun->Intensity > 0.0f)
	{
		Sun->SetIntensity(BaseSunIntensity * FMath::Lerp(1.0f, OvercastSun, CloudW) * (1.0f + Flash));
	}
	if (USkyLightComponent* Sky = SkyLightComp.Get(); Sky && BaseSkyLightIntensity > 0.0f && Sky->Intensity > 0.0f)
	{
		Sky->SetIntensity(BaseSkyLightIntensity * FMath::Lerp(1.0f, OvercastSkyLight, CloudW) * (1.0f + Flash));
	}
}

void AODAtmosphereController::FindLights()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}
	// The sun is the atmosphere sun light (AODDayNightController's moon is
	// spawned with bAtmosphereSunLight=false); fall back to the first one.
	if (BaseSunIntensity <= 0.0f)
	{
		for (TActorIterator<ADirectionalLight> It(World); It; ++It)
		{
			UDirectionalLightComponent* Comp = It->FindComponentByClass<UDirectionalLightComponent>();
			if (Comp && (Comp->IsUsedAsAtmosphereSunLight() || !SunComp.IsValid()))
			{
				SunComp = Comp;
			}
		}
		BaseSunIntensity = SunComp.IsValid() ? SunComp->Intensity : 0.0f;
	}
	if (BaseSkyLightIntensity <= 0.0f)
	{
		for (TActorIterator<ASkyLight> It(World); It; ++It)
		{
			SkyLightComp = It->FindComponentByClass<USkyLightComponent>();
			break;
		}
		BaseSkyLightIntensity = SkyLightComp.IsValid() ? SkyLightComp->Intensity : 0.0f;
	}
}

float AODAtmosphereController::UpdateLightning(float DeltaSeconds, const AODWeatherController& Weather)
{
	if (FlashAge >= 0.0f)
	{
		FlashAge += DeltaSeconds;
	}
	if (Weather.State == EODWeatherState::Thunderstorm && Weather.PrecipIntensity > 0.5f)
	{
		StrikeCountdown -= DeltaSeconds;
		if (StrikeCountdown <= 0.0f)
		{
			StrikeCountdown = FMath::FRandRange(8.0f, 30.0f);
			const float DistM = FMath::FRandRange(500.0f, 8000.0f);
			const float Near = 1.0f - (DistM - 500.0f) / 7500.0f;
			FlashAge = 0.0f;
			FlashPeak = FMath::Lerp(1.5f, 6.0f, Near);
			if (!AmbientMgr.IsValid())
			{
				for (TActorIterator<AODAmbientSoundManager> It(GetWorld()); It; ++It) { AmbientMgr = *It; break; }
			}
			if (AODAmbientSoundManager* Ambient = AmbientMgr.Get())
			{
				Ambient->QueueThunder(DistM / 343.0f, FMath::Lerp(0.35f, 1.0f, Near));
			}
			UE_LOG(LogTemp, Display, TEXT("ODAtmosphereController: lightning at %.0f m (flash x%.1f, thunder in %.1f s)"),
				DistM, 1.0f + FlashPeak, DistM / 343.0f);
		}
	}
	else
	{
		StrikeCountdown = 5.0f;   // first strike shortly after the storm builds
	}

	if (FlashAge < 0.0f || FlashAge > FlashSeconds)
	{
		FlashAge = -1.0f;
		return 0.0f;
	}
	// Double flicker: bright stroke, a dip, a second stroke fading out.
	const float Env = FlashAge < 0.07f ? 1.0f
		: FlashAge < 0.13f ? 0.15f
		: 0.8f * (1.0f - (FlashAge - 0.13f) / (FlashSeconds - 0.13f));
	return FlashPeak * Env;
}
