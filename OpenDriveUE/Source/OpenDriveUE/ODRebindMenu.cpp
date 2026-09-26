#include "ODRebindMenu.h"

#include "Components/InputComponent.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "ODPauseMenu.h"
#include "ODRaceHUD.h"
#include "ODVehiclePawn.h"

namespace
{
// Arc 4.1 (SS7.32): pushes a just-changed binding out to every OD* actor
// that binds one of the rebindable actions, live, without a restart -
// mirrors AODSettingsMenu::ApplyAudioVolumes' "find the one live instance
// and push the new value" idiom (TActorIterator, same as everywhere else in
// this project that needs "the one instance of X on this level").
void RefreshAllLiveInputBindings(UWorld* World)
{
	if (!World)
	{
		return;
	}
	if (AODVehiclePawn* Pawn = Cast<AODVehiclePawn>(UGameplayStatics::GetPlayerPawn(World, 0)))
	{
		Pawn->RefreshODManagedKeyBindings();
	}
	for (TActorIterator<AODPauseMenu> It(World); It; ++It)
	{
		It->RefreshPauseKeyBinding();
	}
	for (TActorIterator<AODRaceHUD> It(World); It; ++It)
	{
		It->RefreshODManagedKeyBindings();
	}
}
}

namespace
{
// Candidate keys offered to key-capture (Arc 4.1, SS7.32): a broad but
// bounded fixed list rather than trying to enumerate every possible FKey -
// covers every practical keyboard/gamepad binding a driving game needs.
// Deliberately EXCLUDES Escape (reserved - cancels capture instead, see
// AODRebindMenu::OnCaptureKey), Up/Down/Left/Right/Enter (the menu's own
// always-on navigation keys - offering them here would let a capture steal
// the very keys needed to navigate out of this screen), and V (dev-facing
// CycleVehicle - unconditional, not routed through FODInputConfig at all,
// see ODVehiclePawn.cpp/SS7.32 - offering V here would let a rebind silently
// double-bind it against that hardcoded dev hotkey).
const FKey KeyboardCandidates[] = {
	EKeys::A, EKeys::B, EKeys::C, EKeys::D, EKeys::E, EKeys::F, EKeys::G,
	EKeys::H, EKeys::I, EKeys::J, EKeys::K, EKeys::L, EKeys::M, EKeys::N,
	EKeys::O, EKeys::P, EKeys::Q, EKeys::R, EKeys::S, EKeys::T, EKeys::U,
	EKeys::W, EKeys::X, EKeys::Y, EKeys::Z,
	EKeys::Zero, EKeys::One, EKeys::Two, EKeys::Three, EKeys::Four,
	EKeys::Five, EKeys::Six, EKeys::Seven, EKeys::Eight, EKeys::Nine,
	EKeys::SpaceBar, EKeys::Tab, EKeys::CapsLock, EKeys::LeftShift,
	EKeys::RightShift, EKeys::LeftControl, EKeys::RightControl,
	EKeys::LeftAlt, EKeys::RightAlt, EKeys::Backslash, EKeys::Slash,
	EKeys::Semicolon, EKeys::Quote, EKeys::Comma, EKeys::Period,
	EKeys::LeftBracket, EKeys::RightBracket, EKeys::Hyphen, EKeys::Equals,
	EKeys::Tilde, EKeys::Insert, EKeys::Delete, EKeys::Home, EKeys::End,
	EKeys::PageUp, EKeys::PageDown, EKeys::BackSpace,
	EKeys::F1, EKeys::F2, EKeys::F3, EKeys::F4, EKeys::F5, EKeys::F6,
	EKeys::F7, EKeys::F8, EKeys::F9, EKeys::F10, EKeys::F11, EKeys::F12,
	EKeys::LeftMouseButton, EKeys::RightMouseButton, EKeys::MiddleMouseButton,
	EKeys::ThumbMouseButton, EKeys::ThumbMouseButton2,
};
const FKey GamepadCandidates[] = {
	EKeys::Gamepad_FaceButton_Bottom, EKeys::Gamepad_FaceButton_Right,
	EKeys::Gamepad_FaceButton_Top, EKeys::Gamepad_FaceButton_Left,
	EKeys::Gamepad_LeftShoulder, EKeys::Gamepad_RightShoulder,
	EKeys::Gamepad_LeftTrigger, EKeys::Gamepad_RightTrigger,
	EKeys::Gamepad_Special_Left, EKeys::Gamepad_Special_Right,
	EKeys::Gamepad_LeftThumbstick, EKeys::Gamepad_RightThumbstick,
	EKeys::Gamepad_DPad_Up, EKeys::Gamepad_DPad_Down,
	EKeys::Gamepad_DPad_Left, EKeys::Gamepad_DPad_Right,
};

const TCHAR* SliderLabel(int32 SliderIdx)
{
	switch (SliderIdx)
	{
	case 0: return TEXT("Steering Deadzone");
	case 1: return TEXT("Steering Sensitivity");
	case 2: return TEXT("Throttle/Brake Deadzone");
	default: return TEXT("?");
	}
}
}

