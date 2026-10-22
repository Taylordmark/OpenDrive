#include "ODPhotoMode.h"

#include "Camera/CameraComponent.h"
#include "Camera/PlayerCameraManager.h"
#include "Containers/Ticker.h"
#include "Engine/PostProcessVolume.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "HAL/FileManager.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/App.h"
#include "Misc/CommandLine.h"
#include "Misc/Paths.h"
#include "UnrealClient.h"
#include "ODDayNightController.h"
#include "ODPauseMenu.h"
#include "ODRoadManager.h"

namespace
{
// 0 = auto focus on the car, else metres
const float FocusM[] = {0.0f, 1.0f, 2.0f, 3.0f, 5.0f, 8.0f, 12.0f, 20.0f, 35.0f, 60.0f, 100.0f, 200.0f};
// 0 = DOF off, else f-stop
const float Fstops[] = {0.0f, 1.4f, 2.0f, 2.8f, 4.0f, 5.6f, 8.0f, 11.0f, 16.0f, 22.0f};
constexpr float GroundClearCm = 50.0f;

template <typename T, int32 N>
int32 Cycle(int32 I, int32 Dir, const T (&)[N])
{
	return (I + Dir + N) % N;
}
}

AODPhotoMode::AODPhotoMode()
{
	PrimaryActorTick.bCanEverTick = true;
	PrimaryActorTick.bTickEvenWhenPaused = true;   // it only exists to run while paused
	Camera = CreateDefaultSubobject<UCameraComponent>(TEXT("Camera"));
	RootComponent = Camera;
	Camera->PostProcessBlendWeight = 1.0f;
}

FVector AODPhotoMode::Pivot() const
{
	const APawn* Car = UGameplayStatics::GetPlayerPawn(this, 0);
	return Car ? Car->GetActorLocation() + FVector(0.0, 0.0, 100.0) : FreeLoc;
}

float AODPhotoMode::FocusCm() const
{
	return FocusIdx == 0 ? FVector::Dist(GetActorLocation(), Pivot()) : FocusM[FocusIdx] * 100.0f;
}

void AODPhotoMode::Enter()
{
	APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0);
	if (!PC || !PC->PlayerCameraManager)
	{
		return;
	}
	// the view must keep updating while paused (AODMainMenu sets the same)
	PC->PlayerCameraManager->PrimaryActorTick.bTickEvenWhenPaused = true;
	const FVector CamLoc = PC->PlayerCameraManager->GetCameraLocation();
	Fov = FMath::RoundToFloat(PC->PlayerCameraManager->GetFOVAngle() / 5.0f) * 5.0f;
	const FRotator ToCar = (Pivot() - CamLoc).Rotation();
	Yaw = ToCar.Yaw;
	Pitch = -ToCar.Pitch;
	DistCm = FMath::Clamp(FVector::Dist(CamLoc, Pivot()), 200.0f, FreeRadiusCm);
	FreeLoc = CamLoc;

	// exposure and vignette start from the grade: the highest-priority
	// unbound volume that sets the bias (AODAtmosphereController's Grade)
	const APostProcessVolume* Grade = nullptr;
	for (TActorIterator<APostProcessVolume> It(GetWorld()); It; ++It)
	{
		if (It->bUnbound && It->Settings.bOverride_AutoExposureBias && (!Grade || It->Priority > Grade->Priority))
		{
			Grade = *It;
		}
	}
	BaseBias = Grade ? Grade->Settings.AutoExposureBias : 0.0f;
	if (!bActive)
	{
		Vignette = Grade && Grade->Settings.bOverride_VignetteIntensity ? Grade->Settings.VignetteIntensity : 0.4f;
	}

	PrevViewTarget = PC->GetViewTarget();
	bActive = true;
	bUiHidden = false;
	UpdateCamera(0.0f);
	PC->SetViewTargetWithBlend(this, 0.0f);
	UE_LOG(LogTemp, Display, TEXT("ODPhotoMode: enter (fov %.0f, dist %.0f cm, base bias %.2f)"), Fov, DistCm, BaseBias);
}

