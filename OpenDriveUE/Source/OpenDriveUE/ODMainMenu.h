#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODMenuTabs.h"
#include "ODWeatherController.h"
#include "ODMainMenu.generated.h"

class AODVehiclePawn;
class UODVehicleMovement;
class AODGarageEnvironment;
class AODSettingsMenu;
class AODForestManager;

/** One area's garage-menu car spot (2026-10-05), from
 *  Content/OpenDrive/Masks/menu_spots.json (pipeline/build_menu_spots.py). */
struct FODMenuSpot
{
	bool bValid = false;
	FVector CarCm = FVector::ZeroVector;   // area-local UE cm, Z = road surface
	float CarYawDeg = 0.0f;
	float ViewYawDeg = 0.0f;               // camera looks this way, from behind the car
	bool bHasSea = false;                  // optional "sea_z_cm": the view looks over sea-level water
	double SeaZCm = 0.0;                   // area-local sea level (= -ORIGIN_HEIGHT_M * 100)
	// per-vista weather (2026-10-05), set by SpawnVistas: the area's
	// climate_<area>_test.json and its vista forest copy (biome/elevation at the spot)
	FODClimate Climate;
	TWeakObjectPtr<AODForestManager> Forest;
};

/** Which stage of the front-end the player is on. */
UENUM()
enum class EODMenuStage : uint8
{
	Menu,        // the menu screens (EODMenuScreen)
	MapSelect,   // town-picker map; only the -odmenumap QA hook reaches it now
	Loading,     // waiting on ground snap + tile streaming after a teleport
	Playing,     // menu dismissed, normal driving
};

/** Screen within EODMenuStage::Menu. Settings is an overlay (AODSettingsMenu; Credits live inside it). */
enum class EODMenuScreen : uint8
{
	Main,     // Drive, Garage, Settings, Quit - garage-room backdrop
	Garage,   // car/engine/drivetrain/tuning/exhaust, applied live - always the garage room
	Sound,    // exhaust sliders, opened from the Garage's "Customize Sound" row - garage room
	Scene,    // Area, Time of Day, Season, Weather, Start Driving - always the picked area's vista
};

/**
 * Front-end menu, drawn on canvas by AODRaceHUD (no UMG). Sequential screens:
 * Main -> Garage (-> Sound) / Scene Select (Drive) / Settings / Quit; Esc or
 * Back returns to Main. The backdrop follows the SCREEN, never the row: Main
 * and Garage show the car in the garage room (ShowroomLocationCm), Scene
 * shows the selected area's preloaded vista (Spots, AODGameMode::SpawnVistaSet).
 *
 * Drive opens AreaLevels[AreaIdx] (OpenArea) carrying the car picks as URL
 * options; that level's menu sees odengine= and skips straight to Loading ->
 * Playing at its default spawn. Driving to the current level skips the reload.
 * Picks (car, engine, drivetrain, tuning, area, exhaust, season, time,
 * weather) persist in GameUserSettings.ini.
 *
 * Fresh boot (OpenMenu()) does not pause: APlayerCameraManager doesn't tick
 * while paused and the showroom camera would freeze. The pause menu's
 * "Garage" row calls OpenGarage(): the Garage screen only, paused, Back
 * resumes in place. Its "Main Menu" row calls OpenMenu().
 *
 * StartAt() teleports, unpauses (streaming only runs unpaused) and enters
 * Loading; Tick() releases to Playing once the pawn is ground-snapped and
 * every terrain/road manager has settled (debounced, with a failsafe).
 */
UCLASS()
class OPENDRIVEUE_API AODMainMenu : public AActor
{
	GENERATED_BODY()

public:
	AODMainMenu();
	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

	/** Fixed display angle for the car preview (deg) - static, not a spin
	 *  (Taylor, 2026-07-28: "stop the car rotating stuff too"). */
	UPROPERTY(EditAnywhere, Category = "Menu")
	float ShowroomYawDeg = 200.0f;

	/** Fixed, self-contained showroom location (2026-07-28) - see
	 *  AODVehiclePawn::PlaceInShowroom's comment for why this needs to be
	 *  somewhere no tile-streaming manager will ever load content: high
	 *  enough above the playable world that no terrain/road/building/barrier
	 *  SpawnRadius reaches it, so the preview never depends on streaming
	 *  state or a ground-snap wait. */
	UPROPERTY(EditAnywhere, Category = "Menu")
	FVector ShowroomLocationCm = FVector(0.0, 0.0, 3000000.0);

