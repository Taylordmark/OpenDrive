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
#include "Engine/Texture2D.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "Camera/PlayerCameraManager.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/SpringArmComponent.h"
#include "ImageUtils.h"
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
#include "ContentStreaming.h"
#include "ShaderCompiler.h"
#include "ODBuildingManager.h"

#include "ODDayNightController.h"
#include "ODEngineSynth.h"
#include "ODForestManager.h"
#include "ODFurnitureManager.h"
#include "ODGameMode.h"
#include "ODGarageEnvironment.h"
#include "ODGuardrailManager.h"
#include "ODMenuGlobe.h"
#include "ODRaceManager.h"
#include "ODRoadManager.h"
#include "ODSeasonController.h"
#include "ODSettingsMenu.h"
#include "ODTerrainManager.h"
#include "ODTuningManager.h"
#include "ODVehicleAudio.h"
#include "ODVehicleDef.h"
#include "ODVehicleMovement.h"
#include "ODVehiclePawn.h"
#include "ODWaterManager.h"
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
// Dawn / Golden Hour / Dusk follow the real sun (2026-10-20): the hour the
// sun crosses SunDeg at this area and date (Dir +1 rising, -1 setting, 0 =
// fixed Hour; Hour is also the fallback). Dusk -5 deg = NightFactor ~0.96.
// Mirrored in pipeline/check_night_exposure.py.
struct FTimePreset
{
	const TCHAR* Name;
	float Hour;
	float SunDeg = 0.0f;
	int32 Dir = 0;
};
const FTimePreset TimeOfDayPresets[] = {
	{TEXT("Dawn"), 6.4f, 3.0f, 1}, {TEXT("Morning"), 10.0f}, {TEXT("Noon"), 12.0f}, {TEXT("Afternoon"), 15.0f},
	{TEXT("Golden Hour"), 17.5f, 6.0f, -1}, {TEXT("Dusk"), 18.3f, -5.0f, -1}, {TEXT("Night"), 22.0f},
};
const TCHAR* const WeatherNames[] = {
	TEXT("Auto"), TEXT("Clear"), TEXT("Overcast"), TEXT("Rain"), TEXT("HeavyRain"), TEXT("Sleet"), TEXT("Snow"),
	TEXT("Blizzard"), TEXT("Fog"), TEXT("DustStorm"), TEXT("Thunderstorm"), TEXT("Tornado"), TEXT("Hurricane"),
};
// WeatherNames[i] = EODWeatherState(i - 1) - WeatherPossible() relies on it
static_assert(UE_ARRAY_COUNT(WeatherNames) == (int32)EODWeatherState::Hurricane + 2, "Auto + one per EODWeatherState");
const TCHAR* TimeConfigSection = TEXT("OpenDrive.TimeOfDay");
const TCHAR* WeatherConfigSection = TEXT("OpenDrive.Weather");

// Garage-menu vistas (2026-10-05) - tunables. The vista copy is shifted so
// its car lands at (VistaBaseXCm, 0): far outside every real area (California
// is ~1e8 cm across). One set at a time since 2026-10-14 (was one per area,
// 200 km apart). Z stays real (the atmosphere and the season snow line read
// absolute height).
constexpr double VistaBaseXCm = 2.0e8;
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
// a town/track start's view yaw = its car yaw - this (2026-10-14), so the car
// stands three-quarter to the camera, nose to screen right, like
// pipeline/build_menu_spots.py car_heading does for the area's spot
constexpr float StartViewOffsetDeg = 150.0f;

// World screen (2026-10-14): the in-engine globe (AODMenuGlobe), parked like
// the showroom - 30 km up, 10 km from it - so no streaming manager reaches it.
// Altitudes in globe radii and timings are the old globe.gl page's.
const FVector GlobeLocationCm(0.0, 1.0e6, 3.0e6);
constexpr double WorldOverviewAlt = 1.7;
constexpr float WorldShowSec = 0.9f;           // fly out to the overview on (re)entering World
constexpr float WorldBrowseSec = 0.7f;         // arrows: fly to the next pin at 0.7 x overview
constexpr double WorldPickAlt = 0.06;          // fly-in altitude for an area without a relief drape
constexpr const TCHAR* DrawnAreaLevel = TEXT("/Game/Areas/AreaTemplate");   // (2026-10-21) + ?odarea=<key>
constexpr float WorldPickSec = 1.8f;           // picked pin: fly down onto the drape, then the AreaMap screen
constexpr float WorldBlendSec = 0.5f;          // view target blend onto the globe camera
// AreaMap framing (2026-10-14): the drape fits the screen left of the rows
// panel (ODRaceHUD: ~62% of a 16:9 screen), its centre shifted into that part
constexpr double AreaMapAspect = 16.0 / 9.0;   // AODMenuGlobe's FOVs assume 16:9
constexpr double AreaMapFreeW = 0.6;           // fraction of the screen width the area fits in
constexpr double AreaMapShiftW = 0.2;          // camera aims this fraction of the width east of the area centre
constexpr double AreaFitMargin = 1.1;
constexpr double AreaZoomIn = 0.125, AreaZoomOut = 1.5;   // wheel limits around the framing altitude
constexpr float AreaTabSec = 1.0f;             // Q/E: fly to the sibling area's drape
constexpr double StartVistaWaitSec = 10.0;     // Start: the globe stays up at most this long for the vista to stream
constexpr float StartFadeSec = 0.15f;          // globe <-> vista: fade to black, cut, fade back in (each half)
constexpr double DriveEaseSec = 1.0;           // Drive here: camera to the chase spot
constexpr double DriveFadeSec = 0.5;           // Drive elsewhere: fade to black before OpenArea

