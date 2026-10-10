#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "InputCoreTypes.h"
#include "ODPauseMenu.generated.h"

class UODVehicleMovement;
class AODSettingsMenu;

/**
 * In-game pause menu (ESC / P). Rows (2026-10-09 menu redesign): World Map,
 * Garage, Settings, Main Menu, Quit, then the "Resume" footer (opened
 * pre-selected). Driver assists, mini-map and credits moved into
 * AODSettingsMenu. Garage opens AODMainMenu's Garage screen in place
 * (AODMainMenu::OpenGarage()) without leaving the drive. Drawn on canvas by
 * ODRaceHUD, which also hit-tests the mouse and calls the public
 * SelectRow/SetTab/Activate/Adjust/Back/Up/Down below.
 *
 * Settings is a plain logic/state object, not an input-owning actor (see
 * AODSettingsMenu's class comment): every handler here checks bSettingsOpen
 * first and forwards, so there's no second input-priority stack.
 */
UCLASS()
class OPENDRIVEUE_API AODPauseMenu : public AActor
{
	GENERATED_BODY()

public:
	AODPauseMenu();
	virtual void BeginPlay() override;

	enum : int32 { RowWorldMap, RowGarage, RowSettings, RowMainMenu, RowQuit, RowResume, NumPauseRows };

	// --- HUD-facing ---
	bool IsOpen() const { return bOpen; }
	int32 GetSelected() const { return Selected; }
	int32 NumRows() const { return NumPauseRows; }
	int32 GetFooterRow() const { return RowResume; }
	void GetRow(int32 Row, FString& OutLabel, FString& OutValue) const;

	bool IsSettingsOpen() const { return bSettingsOpen; }
	AODSettingsMenu* GetSettingsMenu() const { return SettingsMenu; }

	/** Drop the pause overlay WITHOUT unpausing - the world map (SS7.37)
	 *  takes the screen over and owns the unpause when it closes. */
	void HideForOverlay() { bOpen = false; bSettingsOpen = false; }

	// keyboard handlers, also the HUD's mouse entry points
	void Up();
	void Down();
	void Activate();
	void Adjust(int32 Dir);
	/** Escape / right-click: back out one level (capture, rebind, credits,
	 *  settings), else resume. */
	void Back();
	void SelectRow(int32 Row, int32 Col = -1);
	void SetTab(int32 Tab);

	/** Re-derives the Pause key from FODInputConfig and rebuilds it - called
	 *  at BeginPlay and again live by AODRebindMenu after a rebind (Arc 4.1,
	 *  SS7.32). Escape is untouched (fixed, not rebindable). */
	void RefreshPauseKeyBinding();

private:
	void Toggle();
	void SetOpen(bool bInOpen);
	void Left() { Adjust(-1); }
	void Right() { Adjust(+1); }
	// Q/E (shoulders on pad) switch the Settings panel's tabs while it's open
	void PrevTab();
	void NextTab();

	FKey LastPauseKey;

	UODVehicleMovement* FindMovement() const;
	/** One-time Sport-gearbox shift params at BeginPlay - preserves the
	 *  current shift feel now that the gearbox row is gone (no UI). */
	void ApplyGearbox();

	AODSettingsMenu* EnsureSettingsMenu();

	bool bOpen = false;
	int32 Selected = RowResume;
	int32 GearboxIdx = 1;    // Sport (stage-1 shift defaults) - init only

	bool bSettingsOpen = false;
	UPROPERTY()
	TObjectPtr<AODSettingsMenu> SettingsMenu;
};
