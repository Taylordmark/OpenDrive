#pragma once

#include "CoreMinimal.h"
#include "GameFramework/HUD.h"
#include "InputCoreTypes.h"
#include "ODMedia.h"
#include "ODMenuTabs.h"
#include "ODRaceHUD.generated.h"

class AODRaceManager;
class AODRoadManager;
class AODMainMenu;
class AODPauseMenu;
class AODSettingsMenu;
class AODCreditsMenu;
class AODRebindMenu;

/** One clickable rect recorded while a menu draws (AODRaceHUD::MenuHits). */
struct FODMenuHit
{
	enum EKind : uint8 { Row, Prev, Next, Tab, Cell };
	EKind Kind;
	int32 Id;    // row (Tab: tab index)
	int32 Col;   // Cell: 0 keyboard / 1 gamepad, else -1
	float X0, Y0, X1, Y1;
};

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

	/** Mini-map rotates with the car (travel direction up) instead of north-up.
	 *  Pause-menu row; saved in GameUserSettings [OpenDrive.HUD]. */
	bool IsMiniMapHeadingUp() const { return bMiniMapHeadingUp; }
	void SetMiniMapHeadingUp(bool bOn);

	/** Gauges + nav distances in km/h, km, m, C. Settings row; [OpenDrive.HUD]. */
	bool IsMetric() const { return bMetric; }
	void SetMetric(bool bOn);

	/** Re-derives ToggleMap/ZoomMapIn/ZoomMapOut's keys from FODInputConfig
	 *  and rebuilds them - called at BeginPlay and again live by
	 *  AODRebindMenu after a rebind (Arc 4.1, SS7.32). */
	void RefreshODManagedKeyBindings();

	/** Full-screen world map + navigation (Arc 4.2 v3, next steps.md SS7.37).
	 *  Open = game paused + cursor shown; Close unpauses. No-op on levels
	 *  without an AODRoadManager route graph. */
	bool IsWorldMapOpen() const { return bWorldMapOpen; }
	void OpenWorldMap();
	void CloseWorldMap();

