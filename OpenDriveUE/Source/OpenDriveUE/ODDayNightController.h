#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODDayNightController.generated.h"

class ADirectionalLight;
class APostProcessVolume;
class UMaterialInstanceDynamic;
class UMaterialInterface;
class USpotLightComponent;
class UStaticMeshComponent;

/**
 * Day/night cycle (wishlist #2): drives the atmosphere sun through a full
 * cycle at the area's real latitude / longitude / date (ODSolar.h) and gives
 * every vehicle headlights that switch on around dusk.
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

	/** Where the sun is computed for (ODSolar.h): BeginPlay takes the area's
	 *  "ll" from Masks/worldmap.json; these Gasquet defaults stand in if the
	 *  area isn't there. UtcOffset is standard time; US daylight time adds 1. */
	UPROPERTY(EditAnywhere, Category = "DayNight")
	double Latitude = 41.8475;
	UPROPERTY(EditAnywhere, Category = "DayNight")
	double Longitude = -123.965;
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float UtcOffset = -8.0f;
	UPROPERTY(EditAnywhere, Category = "DayNight")
	bool bDaylightTime = true;

	/** Date when there's no season clock (1 = Jan 1). AODSeasonController's
	 *  MonthOfYear() drives it when present; -oddoy=<1..365> pins it. */
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float DayOfYear = 172.0f;

	/** Headlights turn on below this solar elevation (deg). */
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float HeadlightElevation = 8.0f;

	/** Real light cast by the lamps: two headlight spots off the nose
	 *  (candela at the peak of the IES low-beam profile IES_Headlight -
	 *  pipeline/make_headlight_ies.py, which tunes it), one rear spot (lumens)
	 *  that is red for tail / brake, white reversing. */
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float HeadlightCd = 40000.0f;
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float TailLightLm = 30.0f;
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float BrakeLightLm = 200.0f;
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float ReverseLightLm = 400.0f;

	/** Lamp lens glow, emissive nits (M_CarLights "HeadGlow"/"TailGlow",
	 *  unreal/apply_car_lights_material.py, 2026-10-09). Head and tail glow
	 *  with the headlights; the brake pedal (and reverse gear, on the reverse
	 *  lenses) glow at BrakelampGlow by day, 5x TaillampGlow at night. */
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float HeadlampGlow = 300.0f;
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float TaillampGlow = 25.0f;
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float BrakelampGlow = 400.0f;

	/** Visible headlight beams in any weather (2026-10-09, Taylor: "a
	 *  transparent beam of light ... dimmer than where the lights actually
	 *  land ... it should work in all scenarios"): M_HeadlightBeam
	 *  (unreal/apply_headlight_beam_material.py) marches the two head spots
	 *  through this much air haze (scattering, 1/m) on a sphere off the nose.
	 *  pipeline/check_headlight_beam.py tunes it. Weather fog adds on top. */
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float BeamHaze = 0.0003f;   // 2026-10-09: was 0.01 at ~3400 cd - veil hid the road ("hard to see through")
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float BeamRadius = 4500.0f;   // reaches ~87 m, the spots' 90 m attenuation

	/** Moon light intensity (lux) once the sun drops below the horizon - a
	 * dim night key light so Lumen has something to expose against instead
	 * of clipping to black (2026-08-25, Taylor: exposure-clipping warning /
	 * black screen on the far side of the terrain at night). Deliberately
	 * not a sun-intensity floor - a separate, dimmer, opposite-angle light
	 * reads as "moonlit" instead of "sun that never fully sets". */
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float MoonIntensity = 0.15f;   // 2026-10-09: was 0.3 - night is headlights + faint sky light now

	/** Night exposure floor (EV100, 2026-10-09, Taylor: "dusk was not dark
	 *  enough ... make the lights of the car be pretty much the only source
	 *  of light"): auto-exposure may not brighten past this at night, so
	 *  moonlit ground stays a few % of mid grey while the headlit road reads
	 *  bright. Blends in with NightFactor() over a Priority-101 volume on
	 *  top of AODAtmosphereController's -4 EV grade. */
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float NightMinEV = 1.0f;

	/** Peak star emissive scale (M_NightSky "Brightness"). */
	UPROPERTY(EditAnywhere, Category = "DayNight")
	float StarBrightness = 3.0f;

	/** 0 by day .. 1 at night: smoothstep from sun elevation +2 deg to -6 deg
	 *  (civil twilight ends at -6). Mirrored in
	 *  pipeline/check_night_exposure.py. */
	static float NightFactor(float ElevationDeg)
	{
		return FMath::SmoothStep(0.0f, 1.0f, (2.0f - ElevationDeg) / 8.0f);
	}

	UPROPERTY(VisibleAnywhere, Transient, Category = "DayNight")
	float TimeOfDay = 10.0f;

	/** Day of week the session starts on, 0 = Monday (weekday, for 3c
	 *  traffic). -odday=<0..6> overrides. */
	UPROPERTY(EditAnywhere, Category = "DayNight")
	int32 StartDayOfWeek = 0;

	/** Midnights passed since BeginPlay. */
	int32 DayCount = 0;

	/** 0 = Monday .. 6 = Sunday (build_traffic_demand DOW_FACTOR order). */
	int32 GetDayOfWeek() const { return ((StartDayOfWeek + DayCount) % 7 + 7) % 7; }

	/** Last headlight state Tick applied (sun below HeadlightElevation) - 
	 *  AODFurnitureManager lights its street lamps with it. */
	bool bHeadlightsOn = false;

	/** NightFactor() as of the last Tick (0 before the first). */
	float GetNight() const { return FMath::Max(LastNight, 0.0f); }

private:
	void UpdateHeadlights(bool bOn);

	TWeakObjectPtr<ADirectionalLight> Sun;
	TWeakObjectPtr<ADirectionalLight> Moon;
	TWeakObjectPtr<class AODSeasonController> Season;
	bool bPinnedDay = false;   // -oddoy= given: ignore the season clock
	TWeakObjectPtr<APostProcessVolume> NightGrade;
	UPROPERTY()
	TObjectPtr<UStaticMeshComponent> StarDome;
	UPROPERTY()
	TObjectPtr<UMaterialInstanceDynamic> StarMID;
	UPROPERTY()
	TObjectPtr<UMaterialInterface> BeamMat;
	UPROPERTY()
	TObjectPtr<class UTextureLightProfile> HeadIES;
	float LastNight = -1.0f;
	int32 LastLampState = -1;   // player car brake|reverse, logged on change
	TMap<TWeakObjectPtr<APawn>, TArray<TWeakObjectPtr<USpotLightComponent>>> Lights;
};
