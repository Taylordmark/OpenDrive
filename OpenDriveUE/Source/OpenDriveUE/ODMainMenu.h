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
class AODCreditsMenu;
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

/** Which screen of the front-end the player is on. */
UENUM()
enum class EODMenuStage : uint8
{
	CarConfig,   // pick car / engine / drivetrain / tuning (keyboard)
	MapSelect,   // hover a town on the map, click to spawn there (mouse)
	Loading,     // waiting on AODVehiclePawn::IsGroundSnapped() after a teleport
	Playing,     // menu dismissed, normal driving
};

/**
 * Real front-end menu (2026-07-25) - the step up from AODPauseMenu's in-game
 * settings shell into an actual "start the game" flow. Two stages, drawn on
 * canvas by AODRaceHUD (same no-UMG pattern as the pause menu):
 *
 *   1. Car config - arrows pick Car / Engine / Drivetrain / Tuning, applied
 *      LIVE to the player pawn so the choice is real (static showroom preview,
 *      framed to the right of the menu text - see AODVehiclePawn::SetShowroom).
 *      Enter on "Drive" advances.
 *   2. Map select - the route map with clickable town nodes; hovering
 *      highlights the nearest town, a click spawns the car on the road there
 *      (reusing the pawn's proven PM_Asphalt ground-snap).
 *
 * Fresh-boot (OpenMenu()) deliberately does NOT pause the game (2026-07-28,
 * see OpenMenu()'s own comment for the full account) - APlayerCameraManager
 * doesn't tick while paused, so the showroom camera used to freeze at
 * whatever the very first, not-yet-positioned frame looked like ("I was still
 * inside/under the car") no matter how the underlying Camera/SpringArm was
 * configured. The frozen preview pawn can't drive off regardless
 * (SetSimulatePhysics(false) already guarantees that), so pausing was never
 * actually needed for that. OpenGarage() (mid-drive reopen) still pauses -
 * it's a proven-working case this fix doesn't touch. This actor ticks even
 * when paused for THAT path (garage mode). Reopenable mid-game from the pause
 * menu's "Main Menu" row (full return to car-config -> map pick) or its
 * "Garage" row (OpenGarage() - same car-config screen, live edits apply
 * exactly the same way, but "Drive" resumes in place instead of going to
 * map-select, since the car is already sitting on real road).
 *
 * Loading screen (2026-07-26): StartAt() used to teleport then immediately
 * unpause and hand control back, but a fresh teleport target can have zero
 * tiles loaded (AODRoadManager/AODTerrainManager only stream around the
 * PLAYER'S position, and nothing ticks while paused) - the player would see
 * the car hovering, uncontrollable, for as long as TrySnapToGround's own
 * timeout took. StartAt() now unpauses immediately (so streaming can
 * actually happen) but enters EODMenuStage::Loading first; Tick() polls
 * AODVehiclePawn::IsGroundSnapped() and only flips to Playing once the car
 * has actually settled onto real ground - the HUD draws a simple loading
 * overlay for that stretch instead of the raw (possibly airborne) scene.
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
	// Car, Engine, Drivetrain, Tuning, Exhaust preset, Exhaust Loudness,
	// Exhaust Rasp, Exhaust Muffler, Exhaust Pop, Input Device, Season, Time of
	// Day, Weather, Area, Settings, Credits, Drive (5.2: exhaust preset row + 4
	// slider rows, same value-cycling idiom as the rows above them; Settings/
	// Credits added Arc 4.4, next steps.md SS4.4/SS7.31; Season SS7.38; Area
	// 2026-10-05, Time of Day/Weather 2026-10-05 between Season and Area - see
	// Adjust()/GetRow() in the .cpp).
	int32 NumRows() const { return 17; }
	/** True while the outdoor area vista (not the garage room) is on screen -
	 *  the HUD moves the panel right and drops its dim (2026-10-05). */
	bool IsVistaShown() const { return ShownVista != INDEX_NONE; }
	int32 GetSelected() const { return Selected; }
	/** Car / Sound / Options tabs over the rows above; Drive is the footer. */
	TArrayView<const FODMenuTab> GetTabs() const;
	int32 GetTab() const { return TabIdx; }
	void GetRow(int32 Row, FString& OutLabel, FString& OutValue) const;

	bool IsSettingsOpen() const { return bSettingsOpen; }
	bool IsCreditsOpen() const { return bCreditsOpen; }
	AODSettingsMenu* GetSettingsMenu() const { return SettingsMenu; }
	AODCreditsMenu* GetCreditsMenu() const { return CreditsMenu; }

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

	/** (Re)enter the menu at the car-config stage - called at launch and from
	 *  the pause menu's "Main Menu" row. Full front-end: "Drive" goes to
	 *  map-select, same as a fresh boot. */
	void OpenMenu();

	/** Enter car-config from the PAUSE menu's "Garage" row (2026-07-26) - same
	 *  screen/live-apply as OpenMenu(), but the car is already on real road,
	 *  so "Drive" (or Escape) resumes driving in place instead of going to
	 *  map-select, and there's no re-teleport/loading screen involved. */
	void OpenGarage();

	/** World-map fast travel (SS7.37): the same teleport + Loading-screen path
	 *  as picking a town on the front-end map. */
	void TravelToTown(int32 TownIdx, const class AODRoadManager* InRoad = nullptr) { StartAt(TownIdx, InRoad); }

	/** Cursor + input mode for a menu/map (also used by AODRaceHUD's world map). */
	void SetCursorVisible(bool bVisible);