private:
	void ToggleWorldMap() { if (bWorldMapOpen) { CloseWorldMap(); } else { OpenWorldMap(); } }
	void TogglePhotoMode();   // fun #4: forwards to AODPauseMenu
	void ClearRouteKey();
	void MediaPlayPause() { ODMedia::PlayPause(); }
	void MediaNext() { ODMedia::Next(); }
	void MediaPrev() { ODMedia::Prev(); }
	AODRoadManager* FindNavRoadMgr() const;
	void SetMapCursor(bool bVisible);
	/** Polls its own input (pan/zoom/drag/pick) - see SS7.37 for controls. */
	void DrawWorldMap(AODRoadManager* RoadMgr, const TArray<FVector2D>* Centerline);
	/** Top-center next-turn + remaining-distance panel while a route is set. */
	void DrawNavPanel(const AODRoadManager* RoadMgr) const;

	bool bWorldMapOpen = false;
	FVector2D WorldMapCenter = FVector2D::ZeroVector;   // world XY, cm
	float WorldMapRadiusM = 4000.0f;                     // half the shorter screen side
	bool bWorldMapDragging = false;
	FVector2D WorldMapLastMouse = FVector2D::ZeroVector;
	float WorldMapDragPx = 0.0f;
	uint64 WorldMapOpenFrame = 0;   // ignore the key press that opened it
	double NoRouteMsgTime = -100.0;
	// Track editor (2026-10-13): T on the world map. Clicks drop waypoints,
	// routed over the roads like build_track.py; F saves + drives it.
	void RebuildEditTrack(const AODRoadManager* RoadMgr);
	bool bTrackEdit = false;
	bool bEditLoop = false;
	TArray<FVector2D> EditWaypoints;   // world XY cm, as clicked
	TArray<FVector2D> EditXY;          // routed line
	TArray<float> EditZ;
	int32 EditFailLeg = INDEX_NONE;    // waypoint whose leg has no road
	bool bEditSaveFailed = false;
	void ToggleMiniMap() { bShowMiniMap = !bShowMiniMap; }
	void ZoomIn() { MiniMapZoomM = FMath::Max(20.0f, MiniMapZoomM * 0.8f); }
	void ZoomOut() { MiniMapZoomM = FMath::Min(800.0f, MiniMapZoomM / 0.8f); }

	TArray<FKey> LastODManagedKeys;
	// heading-up by default: a turn on the map is then a turn the same way
	// on the road (saved choice still wins)
	bool bMiniMapHeadingUp = true;
	float MiniMapSpeedZoom = 1.0f;   // smoothed speed multiplier on MiniMapZoomM
	bool bMetric = false;
	double TripCm = 0.0;   // gauge trip odometer, this level

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
		float ZoomRadiusM, bool bCircular, const AODRoadManager* RoadMgr,
		const FVector2D* CenterOverride = nullptr) const;   // zoomed view center (world map)

	/** Front-end menu (AODMainMenu): the current screen's panel, the loading
	 *  overlay, or the QA town map. Takes over the whole screen when the menu is active - drawn
	 *  instead of the in-game HUD, not on top of it. DrawAreaMap is the AreaMap
	 *  screen's roads + start dots over the globe drape, DrawGlobePins the World screen's overlay (2026-10-14). */
	void DrawAreaMap(const AODMainMenu* Menu) const;
	void DrawGlobePins(const AODMainMenu* Menu) const;
	/** Area picker (2026-10-21): coverage.json outlines (+ tiles: baked, pending
	 *  hatched, failed red), the New Area draft / bake card, and its mouse. */
	void DrawCoverage(const AODMainMenu* Menu, bool bTiles) const;
	void DrawNewArea(AODMainMenu* Menu) const;
	void HandleNewAreaMouse(AODMainMenu* Menu, APlayerController* PC, const FVector2D& Mouse);
	int32 DrawnPinAt(const AODMainMenu* Menu, const FVector2D& Mouse) const;   // Coverage index of a drawn area's pin, or INDEX_NONE
	mutable FBox2D NewAreaBtn = FBox2D(ForceInit);   // World's "+ New Area", this frame
	int32 AreaDragVert = INDEX_NONE;   // pressed on this vertex ...
	int32 AreaDragMid = INDEX_NONE;    // ... or on the midpoint after this vertex (inserts one when dragged)
	FVector2D AreaPressAt = FVector2D::ZeroVector;
	bool bAreaPress = false, bAreaMoved = false;
	void DrawMainMenu(AODMainMenu* Menu, const TArray<FVector2D>* Centerline,
		const TArray<FTransform>* Gates, AODRoadManager* RoadMgr) const;

	// ---- menus (2026-10-09 redesign, see the comment above DrawMenuPanel in the .cpp)
	/** One menu column: title, tab strip (when >1 tab), the active tab's rows
	 *  (value between clickable arrows, % values as sliders) and the footer
	 *  button. bRight mirrors it to the right edge (Scene vista). Sub: a dim
	 *  line under the title. Rows past the screen height scroll (MenuScrollTop)
	 *  to keep Selected in view (2026-10-14). Returns the Y under the footer. */
	float DrawMenuPanel(const FString& Title, TArrayView<const FODMenuTab> Tabs, int32 ActiveTab,
		int32 Footer, int32 Selected, TFunctionRef<void(int32, FString&, FString&)> GetRow, bool bRight,
		const FString& Sub = FString()) const;
	/** Settings, or its Key Bindings / Credits sub-screen - drawn INSTEAD of
	 *  the parent menu's panel, never stacked on it. */
	void DrawSettingsPanel(AODSettingsMenu* Menu, bool bRight) const;
	void DrawRebindPanel(AODRebindMenu* Menu, bool bRight) const;
	void DrawCreditsPanel(AODCreditsMenu* Menu, bool bRight) const;
	/** The one dim layer plus the dark edge gradient the column sits on. */
	void DrawMenuBackdrop(float Dim, bool bRight) const;
	/** Title (+ Sub line) + tab strip; returns the Y the rows start at. */
	float DrawMenuHeader(const FString& Title, TArrayView<const FODMenuTab> Tabs, int32 ActiveTab, float X,
		const FString& Sub = FString()) const;
	void DrawMenuFooter(const FString& Label, int32 Id, bool bSel, float X, float Y, float W) const;
	void AddMenuHit(FODMenuHit::EKind Kind, int32 Id, int32 Col, float X, float Y, float W, float H) const;
	float MenuScale() const;                  // Canvas height / 1080
	float MenuX(bool bRight, float W) const;  // column left edge
	/** Hover selects, click activates / changes / switches tab, right-click
	 *  backs out, wheel moves - against this frame's MenuHits. World screen:
	 *  pin hover/pick, drag spins and wheel zooms the globe instead. */
	void HandleMenuMouse(AODMainMenu* Menu);
	void HandleMenuMouse(AODPauseMenu* Menu);

	mutable TArray<FODMenuHit> MenuHits;
	FVector2D LastMenuMouse = FVector2D::ZeroVector;
	uint64 LastMenuMouseFrame = 0;
	mutable int32 MenuScrollTop = 0;   // DrawMenuPanel: first visible row of a list too tall for the screen (2026-10-14)
	mutable int32 RebindScrollTop = 0; // DrawRebindPanel's own (2026-10-14)

	/** Loading screen (2026-10-14): opaque backdrop at once, its text fades in,
	 *  the bar eases monotonically; once AODMainMenu releases, the whole screen
	 *  fades out over the first frames of play (Veil < 1, drawn last). Real time. */
	void DrawLoadingScreen(const AODMainMenu* Menu, float Veil) const;
	mutable double LoadingShownAt = -1.0;   // real seconds the screen came up, -1 = not up
	mutable double LoadingEndedAt = -1.0;   // real seconds it was released, -1 = no fade running
	mutable float LoadingBarFrac = 0.0f;
	mutable int32 LoadingLastPhase = 0;
	bool bGlobeDrag = false;   // World screen: a mouse drag is spinning the globe

	/** Paint screen (4.3): saturation/brightness square + hue strip beside the
	 *  panel; click or drag sets the player car's colour, saved on release. */
	void DrawColorPicker(float X, float Y) const;
	void HandlePickerMouse();
	mutable FBox2D PickerSV = FBox2D(ForceInit), PickerHue = FBox2D(ForceInit);   // this frame's, invalid = not drawn
	uint8 PickerDrag = 0;   // 1 square, 2 hue strip
};
