#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODMenuTabs.h"
#include "ODSettingsMenu.generated.h"

class AODRebindMenu;
class AODCreditsMenu;
class AODRaceHUD;
class UODVehicleMovement;

/**
 * Release settings shell (Arc 4.4, next steps.md SS4.4/SS7.31): graphics
 * (resolution/window mode/quality/upscaler/screen-percentage/vsync/asset
 * detail - see ODAssetDetail.h), audio
 * volumes (Master/Vehicle/Ambient), and the Input Device row, all as one
 * canvas-drawn row list - same NumRows()/GetRow()/Adjust() idiom as
 * AODPauseMenu/AODMainMenu (see ODRaceHUD::DrawSettingsMenu for the actual
 * drawing, generic over this row list exactly like the other two menus'
 * panels).
 *
 * Deliberately NOT an independent input-owning actor (no EnableInput/
 * BindKey here, unlike AODPauseMenu/AODMainMenu) - a second actor binding
 * the same Up/Down/Left/Right/Enter/Escape keys would race the parent
 * menu's own bindings for input-stack priority with no reliable ordering
 * guarantee. Instead this is a plain logic+state object, lazily spawned
 * (EnsureSettingsMenu() in both ODPauseMenu.cpp and ODMainMenu.cpp, same
 * lazy-spawn-on-first-use idiom as AODMainMenu::EnsureGarageEnvironment)
 * and DRIVEN by whichever parent menu is currently open: AODPauseMenu's own
 * Up()/Down()/Left()/Right()/Activate()/Toggle() (and AODMainMenu's
 * equivalents) check their own bSettingsOpen flag FIRST and route straight
 * into MoveSelection()/AdjustSelected() below instead of running their
 * normal row logic - a single function each, so there's no cross-actor
 * ordering hazard to reason about.
 *
 * Persistence: UGameUserSettings (resolution/window mode/quality/vsync)
 * persists itself natively (GameUserSettings.ini) - this class reads/writes
 * it directly rather than duplicating its state, so there's only ever one
 * source of truth for those four rows (CurrentResolutionIdx()/
 * CurrentWindowModeIdx() below scan the live GameUserSettings value rather
 * than tracking a shadow index). Upscaler and screen-percentage have no
 * UGameUserSettings equivalent at all (TSR/TAA is a console variable,
 * r.AntiAliasingMethod, not an engine scalability setting) - those two plus
 * the three audio volumes get their own GConfig round-trip into
 * GameUserSettings.ini, same "OpenDrive.<Section>" idiom as AODMainMenu::
 * Save/LoadExhaustConfig's "OpenDrive.Exhaust" (see SaveGraphicsConfig/
 * LoadGraphicsConfig -> "OpenDrive.Graphics", SaveAudioConfig/
 * LoadAudioConfig -> "OpenDrive.Audio").
 */
UCLASS()
class OPENDRIVEUE_API AODSettingsMenu : public AActor
{
	GENERATED_BODY()

public:
	AODSettingsMenu();
	virtual void BeginPlay() override;

	// DISPLAY, AUDIO, GAME (assists, mini-map, units, Credits), CONTROLS (device,
	// wheel conditioning, Key Bindings), then the Close footer - see
	// ESettingsRow in the .cpp.
	int32 NumRows() const { return 23; }
	int32 GetSelected() const { return Selected; }
	/** Up/Down: rows of the active tab + Close; forwards to the Key Bindings
	 *  sub-screen, or scrolls Credits, while one of those is open. */
	void MoveSelection(int32 Dir);
	/** Q/E: Display / Audio / Game / Controls; Close is the always-visible footer. */
	void SwitchTab(int32 Dir);
	void SetTab(int32 Tab);
	TArrayView<const FODMenuTab> GetTabs() const;
	int32 GetTab() const { return TabIdx; }
	/** Mouse hover: select a visible row (Col = Key Bindings column, -1 = keep). */
	void SelectRow(int32 Row, int32 Col = -1);
	/** Left/Right (Dir = -1/+1) on the selected row; forwards to the open sub-screen. */
	void AdjustSelected(int32 Dir);
	/** Enter: opens Key Bindings/Credits, cycles a value row, closes a
	 *  sub-screen. True = the Close row - the parent menu closes Settings. */
	bool Activate();
	/** Escape: cancels a key capture / closes a sub-screen first. True = close Settings. */
	bool Back();
	void GetRow(int32 Row, FString& OutLabel, FString& OutValue) const;

