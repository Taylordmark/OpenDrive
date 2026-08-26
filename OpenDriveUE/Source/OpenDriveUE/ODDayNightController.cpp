#include "ODDayNightController.h"

#include "Components/DirectionalLightComponent.h"
#include "Components/SpotLightComponent.h"
#include "Engine/DirectionalLight.h"
#include "EngineUtils.h"
#include "ODVehicleMovement.h"

AODDayNightController::AODDayNightController()
{
	PrimaryActorTick.bCanEverTick = true;
}

void AODDayNightController::BeginPlay()
{
	Super::BeginPlay();
	TimeOfDay = StartHour;
	for (TActorIterator<ADirectionalLight> It(GetWorld()); It; ++It)
	{
		Sun = *It;
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
			MoonComp->bAtmosphereSunLight = false;  // don't fight the real sun for aerial perspective
		}
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
	UpdateHeadlights(Elevation < HeadlightElevation);
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
