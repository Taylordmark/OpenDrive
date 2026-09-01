#include "ODMainMenu.h"

#include "Components/InputComponent.h"
#include "Containers/Ticker.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/App.h"
#include "Misc/CommandLine.h"
#include "UnrealClient.h"

#include "ODEngineSynth.h"
#include "ODGarageEnvironment.h"
#include "ODRoadManager.h"
#include "ODTerrainManager.h"
#include "ODTuningManager.h"
#include "ODVehicleAudio.h"
#include "ODVehicleDef.h"
#include "ODVehicleMovement.h"
#include "ODVehiclePawn.h"

namespace
{
// Engine characters - the same table AODPauseMenu offers, so a car set up here
// reads identically if the player later tweaks it from the pause menu.
struct FEngineDef
{
	const TCHAR* Name;
	int32 Cylinders;
	float DisplacementL;
	float PeakNm, PeakRpm, MaxRpm, LimiterRpm, Inertia;
};
const FEngineDef Engines[] = {
	{TEXT("V8 Muscle"),    8, 6.2f, 420.0f, 4600.0f, 7000.0f, 6850.0f, 0.25f},
	{TEXT("V6 Sport"),     6, 3.5f, 335.0f, 5500.0f, 7900.0f, 7750.0f, 0.17f},
	{TEXT("V10 Screamer"), 10, 5.2f, 480.0f, 6800.0f, 9000.0f, 8850.0f, 0.14f},
	{TEXT("V12 GT"),       12, 6.0f, 580.0f, 5800.0f, 8600.0f, 8450.0f, 0.19f},
};

struct FLayoutDef
{
	const TCHAR* Name;
	EODDriveLayout Layout;
};
const FLayoutDef Layouts[] = {
	{TEXT("RWD"), EODDriveLayout::RWD},
	{TEXT("FWD"), EODDriveLayout::FWD},
	{TEXT("AWD"), EODDriveLayout::AWD},
};

template <typename T>
T* FindActor(UWorld* World)
{
	for (TActorIterator<T> It(World); It; ++It)
	{
		return *It;
	}
	return nullptr;
}
}