private:
	// input
	void Up();
	void Down();
	void Left();
	void Right();
	void Activate();     // Enter
	void Back();         // Escape
	void OnClick();      // left mouse (map select)
	void Adjust(int32 Dir);
	void PrevTab() { SwitchTab(-1); }   // Q / left shoulder
	void NextTab() { SwitchTab(+1); }   // E / right shoulder
	void SwitchTab(int32 Dir);

	// stage transitions
	void EnterCarConfig();       // showroom + first live apply (once pawn ready)
	void GoToMap();
	void StartAt(int32 TownIdx, const class AODRoadManager* InRoad = nullptr); // teleport + enter Loading (-1 = current spot); null road = first found
	void ResumeFromGarage();     // garage mode's "Drive"/Escape: no teleport, just unpause

	// Area row (2026-10-05): AreaLevels = every /Game/Areas level (AssetRegistry,
	// build_openworld_test_level.py writes them) + TailOfTheDragon + the current
	// level if it's neither (OpenWorldTest). The row only picks; "Drive" with
	// AreaIdx != CurrentAreaIdx calls OpenArea() instead of map-select/resume.
	void FindAreaLevels();
	void OpenArea();             // OpenLevel(AreaLevels[AreaIdx]) carrying the car picks as URL options
	TArray<FString> AreaLevels;  // package paths, e.g. "/Game/Areas/OpenWorld_gv_west"
	TArray<FString> AreaNames;   // readable, parallel to AreaLevels
	TArray<FString> AreaKeys;    // "<area>" as in the sidecar names / menu_spots.json, parallel too
	int32 AreaIdx = 0;
	int32 CurrentAreaIdx = 0;
	// odtunepreset= from the travel URL, applied once by EnterCarConfig() (needs the pawn)
	int32 CarriedTuneIdx = INDEX_NONE;

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

	// persistence (Arc 5.2) - GConfig into GameUserSettings.ini, same
	// mechanism/pattern as AODVehiclePawn::Save/LoadInputDeviceMode (no
	// SaveGame object exists yet in this project; see that class's comment).
	// Tuning/Engine/Drivetrain picks do NOT persist today - only Input
	// Device and now Exhaust do; see ODMainMenu.cpp for the noted gap.
	void SaveExhaustConfig() const;
	void LoadExhaustConfig();

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
	AODCreditsMenu* EnsureCreditsMenu();
	bool bSettingsOpen = false;
	bool bCreditsOpen = false;
	UPROPERTY()
	TObjectPtr<AODSettingsMenu> SettingsMenu;
	UPROPERTY()
	TObjectPtr<AODCreditsMenu> CreditsMenu;

	EODMenuStage Stage = EODMenuStage::CarConfig;
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

	// Garage-menu vistas (2026-10-05): the car parked on a real road per area,
	// live terrain behind it. Spots parallel AreaLevels; every area with a spot
	// gets an offset copy of its terrain/road/forest/water/guardrail managers
	// (AODGameMode::SpawnVistaSet), all preloaded at menu entry so the Area row
	// switches instantly. Shown on the Season/Time/Weather/Area rows; every
	// other row keeps the garage room.
	void LoadMenuSpots();
	void SpawnVistas();
	void DestroyVistas();
	void UpdateScene();          // per Tick in CarConfig: garage room <-> AreaIdx's vista
	FVector VistaCarWorld(int32 Area) const;
	TArray<FODMenuSpot> Spots;
	UPROPERTY()
	TArray<TObjectPtr<AActor>> VistaActors;
	bool bVistasSpawned = false;
	int32 ShownVista = INDEX_NONE;
	FTransform GarageCamXf;      // EnterCarConfig's garage framing, restored on the way back
	// Drive at a spot: set by Activate() (same area) or the odspawnx/y/yaw travel
	// options (OpenArea() to another area -> auto-start there); StartAt() consumes it.
	bool bPendingSpot = false;
	FVector2D PendingSpot = FVector2D::ZeroVector;
	float PendingSpotYaw = 0.0f;

	bool bEntered = false;   // EnterCarConfig() has run (pawn was ready)

	// the pawn's real spawn point, captured once before the first (non-
	// garage) EnterCarConfig() moves it to the showroom void - StartAt()'s
	// "no town picked" fallback needs this instead of the pawn's CURRENT
	// location, which by then is the showroom, not anywhere on the real map.
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
