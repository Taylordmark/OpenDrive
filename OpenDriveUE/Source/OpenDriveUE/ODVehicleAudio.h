#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODVehicleAudio.generated.h"

class UODEngineSynth;
class UODRoadNoiseSynth;
class UODVehicleMovement;

/**
 * Arc 5.1 conductor: attaches the engine + road synths to the player vehicle
 * and pipes UODVehicleMovement telemetry (RPM, effective throttle, load,
 * speed, contact surface) into them every tick. Collision impulses become
 * impact thumps.
 *
 * Scripted-drive hooks (-odrev/-odlift/-odsteer) live on AODVehiclePawn now;
 * this actor keeps the recording hook:
 *   -odrec=D  record D seconds of the output mix starting at boot+3 s,
 *             written to Saved/AudioTest/odrec.wav
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

	/** Settings-shell-facing (Arc 4.4): Master*Vehicle volume combined into
	 *  one scale, pushed straight to both synths' single output-gain
	 *  multiply point (UODEngineSynth/UODRoadNoiseSynth's own VolumeScale -
	 *  see their OnGenerateAudio()). Cached so EnsureSynths() can re-apply
	 *  it to a freshly (re)created pair after a car swap, which would
	 *  otherwise silently reset to the synths' 1.0 default and drop the
	 *  player's volume setting. */
	void SetVehicleVolumeScale(float Scale);

private:
	UFUNCTION()
	void OnVehicleHit(UPrimitiveComponent* HitComponent, AActor* OtherActor,
		UPrimitiveComponent* OtherComp, FVector NormalImpulse,
		const FHitResult& Hit);

	void EnsureSynths(APawn* Car);

	UPROPERTY()
	TObjectPtr<UODEngineSynth> Engine;

	UPROPERTY()
	TObjectPtr<UODRoadNoiseSynth> Road;

	TWeakObjectPtr<APawn> BoundCar;
	double LastImpactTime = -1.0;

	// see SetVehicleVolumeScale()'s comment - re-applied to Engine/Road every
	// time EnsureSynths() (re)creates them.
	float CachedVehicleVolumeScale = 1.0f;

	// -odrec hook
	float RecSeconds = -1.0f;
	bool bRecording = false, bRecDone = false;
};
