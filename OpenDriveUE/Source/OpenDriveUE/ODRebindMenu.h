#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "InputCoreTypes.h"
#include "ODInputConfig.h"
#include "ODWheel.h"
#include "ODRebindMenu.generated.h"

class UInputComponent;

/**
 * Arc 4.1 control-rebinding screen (next steps.md SS4.1/SS7.32). Reached
 * from AODSettingsMenu's "Controls" row - one level deeper in the same
 * "driven entirely by whichever parent has it open" idiom as
 * AODSettingsMenu/AODCreditsMenu themselves (see AODSettingsMenu's class
 * comment): this is NOT an independent input-owning actor for
 * navigation - AODSettingsMenu checks its own bRebindOpen flag first and
 * routes Up/Down/Left/Right/Activate straight into MoveSelection/
 * AdjustSelected/ActivateSelected below.
 *
 * KEY CAPTURE is the one exception: while bCapturing is true, this actor
 * DOES own a real, freshly-pushed InputComponent (BeginCapture/EndCapture)
 * bound to a broad fixed candidate-key list, so it can intercept the very
 * next keyboard/gamepad press regardless of which key it is - something the
 * "forward through the parent's already-bound keys" idiom can't do, since
 * the parent only has Up/Down/Left/Right/Enter/Escape bound. PushInputComponent
 * puts it at the TOP of the input stack (highest priority, freshest push),
 * so it intercepts before AODPauseMenu's Escape or AODVehiclePawn's WASD see
 * the same press; every candidate key sets bConsumeInput so nothing leaks
 * through while capturing. Escape is never in the candidate list and instead
 * cancels capture outright (see OnCaptureKey) - Escape can never be assigned
 * to an action.
 *
 * Layout (2026-10-09): one row per action with Keyboard | Gamepad columns
 * (Left/Right or a click picks the column), then "Reset to Defaults" and
 * "Back". The rows + Reset scroll to follow the selection (ODRaceHUD::
 * DrawRebindPanel, 2026-10-14) - the action list outgrew one screen. The wheel-conditioning
 * sliders moved to AODSettingsMenu's CONTROLS tab.
 *
 * WHEEL column (Arc 6): a third column for the racing wheel's buttons on the
 * actions that have one (handbrake, reset, shift), then wheel-only rows for
 * the axes and the H-shifter slots. Wheel capture polls FODWheelDevice from
 * Tick (ticks while paused): the first axis pulled half its travel or the
 * first new button press, on any wheel/pedal/shifter device. Axis capture
 * reads direction and rest position, so inverted and combined pedals bind
 * right without extra settings.
 */
UCLASS()
class OPENDRIVEUE_API AODRebindMenu : public AActor
{
	GENERATED_BODY()

public:
	AODRebindMenu();
	virtual void Tick(float DeltaSeconds) override;

	static constexpr int32 NumWheelRows = static_cast<int32>(EODWheelAxis::Count)
		+ static_cast<int32>(EODWheelButton::GearR) - static_cast<int32>(EODWheelButton::Gear1) + 1;
	static constexpr int32 NumCols = 3;   // keyboard, gamepad, wheel
	int32 NumRows() const { return FODInputConfig::NumActions + NumWheelRows + 2; }
	int32 ResetRow() const { return FODInputConfig::NumActions + NumWheelRows; }
	int32 BackRow() const { return ResetRow() + 1; }
	bool IsBindingRow(int32 Row) const { return Row >= 0 && Row < ResetRow(); }
	/** Whether Row has a cell in column Col (wheel-only rows: column 2 only;
	 *  actions without a wheel button: columns 0-1). */
	bool HasCell(int32 Row, int32 Col) const;
	static const TCHAR* ColumnHeader(int32 Col);
	int32 GetSelected() const { return Selected; }
	int32 GetColumn() const { return CellCol(Selected, Column); }   // 0 keyboard, 1 gamepad, 2 wheel
	bool IsCapturing() const { return bCapturing; }
	bool IsCapturingCell(int32 Row, int32 Col) const { return bCapturing && CapturingRow == Row * NumCols + Col; }
	/** True for the frame a capture ended on - that key press (maybe a
	 *  mouse click) must not also act on the menu. */
	bool CaptureJustEnded() const { return CaptureEndFrame != 0 && GFrameCounter - CaptureEndFrame <= 1; }

	void MoveSelection(int32 Dir);
	/** Left/Right: switch column on a binding row. */
	void AdjustSelected(int32 Dir);
	/** Mouse hover/click: select Row (and its column, Col >= 0). */
	void SelectCell(int32 Row, int32 Col);
	/** Enter: starts key capture on the selected cell, resets on the Reset
	 *  row. Returns true iff the "Back" row was selected - the caller
	 *  (AODSettingsMenu::Activate) closes this screen. */
	bool ActivateSelected();
	/** Cancels an in-progress capture - no-op if not capturing. */
	void CancelCapture();

	FString GetLabel(int32 Row) const;
	FString GetBinding(int32 Row, int32 Col) const;

private:
	/** Nearest column with a cell on Row, preferring Col. */
	int32 CellCol(int32 Row, int32 Col) const;
	/** The wheel bind behind (Row, column 2): an axis (bAxis) or a button. */
	FODWheelBind* WheelBind(int32 Row, bool& bAxis) const;
	void BeginCapture(int32 Slot);   // Slot = Row * NumCols + column
	void EndCapture();
	void OnCaptureKey(FKey Key);

	int32 Selected = 0;
	int32 Column = 0;

	bool bCapturing = false;
	int32 CapturingRow = -1;     // capture slot, Row * NumCols + column
	uint64 CaptureEndFrame = 0;
	UPROPERTY()
	TObjectPtr<UInputComponent> CaptureInput;
};