	// --- HUD-facing state ---
	bool IsActive() const { return Stage != EODMenuStage::Playing; }
	EODMenuStage GetStage() const { return Stage; }
	bool IsGarageMode() const { return bGarageMode; }
	/** Loading overlay: the Loading stage, or a Drive-launched level about to start. */
	bool IsLoading() const { return Stage == EODMenuStage::Loading || bAutoStart; }
	EODMenuScreen GetScreen() const { return Screen; }
	/** True while the outdoor area vista (not the garage room) is on screen -
	 *  the HUD moves the panel right and drops its dim. Scene Select only. */
	bool IsVistaShown() const { return ShownVista != INDEX_NONE; }
	int32 GetSelected() const { return Selected; }
	/** The current screen's tabs (global row ids, see GetRow()) and its footer row. */
	TArrayView<const FODMenuTab> GetTabs() const;
	int32 GetFooterRow() const;
	int32 GetTab() const { return TabIdx; }
	void GetRow(int32 Row, FString& OutLabel, FString& OutValue) const;

	bool IsSettingsOpen() const { return bSettingsOpen; }
	AODSettingsMenu* GetSettingsMenu() const { return SettingsMenu; }

	/** Loading-screen progress numbers (2026-08-27, spawn-loading-screen task) -
	 *  ODRaceHUD reads these while Stage == Loading to draw a "terrain N/M ·
	 *  roads N/M" line. Zero/zero for a manager that isn't present on this
	 *  level (matches AODTerrainManager/AODRoadManager's own optional-actor
	 *  convention elsewhere in this project). */
	void GetLoadingProgress(int32& OutTerrainReady, int32& OutTerrainTotal,
		int32& OutRoadReady, int32& OutRoadTotal) const;

	/** The HUD computes the town nearest the mouse each frame (it owns the
	 *  world->screen transform) and reports it here for the click handler. */
	void SetHoveredTown(int32 Index) { HoveredTownIdx = Index; }
	int32 GetHoveredTown() const { return HoveredTownIdx; }

	/** (Re)enter the menu at the Main screen - at launch and from the pause
	 *  menu's "Main Menu" row. */
	void OpenMenu();

	/** The Garage screen from the PAUSE menu's "Garage" row: the car is
	 *  already on real road, so Back / Esc resumes driving in place. */
	void OpenGarage();

	/** World-map fast travel (SS7.37): the same teleport + Loading-screen path
	 *  as picking a town on the front-end map. */
	void TravelToTown(int32 TownIdx, const class AODRoadManager* InRoad = nullptr) { StartAt(TownIdx, InRoad); }
	/** Track editor (2026-10-13): pick a just-saved track (Track row) and drive it now. */
	void DriveTrack(const FString& Name, const class AODRoadManager* InRoad);

	/** Cursor + input mode for a menu/map (also used by AODRaceHUD's world map). */
	void SetCursorVisible(bool bVisible);

	// keyboard handlers, also the HUD's mouse entry points
	void Up();
	void Down();
	void Activate();     // Enter / left click
	void Back();         // Escape / right click
	void Adjust(int32 Dir);
	void SelectRow(int32 Row, int32 Col = -1);
	void SetTab(int32 Tab);

private:
	void Left();
	void Right();
	void OnClick();      // left mouse (map select)
	void PrevTab() { SwitchTab(-1); }   // Q / left shoulder
	void NextTab() { SwitchTab(+1); }   // E / right shoulder
	void SwitchTab(int32 Dir);
	void SetScreen(EODMenuScreen NewScreen, int32 Row);   // Row = the row to select

	// stage transitions
	void EnterCarConfig();       // showroom + first live apply (once pawn ready)
	void GoToMap();
	void StartAt(int32 TownIdx, const class AODRoadManager* InRoad = nullptr); // teleport + enter Loading (-1 = level's default spawn); null road = first found
	void ResumeFromGarage();     // pause-menu garage's Back/Escape: no teleport, just unpause

