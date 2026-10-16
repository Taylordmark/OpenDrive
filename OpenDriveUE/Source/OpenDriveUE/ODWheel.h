#pragma once

#include "CoreMinimal.h"

/**
 * Arc 6, wheel and sim rig (next steps.md SS8): racing wheel / pedals /
 * shifter profiles and the pure input + force-feedback math. No Windows
 * types here - the DirectInput device lives in ODWheelDevice.h - so the
 * whole pipeline is provable without hardware (OpenDrive.Wheel.SelfCheck,
 * bottom of ODWheel.cpp).
 *
 * Axis pipeline (device value v in [-1,1], what DirectInput reports with
 * DIPROP_RANGE -10000..10000):
 *   steering: s*v * RotationDeg/2 = wheel angle -> / car steering ratio =
 *     road-wheel angle -> / car lock = steer input, clamped (1:1 with the
 *     car - the soft lock stops the rim where the car's rack stops), then
 *     deadzone and the linearity exponent.
 *   pedal, full axis (separate pedals, one axis each): (s*v + 1)/2.
 *   pedal, half axis (Logitech "combined pedals": throttle one way, brake
 *     the other from a centred rest): max(s*v, 0).
 *   s = -1 when the bind is inverted. Capture (AODRebindMenu) sets s and
 *   half/full from which way the axis moved and where it rested.
 *
 * Profiles are per device (VID/PID), saved in GameUserSettings.ini section
 * "OpenDrive.Wheel.VVVV_PPPP"; the Logitech G29/G920/G923 and Fanatec
 * defaults are in DefaultProfile(). A bind names its device, so separate
 * USB pedals or a USB shifter work alongside the wheel.
 */
enum class EODWheelAxis : uint8
{
	Steer,
	Throttle,
	Brake,
	Clutch,
	Count
};

enum class EODWheelButton : uint8
{
	ShiftUp,
	ShiftDown,
	Handbrake,
	Reset,
	Gear1, Gear2, Gear3, Gear4, Gear5, Gear6, Gear7,
	GearR,
	Count
};

struct FODWheelBind
{
	uint32 Device = 0;     // (VID << 16) | PID; 0 = the primary wheel
	int32 Index = -1;      // axis 0..7 (X Y Z Rx Ry Rz S0 S1) or button; -1 unbound
	bool bInvert = false;
	bool bHalf = false;    // axis only: centred half-axis (combined pedals)
	bool IsBound() const { return Index >= 0; }
};

struct OPENDRIVEUE_API FODWheelProfile
{
	float RotationDeg = 900.0f;   // the wheel's own lock-to-lock, as set in its driver
	float SteerDeadzone = 0.0f;
	float Linearity = 1.0f;       // steering response exponent, 1 = linear
	float PedalDeadzone = 0.03f;
	float FfbStrength = 0.75f;    // [0,1.5], 0 = off
	float FfbDamping = 0.15f;     // device-side damper [0,1]
	bool bFfbInvert = false;      // constant-force sign differs by vendor
	FODWheelBind Axes[static_cast<int32>(EODWheelAxis::Count)];
	FODWheelBind Buttons[static_cast<int32>(EODWheelButton::Count)];
};

struct FODWheelInputs
{
	float Steer = 0.0f;      // [-1,1] of the car's lock
	float Throttle = 0.0f;   // [0,1]
	float Brake = 0.0f;
	float Clutch = 0.0f;     // 1 = pedal down (disengaged)
};

class OPENDRIVEUE_API FODRacingWheel
{
public:
	// ---- pure pipeline (no state; the self-check drives these directly) ----
	static float Pedal(float V, const FODWheelBind& B, float Deadzone);
	/** CarLockDeg = the car's lock-to-lock at the steering wheel. */
	static float Steer(float V, const FODWheelBind& B, float RotationDeg, float CarLockDeg,
		float Deadzone, float Linearity);
	/** Soft-lock spring deadband as a fraction of the rim's travel: the
	 *  spring engages past the car's own lock. 1 = no soft lock (the rim's
	 *  hardware stop comes first). */
	static float SoftLockFrac(float RotationDeg, float CarLockDeg);
	/** Rim torque (Nm, + = toward right steer) -> constant-force command
	 *  [-1,1]: strength, invert, soft knee over the top 20%. */
	static float FfbCommand(float RimNm, float Strength, bool bInvert);
	/** Rim torque that maps to 100% at strength 1. */
	static constexpr float FfbFullScaleNm = 20.0f;

	static FODWheelProfile DefaultProfile(uint32 VidPid);
	static FString DeviceKey(uint32 VidPid);   // "046D_C24F"
	static FString AxisName(int32 Index);
	static FString BindName(const FODWheelBind& B, bool bAxis);
	static const TCHAR* ButtonName(EODWheelButton B);
	static const TCHAR* AxisLabel(EODWheelAxis A);

	// ---- the active profile (follows the primary connected wheel) ----
	/** Loads the profile for VidPid (0 = no wheel: generic defaults) if it
	 *  is not already the active one. Cheap to call every frame. */
	static void UseDevice(uint32 VidPid);
	static FODWheelProfile& Profile() { return Active; }
	static uint32 ActiveDevice() { return ActiveVidPid; }
	static void Save();
	static void ResetProfile();

	/** Gearbox: false = automatic, true = manual (paddles/sequential keys,
	 *  H-shifter, clutch). Global, not per device - keyboard can shift too. */
	static bool IsManualGearbox();
	static void SetManualGearbox(bool bManual);

private:
	static void Load(uint32 VidPid);
	static FODWheelProfile Active;
	static uint32 ActiveVidPid;
	static bool bActiveLoaded;
	static int32 ManualGearbox;   // -1 = not read yet
};