// Row ids: one global GetRow()/Adjust() switch; each screen's tabs show a slice.
enum ERow : int32
{
	RowCar, RowEngine, RowDrivetrain, RowTuning, RowExhaust, RowSound, RowPaint,   // Garage
	RowLoudness, RowRasp, RowMuffler, RowPop,                            // Sound (from Garage)
	RowColor, RowFinish, RowPattern, RowAccent, RowRims,   // Paint (from Garage)
	RowTime, RowSeason, RowWeather,                                      // Conditions: season before the weather it allows
	RowOpenDrive, RowTrackSelect, RowGarage, RowSettings,                // Main
	RowDrive, RowBack, RowQuit,                                          // footers
	RowPick,                                                             // AreaMap: RowPick + i = Picks[i]
};
const FODMenuTab MainTabs[] = {{TEXT("MAIN"), RowOpenDrive, 4}};          // footer Quit
const FODMenuTab GarageTabs[] = {{TEXT("GARAGE"), RowCar, 7}};            // footer Back
const FODMenuTab SoundTabs[] = {{TEXT("SOUND"), RowLoudness, 4}};         // footer Back
const FODMenuTab PaintTabs[] = {{TEXT("PAINT"), RowColor, 5}};            // footer Back
const FODMenuTab ConditionsTabs[] = {{TEXT("CONDITIONS"), RowTime, 3}};   // Start screen, footer Drive
// globe pins: areas whose centres are closer than this (deg) share one pin,
// and the AreaMap screen gets a tab per area (green_valley / oahu / ...)
constexpr double ClusterDeg = 0.5;

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
	// the menu lists only areas with a menu spot (a vista to show)
	for (int32 i = Spots.Num() - 1; i >= 0; --i)
	{
		if (!Spots[i].bValid)
		{
			AreaLevels.RemoveAt(i);
			AreaNames.RemoveAt(i);
			AreaKeys.RemoveAt(i);
			Spots.RemoveAt(i);
			StartPoses.RemoveAt(i);
		}
	}
	// drawn areas share one template level, told apart by ?odarea= (2026-10-21, area picker B)
	const TCHAR* DrawnKey = GetWorld()->URL.GetOption(TEXT("odarea="), nullptr);
	CurrentAreaIdx = AreaLevels.IndexOfByKey(UWorld::RemovePIEPrefix(GetWorld()->GetPackage()->GetName())
		+ (DrawnKey && *DrawnKey ? FString(TEXT("?odarea=")) + DrawnKey : FString()));
	FString SavedArea;
	GConfig->GetString(AreaConfigSection, TEXT("Level"), SavedArea, GGameUserSettingsIni);
	AreaIdx = FMath::Max(0, AreaLevels.IndexOfByKey(SavedArea));   // missing/unlisted -> first area
	LoadTracks();
	LoadWorldMap();
	GConfig->GetString(AreaConfigSection, TEXT("Track"), TrackPick, GGameUserSettingsIni);
	if (!PickedTrack())
	{
		TrackPick.Empty();   // deleted, or built for another area
	}
	GConfig->GetString(AreaConfigSection, TEXT("Town"), TownPick, GGameUserSettingsIni);

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
	if (const TCHAR* V = Url.GetOption(TEXT("odweather="), nullptr))
	{
		CarriedWeather = FCString::Atoi(V);   // validated in StartAtTown
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
		Bind(EKeys::N, &AODMainMenu::NewAreaKey);   // (2026-10-21) World: + New Area
		Bind(EKeys::Z, &AODMainMenu::UndoDraw);     // Ctrl+Z on New Area
	}
	AreaDraw.Load();   // the draft survives leaving the menu (2026-10-21)

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
	SetScreen(EODMenuScreen::Main, RowOpenDrive);
	HoveredTownIdx = INDEX_NONE;
	bEntered = false;   // EnterCarConfig() re-runs once the pawn is confirmed
	// Deliberately NOT pausing here: the camera manager's cached view stops
	// updating while paused, freezing the showroom camera at its first,
	// unpositioned frame. The preview pawn can't drive off anyway
	// (PlaceInShowroom disables physics).
	// The pause menu's "Main Menu" row arrives here PAUSED (2026-10-09,
	// Taylor: "selecting main menu when driving should take you back to the
	// garage view") - unpause so it runs exactly like a fresh boot: pawn,
	// sky and the re-spawned vista managers all tick again.
	UGameplayStatics::SetGamePaused(this, false);
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
		// buildings too (2026-10-14: the screen used to drop with the city still
		// popping in); a frozen (tick-disabled) manager can't progress, so skip it
		bool bBuildingsSettled = true;
		for (TActorIterator<AODBuildingManager> It(GetWorld()); It && Pawn; ++It)
		{
			bBuildingsSettled = bBuildingsSettled
				&& (!It->IsActorTickEnabled() || It->IsInitialStreamingSettled(Pawn->GetActorLocation()));
		}
		const bool bAllSettled = bPawnReady && bTerrainSettled && bRoadSettled && bBuildingsSettled;
		LoadingPhase = !(bPawnReady && bTerrainSettled && bRoadSettled) ? 0 : !bBuildingsSettled ? 1 : 2;

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
				TEXT("terrainSettled=%d, roadSettled=%d, buildingsSettled=%d)"),
				Elapsed, bPawnReady, bTerrainSettled, bRoadSettled, bBuildingsSettled);
			Stage = EODMenuStage::Playing;
			SetCursorVisible(false);
			return;
		}

		// debounced (continuously settled for LoadingSettleDebounceSec) AND
		// past the minimum display floor (so a same-town reload where
		// everything is already resident doesn't just flash the screen).
		const bool bDebounced = LoadingSettledSinceTime != 0.0
			&& (Now - LoadingSettledSinceTime) >= LoadingSettleDebounceSec;
		// then the renderer (2026-10-14): textures still streaming in / shaders
		// still compiling read as a half-loaded first frame. Capped, since an
		// open world may never report zero wanting textures.
		const bool bRenderIdle = IStreamingManager::Get().GetNumWantingResources() == 0
			&& (!GShaderCompilingManager || GShaderCompilingManager->GetNumRemainingJobs() == 0);
		const bool bRenderReady = bRenderIdle || (bDebounced && Now - LoadingSettledSinceTime >= LoadingRenderWaitSec);
		if (bDebounced && bRenderReady && Elapsed >= LoadingMinDisplaySec)
		{
			UE_LOG(LogTemp, Display,
				TEXT("ODMainMenu: LOADSCREEN released after %.1fs (terrain, roads, buildings settled; render %s)"),
				Elapsed, bRenderIdle ? TEXT("idle") : TEXT("wait capped"));
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
	if (bEntered && bAutoStart)
	{
		bAutoStart = false;
		ApplyTrack();
		StartAtTown(TownPick);   // opened by Drive: straight to the picked start
		return;
	}
	const double Now = GetWorld()->GetRealTimeSeconds();
	AreaDraw.Poll(Now);   // coverage.json / status.json / pass-1 roads, when they change (1 Hz)
	if (VistaAtFrame != 0 && GFrameCounter >= VistaAtFrame)
	{
		VistaAtFrame = 0;
		ShowStartVista(AreaIdx);
	}
	if (DriveStart >= 0.0)
	{
		// Drive (2026-10-14): ease the vista camera onto the car's chase spot
		// (the spring arm's end), or let the fade run, then go
		const float T = float((Now - DriveStart) / (bDriveHere ? DriveEaseSec : DriveFadeSec));
		AODVehiclePawn* Pawn = FindPawn();
		UCameraComponent* Cam = Pawn ? Pawn->FindComponentByClass<UCameraComponent>() : nullptr;
		const USpringArmComponent* Arm = Pawn ? Pawn->FindComponentByClass<USpringArmComponent>() : nullptr;
		if (bDriveHere && Cam && Arm)
		{
			FTransform Xf;
			Xf.Blend(DriveFrom, Arm->GetSocketTransform(USpringArmComponent::SocketName), FMath::SmoothStep(0.0f, 1.0f, T));
			Cam->SetWorldTransform(Xf);
		}
		if (T >= 1.0f)
		{
			DriveStart = -1.0;
			DriveGo();
		}
		return;
	}
	// World -> AreaMap (2026-10-14): enter once the picked pin's fly-in has
	// landed on the drape (PickPin already focused its area)
	if (Screen == EODMenuScreen::World && Globe.IsValid() && !Globe->IsFlying())
	{
		if (PickingPin != INDEX_NONE)
		{
			PickingPin = INDEX_NONE;
			EnterArea(AreaIdx);
		}
		else
		{
			Globe->ClearDrape();   // flown back out (it faded with altitude on the way)
		}
	}
	ClampAreaView();
	// Start (2026-10-14): the globe holds until the vista has streamed (or the
	// cap), then fades out, cuts to the vista camera and fades back in
	if (FadeSwapAt >= 0.0 && Now >= FadeSwapAt)
	{
		CutTo(FindPawn());
	}
	else if (bGlobeView && FadeSwapAt < 0.0 && Screen == EODMenuScreen::Start
		&& (IsStartVistaReady() || Now - AreaEnterTime > StartVistaWaitSec))
	{
		if (APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0); PC && PC->PlayerCameraManager)
		{
			PC->PlayerCameraManager->StartCameraFade(0.0f, 1.0f, StartFadeSec, FLinearColor::Black, false, true);
		}
		FadeSwapAt = Now + StartFadeSec;
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
	// Taylor, 2026-07-28 live: "you spawn directly under the car and the car
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
	Pawn->SetShowroom(true);
	// static display angle, not a spin (Taylor, 2026-07-28: "stop the car
	// rotating stuff too") - a single fixed 3/4 view, set once.
	Pawn->SetActorRotation(FRotator(0.0f, ShowroomYawDeg, 0.0f));

	// Garage room (2026-08): built around wherever the car is RIGHT NOW (the
	// pawn was just placed at ShowroomLocationCm a few lines up).
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
	ApplySeason();   // StartAtTown re-applies season + weather at the real start
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
	ShownVista = INDEX_NONE;   // the area vista spawns on entering an area (ShowStartVista)
	// the car sits in the garage room now: freeze the real managers at once
	// (as SpawnVistas did here), not only when the first vista spawns
	AODGameMode* GM = GetWorld()->GetAuthGameMode<AODGameMode>();
	if (!bAutoStart && !bVistasSpawned && GM)
	{
		bVistasSpawned = true;
		GM->FreezeStreaming(true);
	}
	bEntered = true;
}

// ---- input ---------------------------------------------------------------

bool AODMainMenu::BrowsePin(int32 Dir)
{
	if (Stage != EODMenuStage::Menu || Screen != EODMenuScreen::World)
	{
		return false;
	}
	if (const int32 N = Globe.IsValid() ? Globe->GetPins().Num() : 0; N > 0 && PickingPin == INDEX_NONE)
	{
		SetFocusedPin((FocusedPin + Dir + N) % N);
		const FODGlobePin& P = Globe->GetPins()[FocusedPin];
		Globe->FlyTo(P.Lat, P.Lng, WorldOverviewAlt * 0.7, WorldBrowseSec);
	}
	return true;
}

