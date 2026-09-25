#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "InputCoreTypes.h"
#include "ODInputConfig.h"
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
 * Row layout: one row per action per device slot (10 actions x 2 slots = 20
 * rows), then 3 slider rows (steering deadzone, steering sensitivity,
 * throttle/brake deadzone - Arc 6 wheel conditioning, see FODInputConfig),
 * then "Reset to Defaults", then "Back". Scrolls like AODCreditsMenu
 * (VisibleLines window, auto-follows Selected) since 25 rows don't fit one
 * screen.
 */
UCLASS()
class OPENDRIVEUE_API AODRebindMenu : public AActor
{
	GENERATED_BODY()

public:
	AODRebindMenu();

	static constexpr int32 VisibleLines = 12;

	int32 NumRows() const { return FODInputConfig::NumActions * 2 + 3 + 2; }
	int32 GetSelected() const { return Selected; }
	int32 GetScrollOffset() const { return ScrollOffset; }
	bool IsCapturing() const { return bCapturing; }
	int32 GetCapturingRow() const { return CapturingRow; }

	void MoveSelection(int32 Dir);
	/** Left/Right on a slider row adjusts its value; no-op on every other
	 *  row (binding rows are captured via Enter, not cycled via Left/Right -
	 *  see ActivateSelected). */
	void AdjustSelected(int32 Dir);
	/** Enter: starts key capture on a binding row, cycles a slider row
	 *  (Dir=+1, same "Enter cycles" idiom as the other menus), resets
	 *  bindings+sliders on the Reset row. Returns true iff the "Back" row
	 *  was selected - caller (AODSettingsMenu::CloseRebind, via the parent
	 *  menu) should close this screen. */
	bool ActivateSelected();
	/** Cancels an in-progress capture (e.g. the parent's Escape handler
	 *  should call this first before falling through) - no-op if not
	 *  capturing. */
	void CancelCapture();

	void GetRow(int32 Row, FString& OutLabel, FString& OutValue) const;

private:
	bool IsBindingRow(int32 Row) const { return Row < FODInputConfig::NumActions * 2; }
	bool IsSliderRow(int32 Row) const
	{
		const int32 Base = FODInputConfig::NumActions * 2;
		return Row >= Base && Row < Base + 3;
	}
	int32 ResetRow() const { return FODInputConfig::NumActions * 2 + 3; }
	int32 BackRow() const { return FODInputConfig::NumActions * 2 + 4; }

	void BeginCapture(int32 Row);
	void EndCapture();
	void OnCaptureKey(FKey Key);
	void EnsureScrollVisible();

	int32 Selected = 0;
	int32 ScrollOffset = 0;

	bool bCapturing = false;
	int32 CapturingRow = -1;
	UPROPERTY()
	TObjectPtr<UInputComponent> CaptureInput;
};