AODRebindMenu::AODRebindMenu()
{
	PrimaryActorTick.bCanEverTick = false;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODRebindMenu::MoveSelection(int32 Dir)
{
	if (bCapturing)
	{
		return;   // modal while waiting for a key press - see class comment
	}
	Selected = (Selected + Dir + NumRows()) % NumRows();
	EnsureScrollVisible();
}

void AODRebindMenu::AdjustSelected(int32 Dir)
{
	if (bCapturing)
	{
		return;
	}
	if (!IsSliderRow(Selected))
	{
		return;
	}
	const int32 SliderIdx = Selected - FODInputConfig::NumActions * 2;
	switch (SliderIdx)
	{
	case 0:
		FODInputConfig::SetSteeringDeadzone(FODInputConfig::GetSteeringDeadzone() + Dir * 0.05f);
		break;
	case 1:
		FODInputConfig::SetSteeringSensitivity(FODInputConfig::GetSteeringSensitivity() + Dir * 0.25f);
		break;
	case 2:
		FODInputConfig::SetThrottleBrakeDeadzone(FODInputConfig::GetThrottleBrakeDeadzone() + Dir * 0.05f);
		break;
	default:
		break;
	}
}

bool AODRebindMenu::ActivateSelected()
{
	if (bCapturing)
	{
		return false;   // swallow stray Enter presses while capturing
	}
	if (IsBindingRow(Selected))
	{
		BeginCapture(Selected);
		return false;
	}
	if (IsSliderRow(Selected))
	{
		AdjustSelected(+1);   // "Enter cycles a value row", same idiom as the other menus
		return false;
	}
	if (Selected == ResetRow())
	{
		FODInputConfig::ResetToDefaults();
		RefreshAllLiveInputBindings(GetWorld());
		return false;
	}
	if (Selected == BackRow())
	{
		return true;
	}
	return false;
}

void AODRebindMenu::CancelCapture()
{
	if (bCapturing)
	{
		EndCapture();
	}
}

void AODRebindMenu::BeginCapture(int32 Row)
{
	APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0);
	if (!PC)
	{
		return;
	}
	EnableInput(PC);   // pushes a fresh InputComponent to the TOP of the input
	                   // stack - see class comment for why this is the one
	                   // exception to the "driven by the parent" idiom
	if (!InputComponent)
	{
		return;
	}
	CaptureInput = InputComponent;
	CapturingRow = Row;
	bCapturing = true;

	// Escape cancels capture instead of being assignable - bound separately,
	// consuming, so it never reaches AODPauseMenu's own Escape handler while
	// we're capturing.
	FInputKeyBinding& EscB = InputComponent->BindKey(EKeys::Escape, IE_Pressed, this, &AODRebindMenu::OnCaptureKey);
	EscB.bConsumeInput = true;
	EscB.bExecuteWhenPaused = true;

	const bool bGamepadSlot = (Row % 2) == 1;
	if (bGamepadSlot)
	{
		for (const FKey& K : GamepadCandidates)
		{
			FInputKeyBinding& B = InputComponent->BindKey(K, IE_Pressed, this, &AODRebindMenu::OnCaptureKey);
			B.bConsumeInput = true;
			B.bExecuteWhenPaused = true;
		}
	}
	else
	{
		for (const FKey& K : KeyboardCandidates)
		{
			FInputKeyBinding& B = InputComponent->BindKey(K, IE_Pressed, this, &AODRebindMenu::OnCaptureKey);
			B.bConsumeInput = true;
			B.bExecuteWhenPaused = true;
		}
	}
}