void AODPhotoMode::Exit()
{
	if (!bActive)
	{
		return;
	}
	bActive = false;
	if (APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0))
	{
		AActor* Back = PrevViewTarget.IsValid() ? PrevViewTarget.Get() : PC->GetPawn();
		PC->SetViewTargetWithBlend(Back, 0.0f);
	}
	UE_LOG(LogTemp, Display, TEXT("ODPhotoMode: exit"));
}

void AODPhotoMode::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	if (bActive)
	{
		// real frame time: world Dt is no clock while paused. A camera moving
		// at rate x dt is frame-rate independent.
		UpdateCamera(FApp::GetDeltaTime());
	}
}

void AODPhotoMode::UpdateCamera(float RealDt)
{
	APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0);
	auto Axis = [PC](const FKey& Pos, const FKey& Neg, const FKey& Stick, float StickSign)
	{
		if (!PC)
		{
			return 0.0f;
		}
		return (PC->IsInputKeyDown(Pos) ? 1.0f : 0.0f) - (PC->IsInputKeyDown(Neg) ? 1.0f : 0.0f)
			+ StickSign * PC->GetInputAnalogKeyState(Stick);
	};
	const float MoveF = Axis(EKeys::W, EKeys::S, EKeys::Gamepad_LeftY, 1.0f);
	const float MoveR = Axis(EKeys::D, EKeys::A, EKeys::Gamepad_LeftX, 1.0f);
	const float MoveU = Axis(EKeys::R, EKeys::F, EKeys::Gamepad_RightTriggerAxis, 1.0f)
		- (PC ? PC->GetInputAnalogKeyState(EKeys::Gamepad_LeftTriggerAxis) : 0.0f);
	float LookX = Axis(EKeys::Invalid, EKeys::Invalid, EKeys::Gamepad_RightX, 1.0f) * 90.0f * RealDt;
	float LookY = Axis(EKeys::Invalid, EKeys::Invalid, EKeys::Gamepad_RightY, 1.0f) * 60.0f * RealDt;
	float Mx = 0.0f, My = 0.0f;
	if (PC && PC->IsInputKeyDown(EKeys::RightMouseButton) && PC->GetMousePosition(Mx, My))
	{
		if (bDragging)
		{
			LookX += (Mx - LastMouse.X) * 0.2f;
			LookY -= (My - LastMouse.Y) * 0.2f;
		}
		LastMouse = FVector2D(Mx, My);
		bDragging = true;
	}
	else
	{
		bDragging = false;
	}
	if (PC && PC->WasInputKeyJustPressed(EKeys::H))
	{
		bUiHidden = !bUiHidden;
	}
	if (PC && PC->WasInputKeyJustPressed(EKeys::SpaceBar))
	{
		Capture();
	}

	const FVector P = Pivot();
	FVector Loc;
	if (bFree)
	{
		Yaw += LookX;
		Pitch = FMath::Clamp(Pitch - LookY, -85.0f, 85.0f);
		const FRotator Flat(0.0f, Yaw, 0.0f);
		const FVector Fwd = FRotator(-Pitch, Yaw, 0.0f).Vector();
		FreeLoc += (Fwd * MoveF + FRotationMatrix(Flat).GetUnitAxis(EAxis::Y) * MoveR + FVector::UpVector * MoveU)
			* 800.0f * RealDt;
		// held within FreeRadiusCm of the car
		FreeLoc = P + (FreeLoc - P).GetClampedToMaxSize(FreeRadiusCm);
		Loc = FreeLoc;
	}
	else
	{
		Yaw += LookX - MoveR * 90.0f * RealDt;
		Pitch = FMath::Clamp(Pitch - LookY + MoveF * 60.0f * RealDt, -30.0f, 85.0f);
		DistCm = FMath::Clamp(DistCm * (1.0f - MoveU * RealDt), 200.0f, FreeRadiusCm);
		Loc = P - FRotator(-Pitch, Yaw, 0.0f).Vector() * DistCm;
	}
	// above the ground: down from just over the camera, ignoring the car
	FHitResult Hit;
	FCollisionQueryParams Params(SCENE_QUERY_STAT(ODPhotoGround), false, UGameplayStatics::GetPlayerPawn(this, 0));
	if (GetWorld()->LineTraceSingleByChannel(Hit, Loc + FVector(0, 0, 300.0), Loc - FVector(0, 0, 100000.0),
		ECC_Visibility, Params) && Loc.Z < Hit.ImpactPoint.Z + GroundClearCm)
	{
		Loc.Z = Hit.ImpactPoint.Z + GroundClearCm;
		FreeLoc.Z = bFree ? Loc.Z : FreeLoc.Z;
	}
	const FRotator Rot = bFree ? FRotator(-Pitch, Yaw, Roll) : FRotator((P - Loc).Rotation().Pitch, Yaw, Roll);
	SetActorLocationAndRotation(Loc, Rot);
	Camera->SetFieldOfView(Fov);
	ApplyPostProcess();
}

