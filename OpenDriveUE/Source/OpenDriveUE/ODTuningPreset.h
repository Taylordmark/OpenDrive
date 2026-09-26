#pragma once

#include "CoreMinimal.h"
#include "Engine/DataAsset.h"
#include "ODVehicleMovement.h"
#include "ODTuningPreset.generated.h"

/**
 * Tuning archetype (wishlist #8, Arc 1.5): a named set of deltas/scales
 * applied over the vehicle's baseline setup. Data assets referenced by ID =
 * replication-friendly later; the garage UI (Arc 4.3) will list these same
 * assets. Picked in the garage only (Tuning row), never while driving.
 */
UCLASS(BlueprintType)
class OPENDRIVEUE_API UODTuningPreset : public UPrimaryDataAsset
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere, Category = "Preset")
	FString DisplayName = TEXT("Street");

	/** Suspension damping ratio (absolute; 0.9 = the Arc 1.2 street fix). */
	UPROPERTY(EditAnywhere, Category = "Suspension", meta = (ClampMin = 0.1, ClampMax = 1.5))
	float DampingRatio = 0.9f;

	/** Spring rate scale vs baseline (rally soft < 1 < drag stiff). */
	UPROPERTY(EditAnywhere, Category = "Suspension", meta = (ClampMin = 0.3, ClampMax = 3))
	float SpringRateScale = 1.0f;

	/** Ride height delta vs baseline, cm (+ = taller: added to raise & drop). */
	UPROPERTY(EditAnywhere, Category = "Suspension", meta = (ClampMin = -6, ClampMax = 12))
	float RideHeightDelta = 0.0f;

	/** Tire friction scale vs baseline, per axle (drift cuts the rear). */
	UPROPERTY(EditAnywhere, Category = "Grip", meta = (ClampMin = 0.4, ClampMax = 1.5))
	float FrontFrictionScale = 1.0f;

	UPROPERTY(EditAnywhere, Category = "Grip", meta = (ClampMin = 0.4, ClampMax = 1.5))
	float RearFrictionScale = 1.0f;

	/** Engine torque scale vs baseline (drag launches hard). */
	UPROPERTY(EditAnywhere, Category = "Power", meta = (ClampMin = 0.5, ClampMax = 2))
	float TorqueScale = 1.0f;

	/** Aero downforce coefficient (absolute; street = 1.1 from Arc 1.2). */
	UPROPERTY(EditAnywhere, Category = "Aero", meta = (ClampMin = 0, ClampMax = 3))
	float Downforce = 1.1f;

	/** Center-of-mass override height, cm (absolute; lower = more planted). */
	UPROPERTY(EditAnywhere, Category = "Mass", meta = (ClampMin = 20, ClampMax = 70))
	float CenterOfMassZ = 42.0f;

	// ---- surface / tires (see UODVehicleMovement's "Setup" fields) ----
	UPROPERTY(EditAnywhere, Category = "Grip", meta = (ClampMin = 0.3, ClampMax = 1.2))
	float OnRoadGrip = 1.0f;

	UPROPERTY(EditAnywhere, Category = "Grip", meta = (ClampMin = 0.3, ClampMax = 1.2))
	float OffRoadGrip = 0.6f;

	UPROPERTY(EditAnywhere, Category = "Grip", meta = (ClampMin = 0, ClampMax = 10))
	float SlipAngleAddDeg = 0.0f;

	UPROPERTY(EditAnywhere, Category = "Grip", meta = (ClampMin = 0, ClampMax = 1))
	float SlideFracMin = 0.0f;

	// ---- assists: the setup's default mode + how much slide Low allows ----
	UPROPERTY(EditAnywhere, Category = "Assists")
	EODTractionMode DefaultTractionMode = EODTractionMode::High;

	UPROPERTY(EditAnywhere, Category = "Assists", meta = (ClampMin = 0, ClampMax = 45))
	float EscSlideAllowLowDeg = 10.0f;

	// ---- steering / brakes / speed ----
	UPROPERTY(EditAnywhere, Category = "Steering", meta = (ClampMin = 0.5, ClampMax = 1.5))
	float SteerLockScale = 1.0f;

	UPROPERTY(EditAnywhere, Category = "Brakes", meta = (ClampMin = 0.3, ClampMax = 1.5))
	float RearBrakeScale = 1.0f;

	UPROPERTY(EditAnywhere, Category = "Brakes", meta = (ClampMin = 0.3, ClampMax = 2))
	float HandbrakeScale = 1.0f;

	/** 0 = no cap. */
	UPROPERTY(EditAnywhere, Category = "Power", meta = (ClampMin = 0, ClampMax = 400))
	float TopSpeedCapKph = 0.0f;

	// ---- appearance (no new art: wheel mesh scale/tilt + ride height) ----
	UPROPERTY(EditAnywhere, Category = "Look", meta = (ClampMin = 0.85, ClampMax = 1.25))
	float TireDiameterScale = 1.0f;

	UPROPERTY(EditAnywhere, Category = "Look", meta = (ClampMin = 0, ClampMax = 12))
	float CamberDeg = 0.0f;

	UPROPERTY(EditAnywhere, Category = "Look", meta = (ClampMin = 0.6, ClampMax = 1.6))
	float FrontTireWidthScale = 1.0f;

	UPROPERTY(EditAnywhere, Category = "Look", meta = (ClampMin = 0.6, ClampMax = 1.6))
	float RearTireWidthScale = 1.0f;
};
