#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Pawn.h"
#include "InputCoreTypes.h"
#include "ODVehiclePawn.generated.h"

class UCameraComponent;
class UInputComponent;
class UODVehicleDef;
class UODVehicleMovement;
class USpringArmComponent;
class UStaticMesh;
class UStaticMeshComponent;

/** Which driving input source is in control (2026-08-27 controller/wheel
 *  support). Auto = "last device wins": whichever of Keyboard/Gamepad/Wheel
 *  most recently produced non-zero input drives the car; the other two are
 *  ignored (mouse freelook is independent of this and always live). The
 *  explicit modes pin the car to one source regardless of what else moves. */
enum class EODInputDevice : uint8
{
	Auto,
	Keyboard,
	Gamepad,
	Wheel,
};

/** Player camera views, in C-cycle order (see AODVehiclePawn::CameraView). */
enum class EODCameraView : uint8 { Chase, ChaseFar, ChaseNear, Cockpit, Hood, Bumper, Count };

/**
 * The OD car (Chaos rip-out, 2026-07-18): a simulating static-mesh Body, our
 * own UODVehicleMovement for ALL vehicle physics, manual wheel visuals, and
 * DIRECT input - raw keys to smoothed axes in one readable path. No Chaos
 * vehicle component, no Enhanced Input maps, no hidden BP curves.
 *
 * Geometry contract with pipeline/make_car_mesh.py: pivot at wheelbase
 * center at axle height; front axle x=+132, rear x=-128, half-track 84,
 * wheel radius 34 (mirrored in UODVehicleMovement's wheel table).
 *
 * Scripted-drive test hooks (headless verification) live HERE so they can't
 * fight the key input: -odrev=T (full throttle at T s), -odlift=T (throttle
 * off at T s), -odsteer=T (half steer at T s).
 */
UCLASS()
class OPENDRIVEUE_API AODVehiclePawn : public APawn
{
	GENERATED_BODY()

public:
	AODVehiclePawn();
	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;
	virtual void SetupPlayerInputComponent(UInputComponent* Input) override;

	/** Arc 4.1 (next steps.md SS4.1/SS7.32): re-derives every keyboard/
	 *  gamepad key this pawn binds from FODInputConfig and rebuilds them on
	 *  the given InputComponent (or this pawn's own InputComponent if
	 *  nullptr) - called once from SetupPlayerInputComponent at spawn and
	 *  again by AODRebindMenu after a live rebind, so a mid-session key
	 *  change takes effect immediately without a restart. Removes only the
	 *  keys THIS function itself bound last time (tracked in
	 *  LastODManagedKeys) before rebuilding, so arrow-key aliases, V
	 *  (dev-facing CycleVehicle) and the wheel-axis binds in
	 *  SetupGamepadAndWheelInput are untouched. */
	void RefreshODManagedKeyBindings(UInputComponent* Input = nullptr);

	UPROPERTY(VisibleAnywhere, Category = "Vehicle")
	TObjectPtr<UStaticMeshComponent> Body;

	UPROPERTY(VisibleAnywhere, Category = "Vehicle")
	TObjectPtr<UODVehicleMovement> Movement;

	UPROPERTY(VisibleAnywhere, Category = "Vehicle")
	TObjectPtr<USpringArmComponent> SpringArm;

	UPROPERTY(VisibleAnywhere, Category = "Vehicle")
	TObjectPtr<UCameraComponent> Camera;

	UPROPERTY(VisibleAnywhere, Category = "Vehicle")
	TArray<TObjectPtr<UStaticMeshComponent>> WheelVisuals;

	/** Mouse-look camera orbit, degrees of raw mouse delta per unit sensitivity
	 *  (2026-08-25) - lets the player look around the car independent of its
	 *  heading (e.g. to inspect a junction while stopped/driving) by offsetting
	 *  the SpringArm's relative rotation on top of whatever it inherits from
	 *  the car. No Enhanced Input map for this either (see class comment) -
	 *  polled the same DIRECT way as the keyboard axes, via the PlayerController's
	 *  raw GetInputMouseDelta rather than an axis mapping that would need
	 *  project-settings config to exist. */
	UPROPERTY(EditAnywhere, Category = "Input")
	float MouseLookSensitivity = 0.5f;

	UPROPERTY(EditAnywhere, Category = "Input")
	float LookPitchMinDeg = -70.0f;

	UPROPERTY(EditAnywhere, Category = "Input")
	float LookPitchMaxDeg = 70.0f;

	/** Seconds of no mouse movement before freelook eases back to the
	 *  straight-ahead chase view. */
	UPROPERTY(EditAnywhere, Category = "Input")
	float LookRecenterDelaySec = 1.0f;

