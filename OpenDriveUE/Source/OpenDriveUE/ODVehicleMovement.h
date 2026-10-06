#pragma once

#include "Components/ActorComponent.h"
#include "CoreMinimal.h"
#include "ODVehicleMovement.generated.h"

class UPhysicalMaterial;
class UPrimitiveComponent;
class UODVehicleDef;
class AODWeatherController;
class AODSeasonController;

/** Driver-assist level (pause menu; presets pick defaults in stage 3). */
UENUM(BlueprintType)   // BlueprintType: apply_tuning_presets.py sets it
enum class EODTractionMode : uint8
{
	High,   // torque management + ESC under the grip peak - no slides
	Low,    // ESC only, lets the rear slide ~EscSlideAllowLowDeg
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
	float Radius = 34.0f;                   // cm, BaseRadius * TireDiameterScale
	float BaseRadius = 34.0f;               // cm, the car's own (def) radius
	bool bSteer = false;
	bool bDriven = false;
	bool bHandbrake = false;
	float MaxBrakeNm = 4500.0f;
	float MaxHandbrakeNm = 0.0f;

	// --- state ---
	bool bInContact = false;
	FVector ContactPoint = FVector::ZeroVector;   // world
	TWeakObjectPtr<UPhysicalMaterial> Surface;
	TWeakObjectPtr<UPrimitiveComponent> Ground;   // hit component (road chunk snow depth)
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
	float GetBrakeInput() const { return BrakeIn; }
	float GetSteeringInput() const { return SteerIn; }

	// --- spawn parking hold (2026-09-02) ---
	// Full-brake hold engaged by the pawn when the ground-snap releases
	// physics, cleared automatically on the first pedal input. Without it a
	// car spawned on any real grade rolls away with no input (the -odpick
	// Frisco spawn coasted ~19m down a 0.4% grade). Lives here rather than
	// as a pawn-side SetHandbrakeInput(true) because the gamepad/wheel input
	// paths rewrite the handbrake state every tick.
	void EngageParkHold() { bParkHold = true; }
	bool IsParkHeld() const { return bParkHold; }

	// --- engine (pause menu swaps these live) ---
	// tried 1400 (2026-08-25) to further flatten launch feel, but it also
	// raises the "CoupledRpm > IdleRpm" engine-braking gate in
	// TickDrivetrain, which made the car stop applying engine drag at low
	// speed and got stuck creeping ~1.8mph with no way to brake below the
	// bStopped threshold to even request reverse. Reverted to 900 - the
	// TorqueCurve floor bump (0.55->0.85) below is the one that stays.
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
	float LaunchClutchTime = 0.15f;

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

	/** TC High: hold driven-wheel slip ratio at/below this (peak is 0.14).
	 *  Raised from 0.12 (2026-08-26, Taylor: "forward start feels terrible,
	 *  reverse feels fine") - at 0.12 the target sat just under the tire's
	 *  own peak-grip slip ratio, so TC was clamping torque through the
	 *  tire's normal working range on every WOT launch, not just genuine
	 *  runaway wheelspin. Reverse launches never hit TC at all (see
	 *  TickDrivetrain's WheelTq>0 gate), which is why only forward felt
	 *  choked. */
	UPROPERTY(EditAnywhere, Category = "Assists")
	float TcSlipTarget = 0.22f;

	/** ESC (High + Low): how far past the tire's peak slip angle the REAR
	 *  axle may slide before the brakes step in, degrees. Measured relative
	 *  to PeakSlipAngleDeg so it means "degrees of actual slide" for every
	 *  car. High sits just under the peak (grip never breaks); Low allows a
	 *  sport-mode-style ~10 deg of drift. Replaces the old yaw-rate ESC
	 *  (2026-09-25, Taylor: rear swings out braking / off the brake into a
	 *  turn), whose 1 g yaw reference sat far below the 4x-grip car's real
	 *  cornering limit and was Low-only - High had no yaw control at all. */
	UPROPERTY(EditAnywhere, Category = "Assists")
	float EscSlideAllowHighDeg = -2.0f;

	UPROPERTY(EditAnywhere, Category = "Assists")
	float EscSlideAllowLowDeg = 10.0f;

	/** Degrees past the allowance at which intervention is full. */
	UPROPERTY(EditAnywhere, Category = "Assists")
	float EscRampDeg = 4.0f;