void AODMainMenu::Up()
{
	if (bSettingsOpen)
	{
		if (SettingsMenu) { SettingsMenu->MoveSelection(-1); }
		return;
	}
	if (BrowsePin(-1))
	{
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
	if (BrowsePin(+1))
	{
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
		SetTab((TabIdx + Dir + N) % N);
	}
}

TArrayView<const FODMenuTab> AODMainMenu::GetTabs() const
{
	switch (Screen)
	{
	case EODMenuScreen::Garage: return GarageTabs;
	case EODMenuScreen::Sound: return SoundTabs;
	case EODMenuScreen::Paint: return PaintTabs;
	case EODMenuScreen::AreaMap: return AreaTabs;
	case EODMenuScreen::Start: return ConditionsTabs;
	case EODMenuScreen::NewArea: return AreaDraw.GetTabs();
	default: return MainTabs;   // World draws no panel
	}
}

int32 AODMainMenu::GetFooterRow() const
{
	return Screen == EODMenuScreen::Main ? RowQuit : Screen == EODMenuScreen::Start ? RowDrive
		: Screen == EODMenuScreen::NewArea ? AreaDraw.Footer() : RowBack;
}

FString AODMainMenu::GetScreenTitle() const
{
	switch (Screen)
	{
	case EODMenuScreen::Garage: return TEXT("Garage");
	case EODMenuScreen::Sound: return TEXT("Exhaust Sound");
	case EODMenuScreen::Paint: return TEXT("Paint & Wheels");
	case EODMenuScreen::World:
	case EODMenuScreen::AreaMap: return bTrackMode ? TEXT("Track Select") : TEXT("Open Drive");
	case EODMenuScreen::Start: return AreaNames.IsValidIndex(AreaIdx) ? AreaNames[AreaIdx] : TEXT("Conditions");
	case EODMenuScreen::NewArea: return AreaDraw.Title();
	default: return TEXT("OpenDrive");
	}
}

int32 AODMainMenu::GetSelectedPick() const
{
	return Screen == EODMenuScreen::AreaMap && Picks.IsValidIndex(Selected - RowPick) ? Selected - RowPick : INDEX_NONE;
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
	if (Stage == EODMenuStage::Menu && Row >= 0 && (Row <= RowQuit || Picks.IsValidIndex(Row - RowPick)))
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
		if (Screen == EODMenuScreen::AreaMap && Siblings[Tab] != AreaIdx)
		{
			FocusArea(Siblings[Tab]);   // tabs are the cluster's areas: re-drape and fly there
			FlyToArea(AreaTabSec);
			ShowStartVista(AreaIdx);    // its vista streams while a turnout is picked
		}
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
	if (BrowsePin(-1))
	{
		return;
	}
	if (Stage == EODMenuStage::Menu)
	{
		Screen == EODMenuScreen::AreaMap ? SwitchTab(-1) : Adjust(-1);
	}
}

void AODMainMenu::Right()
{
	if (bSettingsOpen)
	{
		Adjust(+1);
		return;
	}
	if (BrowsePin(+1))
	{
		return;
	}
	if (Stage == EODMenuStage::Menu)
	{
		Screen == EODMenuScreen::AreaMap ? SwitchTab(+1) : Adjust(+1);
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
	if (Stage == EODMenuStage::Menu && Screen == EODMenuScreen::World)
	{
		PickPin(FocusedPin);
		return;
	}
	if (Stage == EODMenuStage::Menu && Screen == EODMenuScreen::NewArea)
	{
		if (const FString Key = AreaDraw.Activate(Selected); !Key.IsEmpty())
		{
			OpenDrawnArea(Key);
		}
		const FODMenuTab& Tab = GetTabs()[0];
		if (Selected != GetFooterRow() && (Selected < Tab.First || Selected >= Tab.First + Tab.Count))
		{
			Selected = GetFooterRow();   // a sent draft became its card: its rows differ
		}
		return;
	}
	if (Stage == EODMenuStage::Menu && DriveStart < 0.0)
	{
		switch (Selected)
		{
		case RowOpenDrive:
		case RowTrackSelect:
			bTrackMode = Selected == RowTrackSelect;
			ShowWorld();
			break;
		case RowGarage: SetScreen(EODMenuScreen::Garage, RowCar); break;
		case RowSound: SetScreen(EODMenuScreen::Sound, RowLoudness); break;
		case RowPaint: SetScreen(EODMenuScreen::Paint, RowColor); break;
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
			// not while the globe still waits on the vista (2026-10-14): a double
			// Enter / A on a pick would skip the Start screen and drive blind
			if (!IsStartLoading())
			{
				DriveNow();
			}
			break;
		default:
			if (const int32 Pick = GetSelectedPick(); Pick != INDEX_NONE)
			{
				TownPick = Picks[Pick].Town;     // one of the two is always empty:
				TrackPick = Picks[Pick].Track;   // Open Drive is free roam, a track starts at its line
				ShowStartVista(AreaIdx);   // no-op when preloaded; the globe holds until it streams (Tick)
				AreaEnterTime = GetWorld()->GetRealTimeSeconds();   // the cap counts from here, reused set or not
				SetScreen(EODMenuScreen::Start, RowDrive);
			}
			else
			{
				Adjust(+1);   // Enter cycles a value row, like the pause menu
			}
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
		SetScreen(EODMenuScreen::Main, RowOpenDrive);
	}
	else if (Stage != EODMenuStage::Menu || DriveStart >= 0.0)
	{
		return;
	}
	else if (Screen == EODMenuScreen::Sound || Screen == EODMenuScreen::Paint)
	{
		SetScreen(EODMenuScreen::Garage, Screen == EODMenuScreen::Sound ? RowSound : RowPaint);
	}
	else if (Screen == EODMenuScreen::Start)
	{
		// back onto the row that was picked, on its area's tab, over the drape
		const int32 Pick = Picks.IndexOfByPredicate([this](const FODMenuPick& K) { return K.Town == TownPick && K.Track == TrackPick; });
		SetScreen(EODMenuScreen::AreaMap, RowPick + FMath::Max(0, Pick));
		TabIdx = FMath::Max(0, Siblings.IndexOfByKey(AreaIdx));
		// the area's vista stays up (2026-10-16): another turnout shows the same one
		if (!bGlobeView)
		{
			CutTo(Globe.Get());   // no fade-out: the pawn camera is already back in the garage room
		}
		else if (FadeSwapAt >= 0.0)
		{
			FadeSwapAt = -1.0;   // still on the globe: drop the pending cut and its fade
			if (APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0); PC && PC->PlayerCameraManager)
			{
				PC->PlayerCameraManager->StopCameraFade();
			}
		}
	}
	else if (Screen == EODMenuScreen::NewArea)
	{
		AreaDraw.CloseNameBox();
		AreaDraw.JobKey.Empty();   // back to the draft next time (kept in GameUserSettings)
		ShowWorld();
	}
	else if (Screen == EODMenuScreen::AreaMap)
	{
		ClearStartVista();   // leaving the area
		ShowWorld();   // flies back out; Tick clears the drape once there
	}
	else if (Screen != EODMenuScreen::Main)
	{
		if (Screen == EODMenuScreen::World)
		{
			ViewFrom(FindPawn(), 0.0f);   // straight back into the garage room
		}
		SetScreen(EODMenuScreen::Main, Screen == EODMenuScreen::Garage ? RowGarage
			: bTrackMode ? RowTrackSelect : RowOpenDrive);
	}
	// Main screen: nothing to back out to
}

// ---- Open Drive / Track Select (2026-10-13) ---------------------------------

void AODMainMenu::ShowWorld()
{
	SetScreen(EODMenuScreen::World, RowBack);
	PickingPin = INDEX_NONE;
	if (!Globe.IsValid())
	{
		Globe = GetWorld()->SpawnActor<AODMenuGlobe>(GlobeLocationCm, FRotator::ZeroRotator);
		if (!Globe.IsValid())
		{
			return;
		}
		Globe->SetLines(WorldLines[0], WorldLines[1], WorldLines[2]);
	}
	// one pin per cluster, at its first area (rebuilt: Track Select lists only
	// areas with tracks); focus the picked area's cluster
	TArray<FODGlobePin> Pins;
	int32 Focus = 0;
	for (int32 i = 0; i < AreaKeys.Num(); ++i)
	{
		const TArray<int32> Cluster = AreaListed(i) ? ClusterOf(i) : TArray<int32>();
		if (Cluster.IsEmpty() || Cluster[0] != i)
		{
			continue;
		}
		TArray<FString> Names;
		int32 Count = 0;
		for (const int32 A : Cluster)
		{
			Names.Add(AreaNames[A]);
			Count += bTrackMode
				? Tracks.FilterByPredicate([&](const FTrackInfo& T) { return T.Area == AreaKeys[A]; }).Num()
				: StartPoses[A].Num() - 1;   // turnouts, not the Default
			Focus = A == AreaIdx ? Pins.Num() : Focus;
		}
		FODGlobePin& Pin = Pins.AddDefaulted_GetRef();
		Pin.Key = AreaKeys[i];
		Pin.Name = FString::Join(Names, TEXT(" / "));
		Pin.Sub = FString::Printf(TEXT("%d %s"), Count,
			bTrackMode ? (Count == 1 ? TEXT("track") : TEXT("tracks")) : (Count == 1 ? TEXT("turnout") : TEXT("turnouts")));
		Pin.Lat = AreaLL[i].Y;
		Pin.Lng = AreaLL[i].X;
	}
	Globe->SetPins(Pins);
	FocusedPin = INDEX_NONE;
	if (Pins.IsValidIndex(Focus))
	{
		SetFocusedPin(Focus);
		Globe->FlyTo(Pins[Focus].Lat, Pins[Focus].Lng, WorldOverviewAlt, WorldShowSec);
	}
	ViewFrom(Globe.Get(), WorldBlendSec);
}

void AODMainMenu::NewAreaKey()
{
	if (Stage == EODMenuStage::Menu && Screen == EODMenuScreen::World && !bSettingsOpen && PickingPin == INDEX_NONE)
	{
		BeginNewArea();
	}
}

void AODMainMenu::UndoDraw()
{
	const APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0);
	if (Screen == EODMenuScreen::NewArea && PC && (PC->IsInputKeyDown(EKeys::LeftControl) || PC->IsInputKeyDown(EKeys::RightControl)))
	{
		AreaDraw.Undo();
	}
}

void AODMainMenu::BeginNewArea()
{
	// (2026-10-21) the draft over the globe: framed on its points, else zoomed in where the globe looks
	AreaDraw.JobKey.Empty();
	SetScreen(EODMenuScreen::NewArea, 0);
	if (!Globe.IsValid())
	{
		return;
	}
	Globe->ClearDrape();
	const TArray<FVector2D>& V = AreaDraw.Verts;
	if (V.IsEmpty())
	{
		const FVector View = Globe->GetView();
		Globe->FlyTo(View.X, View.Y, AODMenuGlobe::FitAltitude(120.0), WorldBrowseSec);
		return;
	}
	FVector2D C = FVector2D::ZeroVector;
	for (const FVector2D& P : V)
	{
		C += P / V.Num();
	}
	Globe->FlyTo(C.Y, C.X, AODMenuGlobe::FitAltitude(FMath::Max(ODAreaGeo::SidesKm(V).GetMax() * 2.0, 20.0)), WorldBrowseSec);
}

void AODMainMenu::OpenAreaCard(const FString& Key)
{
	if (Stage != EODMenuStage::Menu || PickingPin != INDEX_NONE)
	{
		return;
	}
	AreaDraw.OpenCard(Key);
	SetScreen(EODMenuScreen::NewArea, AreaDraw.Footer());
	const FODAreaDraw::FCovArea* A = AreaDraw.Coverage.FindByPredicate([&](const FODAreaDraw::FCovArea& C) { return C.Key == Key; });
	if (Globe.IsValid() && A && !A->Outline.IsEmpty())
	{
		Globe->ClearDrape();
		Globe->FlyTo(A->Centre.Y, A->Centre.X, AODMenuGlobe::FitAltitude(FMath::Max(ODAreaGeo::SidesKm(A->Outline).GetMax() * 1.6, 8.0)), WorldPickSec);
	}
}

void AODMainMenu::SetFocusedPin(int32 Pin)
{
	if (!Globe.IsValid() || !Globe->GetPins().IsValidIndex(Pin) || Pin == FocusedPin
		|| PickingPin != INDEX_NONE)   // flying in: the picked pin stays focused
	{
		return;
	}
	FocusedPin = Pin;   // no vista on hover (2026-10-14): ShowStartVista, once the pin is picked
}

void AODMainMenu::PickPin(int32 Pin)
{
	if (Stage != EODMenuStage::Menu || Screen != EODMenuScreen::World || PickingPin != INDEX_NONE
		|| !Globe.IsValid() || !Globe->GetPins().IsValidIndex(Pin))
	{
		return;
	}
	const int32 Area = PinArea(Pin);
	if (Area == INDEX_NONE)
	{
		return;
	}
	SetFocusedPin(Pin);
	// drape its relief and fly down onto it (2026-10-14); Tick enters AreaMap once landed
	FocusArea(Area);
	PickingPin = Pin;
	FlyToArea(WorldPickSec);
	// the vista (2026-10-16) streams during the fly-in and the turnout pick, but
	// spawning it holds the game thread for seconds (livetest6: 6.1 s), so it
	// waits two frames for the picked pin to reach the screen first (2026-10-22)
	VistaAtFrame = GFrameCounter + 2;
	UE_LOG(LogTemp, Display, TEXT("ODMainMenu: pin picked -> %s"), *AreaKeys[AreaIdx]);
}

int32 AODMainMenu::PinArea(int32 Pin) const
{
	const int32 Area = Globe.IsValid() && Globe->GetPins().IsValidIndex(Pin)
		? AreaKeys.IndexOfByKey(Globe->GetPins()[Pin].Key) : INDEX_NONE;
	return Area != INDEX_NONE && ClusterOf(Area).Contains(AreaIdx) ? AreaIdx : Area;
}

void AODMainMenu::FlyToArea(float Seconds)
{
	if (!Globe.IsValid() || !AreaKeys.IsValidIndex(AreaIdx))
	{
		return;
	}
	const FString& Key = AreaKeys[AreaIdx];
	const TArray<FVector2D>* LL = ReliefCorners.Find(Key);   // (lat, lng) NW, NE, SE, SW
	const TObjectPtr<UTexture2D>* Tex = ReliefTex.Find(Key);
	if (!LL || !Tex || !*Tex)
	{
		// no relief baked: the old fly-in over the area's centre, no drape
		const FVector2D C(AreaLL[AreaIdx].Y, AreaLL[AreaIdx].X);
		Globe->ClearDrape();
		AreaFitAlt = WorldPickAlt;
		AreaViewBox = FBox2D(C, C);
		Globe->FlyTo(C.X, C.Y, WorldPickAlt, Seconds);
		return;
	}
	// fit the drape's N-S / E-W extent into the free part of the screen (as a
	// vertical span at 16:9), the camera aimed east of its centre so it sits left
	FBox2D Box(ForceInit);
	for (const FVector2D& C : *LL)
	{
		Box += C;
	}
	const FVector2D Mid = Box.GetCenter(), Size = Box.GetSize();
	const double KmPerDeg = FMath::DegreesToRadians(6371.0);
	const double KmPerDegLng = KmPerDeg * FMath::Cos(FMath::DegreesToRadians(Mid.X));
	const double SpanKm = AreaFitMargin * FMath::Max(Size.X * KmPerDeg, Size.Y * KmPerDegLng / (AreaMapAspect * AreaMapFreeW));
	const double ShiftLng = AreaMapShiftW * SpanKm * AreaMapAspect / KmPerDegLng;
	AreaFitAlt = AODMenuGlobe::FitAltitude(SpanKm);
	AreaViewBox = FBox2D(Box.Min, Box.Max + FVector2D(0.0, ShiftLng));
	Globe->SetDrape(*Tex, *LL);
	Globe->FlyTo(Mid.X, Mid.Y + ShiftLng, AreaFitAlt, Seconds);
}

void AODMainMenu::ClampAreaView()
{
	// AreaMap drag / wheel (ODRaceHUD): the camera centre stays over the area, its zoom near the framing
	if (Screen != EODMenuScreen::AreaMap || !Globe.IsValid() || Globe->IsFlying() || !AreaViewBox.bIsValid)
	{
		return;
	}
	const FVector V = Globe->GetView();
	const FVector C(FMath::Clamp(V.X, AreaViewBox.Min.X, AreaViewBox.Max.X), FMath::Clamp(V.Y, AreaViewBox.Min.Y, AreaViewBox.Max.Y),
		FMath::Clamp(V.Z, AreaFitAlt * AreaZoomIn, AreaFitAlt * AreaZoomOut));
	if (!C.Equals(V, 1e-9))
	{
		Globe->FlyTo(C.X, C.Y, C.Z, 0.0f);
	}
}

void AODMainMenu::CutTo(AActor* Target)
{
	FadeSwapAt = -1.0;
	ViewFrom(Target, 0.0f);
	if (APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0); PC && PC->PlayerCameraManager)
	{
		PC->PlayerCameraManager->StartCameraFade(1.0f, 0.0f, StartFadeSec, FLinearColor::Black);
	}
}