void AODPhotoMode::ApplyPostProcess()
{
	FPostProcessSettings& S = Camera->PostProcessSettings;
	S.bOverride_AutoExposureBias = true;
	S.AutoExposureBias = BaseBias + ExposureEV;
	S.bOverride_VignetteIntensity = true;
	S.VignetteIntensity = Vignette;
	const bool bDof = ApertureIdx > 0;
	S.bOverride_DepthOfFieldFstop = bDof;
	S.bOverride_DepthOfFieldFocalDistance = bDof;
	S.DepthOfFieldFstop = bDof ? Fstops[ApertureIdx] : 4.0f;
	S.DepthOfFieldFocalDistance = bDof ? FocusCm() : 0.0f;
}

void AODPhotoMode::GetRow(int32 Row, FString& OutLabel, FString& OutValue) const
{
	if (Row >= RowEnv && Row < RowCapture)
	{
		ODEnvironment::GetRow(GetWorld(), Row - RowEnv, OutLabel, OutValue);
		return;
	}
	OutValue.Empty();
	switch (Row)
	{
	case RowFov:      OutLabel = TEXT("Field of View"); OutValue = FString::Printf(TEXT("%.0f"), Fov); break;
	case RowRoll:     OutLabel = TEXT("Roll"); OutValue = FString::Printf(TEXT("%+.0f"), Roll); break;
	case RowExposure: OutLabel = TEXT("Exposure"); OutValue = FString::Printf(TEXT("%+.1f EV"), ExposureEV); break;
	case RowFocus:
		OutLabel = TEXT("Focus");
		OutValue = FocusIdx == 0 ? FString::Printf(TEXT("Auto (%.0f m)"), FocusCm() / 100.0f)
			: FString::Printf(TEXT("%.0f m"), FocusM[FocusIdx]);
		break;
	case RowAperture:
		OutLabel = TEXT("Depth of Field");
		OutValue = ApertureIdx == 0 ? FString(TEXT("Off")) : FString::Printf(TEXT("f/%.1f"), Fstops[ApertureIdx]);
		break;
	case RowVignette: OutLabel = TEXT("Vignette"); OutValue = FString::Printf(TEXT("%.0f%%"), Vignette * 100.0f); break;
	case RowCamera:   OutLabel = TEXT("Camera"); OutValue = bFree ? TEXT("Free") : TEXT("Orbit"); break;
	case RowCapture:  OutLabel = TEXT("Take Photo"); break;
	default:          OutLabel.Empty(); break;
	}
}

void AODPhotoMode::Adjust(int32 Row, int32 Dir)
{
	if (Row >= RowEnv && Row < RowCapture)
	{
		ODEnvironment::Adjust(GetWorld(), Row - RowEnv, Dir);
		return;
	}
	switch (Row)
	{
	case RowFov:      Fov = FMath::Clamp(Fov + 5.0f * Dir, 15.0f, 120.0f); break;
	case RowRoll:     Roll = FMath::Clamp(Roll + 5.0f * Dir, -45.0f, 45.0f); break;
	case RowExposure: ExposureEV = FMath::Clamp(ExposureEV + 0.5f * Dir, -4.0f, 4.0f); break;
	case RowFocus:    FocusIdx = Cycle(FocusIdx, Dir, FocusM); break;
	case RowAperture: ApertureIdx = Cycle(ApertureIdx, Dir, Fstops); break;
	case RowVignette: Vignette = FMath::Clamp(FMath::RoundToFloat(Vignette * 10.0f + Dir) / 10.0f, 0.0f, 1.0f); break;
	case RowCamera:
		bFree = !bFree;
		FreeLoc = GetActorLocation();   // free-fly starts where the orbit was (and back)
		if (!bFree)
		{
			DistCm = FMath::Clamp(FVector::Dist(FreeLoc, Pivot()), 200.0f, FreeRadiusCm);
			Yaw = (Pivot() - FreeLoc).Rotation().Yaw;
			Pitch = -(Pivot() - FreeLoc).Rotation().Pitch;
		}
		break;
	default:
		break;
	}
	if (bActive)
	{
		UpdateCamera(0.0f);
	}
}

