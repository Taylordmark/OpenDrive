#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODTuningManager.generated.h"

class UODTuningPreset;

/**
 * Applies tuning archetypes (Arc 1.5) to the player vehicle's
 * UODVehicleMovement. The garage's Tuning row picks them (never while
 * driving); -odtune=N cycles them headlessly for verification.
 */
UCLASS()
class OPENDRIVEUE_API AODTuningManager : public AActor
{
	GENERATED_BODY()

public:
	AODTuningManager();
	virtual void BeginPlay() override;

	/** Garage-selectable setups (set by unreal/apply_tuning_presets.py). */
	UPROPERTY(EditAnywhere, Category = "Tuning")
	TArray<TObjectPtr<UODTuningPreset>> Presets;

	void CyclePreset();
	void ApplyPreset(UODTuningPreset* Preset);

	/** Menu-facing: apply by Presets index and remember it. */
	void ApplyPresetByIndex(int32 Index);
	int32 GetPresetIndex() const { return PresetIndex; }

	// --- HUD-facing ---
	FString CurrentPresetName;

private:
	int32 PresetIndex = -1;
};