bool AODMainMenu::AreaCmToScreen(const APlayerController* PC, const FVector2D& Cm, FVector2D& OutScreen) const
{
	// bilinear in the relief's corners (pipeline/bake_area_relief.py): u west -> east, v north (min Y) -> south
	const FString Key = AreaKeys.IsValidIndex(AreaIdx) ? AreaKeys[AreaIdx] : FString();
	const FBox2D* Box = ReliefBox.Find(Key);
	const TArray<FVector2D>* C = ReliefCorners.Find(Key);
	if (!Globe.IsValid() || !Box || !Box->bIsValid || !C)
	{
		return false;
	}
	const FVector2D UV = (Cm - Box->Min) / Box->GetSize();
	const FVector2D LL = FMath::Lerp(FMath::Lerp((*C)[0], (*C)[1], UV.X), FMath::Lerp((*C)[3], (*C)[2], UV.X), UV.Y);
	return Globe->LatLngToScreen(PC, LL.X, LL.Y, OutScreen);
}

void AODMainMenu::ViewFrom(AActor* Target, float BlendSec)
{
	if (APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0); PC && Target)
	{
		// lock outgoing: leaving the vista swaps the pawn camera back to the garage
		// room (UpdateScene) mid-blend; the blend should leave the vista instead
		PC->SetViewTargetWithBlend(Target, BlendSec, VTBlend_EaseInOut, 2.0f, true);
	}
	bGlobeView = Target && Target == Globe.Get();
}

bool AODMainMenu::AreaListed(int32 Area) const
{
	return !(AreaHidden.IsValidIndex(Area) && AreaHidden[Area])
		&& (!bTrackMode || Tracks.ContainsByPredicate([&](const FTrackInfo& T) { return T.Area == AreaKeys[Area]; }));
}

TArray<int32> AODMainMenu::ClusterOf(int32 Area) const
{
	TArray<int32> Out;
	for (int32 i = 0; i < AreaKeys.Num(); ++i)
	{
		if (AreaListed(i) && AreaLL.IsValidIndex(i) && FVector2D::Distance(AreaLL[i], AreaLL[Area]) < ClusterDeg)
		{
			Out.Add(i);
		}
	}
	return Out;
}