	/** Recenter ease rate (FInterpTo speed, 1/s) - higher snaps back faster. */
	UPROPERTY(EditAnywhere, Category = "Input")
	float LookRecenterSpeed = 4.0f;

	// ---- Keyboard driving (2026-10-09 rework; Taylor: "the current inputs
	// make it difficult to stay on the road", then "still not turning enough -
	// keyboard users fast-tap to set steering"). ALL keyboard steer/pedal
	// tuning lives here. Usual sim/arcade practice: hold ramps in, the
	// opposite key unwinds fast, but RELEASE decays slowly and exponentially,
	// so a tap rhythm settles at a partial lock set by its duty cycle (a
	// linear fast return cancelled every tap). The held-key lock at speed is
	// what puts the front tyres at their peak: kinematic angle for
	// KbSteerTargetG plus the tyre's own peak slip angle (~7 deg at any
	// speed) - a pure 1/v^2 cap left ~3 deg at 60 mph. Exact piecewise
	// integration (KbSteerStep in the .cpp), identical at any fps;
	// pipeline/check_kbm_steer_ramp.py mirrors the math. Gamepad/wheel never
	// touch any of these.

	/** Seconds of holding A/D to go from center to the full (speed-capped) lock. */
	UPROPERTY(EditAnywhere, Category = "Input")
	float KbSteerRampInSec = 0.35f;

	/** Seconds to unwind full lock to center when the OPPOSITE key is held
	 *  (then it ramps out the other way at KbSteerRampInSec). */
	UPROPERTY(EditAnywhere, Category = "Input")
	float KbSteerCounterSec = 0.12f;

	/** Release time constant (s): with no key the ramp decays as
	 *  exp(-t/Tau). 8 Hz taps at 50% duty settle near 0.5 of the speed lock,
	 *  30% duty near 0.2; full lock -> 5% in ~0.6 s with no key. */
	UPROPERTY(EditAnywhere, Category = "Input")
	float KbSteerReleaseTau = 0.2f;

	/** Response curve on the ramp: steer = ramp^Exp (>1 = finer near center). */
	UPROPERTY(EditAnywhere, Category = "Input")
	float KbSteerCurveExp = 1.2f;

	/** Held-key lock at speed, in road-wheel degrees:
	 *  deg(L * TargetG*g / v^2) + SlipFrac * the car's PeakSlipAngleDeg,
	 *  as a fraction of MaxSteer*SteerLockScale*Taper (clamped to 1).
	 *  Defaults (9 deg peak slip, 38 deg lock): full lock below ~25 mph,
	 *  30 mph ~0.62 (~20 deg), 60 mph ~0.42 (~10 deg), 120 mph ~0.40. */
	UPROPERTY(EditAnywhere, Category = "Input")
	float KbSteerTargetG = 1.5f;

	UPROPERTY(EditAnywhere, Category = "Input")
	float KbSteerSlipFrac = 0.8f;

	/** Wheelbase used for the kinematic term (input shaping only). */
	UPROPERTY(EditAnywhere, Category = "Input")
	float KbSteerWheelbaseCm = 270.0f;

	/** Throttle/brake keys: seconds 0 -> full on press, full -> 0 on release. */
	UPROPERTY(EditAnywhere, Category = "Input")
	float PedalRampInSec = 0.25f;

	UPROPERTY(EditAnywhere, Category = "Input")
	float PedalReleaseSec = 0.1f;

	/** Speed-sensitive GAMEPAD stick lock (2026-08-27 keyboard, 2026-09-25
	 *  gamepad; keyboard moved to KbSteerFullLockSpeedCmS 2026-10-09). Below
	 *  FadeStart the stick means full lock; by FadeEnd only
	 *  HighSpeedSteerFrac of lock. The wheel stays 1:1. */
	UPROPERTY(EditAnywhere, Category = "Input")
	float SteerFadeStartCmS = 900.0f;   // ~20 mph

	UPROPERTY(EditAnywhere, Category = "Input")
	float SteerFadeEndCmS = 3600.0f;    // ~80 mph

	UPROPERTY(EditAnywhere, Category = "Input")
	float HighSpeedSteerFrac = 0.35f;

	/** Showroom camera placement (2026-07-28) - deliberately NOT routed
	 *  through the SpringArm's TargetArmLength/SocketOffset/bInheritYaw
	 *  system (see SetShowroom's comment for why: that system's inheritance
	 *  flags did not behave as documented when actually measured, and
	 *  chasing it through several rounds of live screenshots kept producing
	 *  the exact same wrong framing regardless of what was changed). The
	 *  showroom camera is detached and placed with plain, fully-deterministic
	 *  vector math instead - these three tunables are the whole of it. */
	UPROPERTY(EditAnywhere, Category = "Vehicle")
	float ShowroomDistanceCm = 550.0f;