	/** Slip-angle growth lookahead, s: catches a snap before it happens
	 *  (the beta/beta-dot phase-plane idea production ESC uses). 0.2 not
	 *  0.12 (2026-09-25): short-wheelbase cars (JDM/Rally/Coupe) on the lifted
	 *  Offroad setup rotate faster than 0.12 s could see coming; 0.2 holds
	 *  every car x setup in pipeline/checks/esc_sim.py with no clean-corner hits. */
	UPROPERTY(EditAnywhere, Category = "Assists")
	float EscLookahead = 0.2f;

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

	/** Local-space COM height the body is nudged to (cm; axle plane = -14).
	 *  Dropped 10 -> 4 (2026-08-27): the car rolled onto its side in fast
	 *  corners; most of the fix is speed-sensitive steering in the pawn, but
	 *  a lower COM buys roll margin for every input device, not just the
	 *  keyboard. Real cars sit near or below axle height anyway. */
	UPROPERTY(EditAnywhere, Category = "Suspension")
	float ComLocalZ = 4.0f;

	/** Static front-axle weight fraction; sets COM X between the axles
	 *  (2026-09-25, per car via UODVehicleDef). */
	UPROPERTY(EditAnywhere, Category = "Mass", meta = (ClampMin = 0.3, ClampMax = 0.7))
	float FrontWeightFrac = 0.53f;

	/** Yaw (and pitch) inertia as Iz = k * Mass * a * b (2026-09-25). */
	UPROPERTY(EditAnywhere, Category = "Mass", meta = (ClampMin = 0.6, ClampMax = 1.4))
	float YawDynamicIndex = 1.0f;

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

	/** Lateral tire force is applied this many cm ABOVE the contact patch
	 *  (toward the roll center) instead of at ground level. 2026-08-27:
	 *  cornering at speed built enough roll moment that cresting a bump
	 *  mid-turn launched the car into airborne barrel rolls (live telemetry:
	 *  tumbling at 33 mph, brake untouched). Ground-level Fy times a ~45 cm
	 *  COM height is a huge flip lever; real cars fight it with anti-roll
	 *  bars this model doesn't have, so raising the Fy application point is
	 *  the standard sim shortcut for the same effect. Yaw response is
	 *  unaffected (same force, same XY point). */
	UPROPERTY(EditAnywhere, Category = "Tires")
	float LateralForceLiftCm = 35.0f;

	/** Airborne attitude damping, 1/s (roll X / pitch Y of local angular
	 *  velocity decay toward zero while NO wheel has contact; yaw is left
	 *  alone). Kills the crest-launch tumble: whatever roll rate the corner
	 *  had built bleeds off in flight so the car lands flat-ish instead of
	 *  on its roof. 0 disables. */
	UPROPERTY(EditAnywhere, Category = "Tires")
	float AirborneRollDampPerSec = 2.5f;

	UPROPERTY(EditAnywhere, Category = "Tires")
	float AirbornePitchDampPerSec = 1.0f;

	/** Tire load sensitivity: grip per unit load falls as load rises,
	 *  mu *= 1 - k*(Fz/Fz_static - 1) (Pacejka-style; real tires ~0.1-0.3).
	 *  0 = grip exactly proportional to load, which let brake/lift weight
	 *  transfer strip the rear far harder than on a real car - the root of
	 *  "rear swings out braking / off the brake into a turn" (2026-09-25,
	 *  planar sim: 0.25 took a keyboard trail-brake spin from 31 to 14 deg
	 *  past peak with assists OFF). */
	UPROPERTY(EditAnywhere, Category = "Tires")
	float TireLoadSensitivity = 0.25f;

	/** Wheel + tire rotational inertia, kg m^2. */
	UPROPERTY(EditAnywhere, Category = "Tires")
	float WheelInertia = 1.5f;

	UPROPERTY(EditAnywhere, Category = "Tires")
	float RollingResistance = 0.015f;

	/** Per-axle grip scales (tuning presets route here). */
	UPROPERTY(EditAnywhere, Category = "Tires")
	float FrontGripScale = 4.0f;

	UPROPERTY(EditAnywhere, Category = "Tires")
	float RearGripScale = 4.0f;

	// --- aero ---
	/** Drag, N per (m/s)^2 (CdA * rho/2; 0.45 ~= 186 mph drag limit). */
	UPROPERTY(EditAnywhere, Category = "Aero")
	float DragCoeff = 0.45f;