void AODMainMenu::LoadWorldMap()
{
	// {"coast", "borders", "states": [[lon, lat, ...]], "areas": {"<area>": {"ll": [lon, lat],
	// "hidden"}}} (pipeline/bake_world_map.py); lines go to the globe, lat first
	AreaLL.Init(FVector2D::ZeroVector, AreaKeys.Num());
	AreaHidden.Init(false, AreaKeys.Num());
	FString Text;
	TSharedPtr<FJsonObject> Root;
	const TSharedPtr<FJsonObject>* Areas = nullptr;
	if (!FFileHelper::LoadFileToString(Text, *(FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks/worldmap.json")))
		|| !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Root) || !Root.IsValid()
		|| !Root->TryGetObjectField(TEXT("areas"), Areas))
	{
		UE_LOG(LogTemp, Warning, TEXT("ODMainMenu: no Masks/worldmap.json - run pipeline/bake_world_map.py"));
		return;
	}
	const TCHAR* LineSets[] = {TEXT("coast"), TEXT("borders"), TEXT("states")};
	for (int32 k = 0; k < 3; ++k)
	{
		WorldLines[k].Reset();
		const TArray<TSharedPtr<FJsonValue>>* Lines = nullptr;
		if (!Root->TryGetArrayField(LineSets[k], Lines))
		{
			continue;
		}
		for (const TSharedPtr<FJsonValue>& L : *Lines)
		{
			const TArray<TSharedPtr<FJsonValue>>& V = L->AsArray();
			TArray<float>& Out = WorldLines[k].AddDefaulted_GetRef();
			Out.Reserve(V.Num());
			for (int32 i = 0; i + 1 < V.Num(); i += 2)
			{
				Out.Add((float)V[i + 1]->AsNumber());   // lon, lat -> lat, lng
				Out.Add((float)V[i]->AsNumber());
			}
		}
	}
	for (int32 i = 0; i < AreaKeys.Num(); ++i)
	{
		const TSharedPtr<FJsonObject>* A = nullptr;
		const TArray<TSharedPtr<FJsonValue>>* LL = nullptr;
		if (!(*Areas)->TryGetObjectField(AreaKeys[i], A))
		{
			continue;
		}
		(*A)->TryGetBoolField(TEXT("hidden"), AreaHidden[i]);   // a test variant inside another area
		if ((*A)->TryGetArrayField(TEXT("ll"), LL) && LL->Num() == 2)
		{
			AreaLL[i] = FVector2D((*LL)[0]->AsNumber(), (*LL)[1]->AsNumber());
		}
	}
}

void AODMainMenu::EnterArea(int32 Area)
{
	Siblings = ClusterOf(Area);
	if (Siblings.IsEmpty())
	{
		Siblings = {Area};
	}
	AreaTabs.Reset();
	for (const int32 A : Siblings)
	{
		// every tab's rows start at RowPick; Count is that area's own row count
		const int32 N = bTrackMode
			? Tracks.FilterByPredicate([&](const FTrackInfo& T) { return T.Area == AreaKeys[A]; }).Num()
			: StartPoses[A].Num();
		AreaTabs.Add({*AreaNames[A], RowPick, N});
	}
	SetScreen(EODMenuScreen::AreaMap, RowPick);
	SetTab(Siblings.IndexOfByKey(Area));   // Area == AreaIdx (PickPin focused it): no re-fly
}

void AODMainMenu::FocusArea(int32 Area)
{
	AreaIdx = Area;
	const FString& Key = AreaKeys[Area];

	// road map: centerline_map_<area>_test.bin, ODM1 (see AODRoadManager::LoadMapCenterline)
	AreaMap.Reset();
	TArray<uint8> Bin;
	if (FFileHelper::LoadFileToArray(Bin, *(FPaths::ProjectContentDir()
			/ FString::Printf(TEXT("OpenDrive/Masks/centerline_map_%s_test.bin"), *Key)))
		&& Bin.Num() >= 8 && FMemory::Memcmp(Bin.GetData(), "ODM1", 4) == 0)
	{
		uint32 N = 0;
		FMemory::Memcpy(&N, Bin.GetData() + 4, 4);
		for (int64 i = 0; i < N && 8 + (i + 1) * 8 <= Bin.Num(); ++i)
		{
			float XY[2];
			FMemory::Memcpy(XY, Bin.GetData() + 8 + i * 8, 8);
			AreaMap.Add(FVector2D(XY[0], XY[1]));
		}
	}

	// AreaMap drape (2026-10-14): shaded relief, cached per area, and a line of stats
	if (!ReliefBox.Contains(Key))
	{
		// relief_<area>.json = {"min_cm": [x, y], "max_cm": [x, y], "corners_ll": [[lat, lng] NW, NE, SE, SW]}:
		// the png's area-local cm rect and where it lies on the globe
		const FString Base = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks/relief_") + Key;
		FString Text;
		TSharedPtr<FJsonObject> Json;
		const TArray<TSharedPtr<FJsonValue>>* Lo = nullptr;
		const TArray<TSharedPtr<FJsonValue>>* Hi = nullptr;
		UTexture2D* Tex = nullptr;
		FBox2D Box(ForceInit);
		if (FFileHelper::LoadFileToString(Text, *(Base + TEXT(".json")))
			&& FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Json) && Json.IsValid()
			&& Json->TryGetArrayField(TEXT("min_cm"), Lo) && Lo->Num() == 2
			&& Json->TryGetArrayField(TEXT("max_cm"), Hi) && Hi->Num() == 2)
		{
			Box = FBox2D(FVector2D((*Lo)[0]->AsNumber(), (*Lo)[1]->AsNumber()), FVector2D((*Hi)[0]->AsNumber(), (*Hi)[1]->AsNumber()));
			Tex = FImageUtils::ImportFileAsTexture2D(Base + TEXT(".png"));
			const TArray<TSharedPtr<FJsonValue>>* Corners = nullptr;
			if (Json->TryGetArrayField(TEXT("corners_ll"), Corners) && Corners->Num() == 4)
			{
				TArray<FVector2D>& LL = ReliefCorners.Add(Key);
				for (const TSharedPtr<FJsonValue>& C : *Corners)
				{
					const TArray<TSharedPtr<FJsonValue>>& V = C->AsArray();
					LL.Add(V.Num() == 2 ? FVector2D(V[0]->AsNumber(), V[1]->AsNumber()) : FVector2D::ZeroVector);
				}
			}
		}
		ReliefTex.Add(Key, Tex);
		ReliefBox.Add(Key, Box);
	}
	double RoadCm = 0.0;
	for (int32 i = 1; i < AreaMap.Num(); ++i)
	{
		if (!AreaMap[i].ContainsNaN() && !AreaMap[i - 1].ContainsNaN())
		{
			RoadCm += FVector2D::Distance(AreaMap[i], AreaMap[i - 1]);
		}
	}
	// ponytail: no elevation range - nothing loaded here knows the area's heights
	const int32 Starts = bTrackMode
		? Tracks.FilterByPredicate([&](const FTrackInfo& T) { return T.Area == Key; }).Num()
		: StartPoses[Area].Num();
	AreaBlurb = FString::Printf(TEXT("%d %s"), Starts,
		bTrackMode ? (Starts == 1 ? TEXT("track") : TEXT("tracks")) : (Starts == 1 ? TEXT("turnout") : TEXT("turnouts")));
	if (RoadCm > 0.0)
	{
		AreaBlurb = FString::Printf(TEXT("%d km of road  \u00B7  %s"), FMath::RoundToInt(RoadCm / 1.0e5), *AreaBlurb);
	}

	Picks.Reset();
	if (!bTrackMode)
	{
		// Turnouts (2026-10-14, menu_spots.json "starts"): "" = the area's
		// menu spot, the Default Turnout, first; the rest by name
		for (const auto& [Name, Pose] : StartPoses[Area])
		{
			FODMenuPick& K = Picks.AddDefaulted_GetRef();
			K.Label = Name.IsEmpty() ? TEXT("Default Turnout") : Name;
			K.Town = Name;
			K.bHasPos = K.bHasStart = true;
			K.Start = Pose;
			K.Pos = FVector2D(Pose.GetLocation());
		}
		Picks.StableSort([](const FODMenuPick& A, const FODMenuPick& B)
			{ return A.Town.IsEmpty() != B.Town.IsEmpty() ? A.Town.IsEmpty() : A.Town < B.Town; });
		return;
	}
	for (const FTrackInfo& T : Tracks)
	{
		if (T.Area != Key)
		{
			continue;
		}
		FODMenuPick& K = Picks.AddDefaulted_GetRef();
		K.Label = T.Title;
		K.Track = T.Name;
		K.Km = T.Km;
		K.bLoop = T.bLoop;
		TArray<FTransform> Gates;
		bool bLoop = false;
		if (AODRaceManager::ReadTrack(T.Name, Gates, K.Line, bLoop) && K.Line.Num() > 0)
		{
			K.bHasPos = true;
			K.Pos = K.Line[0];
		}
		if (K.Line.Num() >= 2 && Gates.Num() > 0)
		{
			// StartAt's track start: the line's first point facing its second; Z = the first gate's road
			const FVector2D D = K.Line[1] - K.Line[0];
			K.bHasStart = true;
			K.Start = FTransform(FRotator(0.0, FMath::RadiansToDegrees(FMath::Atan2(D.Y, D.X)), 0.0),
				FVector(K.Line[0], Gates[0].GetLocation().Z));
		}
		if (const UODRaceSave* Save = Cast<UODRaceSave>(
				UGameplayStatics::LoadGameFromSlot(FString::Printf(TEXT("OpenDrive_track_%s"), *T.Name), 0)))
		{
			K.BestTime = Save->BestTime;
		}
	}
}