	UPROPERTY(EditAnywhere, Category = "Vehicle")
	float ShowroomHeightCm = 160.0f;

	/** How far to one side the LOOK-AT point is biased (cm) - biasing the
	 *  look-at target left pushes the car right in frame (and vice versa),
	 *  independent of camera position, so the car reads as "off to the side"
	 *  with the menu text panel on the other. */
	UPROPERTY(EditAnywhere, Category = "Vehicle")
	float ShowroomLookAtBiasCm = 120.0f;

	UPROPERTY(EditAnywhere, Category = "Vehicle")
	float MassKg = 1500.0f;

	// --- garage/main-menu API (2026-07-25) ---
	/** Cars discovered at BeginPlay, sorted by DisplayName (index 0 = spawn
	 *  car). Empty only if no UODVehicleDef assets exist (hardcoded-coupe
	 *  fallback). The menu reads names off these for its Car row. */
	const TArray<TObjectPtr<UODVehicleDef>>& GetVehicleDefs() const { return Vehicles; }
	int32 GetVehicleIndex() const { return CurrentVehicleIdx; }
	/** Select a car by index and hot-swap it (mesh + geometry + baseline
	 *  physics). No-op if out of range or already current. */
	void SetVehicleByIndex(int32 Index);

	/** Garage setup's tyres (AODTuningManager::ApplyPreset, by preset
	 *  DisplayName): Realistic swaps each axle's wheel for that setup's
	 *  SM_CarWheel_real_<Kind> (TyreKind in the .cpp); Stylized keeps the one
	 *  wheel. Kept across car swaps. */
	void SetTyreSetup(const FString& SetupName);

	/** Turntable preview for the car-select menu: freezes physics, levels the
	 *  car, and detaches the spring-arm yaw so rotating the pawn spins the car
	 *  in front of a fixed camera (the menu drives the yaw). Off restores the
	 *  normal chase cam. */
	void SetShowroom(bool bOn);
	/** True while the showroom/garage preview is active (SetShowroom(true) has
	 *  run and SetShowroom(false) hasn't since) - AODVehicleAudio checks this
	 *  to mute the engine synth while parked (2026-08-27: the boot menu no
	 *  longer pauses the world, so UODVehicleMovement keeps the pawn's last
	 *  RPM frozen-but-nonzero and the synth kept audibly idling/revving). */
	bool IsInShowroom() const { return bInShowroom; }

	/** Drop the car at a chosen road point (map spawn pick) and re-arm the
	 *  existing PM_Asphalt ground-snap so TrySnapToGround settles it on the
	 *  road - reuses the proven spawn placement, no new logic. */
	void TeleportToSpawn(const FVector& RoadPoint, float YawDeg);

	/** Flip recovery (R, 2026-08-27): right the car in place - keep position
	 *  and heading, zero roll/pitch, lift slightly, and re-arm the ground
	 *  snap with a SHORTENED fallback so an off-road reset (no asphalt under
	 *  the trace) settles in seconds instead of the full spawn timeout. */
	void ResetVehicle();

	// --- out-of-bounds reset (2026-09-06, AUDIT.md 5.4/rec. 5) ---
	/** Distance from the road centerline (cm) beyond which the off-road
	 *  countdown starts. Replaces the invisible barrier wall: the player can
	 *  roam the shoulder freely, but sustained travel past this line resets
	 *  them to the nearest road point instead of bouncing off unseen
	 *  collision. */
	UPROPERTY(EditAnywhere, Category = "OutOfBounds")
	float OobDistCm = 6000.0f;   // 60 m

	/** Seconds the car may stay beyond OobDistCm before the reset fires -
	 *  long enough to recover a wide slide, short enough that "driving away
	 *  into the wilderness" visibly doesn't work. */
	UPROPERTY(EditAnywhere, Category = "OutOfBounds")
	float OobGraceSec = 3.0f;

	/** Seconds with ALL FOUR wheels out of contact before the reset fires -
	 *  the void-fall catch (no-collision far terrain, carve gaps). ~2 s of
	 *  true freefall is a ~20 m drop, far past any jump a road crest can
	 *  launch. */
	UPROPERTY(EditAnywhere, Category = "OutOfBounds")
	float OobAirborneSec = 2.0f;

	/** Stuck-recovery trigger (2026-09-07, Taylor's drive-test find: wedged
	 *  behind a guardrail just off the shoulder - INSIDE OobDistCm, so the
	 *  distance trigger above never fires - with no way back over the rail).
	 *  Fires the same nearest-road reset when the player is actively trying
	 *  to move (throttle above StuckThrottleFrac, brake released) but the
	 *  car isn't going anywhere (below StuckSpeedCmS) for StuckGraceSec.
	 *  Requiring ACTIVE throttle is what makes this safe in free-roam: a
	 *  player who parks off-road to look at the scenery holds no throttle
	 *  and is never yanked back; a launch from standstill passes
	 *  StuckSpeedCmS in well under a second, so it never accumulates. */
	UPROPERTY(EditAnywhere, Category = "OutOfBounds")
	float StuckThrottleFrac = 0.5f;

