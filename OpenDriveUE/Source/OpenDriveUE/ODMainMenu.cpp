#include "ODMainMenu.h"

#include "AssetRegistry/ARFilter.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Camera/CameraComponent.h"
#include "Components/InputComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Containers/Ticker.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Materials/MaterialInterface.h"
#include "Misc/App.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "UnrealClient.h"

#include "ODDayNightController.h"
#include "ODEngineSynth.h"
#include "ODForestManager.h"
#include "ODGameMode.h"
#include "ODGarageEnvironment.h"
#include "ODRoadManager.h"
#include "ODSeasonController.h"
#include "ODSettingsMenu.h"
#include "ODTerrainManager.h"
#include "ODTuningManager.h"
#include "ODVehicleAudio.h"
#include "ODVehicleDef.h"
#include "ODVehicleMovement.h"
#include "ODVehiclePawn.h"
#include "ODWeatherController.h"

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
// Row 0 "Stock" (2026-09-25, Taylor: per-car engines must survive the
// garage) is a sentinel - ApplyEngine() reads the active UODVehicleDef
// instead of these zeros. Same for Layouts[0].
const FEngineDef Engines[] = {
	{TEXT("Stock"),        0, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f},
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
	{TEXT("Stock"), EODDriveLayout::RWD},   // sentinel: def's DriveLayout
	{TEXT("RWD"), EODDriveLayout::RWD},
	{TEXT("FWD"), EODDriveLayout::FWD},
	{TEXT("AWD"), EODDriveLayout::AWD},
};

// the active car's def, or null (no def assets -> hardcoded coupe)
const UODVehicleDef* ActiveDef(const AODVehiclePawn* Pawn)
{
	if (!Pawn || !Pawn->GetVehicleDefs().IsValidIndex(Pawn->GetVehicleIndex()))
	{
		return nullptr;
	}
	return Pawn->GetVehicleDefs()[Pawn->GetVehicleIndex()];
}

// Stock's synth voice: defs carry no audio fields, so borrow the real
// Engines[] row whose peak torque is nearest the def's (V8 without a def)
const FEngineDef& StockVoice(const UODVehicleDef* Def)
{
	int32 Best = 1;
	for (int32 i = 2; Def && i < (int32)UE_ARRAY_COUNT(Engines); ++i)
	{
		if (FMath::Abs(Engines[i].PeakNm - Def->PeakTorqueNm)
			< FMath::Abs(Engines[Best].PeakNm - Def->PeakTorqueNm))
		{
			Best = i;
		}
	}
	return Engines[Best];
}

// Exhaust presets (Arc 5.2) - Stock/Sport/Open, same table-of-defs idiom as
// Engines[]/Layouts[] above. PopProbability/PopWindowSec/PopDecay feed
// UODEngineSynth's lift-off pop mechanism (see ODEngineSynth.cpp's
// OnGenerateAudio firing loop): Stock is near-silent overrun, Sport gets an
// occasional crackle, Open is dense. Loudness/Rasp/MufflerCutoff climb the
// same direction (louder + raspier + more open muffler) so the preset reads
// as one coherent character, not just "pops turned up".
// Plain POD fields (not a nested USTRUCT), same aggregate-init convention as
// FEngineDef/FLayoutDef above - converted to FODExhaustConfig on demand by
// ApplyExhaust().
struct FExhaustDef
{
	const TCHAR* Name;
	float Loudness, Rasp, MufflerCutoff;
	float PopProbability, PopWindowSec, PopDecay;
};
const FExhaustDef ExhaustPresets[] = {
	{TEXT("Stock"), 0.65f, 0.35f, 2200.0f, 0.02f, 0.25f, 2.0f},
	{TEXT("Sport"), 0.80f, 0.55f, 3200.0f, 0.35f, 0.55f, 1.5f},
	{TEXT("Open"),  0.95f, 0.75f, 4600.0f, 0.75f, 1.1f,  1.1f},
};

// Garage slider step sizes/bounds - mirror UODEngineSynth's own ClampMin/Max
// on these same properties (Loudness/Rasp/MufflerCutoff/PopProbability).
constexpr float ExhaustLoudnessStep = 0.05f;
constexpr float ExhaustRaspStep = 0.05f;
constexpr float ExhaustMufflerStep = 200.0f;
constexpr float ExhaustPopStep = 0.05f;

// Garage Season row (SS7.38): "" = Auto, anything else is handed to
// AODSeasonController::SetSeasonLock (same names -odseason= accepts).
const TCHAR* const SeasonNames[] = {TEXT("Auto"), TEXT("Summer"), TEXT("Fall"), TEXT("Winter"), TEXT("Spring")};
const TCHAR* SeasonConfigSection = TEXT("OpenDrive.Season");

// Time of Day / Weather rows (2026-10-05). Hours feed AODDayNightController::
// TimeOfDay (Morning = its StartHour default, so an unset config changes
// nothing); weather names are AODWeatherController's EODWeatherState names.
struct FTimePreset
{
	const TCHAR* Name;
	float Hour;
};
const FTimePreset TimeOfDayPresets[] = {
	{TEXT("Dawn"), 6.4f}, {TEXT("Morning"), 10.0f}, {TEXT("Noon"), 12.0f}, {TEXT("Afternoon"), 15.0f},
	{TEXT("Golden Hour"), 17.5f}, {TEXT("Dusk"), 18.3f}, {TEXT("Night"), 22.0f},
};
const TCHAR* const WeatherNames[] = {
	TEXT("Auto"), TEXT("Clear"), TEXT("Overcast"), TEXT("Rain"), TEXT("HeavyRain"), TEXT("Sleet"), TEXT("Snow"),
	TEXT("Blizzard"), TEXT("Fog"), TEXT("DustStorm"), TEXT("Thunderstorm"), TEXT("Tornado"), TEXT("Hurricane"),
};
// WeatherNames[i] = EODWeatherState(i - 1) - WeatherPossible() relies on it
static_assert(UE_ARRAY_COUNT(WeatherNames) == (int32)EODWeatherState::Hurricane + 2, "Auto + one per EODWeatherState");
const TCHAR* TimeConfigSection = TEXT("OpenDrive.TimeOfDay");
const TCHAR* WeatherConfigSection = TEXT("OpenDrive.Weather");

