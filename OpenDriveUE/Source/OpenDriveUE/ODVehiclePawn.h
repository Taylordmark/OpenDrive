#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Pawn.h"
#include "ODVehiclePawn.generated.h"

class UCameraComponent;
class UODVehicleDef;
class UODVehicleMovement;
class USpringArmComponent;
class UStaticMeshComponent;

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

	/** Steering input speed toward full lock (1/s) and back to center. */
	UPROPERTY(EditAnywhere, Category = "Input")
	float SteerRate = 2.5f;

	UPROPERTY(EditAnywhere, Category = "Input")
	float SteerReturnRate = 5.0f;

	/** Throttle/brake key smoothing (1/s). */
	UPROPERTY(EditAnywhere, Category = "Input")
	float PedalRate = 6.0f;

	/** Showroom-only camera parallax shift (cm, local to the arm's aim) so
	 *  the car frames toward the right of the screen with the menu text
	 *  panel on the left (Taylor, 2026-07-28), instead of dead-centered
	 *  behind the dim overlay. Negative shifts the CAMERA left, which
	 *  (parallax) pushes the CAR right in frame. Normal driving's chase cam
	 *  is unaffected - SetShowroom(false) restores the centered offset. */
	UPROPERTY(EditAnywhere, Category = "Vehicle")
	float ShowroomLateralOffsetCm = -150.0f;

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

	/** Turntable preview for the car-select menu: freezes physics, levels the
	 *  car, and detaches the spring-arm yaw so rotating the pawn spins the car
	 *  in front of a fixed camera (the menu drives the yaw). Off restores the
	 *  normal chase cam. */
	void SetShowroom(bool bOn);

	/** Drop the car at a chosen road point (map spawn pick) and re-arm the
	 *  existing PM_Asphalt ground-snap so TrySnapToGround settles it on the
	 *  road - reuses the proven spawn placement, no new logic. */
	void TeleportToSpawn(const FVector& RoadPoint, float YawDeg);

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
	TArray<float> WheelSpinDeg;

	bool bGroundSnapped = false;
	double SpawnTime = 0.0;
	double LastSnapLogTime = 0.0;
	// generous: the road tile at the spawn point now gets priority in
	// AODRoadManager's own wait-queue (closest-to-player, not first-parsed),
	// so this should rarely be hit, but a long fallback is cheap insurance
	// against a genuinely slow first-load (cold disk cache, etc.)
	static constexpr double GroundSnapTimeoutSec = 20.0;
	static constexpr float GroundClearanceCm = 100.0f;

	// scripted-drive hooks (-odrev/-odlift/-odsteer/-odbrake), parsed at
	// BeginPlay; -odthr=0.45 caps the scripted (and key) throttle for
	// controlled tests
	float RevAt = -1.0f, LiftAt = -1.0f, SteerAt = -1.0f, BrakeAt = -1.0f;
	float HookThrottle = 1.0f;
	// -odveh=T: cycle to the next car once at T s (headless V-swap test)
	float VehSwapAt = -1.0f;
};