void AODMainMenu::DriveNow()
{
	SavePicks();   // (Activate holds Drive until the cut, so no fade is pending here)
	DriveStart = GetWorld()->GetRealTimeSeconds();   // Tick runs the ease / fade, then DriveGo
	bDriveHere = !(AreaLevels.IsValidIndex(AreaIdx) && AreaIdx != CurrentAreaIdx);
	UE_LOG(LogTemp, Display, TEXT("ODMainMenu: Drive pressed (%s)"), bDriveHere ? TEXT("here") : TEXT("open level"));
	const AODVehiclePawn* Pawn = FindPawn();
	if (const UCameraComponent* Cam = Pawn ? Pawn->FindComponentByClass<UCameraComponent>() : nullptr)
	{
		DriveFrom = Cam->GetComponentTransform();
	}
	APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0);
	if (!bDriveHere && PC && PC->PlayerCameraManager)
	{
		PC->PlayerCameraManager->StartCameraFade(0.0f, 1.0f, (float)DriveFadeSec, FLinearColor::Black, false, true);
	}
}

void AODMainMenu::DriveGo()
{
	if (!bDriveHere)
	{
		OpenArea();   // that level's menu auto-starts (bAutoStart) at TownPick / the track
		return;
	}
	ApplyTrack();
	StartAtTown(TownPick);
}

