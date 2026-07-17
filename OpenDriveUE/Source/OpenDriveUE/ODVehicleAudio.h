#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODVehicleAudio.generated.h"

class AODDrivetrainManager;
class UODEngineSynth;
class UODRoadNoiseSynth;
class AWheeledVehiclePawn;

/**
 * Arc 5.1 conductor: attaches the engine + road synths to the player vehicle
 * and pipes Chaos telemetry (RPM, throttle, speed, contact surface) into them
 * every tick. Collision impulses become impact thumps.
 *
 * Test hooks (headless audio verification):
 *   -odrev=T   full throttle T seconds after boot (auto rev/drive)
 *   -odlift=T  throttle back to zero at T seconds (lift-off transients);
 *              while rev/lift hooks are active, a 10 Hz rpm/gear trace is
 *              logged for correlation with the recording
 *   -odrec=D   record D seconds of the output mix starting at boot+3 s,
 *              written to Saved/AudioTest/odrec.wav
 */
UCLASS()
class OPENDRIVEUE_API AODVehicleAudio : public AActor
{
	GENERATED_BODY()

public:
	AODVehicleAudio();
	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

	/** Impact impulse (kg·cm/s) mapping to a full-strength thump. */
	UPROPERTY(EditAnywhere, Category = "Audio")
	float FullImpactImpulse = 60000000.0f;

	/** Speed treated as 1.0 for wind/tire beds (cm/s; ~112 mph). */
	UPROPERTY(EditAnywhere, Category = "Audio")
	float TopSpeed = 5000.0f;

	/** Menu/garage-facing: exhaust + engine-character params live on this. */
	UODEngineSynth* GetEngineSynth() const { return Engine; }

private:
	UFUNCTION()
	void OnVehicleHit(UPrimitiveComponent* HitComponent, AActor* OtherActor,
		UPrimitiveComponent* OtherComp, FVector NormalImpulse,
		const FHitResult& Hit);

	void EnsureSynths(AWheeledVehiclePawn* Car);

	UPROPERTY()
	TObjectPtr<UODEngineSynth> Engine;

	UPROPERTY()
	TObjectPtr<UODRoadNoiseSynth> Road;

	TWeakObjectPtr<AWheeledVehiclePawn> BoundCar;
	TWeakObjectPtr<AODDrivetrainManager> Drivetrain;
	double LastImpactTime = -1.0;

	// test hooks
	float RevAt = -1.0f;
	float LiftAt = -1.0f;
	float RecSeconds = -1.0f;
	bool bRecording = false, bRecDone = false;
	double LastTraceTime = 0.0;
};