	/** Downforce, N per (m/s)^2 of forward speed. */
	UPROPERTY(EditAnywhere, Category = "Aero")
	float DownforceCoeff = 1.1f;

	/** Weather crosswind (W3), N per (m/s)^2 of wind across the car's side:
	 *  ~0.5 * rho * Cd * side area, scaled down to stay gentle. Quadratic, so
	 *  Clear/Rain (3-10 m/s) is noise and only Tornado/Hurricane (25-35 m/s)
	 *  push noticeably (35 m/s ~ 2.4 kN, ~0.15 g on a 1.6 t car). 0 = off. */
	UPROPERTY(EditAnywhere, Category = "Aero")
	float WindSideForceCoeff = 2.0f;

	// --- garage setup (UODTuningPreset owns these; neutral defaults = Street).
	// Applied at their use sites rather than baked into the per-car fields
	// above, so switching setups never compounds on the car's own values.
	/** Surface grip multipliers: asphalt vs everything else. */
	UPROPERTY(EditAnywhere, Category = "Setup")
	float OnRoadGrip = 1.0f;

	UPROPERTY(EditAnywhere, Category = "Setup")
	float OffRoadGrip = 0.6f;

	/** Unpaved-road tier (PLAN-REALISM D2): fraction of OnRoadGrip on
	 *  PM_Gravel / PM_DirtRoad chunks, never below OffRoadGrip (rally tires
	 *  shouldn't grip worse on a gravel road than on the verge). */
	UPROPERTY(EditAnywhere, Category = "Setup")
	float GravelGrip = 0.80f;

	UPROPERTY(EditAnywhere, Category = "Setup")
	float DirtGrip = 0.70f;

	/** Extra grip loss at Wetness 1 on unpaved roads (mud), on top of the
	 *  asphalt weather loss; tread relief applies as for WeatherGrip. */
	UPROPERTY(EditAnywhere, Category = "Setup")
	float GravelWetLoss = 0.10f;

	UPROPERTY(EditAnywhere, Category = "Setup")
	float DirtWetLoss = 0.25f;

	/** Added to PeakSlipAngleDeg: wider peak = softer, catchable limit. */
	UPROPERTY(EditAnywhere, Category = "Setup")
	float SlipAngleAddDeg = 0.0f;

	/** Floor on SlideFrac: grip kept once sliding (drift holds slides). */
	UPROPERTY(EditAnywhere, Category = "Setup")
	float SlideFracMin = 0.0f;

	UPROPERTY(EditAnywhere, Category = "Setup")
	float SteerLockScale = 1.0f;

	/** Scales rear pedal brake (brake bias) and the handbrake. */
	UPROPERTY(EditAnywhere, Category = "Setup")
	float RearBrakeScale = 1.0f;

	UPROPERTY(EditAnywhere, Category = "Setup")
	float HandbrakeScale = 1.0f;

	/** Throttle fades out over the last 8 km/h below this; 0 = no cap. */
	UPROPERTY(EditAnywhere, Category = "Setup")
	float TopSpeedCapKph = 0.0f;

	/** Physics + visual tire diameter vs the car's WheelRadiusCm. */
	UPROPERTY(EditAnywhere, Category = "Setup")
	float TireDiameterScale = 1.0f;

	/** Visual only: negative camber (deg, top leans in) and tire width. */
	UPROPERTY(EditAnywhere, Category = "Setup")
	float CamberDeg = 0.0f;

	UPROPERTY(EditAnywhere, Category = "Setup")
	float FrontTireWidthScale = 1.0f;

	UPROPERTY(EditAnywhere, Category = "Setup")
	float RearTireWidthScale = 1.0f;

	/** Wheel radius incl. TireDiameterScale - visuals size the mesh off it. */
	float GetWheelRadius(int32 i) const { return Wheels[i].BaseRadius * TireDiameterScale; }

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
	/** Body-local XY of wheel i's strut attach point (Z deliberately omitted -
	 *  callers doing a ground-level probe pair this with their own downward
	 *  raycast at that world XY rather than trusting a pre-settle Z). Wheel
	 *  order is FL,FR,RL,RR (see SetupWheels' comment). */
	FVector2D GetWheelAttachXY(int32 i) const
	{
		return FVector2D(Wheels[i].Attach.X, Wheels[i].Attach.Y);
	}