void AODMainMenu::StartAtTown(const FString& Town)
{
	// the Auto weather the area's vista showed (2026-10-16): carried by OpenArea's
	// odweather=, or this level's live vista - the drive opens on that sky
	AODWeatherController* Weather = FindActor<AODWeatherController>(GetWorld());
	int32 Shown = CarriedWeather;
	CarriedWeather = INDEX_NONE;
	if (Shown == INDEX_NONE && VistaArea != INDEX_NONE && Weather)
	{
		Shown = (int32)Weather->State;
	}
	DestroyVistas();   // the vista copies carry the same town names
	[&]
	{
		// the turnout's road-snapped start, "" = the menu spot
		if (const FTransform* Pose = StartPoses.IsValidIndex(AreaIdx) ? StartPoses[AreaIdx].Find(Town) : nullptr)
		{
			StartAt(-1, nullptr, Pose);   // a picked track still overrides it, as before
			return;
		}
		for (TActorIterator<AODRoadManager> It(GetWorld()); It && !Town.IsEmpty(); ++It)
		{
			if (const int32 i = It->GetTownNodes().IndexOfByPredicate([&](const FTownNode& N) { return N.Name == Town; });
				i != INDEX_NONE)
			{
				StartAt(i, *It);
				return;
			}
		}
		StartAt(-1);   // default start (or the track's, which StartAt applies either way)
	}();
	// season + weather at the real start (2026-10-16): EnterCarConfig applied
	// them at the showroom, XY (0, 0), not in this area's biome
	ApplySeason();
	if (WeatherIdx != 0)
	{
		ApplyWeather();
	}
	else if (Weather && Shown >= 0 && Shown <= (int32)EODWeatherState::Hurricane
		&& Weather->CanProduce((EODWeatherState)Shown))
	{
		Weather->ResumeAuto((EODWeatherState)Shown);
	}
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

// ---- Paint screen (4.3 cosmetics v1) ---------------------------------------

namespace
{
// "#RRGGBB Name": ODRaceHUD draws the colour as a chip before the name
FString Swatch(const FColor& C, const TCHAR* Name)
{
	return FString::Printf(TEXT("#%02X%02X%02X %s"), C.R, C.G, C.B, Name);
}

template <typename T, int32 N>
int32 Cycle(int32 Idx, int32 Dir, const T (&)[N]) { return (Idx + Dir + N) % N; }

void PaintRow(const FODCarPaint& P, int32 Row, FString& OutLabel, FString& OutValue)
{
	switch (Row)
	{
	case RowColor:
	{
		OutLabel = TEXT("Color");
		const int32 W = P.SwatchIdx();
		OutValue = P.bFactory ? TEXT("Factory") : Swatch(P.BodySrgb(), W == INDEX_NONE ? TEXT("Custom") : ODCarPaint::Swatches[W].Name);
		break;
	}
	case RowFinish: OutLabel = TEXT("Finish"); OutValue = ODCarPaint::FinishNames[P.Finish]; break;
	case RowPattern: OutLabel = TEXT("Pattern"); OutValue = ODCarPaint::PatternNames[P.Pattern]; break;
	case RowAccent:
		OutLabel = P.Pattern == 0 ? TEXT("Accent (pick a pattern)") : TEXT("Accent Color");
		OutValue = Swatch(ODCarPaint::Swatches[P.Accent].Srgb, ODCarPaint::Swatches[P.Accent].Name);
		break;
	case RowRims:
	{
		OutLabel = TEXT("Rims");
		const ODCarPaint::FRim& R = ODCarPaint::Rims[P.Rim];
		OutValue = R.Kind > 0.0f && P.Rim != ODCarPaint::RimBodyColor ? Swatch(R.Srgb, R.Name) : FString(R.Name);
		break;
	}
	default: break;
	}
}

void AdjustPaint(FODCarPaint& P, int32 Row, int32 Dir)
{
	switch (Row)
	{
	case RowColor:
	{
		// Factory, then the swatches; a custom colour steps from Factory
		const int32 W = P.bFactory ? -1 : P.SwatchIdx();
		const int32 N = UE_ARRAY_COUNT(ODCarPaint::Swatches) + 1;
		const int32 Next = ((W + 1) + Dir + N) % N - 1;
		if (Next < 0)
		{
			P.bFactory = true;
		}
		else
		{
			P.SetSrgb(ODCarPaint::Swatches[Next].Srgb);
		}
		break;
	}
	case RowFinish: P.Finish = Cycle(P.Finish, Dir, ODCarPaint::FinishNames); break;
	case RowPattern: P.Pattern = Cycle(P.Pattern, Dir, ODCarPaint::PatternNames); break;
	case RowAccent: P.Accent = Cycle(P.Accent, Dir, ODCarPaint::Swatches); break;
	case RowRims: P.Rim = Cycle(P.Rim, Dir, ODCarPaint::Rims); break;
	default: break;
	}
}
}

void AODMainMenu::Adjust(int32 Dir)
{
	if (bSettingsOpen)
	{
		if (SettingsMenu) { SettingsMenu->AdjustSelected(Dir); }
		return;
	}
	if (Screen == EODMenuScreen::NewArea)
	{
		AreaDraw.Adjust(Selected, Dir);
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
	case RowColor:
	case RowFinish: case RowPattern: case RowAccent: case RowRims:
		if (AODVehiclePawn* Pawn = FindPawn())
		{
			FODCarPaint P = Pawn->GetPaint();
			AdjustPaint(P, Selected, Dir);
			Pawn->SetPaint(P);   // applied live, saved for this car
		}
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
		ApplyTimeOfDay();   // a new date moves Dawn/Dusk
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
		const FTimePreset& P = TimeOfDayPresets[TimeIdx];
		DayNight->TimeOfDay = P.Dir == 0 ? P.Hour : DayNight->HourAtSunElevation(P.SunDeg, P.Dir > 0, P.Hour);
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
	const bool bVista = Vista != INDEX_NONE && Vista == VistaArea;   // the live start vista (2026-10-14)
	Weather->SetVistaClimate(bVista ? &VistaSpot.Climate : nullptr,
		bVista ? VistaSpot.Forest.Get() : nullptr, bVista ? FVector2D(VistaSpot.CarCm) : FVector2D::ZeroVector);
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
	GConfig->SetString(AreaConfigSection, TEXT("Track"), *TrackPick, GGameUserSettingsIni);
	GConfig->SetString(AreaConfigSection, TEXT("Town"), *TownPick, GGameUserSettingsIni);
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

void AODMainMenu::StartAt(int32 TownIdx, const AODRoadManager* InRoad, const FTransform* Pose)
{
	AODVehiclePawn* Pawn = FindPawn();
	if (!Pawn)
	{
		return;
	}
	ViewFrom(Pawn, 0.0f);   // off the globe, if Drive came before the vista streamed
	DestroyVistas();   // and un-freeze the real area streaming before the teleport

	FVector2D Target = FVector2D::ZeroVector;   // only read when bHaveTarget is true
	float Yaw = Pawn->GetActorRotation().Yaw;
	bool bHaveTarget = false;
	if (Pose)
	{
		Target = FVector2D(Pose->GetLocation());
		Yaw = (float)Pose->Rotator().Yaw;
		bHaveTarget = true;
	}
	else if (const AODRoadManager* Road = InRoad ? InRoad : FindActor<AODRoadManager>(GetWorld());
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

	// custom track (-odtrack): start at the track line's first point, facing
	// along it, whatever town was picked
	for (TActorIterator<AODRaceManager> It(GetWorld()); It; ++It)
	{
		if (It->TrackLine.Num() >= 2)
		{
			const FVector2D A = It->TrackLine[0], D = It->TrackLine[1] - A;
			Pawn->TeleportToSpawn(FVector(A.X, A.Y, 0.0f),
				FMath::RadiansToDegrees(FMath::Atan2(D.Y, D.X)));
			break;
		}
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
	LoadingPhase = 0;
	SetCursorVisible(false);
	UGameplayStatics::SetGamePaused(this, false);
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

	// --- area picker B (2026-10-21): drawn areas from data/user_areas.json,
	// all opened through the one template level as "<template>?odarea=<key>"
	// (AODGameMode::StartDrawnArea). Minimal hook; the menu UI is agent C's.
	FString UserText;
	TSharedPtr<FJsonObject> UserRoot;
	const TSharedPtr<FJsonObject>* UserAreas = nullptr;
	if (FFileHelper::LoadFileToString(UserText, *(FPaths::ProjectDir() / TEXT("../data/user_areas.json")))
		&& FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(UserText), UserRoot) && UserRoot.IsValid()
		&& UserRoot->TryGetObjectField(TEXT("areas"), UserAreas))
	{
		for (const TPair<FString, TSharedPtr<FJsonValue>>& A : (*UserAreas)->Values)
		{
			const TSharedPtr<FJsonObject>* Obj = nullptr;
			FString Name = A.Key;
			if (A.Value->TryGetObject(Obj))
			{
				(*Obj)->TryGetStringField(TEXT("name"), Name);
			}
			AreaLevels.Add(TEXT("/Game/Areas/AreaTemplate?odarea=") + A.Key);
			AreaNames.Add(Name);
			AreaKeys.Add(A.Key);
		}
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
	if (const AODWeatherController* Weather = FindActor<AODWeatherController>(GetWorld());
		Weather && WeatherIdx == 0 && VistaArea != INDEX_NONE)
	{
		Options += FString::Printf(TEXT("?odweather=%d"), (int32)Weather->State);   // StartAtTown resumes Auto from it
	}
	UE_LOG(LogTemp, Display, TEXT("ODMainMenu: Area -> %s (%s)"), *AreaLevels[AreaIdx], *Options);
	UGameplayStatics::OpenLevel(this, FName(*AreaLevels[AreaIdx]), true, Options);
}

void AODMainMenu::OpenDrawnArea(const FString& Key)
{
	// (2026-10-21) a drawn area has no level of its own: the template + ?odarea=<key>
	// (AODGameMode / AODTileSource load it). Its own spawn, so no town / track carries over.
	AreaDraw.CloseNameBox();
	TownPick.Empty();
	TrackPick.Empty();
	SavePicks();
	const FString Options = FString::Printf(TEXT("odengine=%d?oddrive=%d?odarea=%s"), EngineIdx, DriveIdx, *Key)
		+ (FindPawn() ? FString::Printf(TEXT("?odvehidx=%d"), FindPawn()->GetVehicleIndex()) : FString());
	UE_LOG(LogTemp, Display, TEXT("ODMainMenu: drawn area -> %s (%s)"), DrawnAreaLevel, *Options);
	UGameplayStatics::OpenLevel(this, FName(DrawnAreaLevel), true, Options);
}

// ---- garage-menu vistas (2026-10-05) ---------------------------------------

void AODMainMenu::LoadTracks()
{
	// {"<name>": {"area", "title", "loop", "km"}} (pipeline/build_track.py)
	Tracks.Reset();
	FString Text;
	TSharedPtr<FJsonObject> Root;
	const FString Path = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks/tracks.json");
	if (!FFileHelper::LoadFileToString(Text, *Path)
		|| !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Root) || !Root.IsValid())
	{
		return;
	}
	for (const auto& [Name, Value] : Root->Values)
	{
		const TSharedPtr<FJsonObject>* Obj = nullptr;
		if (!Value->TryGetObject(Obj))
		{
			continue;
		}
		FTrackInfo& T = Tracks.AddDefaulted_GetRef();
		T.Name = Name;
		T.Area = (*Obj)->GetStringField(TEXT("area"));
		T.Title = (*Obj)->GetStringField(TEXT("title"));
		T.bLoop = (*Obj)->GetBoolField(TEXT("loop"));
		T.Km = (*Obj)->GetNumberField(TEXT("km"));
	}
}

const AODMainMenu::FTrackInfo* AODMainMenu::PickedTrack() const
{
	return TrackPick.IsEmpty() || !AreaKeys.IsValidIndex(AreaIdx) ? nullptr
		: Tracks.FindByPredicate([this](const FTrackInfo& T) { return T.Name == TrackPick && T.Area == AreaKeys[AreaIdx]; });
}

void AODMainMenu::ApplyTrack()
{
	FString CmdTrack;   // -odtrack (QA): AODGameMode already spawned that race
	if (FParse::Value(FCommandLine::Get(), TEXT("odtrack="), CmdTrack))
	{
		return;
	}
	for (TActorIterator<AODRaceManager> It(GetWorld()); It; ++It)
	{
		if (!It->TrackName.IsEmpty())
		{
			It->Destroy();   // a fresh one reloads gates, PB and ghost
		}
	}
	// by name, not PickedTrack(): the editor's area may not be a listed one
	// (stale picks from another area were already cleared by BeginPlay/Adjust)
	if (const FTrackInfo* T = TrackPick.IsEmpty() ? nullptr
			: Tracks.FindByPredicate([this](const FTrackInfo& I) { return I.Name == TrackPick; }))
	{
		// TrackName set before BeginPlay, which loads Masks/track_<name>.bin
		AODRaceManager* Race = GetWorld()->SpawnActorDeferred<AODRaceManager>(AODRaceManager::StaticClass(), FTransform::Identity);
		Race->TrackName = T->Name;
		UGameplayStatics::FinishSpawningActor(Race, FTransform::Identity);
	}
}

void AODMainMenu::DriveTrack(const FString& Name, const AODRoadManager* InRoad)
{
	LoadTracks();
	TrackPick = Name;
	if (const FTrackInfo* T = Tracks.FindByPredicate([&](const FTrackInfo& I) { return I.Name == Name; }))
	{
		if (const int32 A = AreaKeys.IndexOfByKey(T->Area); A != INDEX_NONE)
		{
			AreaIdx = A;   // PickedTrack() matches on the picked area
		}
	}
	SavePicks();
	ApplyTrack();
	StartAt(-1, InRoad);   // the track's own start overrides the default spawn
}

void AODMainMenu::LoadMenuSpots()
{
	// {"<area>": {"car_cm": [x, y, z], "car_yaw_deg", "view_yaw_deg", "source",
	// "starts": {"<turnout>": {"car_cm", "car_yaw_deg"}}}} (pipeline/build_menu_spots.py).
	// An area without a spot is dropped from the Area row by BeginPlay.
	Spots.Reset();
	Spots.SetNum(AreaLevels.Num());
	StartPoses.Reset();
	StartPoses.SetNum(AreaLevels.Num());
	FString Text;
	TSharedPtr<FJsonObject> Root;
	const FString Path = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks/menu_spots.json");
	if (!FFileHelper::LoadFileToString(Text, *Path)
		|| !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Root) || !Root.IsValid())
	{
		return;
	}
	auto ReadPose = [](const TSharedPtr<FJsonObject>& O, FTransform& Out)
	{
		const TArray<TSharedPtr<FJsonValue>>* Car = nullptr;
		double Yaw = 0.0;
		if (!O->TryGetArrayField(TEXT("car_cm"), Car) || Car->Num() != 3 || !O->TryGetNumberField(TEXT("car_yaw_deg"), Yaw))
		{
			return false;
		}
		Out = FTransform(FRotator(0.0, Yaw, 0.0), FVector((*Car)[0]->AsNumber(), (*Car)[1]->AsNumber(), (*Car)[2]->AsNumber()));
		return true;
	};
	for (int32 i = 0; i < AreaKeys.Num(); ++i)
	{
		const TSharedPtr<FJsonObject>* Spot = nullptr;
		FTransform Car;
		double ViewYaw = 0.0;
		if (!Root->TryGetObjectField(AreaKeys[i], Spot) || !ReadPose(*Spot, Car)
			|| !(*Spot)->TryGetNumberField(TEXT("view_yaw_deg"), ViewYaw))
		{
			continue;
		}
		FODMenuSpot& S = Spots[i];
		S.bValid = true;
		S.CarCm = Car.GetLocation();
		S.CarYawDeg = (float)Car.Rotator().Yaw;
		S.ViewYawDeg = (float)ViewYaw;
		double SeaZ = 0.0;
		S.bHasSea = (*Spot)->TryGetNumberField(TEXT("sea_z_cm"), SeaZ);
		S.SeaZCm = SeaZ;
		// the Turnouts (2026-10-14); "" = the spot itself, the Default Turnout
		StartPoses[i].Add(FString(), Car);
		const TSharedPtr<FJsonObject>* Starts = nullptr;
		if ((*Spot)->TryGetObjectField(TEXT("starts"), Starts))
		{
			for (const auto& [Town, Value] : (*Starts)->Values)
			{
				const TSharedPtr<FJsonObject>* O = nullptr;
				FTransform Pose;
				if (Value->TryGetObject(O) && ReadPose(*O, Pose))
				{
					StartPoses[i].Add(FString(Town), Pose);
				}
			}
		}
	}
}

FVector AODMainMenu::VistaCarWorld() const
{
	return FVector(VistaBaseXCm, 0.0, VistaSpot.CarCm.Z);
}

FODVistaRegion AODMainMenu::VistaRegion() const
{
	const double ViewRad = FMath::DegreesToRadians((double)VistaSpot.ViewYawDeg);
	FODVistaRegion Region;
	Region.bActive = true;
	Region.Focus = VistaSpot.CarCm;
	Region.Dir = FVector2D(FMath::Cos(ViewRad), FMath::Sin(ViewRad));
	Region.BackCm = VistaCamBackCm;
	Region.FarCm = VistaFarCm;
	Region.PatchCm = VistaPatchCm;
	Region.HalfAngleRad = FMath::DegreesToRadians(VistaHalfAngleDeg);
	Region.PanoFarCm = VistaPanoFarCm;
	return Region;
}

void AODMainMenu::ShowStartVista(int32 Area)
{
	AODGameMode* GM = GetWorld()->GetAuthGameMode<AODGameMode>();
	if (!GM || !bEntered || bAutoStart || !Spots.IsValidIndex(Area) || !Spots[Area].bValid || Area == VistaArea)
	{
		return;   // none, or reuse the live set
	}
	ClearStartVista();
	// the area's one spot (2026-10-16, was each turnout's road-snapped pose):
	// build_menu_spots.py's wild viewpoint, whatever turnout Drive starts at
	VistaSpot = Spots[Area];
	VistaArea = Area;
	bVistaDirty = true;
	AreaEnterTime = GetWorld()->GetRealTimeSeconds();   // Tick's vista-stream wait cap
	AODWeatherController::LoadClimateFile(AreaKeys[Area] + TEXT("_test"), VistaSpot.Climate);
	for (AActor* A : GM->SpawnVistaSet(AreaKeys[Area], VistaCarWorld() - VistaSpot.CarCm, VistaRegion()))
	{
		VistaActors.Add(A);
		if (AODForestManager* Forest = Cast<AODForestManager>(A))
		{
			VistaSpot.Forest = Forest;
		}
	}
	if (VistaSpot.bHasSea)
	{
		const FVector Car = VistaCarWorld();
		if (AStaticMeshActor* Sea = GetWorld()->SpawnActor<AStaticMeshActor>(
				FVector(Car.X, Car.Y, VistaSpot.SeaZCm), FRotator::ZeroRotator))
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
	UE_LOG(LogTemp, Display, TEXT("ODMainMenu: area vista %s at (%.0f, %.0f, %.0f) yaw %.0f (%d actor(s))"),
		*AreaKeys[Area], VistaSpot.CarCm.X, VistaSpot.CarCm.Y, VistaSpot.CarCm.Z, VistaSpot.CarYawDeg, VistaActors.Num());
	if (!bVistasSpawned)
	{
		bVistasSpawned = true;
		GM->FreezeStreaming(true);   // the car leaves the real world while a vista shows
	}
	SetWeatherClimate(Area);   // its climate; Auto rolls once here and holds for the visit
}

void AODMainMenu::ClearStartVista()
{
	for (AActor* A : VistaActors)
	{
		if (IsValid(A))
		{
			A->Destroy();
		}
	}
	VistaActors.Reset();
	VistaSpot.Forest = nullptr;
	if (VistaArea != INDEX_NONE)
	{
		VistaArea = INDEX_NONE;
		SetWeatherClimate(INDEX_NONE);   // home climate: its forest copy is gone
	}
	bVistaDirty = true;   // UpdateScene: back to the garage room unless a new set follows
}

void AODMainMenu::DestroyVistas()
{
	ClearStartVista();
	ShownVista = INDEX_NONE;   // StartAt teleports the car anyway
	if (!bVistasSpawned)
	{
		return;
	}
	bVistasSpawned = false;
	if (AODGameMode* GM = GetWorld()->GetAuthGameMode<AODGameMode>())
	{
		GM->FreezeStreaming(false);
	}
}

bool AODMainMenu::IsStartVistaReady() const
{
	if (VistaArea == INDEX_NONE)
	{
		return false;
	}
	for (const AActor* A : VistaActors)
	{
		const AODTerrainManager* T = Cast<AODTerrainManager>(A);
		const AODRoadManager* R = Cast<AODRoadManager>(A);
		const AODForestManager* F = Cast<AODForestManager>(A);
		const AODWaterManager* W = Cast<AODWaterManager>(A);
		if ((T && !T->IsInitialStreamingSettled(VistaSpot.CarCm)) || (R && !R->IsInitialStreamingSettled())
			|| (F && !F->IsInitialStreamingSettled()) || (W && !W->IsInitialStreamingSettled()))
		{
			return false;
		}
	}
	return true;
}

FTransform AODMainMenu::VistaCamGoal() const
{
	// behind the car along the view yaw, shifted right so the car sits left of centre
	const FVector Car = VistaCarWorld();   // road surface under the car
	const FVector Fwd = FRotator(0.0, VistaSpot.ViewYawDeg, 0.0).Vector();
	const FVector Right = FRotator(0.0, VistaSpot.ViewYawDeg + 90.0, 0.0).Vector();
	return FTransform(FRotator(VistaCamPitchDeg, VistaSpot.ViewYawDeg, 0.0),
		Car - Fwd * VistaCamBackCm + Right * VistaCamSideCm + FVector(0.0, 0.0, VistaCamHeightCm));
}

void AODMainMenu::UpdateScene()
{
	if (!bEntered || bSettingsOpen)
	{
		return;
	}
	// the start vista on the Start screen (2026-10-14: AreaMap is the globe's)
	const int32 Want = Screen == EODMenuScreen::Start && VistaArea == AreaIdx ? AreaIdx : INDEX_NONE;
	if (Want == ShownVista && !bVistaDirty)
	{
		return;
	}
	AODVehiclePawn* Pawn = FindPawn();
	if (!Pawn)
	{
		return;
	}
	ShownVista = Want;
	bVistaDirty = false;
	UCameraComponent* Cam = Pawn->FindComponentByClass<UCameraComponent>();
	AODGarageEnvironment* Garage = EnsureGarageEnvironment();
	if (Want == INDEX_NONE)
	{
		// back into the garage room exactly as EnterCarConfig() framed it
		Pawn->PlaceInShowroom(ShowroomLocationCm);
		Pawn->SetShowroom(true);
		Pawn->SetActorRotation(FRotator(0.0f, ShowroomYawDeg, 0.0f));
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
	const UODVehicleMovement* Move = FindMovement();
	Pawn->PlaceInShowroom(VistaCarWorld() + FVector(0.0, 0.0, Move ? Move->GetRestGroundClearanceCm() : 0.0f));
	Pawn->SetActorRotation(FRotator(0.0f, VistaSpot.CarYawDeg, 0.0f));
	Pawn->SetShowroom(true);   // detaches the camera; placed for the vista below
	if (Cam)
	{
		Cam->SetWorldTransform(VistaCamGoal());
	}
}

void AODMainMenu::SpinShowroomCar(float DeltaDeg)
{
	AODVehiclePawn* Pawn = FindPawn();
	if (Pawn && !IsVistaShown() && Stage == EODMenuStage::Menu)
	{
		Pawn->AddActorWorldRotation(FRotator(0.0f, DeltaDeg, 0.0f));
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
	if (Screen == EODMenuScreen::NewArea)
	{
		AreaDraw.GetRow(Row, OutLabel, OutValue);
		return;
	}
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
	case RowPaint:
		OutLabel = TEXT("Paint & Wheels");
		OutValue.Empty();
		break;
	case RowColor:
	case RowFinish: case RowPattern: case RowAccent: case RowRims:
		if (const AODVehiclePawn* Pawn = FindPawn())
		{
			PaintRow(Pawn->GetPaint(), Row, OutLabel, OutValue);
		}
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
	case RowOpenDrive:
		OutLabel = TEXT("Open Drive");
		OutValue.Empty();
		break;
	case RowTrackSelect:
		OutLabel = TEXT("Track Select");
		OutValue.Empty();
		break;
	case RowDrive:
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
		OutLabel = TEXT("Back");
		OutValue.Empty();
		break;
	case RowQuit:
		OutLabel = TEXT("Quit");
		OutValue.Empty();
		break;
	default:
		OutLabel = Row >= RowPick && Picks.IsValidIndex(Row - RowPick) ? Picks[Row - RowPick].Label : FString();
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