// Garage-menu vistas (2026-10-05) - tunables. Each area's copy is shifted so
// its car spot lands at (VistaBaseXCm + i * VistaSpacingCm, 0): far outside
// every real area (California is ~1e8 cm across), 200 km apart so no vista's
// panorama tiles or sea plane reach another's (their Z frames differ - one
// area's sea plane would flood a high area's terrain). Z stays real (the
// atmosphere and the season snow line read absolute height).
constexpr double VistaBaseXCm = 2.0e8;
constexpr double VistaSpacingCm = 2.0e7;
constexpr double VistaFarCm = 100000.0;        // full-detail wedge reach past the car
// panorama trial: coarse/background terrain tiles in the same wedge out to
// here (FODVistaRegion::PanoFarCm); 0 = off
constexpr double VistaPanoFarCm = 2000000.0;
// sea plane (spot's optional "sea_z_cm"): same mesh/material as the level
// builder's Ocean (unreal/build_openworld_test_level.py), a square this
// half-size centred on the car - well past the horizon from road height
constexpr double VistaSeaHalfCm = 6000000.0;
constexpr double VistaPatchCm = 3000.0;        // ground disc around car + camera
constexpr double VistaHalfAngleDeg = 45.0;     // = the camera's 90 deg FOV
// Camera framing: behind the car (opposite view yaw), shifted right of its
// line so the car sits left of centre and the right third stays clear for
// the menu panel. Looks along the view yaw, pitched slightly down.
constexpr double VistaCamBackCm = 700.0;
constexpr double VistaCamSideCm = 250.0;
constexpr double VistaCamHeightCm = 150.0;     // above the road surface
constexpr double VistaCamPitchDeg = -4.0;

// Row ids: one global GetRow()/Adjust() switch; each screen's tabs show a slice.
enum ERow : int32
{
	RowCar, RowEngine, RowDrivetrain, RowTuning, RowExhaust, RowSound,   // Garage
	RowLoudness, RowRasp, RowMuffler, RowPop,                            // Sound (from Garage)
	RowArea, RowTime, RowSeason, RowWeather,                             // Scene: season before the weather it allows
	RowStart, RowGarage, RowSettings,                                    // Main ("Drive" opens Scene)
	RowDrive, RowBack, RowQuit,                                          // footers
};
const FODMenuTab MainTabs[] = {{TEXT("MAIN"), RowStart, 3}};        // footer Quit
const FODMenuTab GarageTabs[] = {{TEXT("GARAGE"), RowCar, 6}};      // footer Back
const FODMenuTab SoundTabs[] = {{TEXT("SOUND"), RowLoudness, 4}};   // footer Back
const FODMenuTab SceneTabs[] = {{TEXT("SCENE"), RowArea, 4}};       // footer Drive