AODMainMenu::AODMainMenu()
{
	PrimaryActorTick.bCanEverTick = true;
	// the game is paused while the menu is up; still tick so the preview car
	// can spin and EnterCarConfig can retry until the pawn is ready
	PrimaryActorTick.bTickEvenWhenPaused = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODMainMenu::BeginPlay()
{
	Super::BeginPlay();
	APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0);
	if (!PC)
	{
		return;
	}
	// THE actual "still under/inside the car" bug (2026-07-28) - found only
	// after several rounds of camera fixes (arm length, collision, lag, a
	// full rewrite to bypass the spring arm entirely) all produced the
	// IDENTICAL rendered frame, which a logged comparison finally explained:
	// APlayerCameraManager doesn't tick while the game is paused (default UE
	// behavior), and this menu pauses the game within the first frame or two
	// of boot - so the camera manager's cached view froze at whatever the
	// very FIRST frame's raw, not-yet-positioned camera happened to be
	// (before the spring arm had ticked even once to place it), and never
	// updated again for the rest of the paused session. Every subsequent
	// Camera/SpringArm change was real and correct - none of it was ever
	// being picked up for rendering. This is the same "doesn't tick while
	// paused" class of bug as AODVehiclePawn/AODScreenshotHook elsewhere in
	// this session, just in an engine class this project doesn't subclass.
	if (PC->PlayerCameraManager)
	{
		PC->PlayerCameraManager->PrimaryActorTick.bTickEvenWhenPaused = true;
	}
	EnableInput(PC);
	if (InputComponent)
	{
		auto Bind = [&](const FKey& Key, void (AODMainMenu::*Fn)())
		{
			FInputKeyBinding& B = InputComponent->BindKey(Key, IE_Pressed, this, Fn);
			B.bExecuteWhenPaused = true;   // must work while the game is paused
			B.bConsumeInput = false;       // pause-menu binds stay guarded, not fought
		};
		Bind(EKeys::Up, &AODMainMenu::Up);
		Bind(EKeys::Down, &AODMainMenu::Down);
		Bind(EKeys::Left, &AODMainMenu::Left);
		Bind(EKeys::Right, &AODMainMenu::Right);
		Bind(EKeys::Enter, &AODMainMenu::Activate);
		Bind(EKeys::Escape, &AODMainMenu::Back);
		Bind(EKeys::LeftMouseButton, &AODMainMenu::OnClick);
		// gamepad menu navigation (2026-08-27) - cheap to add alongside the
		// keyboard binds above (same Bind() helper, same "must work while
		// paused" flags), so a gamepad-only player isn't stuck needing a
		// keyboard to get through the front-end.
		Bind(EKeys::Gamepad_DPad_Up, &AODMainMenu::Up);
		Bind(EKeys::Gamepad_DPad_Down, &AODMainMenu::Down);
		Bind(EKeys::Gamepad_DPad_Left, &AODMainMenu::Left);
		Bind(EKeys::Gamepad_DPad_Right, &AODMainMenu::Right);
		Bind(EKeys::Gamepad_FaceButton_Bottom, &AODMainMenu::Activate);
		Bind(EKeys::Gamepad_FaceButton_Right, &AODMainMenu::Back);
	}

	// headless QA hooks (core ticker, like AODPauseMenu's -odmenu - world
	// timers freeze once paused)
	FParse::Value(FCommandLine::Get(), TEXT("odmenumap="), MenuMapAt);
	FParse::Value(FCommandLine::Get(), TEXT("odpick="), PickTown);
	FParse::Value(FCommandLine::Get(), TEXT("odgo="), GoAt);
	if (MenuMapAt > 0.0f)
	{
		FTSTicker::GetCoreTicker().AddTicker(
			FTickerDelegate::CreateWeakLambda(this, [this](float)
			{
				if (Stage == EODMenuStage::CarConfig)
				{
					GoToMap();
					UE_LOG(LogTemp, Display, TEXT("ODMainMenu: test-open map"));
				}
				return false;
			}), MenuMapAt);
		FTSTicker::GetCoreTicker().AddTicker(
			FTickerDelegate::CreateWeakLambda(this, [](float)
			{
				FScreenshotRequest::RequestScreenshot(false);
				return false;
			}), MenuMapAt + 1.5f);
	}
	if (!PickTown.IsEmpty())
	{
		FTSTicker::GetCoreTicker().AddTicker(
			FTickerDelegate::CreateWeakLambda(this, [this](float)
			{
				if (bPickDone)
				{
					return false;
				}
				const AODRoadManager* Road = FindActor<AODRoadManager>(GetWorld());
				if (Road)
				{
					const TArray<FTownNode>& Nodes = Road->GetTownNodes();
					for (int32 i = 0; i < Nodes.Num(); ++i)
					{
						if (Nodes[i].Name.Contains(PickTown))
						{
							bPickDone = true;
							StartAt(i);
							UE_LOG(LogTemp, Display,
								TEXT("ODMainMenu: -odpick spawned at '%s'"), *Nodes[i].Name);
							break;
						}
					}
				}
				return false;
			}), 6.0f);
	}

	if (GoAt > 0.0f)
	{
		FTSTicker::GetCoreTicker().AddTicker(
			FTickerDelegate::CreateWeakLambda(this, [this](float)
			{
				if (!bGoDone)
				{
					bGoDone = true;
					StartAt(-1);
					UE_LOG(LogTemp, Display, TEXT("ODMainMenu: -odgo started at PlayerStart"));
				}
				return false;
			}), GoAt);
	}

	OpenMenu();
}

