#include "ODAtmosphereController.h"

#include "Camera/PlayerCameraManager.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/ExponentialHeightFogComponent.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Components/SkyAtmosphereComponent.h"
#include "Components/SkyLightComponent.h"
#include "Components/VolumetricCloudComponent.h"
#include "Engine/DirectionalLight.h"
#include "Engine/ExponentialHeightFog.h"
#include "Engine/PostProcessVolume.h"
#include "Engine/SkyLight.h"
#include "Engine/StaticMesh.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "Math/RandomStream.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/MaterialInterface.h"
#include "ODAmbientSoundManager.h"
#include "ODVisualStyle.h"
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
// Visual Style Realistic (PLAN-VISUALS.md Track A): a bit more haze for depth
// between ridgelines. Kept short of the 1.0 that caused the pale-hills veil.
constexpr float RealisticAerialPerspectiveScale = 0.9f;
constexpr float RealisticFogDensity = 0.0013f;   // Stylized 0.001

// ---- Weather W2 (sec 7.40) targets at full weight ----
const FLinearColor DustTan(1.0f, 0.72f, 0.42f);
const FLinearColor StormGreen(0.62f, 0.9f, 0.55f);   // W4 tornado sky (StormTint)
constexpr float FullCloudCoverage = 0.5f;     // m_SimpleVolumetricCloud_Inst Cloud_GlobalCoverage (baseline -0.2)
constexpr float OvercastSkyLuminance = 0.35f; // x SkyLuminanceFactor
constexpr float OvercastSun = 0.25f;          // x sun intensity
constexpr float OvercastSkyLight = 0.6f;      // x sky light intensity
constexpr float FlashSeconds = 0.35f;

// ---- Lightning bolt + flash light (PLAN-REALISM Phase 6, 2026-10-09) ----
// Mirrored in pipeline/check_lightning.py.
constexpr float BoltCloudBaseM = 1500.0f;   // bolt top above the camera
constexpr int32 BoltSegments = 14;
constexpr int32 BoltBranchSegments = 5;
constexpr float BoltJitter = 0.5f;          // lateral step / vertical step
constexpr float BoltWidthPerKm = 1.5f;      // m wide per km away: stays a few px at 8 km
constexpr float BoltMinWidthM = 1.0f;
constexpr float FlashLightScale = 0.15f;    // flash light = day sun x this x Flash (peak 1.5..6)
constexpr float FlashLightFallbackLux = 10.0f;
// Double flicker: bright stroke, a dip, a second stroke fading linearly out.
constexpr float FlashStrokeS = 0.07f;
constexpr float FlashDipEndS = 0.13f;
constexpr float FlashDipLevel = 0.15f;
constexpr float FlashTailLevel = 0.8f;

// Integral of the flash envelope from 0 to T (0 before the strike). Tick
// shows the envelope's MEAN over the frame, so every frame rate sees the
// same light per strike and a slow frame can't skip the first stroke
// (point-sampling did below ~15 fps).
float FlashIntegral(float T)
{
	const float A = FMath::Clamp(T, 0.0f, FlashStrokeS);
	const float B = FMath::Clamp(T, FlashStrokeS, FlashDipEndS) - FlashStrokeS;
	const float Tail = FlashSeconds - FlashDipEndS;
	const float C = FMath::Clamp(T, FlashDipEndS, FlashSeconds) - FlashDipEndS;
	return A + FlashDipLevel * B + FlashTailLevel * (C - 0.5f * C * C / Tail);
}

// Appends N cylinder segments (/Engine/BasicShapes/Cylinder: 100 uu tall,
// 100 uu across, centred) walking from A along Dir with sideways jitter;
// returns the path's points (A first).
TArray<FVector> AddBoltPath(TArray<FTransform>& Out, FRandomStream& R, FVector A, const FVector& Dir,
	int32 N, float StepCm, float WidthCm)
{
	TArray<FVector> Pts{A};
	for (int32 i = 0; i < N; ++i)
	{
		const FVector B = A + Dir * StepCm + FVector(R.FRandRange(-1.0f, 1.0f), R.FRandRange(-1.0f, 1.0f), 0.0f) * StepCm * BoltJitter;
		const FVector D = B - A;
		Out.Add(FTransform(FRotationMatrix::MakeFromZ(D).Rotator(), (A + B) * 0.5f,
			FVector(WidthCm / 100.0f, WidthCm / 100.0f, D.Size() / 100.0f)));
		Pts.Add(B);
		A = B;
	}
	return Pts;
}
}

