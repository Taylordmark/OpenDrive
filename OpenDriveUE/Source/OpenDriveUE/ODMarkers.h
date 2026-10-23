#pragma once

#include "CoreMinimal.h"

class AActor;
class UInstancedStaticMeshComponent;

/** Roadside challenge/race markers that render in every build (2026-10-22;
 *  they were DrawDebugCylinder, stripped in Shipping). */
namespace ODMarkers
{
	/**
	 * A gantry - two posts and an overhead bar - across the road at each gate
	 * (gate X = travel direction), as instances of the engine cube in one ISM
	 * on Owner, BasicShapeMaterial tinted Col, no collision, culled past CullCm.
	 * 3 instances per gate.
	 */
	UInstancedStaticMeshComponent* AddGantries(AActor* Owner, TArrayView<const FTransform> Gates,
		const FLinearColor& Col, float HalfWidthCm, float CullCm = 150000.0f);
}