void AODMainMenu::OpenMenu()
{
	Stage = EODMenuStage::CarConfig;
	bGarageMode = false;
	Selected = 0;
	HoveredTownIdx = INDEX_NONE;
	bEntered = false;   // EnterCarConfig() re-runs once the pawn is confirmed
	// Deliberately NOT pausing here (2026-07-28, unlike OpenGarage() below) -
	// see this class's declaration comment for the full account, but the
	// short version: pausing broke the showroom camera in a way that took
	// several rounds of fixes (spring arm lag/collision/arm-length, a full
	// rewrite bypassing the arm, then the camera manager's own tick) to even
	// properly diagnose, because APlayerCameraManager's cached view simply
	// stops updating the instant the world pauses - it froze at whatever the
	// very first, not-yet-positioned frame looked like and never moved again
	// for the rest of the menu, regardless of what the underlying Camera/
	// SpringArm state actually was. The ONLY reason this menu paused at all
	// was so the frozen preview pawn couldn't drive off - already guaranteed
	// on its own by PlaceInShowroom's SetSimulatePhysics(false) (a
	// non-simulating body ignores input-driven forces entirely), so pausing
	// was never actually load-bearing for that goal. OpenGarage() still
	// pauses because it opens MID-DRIVE, where the camera is already
	// correctly positioned from live gameplay before the freeze - a working
	// case this fix doesn't need to touch.
	SetCursorVisible(true);
}

void AODMainMenu::OpenGarage()
{
	Stage = EODMenuStage::CarConfig;
	bGarageMode = true;
	Selected = 0;
	bEntered = false;   // re-sync the displayed engine/drivetrain with reality
	UGameplayStatics::SetGamePaused(this, true);
	// 2026-08-27 fix: this call was missing entirely, so the pause menu's
	// "Garage" row left the cursor exactly as the driving path had set it
	// (hidden, FInputModeGameOnly) - unlike OpenMenu(), which has always
	// called this. Without it the mouse is invisible and every click/move
	// goes to the game instead of the garage UI.
	SetCursorVisible(true);
}

void AODMainMenu::SetCursorVisible(bool bVisible)
{
	if (APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0))
	{
		PC->bShowMouseCursor = bVisible;
		// visibility alone is not enough: without GameOnly capture the
		// hidden OS cursor keeps moving under the game and pins against the
		// viewport edge, at which point GetInputMouseDelta() reads zero in
		// that direction - freelook orbits partway then "locks". Capture the
		// mouse while driving; release it (GameAndUI, unlocked) for menus.
		if (bVisible)
		{
			FInputModeGameAndUI Mode;
			Mode.SetLockMouseToViewportBehavior(EMouseLockMode::DoNotLock);
			Mode.SetHideCursorDuringCapture(false);
			PC->SetInputMode(Mode);
		}
		else
		{
			PC->SetInputMode(FInputModeGameOnly());
		}
	}
}