AODAtmosphereController::AODAtmosphereController()
{
	PrimaryActorTick.bCanEverTick = true;   // Weather W2
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));   // bolt + flash light attach here
}

void AODAtmosphereController::EnsureBoltComponents()
{
	if (BoltISM)
	{
		return;
	}
	UMaterialInterface* Mat = LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_LightningBolt.M_LightningBolt"));
	if (!Mat)
	{
		UE_LOG(LogTemp, Warning, TEXT("ODAtmosphereController: M_LightningBolt missing (unreal/apply_lightning_material.py) - default material"));
	}
	BoltMID = Mat ? UMaterialInstanceDynamic::Create(Mat, this) : nullptr;
	BoltISM = NewObject<UInstancedStaticMeshComponent>(this);
	BoltISM->SetupAttachment(RootComponent);
	BoltISM->SetUsingAbsoluteLocation(true);
	BoltISM->SetStaticMesh(LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cylinder.Cylinder")));
	if (BoltMID)
	{
		BoltISM->SetMaterial(0, BoltMID);
	}
	BoltISM->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	BoltISM->SetCastShadow(false);
	BoltISM->SetVisibility(false);
	BoltISM->RegisterComponent();

	FlashLight = NewObject<UDirectionalLightComponent>(this);
	FlashLight->SetupAttachment(RootComponent);
	FlashLight->SetUsingAbsoluteRotation(true);
	FlashLight->SetMobility(EComponentMobility::Movable);
	FlashLight->SetCastShadows(false);
	FlashLight->SetLightColor(FLinearColor(0.75f, 0.8f, 1.0f));
	FlashLight->SetAtmosphereSunLight(true);       // lights the clouds and sky too
	FlashLight->SetAtmosphereSunLightIndex(1);     // the sun is index 0
	FlashLight->SetAtmosphereSunDiskColorScale(FLinearColor::Black);   // no second sun disk
	FlashLight->SetIntensity(0.0f);
	FlashLight->SetVisibility(false);
	FlashLight->RegisterComponent();
}

void AODAtmosphereController::Strike(float DistM)
{
	EnsureBoltComponents();
	const APlayerCameraManager* Cam = UGameplayStatics::GetPlayerCameraManager(this, 0);
	const FVector C = Cam ? Cam->GetCameraLocation() : GetActorLocation();
	FRandomStream R(FMath::Rand());
	const float Bearing = R.FRandRange(0.0f, 2.0f * PI);
	const FVector Ground = C + FVector(FMath::Cos(Bearing), FMath::Sin(Bearing), 0.0f) * DistM * 100.0f;
	const float HeightCm = BoltCloudBaseM * 100.0f;
	const float WidthCm = FMath::Max(BoltMinWidthM, BoltWidthPerKm * DistM / 1000.0f) * 100.0f;
	const float StepCm = HeightCm / BoltSegments;
	TArray<FTransform> Xf;
	const TArray<FVector> Main = AddBoltPath(Xf, R, Ground + FVector(0.0f, 0.0f, HeightCm), FVector::DownVector,
		BoltSegments, StepCm, WidthCm);
	const FVector Out = FVector(R.FRandRange(-1.0f, 1.0f), R.FRandRange(-1.0f, 1.0f), 0.0f).GetSafeNormal();
	AddBoltPath(Xf, R, Main[R.RandRange(2, BoltSegments / 2)], (FVector::DownVector + Out * 0.8f).GetSafeNormal(),
		BoltBranchSegments, StepCm * 0.8f, WidthCm * 0.5f);
	BoltISM->ClearInstances();
	BoltISM->AddInstances(Xf, false);
	FlashLight->SetWorldRotation((C - (Ground + FVector(0.0f, 0.0f, HeightCm * 0.5f))).Rotation());
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
	Comp->SetAerialPespectiveViewDistanceScale(ODVisualStyle::IsRealistic()   // sic - engine's own spelling
		? RealisticAerialPerspectiveScale : AerialPerspectiveViewDistanceScale);
	// Large-world (2026-10-02): the default PlanetTopAtAbsoluteWorldOrigin puts
	// the planet top at (0,0,0), so an area ~650 km from the origin sat ~33 km
	// up the curve. Anchor the planet top to the component instead; Tick keeps
	// it under the camera.
	Comp->SetMobility(EComponentMobility::Movable);
	Comp->TransformMode = ESkyAtmosphereTransformMode::PlanetTopAtComponentTransform;
	Comp->SetWorldLocation(FVector::ZeroVector);
	Comp->MarkRenderStateDirty();
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
	Comp->SetFogDensity(ODVisualStyle::IsRealistic() ? RealisticFogDensity : 0.001f);
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

	// Visual Style Realistic (PLAN-VISUALS.md Track A): the "car ad" grade -
	// warm highlights, firmer shadows, more bloom, no grain. Color-grading
	// fields, not a LUT texture, so there's no asset to build. Exposure is
	// untouched (the -1 bias fix above holds for both styles).
	if (ODVisualStyle::IsRealistic())
	{
		S.ColorSaturation = FVector4(0.98, 0.98, 0.98, 1.0);
		S.ColorContrast = FVector4(1.12, 1.12, 1.12, 1.0);
		S.bOverride_ColorGainHighlights = true;
		S.ColorGainHighlights = FVector4(1.04, 1.0, 0.94, 1.0);
		S.bOverride_ColorGammaShadows = true;
		S.ColorGammaShadows = FVector4(0.98, 0.99, 1.02, 1.0);   // cool, barely crushed (0.95 was too dark at night)
		S.BloomIntensity = 0.7f;
		S.BloomThreshold = 0.8f;
		S.VignetteIntensity = 0.3f;
		S.bOverride_FilmGrainIntensity = true;
		S.FilmGrainIntensity = 0.0f;
		// Car glass is translucent, so without this it only gets the blurry
		// radiance-cache reflection - windscreens read as flat grey. Front layer
		// = one extra GBuffer-ish pass for the nearest translucent surface only.
		S.bOverride_LumenFrontLayerTranslucencyReflections = true;
		S.LumenFrontLayerTranslucencyReflections = 1;
	}

	UE_LOG(LogTemp, Display, TEXT("ODAtmosphereController: spawned Grade post-process volume "
		"(style=%d, Priority=%.0f, saturation=%.2f, contrast=%.2f, bloom=%.2f@%.2f, vignette=%.2f)"),
		ODVisualStyle::Level(), GradeVolume->Priority, S.ColorSaturation.X, S.ColorContrast.X,
		S.BloomIntensity, S.BloomThreshold, S.VignetteIntensity);
}

void AODAtmosphereController::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	// Keep the planet top under the camera, snapped to 1 km so the render
	// state isn't dirtied every frame (curvature at 1 km is 8 cm).
	if (USkyAtmosphereComponent* Atmo = AtmoComp.Get())
	{
		const APlayerController* PC = GetWorld()->GetFirstPlayerController();
		if (PC && PC->PlayerCameraManager)
		{
			const FVector Cam = PC->PlayerCameraManager->GetCameraLocation();
			const FVector Snap(FMath::RoundToDouble(Cam.X / 1e5) * 1e5, FMath::RoundToDouble(Cam.Y / 1e5) * 1e5, 0.0);
			if (!Atmo->GetComponentLocation().Equals(Snap, 1.0))
			{
				Atmo->SetWorldLocation(Snap);
			}
		}
	}

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
	const float TintW = Weight(Weather->StormTint);
	const FLinearColor Green = FMath::Lerp(FLinearColor::White, StormGreen, TintW);
	const float Flash = UpdateLightning(DeltaSeconds, *Weather);

	// Weather scalars ramp over ~90 s - skip the render-state writes when
	// nothing moved (most frames).
	if (FMath::IsNearlyEqual(FogW, LastFogW, 1e-4f) && FMath::IsNearlyEqual(CloudW, LastCloudW, 1e-4f)
		&& FMath::IsNearlyEqual(DustW, LastDustW, 1e-4f) && FMath::IsNearlyEqual(TintW, LastTintW, 1e-4f)
		&& Flash == LastFlash)
	{
		return;
	}
	LastFogW = FogW; LastCloudW = CloudW; LastDustW = DustW; LastTintW = TintW; LastFlash = Flash;

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
			FMath::Lerp(BaseFogAmbientScale, BaseFogAmbientScale * DustTan, DustW) * Green);
		F->SetVolumetricFogAlbedo(DustW > 0.0f
			? FMath::Lerp(FLinearColor(BaseFogAlbedo), DustTan, DustW).ToFColor(true) : BaseFogAlbedo);
	}
	if (USkyAtmosphereComponent* A = AtmoComp.Get())
	{
		const FLinearColor Tint = FMath::Lerp(FLinearColor::White, DustTan, 0.7f * DustW) * Green;
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
		Sun->SetIntensity(BaseSunIntensity * FMath::Lerp(1.0f, OvercastSun, CloudW));
	}
	// Lightning (Phase 6): the bolt glows and FlashLight fires with Flash -
	// day or night, since the night sun is below the horizon. Skipped in the
	// garage blackout (sun at 0) like the lights above.
	const bool bFlash = Flash > 0.0f && !(SunComp.IsValid() && SunComp->Intensity <= 0.0f);
	if (BoltISM)
	{
		BoltISM->SetVisibility(bFlash);
		if (BoltMID)
		{
			BoltMID->SetScalarParameterValue(TEXT("Glow"), Flash);
		}
	}
	if (FlashLight)
	{
		// the moon shares atmosphere index 1 (its disk) - it steps aside so
		// two lights never compete for the slot
		if (!MoonComp.IsValid())
		{
			for (TActorIterator<ADirectionalLight> It(GetWorld()); It; ++It)
			{
				UDirectionalLightComponent* Comp = It->FindComponentByClass<UDirectionalLightComponent>();
				if (Comp && Comp->IsUsedAsAtmosphereSunLight() && Comp->GetAtmosphereSunLightIndex() == 1)
				{
					MoonComp = Comp;
				}
			}
		}
		if (MoonComp.IsValid())
		{
			MoonComp->SetVisibility(!bFlash);
		}
		FlashLight->SetVisibility(bFlash);
		FlashLight->SetIntensity((BaseSunIntensity > 0.0f ? BaseSunIntensity : FlashLightFallbackLux) * FlashLightScale * Flash);
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
	// The sun is atmosphere light 0 (AODDayNightController's moon is
	// atmosphere light 1, for its disk); fall back to the first one.
	if (BaseSunIntensity <= 0.0f)
	{
		for (TActorIterator<ADirectionalLight> It(World); It; ++It)
		{
			UDirectionalLightComponent* Comp = It->FindComponentByClass<UDirectionalLightComponent>();
			if (Comp && ((Comp->IsUsedAsAtmosphereSunLight() && Comp->GetAtmosphereSunLightIndex() == 0) || !SunComp.IsValid()))
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
	if (Weather.State == EODWeatherState::Thunderstorm && Weather.PrecipIntensity > 0.5f && !Weather.IsIndoors())
	{
		StrikeCountdown -= DeltaSeconds;
		if (StrikeCountdown <= 0.0f)
		{
			// the strike happened -StrikeCountdown ago, inside this frame: the
			// flash and the thunder delay both start from that instant
			const float Late = -StrikeCountdown;
			StrikeCountdown += FMath::FRandRange(8.0f, 30.0f);
			const float DistM = FMath::FRandRange(500.0f, 8000.0f);
			const float Near = 1.0f - (DistM - 500.0f) / 7500.0f;
			FlashAge = Late;
			FlashPeak = FMath::Lerp(1.5f, 6.0f, Near);
			Strike(DistM);
			if (!AmbientMgr.IsValid())
			{
				for (TActorIterator<AODAmbientSoundManager> It(GetWorld()); It; ++It) { AmbientMgr = *It; break; }
			}
			if (AODAmbientSoundManager* Ambient = AmbientMgr.Get())
			{
				Ambient->QueueThunder(FMath::Max(DistM / 343.0f - Late, 0.0f), FMath::Lerp(0.35f, 1.0f, Near));
			}
			UE_LOG(LogTemp, Display, TEXT("ODAtmosphereController: lightning at %.0f m (flash x%.1f, thunder in %.1f s)"),
				DistM, 1.0f + FlashPeak, DistM / 343.0f);
		}
	}
	else
	{
		StrikeCountdown = 5.0f;   // first strike shortly after the storm builds
	}

	// frame window [FlashAge - Dt, FlashAge]; done once it starts past the end
	if (FlashAge < 0.0f || FlashAge - DeltaSeconds >= FlashSeconds)
	{
		FlashAge = -1.0f;
		return 0.0f;
	}
	const float Dt = FMath::Max(DeltaSeconds, 1.0e-4f);
	return FlashPeak * (FlashIntegral(FlashAge) - FlashIntegral(FlashAge - Dt)) / Dt;
}
