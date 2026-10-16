#include "ODRebindMenu.h"

#include "Components/InputComponent.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "ODPauseMenu.h"
#include "ODRaceHUD.h"
#include "ODVehiclePawn.h"
#include "ODWheelDevice.h"

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

}

AODRebindMenu::AODRebindMenu()
{
	// ticks only while capturing a wheel cell (polls the device)
	PrimaryActorTick.bCanEverTick = true;
	PrimaryActorTick.bStartWithTickEnabled = false;
	SetTickableWhenPaused(true);
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

const TCHAR* AODRebindMenu::ColumnHeader(int32 Col)
{
	return Col == 0 ? TEXT("KEYBOARD") : Col == 1 ? TEXT("GAMEPAD") : TEXT("WHEEL");
}

FODWheelBind* AODRebindMenu::WheelBind(int32 Row, bool& bAxis) const
{
	FODWheelProfile& P = FODRacingWheel::Profile();
	bAxis = false;
	const int32 W = Row - FODInputConfig::NumActions;
	if (W >= 0 && W < NumWheelRows)
	{
		bAxis = W < static_cast<int32>(EODWheelAxis::Count);
		return bAxis ? &P.Axes[W]
			: &P.Buttons[static_cast<int32>(EODWheelButton::Gear1) + W - static_cast<int32>(EODWheelAxis::Count)];
	}
	switch (static_cast<EODInputAction>(Row))
	{
	case EODInputAction::Handbrake: return &P.Buttons[static_cast<int32>(EODWheelButton::Handbrake)];
	case EODInputAction::ResetVehicle: return &P.Buttons[static_cast<int32>(EODWheelButton::Reset)];
	case EODInputAction::ShiftUp: return &P.Buttons[static_cast<int32>(EODWheelButton::ShiftUp)];
	case EODInputAction::ShiftDown: return &P.Buttons[static_cast<int32>(EODWheelButton::ShiftDown)];
	default: return nullptr;
	}
}

bool AODRebindMenu::HasCell(int32 Row, int32 Col) const
{
	bool bAxis = false;
	return Col == 2 ? IsBindingRow(Row) && WheelBind(Row, bAxis) != nullptr
		: Col >= 0 && Col < 2 && Row >= 0 && Row < FODInputConfig::NumActions;
}

int32 AODRebindMenu::CellCol(int32 Row, int32 Col) const
{
	for (int32 d = 0; d < NumCols; ++d)
	{
		if (HasCell(Row, Col - d))
		{
			return Col - d;
		}
		if (HasCell(Row, Col + d))
		{
			return Col + d;
		}
	}
	return Col;
}

void AODRebindMenu::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	bool bAxis = false;
	FODWheelBind* Target = bCapturing ? WheelBind(CapturingRow / NumCols, bAxis) : nullptr;
	if (!Target || CapturingRow % NumCols != 2)
	{
		SetActorTickEnabled(false);
		return;
	}
	FODWheelBind Got;
	if (bAxis ? FODWheelDevice::PollCaptureAxis(Got) : FODWheelDevice::PollCaptureButton(Got))
	{
		*Target = Got;
		FODRacingWheel::Save();
		EndCapture();
	}
}

void AODRebindMenu::MoveSelection(int32 Dir)
{
	if (bCapturing)
	{
		return;   // modal while waiting for a key press - see class comment
	}
	Selected = (Selected + Dir + NumRows()) % NumRows();
}

void AODRebindMenu::AdjustSelected(int32 Dir)
{
	if (!bCapturing && IsBindingRow(Selected))
	{
		// step over columns this row has no cell in
		for (int32 C = GetColumn() + Dir; C >= 0 && C < NumCols; C += Dir)
		{
			if (HasCell(Selected, C))
			{
				Column = C;
				break;
			}
		}
	}
}

void AODRebindMenu::SelectCell(int32 Row, int32 Col)
{
	if (bCapturing || Row < 0 || Row >= NumRows())
	{
		return;
	}
	Selected = Row;
	if (Col >= 0)
	{
		Column = FMath::Clamp(Col, 0, NumCols - 1);
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
		BeginCapture(Selected * NumCols + GetColumn());
		return false;
	}
	if (Selected == ResetRow())
	{
		FODInputConfig::ResetToDefaults();
		FODRacingWheel::ResetProfile();
		RefreshAllLiveInputBindings(GetWorld());
		return false;
	}
	return Selected == BackRow();
}

void AODRebindMenu::CancelCapture()
{
	if (bCapturing)
	{
		EndCapture();
	}
}

void AODRebindMenu::BeginCapture(int32 Slot)
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
	CapturingRow = Slot;
	bCapturing = true;

	// Escape cancels capture instead of being assignable - bound separately,
	// consuming, so it never reaches AODPauseMenu's own Escape handler while
	// we're capturing.
	FInputKeyBinding& EscB = InputComponent->BindKey(EKeys::Escape, IE_Pressed, this, &AODRebindMenu::OnCaptureKey);
	EscB.bConsumeInput = true;
	EscB.bExecuteWhenPaused = true;

	if (Slot % NumCols == 2)
	{
		// wheel: polled in Tick from the device, keys only for Escape
		FODWheelDevice::Start();
		FODWheelDevice::BeginCapture();
		SetActorTickEnabled(true);
		return;
	}
	const bool bGamepadSlot = (Slot % NumCols) == 1;
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
	CaptureEndFrame = GFrameCounter;
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
	if (CapturingRow % NumCols == 2)
	{
		return;   // wheel capture takes no keys but Escape
	}
	const int32 ActionIdx = CapturingRow / NumCols;
	const bool bGamepadSlot = (CapturingRow % NumCols) == 1;
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

FString AODRebindMenu::GetLabel(int32 Row) const
{
	if (Row >= 0 && Row < FODInputConfig::NumActions)
	{
		return FODInputConfig::ActionName(static_cast<EODInputAction>(Row));
	}
	const int32 W = Row - FODInputConfig::NumActions;
	if (W >= 0 && W < NumWheelRows)
	{
		return W < static_cast<int32>(EODWheelAxis::Count)
			? FString(FODRacingWheel::AxisLabel(static_cast<EODWheelAxis>(W))) + TEXT(" axis")
			: FString(FODRacingWheel::ButtonName(static_cast<EODWheelButton>(
				static_cast<int32>(EODWheelButton::Gear1) + W - static_cast<int32>(EODWheelAxis::Count))));
	}
	return Row == ResetRow() ? TEXT("Reset to Defaults") : Row == BackRow() ? TEXT("Back") : TEXT("");
}

FString AODRebindMenu::GetBinding(int32 Row, int32 Col) const
{
	if (!HasCell(Row, Col))
	{
		return FString();
	}
	if (IsCapturingCell(Row, Col))
	{
		return Col == 2 ? TEXT("Move / press...") : TEXT("Press a key...");
	}
	if (Col == 2)
	{
		bool bAxis = false;
		return FODRacingWheel::BindName(*WheelBind(Row, bAxis), bAxis);
	}
	const EODInputAction Action = static_cast<EODInputAction>(Row);
	const FKey Key = Col == 1 ? FODInputConfig::GetGamepadKey(Action) : FODInputConfig::GetKeyboardKey(Action);
	return Key.IsValid() ? Key.GetDisplayName(false).ToString() : TEXT("-");
}
