#include "ODDayNightController.h"

#include "Components/DirectionalLightComponent.h"
#include "Components/SpotLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/DirectionalLight.h"
#include "Engine/PostProcessVolume.h"
#include "Engine/StaticMesh.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "Camera/PlayerCameraManager.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/CommandLine.h"
#include "ODVehicleMovement.h"

AODDayNightController::AODDayNightController()
{
	PrimaryActorTick.bCanEverTick = true;
}

void AODDayNightController::BeginPlay()
{
	Super::BeginPlay();
	// -odhour=<H> QA override (2026-09-01, added for atmosphere/lighting
	// screenshot passes - see AODScreenshotHook's -odshot for the matching
	// pattern): lets a headless launch pick a specific time of day instead of
	// always starting at StartHour, so e.g. -odhour=21 gets a night shot
	// without waiting out a full CycleSeconds run.
	FParse::Value(FCommandLine::Get(), TEXT("odhour="), StartHour);
	TimeOfDay = StartHour;
	for (TActorIterator<ADirectionalLight> It(GetWorld()); It; ++It)
	{
		Sun = *It;
		// the moon below keeps priority 0; an equal pair puts an engine
		// "Multiple directional lights are competing" warning on screen
		if (UDirectionalLightComponent* SunComp = Cast<UDirectionalLightComponent>(Sun->GetLightComponent()))
		{
			SunComp->ForwardShadingPriority = 1;
			SunComp->MarkRenderStateDirty();   // the proxy copied 0 at registration
		}
		break;
	}

	FActorSpawnParameters SpawnParams;
	SpawnParams.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
	Moon = GetWorld()->SpawnActor<ADirectionalLight>(ADirectionalLight::StaticClass(), FTransform::Identity, SpawnParams);
	if (Moon.IsValid())
	{
		Moon->SetMobility(EComponentMobility::Movable);
		if (UDirectionalLightComponent* MoonComp = Cast<UDirectionalLightComponent>(Moon->GetLightComponent()))
		{
			MoonComp->SetIntensity(0.0f);
			MoonComp->SetLightColor(FLinearColor(0.6f, 0.7f, 1.0f));  // cool moonlight
			MoonComp->SetCastShadows(false);
			// the sky atmosphere's SECOND light (2026-10-09): draws the moon
			// disk and a faint moonlit sky without taking the sun's slot 0;
			// disk scaled down so it reads white, not a bloom flare
			MoonComp->SetAtmosphereSunLight(true);
			MoonComp->SetAtmosphereSunLightIndex(1);
			MoonComp->SetAtmosphereSunDiskColorScale(FLinearColor(0.02f, 0.02f, 0.02f));
		}
	}

	// night exposure floor, blended in by NightFactor() (see NightMinEV)
	NightGrade = GetWorld()->SpawnActor<APostProcessVolume>(APostProcessVolume::StaticClass(), FTransform::Identity, SpawnParams);
	if (NightGrade.IsValid())
	{
		NightGrade->bUnbound = true;
		NightGrade->Priority = 101.0f;
		NightGrade->BlendWeight = 0.0f;
		NightGrade->Settings.bOverride_AutoExposureMinBrightness = true;
		NightGrade->Settings.AutoExposureMinBrightness = NightMinEV;
	}

	// stars: M_NightSky (unreal/apply_night_sky_material.py) on a 500 km
	// inverted sphere that follows the camera
	UStaticMesh* Sphere = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Sphere.Sphere"));
	UMaterialInterface* StarMat = LoadObject<UMaterialInterface>(nullptr, TEXT("/Game/OpenDrive/Materials/M_NightSky.M_NightSky"));
	if (Sphere && StarMat)
	{
		StarDome = NewObject<UStaticMeshComponent>(this);
		SetRootComponent(StarDome);
		StarDome->SetMobility(EComponentMobility::Movable);
		StarDome->SetStaticMesh(Sphere);
		StarDome->SetWorldScale3D(FVector(1.0e6));   // 50 cm sphere -> 500 km
		StarDome->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		StarDome->SetCastShadow(false);
		StarDome->bAffectDistanceFieldLighting = false;
		StarDome->bVisibleInReflectionCaptures = false;
		StarDome->bVisibleInRayTracing = false;
		StarDome->SetTranslucentSortPriority(-100);   // under rain/snow/bolt
		StarMID = UMaterialInstanceDynamic::Create(StarMat, this);
		StarMID->SetScalarParameterValue(TEXT("Brightness"), StarBrightness);
		StarDome->SetMaterial(0, StarMID);
		StarDome->SetVisibility(false);
		StarDome->RegisterComponent();
	}
	else
	{
		UE_LOG(LogTemp, Warning, TEXT("ODDayNightController: no M_NightSky - run unreal/apply_night_sky_material.py; no stars"));
	}
}

