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
};
