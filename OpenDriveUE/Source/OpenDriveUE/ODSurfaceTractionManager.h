#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODSurfaceTractionManager.generated.h"

/**
 * Scales each wheel's friction by the physical material it is touching
 * (wishlist #4: on-road vs off-road grip, per tire). Lives in the level and
 * services every wheeled vehicle pawn, so vehicle blueprints need no edits.
 */
UCLASS()
class OPENDRIVEUE_API AODSurfaceTractionManager : public AActor
{
	GENERATED_BODY()

public:
	AODSurfaceTractionManager();
	virtual void Tick(float DeltaSeconds) override;

	/** Grip multiplier when the contact material name contains the key. */
	UPROPERTY(EditAnywhere, Category = "Traction")
	TMap<FString, float> SurfaceGrip;

	/** Fallback when no physical material is reported (airborne etc.). */
	UPROPERTY(EditAnywhere, Category = "Traction")
	float DefaultGrip = 1.0f;

private:
	/** Base FrictionForceMultiplier per wheel, captured per vehicle. */
	TMap<TWeakObjectPtr<APawn>, TArray<float>> BaseFriction;
};
