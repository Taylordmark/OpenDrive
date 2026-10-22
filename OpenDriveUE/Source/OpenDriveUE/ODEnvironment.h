#pragma once

#include "CoreMinimal.h"

class UWorld;

/**
 * Fun feature #7: the time / speed / season / weather scrubber rows, shared
 * by the pause menu's Environment page and photo mode (#4). Stateless: every
 * value is read back from the live controllers (AODDayNightController,
 * AODSeasonController, AODWeatherController) through the same setters the
 * garage uses (AODMainMenu::ApplySeason/ApplyTimeOfDay/ApplyWeather), so the
 * rows always show what the world is doing and nothing persists past the
 * level (the garage owns saved defaults). The controllers tick while paused
 * with their clocks held, so a paused scrub shows at once.
 */
namespace ODEnvironment
{
enum : int32 { RowTime, RowSpeed, RowSeason, RowWeather, NumRows };

void GetRow(const UWorld* World, int32 Row, FString& OutLabel, FString& OutValue);
/** Left/Right on a row: time in 15-minute steps, the rest cycle. */
void Adjust(UWorld* World, int32 Row, int32 Dir);

/** `-odenvcheck=<s>`: at s seconds pauses, scrubs, and logs
 *  "ODEnvCheck: PASS/FAIL" - visuals must follow a paused scrub while the
 *  clocks hold. Core ticker, so it runs while paused. */
void MaybeStartSelfCheck(UWorld* World);
}
