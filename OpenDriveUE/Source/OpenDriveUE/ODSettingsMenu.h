#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODSettingsMenu.generated.h"

/**
 * Release settings shell (Arc 4.4, next steps.md SS4.4/SS7.31): graphics
 * (resolution/window mode/quality/upscaler/screen-percentage/vsync), audio
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

	// Resolution, Window Mode, Quality, Upscaler, Screen %, VSync,
	// Master Volume, Vehicle Volume, Ambient Volume, Input Device, Close.
	int32 NumRows() const { return 11; }
	int32 GetSelected() const { return Selected; }
	void MoveSelection(int32 Dir) { Selected = (Selected + Dir + NumRows()) % NumRows(); }
	/** Left/Right (Dir = -1/+1) on the selected row; Enter also calls this
	 *  with Dir=+1 for a value row (same "Enter cycles" idiom as the other
	 *  two menus), except the terminal "Close" row - see the parent menus'
	 *  Activate() for that one special case. */
	void AdjustSelected(int32 Dir);
	void GetRow(int32 Row, FString& OutLabel, FString& OutValue) const;

private:
	void ApplyResolution(int32 Dir);
	void ApplyWindowMode(int32 Dir);
	void ApplyQuality(int32 Dir);
	void ApplyUpscaler(int32 Dir);
	void ApplyScreenPercent(int32 Dir);
	void ApplyVSync();
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

	int32 Selected = 0;

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

	float MasterVolume = 1.0f;
	float VehicleVolume = 1.0f;
	float AmbientVolume = 1.0f;
};
