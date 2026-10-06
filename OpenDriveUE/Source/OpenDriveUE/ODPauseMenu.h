#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "InputCoreTypes.h"
#include "ODPauseMenu.generated.h"

class UODVehicleMovement;
class AODSettingsMenu;
class AODCreditsMenu;

/**
 * In-game pause menu (ESC / P). Deliberately minimal (2026-07-25, Taylor's
 * call): the two driver assists - Traction Control and ABS - are the only
 * settings changed directly here. Everything that defines the car itself
 * (car, engine, drivetrain, tuning) still lives in AODMainMenu's Garage
 * screen; the "Garage" row opens that screen in place
 * (AODMainMenu::OpenGarage()) without leaving the drive. Rows are
 * Resume, World Map (SS7.37 - hands the screen to AODRaceHUD's world map),
 * Traction, ABS, Garage, Settings, Credits, Main Menu (back to the
 * front-end), Quit. Drawn on canvas by ODRaceHUD; arrows navigate/change,
 * Enter activates.
 *
 * Settings/Credits (Arc 4.4, next steps.md SS4.4/SS7.31): AODSettingsMenu/
 * AODCreditsMenu are plain logic/state objects, not independent input-owning
 * actors (see AODSettingsMenu's class comment for why) - this actor's own
 * Up()/Down()/Left()/Right()/Activate()/Toggle() check bSettingsOpen/
 * bCreditsOpen FIRST and route straight into them instead of running the
 * normal pause-row logic, all inside the SAME already-bound key handlers, so
 * there's no second input-priority stack to reason about.
 */
UCLASS()
class OPENDRIVEUE_API AODPauseMenu : public AActor
{
	GENERATED_BODY()

public:
	AODPauseMenu();
	virtual void BeginPlay() override;

	// --- HUD-facing ---
	bool IsOpen() const { return bOpen; }
	int32 GetSelected() const { return Selected; }
	int32 NumRows() const { return 9; }
	void GetRow(int32 Row, FString& OutLabel, FString& OutValue) const;

	bool IsSettingsOpen() const { return bSettingsOpen; }
	bool IsCreditsOpen() const { return bCreditsOpen; }
	AODSettingsMenu* GetSettingsMenu() const { return SettingsMenu; }
	AODCreditsMenu* GetCreditsMenu() const { return CreditsMenu; }

	/** Drop the pause overlay WITHOUT unpausing - the world map (SS7.37)
	 *  takes the screen over and owns the unpause when it closes. */
	void HideForOverlay() { bOpen = false; bSettingsOpen = false; bCreditsOpen = false; }

private:
	void Toggle();
	void Up();
	void Down();
	void Left();
	void Right();
	void Activate();
	void Adjust(int32 Dir);
	// Q/E (shoulders on pad) switch the Settings panel's tabs while it's open
	void PrevTab();
	void NextTab();

public:
	/** Re-derives the Pause key from FODInputConfig and rebuilds it - called
	 *  at BeginPlay and again live by AODRebindMenu after a rebind (Arc 4.1,
	 *  SS7.32). Escape is untouched (fixed, not rebindable). */
	void RefreshPauseKeyBinding();

private:
	FKey LastPauseKey;

	UODVehicleMovement* FindMovement() const;
	/** One-time Sport-gearbox shift params at BeginPlay - preserves the
	 *  current shift feel now that the gearbox row is gone (no UI). */
	void ApplyGearbox();

	AODSettingsMenu* EnsureSettingsMenu();
	AODCreditsMenu* EnsureCreditsMenu();

	bool bOpen = false;
	int32 Selected = 0;
	int32 GearboxIdx = 1;    // Sport (stage-1 shift defaults) - init only

	bool bSettingsOpen = false;
	bool bCreditsOpen = false;
	UPROPERTY()
	TObjectPtr<AODSettingsMenu> SettingsMenu;
	UPROPERTY()
	TObjectPtr<AODCreditsMenu> CreditsMenu;
};
