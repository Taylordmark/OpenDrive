#pragma once

#include "CoreMinimal.h"
#include "InputCoreTypes.h"

/**
 * Arc 4.1 control rebinding (next steps.md SS4.1/SS7.32) - project-level
 * indirection over the existing legacy BindKey/BindAxisKey input path.
 *
 * MIGRATION DECISION (see SS7.32 for the full reasoning): this project has
 * DefaultPlayerInputClass=EnhancedPlayerInput in DefaultInput.ini and an
 * EnhancedInput module dependency in the .uproject/Build.cs, but NO actual
 * UInputAction/UInputMappingContext assets or IMC-based binding anywhere -
 * every bind site (AODVehiclePawn::SetupPlayerInputComponent/
 * SetupGamepadAndWheelInput, AODPauseMenu::BeginPlay, AODRaceHUD::BeginPlay,
 * AODRaceManager::BeginPlay, AODTuningManager::BeginPlay, AODMainMenu) uses
 * UInputComponent::BindKey/BindAxisKey directly against hardcoded FKey
 * literals. A real Enhanced Input migration (data assets, mapping contexts,
 * per-device IMCs) would touch every one of those files for a
 * keyboard+gamepad-only game with hand-drawn canvas menus and no
 * complex chorded/contextual input - high churn, no behavior change, and
 * against this project's stated no-framework philosophy (canvas over UMG,
 * C++ over MetaSounds). Per the task brief's explicit allowance, rebinding
 * is instead implemented as a project-level indirection layer: every bind
 * site asks THIS class for the current FKey instead of using a literal, and
 * re-derives its bindings live via a small "RefreshODManagedKeyBindings()"
 * style helper (see each class) whenever the rebind screen changes one.
 *
 * Pure static data layer - no UObject, no actor, no per-instance state.
 * Deliberately kept separate from any single OD* class so AODVehiclePawn,
 * AODPauseMenu, AODRaceHUD and the new AODRebindMenu can all depend on it
 * without depending on each other.
 *
 * Persistence: GConfig section "OpenDrive.Input" in GameUserSettings.ini,
 * same SetInt/GetInt + SetFloat/GetFloat + GConfig->Flush(false,
 * GGameUserSettingsIni) idiom as AODMainMenu::Save/LoadExhaustConfig's
 * "OpenDrive.Exhaust" (SS7.30) and AODSettingsMenu's "OpenDrive.Graphics"/
 * "OpenDrive.Audio" (SS7.31). LoadAll() is idempotent and safe to call
 * repeatedly (e.g. once per actor's BeginPlay) - missing keys fall back to
 * DefaultBindings so "no OpenDrive.Input section" reproduces today's
 * defaults exactly, same "missing section = today's defaults" guarantee as
 * those two precedents.
 */
enum class EODInputAction : uint8
{
	Throttle,
	Brake,
	SteerLeft,
	SteerRight,
	Handbrake,
	ResetVehicle,
	Pause,
	ToggleMap,
	ZoomMapIn,
	ZoomMapOut,
	WorldMap,     // SS7.37 full-screen world map + navigation
	ClearRoute,
	CameraView,   // cycle chase / far / near / cockpit / hood / bumper
	ShiftUp,      // manual gearbox (Arc 6); ignored in automatic
	ShiftDown,
	PhotoMode,    // fun #4: pause into photo mode (or back out)
	MediaPlayPause,   // music controls: OS media keys (ODMedia)
	MediaNext,
	MediaPrev,
	Replay,      // #9 instant replay
	Horn,         // fun-features #8a; traffic reacts (ODTrafficManager)
	Count  // sentinel, not a real action
};

struct FODInputBinding
{
	FKey Keyboard;
	FKey Gamepad;   // EKeys::Invalid == unbound on gamepad
};

class OPENDRIVEUE_API FODInputConfig
{
public:
	static constexpr int32 NumActions = static_cast<int32>(EODInputAction::Count);

	/** Human-readable display name for a row/log, e.g. "Steer Left". */
	static const TCHAR* ActionName(EODInputAction Action);

	static FKey GetKeyboardKey(EODInputAction Action);
	static FKey GetGamepadKey(EODInputAction Action);   // FKey() (invalid) if unbound

	/** Conflict policy: SWAP. If NewKey is already assigned to another
	 *  action's same slot (keyboard or gamepad), that other action receives
	 *  the key being displaced instead of being left unbound - two actions
	 *  can never silently point at the same physical key, and nothing is
	 *  ever left in a "clear and warn" dead state. Escape/Up/Down/Left/
	 *  Right/Enter are reserved (menu-navigation keys, never offered by the
	 *  capture candidate list - see AODRebindMenu) so they can never be
	 *  assigned here in the first place; no extra guard needed. Saves
	 *  immediately (every assignment is a GConfig write, same
	 *  "save on every adjustment" idiom as SS7.31). */
	static void AssignKeyboardKey(EODInputAction Action, FKey NewKey);
	static void AssignGamepadKey(EODInputAction Action, FKey NewKey);

	static void ResetToDefaults();

	/** Deadzone [0,1). Values at or below this magnitude condition to
	 *  exactly 0. */
	static float GetSteeringDeadzone() { return SteeringDeadzone; }
	static float GetSteeringSensitivity() { return SteeringSensitivity; }   // response exponent, >0
	static float GetThrottleBrakeDeadzone() { return ThrottleBrakeDeadzone; }
	static void SetSteeringDeadzone(float V);
	static void SetSteeringSensitivity(float V);
	static void SetThrottleBrakeDeadzone(float V);

	/** Signed steering conditioning: raw in [-1,1] -> conditioned in [-1,1].
	 *  |raw| <= Deadzone -> 0. Otherwise the remaining [Deadzone,1] range is
	 *  rescaled to [0,1] (continuous at the deadzone edge - no jump) and
	 *  raised to Exponent, sign preserved. Exponent=2 reproduces the
	 *  pre-existing hardcoded x*|x| gamepad-steer curve exactly (see
	 *  AODVehiclePawn::GamepadSteerAxis's prior implementation, now just a
	 *  call-through to this function with the persisted slider values). */
	static float ConditionSteering(float Raw, float Deadzone, float Exponent);
	/** Unsigned throttle/brake conditioning: raw in [0,1] -> conditioned in
	 *  [0,1], same deadzone-then-continuous-rescale shape, linear (no
	 *  exponent - the task brief only asks for a deadzone on this axis
	 *  class, not a curve). */
	static float ConditionThrottleBrake(float Raw, float Deadzone);

	/** Loads every binding + slider from GConfig (or defaults if unset).
	 *  Idempotent - safe to call from every actor's BeginPlay(); the first
	 *  call in a process does the real GConfig read, later calls are a
	 *  cheap early-out. */
	static void EnsureLoaded();

private:
	static void Load();
	static void SaveBindings();
	static void SaveSliders();

	static bool bLoaded;
	static FODInputBinding Bindings[NumActions];
	static float SteeringDeadzone;
	static float SteeringSensitivity;
	static float ThrottleBrakeDeadzone;
};
