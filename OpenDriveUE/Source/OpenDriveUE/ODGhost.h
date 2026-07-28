#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODGhost.generated.h"

class UStaticMesh;
class UStaticMeshComponent;

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
	void Init(UStaticMesh* CarMesh);

	bool NeedsMesh() const;

	void Play(float WorldStartTime);
	void Stop();

	float SampleInterval = 0.1f;
	TArray<FVector> Positions;
	TArray<FRotator> Rotations;

	virtual void Tick(float DeltaSeconds) override;

private:
	UPROPERTY()
	TObjectPtr<UStaticMeshComponent> Mesh;

	bool bPlaying = false;
	float StartTime = 0.0f;
};