FString AODPhotoMode::Capture()
{
	const AODRoadManager* Road = nullptr;
	for (TActorIterator<AODRoadManager> It(GetWorld()); It && !Road; ++It)
	{
		Road = *It;
	}
	const FString Area = Road ? Road->GetAreaKey() : UGameplayStatics::GetCurrentLevelName(this);
	const FString Path = FPaths::ConvertRelativePathToFull(FPaths::ProjectSavedDir() / TEXT("Screenshots") /
		FString::Printf(TEXT("OpenDrive_%s_%s.png"), *Area, *FDateTime::Now().ToString(TEXT("%Y%m%d_%H%M%S"))));
	// no UI: the HUD skips the panel while a request is pending, and the
	// request is read back on the next viewport draw
	// ponytail: viewport resolution only - add a HighResShot 2x/4x row if wanted
	FScreenshotRequest::RequestScreenshot(Path, false, false);
	ToastText = FString::Printf(TEXT("Saved %s"), *FPaths::GetCleanFilename(Path));
	ToastUntil = FPlatformTime::Seconds() + 4.0;
	UE_LOG(LogTemp, Display, TEXT("ODPhotoMode: capture -> %s"), *Path);
	return Path;
}

FString AODPhotoMode::GetToast() const
{
	return FPlatformTime::Seconds() < ToastUntil ? ToastText : FString();
}

