#pragma once

#include "CoreMinimal.h"
#include "GameFramework/HUD.h"
#include "ODRaceHUD.generated.h"

class AODRaceManager;
class AODRoadManager;
class AODMainMenu;

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

private:
	void ToggleMiniMap() { bShowMiniMap = !bShowMiniMap; }
	void ZoomIn() { MiniMapZoomM = FMath::Max(20.0f, MiniMapZoomM * 0.8f); }
	void ZoomOut() { MiniMapZoomM = FMath::Min(800.0f, MiniMapZoomM / 0.8f); }

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
};
