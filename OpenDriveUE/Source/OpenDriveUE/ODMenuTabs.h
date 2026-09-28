#pragma once

#include "CoreMinimal.h"

/** One tab of a canvas row-list menu: rows [First, First + Count). */
struct FODMenuTab
{
	const TCHAR* Name;
	int32 First;
	int32 Count;
};

/**
 * Tabbed row lists (2026-09-28) shared by AODMainMenu and AODSettingsMenu:
 * only the active tab's rows plus one always-visible footer row (Drive /
 * Close) are shown, so the panel can't grow off-screen as rows get added.
 * Row indices stay global, so each menu's GetRow()/Adjust() switch is
 * untouched - tabs only change which rows are visible and reachable.
 */
namespace ODMenuTabs
{
// Up/Down: cycle through the tab's rows, then the footer.
constexpr int32 Step(const FODMenuTab& Tab, int32 Footer, int32 Selected, int32 Dir)
{
	const int32 N = Tab.Count + 1;
	int32 Pos = Selected == Footer ? Tab.Count : FMath::Clamp(Selected - Tab.First, 0, Tab.Count - 1);
	Pos = (Pos + Dir + N) % N;
	return Pos == Tab.Count ? Footer : Tab.First + Pos;
}

// Tab rows 4..8, footer 13: up from the first row wraps to the footer, down
// from the footer returns to the first row, down from the last row hits the footer.
static_assert(Step({TEXT("T"), 4, 5}, 13, 4, -1) == 13);
static_assert(Step({TEXT("T"), 4, 5}, 13, 13, +1) == 4);
static_assert(Step({TEXT("T"), 4, 5}, 13, 8, +1) == 13);
static_assert(Step({TEXT("T"), 4, 5}, 13, 5, +1) == 6);
}