	// ---- sub-screens: Key Bindings (Arc 4.1, SS7.32) and Credits ----
	bool IsRebindOpen() const { return bRebindOpen; }
	AODRebindMenu* GetRebindMenu() const { return RebindMenu; }
	bool IsCreditsOpen() const { return bCreditsOpen; }
	AODCreditsMenu* GetCreditsMenu() const { return CreditsMenu; }

private:
	void ApplyResolution(int32 Dir);
	void ApplyWindowMode(int32 Dir);
	void ApplyQuality(int32 Dir);
	void ApplyUpscaler(int32 Dir);
	void ApplyScreenPercent(int32 Dir);
	void ApplyVSync();
	void ApplyAssetDetail(int32 Dir);
	void ApplyVisualStyle(int32 Dir);
	void ApplyAudioVolumes() const;   // pushes Master*Vehicle / Master*Ambient to the audio actors
	void ApplyInputDevice(int32 Dir);

	// Console-variable-only settings (no UGameUserSettings equivalent) -
	// applied on load and whenever adjusted; see class comment.
	void ApplyUpscalerCvars() const;

	void SaveGraphicsConfig() const;
	void LoadGraphicsConfig();
	void SaveAudioConfig() const;
	void LoadAudioConfig();

	int32 CurrentResolutionIdx() const;
	int32 CurrentWindowModeIdx() const;

	AODRebindMenu* EnsureRebindMenu();
	AODCreditsMenu* EnsureCreditsMenu();
	UODVehicleMovement* FindMovement() const;
	AODRaceHUD* FindHud() const;

	int32 Selected = 0;
	int32 TabIdx = 0;

	bool bRebindOpen = false;
	UPROPERTY()
	TObjectPtr<AODRebindMenu> RebindMenu;
	bool bCreditsOpen = false;
	UPROPERTY()
	TObjectPtr<AODCreditsMenu> CreditsMenu;

	/** 0 = Off (plain TAA), 1 = TSR. FSR isn't offered - no FidelityFX Super
	 *  Resolution plugin is enabled in OpenDriveUE.uproject (checked, see
	 *  this task's final report) - so the only real choice on stock UE 5.8
	 *  is TSR (built into the engine, no plugin needed) vs off. */
	int32 UpscalerIdx = 1;

	/** r.ScreenPercentage, 50-100 in steps of 10. Only visibly matters under
	 *  TSR (a real upscale ratio); left adjustable regardless so a player who
	 *  turns TSR on later doesn't need to also hunt down this row for the
	 *  first time then. */
	int32 ScreenPercent = 100;

	/** 0 Standard / 1 High / 2 Ultra flora, rock and car meshes - see
	 *  ODAssetDetail.h. Meshes load in BeginPlay, so a change takes effect
	 *  on the next level load; AssetDetailAtLoad is what this level actually
	 *  loaded, so the row can say so. */
	int32 AssetDetail = 0;
	int32 AssetDetailAtLoad = 0;

	/** 0 Stylized / 1 Realistic - see ODVisualStyle.h; next level load, like Asset Detail. */
	int32 VisualStyle = 0;
	int32 VisualStyleAtLoad = 0;

	float MasterVolume = 1.0f;
	float VehicleVolume = 1.0f;
	float AmbientVolume = 1.0f;
};
