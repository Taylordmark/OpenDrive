#pragma once

#include "CoreMinimal.h"

struct FODWheelBind;

/**
 * Racing wheel / pedals / shifter hardware, via DirectInput8 (Windows; the
 * stubs elsewhere report no device). DirectInput rather than the engine's
 * RawInput plugin: that one exposes global GenericUSBController keys with no
 * per-device split and no force feedback, and GameInput only does rumble.
 *
 * Polled once per frame on the core ticker (runs while paused, so the
 * rebind menu can capture). Force feedback: one constant force on the
 * steering axis (the sim's rim torque, see UODVehicleMovement::
 * ConsumeFfbRimNm), plus a device-side damper and a soft-lock spring
 * whose deadband ends at the car's own steering lock - device-side so
 * neither depends on frame rate. A watchdog zeroes the force if nobody
 * calls SetForce for 100 ms (pause, menu, alt-tab, crash in the pawn).
 */
class OPENDRIVEUE_API FODWheelDevice
{
public:
	/** Idempotent: registers the ticker on first call. */
	static void Start();

	/** (VID << 16) | PID of the primary wheel (first with force feedback,
	 *  else the first driving device); 0 = none connected. */
	static uint32 PrimaryVidPid();

	/** Axis in [-1,1] / button state; Device 0 = the primary wheel. Unbound
	 *  or disconnected reads 0 / false. */
	static float Axis(uint32 Device, int32 Index);
	static bool Button(uint32 Device, int32 Index);
	/** Press edge this frame. */
	static bool Pressed(uint32 Device, int32 Index);

	/** Seconds (FPlatformTime) of the last real input on any device: an axis
	 *  moving or a button press. 0 = never. Drives Auto device switching. */
	static double LastActivityTime();

	/** Rebind capture: BeginCapture snapshots every axis; PollCapture* return
	 *  true once an axis travelled 0.5 from rest (bInvert from the direction,
	 *  bHalf when it rested near centre) or a new button went down. */
	static void BeginCapture();
	static bool PollCaptureAxis(FODWheelBind& Out);
	static bool PollCaptureButton(FODWheelBind& Out);

	/** Command [-1,1] (+ = toward right steer before the profile's invert,
	 *  which FODRacingWheel::FfbCommand already applied), damper [0,1], soft lock
	 *  deadband fraction (1 = off). Call every frame while driving. */
	static void SetForce(float Command, float Damping, float SoftLockFrac);
};
