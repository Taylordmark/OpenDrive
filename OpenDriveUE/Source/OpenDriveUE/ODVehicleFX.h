#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "ODVehicleFX.generated.h"

class AODSeasonController;
class AODWeatherController;
class UInstancedStaticMeshComponent;
class UPhysicalMaterial;

/** What a tire is on, as the effects see it (from the contact phys-mat name,
 *  like UODVehicleMovement::SurfaceGrip and AODVehicleAudio do). */
enum class EODFxSurface : uint8
{
	None,      // airborne, guardrail, unknown
	Asphalt,   // PM_Asphalt: smoke on slip, spray when wet
	Loose,     // PM_Gravel, PM_DirtRoad, PM_Dirt (the landscape): dust
};

/** Puffs per second for one wheel, per effect. */
struct FODFxRates
{
	float Smoke = 0.0f;
	float Dust = 0.0f;
	float Spray = 0.0f;
};

/**
 * Track E (PLAN-VISUALS.md), Realistic only: tire smoke on slip, dust off
 * loose surfaces, road spray when wet. Reads UODVehicleMovement's public
 * wheel state and the body's velocity - never writes to the sim.
 *
 * Not Niagara: same call as AODSeasonController's snowfall - Niagara assets
 * can't be authored reliably from headless Python in this 5.8 project, so
 * this is one instanced-sphere pool the component animates itself, drawn
 * with M_FXPuff (unreal/build_vehicle_fx_material.py: translucent, soft
 * fresnel edge, depth fade, color and alpha from per-instance custom data).
 *
 * Frame-rate independent: each wheel/effect accumulates Rate x Dt as spawn
 * debt (so the puff count over a drive is the integral of the rate, not a
 * per-frame count), a puff spawned between frames is placed and aged by its
 * sub-frame time, and motion uses the exact solution of v' = a - k v.
 * pipeline/tests/test_vehicle_fx_rates.py mirrors Rates() and that loop.
 *
 * Also hands the car's state to the Realistic car materials as custom
 * primitive data (PushCarState; build_car_materials_real.py reads it):
 * 0 Dirt, 1 Wetness, 2 SnowCover on every car mesh, 3..5 FrontAxleX /
 * RearAxleX / WheelRadius (body-local cm) on the chassis.
 *
 * Disabled at BeginPlay in Stylized (the style applies on level load).
 */
UCLASS(ClassGroup = (Custom), meta = (BlueprintSpawnableComponent))
class OPENDRIVEUE_API UODVehicleFX : public UActorComponent
{
	GENERATED_BODY()

public:
	UODVehicleFX();
	virtual void BeginPlay() override;
	virtual void TickComponent(float DeltaTime, ELevelTick TickType,
		FActorComponentTickFunction* ThisTickFunction) override;

	static EODFxSurface Classify(const UPhysicalMaterial* Mat);
	/** SlipMs: contact-patch sliding speed, m/s. SpeedMs: contact speed over
	 *  ground, m/s. Wetness: AODWeatherController::Wetness, 0..1. */
	static FODFxRates Rates(EODFxSurface Surface, float SlipMs, float SpeedMs, float Wetness);

	/** Grime/wet/snow + axles into the car meshes' custom primitive data. */
	void PushCarState();

private:
	struct FPuff
	{
		FVector Pos = FVector::ZeroVector;   // world, cm
		FVector Vel = FVector::ZeroVector;   // cm/s
		float Age = 0.0f;
		float Life = 0.0f;                   // 0 = free slot
		float Size0 = 0.0f, Size1 = 0.0f;    // diameter, cm
		uint8 Kind = 0;                      // index into the kind table (.cpp)
	};

	void Spawn(int32 Kind, const FVector& Contact, const FVector& CarVel,
		const FVector& Fwd, float Tau);
	void Integrate(FPuff& P, float Dt) const;
	void Draw(bool bAnyLive);

	UPROPERTY()
	TObjectPtr<UInstancedStaticMeshComponent> Pool;

	TArray<FPuff> Puffs;
	int32 Next = 0;                 // ring cursor: a full pool recycles the oldest-spawned slot
	TArray<float> Debt;             // NumWheels x 3 effects, fractional puffs owed
	FRandomStream Rng{ 0x0DF7 };
	bool bDrawnLive = false;        // last Draw had live puffs (so one more clears them)

	TWeakObjectPtr<AODWeatherController> WeatherCtl;
	TWeakObjectPtr<AODSeasonController> SeasonCtl;
	double LastWeatherSearch = -10.0;

	// scratch for Draw (kept to avoid per-frame allocs)
	TArray<FTransform> Xf;
	TArray<float> Custom;
};
