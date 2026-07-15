#include "ODDayNightController.h"

#include "Components/SpotLightComponent.h"
#include "Engine/DirectionalLight.h"
#include "EngineUtils.h"
#include "WheeledVehiclePawn.h"

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
	UpdateHeadlights(Elevation < HeadlightElevation);
}

void AODDayNightController::UpdateHeadlights(bool bOn)
{
	for (TActorIterator<AWheeledVehiclePawn> It(GetWorld()); It; ++It)
	{
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
