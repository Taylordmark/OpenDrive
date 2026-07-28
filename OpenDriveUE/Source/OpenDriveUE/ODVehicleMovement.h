#pragma once

#include "Components/ActorComponent.h"
#include "CoreMinimal.h"
#include "ODVehicleMovement.generated.h"

class UPhysicalMaterial;
class UPrimitiveComponent;
class UODVehicleDef;

/** Driver-assist level (pause menu; presets pick defaults in stage 3). */
UENUM()
enum class EODTractionMode : uint8
{
	High,   // engine torque management - wheels never break loose
	Low,    // slides allowed; excess yaw damped with single-wheel brakes
	Off
};

/** Which axle(s) the engine drives. AWD needs no drivetrain-math change - the
 *  torque split and reflected-inertia division already divide by DrivenCount;
 *  this only decides which wheels get bDriven set in SetupWheels(). */
UENUM()
enum class EODDriveLayout : uint8
{
	RWD,   // rear only (the placeholder coupe)
	FWD,   // front only
	AWD    // all four, torque split evenly per wheel
};

/** Per-wheel config + sim state for UODVehicleMovement. */
USTRUCT()
struct FODWheel
{
	GENERATED_BODY()

	// --- config (filled by UODVehicleMovement's constructor) ---
	FVector Attach = FVector::ZeroVector;   // body-local strut top, cm
	float Radius = 34.0f;                   // cm
	bool bSteer = false;
	bool bDriven = false;
	bool bHandbrake = false;
	float MaxBrakeNm = 4500.0f;
	float MaxHandbrakeNm = 0.0f;

	// --- state ---
	bool bInContact = false;
	FVector ContactPoint = FVector::ZeroVector;   // world
	TWeakObjectPtr<UPhysicalMaterial> Surface;
	float SpringLen = 20.0f;      // current strut length, cm
	float FzSm = 0.0f;            // smoothed vertical load, kg cm/s^2
	float Omega = 0.0f;           // wheel spin, rad/s (+ = forward)
	float SteerDeg = 0.0f;
	float DriveNm = 0.0f;
	float BrakeNm = 0.0f;
};

/**
 * The whole car, in one readable component (Chaos rip-out, 2026-07-18).
 * Chaos vehicles are gone: the 5.8 constraint-based suspension silently
 * pinned our bone-less static-mesh pawn to the world (susp offset frozen at
 * MaxRaise, 4.8 kN had zero effect), and every stage of the drivetrain
 * rework had already replaced a Chaos subsystem anyway. Now the full chain
 * is ours:
 *
 *   raycast suspension (spring + damper, real per-corner loads)
 *     -> engine (torque curve, flywheel inertia, soft/hard rev limiter)
 *     -> auto gearbox (throttle-scaled upshifts, coast-hold, kickdown)
 *     -> tire model (slip ratio + slip angle on one friction circle,
 *        surface-scaled grip, per-wheel spin integration, brakes/handbrake)
 *     -> aero (quadratic drag + downforce)
 *
 * Forces are applied to the pawn's simulating static-mesh Body via
 * AddForceAtLocation from the game-thread tick - the same path the stage-2
 * tire model already drove the template car with at 0.4 g.
 *
 * Units: UE distances are cm, forces kg cm/s^2 (1 N = 100 units). Torques
 * stay in Nm and are converted at the contact patch.
 */
UCLASS(ClassGroup = (Custom), meta = (BlueprintSpawnableComponent))
class OPENDRIVEUE_API UODVehicleMovement : public UActorComponent
{
	GENERATED_BODY()

public:
	UODVehicleMovement();
	virtual void TickComponent(float DeltaSeconds, ELevelTick TickType,
		FActorComponentTickFunction* ThisTickFunction) override;

	// --- inputs (pawn writes every tick; already smoothed there) ---
	void SetThrottleInput(float V) { ThrottleIn = FMath::Clamp(V, 0.0f, 1.0f); }
	void SetBrakeInput(float V) { BrakeIn = FMath::Clamp(V, 0.0f, 1.0f); }
	void SetSteeringInput(float V) { SteerIn = FMath::Clamp(V, -1.0f, 1.0f); }
	void SetHandbrakeInput(bool bOn) { bHandbrakeIn = bOn; }
	float GetThrottleInput() const { return ThrottleIn; }
	float GetSteeringInput() const { return SteerIn; }

	// --- engine (pause menu swaps these live) ---
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

	/** Clutch-bite time from a standing start: flare up, then the bite
	 *  drags rpm down to meet the (still slow) wheels before it climbs with
	 *  road speed. Without this the engine starts already in the powerband,
	 *  which can't stall on an uphill launch. */
	UPROPERTY(EditAnywhere, Category = "Engine")
	float LaunchClutchTime = 0.45f;

	/** Limiter bounce rate - the brap-brap repetition (audio-facing cut). */
	UPROPERTY(EditAnywhere, Category = "Engine")
	float LimiterBounceHz = 12.0f;