void AODMainMenu::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	if (Stage == EODMenuStage::Loading)
	{
		// polls the same settle signal TrySnapToGround uses for itself, PLUS
		// both tile managers' own readiness (2026-08-27) - see this stage's
		// declaration comment and StartAt()'s comment for why
		// IsGroundSnapped() alone isn't enough: the car can be resolved onto
		// real pavement while the terrain/road tiles around it are still
		// mid-build, which is exactly the "world fills in around the player"
		// Taylor saw live. A manager that doesn't exist on this level (e.g. a
		// bare menu-only map) is treated as already settled, same optional-
		// actor convention as everything else that reads these managers.
		const double Now = GetWorld()->GetTimeSeconds();
		const AODVehiclePawn* Pawn = FindPawn();
		const bool bPawnReady = Pawn && Pawn->IsGroundSnapped();
		const AODTerrainManager* Terrain = FindActor<AODTerrainManager>(GetWorld());
		const AODRoadManager* Road = FindActor<AODRoadManager>(GetWorld());
		const bool bTerrainSettled = !Terrain
			|| Terrain->IsInitialStreamingSettled(Pawn ? Pawn->GetActorLocation() : FVector::ZeroVector);
		const bool bRoadSettled = !Road || Road->IsInitialStreamingSettled();
		const bool bAllSettled = bPawnReady && bTerrainSettled && bRoadSettled;

		if (bAllSettled)
		{
			if (LoadingSettledSinceTime == 0.0)
			{
				LoadingSettledSinceTime = Now;
			}
		}
		else
		{
			LoadingSettledSinceTime = 0.0;   // dropped out - restart the debounce window
		}

		const double Elapsed = Now - LoadingStartTime;

		// hard failsafe: release regardless of settle state so a genuinely
		// stuck streaming condition (missing sidecar, pathological tile)
		// can't strand the player on a black screen forever.
		if (Elapsed > LoadingFailsafeSec)
		{
			UE_LOG(LogTemp, Warning,
				TEXT("ODMainMenu: LOADSCREEN failsafe released after %.1fs (groundSnapped=%d, ")
				TEXT("terrainSettled=%d, roadSettled=%d)"),
				Elapsed, bPawnReady, bTerrainSettled, bRoadSettled);
			Stage = EODMenuStage::Playing;
			SetCursorVisible(false);
			return;
		}

		// debounced (continuously settled for LoadingSettleDebounceSec) AND
		// past the minimum display floor (so a same-town reload where
		// everything is already resident doesn't just flash the screen).
		const bool bDebounced = LoadingSettledSinceTime != 0.0
			&& (Now - LoadingSettledSinceTime) >= LoadingSettleDebounceSec;
		if (bDebounced && Elapsed >= LoadingMinDisplaySec)
		{
			UE_LOG(LogTemp, Display,
				TEXT("ODMainMenu: LOADSCREEN released after %.1fs (terrain settled, roads settled)"),
				Elapsed);
			Stage = EODMenuStage::Playing;
			SetCursorVisible(false);
		}
		return;
	}
	if (Stage != EODMenuStage::CarConfig)
	{
		return;
	}
	if (!bEntered)
	{
		EnterCarConfig();
	}
}

void AODMainMenu::EnterCarConfig()
{
	AODVehiclePawn* Pawn = FindPawn();
	if (!Pawn)
	{
		return;   // pawn not spawned yet - retry next tick
	}
	if (!bGarageMode)
	{
		// Fresh boot only - NOT the pause menu's "Garage" reopen, where the
		// car is already correctly settled on real road and OpenGarage()'s
		// own contract is to leave it exactly there (no re-teleport). Taylor,
		// 2026-07-28 live: "you spawn directly under the car and the car
		// isn't moving." Root cause: this actor pauses the game (so the
		// frozen preview pawn can't drive off) before AODVehiclePawn's own
		// BeginPlay ground-snap wait has necessarily finished - neither the
		// pawn nor the tile-streaming managers tick while paused, so the car
		// could be frozen mid-air at ANY point of that wait (including its
		// initial spawn-height lift) for as long as the player stays on this
		// screen. Fixed by not depending on the real world at all here: move
		// the preview to a fixed, self-contained void first (capturing the
		// real spawn point once, so "Drive" with no town picked still starts
		// in the right place - see StartAt()).
		if (!bCapturedSpawn)
		{
			CapturedSpawnLoc = Pawn->GetActorLocation();
			CapturedSpawnYaw = Pawn->GetActorRotation().Yaw;
			bCapturedSpawn = true;
		}
		Pawn->PlaceInShowroom(ShowroomLocationCm);
	}
	Pawn->SetShowroom(true);
	// static display angle, not a spin (Taylor, 2026-07-28: "stop the car
	// rotating stuff too") - a single fixed 3/4 view, set once.
	Pawn->SetActorRotation(FRotator(0.0f, ShowroomYawDeg, 0.0f));

	// Garage room (2026-08): built around wherever the car is RIGHT NOW, which
	// covers both entry paths this function serves - fresh boot just placed
	// the pawn at ShowroomLocationCm a few lines up, and a mid-drive
	// OpenGarage() reopen leaves the pawn at its real on-map location. Either
	// way, "current location" is the correct pivot, so one call site here
	// covers both.
	if (AODGarageEnvironment* Garage = EnsureGarageEnvironment())
	{
		// pawn's actor origin is the body pivot at axle height, not ground
		// level (2026-08-27 "cars sink into the garage floor" fix) - drop the
		// floor by the wheels' actual clearance below that pivot so it lands
		// under the tires instead of at axle height.
		float GroundOffset = 0.0f;
		if (const UODVehicleMovement* Move = FindMovement())
		{
			GroundOffset = Move->GetRestGroundClearanceCm();
		}
		Garage->PlaceAt(Pawn->GetActorLocation(), ShowroomYawDeg, GroundOffset);
		Garage->SetActive(true);
	}
	// sync the displayed engine/drivetrain with reality (leave tuning at stock
	// until the player picks one, same as the pause menu)
	ApplyEngine();
	ApplyDrivetrain();
	bEntered = true;
}

