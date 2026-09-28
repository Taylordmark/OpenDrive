#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "ODVehicleMovement.h"   // EODDriveLayout
#include "ODVehicleDef.generated.h"

class UStaticMesh;

/**
 * One drivable car (garage groundwork, 2026-07-24). Sits a layer ABOVE
 * UODTuningPreset: a preset is a *setup* scaled over a car; a def is the car
 * itself - its meshes, wheel geometry, drive layout, and full baseline
 * physics envelope (the knobs presets don't reach: drag, gearing, rpm, mass,
 * drive layout). AODVehiclePawn applies one on spawn and on a live V-swap;
 * an active tuning preset re-applies its scales on top afterward.
 *
 * Every default here is the placeholder coupe's current value, so a def left
 * at defaults reproduces today's car exactly (and the fields double as the
 * safe fallback if a mesh soft-ref fails to load).
 */
UCLASS(BlueprintType)
class OPENDRIVEUE_API UODVehicleDef : public UPrimaryDataAsset
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere, Category = "Identity")
	FString DisplayName = TEXT("Coupe");

	// ---- visuals ----
	/** Chassis static mesh (pipeline/make_car_mesh.py output, imported). */
	UPROPERTY(EditAnywhere, Category = "Visuals")
	TSoftObjectPtr<UStaticMesh> ChassisMesh;

	/** Wheel static mesh; authored at WheelRadiusCm so no runtime scaling. */
	UPROPERTY(EditAnywhere, Category = "Visuals")
	TSoftObjectPtr<UStaticMesh> WheelMesh;

	// ---- geometry (must match the mesh's axle contract) ----
	UPROPERTY(EditAnywhere, Category = "Geometry")
	float FrontAxleXCm = 132.0f;

	UPROPERTY(EditAnywhere, Category = "Geometry")
	float RearAxleXCm = -128.0f;

	UPROPERTY(EditAnywhere, Category = "Geometry")
	float HalfTrackYCm = 84.0f;

	UPROPERTY(EditAnywhere, Category = "Geometry")
	float WheelRadiusCm = 34.0f;

	UPROPERTY(EditAnywhere, Category = "Geometry")
	EODDriveLayout DriveLayout = EODDriveLayout::RWD;

	UPROPERTY(EditAnywhere, Category = "Mass")
	float MassKg = 1500.0f;

	/** Static fraction of weight on the front axle (2026-09-25): InitBody
	 *  places the COM this far from the rear axle toward the front. */
	UPROPERTY(EditAnywhere, Category = "Mass", meta = (ClampMin = 0.3, ClampMax = 0.7))
	float FrontWeightFrac = 0.53f;

	/** Yaw dynamic index k (2026-09-25): Iz = k * Mass * a * b, a/b = COM to
	 *  front/rear axle. ~0.85 mid-engine, ~1.0 typical, ~1.1 heavy ends. */
	UPROPERTY(EditAnywhere, Category = "Mass", meta = (ClampMin = 0.6, ClampMax = 1.4))
	float YawDynamicIndex = 1.0f;

	// ---- engine ----
	UPROPERTY(EditAnywhere, Category = "Engine")
	float MaxRpm = 7000.0f;

	UPROPERTY(EditAnywhere, Category = "Engine")
	float LimiterRpm = 6850.0f;

	UPROPERTY(EditAnywhere, Category = "Engine")
	float PeakTorqueNm = 420.0f;

	UPROPERTY(EditAnywhere, Category = "Engine")
	float PeakTorqueRpm = 4600.0f;

	UPROPERTY(EditAnywhere, Category = "Engine")
	float EngineInertia = 0.25f;

	UPROPERTY(EditAnywhere, Category = "Engine")
	float EngineBrakeFrac = 0.22f;

	// ---- transmission ----
	UPROPERTY(EditAnywhere, Category = "Transmission")
	TArray<float> GearRatios = {4.25f, 2.52f, 1.66f, 1.22f, 1.0f};

	UPROPERTY(EditAnywhere, Category = "Transmission")
	float FinalRatio = 2.81f;

	// ---- tires ----
	UPROPERTY(EditAnywhere, Category = "Tires")
	float MuPeak = 1.05f;

	UPROPERTY(EditAnywhere, Category = "Tires")
	float SlideFrac = 0.88f;

	UPROPERTY(EditAnywhere, Category = "Tires")
	float PeakSlipRatio = 0.14f;

	UPROPERTY(EditAnywhere, Category = "Tires")
	float PeakSlipAngleDeg = 13.0f;

	UPROPERTY(EditAnywhere, Category = "Tires")
	float WheelInertia = 1.5f;

	// ---- suspension ----
	UPROPERTY(EditAnywhere, Category = "Suspension")
	float SpringRate = 61000.0f;

	UPROPERTY(EditAnywhere, Category = "Suspension")
	float DampingRatio = 0.9f;

	UPROPERTY(EditAnywhere, Category = "Suspension")
	float RestLengthCm = 20.0f;

	UPROPERTY(EditAnywhere, Category = "Suspension")
	float ComLocalZ = 10.0f;

	// ---- aero ----
	UPROPERTY(EditAnywhere, Category = "Aero")
	float DragCoeff = 0.45f;

	/** N per (m/s)^2. Tuning presets scale this by Preset->Downforce / 1.1. */
	UPROPERTY(EditAnywhere, Category = "Aero")
	float DownforceCoeff = 1.1f;

	// ---- steering ----
	UPROPERTY(EditAnywhere, Category = "Steering")
	float MaxSteerAngleDeg = 38.0f;

	// ---- brakes (Nm) ----
	UPROPERTY(EditAnywhere, Category = "Brakes")
	float FrontBrakeNm = 4500.0f;

	UPROPERTY(EditAnywhere, Category = "Brakes")
	float RearBrakeNm = 3000.0f;

	UPROPERTY(EditAnywhere, Category = "Brakes")
	float HandbrakeNm = 6000.0f;
};
