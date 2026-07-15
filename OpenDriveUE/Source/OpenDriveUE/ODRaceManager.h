#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "GameFramework/SaveGame.h"
#include "ODRaceManager.generated.h"

UCLASS()
class OPENDRIVEUE_API UODRaceSave : public USaveGame
{
	GENERATED_BODY()

public:
	UPROPERTY()
	float BestTime = 0.0f;

	/** Best-run transform samples (the ghost), every SampleInterval s. */
	UPROPERTY()
	float SampleInterval = 0.1f;

	UPROPERTY()
	TArray<FVector> GhostPositions;

	UPROPERTY()
	TArray<FRotator> GhostRotations;

	/** Elapsed time at each gate of the best run (sector splits). */
	UPROPERTY()
	TArray<float> BestGateTimes;
};

/**
 * Arc 3 race framework v1: ordered checkpoint gates along the road (baked in
 * by the stage pipeline), timer, best-time persistence, R-to-respawn at the
 * last gate. HUD drawn by AODRaceHUD.
 */
UCLASS()
class OPENDRIVEUE_API AODRaceManager : public AActor
{
	GENERATED_BODY()

public:
	AODRaceManager();
	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

	/** Gate transforms in order, start first, finish last (set by pipeline). */
	UPROPERTY(EditAnywhere, Category = "Race")
	TArray<FTransform> Checkpoints;

	/** Pass radius (cm). */
	UPROPERTY(EditAnywhere, Category = "Race")
	float GateRadius = 2200.0f;

	// --- HUD-facing state ---
	bool bRunning = false;
	int32 NextGate = 0;
	float RaceStart = 0.0f;
	float LastTime = 0.0f;
	float BestTime = 0.0f;

	/** Split vs best run at the last gate passed (negative = ahead). */
	float SplitDelta = 0.0f;
	float SplitShownAt = -100.0f;   // world seconds; HUD fades it after ~3 s

	float ElapsedTime() const;

	void RespawnAtLastGate();

private:
	FString SaveSlot() const;
	void SaveBest();

	/** Recording of the current run (becomes the ghost on a PB). */
	static constexpr float SampleInterval = 0.1f;
	float NextSampleTime = 0.0f;
	TArray<FVector> RecPositions;
	TArray<FRotator> RecRotations;
	TArray<float> RecGateTimes;
	TArray<float> BestGateTimes;

	TWeakObjectPtr<class AODGhost> Ghost;
};
