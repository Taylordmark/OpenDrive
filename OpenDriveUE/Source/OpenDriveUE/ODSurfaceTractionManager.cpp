#include "ODSurfaceTractionManager.h"

#include "ChaosVehicleWheel.h"
#include "ChaosWheeledVehicleMovementComponent.h"
#include "EngineUtils.h"
#include "PhysicalMaterials/PhysicalMaterial.h"
#include "WheeledVehiclePawn.h"

AODSurfaceTractionManager::AODSurfaceTractionManager()
{
	PrimaryActorTick.bCanEverTick = true;
	SurfaceGrip.Add(TEXT("Asphalt"), 1.0f);
	SurfaceGrip.Add(TEXT("Dirt"), 0.72f);
	SurfaceGrip.Add(TEXT("Grass"), 0.60f);
}

void AODSurfaceTractionManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	for (TActorIterator<AWheeledVehiclePawn> It(GetWorld()); It; ++It)
	{
		auto* Move = Cast<UChaosWheeledVehicleMovementComponent>(
			It->GetVehicleMovementComponent());
		if (!Move)
		{
			continue;
		}

		TArray<float>& Base = BaseFriction.FindOrAdd(*It);
		if (Base.Num() != Move->Wheels.Num())
		{
			Base.Reset();
			for (const UChaosVehicleWheel* Wheel : Move->Wheels)
			{
				Base.Add(Wheel ? Wheel->FrictionForceMultiplier : 1.0f);
			}
		}

		for (int32 i = 0; i < Move->Wheels.Num(); ++i)
		{
			UChaosVehicleWheel* Wheel = Move->Wheels[i];
			if (!Wheel)
			{
				continue;
			}
			float Grip = DefaultGrip;
			if (const UPhysicalMaterial* Mat = Wheel->GetContactSurfaceMaterial())
			{
				const FString MatName = Mat->GetName();
				for (const TPair<FString, float>& Pair : SurfaceGrip)
				{
					if (MatName.Contains(Pair.Key))
					{
						Grip = Pair.Value;
						break;
					}
				}
			}
			Move->SetWheelFrictionMultiplier(i, Base[i] * Grip);
		}
	}
}
