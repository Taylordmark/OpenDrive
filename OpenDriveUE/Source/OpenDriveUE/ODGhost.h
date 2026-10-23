#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODGhost.generated.h"

class UStaticMesh;
class UStaticMeshComponent;

/**
 * Shareable ghost file (fun feature #3): Saved/Ghosts/<area>_<track>_<time>.odghost.
 * 'ODGH' + uint32 version + area + uint32 track hash + car id + float time
 * + float sample interval + int32 N + N * (pos xyz, quat xyzw), little-endian
 * via FArchive (FString = UE's length-prefixed string). The track hash comes
 * from the gate transforms, not the track name: custom_<n> differs per machine.
 */
struct OPENDRIVEUE_API FODGhostFile
{
	static constexpr uint32 Version = 1;
	FString Area;
	uint32 TrackHash = 0;
	FString CarId;           // UODVehicleDef asset name; empty = unknown
	float Time = 0.0f;
	float SampleInterval = 0.1f;
	TArray<FVector> Positions;
	TArray<FQuat> Rotations;
	FString Path;            // set by Load, not stored

	static FString Dir();
	static uint32 HashTrack(const TArray<FTransform>& Gates);
	bool Save(const FString& InPath) const;
	bool Load(const FString& InPath);
	/** -odselfcheck: write, read back, compare; logs PASS/FAIL. */
	static bool SelfCheck();
};

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
	TArray<FQuat> Rotations;

	virtual void Tick(float DeltaSeconds) override;

private:
	UPROPERTY()
	TObjectPtr<UStaticMeshComponent> Mesh;

	bool bPlaying = false;
	float StartTime = 0.0f;
};
