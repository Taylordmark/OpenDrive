#pragma once

#include "CoreMinimal.h"

/** The game's side of the area-picker bake (2026-10-21, area picker piece B;
 *  formats: docs/area-picker-contract.md). Everything the runtime needs to
 *  play a drawn area while the background runner is still baking it:
 *
 *  - data/user_areas.json: a drawn area's outline, start and terrain tile
 *    size, projected into UE cm with the zone's LCC (pipeline/world_frame.py).
 *  - data/bake/<key>/status.json: which 1 km cells are baked. A tile whose
 *    cells are still pending/running resolves as Pending (ODTileSource), so
 *    managers retry instead of marking it empty forever.
 *  - data/bake/car.json: ~1 Hz heartbeat (position, mode, target, quit
 *    behaviour) the runner prioritises its work by.
 *  - the "ready to drive" toast + taskbar flash when a bake's
 *    ready_to_drive flips true.
 *
 *  Hand areas never have a user_areas.json entry or a status.json, so none
 *  of this changes them. */
namespace ODBakeLink
{
	constexpr int64 CellCm = 100000;   // status.json cell_m = 1000

	/** GameUserSettings.ini [OpenDrive.Bake] BakeQuitBehaviour =
	 *  stop_after_job (default) | keep_baking - written by the menu. */
	extern const TCHAR* const ConfigSection;
	extern const TCHAR* const QuitBehaviourKey;

	struct FUserArea
	{
		FString Key, Name, World;
		TArray<FVector2D> OutlineCm;   // UE cm (X east, Y = -north)
		FVector2D StartCm = FVector2D::ZeroVector;
		bool bHasStart = false;
		float TerrainTileCm = 200000.0f;
	};

	/** Module startup, game thread: the heartbeat + ready-watcher ticker. */
	void Init();

	/** Repo root: FPaths::ProjectDir() / "..". */
	FString RepoRoot();

	/** Game thread: reads data/user_areas.json. False when Key isn't a drawn area. */
	bool LoadUserArea(const FString& Key, FUserArea& Out);

	/** Game thread: the drawn area being driven ("" = a hand area / none).
	 *  Sets the outline every boundary effect reads. */
	void SetActiveArea(const FUserArea* Area);
	FString ActiveKey();
	/** Any thread: the active outline (UE cm), null when none. */
	TSharedPtr<const TArray<FVector2D>, ESPMode::ThreadSafe> ActiveOutline();

	/** Signed distance (cm) from P to a closed outline: + inside, - outside. */
	double SignedDistanceCm(const TArray<FVector2D>& Outline, const FVector2D& P);

	/** Any thread: true when ContentPath is a "<prefix>_tile_<x>_<y>.bin"
	 *  of a drawn area whose bake is running and any cell under the tile is
	 *  still pending/running. Reads status.json at most once a second. */
	bool IsTilePending(const FString& ContentPath);

	/** Any thread: the active area's cell under P (UE cm) is not baked yet. */
	bool IsPointPending(const FVector2D& P);

	/** Bumps whenever any status.json the game has read changes - cheap
	 *  "re-check your pending tiles" signal for the terrain horizon retag. */
	uint32 StatusVersion();

	/** Any thread: start_snapped_ll of the active area's status, in UE cm. */
	bool SnappedStartCm(FVector2D& Out);

	/** car.json target_cm: where the car is waiting for tiles (null = none). */
	void SetTarget(const FVector2D* TargetCm);

	/** Cells (inclusive ranges) a tile of TileCm at (Tx, Ty) covers. Cells are
	 *  i = floor(east_m / 1000), j = floor(north_m / 1000); UE Y = -north. */
	struct FCellRange { int64 I0, I1, J0, J1; };
	constexpr int64 FloorDiv(int64 A, int64 B) { return A >= 0 ? A / B : -((-A + B - 1) / B); }
	constexpr int64 CeilDiv(int64 A, int64 B) { return -FloorDiv(-A, B); }
	constexpr FCellRange TileCells(int64 Tx, int64 Ty, int64 TileCm)
	{
		return { FloorDiv(Tx * TileCm, CellCm), CeilDiv((Tx + 1) * TileCm, CellCm) - 1,
		         FloorDiv(-(Ty + 1) * TileCm, CellCm), CeilDiv(-Ty * TileCm, CellCm) - 1 };
	}
}
