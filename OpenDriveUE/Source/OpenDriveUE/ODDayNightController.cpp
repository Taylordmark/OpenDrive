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
	BeamMat = LoadObject<UMaterialInterface>(nullptr, TEXT("/Game/OpenDrive/Materials/M_HeadlightBeam.M_HeadlightBeam"));
	if (!BeamMat)
	{
		UE_LOG(LogTemp, Warning, TEXT("ODDayNightController: no M_HeadlightBeam - run unreal/apply_headlight_beam_material.py; no beams"));
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
		// [0], [1] headlights, [2] rear (tail / brake / reverse). Runtime lights
		// default to Unitless (4500 was ~7 cd - Taylor 2026-10-09: "don't
		// actually light anything"), so the units are set explicitly.
		TArray<TWeakObjectPtr<USpotLightComponent>>& Spots = Lights.FindOrAdd(*It);
		if (Spots.Num() == 0)
		{
			for (int32 i = 0; i < 3; ++i)
			{
				USpotLightComponent* Spot = NewObject<USpotLightComponent>(*It);
				Spot->SetupAttachment(It->GetRootComponent());
				Spot->SetIntensityUnits(ELightUnits::Lumens);
				Spot->SetAttenuationRadius(i < 2 ? 9000.0f : 1500.0f);
				Spot->SetInnerConeAngle(i < 2 ? 22.0f : 40.0f);
				Spot->SetOuterConeAngle(i < 2 ? 38.0f : 70.0f);
				Spot->SetCastShadows(i < 2);
				Spot->SetVisibility(false);
				Spot->RegisterComponent();
				Spots.Add(Spot);
			}
		}
		// placed off the body mesh's bounds (body-local, so the def's visual
		// scale carries through): just outside the nose / tail so the
		// two-sided hull can't shadow them, at the lenses' measured height
		// (make_car_meshes.py: heads ~28% up, ~half the half-width)
		const UStaticMeshComponent* Body = Cast<UStaticMeshComponent>(It->GetRootComponent());
		const FBox B = Body && Body->GetStaticMesh() ? Body->GetStaticMesh()->GetBoundingBox()
			: FBox(FVector(-220.0, -90.0, -20.0), FVector(220.0, 90.0, 100.0));
		const double H = B.Max.Z - B.Min.Z;
		const UODVehicleMovement* Move = It->FindComponentByClass<UODVehicleMovement>();
		const bool bBrake = Move->GetPedalBrake() > 0.05f;
		const bool bReverse = Move->GetGear() < 0;
		// which rear-lamp signal flips (2026-10-09, Taylor: backup lights
		// flicker driving forward - gear never reads reverse in the trace)
		if (It->IsPlayerControlled() && LastLampState != (int32(bBrake) + 2 * int32(bReverse)))
		{
			LastLampState = int32(bBrake) + 2 * int32(bReverse);
			UE_LOG(LogTemp, Warning, TEXT("ODLamps: t=%.2f brake=%d reverse=%d pedal=%.3f gear=%d spd=%.0f"),
				GetWorld()->GetTimeSeconds(), bBrake, bReverse, Move->GetPedalBrake(),
				Move->GetGear(), Move->GetForwardSpeed());
		}
		const float RearLm = bReverse ? ReverseLightLm : bBrake ? BrakeLightLm : bOn ? TailLightLm : 0.0f;
		for (int32 i = 0; i < Spots.Num(); ++i)
		{
			USpotLightComponent* Spot = Spots[i].Get();
			if (!Spot)
			{
				continue;
			}
			const bool bRear = i == 2;
			Spot->SetRelativeLocationAndRotation(
				bRear ? FVector(B.Min.X - 5.0, 0.0, B.Min.Z + 0.4 * H)
				      : FVector(B.Max.X + 5.0, (i ? 0.5 : -0.5) * B.Max.Y, B.Min.Z + 0.28 * H),
				FRotator(bRear ? -15.0f : -6.0f, bRear ? 180.0f : 0.0f, 0.0f));
			const float Lm = bRear ? RearLm : (bOn ? HeadlightLm : 0.0f);
			Spot->SetIntensity(Lm);
			Spot->SetLightColor(bRear ? (bReverse ? FLinearColor::White : FLinearColor(1.0f, 0.02f, 0.01f))
				: FLinearColor(1.0f, 0.95f, 0.85f));
			if (Spot->GetVisibleFlag() != (Lm > 0.0f))
			{
				Spot->SetVisibility(Lm > 0.0f);
			}
		}
		// visible beams (BeamHaze): M_HeadlightBeam on a sphere off the nose,
		// fed both head spots relative to its centre. ponytail: unshadowed -
		// the beam shows through trees it passes (it still ends where the view
		// hits them); light-space occlusion would need the spots' shadow maps.
		UStaticMeshComponent* Beam = It->FindComponentByTag<UStaticMeshComponent>(TEXT("HeadlightBeam"));
		if (!Beam && BeamMat)
		{
			Beam = NewObject<UStaticMeshComponent>(*It);
			Beam->ComponentTags.Add(TEXT("HeadlightBeam"));
			Beam->SetupAttachment(It->GetRootComponent());
			Beam->SetStaticMesh(LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Sphere.Sphere")));
			Beam->SetCollisionEnabled(ECollisionEnabled::NoCollision);
			Beam->SetCastShadow(false);
			Beam->bAffectDistanceFieldLighting = false;
			Beam->bVisibleInReflectionCaptures = false;
			Beam->bVisibleInRayTracing = false;
			Beam->SetMaterial(0, UMaterialInstanceDynamic::Create(BeamMat, Beam));
			Beam->SetVisibility(false);
			Beam->RegisterComponent();
		}
		const USpotLightComponent* L = Spots[0].Get();
		const USpotLightComponent* R = Spots[1].Get();
		const bool bBeam = bOn && BeamHaze > 0.0f && L && R;
		if (Beam && Beam->GetVisibleFlag() != bBeam)
		{
			Beam->SetVisibility(bBeam);
		}
		if (Beam && bBeam)
		{
			const FVector Dir = L->GetForwardVector();
			const FVector Centre = (L->GetComponentLocation() + R->GetComponentLocation()) * 0.5 + Dir * (BeamRadius - 300.0);
			Beam->SetWorldLocation(Centre);
			Beam->SetWorldScale3D(FVector(BeamRadius / 50.0));   // engine sphere: 50 cm radius
			UMaterialInstanceDynamic* BeamMID = Cast<UMaterialInstanceDynamic>(Beam->GetMaterial(0));
			const float Cd = HeadlightLm / (2.0f * PI * (1.0f - FMath::Cos(FMath::DegreesToRadians(L->OuterConeAngle))));
			BeamMID->SetVectorParameterValue(TEXT("Lamp0"), FLinearColor(L->GetComponentLocation() - Centre));
			BeamMID->SetVectorParameterValue(TEXT("Lamp1"), FLinearColor(R->GetComponentLocation() - Centre));
			BeamMID->SetVectorParameterValue(TEXT("BeamDir"), FLinearColor(Dir));
			BeamMID->SetVectorParameterValue(TEXT("Beam"), FLinearColor(1.0f, 0.95f, 0.85f) * (Cd * BeamHaze * 100.0f));
			BeamMID->SetScalarParameterValue(TEXT("Radius"), BeamRadius);
			BeamMID->SetScalarParameterValue(TEXT("CosIn"), FMath::Cos(FMath::DegreesToRadians(L->InnerConeAngle)));
			BeamMID->SetScalarParameterValue(TEXT("CosOut"), FMath::Cos(FMath::DegreesToRadians(L->OuterConeAngle)));
		}
		// lamp lenses: the car mesh's "lights" slot (head, tail, reverse share it)
		const float Head = bOn ? HeadlampGlow : 0.0f;
		// at night brake / reverse = 5x tail (a fixed daylight value blooms the scene)
		const float Signal = bOn ? TaillampGlow * 5.0f : BrakelampGlow;
		// reversing keeps the brake lamps lit too (Taylor 2026-10-09)
		const float Tail = (bBrake || bReverse) ? Signal : (bOn ? TaillampGlow : 0.0f);
		TInlineComponentArray<UStaticMeshComponent*> Meshes(*It);
		for (UStaticMeshComponent* Mesh : Meshes)
		{
			const int32 Slot = Mesh->GetMaterialIndex(TEXT("lights"));
			if (Slot == INDEX_NONE)
			{
				continue;
			}
			UMaterialInstanceDynamic* Lamp = Cast<UMaterialInstanceDynamic>(Mesh->GetMaterial(Slot));
			if (!Lamp)   // first tick, or a car / detail-tier swap put the asset back
			{
				Lamp = Mesh->CreateDynamicMaterialInstance(Slot);
			}
			Lamp->SetScalarParameterValue(TEXT("HeadGlow"), Head);
			Lamp->SetScalarParameterValue(TEXT("TailGlow"), Tail);
			Lamp->SetScalarParameterValue(TEXT("ReverseGlow"), bReverse ? Signal : 0.0f);
		}
	}
}