void AODRebindMenu::EndCapture()
{
	if (APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0))
	{
		DisableInput(PC);
	}
	CaptureInput = nullptr;
	bCapturing = false;
	CapturingRow = -1;
}

void AODRebindMenu::OnCaptureKey(FKey Key)
{
	if (!bCapturing)
	{
		return;
	}
	if (Key == EKeys::Escape)
	{
		EndCapture();   // cancel, no assignment - see class comment
		return;
	}
	const int32 ActionIdx = CapturingRow / 2;
	const bool bGamepadSlot = (CapturingRow % 2) == 1;
	const EODInputAction Action = static_cast<EODInputAction>(ActionIdx);

	// device-class guard: a gamepad-slot capture only accepts a gamepad key
	// and vice versa (see BeginCapture's separate candidate lists) - this
	// should be unreachable since each slot only binds its own candidate
	// list, but keep the check as a hard guarantee against a future
	// candidate-list edit accidentally mixing the two.
	if (bGamepadSlot != Key.IsGamepadKey())
	{
		return;
	}

	if (bGamepadSlot)
	{
		FODInputConfig::AssignGamepadKey(Action, Key);
	}
	else
	{
		FODInputConfig::AssignKeyboardKey(Action, Key);
	}
	RefreshAllLiveInputBindings(GetWorld());
	EndCapture();
}

void AODRebindMenu::EnsureScrollVisible()
{
	if (Selected < ScrollOffset)
	{
		ScrollOffset = Selected;
	}
	else if (Selected >= ScrollOffset + VisibleLines)
	{
		ScrollOffset = Selected - VisibleLines + 1;
	}
	ScrollOffset = FMath::Clamp(ScrollOffset, 0, FMath::Max(0, NumRows() - VisibleLines));
}

void AODRebindMenu::GetRow(int32 Row, FString& OutLabel, FString& OutValue) const
{
	if (IsBindingRow(Row))
	{
		const int32 ActionIdx = Row / 2;
		const bool bGamepadSlot = (Row % 2) == 1;
		const EODInputAction Action = static_cast<EODInputAction>(ActionIdx);
		OutLabel = FString::Printf(TEXT("%s (%s)"), FODInputConfig::ActionName(Action),
			bGamepadSlot ? TEXT("Pad") : TEXT("Key"));
		const FKey Key = bGamepadSlot ? FODInputConfig::GetGamepadKey(Action) : FODInputConfig::GetKeyboardKey(Action);
		OutValue = Key.IsValid() ? Key.ToString() : TEXT("(unbound)");
		if (bCapturing && Row == CapturingRow)
		{
			OutValue = TEXT("press a key... (Esc cancels)");
		}
		return;
	}
	if (IsSliderRow(Row))
	{
		const int32 SliderIdx = Row - FODInputConfig::NumActions * 2;
		OutLabel = SliderLabel(SliderIdx);
		switch (SliderIdx)
		{
		case 0: OutValue = FString::Printf(TEXT("%.2f"), FODInputConfig::GetSteeringDeadzone()); break;
		case 1: OutValue = FString::Printf(TEXT("%.2f"), FODInputConfig::GetSteeringSensitivity()); break;
		case 2: OutValue = FString::Printf(TEXT("%.2f"), FODInputConfig::GetThrottleBrakeDeadzone()); break;
		default: OutValue.Empty(); break;
		}
		return;
	}
	if (Row == ResetRow())
	{
		OutLabel = TEXT("Reset to Defaults");
		OutValue.Empty();
		return;
	}
	if (Row == BackRow())
	{
		OutLabel = TEXT("Back");
		OutValue.Empty();
		return;
	}
	OutLabel.Empty();
	OutValue.Empty();
}