	/** Fraction of each bounce cycle spent with ignition cut. */
	UPROPERTY(EditAnywhere, Category = "Engine")
	float LimiterBounceDuty = 0.45f;

	// --- transmission ---
	UPROPERTY(EditAnywhere, Category = "Transmission")
	TArray<float> GearRatios = {4.25f, 2.52f, 1.66f, 1.22f, 1.0f};

	UPROPERTY(EditAnywhere, Category = "Transmission")
	float ReverseRatio = 4.04f;

	UPROPERTY(EditAnywhere, Category = "Transmission")
	float FinalRatio = 2.81f;

	UPROPERTY(EditAnywhere, Category = "Transmission")
	float Efficiency = 0.9f;

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

	/** Preset torque multiplier (tuning presets route here). */
	UPROPERTY(EditAnywhere, Category = "Power")
	float TorqueScale = 1.0f;

	// --- assists ---
	UPROPERTY(EditAnywhere, Category = "Assists")
	EODTractionMode TractionMode = EODTractionMode::High;

	/** TC High: hold driven-wheel slip ratio at/below this (peak is 0.14). */
	UPROPERTY(EditAnywhere, Category = "Assists")
	float TcSlipTarget = 0.12f;

	/** ESC Low: yaw-rate excess deadband before the brakes step in (rad/s). */
	UPROPERTY(EditAnywhere, Category = "Assists")
	float EscYawDeadband = 0.4f;

	/** ABS: release brake torque past this lock-slip; off = lockups live. */
	UPROPERTY(EditAnywhere, Category = "Assists")
	bool bAbsEnabled = true;

	UPROPERTY(EditAnywhere, Category = "Assists")
	float AbsSlipTarget = 0.13f;

	static const TCHAR* TractionModeName(EODTractionMode M)
	{
		return M == EODTractionMode::High ? TEXT("High")
			: M == EODTractionMode::Low ? TEXT("Low") : TEXT("Off");
	}

	// --- suspension ---
	/** Strut rest length, cm; static sag is CornerWeight/SpringRate. */
	UPROPERTY(EditAnywhere, Category = "Suspension")
	float RestLengthCm = 20.0f;

	/** Spring rate, force units per cm (61k ~= 6 cm sag at 375 kg/corner). */
	UPROPERTY(EditAnywhere, Category = "Suspension")
	float SpringRate = 61000.0f;

	UPROPERTY(EditAnywhere, Category = "Suspension")
	float SpringRateScale = 1.0f;

	UPROPERTY(EditAnywhere, Category = "Suspension")
	float DampingRatio = 0.9f;

	/** Ride height delta, cm (presets; + = taller). */
	UPROPERTY(EditAnywhere, Category = "Suspension")
	float RideHeightDelta = 0.0f;

	/** Local-space COM height the body is nudged to (cm; axle plane = -14). */
	UPROPERTY(EditAnywhere, Category = "Suspension")
	float ComLocalZ = 10.0f;

	// --- steering ---
	UPROPERTY(EditAnywhere, Category = "Steering")
	float MaxSteerAngleDeg = 38.0f;

	// --- tires ---
	/** Peak grip coefficient on dry asphalt (road tire ~1.0). */
	UPROPERTY(EditAnywhere, Category = "Tires")
	float MuPeak = 1.05f;

	/** Sliding grip as a fraction of peak - how harsh the breakaway is. */
	UPROPERTY(EditAnywhere, Category = "Tires")
	float SlideFrac = 0.72f;

	/** Slip ratio at which longitudinal grip peaks. */
	UPROPERTY(EditAnywhere, Category = "Tires")
	float PeakSlipRatio = 0.14f;

	/** Slip angle (degrees) at which lateral grip peaks. */
	UPROPERTY(EditAnywhere, Category = "Tires")
	float PeakSlipAngleDeg = 9.0f;

	/** Wheel + tire rotational inertia, kg m^2. */
	UPROPERTY(EditAnywhere, Category = "Tires")
	float WheelInertia = 1.5f;

	UPROPERTY(EditAnywhere, Category = "Tires")
	float RollingResistance = 0.015f;

	/** Per-axle grip scales (tuning presets route here). */
	UPROPERTY(EditAnywhere, Category = "Tires")
	float FrontGripScale = 1.0f;

	UPROPERTY(EditAnywhere, Category = "Tires")
	float RearGripScale = 1.0f;

	// --- aero ---
	/** Drag, N per (m/s)^2 (CdA * rho/2; 0.45 ~= 186 mph drag limit). */
	UPROPERTY(EditAnywhere, Category = "Aero")
	float DragCoeff = 0.45f;

	/** Downforce, N per (m/s)^2 of forward speed. */
	UPROPERTY(EditAnywhere, Category = "Aero")
	float DownforceCoeff = 1.1f;