// ---- input ---------------------------------------------------------------

void AODMainMenu::Up()
{
	if (Stage == EODMenuStage::CarConfig)
	{
		Selected = (Selected + NumRows() - 1) % NumRows();
	}
}

void AODMainMenu::Down()
{
	if (Stage == EODMenuStage::CarConfig)
	{
		Selected = (Selected + 1) % NumRows();
	}
}

void AODMainMenu::Left()
{
	if (Stage == EODMenuStage::CarConfig)
	{
		Adjust(-1);
	}
}

void AODMainMenu::Right()
{
	if (Stage == EODMenuStage::CarConfig)
	{
		Adjust(+1);
	}
}

void AODMainMenu::Activate()
{
	if (Stage == EODMenuStage::CarConfig)
	{
		if (Selected == 5)          // "Drive" / "Resume Driving" row
		{
			if (bGarageMode)
			{
				ResumeFromGarage();
			}
			else
			{
				GoToMap();
			}
		}
		else
		{
			Adjust(+1);             // Enter cycles a value row, like the pause menu
		}
	}
	else if (Stage == EODMenuStage::MapSelect)
	{
		StartAt(HoveredTownIdx);    // -1 (no town) starts at the level's PlayerStart
	}
}

void AODMainMenu::Back()
{
	if (Stage == EODMenuStage::MapSelect)
	{
		Stage = EODMenuStage::CarConfig;   // back to the car screen
		Selected = 5;
	}
	else if (Stage == EODMenuStage::CarConfig && bGarageMode)
	{
		ResumeFromGarage();   // Escape also exits the garage back to driving
	}
	// CarConfig, non-garage: no-op (you commit by driving, there's nothing to back out to)
}

void AODMainMenu::OnClick()
{
	if (Stage == EODMenuStage::MapSelect && HoveredTownIdx != INDEX_NONE)
	{
		StartAt(HoveredTownIdx);
	}
}

void AODMainMenu::Adjust(int32 Dir)
{
	switch (Selected)
	{
	case 0:
		ChangeCar(Dir);
		break;
	case 1:
		EngineIdx = (EngineIdx + Dir + UE_ARRAY_COUNT(Engines)) % UE_ARRAY_COUNT(Engines);
		ApplyEngine();
		break;
	case 2:
		DriveIdx = (DriveIdx + Dir + UE_ARRAY_COUNT(Layouts)) % UE_ARRAY_COUNT(Layouts);
		ApplyDrivetrain();
		break;
	case 3:
		if (const int32 N = NumPresets(); N > 0)
		{
			TuneIdx = (TuneIdx + Dir + N) % N;
			ApplyTuning();
		}
		break;
	case 4:
		// Input Device row (2026-08-27): Auto/Keyboard/Gamepad/Wheel, cycling
		// the same way the other value rows do. Persisted on the pawn itself
		// (SetInputDeviceMode writes it to GameUserSettings.ini) rather than
		// mirrored here, since the pawn is what actually needs it every Tick.
		if (AODVehiclePawn* Pawn = FindPawn())
		{
			constexpr int32 NumModes = static_cast<int32>(EODInputDevice::Wheel) + 1;
			const int32 Next = (static_cast<int32>(Pawn->GetInputDeviceMode()) + Dir + NumModes) % NumModes;
			Pawn->SetInputDeviceMode(static_cast<EODInputDevice>(Next));
		}
		break;
	default:
		break;
	}
}

