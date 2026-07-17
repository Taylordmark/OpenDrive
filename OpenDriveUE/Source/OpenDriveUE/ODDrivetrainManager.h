#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODDrivetrainManager.generated.h"

class AWheeledVehiclePawn;
class UChaosWheeledVehicleMovementComponent;

/**
 * Custom drivetrain sim, stage 1 (drive test 2026-07-16): decouples engine
 * RPM from car speed. Chaos's mechanical sim slaves engine RPM to wheel RPM
 * and its auto box rev-matches upward on coast downshifts - so revs rose on
 * lift-off and the limiter-at-30mph burnout state was unreachable.
 *
 * Here the engine is its own integrated state (torque curve, flywheel
 * inertia, rev limiter with fuel cut, engine braking) and gears are decided
 * by a shift controller (coast-hold, kickdown, torque-cut shifts). Torque is
 * delivered per driven wheel via SetDriveTorque with the Chaos mechanical
 * sim disabled; wheel spin vs ground stays Chaos's job (stage 2 replaces the
 * tire friction model itself).
 *
 * Services the player vehicle like the other OD managers - no BP edits.
 * Audio (AODVehicleAudio) reads Rpm/EffectiveThrottle/Load from here, so
 * fuel cuts and shift cuts are audible for free.
 */
UCLASS()
class OPENDRIVEUE_API AODDrivetrainManager : public AActor
{
	GENERATED_BODY()

public:
	AODDrivetrainManager();
	virtual void Tick(float DeltaSeconds) override;

	// --- engine (per-car data assets later; Dragon sports car for now) ---
	UPROPERTY(EditAnywhere, Category = "Engine")
	float IdleRpm = 900.0f;

	UPROPERTY(EditAnywhere, Category = "Engine")
	float MaxRpm = 7000.0f;

	/** Fuel cut above this; bounces ~LimiterCutTime. */
	UPROPERTY(EditAnywhere, Category = "Engine")
	float LimiterRpm = 6850.0f;

	UPROPERTY(EditAnywhere, Category = "Engine")
	float LimiterCutTime = 0.08f;

	UPROPERTY(EditAnywhere, Category = "Engine")
	float PeakTorqueNm = 420.0f;

	UPROPERTY(EditAnywhere, Category = "Engine")
	float PeakTorqueRpm = 4600.0f;

	/** Crank+flywheel inertia (kg m^2); sets free-rev snappiness. */
	UPROPERTY(EditAnywhere, Category = "Engine")
	float EngineInertia = 0.25f;

	/** Engine-brake torque at MaxRpm as a fraction of peak torque. */
	UPROPERTY(EditAnywhere, Category = "Engine")
	float EngineBrakeFrac = 0.22f;

	// --- transmission ---
	UPROPERTY(EditAnywhere, Category = "Transmission")
	float ShiftTime = 0.12f;

	/** Upshift rpm scales with throttle: relaxed part-throttle, redline WOT. */
	UPROPERTY(EditAnywhere, Category = "Transmission")
	float UpshiftRpmAtWot = 6500.0f;

	UPROPERTY(EditAnywhere, Category = "Transmission")
	float UpshiftRpmAtCruise = 2800.0f;

	/** Coasting: hold gear until wheel-coupled rpm sags below this. */
	UPROPERTY(EditAnywhere, Category = "Transmission")
	float CoastDownshiftRpm = 950.0f;

	/** Kickdown: throttle above this may drop gears into the power band. */
	UPROPERTY(EditAnywhere, Category = "Transmission")
	float KickdownThrottle = 0.65f;

	UPROPERTY(EditAnywhere, Category = "Transmission")
	float KickdownTargetRpm = 5800.0f;

	/** Preset torque multiplier (AODTuningManager routes 1.5 presets here). */
	UPROPERTY(EditAnywhere, Category = "Power")
	float TorqueScale = 1.0f;

	// --- telemetry (audio, HUD, traces) ---
	float GetRpm() const { return EngineRpm; }
	int32 GetGear() const { return Gear; }
	/** Throttle after fuel/shift cuts - what the engine actually receives. */
	float GetEffectiveThrottle() const { return EffectiveThrottle; }
	/** Torque delivered / torque available at this rpm. */
	float GetLoad() const { return Load; }
	bool IsBound() const { return BoundCar.IsValid(); }

private:
	void Bind(AWheeledVehiclePawn* Car, UChaosWheeledVehicleMovementComponent* Move);
	float TorqueCurve(float Rpm) const;

	TWeakObjectPtr<AWheeledVehiclePawn> BoundCar;

	// mirrored setup (read from the Chaos transmission config at bind)
	TArray<float> GearRatios;
	float ReverseRatio = 4.0f;
	float FinalRatio = 2.81f;
	float Efficiency = 0.9f;
	TArray<int32> DrivenWheels;

	// sim state
	float EngineRpm = 900.0f;
	int32 Gear = 1;                  // 1..N forward, -1 reverse
	float ShiftTimer = 0.0f;         // >0 while in a torque-cut shift
	float LimiterTimer = 0.0f;       // >0 while fuel is cut
	float ReverseIntent = 0.0f;      // seconds brake held at standstill
	float EffectiveThrottle = 0.0f;
	float Load = 0.0f;
	double LastTraceTime = 0.0;
};
