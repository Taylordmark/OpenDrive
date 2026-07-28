#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODPauseMenu.generated.h"

class UODVehicleMovement;

/**
 * In-game pause menu (ESC / P). Deliberately minimal (2026-07-25, Taylor's
 * call): the two driver assists - Traction Control and ABS - are the only
 * settings changed directly here. Everything that defines the car itself
 * (car, engine, drivetrain, tuning) still lives in AODMainMenu's car-config
 * screen; the "Garage" row (2026-07-26) opens that screen in place
 * (AODMainMenu::OpenGarage()) instead of requiring a full trip through
 * "Main Menu" -> map-select just to change the car mid-drive. Rows are
 * Resume, Traction, ABS, Garage, Main Menu (back to the front-end), Quit.
 * Drawn on canvas by ODRaceHUD; arrows navigate/change, Enter activates.
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
	int32 NumRows() const { return 6; }
	void GetRow(int32 Row, FString& OutLabel, FString& OutValue) const;

private:
	void Toggle();
	void Up();
	void Down();
	void Left();
	void Right();
	void Activate();
	void Adjust(int32 Dir);

	UODVehicleMovement* FindMovement() const;
	/** One-time Sport-gearbox shift params at BeginPlay - preserves the
	 *  current shift feel now that the gearbox row is gone (no UI). */
	void ApplyGearbox();

	bool bOpen = false;
	int32 Selected = 0;
	int32 GearboxIdx = 1;    // Sport (stage-1 shift defaults) - init only
};
