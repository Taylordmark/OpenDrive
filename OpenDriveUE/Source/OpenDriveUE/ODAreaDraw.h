#pragma once

#include "CoreMinimal.h"
#include "ODMenuTabs.h"

class SWidget;

/**
 * Lon/lat geometry for the New Area screen (2026-10-21, area picker Phase 2,
 * docs/area-picker-contract.md). Pure: no engine state; LL = (lon, lat)
 * degrees throughout, as in the contract. Areas are <= 30 km, so a local
 * equirectangular km plane around the shape is plenty. One automation test,
 * OpenDrive.AreaDraw.Geo, checks it.
 */
namespace ODAreaGeo
{
constexpr double CorridorHalfKm = 1.0;   // the request's corridor_half_width_m / 1000
constexpr int32 MaxVerts = 64;
constexpr double MinSideKm = 1.0, MaxSideKm = 30.0;

double Km2(const TArray<FVector2D>& Poly);                          // polygon area
FVector2D SidesKm(const TArray<FVector2D>& LL, double PadKm = 0.0);  // bbox E-W, N-S (+ PadKm each side)
bool InUS(const FVector2D& LL);                                      // CONUS, Alaska or Hawaii box
bool Inside(const FVector2D& P, const TArray<FVector2D>& Poly);
bool SelfIntersects(const TArray<FVector2D>& Poly);                  // closed ring, non-adjacent edges
double DistKm(const FVector2D& P, const TArray<FVector2D>& Line);    // to an open polyline
double CorridorKm2(const TArray<FVector2D>& Line, double HalfKm = CorridorHalfKm);
/** The contract's live estimate; the runner uses the same numbers. */
struct FEstimate { double Km2 = 0.0, FirstDriveS = 0.0, DoneS = 0.0, DiskMb = 0.0; };
FEstimate Estimate(double Km2);
/** Limits (1-30 km sides, <= 64 verts, US only, simple polygon): "" = OK, else what to fix. */
FString Check(const TArray<FVector2D>& LL, bool bCorridor);
/** Zone metres (east, north) -> LL; Zone = user_areas.json "worlds" entry [lat1, lat2, lat0, lon0]
 *  (pipeline/world_frame.py lcc_inverse, WGS84). */
FVector2D LccInverse(double East, double North, const double Zone[4]);
}

/**
 * New Area screen state (2026-10-21), owned by AODMainMenu, drawn by
 * AODRaceHUD over the globe. Two modes:
 *  - Draft (JobKey empty): a polygon or road corridor being drawn, its start
 *    and name. Kept in GameUserSettings [OpenDrive.AreaDraft] on every edit,
 *    so it survives leaving the menu. Bake writes the request
 *    (data/bake/requests/<key>.json) and starts
 *    `python pipeline/bake_request.py --run <request>` detached.
 *  - Card (JobKey set): one drawn area's progress from data/bake/<key>/status.json,
 *    its pass-1 roads and towns, Cancel / Retry / Delete, Drive.
 * Coverage (Masks/coverage.json) is loaded here for every screen that draws it.
 * Files are re-read only when their timestamp moves (Poll, 1 Hz).
 */
class FODAreaDraw
{
public:
	// ---- draft
	TArray<FVector2D> Verts;   // LL; polygon ring (no repeat) or corridor polyline
	bool bClosed = false;      // polygon closed / corridor finished: clicks now place the start
	bool bCorridor = false;
	bool bHasStart = false;
	FVector2D Start = FVector2D::ZeroVector;
	FString Name;              // empty = the runner's auto-name
	void Load();
	void Save() const;
	void Push();               // undo snapshot, before an edit
	void Undo();
	/** A map click (not on a vertex): add a vertex, close on a double click, or place the start. */
	void Click(const FVector2D& LL, const FVector2D& ScreenPos, double Now);
	/** A click on vertex I (not dragged): the first closes a polygon, a double click on the last finishes it. */
	void ClickVertex(int32 I, const FVector2D& ScreenPos, double Now);
	void DeleteVertex(int32 I);
	bool CanEdit() const { return JobKey.IsEmpty(); }
	/** Draft validity: "" = ready to bake. */
	FString Problem() const;

