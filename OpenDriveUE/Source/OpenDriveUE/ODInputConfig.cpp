#include "ODInputConfig.h"

#include "Misc/ConfigCacheIni.h"

namespace
{
const TCHAR* InputConfigSection = TEXT("OpenDrive.Input");

struct FActionMeta
{
	EODInputAction Action;
	const TCHAR* Name;      // display + GConfig key stem
	FKey DefaultKeyboard;
	FKey DefaultGamepad;    // FKey() (invalid) == no default gamepad bind
};

const FActionMeta ActionTable[] = {
	{EODInputAction::Throttle,    TEXT("Throttle"),     EKeys::W,         FKey()},
	{EODInputAction::Brake,       TEXT("Brake"),        EKeys::S,         FKey()},
	{EODInputAction::SteerLeft,   TEXT("SteerLeft"),    EKeys::A,         FKey()},
	{EODInputAction::SteerRight,  TEXT("SteerRight"),   EKeys::D,         FKey()},
	{EODInputAction::Handbrake,   TEXT("Handbrake"),    EKeys::SpaceBar,  EKeys::Gamepad_FaceButton_Bottom},
	{EODInputAction::ResetVehicle,TEXT("ResetVehicle"), EKeys::R,         FKey()},
	{EODInputAction::Pause,       TEXT("Pause"),        EKeys::P,         FKey()},
	{EODInputAction::ToggleMap,   TEXT("ToggleMap"),    EKeys::M,         FKey()},
	{EODInputAction::ZoomMapIn,   TEXT("ZoomMapIn"),    EKeys::RightBracket, FKey()},
	{EODInputAction::ZoomMapOut,  TEXT("ZoomMapOut"),   EKeys::LeftBracket,  FKey()},
	{EODInputAction::WorldMap,    TEXT("WorldMap"),     EKeys::Tab,       EKeys::Gamepad_Special_Left},
	{EODInputAction::ClearRoute,  TEXT("ClearRoute"),   EKeys::BackSpace, FKey()},
	{EODInputAction::CameraView,  TEXT("CameraView"),   EKeys::C,         EKeys::Gamepad_FaceButton_Top},
	{EODInputAction::ShiftUp,     TEXT("ShiftUp"),      EKeys::E,         FKey()},
	{EODInputAction::ShiftDown,   TEXT("ShiftDown"),    EKeys::Q,         FKey()},
	{EODInputAction::Horn,        TEXT("Horn"),         EKeys::H,         FKey()},
};
static_assert(UE_ARRAY_COUNT(ActionTable) == FODInputConfig::NumActions,
	"ActionTable must have one entry per EODInputAction");

const FActionMeta& MetaFor(EODInputAction Action)
{
	return ActionTable[static_cast<int32>(Action)];
}
}

bool FODInputConfig::bLoaded = false;
FODInputBinding FODInputConfig::Bindings[FODInputConfig::NumActions];
float FODInputConfig::SteeringDeadzone = 0.15f;       // matches the pre-existing hardcoded GamepadDeadzone
float FODInputConfig::SteeringSensitivity = 2.0f;     // matches the pre-existing hardcoded x*|x| curve
float FODInputConfig::ThrottleBrakeDeadzone = 0.15f;  // matches the pre-existing hardcoded GamepadDeadzone

const TCHAR* FODInputConfig::ActionName(EODInputAction Action)
{
	const int32 Idx = static_cast<int32>(Action);
	if (Idx < 0 || Idx >= NumActions)
	{
		return TEXT("?");
	}
	return ActionTable[Idx].Name;
}

FKey FODInputConfig::GetKeyboardKey(EODInputAction Action)
{
	EnsureLoaded();
	const int32 Idx = static_cast<int32>(Action);
	return (Idx >= 0 && Idx < NumActions) ? Bindings[Idx].Keyboard : FKey();
}

FKey FODInputConfig::GetGamepadKey(EODInputAction Action)
{
	EnsureLoaded();
	const int32 Idx = static_cast<int32>(Action);
	return (Idx >= 0 && Idx < NumActions) ? Bindings[Idx].Gamepad : FKey();
}

void FODInputConfig::AssignKeyboardKey(EODInputAction Action, FKey NewKey)
{
	EnsureLoaded();
	const int32 Idx = static_cast<int32>(Action);
	if (Idx < 0 || Idx >= NumActions || !NewKey.IsValid())
	{
		return;
	}
	// swap policy: whoever else has this key on the keyboard slot gets our
	// old key instead of being left unbound
	for (int32 i = 0; i < NumActions; ++i)
	{
		if (i != Idx && Bindings[i].Keyboard == NewKey)
		{
			Bindings[i].Keyboard = Bindings[Idx].Keyboard;
			break;
		}
	}
	Bindings[Idx].Keyboard = NewKey;
	SaveBindings();
}

void FODInputConfig::AssignGamepadKey(EODInputAction Action, FKey NewKey)
{
	EnsureLoaded();
	const int32 Idx = static_cast<int32>(Action);
	if (Idx < 0 || Idx >= NumActions || !NewKey.IsValid())
	{
		return;
	}
	for (int32 i = 0; i < NumActions; ++i)
	{
		if (i != Idx && Bindings[i].Gamepad == NewKey)
		{
			Bindings[i].Gamepad = Bindings[Idx].Gamepad;
			break;
		}
	}
	Bindings[Idx].Gamepad = NewKey;
	SaveBindings();
}

