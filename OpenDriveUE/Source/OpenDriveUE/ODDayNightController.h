#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODDayNightController.generated.h"

class ADirectionalLight;
class USpotLightComponent;

/**
 * Day/night cycle (wishlist #2): drives the atmosphere sun through a full
 * cycle and gives every vehicle headlights that switch on around dusk.
 * "Real-time accurate" mode arrives with real-world georeferencing (Arc 7).
 */
UCLASS()
class OPENDRIVEUE_API AODDayNightController : public AActor
{
	GENERATED_BODY()

public:
	AODDayNightController();
	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

	/** Real seconds for a full 24h cycle (default 1 hour). */
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float CycleSeconds = 3600.0f;

	/** Clock time at level start, hours [0-24). */
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float StartHour = 10.0f;

	/** Max solar elevation at noon, degrees. */
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float MaxElevation = 68.0f;

	/** Headlights turn on below this solar elevation (deg). */
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float HeadlightElevation = 8.0f;

	/** Moon light intensity (lux) once the sun drops below the horizon - a
	 * dim night key light so Lumen has something to expose against instead
	 * of clipping to black (2026-08-25, Taylor: exposure-clipping warning /
	 * black screen on the far side of the terrain at night). Deliberately
	 * not a sun-intensity floor - a separate, dimmer, opposite-angle light
	 * reads as "moonlit" instead of "sun that never fully sets". */
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float MoonIntensity = 0.3f;

	UPROPERTY(VisibleAnywhere, Transient, Category = "DayNight")
	float TimeOfDay = 10.0f;

private:
	void UpdateHeadlights(bool bOn);

	TWeakObjectPtr<ADirectionalLight> Sun;
	TWeakObjectPtr<ADirectionalLight> Moon;
	TMap<TWeakObjectPtr<APawn>, TArray<TWeakObjectPtr<USpotLightComponent>>> Lights;
};
