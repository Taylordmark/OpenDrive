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
	virtual void EndPlay(const EEndPlayReason::Type Reason) override;
	virtual void Tick(float DeltaSeconds) override;

	/** Gate transforms in order, start first, finish last (set by pipeline). */
	UPROPERTY(EditAnywhere, Category = "Race")
	TArray<FTransform> Checkpoints;

	/** Custom track (2026-10-13, pipeline/build_track.py): -odtrack=<name>
	 *  loads Masks/track_<name>.bin into Checkpoints/TrackLine instead of
	 *  the level's own gates. Empty = the level's race, as before. */
	FString TrackName;

	/** Track editor (2026-10-13): writes a road route (XY + Z cm, from
	 *  AODRoadManager::RouteThrough) as Masks/track_custom_<n>.bin with
	 *  build_track.py's gates and adds it to Masks/tracks.json, so the
	 *  menu's Track row lists it. Returns the new track name, empty on failure. */
	static FString SaveTrack(const FString& Area, const TArray<FVector2D>& XY,
		const TArray<float>& Z, bool bLoop);

	/** Track line (world XY cm), spawn at [0]. Empty without a track. */
	TArray<FVector2D> TrackLine;

	/** Circuit: the finish is the start line, laps run back to back. */
	bool bLoop = false;

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

	/** Full-density road centerline for the map (pipeline/
	 *  export_centerline_bin.py; world XY, cm). Empty if the sidecar is
	 *  missing - HUD falls back to a spline guess between the gates. */
	UPROPERTY()
	TArray<FVector2D> RoadCenterline;

	float ElapsedTime() const;

	void RespawnAtLastGate();

private:
	FString SaveSlot() const;
	void SaveBest();
	void LoadCenterline();
	bool LoadTrack();
	void StartRun(double CrossTime);

	/** Recording of the current run (becomes the ghost on a PB). */
	static constexpr float SampleInterval = 0.1f;
	float NextSampleTime = 0.0f;
	TArray<FVector> RecPositions;
	TArray<FRotator> RecRotations;
	TArray<float> RecGateTimes;
	TArray<float> BestGateTimes;
	/** Last Tick's pawn pose: gate times and ghost samples interpolate
	 *  between it and this Tick's, so they don't snap to the frame. */
	FVector PrevPos = FVector::ZeroVector;
	FQuat PrevRot = FQuat::Identity;
	double PrevTime = -1.0;

	TWeakObjectPtr<class AODGhost> Ghost;
};