	// ---- card
	FString JobKey;
	struct FStatus
	{
		bool bValid = false;
		FString Name, State, Stage, Error;
		bool bPass1Done = false, bReady = false;
		bool bSnapped = false;
		FVector2D SnappedLL = FVector2D::ZeroVector;
		int32 Cells = 0, Baked = 0, Failed = 0;
		double EtaFirstS = 0.0, EtaDoneS = 0.0;
	} Status;
	void OpenCard(const FString& Key);
	TArray<FVector2D> RoadsLL;         // the job's pass-1 centerline_map, NaN = break
	TArray<TArray<FVector2D>> Holes;   // status cells marked baked with no tile on disk (round 9)
	TArray<FString> TownNames;         // osm_places inside the outline, after pass 1
	TArray<FVector2D> TownLL;
	const TArray<FVector2D>* JobOutline() const;   // coverage / user_areas polygon, null if unknown

	// ---- coverage (Masks/coverage.json) + drawn areas (data/user_areas.json)
	struct FCovArea
	{
		FString Key, Name;
		bool bDrawn = false;
		TArray<FVector2D> Outline;
		TArray<TArray<FVector2D>> Tiles;   // 4 LL corners each
		TArray<uint8> TileState;           // 0 baked, 1 pending, 2 failed
		double BakedKm2 = 0.0, TotalKm2 = 0.0;
		FVector2D Centre = FVector2D::ZeroVector;
	};
	TArray<FCovArea> Coverage;
	/** Re-read whatever changed on disk; throttled to 1 Hz (real seconds). */
	void Poll(double Now);

	// ---- panel rows (AODMainMenu routes its row calls here on this screen)
	TArrayView<const FODMenuTab> GetTabs() const;
	int32 Footer() const;
	void GetRow(int32 Row, FString& OutLabel, FString& OutValue) const;
	void Adjust(int32 Row, int32 Dir);
	/** Enter / click. Returns the key to drive (OpenArea ?odarea=) or empty. */
	FString Activate(int32 Row);
	/** Lines under the panel: the live estimate (draft) or the progress (card). Kind 0 text, 1 dim, 2 problem. */
	struct FLine { FString Text; uint8 Kind = 0; };
	void Readout(TArray<FLine>& Out) const;
	FString Message;           // last action's note, shown in the readout
	FString Title() const;     // panel title: "New Area", or the card's area name
	void CloseNameBox();

private:
	struct FSnap
	{
		TArray<FVector2D> Verts;
		bool bClosed, bCorridor, bHasStart;
		FVector2D Start;
	};
	TArray<FSnap> UndoStack;
	double LastClickT = -1.0;
	FVector2D LastClickPos = FVector2D::ZeroVector;
	bool bBakeArmed = false, bDeleteArmed = false;
	int32 StartChoice = 0;     // card: 0 = the snapped road start, 1.. = TownNames
	double NextPoll = 0.0;
	FDateTime CoverageStamp, StatusStamp, RoadsStamp, UserAreasStamp;
	TMap<FString, TArray<double>> Zones;            // user_areas "worlds"
	TMap<FString, FString> AreaZone;                // drawn key -> zone
	TMap<FString, TArray<FVector2D>> DrawnPolys;    // drawn key -> polygon_ll
	TMap<FString, FString> DrawnNames;
	TMap<FString, FString> CellState;               // the job's status "cells", for FindHoles
	void LoadCoverage();
	void LoadUserAreas();
	TSharedPtr<SWidget> NameBox;
	void OpenNameBox();
	void LoadStatus();
	void LoadRoads();
	void LoadTowns();
	void FindHoles();
	bool Send(const FVector2D& StartLL, const FString& Key);   // request + runner
	static bool RunRunner(const FString& Args);
};