void AODPhotoMode::MaybeStartSelfCheck(UWorld* World)
{
	float At = -1.0f;
	if (!World || !FParse::Value(FCommandLine::Get(), TEXT("odphotocheck="), At) || At <= 0.0f)
	{
		return;
	}
	// one step per real second: 0 open photo mode through the pause menu,
	// 1 check the view + post-process, try to fly out past the radius, scrub
	// time, capture, 2 check the clamp, time and file, back out to the pause menu
	TWeakObjectPtr<UWorld> WeakWorld(World);
	const FTickerDelegate Steps = FTickerDelegate::CreateLambda(
		[WeakWorld, Step = 0, Shot = FString(), Hour = 0.0f, Fails = FString()](float) mutable
		{
			UWorld* W = WeakWorld.Get();
			AODPauseMenu* Menu = nullptr;
			for (TActorIterator<AODPauseMenu> It(W); It && !Menu; ++It)
			{
				Menu = *It;
			}
			APlayerController* PC = W ? W->GetFirstPlayerController() : nullptr;
			AODPhotoMode* Photo = Menu ? Menu->GetPhotoMode() : nullptr;
			auto Expect = [&Fails](bool bOk, const FString& What)
			{
				UE_LOG(LogTemp, Display, TEXT("ODPhotoCheck:   %s %s"), bOk ? TEXT("ok  ") : TEXT("FAIL"), *What);
				Fails += bOk ? TEXT("") : What + TEXT("; ");
			};
			if (!Menu || !PC)
			{
				UE_LOG(LogTemp, Display, TEXT("ODPhotoCheck: FAIL (no pause menu / player controller)"));
				return false;
			}
			const AODDayNightController* DN = nullptr;
			for (TActorIterator<AODDayNightController> It(W); It && !DN; ++It)
			{
				DN = *It;
			}
			FString L, V;
			switch (Step++)
			{
			case 0:
				Menu->TogglePhotoMode();
				return true;
			case 1:
				Expect(Photo && Photo->IsActive() && Menu->IsPhotoOpen(), TEXT("photo mode open"));
				if (!Photo)
				{
					break;
				}
				Expect(W->IsPaused(), TEXT("game paused"));
				Expect(PC->GetViewTarget() == Photo, TEXT("photo camera is the view target"));
				Photo->Adjust(RowFov, -2);
				Photo->Adjust(RowRoll, +2);
				Photo->Adjust(RowExposure, +1);
				Photo->Adjust(RowAperture, +4);   // f/4
				Photo->Adjust(RowVignette, +1);
				{
					const FPostProcessSettings& S = Photo->Camera->PostProcessSettings;
					Expect(S.bOverride_DepthOfFieldFstop && FMath::IsNearlyEqual(S.DepthOfFieldFstop, 4.0f)
						&& S.DepthOfFieldFocalDistance > 0.0f,
						FString::Printf(TEXT("DOF f/%.1f focus %.0f cm"), S.DepthOfFieldFstop, S.DepthOfFieldFocalDistance));
					Expect(FMath::IsNearlyEqual(S.AutoExposureBias, Photo->BaseBias + 0.5f),
						FString::Printf(TEXT("exposure bias %.2f = grade %.2f + 0.5"), S.AutoExposureBias, Photo->BaseBias));
					Expect(FMath::IsNearlyEqual(Photo->GetActorRotation().Roll, 10.0f, 0.1f),
						FString::Printf(TEXT("roll %.1f"), Photo->GetActorRotation().Roll));
					Expect(FMath::IsNearlyEqual(Photo->Camera->FieldOfView, Photo->Fov),
						FString::Printf(TEXT("fov %.0f"), Photo->Camera->FieldOfView));
				}
				Photo->Adjust(RowCamera, +1);   // free-fly
				Photo->FreeLoc += FVector(100000.0, 0.0, 0.0);   // 1 km out: the next tick must pull it back
				if (DN)
				{
					Hour = DN->TimeOfDay;
					Photo->Adjust(RowEnv + ODEnvironment::RowTime, +4);   // +1 h through the photo rows
				}
				Shot = Photo->Capture();
				Expect(Shot.Contains(TEXT("Saved/Screenshots/OpenDrive_")) && FScreenshotRequest::IsScreenshotRequested(),
					FString::Printf(TEXT("capture queued to %s"), *Shot));
				return true;
			case 2:
				if (!Photo)
				{
					break;
				}
				// across the ground: the terrain lift may add height past the radius
				Expect(FVector::Dist2D(Photo->GetActorLocation(), Photo->Pivot()) <= FreeRadiusCm + 1.0f,
					FString::Printf(TEXT("free-fly held within 50 m (%.0f cm)"),
						FVector::Dist2D(Photo->GetActorLocation(), Photo->Pivot())));
				if (DN)
				{
					Expect(FMath::IsNearlyEqual(FMath::Fmod(DN->TimeOfDay - Hour + 24.0f, 24.0f), 1.0f, 0.13f),
						FString::Printf(TEXT("environment row moved the clock %.2f -> %.2f"), Hour, DN->TimeOfDay));
				}
				// -nullrhi never draws a viewport, so nothing is written there
				Expect(!FApp::CanEverRender() || IFileManager::Get().FileExists(*Shot),
					FString::Printf(TEXT("screenshot file %s"), FApp::CanEverRender() ? TEXT("written") : TEXT("skipped (no RHI)")));
				Menu->Back();
				Expect(!Photo->IsActive() && !Menu->IsPhotoOpen() && Menu->IsOpen()
					&& PC->GetViewTarget() != Photo, TEXT("Escape returns to the pause menu and the car camera"));
				Menu->Back();   // resume
				break;
			default:
				break;
			}
			UE_LOG(LogTemp, Display, TEXT("ODPhotoCheck: %s %s"), Fails.IsEmpty() ? TEXT("PASS") : TEXT("FAIL"), *Fails);
			return false;
		});
	FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateLambda([Steps](float)
	{
		FTSTicker::GetCoreTicker().AddTicker(Steps, 1.0f);
		return false;
	}), At);
}