// ---- live application ----------------------------------------------------

AODVehiclePawn* AODMainMenu::FindPawn() const
{
	return Cast<AODVehiclePawn>(UGameplayStatics::GetPlayerPawn(GetWorld(), 0));
}

UODVehicleMovement* AODMainMenu::FindMovement() const
{
	AODVehiclePawn* Pawn = FindPawn();
	return Pawn ? Pawn->FindComponentByClass<UODVehicleMovement>() : nullptr;
}

int32 AODMainMenu::NumCars() const
{
	const AODVehiclePawn* Pawn = FindPawn();
	return Pawn ? Pawn->GetVehicleDefs().Num() : 0;
}

int32 AODMainMenu::NumPresets() const
{
	const AODTuningManager* Tuning = FindActor<AODTuningManager>(GetWorld());
	return Tuning ? Tuning->Presets.Num() : 0;
}

void AODMainMenu::ApplyEngine()
{
	const FEngineDef& E = Engines[EngineIdx];
	if (UODVehicleMovement* D = FindMovement())
	{
		D->PeakTorqueNm = E.PeakNm;
		D->PeakTorqueRpm = E.PeakRpm;
		D->MaxRpm = E.MaxRpm;
		D->LimiterRpm = E.LimiterRpm;
		D->EngineInertia = E.Inertia;
	}
	if (AODVehicleAudio* Audio = FindActor<AODVehicleAudio>(GetWorld()))
	{
		if (UODEngineSynth* Synth = Audio->GetEngineSynth())
		{
			Synth->ConfigureEngine(E.Cylinders, E.DisplacementL);
		}
	}
}

void AODMainMenu::ApplyDrivetrain()
{
	if (UODVehicleMovement* D = FindMovement())
	{
		D->SetDriveLayout(Layouts[DriveIdx].Layout);
	}
}

void AODMainMenu::ApplyTuning()
{
	if (AODTuningManager* Tuning = FindActor<AODTuningManager>(GetWorld()))
	{
		Tuning->ApplyPresetByIndex(TuneIdx);
	}
}

void AODMainMenu::ChangeCar(int32 Dir)
{
	AODVehiclePawn* Pawn = FindPawn();
	const int32 N = NumCars();
	if (!Pawn || N < 1)
	{
		return;   // no def assets - hardcoded coupe only, nothing to cycle
	}
	const int32 Next = (Pawn->GetVehicleIndex() + Dir + N) % N;
	Pawn->SetVehicleByIndex(Next);
	// ApplyVehicleDef reset engine/drivetrain to the def's baseline (and
	// re-applied any active tuning preset) - re-assert the menu's picks on top
	ApplyEngine();
	ApplyDrivetrain();
}

// ---- stage transitions ---------------------------------------------------

void AODMainMenu::GoToMap()
{
	Stage = EODMenuStage::MapSelect;
	HoveredTownIdx = INDEX_NONE;
	SetCursorVisible(true);
}

