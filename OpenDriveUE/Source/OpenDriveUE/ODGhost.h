#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODGhost.generated.h"

class USkeletalMeshComponent;

/**
 * Spectral playback of the best run (Arc 3): replays transform samples
 * recorded by AODRaceManager, drawn as the player's car mesh with a
 * translucent ghost material. Hidden while no run is in progress.
 */
UCLASS()
class OPENDRIVEUE_API AODGhost : public AActor
{
	GENERATED_BODY()

public:
	AODGhost();

	/** Copy the player's car mesh and start hidden. */
	void Init(USkeletalMesh* CarMesh);

	bool NeedsMesh() const;

	void Play(float WorldStartTime);
	void Stop();

	float SampleInterval = 0.1f;
	TArray<FVector> Positions;
	TArray<FRotator> Rotations;

	virtual void Tick(float DeltaSeconds) override;

private:
	UPROPERTY()
	TObjectPtr<USkeletalMeshComponent> Mesh;

	bool bPlaying = false;
	float StartTime = 0.0f;
};