	/** Speed floor (cm/s) below which throttle input counts as "stuck". */
	UPROPERTY(EditAnywhere, Category = "OutOfBounds")
	float StuckSpeedCmS = 60.0f;

	/** Seconds of continuous throttle-but-not-moving before the reset. */
	UPROPERTY(EditAnywhere, Category = "OutOfBounds")
	float StuckGraceSec = 2.5f;

	// --- crash response (2026-10-06, PLAN-REALISM.md Phase 0; D1: a clean
	// stop, impact sound + small shake, no damage) ---
	/** Rate Chaos may push the body out of an overlap it first finds already
	 *  penetrating (cm/s). Chaos's default is unlimited: it resolves the whole
	 *  depth in one step, so a car ~1 m into a merged building trimesh left
	 *  at depth/dt - hundreds of m/s. Applied with CCD at BeginPlay. */
	UPROPERTY(EditAnywhere, Category = "Crash")
	float CrashMaxDepenetrationCmS = 300.0f;

	/** Share of the into-wall speed handed back as bounce. */
	UPROPERTY(EditAnywhere, Category = "Crash")
	float CrashRestitution = 0.1f;

	/** Most angular velocity one impact may add (deg/s). */
	UPROPERTY(EditAnywhere, Category = "Crash")
	float CrashMaxSpinDegS = 90.0f;

	/** Into-wall speed below which a contact is a scrape, left to the solver
	 *  (cm/s, ~4.5 mph) - a rail brush keeps its "Crew 2 deflect" feel. */
	UPROPERTY(EditAnywhere, Category = "Crash")
	float CrashMinNormalSpeedCmS = 200.0f;

	/** Into-wall speed that maps to a full-strength impact sound and shake
	 *  (cm/s, ~50 mph). */
	UPROPERTY(EditAnywhere, Category = "Crash")
	float CrashFullSpeedCmS = 2235.0f;

	/** Camera shake amplitude at full strength (deg). */
	UPROPERTY(EditAnywhere, Category = "Crash")
	float CrashShakeDeg = 2.0f;

	/** Remaining seconds on the off-road countdown for the HUD warning, or
	 *  <0 when not out of bounds (also <0 on levels with no AODRoadManager,
	 *  where the feature is entirely inert - e.g. Tail of the Dragon). */
	float GetOobCountdownSec() const { return OobCountdownSec; }

	/** True while the active countdown (GetOobCountdownSec() >= 0) came from
	 *  the stuck trigger rather than the off-road distance trigger - HUD
	 *  wording only ("STUCK" vs "OFF ROAD"). */
	bool IsOobStuckReset() const { return bOobStuckReset; }

	/** Road manager nearest the car as of the last 2 Hz out-of-bounds scan
	 *  (2026-10-05, large world) - AODRaceHUD's nav uses it. Null before the
	 *  first scan / on levels with no road. */
	class AODRoadManager* GetNearestRoadMgr() const { return OobRoadMgr.Get(); }

	/** Place the car at a FIXED, self-contained location (the main menu's
	 *  showroom void, 2026-07-28) with no ground-snap wait at all - unlike
	 *  TeleportToSpawn, which waits on real streamed terrain/road, this
	 *  location is chosen once and never depends on tile streaming, so it's
	 *  immediately marked settled. See AODMainMenu's EnterCarConfig() for why:
	 *  the menu pauses the game (so the frozen pawn can't drive off), but
	 *  neither the pawn nor the streaming managers tick while paused, so the
	 *  real-world ground-snap wait could freeze the car mid-air at any point
	 *  for the whole time the menu is up. */
	void PlaceInShowroom(const FVector& Loc);

	/** True once TrySnapToGround has settled the car (real pavement hit, or
	 *  the GroundSnapTimeoutSec fallback) and released physics - the menu's
	 *  loading screen (2026-07-26) polls this instead of unpausing blind, so
	 *  the player never sees the car hovering mid-air while a freshly-
	 *  teleported area's tiles are still streaming in. */
	bool IsGroundSnapped() const { return bGroundSnapped; }

	/** True once the snap trace has CONFIRMED real pavement under the car and
	 *  it's being held kinematic on that surface through the settle window
	 *  (or the snap has fully completed). Heavy one-shot streaming work that
	 *  gates on the player's ground being real (building/forest placement
	 *  bursts) should key on THIS, not IsGroundSnapped(): keying on the
	 *  latter fires the burst on the exact frame physics releases, and the
	 *  resulting 400-950ms frames let the raycast-suspension car free-fall
	 *  through the road plane between wheel traces (Frisco spawn,
	 *  2026-09-01). During the hold the car is frozen, so hitches are free. */
	bool IsGroundResolved() const { return bGroundSnapped || SnapReadyTime != 0.0; }

