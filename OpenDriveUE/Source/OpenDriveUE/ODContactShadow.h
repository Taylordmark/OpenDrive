#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "ODContactShadow.generated.h"

class UDecalComponent;

/**
 * Realistic only (PLAN-VISUALS Track B, grounding): soft dark blobs on the
 * ground under the car body and under each tyre, so the car sits on the
 * road in overcast and at night too, when the sun shadow is weak or gone.
 *
 * Five deferred decals (M_CarContactShadow, unreal/
 * build_car_contact_shadow_material.py) that darken albedo and kill
 * specular under the car. The car's own meshes have bReceivesDecals off, so
 * a blob never draws on the car and the body hides it by depth.
 *
 * Each tick (post-physics) every blob traces down along the body's -Z from
 * its anchor (body footprint centre / wheel axle), sits on the hit with the
 * hit normal (terrain slope), and fades by the gap to the ground and by body
 * tilt - a pure function of the current physics state, no accumulation, so
 * it's the same at any frame rate. pipeline/checks/check_contact_shadow.py
 * mirrors Strength() and AlongOnPlane().
 *
 * Also, Realistic only: car meshes cast two-sided shadows (open low-poly
 * shells otherwise leak light into the cabin and wheel wells).
 */
UCLASS(ClassGroup = (Custom), meta = (BlueprintSpawnableComponent))
class OPENDRIVEUE_API UODContactShadow : public UActorComponent
{
	GENERATED_BODY()

public:
	UODContactShadow();
	virtual void BeginPlay() override;
	virtual void TickComponent(float DeltaTime, ELevelTick TickType,
		FActorComponentTickFunction* ThisTickFunction) override;

	/** 0..1: 1 while GapCm <= FadeStartCm, smoothstep to 0 at FadeEndCm,
	 *  times a tilt fade on UpZ (body up . world up): 1 at >= 0.8, 0 at <= 0.5. */
	static float Strength(float GapCm, float FadeStartCm, float FadeEndCm, float UpZ);
	/** Fwd projected onto the plane with normal N, unit (falls back to any
	 *  perpendicular when Fwd is parallel to N). */
	static FVector AlongOnPlane(const FVector& Fwd, const FVector& N);

private:
	/** Trace from Anchor along -Up up to MaxCm; on a hit place D on the
	 *  ground. Returns the hit distance, or -1 (and hides D) on no hit. */
	float Place(UDecalComponent* D, const FVector& Anchor, const FVector& Up,
		const FVector& Fwd, float MaxCm, float HalfAlong, float HalfAcross, float HalfDepth);
	void SetStrength(UDecalComponent* D, float Inner, float S);

	UPROPERTY()
	TArray<TObjectPtr<UDecalComponent>> Blobs;   // 0 body, 1..4 wheels FL,FR,RL,RR
};