	// Area row: the /Game/Areas/OpenWorld_<area> levels (AssetRegistry) that
	// also have a menu_spots.json entry (BeginPlay drops the rest). The row
	// only picks; Drive with AreaIdx != CurrentAreaIdx calls OpenArea().
	void FindAreaLevels();
	void OpenArea();             // OpenLevel(AreaLevels[AreaIdx]) carrying the car picks as URL options
	TArray<FString> AreaLevels;  // package paths, e.g. "/Game/Areas/OpenWorld_gv_west"
	TArray<FString> AreaNames;   // readable, parallel to AreaLevels
	TArray<FString> AreaKeys;    // "<area>" as in the sidecar names / menu_spots.json, parallel too
	int32 AreaIdx = 0;
	int32 CurrentAreaIdx = INDEX_NONE;   // this level's index in AreaLevels, if listed
	// Track row (2026-10-13): Free Roam or a custom track of the picked area,
	// from Masks/tracks.json (pipeline/build_track.py). Saved like Area, so a
	// Drive into another level picks it back up there.
	struct FTrackInfo { FString Name, Title, Area; bool bLoop = false; float Km = 0.0f; };
	void LoadTracks();
	void ApplyTrack();           // (re)spawn the track's AODRaceManager before StartAt, or clear it
	const FTrackInfo* PickedTrack() const;
	TArray<FTrackInfo> Tracks;
	FString TrackPick;           // track name, empty = Free Roam
	// saved / odtunepreset= tuning preset, applied once by EnterCarConfig() (needs the pawn)
	int32 CarriedTuneIdx = INDEX_NONE;
	// this level was opened by Drive (travel URL has odengine=): skip the menu,
	// straight to Loading -> Playing at the default spawn
	bool bAutoStart = false;

	// live application of the current selection
	void ApplyEngine();
	void ApplyDrivetrain();
	void ApplyTuning();
	void ApplyExhaust();     // pushes current Exhaust* fields to the synth
	void ApplySeason();      // pushes SeasonIdx to AODSeasonController (lock or Auto)
	void ApplyTimeOfDay();   // TimeIdx -> AODDayNightController::TimeOfDay
	void ApplyWeather();     // WeatherIdx -> AODWeatherController::ForceState (instant); Auto if the climate can't
	bool WeatherPossible(int32 Idx) const;   // Auto, or AODWeatherController::CanProduce
	void SetWeatherClimate(int32 Vista);     // the weather's climate -> Spots[Vista]'s (INDEX_NONE = home)
	void ChangeCar(int32 Dir);

	// persistence - GConfig into GameUserSettings.ini, same pattern as
	// AODVehiclePawn::Save/LoadInputDeviceMode. SavePicks: car (read back by
	// AODVehiclePawn::BeginPlay), engine, drivetrain, tuning preset, area.
	void SaveExhaustConfig() const;
	void LoadExhaustConfig();
	void SavePicks() const;

	AODVehiclePawn* FindPawn() const;
	UODVehicleMovement* FindMovement() const;
	int32 NumCars() const;
	int32 NumPresets() const;

	// spawned lazily on first use (see EnsureGarageEnvironment()) - not from
	// BeginPlay, to avoid racing pawn/level discovery at boot.
	AODGarageEnvironment* EnsureGarageEnvironment();
	TWeakObjectPtr<AODGarageEnvironment> GarageEnv;

	// Settings/Credits (Arc 4.4): same lazy-spawn/plain-logic-object pattern
	// as EnsureGarageEnvironment() above - see AODSettingsMenu's class
	// comment for why these aren't independent input-owning actors.
	AODSettingsMenu* EnsureSettingsMenu();
	bool bSettingsOpen = false;
	UPROPERTY()
	TObjectPtr<AODSettingsMenu> SettingsMenu;

	EODMenuStage Stage = EODMenuStage::Menu;
	EODMenuScreen Screen = EODMenuScreen::Main;
	bool bGarageMode = false;   // opened via OpenGarage(), not OpenMenu()
	int32 Selected = 0;
	int32 TabIdx = 0;
	int32 EngineIdx = 0;     // index into the Engines[] table (ODMainMenu.cpp), 0 = Stock
	int32 DriveIdx = 0;      // index into the Layouts[] table, 0 = Stock
	int32 TuneIdx = 0;       // index into AODTuningManager::Presets
	int32 HoveredTownIdx = INDEX_NONE;

	// Exhaust (Arc 5.2): ExhaustPresetIdx picks a baseline from ExhaustPresets[]
	// (ODMainMenu.cpp); the four slider fields are the CURRENT effective
	// values, seeded from the preset on pick and then independently nudgeable
	// - same relationship real cars have between "exhaust kit" and "how open
	// you actually left the valve". Persisted (see Save/LoadExhaustConfig).
	int32 ExhaustPresetIdx = 0;
	float ExhaustLoudness = 0.65f;
	float ExhaustRasp = 0.35f;
	float ExhaustMufflerCutoff = 2200.0f;
	float ExhaustPop = 0.02f;