void AODMainMenu::StartAt(int32 TownIdx)
{
	AODVehiclePawn* Pawn = FindPawn();
	if (!Pawn)
	{
		return;
	}

	FVector2D Target = FVector2D::ZeroVector;   // only read when bHaveTarget is true
	float Yaw = Pawn->GetActorRotation().Yaw;
	bool bHaveTarget = false;
	if (AODRoadManager* Road = FindActor<AODRoadManager>(GetWorld());
		Road && Road->GetTownNodes().IsValidIndex(TownIdx))
	{
		FVector2D Snap;
		float SnapYaw;
		if (Road->NearestRoadPoint(Road->GetTownNodes()[TownIdx].Pos, Snap, SnapYaw))
		{
			Target = Snap;
			Yaw = SnapYaw;
			bHaveTarget = true;
		}
	}

	Pawn->SetShowroom(false);
	if (GarageEnv.IsValid())
	{
		GarageEnv->SetActive(false);
	}
	if (bHaveTarget)
	{
		Pawn->TeleportToSpawn(FVector(Target.X, Target.Y, 0.0f), Yaw);
	}
	else
	{
		// no town picked / no place data - start where the level's PlayerStart
		// originally put us. NOT Pawn->GetActorLocation() (2026-07-28 bug fix):
		// the pawn has been sitting in the showroom void since EnterCarConfig(),
		// so its CURRENT location is nowhere near the real map - CapturedSpawnLoc
		// is the one captured before that move happened.
		const FVector Loc = bCapturedSpawn ? CapturedSpawnLoc : Pawn->GetActorLocation();
		const float SpawnYaw = bCapturedSpawn ? CapturedSpawnYaw : Yaw;
		Pawn->TeleportToSpawn(FVector(Loc.X, Loc.Y, 0.0f), SpawnYaw);
	}

	// debug override for isolated-junction test builds (experimental_
	// junction_pads.py --solo): TeleportToSpawn just placed the pawn on
	// whatever real road point the picked town snapped to, which is nowhere
	// near the one junction that script isolates - override the XY here
	// (AFTER the real teleport, not in AODVehiclePawn::BeginPlay, where it
	// silently no-ops - this pawn persists from the menu map and BeginPlay
	// only ever fires once, before StartAt() moves it here).
	float OdSpawnX = 0.0f, OdSpawnY = 0.0f;
	if (FParse::Value(FCommandLine::Get(), TEXT("odspawnx="), OdSpawnX)
		&& FParse::Value(FCommandLine::Get(), TEXT("odspawny="), OdSpawnY))
	{
		UE_LOG(LogTemp, Display, TEXT("ODMainMenu: odspawn override -> (%.0f,%.0f)"), OdSpawnX, OdSpawnY);
		Pawn->TeleportToSpawn(FVector(OdSpawnX, OdSpawnY, 0.0f), Yaw);
	}

	// Unpause NOW, before handing control back - tile streaming
	// (AODRoadManager/AODTerrainManager) only runs while unpaused, and a
	// fresh teleport target can have nothing loaded there yet. Loading stage
	// keeps the player from seeing/driving a possibly-airborne car until
	// Tick() confirms IsGroundSnapped() AND both managers' initial streaming
	// has settled (2026-08-27, spawn-loading-screen task: IsGroundSnapped()
	// alone only proves the car itself is standing on real pavement, not that
	// the surrounding terrain/road ring has finished building - Taylor's live
	// drive-test still saw the world visibly fill in right after the old
	// loading screen dropped).
	Stage = EODMenuStage::Loading;
	LoadingStartTime = GetWorld()->GetTimeSeconds();
	LoadingSettledSinceTime = 0.0;
	SetCursorVisible(false);
	UGameplayStatics::SetGamePaused(this, false);
}

void AODMainMenu::ResumeFromGarage()
{
	// no teleport here - the car was already sitting on real road when the
	// pause menu opened it, so there's nothing to wait on (unlike StartAt()).
	if (AODVehiclePawn* Pawn = FindPawn())
	{
		Pawn->SetShowroom(false);
	}
	if (GarageEnv.IsValid())
	{
		GarageEnv->SetActive(false);
	}
	Stage = EODMenuStage::Playing;
	SetCursorVisible(false);
	UGameplayStatics::SetGamePaused(this, false);
	bGarageMode = false;
}

