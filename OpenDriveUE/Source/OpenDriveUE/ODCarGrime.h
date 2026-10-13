#pragma once

#include "CoreMinimal.h"
#include "PhysicalMaterials/PhysicalMaterial.h"

/**
 * Track B pass 2 (PLAN-VISUALS.md): how dirty the car's body gets, 0..MaxDirt.
 * UODVehicleMovement integrates it once per fixed physics step from the
 * distance the car rolled on each surface; UODVehicleFX hands it to the
 * Realistic car materials as custom primitive data ("Dirt").
 *
 * Per distance, not per time: dD/ds = Rate * (MaxDirt - D), whose exact step
 * D' = Max - (Max - D) * exp(-Rate * ds) gives the same result however the
 * distance is cut into steps (pipeline/checks/check_car_dirt.py mirrors this
 * file and checks that, the accumulation and the reset). Parked = no change.
 */
namespace ODCarGrime
{
	constexpr float MaxDirt = 0.85f;
	// per metre of rolling: 1 / Rate = metres to get ~63% of the way to MaxDirt
	constexpr float LooseRatePerM = 1.0f / 600.0f;      // PM_Gravel, PM_DirtRoad, PM_Dirt (off-road landscape)
	constexpr float OffRoadRatePerM = 1.0f / 900.0f;    // grass and anything else that isn't paved
	constexpr float AsphaltRatePerM = 1.0f / 25000.0f;  // dry tarmac: slow road film
	constexpr float AsphaltWetRatePerM = 1.0f / 2500.0f;// wet tarmac: road spray

	/** Rate for one wheel's contact surface (null = not touching). */
	inline float RatePerM(const UPhysicalMaterial* Mat, float Wetness)
	{
		if (!Mat)
		{
			return 0.0f;
		}
		const FString Name = Mat->GetName();
		if (Name.Contains(TEXT("Asphalt")))
		{
			return FMath::Lerp(AsphaltRatePerM, AsphaltWetRatePerM, FMath::Clamp(Wetness, 0.0f, 1.0f));
		}
		if (Name.Contains(TEXT("Gravel")) || Name.Contains(TEXT("Dirt")))
		{
			return LooseRatePerM;
		}
		return OffRoadRatePerM;
	}

	/** One step: DistM metres rolled at RatePerM (mean over the wheels). */
	inline float Step(float Dirt, float DistM, float Rate)
	{
		return MaxDirt - (MaxDirt - Dirt) * FMath::Exp(-Rate * FMath::Max(DistM, 0.0f));
	}
}