	// --- controller/wheel device selection (2026-08-27) ---
	/** Explicit device pin, or Auto (last-device-wins). Persisted to
	 *  GameUserSettings.ini (see ODVehiclePawn.cpp's Save/LoadInputDeviceMode)
	 *  the same lightweight way other one-off settings in this project are
	 *  kept - no SaveGame object exists yet, and this is one CVar-sized value. */
	EODInputDevice GetInputDeviceMode() const { return DeviceMode; }
	void SetInputDeviceMode(EODInputDevice Mode);
	/** The device actually driving the car THIS tick - equal to
	 *  GetInputDeviceMode() unless that's Auto, in which case it's whichever
	 *  device most recently produced input. The menu's row displays this
	 *  resolved value next to "Auto" so it reads as live, not just a mode name. */
	EODInputDevice GetActiveInputDevice() const { return ActiveDevice; }
	static FString InputDeviceModeToString(EODInputDevice Mode);

	/** Degrees-of-rotation scale for a wheel's steering axis (1.0 = wheel's
	 *  full physical lock-to-lock maps 1:1 to the car's full steering lock;
	 *  0.5 would mean the wheel needs to turn twice as far - use this to
	 *  match a specific wheel's configured rotation range without adding any
	 *  response curve, which wheel steering must not have). */
	UPROPERTY(EditAnywhere, Category = "Input|Wheel")
	float WheelDegreesOfRotationScale = 1.0f;

	/** Which RawInput GenericUSBController_AxisN (1-based) feeds each wheel
	 *  channel - varies per wheel model/driver, so these are exposed rather
	 *  than guessed. Defaults are a reasonable Logitech/Thrustmaster-shaped
	 *  starting guess (1=wheel rotation, 2=throttle, 3=brake); adjust to taste
	 *  once real hardware is plugged in (see class comment / task report). */
	UPROPERTY(EditAnywhere, Category = "Input|Wheel")
	int32 WheelSteerAxisIndex = 1;

	UPROPERTY(EditAnywhere, Category = "Input|Wheel")
	int32 WheelThrottleAxisIndex = 2;

	UPROPERTY(EditAnywhere, Category = "Input|Wheel")
	int32 WheelBrakeAxisIndex = 3;

	UPROPERTY(EditAnywhere, Category = "Input|Wheel")
	bool bWheelInvertThrottle = false;

	UPROPERTY(EditAnywhere, Category = "Input|Wheel")
	bool bWheelInvertBrake = false;

private:
	void ThrottlePressed() { bThrottleKey = true; }
	void ThrottleReleased() { bThrottleKey = false; }
	void BrakePressed() { bBrakeKey = true; }
	void BrakeReleased() { bBrakeKey = false; }
	void LeftPressed() { bLeftKey = true; }
	void LeftReleased() { bLeftKey = false; }
	void RightPressed() { bRightKey = true; }
	void RightReleased() { bRightKey = false; }
	void HandbrakePressed();
	void HandbrakeReleased();

	void EnsureMeshes();

	// --- selectable cars (garage groundwork, 2026-07-24) ---
	// Discovered from the asset registry at BeginPlay (drop a UODVehicleDef in
	// content -> it appears), sorted by DisplayName so the base "Coupe" is
	// index 0 = the spawn car. V cycles; the swap is a live hot-swap on this
	// same pawn (meshes + geometry + baseline stats), keeping position/camera.
	void LoadVehicleDefs();
	void ApplyVehicleDef(UODVehicleDef* Def);
	void CycleVehicle();

	UPROPERTY()
	TArray<TObjectPtr<UODVehicleDef>> Vehicles;

	int32 CurrentVehicleIdx = 0;

	// the car's wheel after ODVisualStyle::Pick (before the tier Upgrade), and
	// the garage setup whose tyre kinds ApplyWheelMeshes swaps in
	UPROPERTY()
	TObjectPtr<UStaticMesh> WheelBaseMesh;
	FString TyreSetup;
	void ApplyWheelMeshes();

	/** Holds the Body kinematic (no physics) from spawn until a downward
	 *  trace confirms where the actual streamed terrain/road is, then
	 *  teleports there and releases physics - same ground-snap idea as
	 *  AODRoadManager's SnapChunkToGround, applied to the pawn's own spawn.
	 *  PlayerStart's baked height (from the offline pipeline) can disagree
	 *  with the actual runtime terrain by tens of meters (measured via
	 *  -odseam); spawning into that gap let the car overlap solid collision
	 *  and get violently ejected by Chaos depenetration - this replaces
	 *  "trust the baked height plus a clearance guess" with "wait for the
	 *  real answer, then place exactly." */
	void TrySnapToGround();

