#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "InputCoreTypes.h"
#include "ODVehicleMovement.h"
#include "ODReplay.generated.h"

class AODVehiclePawn;
class UCameraComponent;
class UCanvas;
class UPhysicalMaterial;

/** One replay sample: body pose + what audio, lights, wheels and HUD read
 *  off UODVehicleMovement. Wheel spin is stored as Omega (rad/s), the pawn
 *  integrates it like it does live. */
struct FODReplayFrame
{
	FVector Pos = FVector::ZeroVector;
	FQuat Rot = FQuat::Identity;
	float Spring[4] = {}, Steer[4] = {}, Omega[4] = {};
	float Rpm = 0.0f, Throttle = 0.0f, Brake = 0.0f, EffThrottle = 0.0f, Load = 0.0f, Speed = 0.0f;
	int32 Gear = 1;
	uint8 Contact = 0;   // bit per wheel
	TWeakObjectPtr<UPhysicalMaterial> Surface[4];

	/** Floats lerp, rotation slerps, discrete fields (gear/contact/surface)
	 *  come from the nearer frame. */
	static FODReplayFrame Lerp(const FODReplayFrame& A, const FODReplayFrame& B, float T);
};

/** Always-on 30 s ring of FODReplayFrame at a fixed 60 Hz grid. Record()
 *  places samples on the straight path between two Tick poses (same scheme
 *  as the race manager's ghost recorder), so the buffer is identical at any
 *  frame rate. */
struct OPENDRIVEUE_API FODReplayBuffer
{
	static constexpr int32 Hz = 60;
	static constexpr int32 Capacity = 30 * Hz;

	void Record(const FODReplayFrame& Prev, double PrevT, const FODReplayFrame& Cur, double CurT);
	int32 Num() const { return Count; }
	/** 0 = oldest */
	const FODReplayFrame& Get(int32 I) const { return Ring[(Head - Count + I + Capacity) % Capacity]; }
	float Duration() const { return Count > 1 ? (Count - 1) / float(Hz) : 0.0f; }
	/** T seconds after the oldest sample, clamped */
	FODReplayFrame Sample(float T) const;

	static bool SelfCheck();   // -odselfcheck

private:
	TArray<FODReplayFrame> Ring;
	int32 Head = 0, Count = 0;
	double NextT = 0.0;
};

/**
 * #9 view-only instant replay. Records the player car every tick; the
 * Replay action (I / D-pad down) freezes the world (global time dilation,
 * not pause - pause mutes game audio), turns the car kinematic and drives
 * its pose + Movement telemetry from the buffer, so audio, lights, wheels
 * and HUD play back through their normal getters. Exit restores the exact
 * pose, velocities and Movement state. No rewind-and-resume.
 * Controls while active: Left/Right scrub, Up/Down speed, Space pause,
 * T trackside camera (pad: D-pad, shoulders, A, X); C still cycles car views.
 */
UCLASS()
class OPENDRIVEUE_API AODReplay : public AActor
{
	GENERATED_BODY()

public:
	AODReplay();
	virtual void Tick(float DeltaSeconds) override;

	static AODReplay* Find(const UWorld* World);
	static bool IsActiveIn(const UWorld* World);
	bool IsActive() const { return bActive; }
	void Toggle();
	void DrawOverlay(UCanvas* Canvas, float S) const;

private:
	void Enter();
	void Exit();
	FODReplayFrame Capture() const;
	void Apply(const FODReplayFrame& F);
	void UpdateTrackside();
	void SetTrackside(bool bOn);
	void OnKey(FKey Key);
	void OnKeyUp(FKey Key);
	float Rate() const;

	UPROPERTY()
	TObjectPtr<UCameraComponent> Cam;

	TWeakObjectPtr<AODVehiclePawn> Car;
	FODReplayBuffer Buf;
	FODReplayFrame Prev;
	double PrevT = -1.0;

	bool bActive = false, bPaused = false, bTrackside = false, bHaveSpot = false;
	float Play = 0.0f;
	int32 SpeedIdx = 2;
	int32 Scrub = 0;
	FVector Spot = FVector::ZeroVector;

	// exact-restore snapshot
	float SavedDilation = 1.0f;
	FTransform SavedXf;
	FVector SavedV = FVector::ZeroVector, SavedW = FVector::ZeroVector;
	FODReplayFrame SavedMove;          // scalars
	TArray<FODWheel> SavedWheels;     // every wheel field, not just the replayed ones
	bool bSavedCut = false;
};