void FODInputConfig::ResetToDefaults()
{
	for (int32 i = 0; i < NumActions; ++i)
	{
		Bindings[i].Keyboard = ActionTable[i].DefaultKeyboard;
		Bindings[i].Gamepad = ActionTable[i].DefaultGamepad;
	}
	SteeringDeadzone = 0.15f;
	SteeringSensitivity = 2.0f;
	ThrottleBrakeDeadzone = 0.15f;
	bLoaded = true;
	SaveBindings();
	SaveSliders();
}

void FODInputConfig::SetSteeringDeadzone(float V)
{
	EnsureLoaded();
	SteeringDeadzone = FMath::Clamp(V, 0.0f, 0.9f);
	SaveSliders();
}

void FODInputConfig::SetSteeringSensitivity(float V)
{
	EnsureLoaded();
	SteeringSensitivity = FMath::Clamp(V, 0.25f, 4.0f);
	SaveSliders();
}

void FODInputConfig::SetThrottleBrakeDeadzone(float V)
{
	EnsureLoaded();
	ThrottleBrakeDeadzone = FMath::Clamp(V, 0.0f, 0.9f);
	SaveSliders();
}

float FODInputConfig::ConditionSteering(float Raw, float Deadzone, float Exponent)
{
	const float Mag = FMath::Abs(Raw);
	if (Mag <= Deadzone)
	{
		return 0.0f;
	}
	const float Denom = FMath::Max(1.0f - Deadzone, KINDA_SMALL_NUMBER);
	const float Rescaled = FMath::Clamp((Mag - Deadzone) / Denom, 0.0f, 1.0f);
	return FMath::Sign(Raw) * FMath::Pow(Rescaled, Exponent);
}

float FODInputConfig::ConditionThrottleBrake(float Raw, float Deadzone)
{
	const float Mag = FMath::Abs(Raw);
	if (Mag <= Deadzone)
	{
		return 0.0f;
	}
	const float Denom = FMath::Max(1.0f - Deadzone, KINDA_SMALL_NUMBER);
	const float Rescaled = FMath::Clamp((Mag - Deadzone) / Denom, 0.0f, 1.0f);
	return FMath::Sign(Raw) * Rescaled;
}

void FODInputConfig::EnsureLoaded()
{
	if (bLoaded)
	{
		return;
	}
	Load();
	bLoaded = true;
}

void FODInputConfig::Load()
{
	for (int32 i = 0; i < NumActions; ++i)
	{
		Bindings[i].Keyboard = ActionTable[i].DefaultKeyboard;
		Bindings[i].Gamepad = ActionTable[i].DefaultGamepad;

		FString KeyStr;
		if (GConfig->GetString(InputConfigSection,
			*FString::Printf(TEXT("%s_Keyboard"), ActionTable[i].Name), KeyStr, GGameUserSettingsIni)
			&& !KeyStr.IsEmpty())
		{
			const FKey Loaded(*KeyStr);
			if (Loaded.IsValid())
			{
				Bindings[i].Keyboard = Loaded;
			}
		}
		if (GConfig->GetString(InputConfigSection,
			*FString::Printf(TEXT("%s_Gamepad"), ActionTable[i].Name), KeyStr, GGameUserSettingsIni)
			&& !KeyStr.IsEmpty())
		{
			// "None" is a valid saved value meaning "explicitly unbound" -
			// distinct from "key absent from ini" (falls back to the
			// per-action default above)
			Bindings[i].Gamepad = (KeyStr == TEXT("None")) ? FKey() : FKey(*KeyStr);
		}
	}

	GConfig->GetFloat(InputConfigSection, TEXT("SteeringDeadzone"), SteeringDeadzone, GGameUserSettingsIni);
	GConfig->GetFloat(InputConfigSection, TEXT("SteeringSensitivity"), SteeringSensitivity, GGameUserSettingsIni);
	GConfig->GetFloat(InputConfigSection, TEXT("ThrottleBrakeDeadzone"), ThrottleBrakeDeadzone, GGameUserSettingsIni);
	SteeringDeadzone = FMath::Clamp(SteeringDeadzone, 0.0f, 0.9f);
	SteeringSensitivity = FMath::Clamp(SteeringSensitivity, 0.25f, 4.0f);
	ThrottleBrakeDeadzone = FMath::Clamp(ThrottleBrakeDeadzone, 0.0f, 0.9f);
}

void FODInputConfig::SaveBindings()
{
	for (int32 i = 0; i < NumActions; ++i)
	{
		GConfig->SetString(InputConfigSection,
			*FString::Printf(TEXT("%s_Keyboard"), ActionTable[i].Name),
			*Bindings[i].Keyboard.ToString(), GGameUserSettingsIni);
		GConfig->SetString(InputConfigSection,
			*FString::Printf(TEXT("%s_Gamepad"), ActionTable[i].Name),
			Bindings[i].Gamepad.IsValid() ? *Bindings[i].Gamepad.ToString() : TEXT("None"),
			GGameUserSettingsIni);
	}
	GConfig->Flush(false, GGameUserSettingsIni);
}

void FODInputConfig::SaveSliders()
{
	GConfig->SetFloat(InputConfigSection, TEXT("SteeringDeadzone"), SteeringDeadzone, GGameUserSettingsIni);
	GConfig->SetFloat(InputConfigSection, TEXT("SteeringSensitivity"), SteeringSensitivity, GGameUserSettingsIni);
	GConfig->SetFloat(InputConfigSection, TEXT("ThrottleBrakeDeadzone"), ThrottleBrakeDeadzone, GGameUserSettingsIni);
	GConfig->Flush(false, GGameUserSettingsIni);
}
