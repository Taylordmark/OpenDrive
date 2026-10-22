#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODBoundaryEffect.generated.h"

class UDynamicMeshComponent;
class UPostProcessComponent;

/**
 * The drawn area's edge (2026-10-21, area picker piece B; scoping rounds 3 + 8).
 * Spawned by AODGameMode::StartDrawnArea when ODBakeLink has an outline:
 *  - a curtain: a translucent M_ODCurtain ribbon standing on the outline
 *    (unreal/apply_boundary_materials.py), ground-traced every ~50 m, its
 *    unresolved posts re-traced as terrain streams in;
 *  - an unbound post-process that desaturates + fringes the view over the
 *    last 150 m inside the outline and fully outside it.
 * The physical push-back lives in UODVehicleMovement's fixed step; the
 * unsynced ground look in AODTerrainManager. Visual only, no collision.
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
	void RebuildCurtain();

	UPROPERTY()
	TObjectPtr<UDynamicMeshComponent> Curtain;
	UPROPERTY()
	TObjectPtr<UPostProcessComponent> Fade;

	TArray<FVector2D> Posts;    // densified outline, UE cm
	TArray<float> PostZ;        // ground Z, NaN = not traced onto ground yet
	int32 NumUnresolved = 0;
	double NextRetrace = 0.0;
};
