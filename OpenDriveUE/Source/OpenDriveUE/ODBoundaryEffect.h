#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODBoundaryEffect.generated.h"

class UDynamicMeshComponent;
class UMaterialInstanceDynamic;

/**
 * The drawn area's edge (2026-10-21, area picker piece B; glass wall 2026-10-23).
 * Spawned by AODGameMode::StartDrawnArea when ODBakeLink has an outline: a
 * wall of clear, overlapping glass pyramids on an irregular (jittered)
 * triangle lattice standing on the outline, built only within ~26 m of the
 * car and rebuilt as it moves. M_ODCurtain (unreal/apply_boundary_materials.py)
 * refracts the terrain through it, fades it out by 8..22 m from the car and
 * breathes the pyramid tips (WPO) - only while slow: "Breath" eases to 0 above
 * 12 mph, like the trees' sway shadows. The physical push-back lives in
 * UODVehicleMovement's fixed step; the unsynced ground look in
 * AODTerrainManager. Visual only, no collision.
 */
UCLASS()
class OPENDRIVEUE_API AODBoundaryEffect : public AActor
{
	GENERATED_BODY()

public:
	AODBoundaryEffect();

	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

private:
	void Rebuild(const FVector& Car);
	FVector2D PosAt(double S, FVector2D* InwardOut = nullptr) const;
	double GroundAt(double S, double FallbackZ);

	UPROPERTY()
	TObjectPtr<UDynamicMeshComponent> Wall;
	UPROPERTY()
	TObjectPtr<UMaterialInstanceDynamic> Mat;

	TArray<FVector2D> Pts;       // outline, UE cm
	TArray<double> Cum;          // arc length at Pts[i], Num()+1 entries (last = perimeter)
	TArray<FVector2D> Inward;    // per segment, unit, into the area
	TMap<int64, float> GroundZ;  // ground trace per lattice column (s / tile)
	FVector2D BuiltAt = FVector2D(1e30, 1e30);
	float Breath = 1.0f;
};