	bool bThrottleKey = false, bBrakeKey = false;
	bool bLeftKey = false, bRightKey = false;
	float SmSteer = 0.0f, SmThrottle = 0.0f, SmBrake = 0.0f;
	float KbSteerRamp = 0.0f;   // keyboard steer time-held state [-1,1], pre-curve/cap
	TArray<float> WheelSpinDeg;

	// free-look orbit offset on top of the SpringArm's base -12 deg tilt
	// (BaseCameraPitchDeg, set once from the constructor's initial relative
	// rotation so this doesn't have to duplicate that literal)
	float BaseCameraPitchDeg = 0.0f;
	float LookYawDeg = 0.0f;
	float LookPitchDeg = 0.0f;
	double LastLookInputTime = 0.0;
	void UpdateFreeLook(float DeltaSeconds);

	// --- camera views (2026-10-16): C / pad Y cycles, persisted in
	// GameUserSettings [OpenDrive.Camera] View. Chase* ride the spring arm
	// with exact-exponential lag; the first-person ones pin the arm (length
	// 0, no lag) at a per-car mount from the def (zero = derived from the
	// mesh bounds in ResolveCameraMounts) and add head motion on Camera.
	EODCameraView CameraView = EODCameraView::Chase;
	FVector CockpitEyeCm, SteeringWheelCm, HoodCamCm, BumperCamCm;
	FVector HeadAccelG = FVector::ZeroVector;   // smoothed car-local accel, g
	FVector HeadPrevVel = FVector::ZeroVector;
	float HeadApexYawDeg = 0.0f;
	void CycleCameraView();
	void ApplyCameraView();
	void ResolveCameraMounts(const UODVehicleDef* Def);
	void UpdateCameraMotion(float DeltaSeconds);

	// procedural steering wheel (rim/spokes/hub of engine cylinders), shown
	// only in Cockpit, laid over the Realistic mesh's baked static wheel
	UPROPERTY()
	TObjectPtr<USceneComponent> CockpitWheel;
	void BuildCockpitWheel();

	bool bGroundSnapped = false;
	bool bInShowroom = false;   // see IsInShowroom() above
	double SpawnTime = 0.0;
	double LastSnapLogTime = 0.0;
	double LastRollLogTime = 0.0;   // rollover-tripwire log throttle
	double LastScriptLogTime = 0.0; // 1 Hz scripted-drive telemetry throttle

	// --- out-of-bounds reset state (2026-09-06) ---
	/** Ticks the off-road/airborne accumulators and fires the reset. */
	void TickOutOfBounds(float DeltaSeconds);
	/** Nearest-road scan is O(centerline) - throttled to 2 Hz (same
	 *  convention as AODRoadManager's place-label scan); the cached distance
	 *  is reused between scans. */
	double LastOobScanTime = 0.0;
	float OobDistNowCm = 0.0f;      // last measured distance to centerline
	float OobOffRoadSec = 0.0f;     // time continuously beyond OobDistCm
	float OobAirSec = 0.0f;         // time continuously with 0 wheel contacts
	float OobStuckSec = 0.0f;       // time continuously throttling-but-not-moving
	                                 // (see StuckThrottleFrac's UPROPERTY doc)
	float OobCountdownSec = -1.0f;  // HUD-visible remaining grace, <0 = clear
	bool bOobStuckReset = false;    // last countdown/reset was the stuck trigger
	                                 // (HUD wording only)
	TWeakObjectPtr<class AODRoadManager> OobRoadMgr;   // nearest as of the last scan

	// --- crash response state (see the Crash UPROPERTYs) ---
	/** Body OnComponentHit: wall/building/rail/trunk impacts get a defined
	 *  response computed from the PRE-impact step velocity
	 *  (UODVehicleMovement::PreHitVelocity), so nothing the solver did that
	 *  step - depenetration included - reaches the car. */
	UFUNCTION()
	void OnBodyHit(UPrimitiveComponent* HitComp, AActor* OtherActor,
		UPrimitiveComponent* OtherComp, FVector NormalImpulse, const FHitResult& Hit);
	// body velocity as of this frame's Tick - -odcrashtrace diagnostics only
	FVector CrashPreVel = FVector::ZeroVector;
	float CrashShake = 0.0f;   // 0..1, decays in Tick

