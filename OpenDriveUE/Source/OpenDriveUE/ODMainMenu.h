#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODMainMenu.generated.h"

class AODVehiclePawn;
class UODVehicleMovement;

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
	int32 NumRows() const { return 5; }        // Car, Engine, Drivetrain, Tuning, Drive
	int32 GetSelected() const { return Selected; }
	void GetRow(int32 Row, FString& OutLabel, FString& OutValue) const;

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

	// stage transitions
	void EnterCarConfig();       // showroom + first live apply (once pawn ready)
	void GoToMap();
	void StartAt(int32 TownIdx); // teleport + enter Loading (-1 = current spot)
	void ResumeFromGarage();     // garage mode's "Drive"/Escape: no teleport, just unpause

	// live application of the current selection
	void ApplyEngine();
	void ApplyDrivetrain();
	void ApplyTuning();
	void ChangeCar(int32 Dir);

	AODVehiclePawn* FindPawn() const;
	UODVehicleMovement* FindMovement() const;
	int32 NumCars() const;
	int32 NumPresets() const;
	void SetCursorVisible(bool bVisible);

	EODMenuStage Stage = EODMenuStage::CarConfig;
	bool bGarageMode = false;   // opened via OpenGarage(), not OpenMenu()
	int32 Selected = 0;
	int32 EngineIdx = 0;     // index into the Engines[] table (ODMainMenu.cpp)
	int32 DriveIdx = 0;      // index into the Layouts[] table
	int32 TuneIdx = 0;       // index into AODTuningManager::Presets
	int32 HoveredTownIdx = INDEX_NONE;

	bool bEntered = false;   // EnterCarConfig() has run (pawn was ready)

	// the pawn's real spawn point, captured once before the first (non-
	// garage) EnterCarConfig() moves it to the showroom void - StartAt()'s
	// "no town picked" fallback needs this instead of the pawn's CURRENT
	// location, which by then is the showroom, not anywhere on the real map.
	FVector CapturedSpawnLoc = FVector::ZeroVector;
	float CapturedSpawnYaw = 0.0f;
	bool bCapturedSpawn = false;

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
