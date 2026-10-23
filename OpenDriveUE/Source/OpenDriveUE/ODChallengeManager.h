#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "GameFramework/SaveGame.h"
#include "ODChallengeManager.generated.h"

class UCanvas;
class AODRoadManager;

/** Per-area progress (2026-10-22, fun-features #2/#5), slot OpenDrive_area_<area>. */
UCLASS()
class OPENDRIVEUE_API UODAreaSave : public USaveGame
{
	GENERATED_BODY()

public:
	UPROPERTY()
	TMap<FString, float> TrapBestKmh;

	UPROPERTY()
	TMap<FString, float> ZoneBestKmh;   // average speed over the zone

	UPROPERTY()
	TArray<FString> Discovered;          // landmark names
};

/**
 * Free-roam speed traps, speed zones and landmark hunting (2026-10-22,
 * docs/fun-features-scoping.md #2 and #5). Spawned by AODGameMode when a home
 * area is set and no race runs. Reads Masks/challenges_<area>_test.json
 * (pipeline/build_speed_challenges.py) and Masks/landmarks_<area>_test.json
 * (pipeline/build_landmarks.py). Gate crossings are placed on the straight
 * path between last Tick's pose and this one (GateCross), like
 * AODRaceManager's gates, so results don't depend on frame rate.
 *
 * AODRaceHUD draws through DrawToast / DrawMapIcons / DrawChecklist and
 * routes world-map clicks through LandmarkNear.
 */
UCLASS()
class OPENDRIVEUE_API AODChallengeManager : public AActor
{
	GENERATED_BODY()

public:
	AODChallengeManager();
	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

	/** "<area>" as in AODRoadManager::GetAreaKey; set before FinishSpawning. */
	UPROPERTY()
	FString AreaKey;

	static AODChallengeManager* Find(const UWorld* World);

	/**
	 * Did the step Prev -> Cur cross the gate line through Center, normal Dir
	 * (unit, 2D)? Returns +1 crossing along Dir, -1 against it, 0 none (or
	 * more than HalfWidthCm along the line / 10 m off in height). OutT is the
	 * crossing's fraction of the step, for sub-frame time and speed.
	 */
	static int32 GateCross(const FVector& Prev, const FVector& Cur, const FVector& Center,
		const FVector2D& Dir, float HalfWidthCm, double& OutT);

	// ---- AODRaceHUD hooks
	void DrawToast(UCanvas* C, float S, bool bMetric) const;
	void DrawMapIcons(UCanvas* C, float S, TFunctionRef<FVector2D(const FVector&)> ToMap,
		TFunctionRef<bool(const FVector2D&)> Visible) const;
	void DrawChecklist(UCanvas* C, float S, float X, float Y, float W, float MaxH, bool bMetric) const;
	/** Landmark within RadiusCm of World (world-map pick). */
	bool LandmarkNear(const FVector2D& World, float RadiusCm, FVector2D& OutPos, FString& OutName) const;

private:
	struct FTrap { FString Name; FVector Pos; FVector2D Dir; };
	struct FZone { FString Name; float LengthM = 0.0f; TArray<FVector> Line; FVector2D StartDir, EndDir; };
	struct FLandmark { FString Name; FVector2D Pos; float RadiusCm = 0.0f; };

	void Load();
	void Save() const;
	void Toast(const FString& Title, const FString& Detail, float Kmh = 0.0f, float BestKmh = 0.0f);

	TArray<FTrap> Traps;
	TArray<FZone> Zones;
	TArray<FLandmark> Landmarks;
	UPROPERTY()
	TObjectPtr<UODAreaSave> Progress;

	// last Tick's pose, for sub-frame crossings
	FVector PrevPos = FVector::ZeroVector;
	float PrevSpeed = 0.0f;   // cm/s, unsigned
	double PrevTime = -1.0;

	int32 ActiveZone = INDEX_NONE;
	int32 ZoneDir = 1;        // +1 line[0] -> line[-1], -1 the other way
	double ZoneStart = 0.0;

	FString ToastTitle, ToastDetail;
	double ToastTime = -100.0;
	float ToastKmh = 0.0f, ToastBestKmh = 0.0f;   // > 0: a result line under Detail (units at draw time)
};