	// --- telemetry (audio, HUD, ghost, pause menu) ---
	float GetRpm() const { return EngineRpm; }
	int32 GetGear() const { return Gear; }
	/** Throttle after fuel/shift cuts - what the engine actually receives. */
	float GetEffectiveThrottle() const { return EffectiveThrottle; }
	/** Torque delivered / torque available at this rpm. */
	float GetLoad() const { return Load; }
	/** Raw ignition-cut flag (limiter bounce, shift cut) - NOT smoothed. */
	bool IsIgnitionCut() const { return bIgnitionCut; }
	/** Signed forward speed, cm/s. */
	float GetForwardSpeed() const { return FwdSpeedCmS; }

	int32 NumWheels() const { return Wheels.Num(); }
	bool IsWheelInContact(int32 i) const { return Wheels[i].bInContact; }
	const UPhysicalMaterial* GetContactSurface(int32 i) const
	{
		return Wheels[i].Surface.Get();
	}
	/** Axle center in body space (suspension travel included) - visuals. */
	FVector GetWheelLocalPos(int32 i) const
	{
		return Wheels[i].Attach - FVector(0, 0, Wheels[i].SpringLen);
	}
	float GetWheelSteerDeg(int32 i) const { return Wheels[i].SteerDeg; }
	float GetWheelOmega(int32 i) const { return Wheels[i].Omega; }

	/** Re-derive mass/COM (after mesh swap or ComLocalZ preset change). */
	void MarkBodyDirty() { bBodyInit = false; }

	/** Rebuild the wheel array + baseline stat fields from a car definition
	 *  (used on spawn and on a live vehicle swap). Tuning presets still scale
	 *  their subset on top of whatever this establishes - call the active
	 *  preset's Apply again after this if one is selected. */
	void ApplyVehicleDef(const UODVehicleDef& Def);

	/** Live drivetrain change (garage menu) without rebuilding the wheel
	 *  array: layout only ever decides which wheels carry bDriven, and
	 *  DrivenCount is recomputed from those flags every tick, so flipping them
	 *  is the whole change. Keeps wheel geometry + sim state intact. */
	void SetDriveLayout(EODDriveLayout Layout);

private:
	// (re)build the 4-wheel array for a wheelbase/track/radius + drive layout;
	// called from the constructor (the placeholder-coupe defaults) and from
	// ApplyVehicleDef (per-car geometry). Keeps FL,FR,RL,RR order.
	void SetupWheels(float FrontAxleX, float RearAxleX, float HalfTrackY,
		float WheelRadiusCm, EODDriveLayout Layout,
		float FrontBrakeNm, float RearBrakeNm, float HandbrakeNm);

	UPrimitiveComponent* GetBody() const;
	void InitBody(UPrimitiveComponent* Body);
	void TickSuspension(UPrimitiveComponent* Body, float Dt);
	void TickDrivetrain(UPrimitiveComponent* Body, float Dt);
	void TickTires(UPrimitiveComponent* Body, float Dt);
	void TickAero(UPrimitiveComponent* Body);
	float TorqueCurve(float Rpm) const;
	float SurfaceGrip(const UPhysicalMaterial* Mat) const;
	/** Highest gear that keeps rpm in the band at this road speed. */
	int32 GearForSpeed(float AbsSpeedCmS) const;

	UPROPERTY()
	TArray<FODWheel> Wheels;

	// inputs
	float ThrottleIn = 0.0f, BrakeIn = 0.0f, SteerIn = 0.0f;
	bool bHandbrakeIn = false;

	// engine/gearbox state
	float EngineRpm = 900.0f;
	int32 Gear = 1;                  // 1..N forward, -1 reverse
	float ShiftTimer = 0.0f;         // >0 while in a torque-cut shift
	float LimiterTimer = 0.0f;       // >0 while fuel is cut
	float ReverseIntent = 0.0f;      // seconds brake held at standstill
	float LastShiftTime = -10.0f;    // kickdown cooldown anchor
	float EffectiveThrottle = 0.0f;
	float Load = 0.0f;
	float FwdSpeedCmS = 0.0f;
	float CurTotalRatio = 0.0f;      // gear x final, this tick
	bool bDrivelineCoupled = false;  // in gear, not mid-shift
	float ChatterTime = -1.0f;       // <0 = off the limiter
	bool bIgnitionCut = false;
	float CruiseTimer = 0.0f;        // seconds of settled low throttle
	bool bRelaxedShift = true;       // false = attack mode: gears HOLD
	float LaunchTimer = 0.0f;        // >0 while the clutch is slipping in

	// assists state
	float TcScale = 1.0f;            // High: drive-torque authority [0.15,1]
	float EscBrakeNm = 0.0f;         // Low: smoothed corrective brake
	int32 EscWheel = -1;             // Low: which front wheel gets it

	bool bBodyInit = false;
	double LastTraceTime = 0.0;

	// trace fields (-odtrace)
	float TraceSlip = 0.0f;
	float TraceAlphaF = 0.0f;
	float TraceFyF = 0.0f, TraceFyR = 0.0f, TraceFxR = 0.0f;
	float TraceCoupled = 0.0f;
	bool bTraceCut = false;
	bool bAbsActiveThisTick = false;
};
