#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODScreenshotHook.generated.h"

/**
 * Standalone `-odshot=N` screenshot QA hook, lifted unmodified out of
 * ODForestManager::Tick() (Arc 7.2 slice 1) - that copy only fires when a
 * traditional Landscape actor exists, so it silently no-ops on any
 * Landscape-free open-world level. This actor has no such dependency.
 * ODForestManager and TailOfTheDragon are untouched; this is spawned only
 * into open-world test levels (see unreal/build_openworld_test_level.py).
 */
UCLASS()
class OPENDRIVEUE_API AODScreenshotHook : public AActor
{
	GENERATED_BODY()

public:
	AODScreenshotHook();
	virtual void Tick(float DeltaSeconds) override;

private:
	// -odshot=N QA hook state (2026-08-22: moved off function-local `static`
	// into instance members - a `static` inside Tick() only initializes once
	// per PROCESS, not once per PIE session, so triggering -odshot, stopping
	// PIE, then starting a new PIE run in the same editor process left
	// bShotDone still true from the previous run and the screenshot never
	// fired again. An instance member is naturally fresh every PIE session
	// since PIE spawns a brand-new AODScreenshotHook each time - same fix
	// applied to AODForestManager/AODRoadManager's identical pattern).
	double StartTime = -1.0;
	float ShotAt = -1.0f;
	bool bShotParsed = false;
	bool bShotDone = false;
};