AODGarageEnvironment* AODMainMenu::EnsureGarageEnvironment()
{
	if (GarageEnv.IsValid())
	{
		return GarageEnv.Get();
	}
	// lazy, not BeginPlay - avoids racing pawn/level discovery at boot (same
	// reasoning as this class's own pawn lookups, which all retry-by-Tick
	// rather than assume anything exists yet).
	if (UWorld* World = GetWorld())
	{
		AODGarageEnvironment* Spawned = World->SpawnActor<AODGarageEnvironment>();
		GarageEnv = Spawned;
		return Spawned;
	}
	return nullptr;
}

// ---- HUD-facing ----------------------------------------------------------

void AODMainMenu::GetLoadingProgress(int32& OutTerrainReady, int32& OutTerrainTotal,
	int32& OutRoadReady, int32& OutRoadTotal) const
{
	OutTerrainReady = OutTerrainTotal = 0;
	OutRoadReady = OutRoadTotal = 0;

	const AODVehiclePawn* Pawn = FindPawn();
	if (const AODTerrainManager* Terrain = FindActor<AODTerrainManager>(GetWorld()))
	{
		Terrain->GetStreamingProgress(Pawn ? Pawn->GetActorLocation() : FVector::ZeroVector,
			OutTerrainReady, OutTerrainTotal);
	}
	if (const AODRoadManager* Road = FindActor<AODRoadManager>(GetWorld()))
	{
		Road->GetStreamingProgress(OutRoadReady, OutRoadTotal);
	}
}

void AODMainMenu::GetRow(int32 Row, FString& OutLabel, FString& OutValue) const
{
	switch (Row)
	{
	case 0:
	{
		OutLabel = TEXT("Car");
		OutValue = TEXT("Coupe");
		if (const AODVehiclePawn* Pawn = FindPawn())
		{
			const TArray<TObjectPtr<UODVehicleDef>>& Defs = Pawn->GetVehicleDefs();
			if (Defs.IsValidIndex(Pawn->GetVehicleIndex()) && Defs[Pawn->GetVehicleIndex()])
			{
				OutValue = Defs[Pawn->GetVehicleIndex()]->DisplayName;
			}
		}
		break;
	}
	case 1:
		OutLabel = TEXT("Engine");
		OutValue = Engines[EngineIdx].Name;
		break;
	case 2:
		OutLabel = TEXT("Drivetrain");
		OutValue = Layouts[DriveIdx].Name;
		break;
	case 3:
		OutLabel = TEXT("Tuning");
		OutValue = TEXT("(stock)");
		if (const AODTuningManager* Tuning = FindActor<AODTuningManager>(GetWorld());
			Tuning && !Tuning->CurrentPresetName.IsEmpty())
		{
			OutValue = Tuning->CurrentPresetName;
		}
		break;
	case 4:
	{
		OutLabel = TEXT("Input Device");
		OutValue = TEXT("Auto");
		if (const AODVehiclePawn* Pawn = FindPawn())
		{
			const EODInputDevice Mode = Pawn->GetInputDeviceMode();
			OutValue = AODVehiclePawn::InputDeviceModeToString(Mode);
			if (Mode == EODInputDevice::Auto)
			{
				// show what Auto has actually resolved to, so the row reads
				// as live feedback ("Auto (Gamepad)") instead of a static name
				OutValue += FString::Printf(TEXT(" (%s)"),
					*AODVehiclePawn::InputDeviceModeToString(Pawn->GetActiveInputDevice()));
			}
		}
		break;
	}
	case 5:
		OutLabel = bGarageMode ? TEXT("Resume Driving") : TEXT("Drive  →");
		OutValue.Empty();
		break;
	default:
		OutLabel.Empty();
		OutValue.Empty();
		break;
	}
}