	// -odcrashtrace: per-frame body state for the frames around an impact
	// (a short pre-impact ring, then 2 s after), each hit's normal/
	// penetration/solver output, and a peak-vs-impact speed summary
	void TickCrashTrace(double Now);
	void OpenCrashTraceWindow(double Now, float ImpactCmS, const TCHAR* Why);
	bool bCrashTrace = false;
	TArray<FString> CrashTraceRing;
	int32 CrashTraceRingNext = 0;
	double CrashTraceUntil = 0.0;      // >0 while a window is open
	float CrashTraceImpactCmS = 0.0f;  // speed at the impact(s) in this window
	float CrashTracePeakCmS = 0.0f;    // highest speed seen after them
	double LastBellyLog = 0.0;         // ODBelly rate limit
	// generous: the road tile at the spawn point now gets priority in
	// AODRoadManager's own wait-queue (closest-to-player, not first-parsed),
	// so this should rarely be hit, but a long fallback is cheap insurance
	// against a genuinely slow first-load (cold disk cache, etc.)
	static constexpr double GroundSnapTimeoutSec = 20.0;
	// 30, was 100 (2026-09-01): the chassis origin naturally rests ~15-20cm
	// above the contact surface (measured from the Frisco spawn traces), so
	// releasing from +100 free-falls the car ~85cm - a 1.6M-N landing spike
	// that bounced it backwards off the road-end edge. +30 leaves margin for
	// surface tracking error while dropping the car ~15cm instead.
	static constexpr float GroundClearanceCm = 30.0f;

	// 2026-08-26: hold the car kinematic this long AFTER the asphalt trace
	// first connects before releasing physics. A trace hit proves the road
	// is QUERYABLE, not that its physics body is fully registered for
	// simulation contact - Taylor fell straight through the road twice on
	// junction spawns where the pad had just been built (the open
	// Chaos/UE5.8 registration bug's window). The settle restarts if the
	// asphalt hit lapses mid-wait.
	static constexpr double GroundSnapSettleSec = 3.0;
	// 2026-09-07 (Taylor: "respawn takes forever"): the 3s settle above
	// guards against Chaos body registration lag on FRESHLY STREAMED tiles.
	// A mid-drive OOB/stuck reset teleports to a road whose collision has
	// been resident and simulated for a while - if the asphalt trace
	// connects almost immediately after the teleport (within
	// WarmSnapWindowSec of SpawnTime), the registration race can't apply
	// and the short settle below is used instead. Cold spawns and genuinely
	// streaming-lagged resets still take the full 3s path.
	static constexpr double WarmSnapWindowSec = 1.0;
	static constexpr double WarmSnapSettleSec = 0.75;
	// true once ANY real-world snap has fully completed this session - the
	// warm settle requires it, so the initial spawn (whose road tile may
	// have JUST been built - the exact registration race the 3s settle
	// guards) always takes the full settle even when its trace connects
	// instantly. PlaceInShowroom's fake snap deliberately does not set it.
	bool bEverGroundSnapped = false;
	double SnapReadyTime = 0.0;

	// 2026-09-01: additionally require quiet frames before release. The
	// suspension is a game-thread raycast; during streaming-burst hitches
	// (Frisco spawn measured 388ms frames from the forest scatter) the body
	// free-falls under physics-thread gravity for the whole frame and can
	// tunnel straight through the road plane between two wheel traces - the
	// settle timer alone released mid-hitch and the car dropped ~10m through
	// pavement onto the terrain carve below. Frames longer than
	// SnapHitchFrameSec reset a quiet-period clock; release waits for
	// SnapQuietSec of continuous calm (GroundSnapTimeoutSec still caps the
	// total wait so a genuinely slow machine isn't held forever).
	static constexpr double SnapHitchFrameSec = 0.075;
	static constexpr double SnapQuietSec = 0.75;
	double LastHitchTime = 0.0;

	// 2026-09-03: one-shot nearby-asphalt nudge, tried once a spawn point has
	// spent a couple of seconds NOT finding PM_Asphalt directly below it (see
	// TrySnapToGround's off-road search block for why this exists - the
	// Breckenridge/Lincoln Ave spawn XY simply isn't above any road collision
	// at all, off in space over a DynamicMeshComponent instead, so no amount
	// of waiting out the 20s timeout was ever going to fix it).
	bool bTriedOffRoadNudge = false;

	// scripted-drive hooks (-odrev/-odlift/-odsteer/-odbrake), parsed at
	// BeginPlay; -odthr=0.45 caps the scripted (and key) throttle for
	// controlled tests
	float RevAt = -1.0f, LiftAt = -1.0f, SteerAt = -1.0f, BrakeAt = -1.0f;
	float HookThrottle = 1.0f;
	// -odveh=T: cycle to the next car once at T s (headless V-swap test)
	float VehSwapAt = -1.0f;
	// -odsetvat=T -odsetv=CMS: at T s, hard-set body velocity to CMS cm/s
	// along the current facing, once. Exists because no test area has a
	// straight long enough to actually reach highway speed under scripted
	// throttle - this is the only way to brake-test FROM high speed headless.
	float SetVAt = -1.0f;
	float SetVCmS = 0.0f;