void AODDayNightController::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	TimeOfDay = FMath::Fmod(TimeOfDay + DeltaSeconds / CycleSeconds * 24.0f, 24.0f);

	const float DayPhase = (TimeOfDay - 6.0f) / 12.0f * PI;  // 6h->0, 12h->pi/2
	const float Elevation = FMath::Sin(DayPhase) * MaxElevation;
	const float Azimuth = TimeOfDay * 15.0f + 90.0f;

	if (Sun.IsValid())
	{
		Sun->SetActorRotation(FRotator(-Elevation, Azimuth, 0.0f));
	}
	if (Moon.IsValid())
	{
		// Opposite the sun in azimuth, mirrored elevation - rises as the sun
		// sets, peaks at solar midnight, sets as the sun rises.
		Moon->SetActorRotation(FRotator(Elevation, Azimuth + 180.0f, 0.0f));
		if (UDirectionalLightComponent* MoonComp = Cast<UDirectionalLightComponent>(Moon->GetLightComponent()))
		{
			MoonComp->SetIntensity(Elevation < 0.0f ? MoonIntensity : 0.0f);
		}
	}
	const float Night = NightFactor(Elevation);
	if (!FMath::IsNearlyEqual(Night, LastNight, 1e-3f))
	{
		LastNight = Night;
		if (NightGrade.IsValid())
		{
			NightGrade->BlendWeight = Night;
		}
		if (StarDome)
		{
			StarDome->SetVisibility(Night > 0.0f);
			StarMID->SetScalarParameterValue(TEXT("Night"), Night);
		}
	}
	if (StarDome && Night > 0.0f)
	{
		if (const APlayerController* PC = GetWorld()->GetFirstPlayerController(); PC && PC->PlayerCameraManager)
		{
			StarDome->SetWorldLocation(PC->PlayerCameraManager->GetCameraLocation());
		}
	}
	bHeadlightsOn = Elevation < HeadlightElevation;
	UpdateHeadlights(bHeadlightsOn);
}

void AODDayNightController::UpdateHeadlights(bool bOn)
{
	for (TActorIterator<APawn> It(GetWorld()); It; ++It)
	{
		// headlights only on vehicles (any pawn carrying our movement sim)
		if (!It->FindComponentByClass<UODVehicleMovement>())
		{
			continue;
		}
		TArray<TWeakObjectPtr<USpotLightComponent>>& Pair = Lights.FindOrAdd(*It);
		if (Pair.Num() == 0)
		{
			for (const float Side : {-55.0f, 55.0f})
			{
				USpotLightComponent* Spot = NewObject<USpotLightComponent>(*It);
				Spot->SetupAttachment(It->GetRootComponent());
				Spot->SetRelativeLocation(FVector(160.0f, Side, 70.0f));
				Spot->SetRelativeRotation(FRotator(-6.0f, 0.0f, 0.0f));
				Spot->SetIntensity(4500.0f);                 // lumens
				Spot->SetAttenuationRadius(9000.0f);
				Spot->SetInnerConeAngle(22.0f);
				Spot->SetOuterConeAngle(38.0f);
				Spot->SetLightColor(FLinearColor(1.0f, 0.95f, 0.85f));
				Spot->SetVisibility(false);
				Spot->RegisterComponent();
				Pair.Add(Spot);
			}
		}
		for (const TWeakObjectPtr<USpotLightComponent>& Spot : Pair)
		{
			if (Spot.IsValid() && Spot->GetVisibleFlag() != bOn)
			{
				Spot->SetVisibility(bOn);
			}
		}
	}
}
