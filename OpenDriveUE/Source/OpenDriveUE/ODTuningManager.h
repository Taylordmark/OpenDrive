#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODTuningManager.generated.h"

class AWheeledVehiclePawn;
class UODTuningPreset;

/**
 * Applies tuning archetypes (Arc 1.5) to the player vehicle. T cycles
 * presets; baseline wheel/engine values are captured on first apply so
 * presets stay deltas over whatever car is driven. The garage UI will call
 * ApplyPreset directly later.
 */
UCLASS()
class OPENDRIVEUE_API AODTuningManager : public AActor
{
	GENERATED_BODY()

public:
	AODTuningManager();
	virtual void BeginPlay() override;

	/** Preset rotation for the debug key (set by the setup script). */
	UPROPERTY(EditAnywhere, Category = "Tuning")
	TArray<TObjectPtr<UODTuningPreset>> Presets;

	void CyclePreset();
	void ApplyPreset(UODTuningPreset* Preset);

	/** Menu-facing: apply by Presets index and remember it (T stays in sync). */
	void ApplyPresetByIndex(int32 Index);
	int32 GetPresetIndex() const { return PresetIndex; }

	// --- HUD-facing ---
	FString CurrentPresetName;
	float SwapShownAt = -100.0f;

private:
	struct FBaseline
	{
		TArray<float> SpringRate, MaxRaise, MaxDrop, Friction;
		float MaxTorque = 0.0f;
	};
	TMap<TWeakObjectPtr<AWheeledVehiclePawn>, FBaseline> Baselines;
	int32 PresetIndex = -1;
};
