#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODGarageEnvironment.generated.h"

class UStaticMeshComponent;
class UDynamicMeshComponent;
class UPointLightComponent;
class URectLightComponent;
class UBoxReflectionCaptureComponent;
class UMaterialInterface;
class UMaterialInstanceDynamic;
class UDirectionalLightComponent;
class USkyLightComponent;

/**
 * Procedural garage/showroom room (2026-08-27) - replaces the bare void
 * AODVehiclePawn::PlaceInShowroom used to leave the menu/garage preview car
 * sitting in: black walls, a glossy dark concrete floor, and a honeycomb
 * ceiling of cool-white emissive hex panels. The hex ceiling is a single
 * UDynamicMeshComponent built in code with a per-hex vertex-color overlay
 * (same FDynamicMesh3 + PrimaryColors pattern AODTerrainManager::BuildTile
 * uses for its runtime ground tint - see BuildHexCeiling()) so a few tiles
 * can read dimmer than the rest without needing separate materials.
 *
 * Spawned once, lazily, from AODMainMenu::EnterCarConfig() and repositioned
 * with PlaceAt() around wherever the showroom pivot currently is:
 * PlaceInShowroom's fixed void point on a fresh boot, or the car's real
 * on-road location for the pause menu's "Garage" reopen (OpenGarage()
 * deliberately leaves the car exactly where it was). Both entry paths funnel
 * through EnterCarConfig(), so one PlaceAt()+SetActive(true) call there
 * covers both. The room's footprint is a square (RoomHalfExtentCm on every
 * side) specifically so it never needs to rotate to match the car's
 * facing/yaw - simpler than tracking ShowroomYawDeg here too.
 *
 * While active, SetActive() also zeroes out every ADirectionalLight/
 * ASkyLight component in the level (the normal outdoor sun/sky would
 * otherwise blow the room's exposure out, and would keep drifting with
 * AODDayNightController's cycle right through the walls) and restores their
 * original intensities on deactivate. This is plain component-property
 * work, not Tick-driven, so it's safe to call from EnterCarConfig() even
 * though AODMainMenu may pause the world in that same call (the OpenGarage()
 * path) - nothing here depends on Tick to take effect.
 */
UCLASS()
class OPENDRIVEUE_API AODGarageEnvironment : public AActor
{
	GENERATED_BODY()

public:
	AODGarageEnvironment();
	virtual void BeginPlay() override;

	/** Half-extent (cm) of the square room footprint - generous margin
	 *  around AODVehiclePawn's showroom camera math (ShowroomDistanceCm=550
	 *  behind the car + ShowroomLookAtBiasCm=120 sideways, see that class) so
	 *  the turntable camera never gets close to a wall regardless of the
	 *  car's facing. */
	UPROPERTY(EditAnywhere, Category = "Garage")
	float RoomHalfExtentCm = 900.0f;

	/** Floor-to-ceiling height (cm) - comfortably above
	 *  AODVehiclePawn::ShowroomHeightCm (160) so the camera stays well under
	 *  the hex ceiling. */
	UPROPERTY(EditAnywhere, Category = "Garage")
	float RoomHeightCm = 500.0f;

	/** Circumradius (cm) of one hex ceiling tile (center to vertex). */
	UPROPERTY(EditAnywhere, Category = "Garage")
	float HexRadiusCm = 70.0f;

	/** Inner edge of each hex's lit outline as a fraction of HexRadiusCm: the
	 *  ceiling glows along the hex edges (Taylor, 2026-10-16) and the tile
	 *  centres stay dark. Two neighbours' bands meet on their shared edge, so
	 *  a line is 2 * (1 - this) * HexRadiusCm wide. */
	UPROPERTY(EditAnywhere, Category = "Garage")
	float HexFillRatio = 0.95f;

	/** Move the room to sit around this world point - see class comment for
	 *  why Center should be the current showroom pivot
	 *  (AODVehiclePawn::GetActorLocation()) for both entry paths.
	 *  GroundContactOffsetCm (2026-08-27 fix) is how far BELOW Center.Z the
	 *  car's actual wheel-ground contact sits - the pawn's actor origin is
	 *  the body pivot at axle height (UODVehicleMovement::GetRestGroundClearanceCm),
	 *  not ground level, so the floor top surface is placed at
	 *  Center.Z - GroundContactOffsetCm (previously just Center.Z, which put
	 *  the floor at axle height and sank the wheels into it). YawDeg orients
	 *  the room to match the car's display yaw (AODMainMenu::ShowroomYawDeg) -
	 *  cosmetic only, since the square footprint is symmetric and would clear
	 *  the camera either way. */
	void PlaceAt(const FVector& Center, float YawDeg, float GroundContactOffsetCm = 0.0f);

	/** Shows/hides the room and (de)activates the scene-lighting override.
	 *  Safe to call repeatedly/redundantly (e.g. two SetShowroom(false)
	 *  call sites in AODMainMenu both call this). */
	void SetActive(bool bOn);

	bool IsActive() const { return bIsActive; }

private:
	void BuildRoom();
	void BuildHexCeiling();
	void OverrideSceneLighting(bool bOn);

	UPROPERTY()
	TObjectPtr<USceneComponent> Root;
	UPROPERTY()
	TObjectPtr<UStaticMeshComponent> Floor;
	UPROPERTY()
	TObjectPtr<UStaticMeshComponent> WallNorth;
	UPROPERTY()
	TObjectPtr<UStaticMeshComponent> WallSouth;
	UPROPERTY()
	TObjectPtr<UStaticMeshComponent> WallEast;
	UPROPERTY()
	TObjectPtr<UStaticMeshComponent> WallWest;
	UPROPERTY()
	TObjectPtr<UStaticMeshComponent> CeilingBacking;
	UPROPERTY()
	TObjectPtr<UDynamicMeshComponent> HexCeiling;
	UPROPERTY()
	TObjectPtr<UPointLightComponent> FillLightA;
	UPROPERTY()
	TObjectPtr<UPointLightComponent> FillLightB;
	UPROPERTY()
	TObjectPtr<URectLightComponent> CeilingLight;
	UPROPERTY()
	TObjectPtr<UBoxReflectionCaptureComponent> ReflectionCapture;

	bool bIsActive = false;

	// original intensities of every real directional/sky light in the level,
	// captured the first time this room activates so they can be restored -
	// two arrays (not one over a common base) because
	// UDirectionalLightComponent/USkyLightComponent each declare their own
	// non-virtual SetIntensity(), not one shared on ULightComponentBase.
	struct FCachedDirLight
	{
		TWeakObjectPtr<UDirectionalLightComponent> Component;
		float OriginalIntensity = 0.0f;
	};
	struct FCachedSkyLight
	{
		TWeakObjectPtr<USkyLightComponent> Component;
		float OriginalIntensity = 0.0f;
	};
	TArray<FCachedDirLight> CachedDirLights;
	TArray<FCachedSkyLight> CachedSkyLights;
};