	/** Nominal (unloaded) wheel-to-ground clearance below the body pivot, cm -
	 *  RestLengthCm + RideHeightDelta (strut extension from the pivot) + wheel
	 *  radius. Garage/showroom placement (AODMainMenu::EnterCarConfig) uses
	 *  this instead of a live SpringLen sample: physics is frozen there (no
	 *  suspension settling ever happens), so the pivot's actor-origin Z is NOT
	 *  ground level - it sits this far above it. */
	float GetRestGroundClearanceCm() const
	{
		return RestLengthCm + RideHeightDelta + (Wheels.Num() > 0 ? GetWheelRadius(0) : 34.0f);
	}

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

	/** Prime every wheel's SpringLen (and reset FzSm) from a fresh raycast at
	 *  the body's CURRENT transform, without going through the normal
	 *  ComprRate math. Call this once, immediately before
	 *  Body->SetSimulatePhysics(true), while the pawn is still kinematic.
	 *
	 *  Why this exists: TickComponent early-outs whenever
	 *  !Body->IsSimulatingPhysics(), so TickSuspension() never runs during the
	 *  BeginPlay lift + ground-snap raycast wait + settle hold - every
	 *  FODWheel::SpringLen sits frozen at its default construction value
	 *  (20.0f) for the whole multi-second kinematic phase, no matter how far
	 *  above/below that the wheel actually ends up. The instant physics goes
	 *  live, TickSuspension's very first call computes
	 *  ComprRate = (W.SpringLen - NewLen) / Dt using that stale 20.0f against
	 *  the real first-hit distance - e.g. a 6cm real gap against a stale 20cm
	 *  reads as the spring closing 14cm in a single ~16ms tick, an
	 *  ~875 cm/s "velocity" that never existed. The damper term
	 *  (C * ComprRate) turns that phantom velocity into a real force spike,
	 *  and because each corner's true ground distance differs slightly, the
	 *  four spikes land unevenly - enough net torque to flip the car before
	 *  the first real tick even settles (reproduced at the Breckenridge
	 *  Lincoln Ave spawn, where ground-snap's asphalt-search timeout means
	 *  the release pose is least likely to already match true ground
	 *  contact). Priming SpringLen to the real distance here makes that first
	 *  tick's ComprRate ~0, same as if suspension had been ticking normally
	 *  the whole time. */
	void PrimeSuspension();

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
	/** W3: refresh WeatherGripMult + apply the crosswind force. */
	void TickWeather(UPrimitiveComponent* Body);
	float TorqueCurve(float Rpm) const;
	float SurfaceGrip(const UPhysicalMaterial* Mat) const;
	/** Highest gear that keeps rpm in the band at this road speed. */
	int32 GearForSpeed(float AbsSpeedCmS) const;

	UPROPERTY()
	TArray<FODWheel> Wheels;

	// inputs
	float ThrottleIn = 0.0f, BrakeIn = 0.0f, SteerIn = 0.0f;
	bool bHandbrakeIn = false;
	bool bParkHold = false;   // see EngageParkHold()

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
	float EscU = 0.0f;               // smoothed intervention [0,1]
	int32 EscWheel = -1;             // outside front (gets the brake)
	float EscPrevSlipDeg = 0.0f;     // rear slip angle last tick
	float EscSlipRate = 0.0f;        // filtered growth rate, deg/s

	bool bBodyInit = false;
	double LastTraceTime = 0.0;

	// weather (W3): cached controllers, re-searched every 2 s while missing
	// (spawned by AODGameMode, may come up after us; absent in the garage)
	TWeakObjectPtr<AODWeatherController> WeatherCtl;
	TWeakObjectPtr<AODSeasonController> SeasonCtl;
	double LastWeatherSearch = -10.0;
	float WeatherGripMult = 1.0f;    // on-road tier multiplier, this tick
	float GravelWeatherMult = 1.0f;  // ... x gravel mud loss
	float DirtWeatherMult = 1.0f;    // ... x dirt mud loss

	// trace fields (-odtrace)
	float TraceSlip = 0.0f;
	float TraceAlphaF = 0.0f;
	float TraceFyF = 0.0f, TraceFyR = 0.0f, TraceFxR = 0.0f;
	float TraceCoupled = 0.0f;
	bool bTraceCut = false;
	bool bAbsActiveThisTick = false;
};