// same section pattern as Season/Time/Weather; VehicleIdx is read by AODVehiclePawn::BeginPlay
const TCHAR* CarConfigSection = TEXT("OpenDrive.Car");
const TCHAR* AreaConfigSection = TEXT("OpenDrive.Area");

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
	LoadExhaustConfig();   // restore last session's exhaust pick before ApplyExhaust() ever runs
	GConfig->GetInt(SeasonConfigSection, TEXT("SeasonIdx"), SeasonIdx, GGameUserSettingsIni);
	SeasonIdx = FMath::Clamp(SeasonIdx, 0, (int32)UE_ARRAY_COUNT(SeasonNames) - 1);
	GConfig->GetInt(TimeConfigSection, TEXT("TimeIdx"), TimeIdx, GGameUserSettingsIni);
	TimeIdx = FMath::Clamp(TimeIdx, 0, (int32)UE_ARRAY_COUNT(TimeOfDayPresets) - 1);
	GConfig->GetInt(WeatherConfigSection, TEXT("WeatherIdx"), WeatherIdx, GGameUserSettingsIni);
	WeatherIdx = FMath::Clamp(WeatherIdx, 0, (int32)UE_ARRAY_COUNT(WeatherNames) - 1);
	FindAreaLevels();
	LoadMenuSpots();
	// Scene Select lists only areas with a menu spot (a vista to show)
	for (int32 i = Spots.Num() - 1; i >= 0; --i)
	{
		if (!Spots[i].bValid)
		{
			AreaLevels.RemoveAt(i);
			AreaNames.RemoveAt(i);
			AreaKeys.RemoveAt(i);
			Spots.RemoveAt(i);
		}
	}
	CurrentAreaIdx = AreaLevels.IndexOfByKey(UWorld::RemovePIEPrefix(GetWorld()->GetPackage()->GetName()));
	FString SavedArea;
	GConfig->GetString(AreaConfigSection, TEXT("Level"), SavedArea, GGameUserSettingsIni);
	AreaIdx = FMath::Max(0, AreaLevels.IndexOfByKey(SavedArea));   // missing/unlisted -> first area

	// saved car picks (factory default when missing or out of range); the car
	// itself is restored by AODVehiclePawn::BeginPlay
	GConfig->GetInt(CarConfigSection, TEXT("EngineIdx"), EngineIdx, GGameUserSettingsIni);
	EngineIdx = EngineIdx >= 0 && EngineIdx < (int32)UE_ARRAY_COUNT(Engines) ? EngineIdx : 0;
	GConfig->GetInt(CarConfigSection, TEXT("DriveIdx"), DriveIdx, GGameUserSettingsIni);
	DriveIdx = DriveIdx >= 0 && DriveIdx < (int32)UE_ARRAY_COUNT(Layouts) ? DriveIdx : 0;
	GConfig->GetInt(CarConfigSection, TEXT("TunePreset"), CarriedTuneIdx, GGameUserSettingsIni);   // validated in EnterCarConfig

	// picks carried by Drive's OpenArea() (override the saved ones; odvehidx=
	// is read by AODVehiclePawn::BeginPlay). odengine= marks a Drive launch.
	const FURL& Url = GetWorld()->URL;
	if (const TCHAR* V = Url.GetOption(TEXT("odengine="), nullptr))
	{
		EngineIdx = FMath::Clamp(FCString::Atoi(V), 0, (int32)UE_ARRAY_COUNT(Engines) - 1);
		bAutoStart = true;
	}
	if (const TCHAR* V = Url.GetOption(TEXT("oddrive="), nullptr))
	{
		DriveIdx = FMath::Clamp(FCString::Atoi(V), 0, (int32)UE_ARRAY_COUNT(Layouts) - 1);
	}
	if (const TCHAR* V = Url.GetOption(TEXT("odtunepreset="), nullptr))
	{
		CarriedTuneIdx = FCString::Atoi(V);
	}
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
		Bind(EKeys::Q, &AODMainMenu::PrevTab);
		Bind(EKeys::E, &AODMainMenu::NextTab);
		Bind(EKeys::Gamepad_LeftShoulder, &AODMainMenu::PrevTab);
		Bind(EKeys::Gamepad_RightShoulder, &AODMainMenu::NextTab);
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
				if (Stage == EODMenuStage::Menu)
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
				// every road manager (2026-10-05, large world: one per streamed
				// area set), and StartAt is told which one the index belongs to
				for (TActorIterator<AODRoadManager> It(GetWorld()); It && !bPickDone; ++It)
				{
					const AODRoadManager* Road = *It;
					const TArray<FTownNode>& Nodes = Road->GetTownNodes();
					for (int32 i = 0; i < Nodes.Num(); ++i)
					{
						if (Nodes[i].Name.Contains(PickTown))
						{
							bPickDone = true;
							StartAt(i, Road);
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
	Stage = EODMenuStage::Menu;
	bGarageMode = false;
	SetScreen(EODMenuScreen::Main, RowStart);
	HoveredTownIdx = INDEX_NONE;
	bEntered = false;   // EnterCarConfig() re-runs once the pawn is confirmed
	// Deliberately NOT pausing here (unlike OpenGarage() below): the camera
	// manager's cached view stops updating while paused, freezing the
	// showroom camera at its first, unpositioned frame. The preview pawn
	// can't drive off anyway (PlaceInShowroom disables physics). OpenGarage()
	// pauses because it opens mid-drive, with the camera already placed.
	// The pause menu's "Main Menu" row arrives here PAUSED (2026-10-09,
	// Taylor: "selecting main menu when driving should take you back to the
	// garage view") - unpause so it runs exactly like a fresh boot: pawn,
	// sky and the re-spawned vista managers all tick again.
	UGameplayStatics::SetGamePaused(this, false);
	SetCursorVisible(true);
}

void AODMainMenu::OpenGarage()
{
	Stage = EODMenuStage::Menu;
	bGarageMode = true;
	SetScreen(EODMenuScreen::Garage, RowCar);
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
		// AND over every terrain/road manager (2026-10-05, large world: one
		// per streamed area set) - none at all still counts as settled.
		bool bTerrainSettled = true;
		for (TActorIterator<AODTerrainManager> It(GetWorld()); It; ++It)
		{
			bTerrainSettled = bTerrainSettled && It->IsInitialStreamingSettled(Pawn ? Pawn->GetActorLocation() : FVector::ZeroVector);
		}
		bool bRoadSettled = true;
		for (TActorIterator<AODRoadManager> It(GetWorld()); It; ++It)
		{
			bRoadSettled = bRoadSettled && It->IsInitialStreamingSettled();
		}
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
	if (Stage != EODMenuStage::Menu)
	{
		return;
	}
	if (!bEntered)
	{
		EnterCarConfig();
	}
	if (bEntered && bAutoStart && !bGarageMode)
	{
		bAutoStart = false;
		StartAt(-1);   // opened by Drive: straight to the default spawn
		return;
	}
	UpdateScene();
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
	// sync the displayed engine/drivetrain with reality (tuning stays stock
	// unless a preset was saved/carried); exhaust reapplies every entry since
	// the synth is recreated per-car (AODVehicleAudio::EnsureSynths).
	if (CarriedTuneIdx >= 0 && CarriedTuneIdx < NumPresets())
	{
		TuneIdx = CarriedTuneIdx;   // one-shot: saved or odtunepreset= pick
		ApplyTuning();
	}
	CarriedTuneIdx = INDEX_NONE;
	ApplyEngine();
	ApplyDrivetrain();
	ApplyExhaust();
	ApplySeason();
	if (!bGarageMode)
	{
		// fresh menu only: a mid-drive garage visit keeps the running clock and sky
		float OdHour = 0.0f;
		if (!FParse::Value(FCommandLine::Get(), TEXT("odhour="), OdHour))
		{
			ApplyTimeOfDay();
		}
		if (WeatherIdx != 0)
		{
			ApplyWeather();
		}
		if (UCameraComponent* Cam = Pawn->FindComponentByClass<UCameraComponent>())
		{
			GarageCamXf = Cam->GetComponentTransform();
		}
		ShownVista = INDEX_NONE;
		if (!bAutoStart)
		{
			SpawnVistas();
		}
	}
	bEntered = true;
}

// ---- input ---------------------------------------------------------------

void AODMainMenu::Up()
{
	if (bSettingsOpen)
	{
		if (SettingsMenu) { SettingsMenu->MoveSelection(-1); }
		return;
	}
	if (Stage == EODMenuStage::Menu)
	{
		Selected = ODMenuTabs::Step(GetTabs()[TabIdx], GetFooterRow(), Selected, -1);
	}
}

void AODMainMenu::Down()
{
	if (bSettingsOpen)
	{
		if (SettingsMenu) { SettingsMenu->MoveSelection(+1); }
		return;
	}
	if (Stage == EODMenuStage::Menu)
	{
		Selected = ODMenuTabs::Step(GetTabs()[TabIdx], GetFooterRow(), Selected, +1);
	}
}

void AODMainMenu::SwitchTab(int32 Dir)
{
	if (bSettingsOpen)
	{
		if (SettingsMenu) { SettingsMenu->SwitchTab(Dir); }
		return;
	}
	if (const int32 N = GetTabs().Num(); Stage == EODMenuStage::Menu && N > 1)
	{
		TabIdx = (TabIdx + Dir + N) % N;
		Selected = GetTabs()[TabIdx].First;
	}
}

TArrayView<const FODMenuTab> AODMainMenu::GetTabs() const
{
	switch (Screen)
	{
	case EODMenuScreen::Garage: return GarageTabs;
	case EODMenuScreen::Sound: return SoundTabs;
	case EODMenuScreen::Scene: return SceneTabs;
	default: return MainTabs;
	}
}

int32 AODMainMenu::GetFooterRow() const
{
	return Screen == EODMenuScreen::Main ? RowQuit : Screen == EODMenuScreen::Scene ? RowDrive : RowBack;
}

void AODMainMenu::SetScreen(EODMenuScreen NewScreen, int32 Row)
{
	Screen = NewScreen;
	TabIdx = 0;
	Selected = Row;
}

void AODMainMenu::SelectRow(int32 Row, int32 Col)
{
	if (bSettingsOpen)
	{
		if (SettingsMenu) { SettingsMenu->SelectRow(Row, Col); }
		return;
	}
	if (Stage == EODMenuStage::Menu && Row >= 0 && Row <= RowQuit)
	{
		Selected = Row;
	}
}

void AODMainMenu::SetTab(int32 Tab)
{
	if (bSettingsOpen)
	{
		if (SettingsMenu) { SettingsMenu->SetTab(Tab); }
		return;
	}
	if (Stage == EODMenuStage::Menu && GetTabs().IsValidIndex(Tab))
	{
		TabIdx = Tab;
		Selected = GetTabs()[TabIdx].First;
	}
}

void AODMainMenu::Left()
{
	if (bSettingsOpen)
	{
		Adjust(-1);
		return;
	}
	if (Stage == EODMenuStage::Menu)
	{
		Adjust(-1);
	}
}

void AODMainMenu::Right()
{
	if (bSettingsOpen)
	{
		Adjust(+1);
		return;
	}
	if (Stage == EODMenuStage::Menu)
	{
		Adjust(+1);
	}
}

void AODMainMenu::Activate()
{
	if (bSettingsOpen)
	{
		if (SettingsMenu && SettingsMenu->Activate())   // true == its Close row
		{
			bSettingsOpen = false;
		}
		return;
	}
	if (Stage == EODMenuStage::Menu)
	{
		switch (Selected)
		{
		case RowStart: SetScreen(EODMenuScreen::Scene, RowArea); break;
		case RowGarage: SetScreen(EODMenuScreen::Garage, RowCar); break;
		case RowSound: SetScreen(EODMenuScreen::Sound, RowLoudness); break;
		case RowSettings:
			EnsureSettingsMenu();
			bSettingsOpen = true;
			break;
		case RowQuit:
			UKismetSystemLibrary::QuitGame(this, UGameplayStatics::GetPlayerController(this, 0),
				EQuitPreference::Quit, false);
			break;
		case RowBack: Back(); break;
		case RowDrive:
			if (AreaLevels.IsValidIndex(AreaIdx) && AreaIdx != CurrentAreaIdx)
			{
				OpenArea();   // that level's menu auto-starts (bAutoStart)
			}
			else
			{
				StartAt(-1);  // already on this area (or none listed): default spawn, no reload
			}
			break;
		default:
			Adjust(+1);   // Enter cycles a value row, like the pause menu
			break;
		}
	}
	else if (Stage == EODMenuStage::MapSelect)
	{
		StartAt(HoveredTownIdx);    // -1 (no town) starts at the level's PlayerStart
	}
}

void AODMainMenu::Back()
{
	if (bSettingsOpen)
	{
		if (SettingsMenu && SettingsMenu->Back())   // true == nothing left to back out of
		{
			bSettingsOpen = false;
		}
		return;
	}
	if (Stage == EODMenuStage::MapSelect)
	{
		Stage = EODMenuStage::Menu;
		SetScreen(EODMenuScreen::Scene, RowDrive);
	}
	else if (Stage == EODMenuStage::Menu && Screen == EODMenuScreen::Sound)
	{
		SetScreen(EODMenuScreen::Garage, RowSound);   // before the garage-mode resume below
	}
	else if (Stage == EODMenuStage::Menu && bGarageMode)
	{
		ResumeFromGarage();   // the pause menu's garage: Back resumes driving
	}
	else if (Stage == EODMenuStage::Menu && Screen != EODMenuScreen::Main)
	{
		SetScreen(EODMenuScreen::Main, Screen == EODMenuScreen::Garage ? RowGarage : RowStart);
	}
	// Main screen: nothing to back out to
}

void AODMainMenu::OnClick()
{
	if (bSettingsOpen)
	{
		return;
	}
	if (Stage == EODMenuStage::MapSelect && HoveredTownIdx != INDEX_NONE)
	{
		StartAt(HoveredTownIdx);
	}
}

void AODMainMenu::Adjust(int32 Dir)
{
	if (bSettingsOpen)
	{
		if (SettingsMenu) { SettingsMenu->AdjustSelected(Dir); }
		return;
	}
	switch (Selected)
	{
	case RowCar:
		ChangeCar(Dir);
		SavePicks();
		break;
	case RowEngine:
		EngineIdx = (EngineIdx + Dir + UE_ARRAY_COUNT(Engines)) % UE_ARRAY_COUNT(Engines);
		ApplyEngine();
		SavePicks();
		break;
	case RowDrivetrain:
		DriveIdx = (DriveIdx + Dir + UE_ARRAY_COUNT(Layouts)) % UE_ARRAY_COUNT(Layouts);
		ApplyDrivetrain();
		SavePicks();
		break;
	case RowTuning:
		if (const int32 N = NumPresets(); N > 0)
		{
			TuneIdx = (TuneIdx + Dir + N) % N;
			ApplyTuning();
			SavePicks();
		}
		break;
	case RowExhaust:
		// Exhaust preset row (Arc 5.2): cycles Stock/Sport/Open, same idiom
		// as Engine/Drivetrain/Tuning above - picking a preset seeds ALL four
		// slider fields below from it (a fresh baseline), then persists.
		ExhaustPresetIdx = (ExhaustPresetIdx + Dir + UE_ARRAY_COUNT(ExhaustPresets)) % UE_ARRAY_COUNT(ExhaustPresets);
		ExhaustLoudness = ExhaustPresets[ExhaustPresetIdx].Loudness;
		ExhaustRasp = ExhaustPresets[ExhaustPresetIdx].Rasp;
		ExhaustMufflerCutoff = ExhaustPresets[ExhaustPresetIdx].MufflerCutoff;
		ExhaustPop = ExhaustPresets[ExhaustPresetIdx].PopProbability;
		ApplyExhaust();
		SaveExhaustConfig();
		break;
	case RowLoudness:
		// Exhaust Loudness slider - individually adjustable beyond the preset
		// baseline (5.2 spec), bounds matching UODEngineSynth's own ClampMin/Max.
		ExhaustLoudness = FMath::Clamp(ExhaustLoudness + Dir * ExhaustLoudnessStep, 0.0f, 1.0f);
		ApplyExhaust();
		SaveExhaustConfig();
		break;
	case RowRasp:
		ExhaustRasp = FMath::Clamp(ExhaustRasp + Dir * ExhaustRaspStep, 0.0f, 1.0f);
		ApplyExhaust();
		SaveExhaustConfig();
		break;
	case RowMuffler:
		ExhaustMufflerCutoff = FMath::Clamp(ExhaustMufflerCutoff + Dir * ExhaustMufflerStep, 200.0f, 8000.0f);
		ApplyExhaust();
		SaveExhaustConfig();
		break;
	case RowPop:
		// Exhaust Pop slider - lift-off pop/burble probability (5.2's new param).
		ExhaustPop = FMath::Clamp(ExhaustPop + Dir * ExhaustPopStep, 0.0f, 1.0f);
		ApplyExhaust();
		SaveExhaustConfig();
		break;
	case RowSeason:
		// Season row (SS7.38): Auto/Summer/Fall/Winter/Spring, applied live and
		// persisted the same GConfig way as the exhaust rows.
		SeasonIdx = (SeasonIdx + Dir + UE_ARRAY_COUNT(SeasonNames)) % UE_ARRAY_COUNT(SeasonNames);
		ApplySeason();
		// the Weather row only offers the picked season's weather (2026-10-09):
		// re-apply instantly - Auto re-rolls in the new season (SetSeasonLock's
		// own re-roll ramps in over minutes, so a winter blizzard kept falling),
		// and a pick the new season can't produce drops to Auto
		ApplyWeather();
		GConfig->SetInt(SeasonConfigSection, TEXT("SeasonIdx"), SeasonIdx, GGameUserSettingsIni);
		GConfig->Flush(false, GGameUserSettingsIni);
		break;
	case RowTime:
		TimeIdx = (TimeIdx + Dir + UE_ARRAY_COUNT(TimeOfDayPresets)) % UE_ARRAY_COUNT(TimeOfDayPresets);
		ApplyTimeOfDay();
		GConfig->SetInt(TimeConfigSection, TEXT("TimeIdx"), TimeIdx, GGameUserSettingsIni);
		GConfig->Flush(false, GGameUserSettingsIni);
		break;
	case RowWeather:
		// only states the active climate can produce (2026-10-05) in the picked
		// season (2026-10-09); Auto always can
		do
		{
			WeatherIdx = (WeatherIdx + Dir + UE_ARRAY_COUNT(WeatherNames)) % UE_ARRAY_COUNT(WeatherNames);
		} while (!WeatherPossible(WeatherIdx));
		ApplyWeather();
		GConfig->SetInt(WeatherConfigSection, TEXT("WeatherIdx"), WeatherIdx, GGameUserSettingsIni);
		GConfig->Flush(false, GGameUserSettingsIni);
		break;
	case RowArea:
		// picks only - nothing loads until Drive; the vista (UpdateScene)
		// swaps to the picked area's preloaded copy
		if (const int32 N = AreaLevels.Num(); N > 0)
		{
			AreaIdx = (AreaIdx + Dir + N) % N;
			SavePicks();
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
	// Stock (row 0) restores the active def's own engine; with no def the
	// V8 row is the def/ctor default anyway
	const UODVehicleDef* Def = ActiveDef(FindPawn());
	const FEngineDef& E = EngineIdx != 0 ? Engines[EngineIdx] : StockVoice(Def);
	if (UODVehicleMovement* D = FindMovement())
	{
		const bool bStockDef = EngineIdx == 0 && Def;
		D->PeakTorqueNm = bStockDef ? Def->PeakTorqueNm : E.PeakNm;
		D->PeakTorqueRpm = bStockDef ? Def->PeakTorqueRpm : E.PeakRpm;
		D->MaxRpm = bStockDef ? Def->MaxRpm : E.MaxRpm;
		D->LimiterRpm = bStockDef ? Def->LimiterRpm : E.LimiterRpm;
		D->EngineInertia = bStockDef ? Def->EngineInertia : E.Inertia;
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
		const UODVehicleDef* Def = ActiveDef(FindPawn());
		D->SetDriveLayout(DriveIdx != 0 ? Layouts[DriveIdx].Layout
			: Def ? Def->DriveLayout : EODDriveLayout::RWD);
	}
}

void AODMainMenu::ApplyTuning()
{
	if (AODTuningManager* Tuning = FindActor<AODTuningManager>(GetWorld()))
	{
		Tuning->ApplyPresetByIndex(TuneIdx);
	}
}

void AODMainMenu::ApplyExhaust()
{
	if (AODVehicleAudio* Audio = FindActor<AODVehicleAudio>(GetWorld()))
	{
		if (UODEngineSynth* Synth = Audio->GetEngineSynth())
		{
			// PopWindowSec/PopDecay stay at the current preset's tuning-constant
			// values (not garage sliders - see FODExhaustConfig's comment); only
			// the four slider fields below are player-adjustable.
			const FExhaustDef& Preset = ExhaustPresets[ExhaustPresetIdx];
			FODExhaustConfig Config;
			Config.Loudness = ExhaustLoudness;
			Config.Rasp = ExhaustRasp;
			Config.MufflerCutoff = ExhaustMufflerCutoff;
			Config.PopProbability = ExhaustPop;
			Config.PopWindowSec = Preset.PopWindowSec;
			Config.PopDecay = Preset.PopDecay;
			Synth->SetExhaustConfig(Config);
		}
	}
}

void AODMainMenu::ApplySeason()
{
	if (AODSeasonController* Season = FindActor<AODSeasonController>(GetWorld()))
	{
		Season->SetSeasonLock(SeasonIdx == 0 ? FString() : FString(SeasonNames[SeasonIdx]));
	}
}

void AODMainMenu::ApplyTimeOfDay()
{
	if (AODDayNightController* DayNight = FindActor<AODDayNightController>(GetWorld()))
	{
		DayNight->TimeOfDay = TimeOfDayPresets[TimeIdx].Hour;
	}
}

void AODMainMenu::ApplyWeather()
{
	if (AODWeatherController* Weather = FindActor<AODWeatherController>(GetWorld()))
	{
		if (!WeatherPossible(WeatherIdx))
		{
			// (2026-10-05) a saved/carried pick this climate never produces -> Auto,
			// saved so the row and the next level agree
			UE_LOG(LogTemp, Display, TEXT("ODMainMenu: weather %s impossible here - Auto"), WeatherNames[WeatherIdx]);
			WeatherIdx = 0;
			GConfig->SetInt(WeatherConfigSection, TEXT("WeatherIdx"), WeatherIdx, GGameUserSettingsIni);
			GConfig->Flush(false, GGameUserSettingsIni);
		}
		Weather->ForceState(WeatherNames[WeatherIdx], /*bInstant*/true);
	}
}

bool AODMainMenu::WeatherPossible(int32 Idx) const
{
	// ponytail: tests the weather's active climate - a vista's, else this
	// level's. An Area pick with no menu spot (no vista) is filtered by this
	// level's climate; its own level re-checks at start. Load every AreaKeys
	// climate if that misleads.
	const AODWeatherController* Weather = FindActor<AODWeatherController>(GetWorld());
	return Idx == 0 || !Weather || Weather->CanProduce((EODWeatherState)(Idx - 1));
}

void AODMainMenu::SetWeatherClimate(int32 Vista)
{
	AODWeatherController* Weather = FindActor<AODWeatherController>(GetWorld());
	if (!Weather)
	{
		return;
	}
	const bool bVista = Spots.IsValidIndex(Vista);
	Weather->SetVistaClimate(bVista ? &Spots[Vista].Climate : nullptr,
		bVista ? Spots[Vista].Forest.Get() : nullptr, bVista ? FVector2D(Spots[Vista].CarCm) : FVector2D::ZeroVector);
	// Auto re-rolls in the new climate; a vista also drops a forced state its
	// area never produces. Home keeps it: the pick is for the area about to
	// load (its level re-checks).
	if (WeatherIdx == 0 || (bVista && !WeatherPossible(WeatherIdx)))
	{
		ApplyWeather();
	}
}

namespace
{
const TCHAR* ExhaustConfigSection = TEXT("OpenDrive.Exhaust");
}

void AODMainMenu::SaveExhaustConfig() const
{
	// GConfig into GameUserSettings.ini - same mechanism as AODVehiclePawn::
	// SaveInputDeviceMode (no SaveGame object exists yet in this project).
	GConfig->SetInt(ExhaustConfigSection, TEXT("PresetIdx"), ExhaustPresetIdx, GGameUserSettingsIni);
	GConfig->SetFloat(ExhaustConfigSection, TEXT("Loudness"), ExhaustLoudness, GGameUserSettingsIni);
	GConfig->SetFloat(ExhaustConfigSection, TEXT("Rasp"), ExhaustRasp, GGameUserSettingsIni);
	GConfig->SetFloat(ExhaustConfigSection, TEXT("MufflerCutoff"), ExhaustMufflerCutoff, GGameUserSettingsIni);
	GConfig->SetFloat(ExhaustConfigSection, TEXT("Pop"), ExhaustPop, GGameUserSettingsIni);
	GConfig->Flush(false, GGameUserSettingsIni);
}

void AODMainMenu::LoadExhaustConfig()
{
	// defaults = Stock preset's baseline (matches the member initializers
	// above), so a config with no saved section yet reproduces today's
	// silent-overrun behavior exactly.
	ExhaustPresetIdx = 0;
	GConfig->GetInt(ExhaustConfigSection, TEXT("PresetIdx"), ExhaustPresetIdx, GGameUserSettingsIni);
	if (ExhaustPresetIdx < 0 || ExhaustPresetIdx >= (int32)UE_ARRAY_COUNT(ExhaustPresets))
	{
		ExhaustPresetIdx = 0;
	}
	const FExhaustDef& Base = ExhaustPresets[ExhaustPresetIdx];
	ExhaustLoudness = Base.Loudness;
	ExhaustRasp = Base.Rasp;
	ExhaustMufflerCutoff = Base.MufflerCutoff;
	ExhaustPop = Base.PopProbability;
	GConfig->GetFloat(ExhaustConfigSection, TEXT("Loudness"), ExhaustLoudness, GGameUserSettingsIni);
	GConfig->GetFloat(ExhaustConfigSection, TEXT("Rasp"), ExhaustRasp, GGameUserSettingsIni);
	GConfig->GetFloat(ExhaustConfigSection, TEXT("MufflerCutoff"), ExhaustMufflerCutoff, GGameUserSettingsIni);
	GConfig->GetFloat(ExhaustConfigSection, TEXT("Pop"), ExhaustPop, GGameUserSettingsIni);
	ExhaustLoudness = FMath::Clamp(ExhaustLoudness, 0.0f, 1.0f);
	ExhaustRasp = FMath::Clamp(ExhaustRasp, 0.0f, 1.0f);
	ExhaustMufflerCutoff = FMath::Clamp(ExhaustMufflerCutoff, 200.0f, 8000.0f);
	ExhaustPop = FMath::Clamp(ExhaustPop, 0.0f, 1.0f);
}

void AODMainMenu::SavePicks() const
{
	if (const AODVehiclePawn* Pawn = FindPawn())
	{
		GConfig->SetInt(CarConfigSection, TEXT("VehicleIdx"), Pawn->GetVehicleIndex(), GGameUserSettingsIni);
	}
	GConfig->SetInt(CarConfigSection, TEXT("EngineIdx"), EngineIdx, GGameUserSettingsIni);
	GConfig->SetInt(CarConfigSection, TEXT("DriveIdx"), DriveIdx, GGameUserSettingsIni);
	const AODTuningManager* Tuning = FindActor<AODTuningManager>(GetWorld());
	GConfig->SetInt(CarConfigSection, TEXT("TunePreset"), Tuning ? Tuning->GetPresetIndex() : INDEX_NONE, GGameUserSettingsIni);
	if (AreaLevels.IsValidIndex(AreaIdx))
	{
		GConfig->SetString(AreaConfigSection, TEXT("Level"), *AreaLevels[AreaIdx], GGameUserSettingsIni);
	}
	GConfig->Flush(false, GGameUserSettingsIni);
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

void AODMainMenu::StartAt(int32 TownIdx, const AODRoadManager* InRoad)
{
	AODVehiclePawn* Pawn = FindPawn();
	if (!Pawn)
	{
		return;
	}
	DestroyVistas();   // and un-freeze the real area streaming before the teleport

	FVector2D Target = FVector2D::ZeroVector;   // only read when bHaveTarget is true
	float Yaw = Pawn->GetActorRotation().Yaw;
	bool bHaveTarget = false;
	if (const AODRoadManager* Road = InRoad ? InRoad : FindActor<AODRoadManager>(GetWorld());
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
		// face along the nearest road at the override point (the town/default
		// yaw above belongs to somewhere else); XY stays exactly as given
		double BestCm = TNumericLimits<double>::Max();
		for (TActorIterator<AODRoadManager> It(GetWorld()); It; ++It)
		{
			FVector2D Snap;
			float SnapYaw;
			if (It->NearestRoadPoint(FVector2D(OdSpawnX, OdSpawnY), Snap, SnapYaw)
				&& FVector2D::Distance(FVector2D(OdSpawnX, OdSpawnY), Snap) < BestCm)
			{
				BestCm = FVector2D::Distance(FVector2D(OdSpawnX, OdSpawnY), Snap);
				Yaw = SnapYaw;
			}
		}
		UE_LOG(LogTemp, Display, TEXT("ODMainMenu: odspawn override -> (%.0f,%.0f) yaw %.0f"), OdSpawnX, OdSpawnY, Yaw);
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

void AODMainMenu::FindAreaLevels()
{
	AreaLevels.Reset();
	AreaNames.Reset();
	AreaKeys.Reset();
	// AssetRegistry, not a list file (2026-10-05): the cooked registry knows
	// every packaged /Game/Areas map (DefaultGame.ini always-cooks the folder).
	// Uncooked, the same forced scan as AODVehiclePawn::LoadVehicleDefs - a
	// -game launch can query before the async content scan reaches the folder.
	IAssetRegistry& AR =
		FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
	if (!FPlatformProperties::RequiresCookedData())
	{
		AR.ScanPathsSynchronous({TEXT("/Game/Areas")}, /*bForceRescan*/true);
	}
	FARFilter Filter;
	Filter.ClassPaths.Add(UWorld::StaticClass()->GetClassPathName());
	Filter.PackagePaths.Add(TEXT("/Game/Areas"));
	TArray<FAssetData> Found;
	AR.GetAssets(Filter, Found);
	for (const FAssetData& A : Found)
	{
		if (A.AssetName.ToString().StartsWith(TEXT("OpenWorld_")))
		{
			AreaLevels.Add(A.PackageName.ToString());
		}
	}
	AreaLevels.Sort();
	for (const FString& Level : AreaLevels)
	{
		// "/Game/Areas/OpenWorld_summit_county" -> key "summit_county", name "Summit County"
		FString Key = FPackageName::GetShortName(Level);
		Key.RemoveFromStart(TEXT("OpenWorld_"));
		AreaNames.Add(FName::NameToDisplayString(Key, false));
		AreaKeys.Add(Key);
	}
}

void AODMainMenu::OpenArea()
{
	if (!AreaLevels.IsValidIndex(AreaIdx))
	{
		return;
	}
	// OpenLevel resets every actor. Every pick is already saved to
	// GameUserSettings.ini on change (SavePicks, Season/Time/Weather/Exhaust);
	// the car picks also ride the travel URL, read back in BeginPlay (this
	// class; odengine= also triggers bAutoStart) and AODVehiclePawn::BeginPlay
	// (odvehidx=). The new level starts at its default spawn.
	SavePicks();
	FString Options = FString::Printf(TEXT("odengine=%d?oddrive=%d"), EngineIdx, DriveIdx);
	if (const AODVehiclePawn* Pawn = FindPawn())
	{
		Options += FString::Printf(TEXT("?odvehidx=%d"), Pawn->GetVehicleIndex());
	}
	if (const AODTuningManager* Tuning = FindActor<AODTuningManager>(GetWorld());
		Tuning && Tuning->GetPresetIndex() != INDEX_NONE)
	{
		Options += FString::Printf(TEXT("?odtunepreset=%d"), Tuning->GetPresetIndex());
	}
	UE_LOG(LogTemp, Display, TEXT("ODMainMenu: Area -> %s (%s)"), *AreaLevels[AreaIdx], *Options);
	UGameplayStatics::OpenLevel(this, FName(*AreaLevels[AreaIdx]), true, Options);
}

// ---- garage-menu vistas (2026-10-05) ---------------------------------------

void AODMainMenu::LoadMenuSpots()
{
	// {"<area>": {"car_cm": [x, y, z], "car_yaw_deg", "view_yaw_deg", "source"}}
	// (pipeline/build_menu_spots.py). An area without a spot is dropped from
	// the Area row by BeginPlay.
	Spots.Reset();
	Spots.SetNum(AreaLevels.Num());
	FString Text;
	TSharedPtr<FJsonObject> Root;
	const FString Path = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks/menu_spots.json");
	if (!FFileHelper::LoadFileToString(Text, *Path)
		|| !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Root) || !Root.IsValid())
	{
		return;
	}
	for (int32 i = 0; i < AreaKeys.Num(); ++i)
	{
		const TSharedPtr<FJsonObject>* Spot = nullptr;
		const TArray<TSharedPtr<FJsonValue>>* Car = nullptr;
		double CarYaw = 0.0, ViewYaw = 0.0;
		if (!Root->TryGetObjectField(AreaKeys[i], Spot)
			|| !(*Spot)->TryGetArrayField(TEXT("car_cm"), Car) || Car->Num() != 3
			|| !(*Spot)->TryGetNumberField(TEXT("car_yaw_deg"), CarYaw)
			|| !(*Spot)->TryGetNumberField(TEXT("view_yaw_deg"), ViewYaw))
		{
			continue;
		}
		FODMenuSpot& S = Spots[i];
		S.bValid = true;
		S.CarCm = FVector((*Car)[0]->AsNumber(), (*Car)[1]->AsNumber(), (*Car)[2]->AsNumber());
		S.CarYawDeg = (float)CarYaw;
		S.ViewYawDeg = (float)ViewYaw;
		double SeaZ = 0.0;
		S.bHasSea = (*Spot)->TryGetNumberField(TEXT("sea_z_cm"), SeaZ);
		S.SeaZCm = SeaZ;
	}
}

FVector AODMainMenu::VistaCarWorld(int32 Area) const
{
	return FVector(VistaBaseXCm + Area * VistaSpacingCm, 0.0, Spots[Area].CarCm.Z);
}

void AODMainMenu::SpawnVistas()
{
	AODGameMode* GM = GetWorld()->GetAuthGameMode<AODGameMode>();
	if (bVistasSpawned || !GM)
	{
		return;
	}
	bVistasSpawned = true;
	for (int32 i = 0; i < Spots.Num(); ++i)
	{
		if (!Spots[i].bValid)
		{
			continue;
		}
		AODWeatherController::LoadClimateFile(AreaKeys[i] + TEXT("_test"), Spots[i].Climate);
		const double ViewRad = FMath::DegreesToRadians((double)Spots[i].ViewYawDeg);
		FODVistaRegion Region;
		Region.bActive = true;
		Region.Focus = Spots[i].CarCm;
		Region.Dir = FVector2D(FMath::Cos(ViewRad), FMath::Sin(ViewRad));
		Region.BackCm = VistaCamBackCm;
		Region.FarCm = VistaFarCm;
		Region.PatchCm = VistaPatchCm;
		Region.HalfAngleRad = FMath::DegreesToRadians(VistaHalfAngleDeg);
		Region.PanoFarCm = VistaPanoFarCm;
		for (AActor* A : GM->SpawnVistaSet(AreaKeys[i], VistaCarWorld(i) - Spots[i].CarCm, Region))
		{
			VistaActors.Add(A);
			if (AODForestManager* Forest = Cast<AODForestManager>(A))
			{
				Spots[i].Forest = Forest;
			}
		}
		if (Spots[i].bHasSea)
		{
			const FVector Car = VistaCarWorld(i);
			if (AStaticMeshActor* Sea = GetWorld()->SpawnActor<AStaticMeshActor>(
					FVector(Car.X, Car.Y, Spots[i].SeaZCm), FRotator::ZeroRotator))
			{
				UStaticMeshComponent* Comp = Sea->GetStaticMeshComponent();
				Comp->SetMobility(EComponentMobility::Movable);   // runtime mesh swap needs it
				Comp->SetStaticMesh(LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Plane.Plane")));
				Comp->SetMaterial(0, LoadObject<UMaterialInterface>(nullptr,
					TEXT("/Game/OpenDrive/Materials/M_WaterStylized.M_WaterStylized")));
				Comp->SetCollisionEnabled(ECollisionEnabled::NoCollision);
				Comp->SetCastShadow(false);
				Sea->SetActorScale3D(FVector(VistaSeaHalfCm / 50.0, VistaSeaHalfCm / 50.0, 1.0));   // Plane = 100 uu
				VistaActors.Add(Sea);
			}
		}
	}
	UE_LOG(LogTemp, Display, TEXT("ODMainMenu: spawned %d vista manager(s)"), VistaActors.Num());
	if (VistaActors.Num() > 0)
	{
		GM->FreezeStreaming(true);   // the car leaves the real world while a vista shows
	}
}

void AODMainMenu::DestroyVistas()
{
	if (!bVistasSpawned)
	{
		return;
	}
	for (AActor* A : VistaActors)
	{
		if (IsValid(A))
		{
			A->Destroy();
		}
	}
	VistaActors.Reset();
	bVistasSpawned = false;
	if (ShownVista != INDEX_NONE)
	{
		SetWeatherClimate(INDEX_NONE);   // its forest copy is gone
	}
	ShownVista = INDEX_NONE;
	if (AODGameMode* GM = GetWorld()->GetAuthGameMode<AODGameMode>())
	{
		GM->FreezeStreaming(false);
	}
}

void AODMainMenu::UpdateScene()
{
	if (bGarageMode || !bEntered || bSettingsOpen)
	{
		return;
	}
	const int32 Want = Screen == EODMenuScreen::Scene && VistaActors.Num() > 0 && Spots.IsValidIndex(AreaIdx) && Spots[AreaIdx].bValid
		? AreaIdx : INDEX_NONE;
	if (Want == ShownVista)
	{
		return;
	}
	AODVehiclePawn* Pawn = FindPawn();
	if (!Pawn)
	{
		return;
	}
	ShownVista = Want;
	UCameraComponent* Cam = Pawn->FindComponentByClass<UCameraComponent>();
	AODGarageEnvironment* Garage = EnsureGarageEnvironment();
	if (Want == INDEX_NONE)
	{
		// back into the garage room exactly as EnterCarConfig() framed it
		Pawn->PlaceInShowroom(ShowroomLocationCm);
		Pawn->SetShowroom(true);
		Pawn->SetActorRotation(FRotator(0.0f, ShowroomYawDeg, 0.0f));
		SetWeatherClimate(INDEX_NONE);   // home climate, sampled at the showroom like level start
		if (Cam)
		{
			Cam->SetWorldTransform(GarageCamXf);
		}
		if (Garage)
		{
			Garage->SetActive(true);
		}
		return;
	}
	if (Garage)
	{
		Garage->SetActive(false);
	}
	const FODMenuSpot& S = Spots[Want];
	const FVector Car = VistaCarWorld(Want);   // road surface under the car
	const UODVehicleMovement* Move = FindMovement();
	Pawn->PlaceInShowroom(Car + FVector(0.0, 0.0, Move ? Move->GetRestGroundClearanceCm() : 0.0f));
	Pawn->SetActorRotation(FRotator(0.0f, S.CarYawDeg, 0.0f));
	Pawn->SetShowroom(true);   // detaches the camera; placed for the vista below
	SetWeatherClimate(Want);   // this vista's climate (2026-10-05)
	if (Cam)
	{
		const FRotator View(0.0, S.ViewYawDeg, 0.0);
		const FVector Fwd = View.Vector();
		const FVector Right = FRotator(0.0, S.ViewYawDeg + 90.0, 0.0).Vector();
		const FVector CamPos = Car - Fwd * VistaCamBackCm + Right * VistaCamSideCm
			+ FVector(0.0, 0.0, VistaCamHeightCm);
		Cam->SetWorldLocationAndRotation(CamPos, FRotator(VistaCamPitchDeg, S.ViewYawDeg, 0.0));
	}
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

	// summed over every terrain/road manager (2026-10-05, large world)
	const AODVehiclePawn* Pawn = FindPawn();
	for (TActorIterator<AODTerrainManager> It(GetWorld()); It; ++It)
	{
		int32 Ready = 0, Total = 0;
		It->GetStreamingProgress(Pawn ? Pawn->GetActorLocation() : FVector::ZeroVector, Ready, Total);
		OutTerrainReady += Ready;
		OutTerrainTotal += Total;
	}
	for (TActorIterator<AODRoadManager> It(GetWorld()); It; ++It)
	{
		int32 Ready = 0, Total = 0;
		It->GetStreamingProgress(Ready, Total);
		OutRoadReady += Ready;
		OutRoadTotal += Total;
	}
}

void AODMainMenu::GetRow(int32 Row, FString& OutLabel, FString& OutValue) const
{
	switch (Row)
	{
	case RowCar:
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
	case RowEngine:
		OutLabel = TEXT("Engine");
		OutValue = Engines[EngineIdx].Name;
		if (EngineIdx == 0)
		{
			// "Stock (V8)" - first word of the borrowed synth voice's name
			const FString Voice = StockVoice(ActiveDef(FindPawn())).Name;
			OutValue = FString::Printf(TEXT("Stock (%s)"),
				*Voice.Left(Voice.Find(TEXT(" "))));
		}
		break;
	case RowDrivetrain:
		OutLabel = TEXT("Drivetrain");
		OutValue = Layouts[DriveIdx].Name;
		if (DriveIdx == 0)
		{
			const UODVehicleDef* Def = ActiveDef(FindPawn());
			const EODDriveLayout L = Def ? Def->DriveLayout : EODDriveLayout::RWD;
			OutValue = FString::Printf(TEXT("Stock (%s)"),
				L == EODDriveLayout::FWD ? TEXT("FWD")
				: L == EODDriveLayout::AWD ? TEXT("AWD") : TEXT("RWD"));
		}
		break;
	case RowTuning:
		OutLabel = TEXT("Tuning");
		OutValue = TEXT("(stock)");
		if (const AODTuningManager* Tuning = FindActor<AODTuningManager>(GetWorld());
			Tuning && !Tuning->CurrentPresetName.IsEmpty())
		{
			OutValue = Tuning->CurrentPresetName;
		}
		break;
	case RowExhaust:
		OutLabel = TEXT("Exhaust");
		OutValue = ExhaustPresets[ExhaustPresetIdx].Name;
		break;
	case RowSound:
		OutLabel = TEXT("Customize Sound");
		OutValue.Empty();
		break;
	case RowLoudness:
		OutLabel = TEXT("Loudness");
		OutValue = FString::Printf(TEXT("%d%%"), FMath::RoundToInt(ExhaustLoudness * 100.0f));
		break;
	case RowRasp:
		OutLabel = TEXT("Rasp");
		OutValue = FString::Printf(TEXT("%d%%"), FMath::RoundToInt(ExhaustRasp * 100.0f));
		break;
	case RowMuffler:
		// muffler cutoff shown as brightness: 200 Hz (muffled) = 0%, 8000 Hz = 100%
		OutLabel = TEXT("Tone");
		OutValue = FString::Printf(TEXT("%d%%"), FMath::RoundToInt((ExhaustMufflerCutoff - 200.0f) / 78.0f));
		break;
	case RowPop:
		OutLabel = TEXT("Pops");
		OutValue = FString::Printf(TEXT("%d%%"), FMath::RoundToInt(ExhaustPop * 100.0f));
		break;
	case RowSeason:
		OutLabel = TEXT("Season");
		OutValue = SeasonNames[SeasonIdx];
		break;
	case RowTime:
		OutLabel = TEXT("Time of Day");
		OutValue = TimeOfDayPresets[TimeIdx].Name;
		break;
	case RowWeather:
		OutLabel = TEXT("Weather");
		OutValue = FName::NameToDisplayString(WeatherNames[WeatherIdx], false);   // "HeavyRain" -> "Heavy Rain"
		break;
	case RowArea:
		OutLabel = TEXT("Area");
		OutValue = AreaNames.IsValidIndex(AreaIdx) ? AreaNames[AreaIdx] : FString();
		break;
	case RowDrive:
		OutLabel = TEXT("Start Driving");
		OutValue.Empty();
		break;
	case RowStart:
		OutLabel = TEXT("Drive");
		OutValue.Empty();
		break;
	case RowGarage:
		OutLabel = TEXT("Garage");
		OutValue.Empty();
		break;
	case RowSettings:
		OutLabel = TEXT("Settings");
		OutValue.Empty();
		break;
	case RowBack:
		OutLabel = bGarageMode ? TEXT("Resume") : TEXT("Back");
		OutValue.Empty();
		break;
	case RowQuit:
		OutLabel = TEXT("Quit");
		OutValue.Empty();
		break;
	default:
		OutLabel.Empty();
		OutValue.Empty();
		break;
	}
}

AODSettingsMenu* AODMainMenu::EnsureSettingsMenu()
{
	if (SettingsMenu)
	{
		return SettingsMenu;
	}
	if (AODSettingsMenu* Found = FindActor<AODSettingsMenu>(GetWorld()))
	{
		SettingsMenu = Found;
		return SettingsMenu;
	}
	if (UWorld* World = GetWorld())
	{
		SettingsMenu = World->SpawnActor<AODSettingsMenu>();
	}
	return SettingsMenu;
}
