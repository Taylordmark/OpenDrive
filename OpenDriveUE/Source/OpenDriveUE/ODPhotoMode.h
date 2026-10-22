#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODEnvironment.h"
#include "ODPhotoMode.generated.h"

class UCameraComponent;

/**
 * Fun feature #4: photo mode. A paused camera actor that becomes the view
 * target: it orbits the car (or free-flies, held within FreeRadiusCm of it
 * and above the ground) and carries its own post-process on the camera -
 * FOV, roll, exposure bias, depth of field and vignette - plus the #7
 * Environment rows. Enter captures the frame to
 * Saved/Screenshots/OpenDrive_<area>_<stamp>.png.
 *
 * Like AODSettingsMenu, a plain logic object driven by AODPauseMenu (its
 * Up/Down/Left/Right/Enter/Escape route here while photo mode is open), so
 * no second input stack. Camera moves are polled in Tick, which runs while
 * paused: WASD / left stick move (orbit: around the car), R/F raise/lower
 * (orbit: closer/further), right-mouse drag / right stick look, Space
 * captures, H hides the panel.
 */
UCLASS()
class OPENDRIVEUE_API AODPhotoMode : public AActor
{
	GENERATED_BODY()

public:
	AODPhotoMode();
	virtual void Tick(float DeltaSeconds) override;

	// value rows, then the #7 environment rows, then the Take Photo footer
	enum : int32 { RowFov, RowRoll, RowExposure, RowFocus, RowAperture, RowVignette, RowCamera, RowEnv,
		RowCapture = RowEnv + ODEnvironment::NumRows, NumPhotoRows };

	/** Takes the view from the current camera; the game must already be paused. */
	void Enter();
	void Exit();
	bool IsActive() const { return bActive; }

	int32 NumRows() const { return NumPhotoRows; }
	int32 GetFooterRow() const { return RowCapture; }
	void GetRow(int32 Row, FString& OutLabel, FString& OutValue) const;
	void Adjust(int32 Row, int32 Dir);
	/** Queues the screenshot; returns the file path. */
	FString Capture();

	bool IsUiHidden() const { return bUiHidden; }
	/** "Saved ..." for a few seconds after a capture, else empty. */
	FString GetToast() const;

	/** `-odphotocheck=<s>`: drives the pause menu into photo mode, exercises
	 *  the rows, free-fly clamp and capture, logs "ODPhotoCheck: PASS/FAIL". */
	static void MaybeStartSelfCheck(UWorld* World);

	static constexpr float FreeRadiusCm = 5000.0f;   // doc's ~50 m

private:
	void UpdateCamera(float RealDt);
	void ApplyPostProcess();
	FVector Pivot() const;
	float FocusCm() const;

	UPROPERTY()
	TObjectPtr<UCameraComponent> Camera;
	TWeakObjectPtr<AActor> PrevViewTarget;

	bool bActive = false;
	bool bUiHidden = false;
	bool bFree = false;
	float Fov = 70.0f;
	float Roll = 0.0f;
	float ExposureEV = 0.0f;       // on top of the grade's bias
	float BaseBias = 0.0f;         // the grade PPV's AutoExposureBias at Enter
	int32 FocusIdx = 0;            // 0 = auto (the car)
	int32 ApertureIdx = 0;         // 0 = DOF off
	float Vignette = 0.4f;

	// orbit around Pivot(); free-fly keeps FreeLoc
	float Yaw = 0.0f;
	float Pitch = 10.0f;
	float DistCm = 600.0f;
	FVector FreeLoc = FVector::ZeroVector;
	FVector2D LastMouse = FVector2D::ZeroVector;
	bool bDragging = false;

	FString ToastText;
	double ToastUntil = 0.0;
};