	// Season row (SS7.38): index into SeasonNames[] (ODMainMenu.cpp), 0 = Auto
	// (the 4 h loop). Persisted beside the exhaust keys in GameUserSettings.ini.
	int32 SeasonIdx = 0;
	// Time of Day / Weather rows (2026-10-05): TimeOfDayPresets[] / WeatherNames[]
	// in the .cpp (Weather 0 = Auto). Persisted like Season; GameUserSettings.ini
	// is what carries them into the next level, same as Season/Exhaust.
	int32 TimeIdx = 1;
	int32 WeatherIdx = 0;

	// Scene Select vistas: the car parked on a real road per area, live
	// terrain behind it. Spots parallel AreaLevels; every area gets an offset
	// copy of its terrain/road/forest/water/guardrail managers
	// (AODGameMode::SpawnVistaSet), all preloaded at menu entry so the Area row
	// switches instantly.
	void LoadMenuSpots();
	void SpawnVistas();
	void DestroyVistas();
	void UpdateScene();          // per Tick in Menu: garage room, or AreaIdx's vista on the Scene screen
	FVector VistaCarWorld(int32 Area) const;
	TArray<FODMenuSpot> Spots;
	UPROPERTY()
	TArray<TObjectPtr<AActor>> VistaActors;
	bool bVistasSpawned = false;
	int32 ShownVista = INDEX_NONE;
	FTransform GarageCamXf;      // EnterCarConfig's garage framing, restored on the way back

	bool bEntered = false;   // EnterCarConfig() has run (pawn was ready)

	// the pawn's real spawn point, captured once before the first (non-
	// garage) EnterCarConfig() moves it to the showroom void - StartAt(-1)
	// needs this instead of the pawn's CURRENT location (the showroom).
	FVector CapturedSpawnLoc = FVector::ZeroVector;
	float CapturedSpawnYaw = 0.0f;
	bool bCapturedSpawn = false;

	// Loading-gate timing (2026-08-27, spawn-loading-screen task): StartAt()
	// stamps LoadingStartTime when it enters EODMenuStage::Loading. Tick()
	// requires IsGroundSnapped() AND both tile managers' IsInitialStreamingSettled()
	// to hold TRUE CONTINUOUSLY for LoadingSettleDebounceSec before releasing
	// (LoadingSettledSinceTime is the world-time the combined condition first
	// went true; reset to 0 the instant it drops again) - a debounce, not a
	// one-shot check, because a tile finishing its build can immediately kick
	// off the next ring's dispatch and re-arm PendingTiles for a frame or two.
	// LoadingMinDisplaySec is a floor under Elapsed regardless of how fast
	// settling actually finishes, so the screen never just flashes on a
	// same-town reload. LoadingFailsafeSec is the hard ceiling - released
	// regardless of settle state, with a Warning log, so a genuinely stuck
	// streaming state (missing sidecar, pathological tile) can't strand the
	// player on a black screen forever.
	double LoadingStartTime = 0.0;
	double LoadingSettledSinceTime = 0.0;
	static constexpr double LoadingSettleDebounceSec = 0.5;
	static constexpr double LoadingMinDisplaySec = 1.0;
	// 25 -> 60 (2026-09-07, Taylor's cold-boot "screen is blank, game
	// doesn't appear to be loading" report): a genuinely cold first load
	// (disk cache empty) was still at road tiles=0 well past the old 25s
	// ceiling - the failsafe dropped the player into an empty world that
	// then visibly filled in, which reads as broken, while the loading
	// screen it replaced shows animated dots + live tile counts and reads
	// as working. 60s comfortably covers the measured cold worst case
	// (buildings were still waiting on ground past 30s) while still
	// guaranteeing a stuck streaming state can't black-screen forever.
	static constexpr double LoadingFailsafeSec = 60.0;

	// headless QA (mirrors AODPauseMenu's -odmenu): -odmenumap=T jumps to the
	// map stage + screenshots; -odpick=<Town> auto-picks a town and starts;
	// -odgo=T skips car-config/map entirely and starts at the level's own
	// PlayerStart (StartAt(-1)'s "no town picked" fallback) - for screenshotting
	// an arbitrary point moved onto Start_TestArea beforehand (2026-07-30,
	// retaining-wall Phase 1 verification - build_openworld_test_level.py's
	// PlayerStart doesn't correspond to a town node, so -odpick can't reach it).
	float MenuMapAt = -1.0f;
	FString PickTown;
	bool bPickDone = false;
	float GoAt = -1.0f;
	bool bGoDone = false;
};