	// ===========================================================================
	// Controller (gamepad) + racing wheel input (2026-08-27). Kept entirely
	// separate from the keyboard block above so the two can be edited without
	// conflicting: this section only ever WRITES its own dedicated members
	// (GamepadSteerAxis/GamepadThrottleAxis/... below, RawWheelAxis[]) and
	// reads them back in Tick(); it never reaches into bThrottleKey/bLeftKey/
	// SmSteer/etc. Analog gamepad/wheel axes deliberately BYPASS the keyboard
	// smoothing (SmSteer/SmThrottle/SmBrake) - an analog input is already
	// smooth, so routing it through the same rate-limited interpolation used
	// to fake analog behavior out of on/off keys would only add lag. Wheel
	// axes additionally get NO response curve and NO deadzone (full 1:1
	// range, scaled only by WheelDegreesOfRotationScale) - unlike the gamepad
	// stick, a wheel is already a precise analog position, not a spring-
	// loaded stick that rests dead-center and needs a curve to feel good off
	// center.
	// ===========================================================================

	void GamepadSteerAxis(float Value);
	void GamepadThrottleAxis(float Value);
	void GamepadBrakeAxis(float Value);
	void GamepadLookXAxis(float Value) { GamepadLookX = Value; }
	void GamepadLookYAxis(float Value) { GamepadLookY = Value; }
	void GamepadHandbrakePressed();
	void GamepadHandbrakeReleased();

	// raw analog values latched by the bound axis keys above/below, consumed
	// (and zero-checked for "was this device just used") once per Tick
	float GamepadSteerRaw = 0.0f;     // post deadzone+curve, [-1,1]
	float GamepadThrottleRaw = 0.0f;  // [0,1]
	float GamepadBrakeRaw = 0.0f;     // [0,1]
	float GamepadLookX = 0.0f;        // right stick, raw [-1,1], consumed in UpdateFreeLook
	float GamepadLookY = 0.0f;
	bool bGamepadHandbrake = false;

	static constexpr float GamepadDeadzone = 0.15f;
	UPROPERTY(EditAnywhere, Category = "Input|Gamepad")
	float GamepadLookSensitivityDegPerSec = 120.0f;

	/** RawInput GenericUSBController_Axis1..6 (0-based here), latched
	 *  raw/normalized 0..1 by the RawInput plugin - see WheelSteerAxisIndex
	 *  etc. (public section above) for which of these feeds which channel.
	 *  Bound via lambdas in SetupPlayerInputComponent (BindAxisKey's
	 *  GetDelegateForManualSet()), not named per-axis functions, since the
	 *  RawInput key names are only known at runtime (module registers them on
	 *  plugin load) and there's no way to know in advance which physical axis
	 *  a given wheel model puts steering/throttle/brake on. */
	static constexpr int32 NumWheelAxesBound = 6;
	float RawWheelAxis[NumWheelAxesBound] = {0.5f, 0.5f, 0.5f, 0.5f, 0.5f, 0.5f};
	float PrevWheelAxis[NumWheelAxesBound] = {0.5f, 0.5f, 0.5f, 0.5f, 0.5f, 0.5f};
	bool bWheelHandbrake = false;
	void WheelHandbrakePressed() { bWheelHandbrake = true; }
	void WheelHandbrakeReleased() { bWheelHandbrake = false; }

	void SetupGamepadAndWheelInput(UInputComponent* Input);

	/** Keys RefreshODManagedKeyBindings bound last call, so the next call can
	 *  remove exactly those before rebuilding from the current config -
	 *  see that function's header comment. */
	TArray<FKey> LastODManagedKeys;
	TWeakObjectPtr<UInputComponent> CachedInputComponent;

	// --- device selection state (Auto = last-device-wins) ---
	EODInputDevice DeviceMode = EODInputDevice::Auto;
	EODInputDevice ActiveDevice = EODInputDevice::Keyboard;
	double LastKeyboardInputTime = 0.0;
	double LastGamepadInputTime = 0.0;
	double LastWheelInputTime = 0.0;
	// change-detection baselines: an analog device counts as "just used" when
	// its latched value moves at all since last tick, not merely "away from
	// center" - a wheel's pedals commonly idle at a hard extreme (not 0.5),
	// so "away from center" can't tell idle from active for those axes.
	float PrevGamepadSteerRaw = 0.0f, PrevGamepadThrottleRaw = 0.0f, PrevGamepadBrakeRaw = 0.0f;
	void UpdateActiveInputDevice();
	static void SaveInputDeviceMode(EODInputDevice Mode);
	static EODInputDevice LoadInputDeviceMode();
};
