#pragma once

#include "CoreMinimal.h"
#include "GameFramework/HUD.h"
#include "InputCoreTypes.h"
#include "ODRaceHUD.generated.h"

class AODRaceManager;
class AODRoadManager;
class AODMainMenu;
class AODSettingsMenu;
class AODCreditsMenu;
class AODRebindMenu;

/**
 * Canvas race HUD: timer, best, gate progress, speed — plus the stage map
 * (Arc 4.2 v2): the race centerline (checkpoint gates), smoothed via
 * Catmull-Rom subdivision, drawn north-up with the player arrow, next gate,
 * and PB ghost. Mini-map is a round, player-centered, zoomable panel
 * bottom-left (M toggles, [ / ] zoom); full map (whole-route fit, square)
 * lives on the pause screen. World frame is X=east, Y=south, so
 * world→screen is a straight scale (screen y down = south).
 *
 * Everything on this HUD except the timer/gate-progress text works with no
 * AODRaceManager on the level at all (open-world levels don't have
 * one - checkpoint races don't apply there). The map specifically falls
 * back to AODRoadManager's own route-only centerline in that case - see
 * DrawHUD's MapCenterline/MapGates. A road-name/city label (2026-07-23)
 * draws just above both map panels when an AODRoadManager with
 * PlaceLabelFilePrefix set is present - RoadMgr-only (Dragon/Race levels
 * don't have this data), see DrawHUD's PlaceLabelText.
 */
UCLASS()
class OPENDRIVEUE_API AODRaceHUD : public AHUD
{
	GENERATED_BODY()

public:
	virtual void BeginPlay() override;
	virtual void DrawHUD() override;

	/** Mini-map visibility (M key). */
	UPROPERTY(EditAnywhere, Category = "Map")
	bool bShowMiniMap = true;

	UPROPERTY(EditAnywhere, Category = "Map")
	float MiniMapSize = 230.0f;

	/** Mini-map visible radius, meters ([ / ] adjust; smaller = more zoomed). */
	UPROPERTY(EditAnywhere, Category = "Map", meta = (ClampMin = 20, ClampMax = 800))
	float MiniMapZoomM = 90.0f;

	/** Re-derives ToggleMap/ZoomMapIn/ZoomMapOut's keys from FODInputConfig
	 *  and rebuilds them - called at BeginPlay and again live by
	 *  AODRebindMenu after a rebind (Arc 4.1, SS7.32). */
	void RefreshODManagedKeyBindings();

private:
	void ToggleMiniMap() { bShowMiniMap = !bShowMiniMap; }
	void ZoomIn() { MiniMapZoomM = FMath::Max(20.0f, MiniMapZoomM * 0.8f); }
	void ZoomOut() { MiniMapZoomM = FMath::Min(800.0f, MiniMapZoomM / 0.8f); }

	TArray<FKey> LastODManagedKeys;

	/**
	 * Draw the stage map into a panel. ZoomRadiusM <= 0 fits the whole route
	 * (full map); > 0 centers on the player at that radius (mini-map).
	 * bCircular clips the panel to a filled+bordered circle (corners stay
	 * transparent, showing the 3D scene through) instead of a square tile.
	 *
	 * Centerline/Gates are passed as plain data, not an AODRaceManager&, so
	 * the map can draw from AODRoadManager's route-only copy on levels with
	 * no checkpoint race (see DrawHUD). Gates may be null (route-only, no
	 * start/finish/next-gate markers or PB ghost dot); Centerline may be
	 * null/empty if only Gates is available (falls back to a smoothed guess
	 * between them).
	 *
	 * RoadMgr (may be null) supplies the optional stylized terrain-color
	 * background (MapTexture/MapTextureCenterCm/MapTextureSpanCm, 2026-07-26)
	 * - unset on any level without unreal/apply_map_texture.py run, which
	 * just means the original flat-color background draws instead.
	 */
	void DrawStageMap(const TArray<FVector2D>* Centerline,
		const TArray<FTransform>* Gates, bool bRunning, int32 NextGate,
		float PanelX, float PanelY, float PanelSize, float BgAlpha,
		float ZoomRadiusM, bool bCircular, const AODRoadManager* RoadMgr) const;

	/** Front-end menu (AODMainMenu): the car-config panel, or the interactive
	 *  town map. Takes over the whole screen when the menu is active - drawn
	 *  instead of the in-game HUD, not on top of it. */
	void DrawMainMenu(AODMainMenu* Menu, const TArray<FVector2D>* Centerline,
		const TArray<FTransform>* Gates, AODRoadManager* RoadMgr) const;

	/** Settings/Credits panels (Arc 4.4) - drawn full-screen over whatever's
	 *  behind them (pause menu or front-end), same dim-and-panel shape as
	 *  DrawMainMenu's car-config panel above; generic over AODSettingsMenu's/
	 *  AODCreditsMenu's own row-list APIs so both AODPauseMenu and AODMainMenu
	 *  share one drawing implementation each regardless of which parent has
	 *  them open. */
	void DrawSettingsMenu(AODSettingsMenu* Menu) const;
	void DrawCreditsMenu(AODCreditsMenu* Menu) const;
	/** Arc 4.1 (SS7.32) rebind sub-screen - drawn ON TOP of the Settings
	 *  panel (not instead of it) when AODSettingsMenu::IsRebindOpen(), same
	 *  as DrawSettingsMenu/DrawCreditsMenu's "generic over whichever parent
	 *  has it open" shape. */
	void DrawRebindMenu(AODRebindMenu* Menu) const;
};
