#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODPauseMenu.generated.h"

/**
 * Stopgap pause menu (2026-07-16) until the real Arc 4 UMG shell: ESC (or P)
 * pauses and opens a canvas menu drawn by ODRaceHUD. Only surfaces systems
 * that already exist - tuning presets (1.5), gearbox character (1.7 stage 1
 * shift-controller params), engine character (drivetrain torque curve +
 * synth cylinder count), exhaust (5.2 synth params) - plus Resume and Quit.
 * Arrows navigate/change, Enter activates.
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

	void ApplyGearbox();
	void ApplyEngine();
	void ApplyExhaust();

	bool bOpen = false;
	int32 Selected = 0;
	int32 GearboxIdx = 1;    // Comfort / Sport (Sport ~= stage-1 defaults)
	int32 EngineIdx = 0;     // V8 / V6
	int32 ExhaustIdx = 0;    // Stock / Sport / Open
};
