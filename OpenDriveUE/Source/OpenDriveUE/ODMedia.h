#pragma once

#include "CoreMinimal.h"

/**
 * Music controls (fun-features #1). Taps the Windows media keys so whichever
 * player owns the system media session (Spotify, a browser, Apple Music...)
 * plays/pauses/skips - no per-player API.
 */
namespace ODMedia
{
OPENDRIVEUE_API void PlayPause();
OPENDRIVEUE_API void Next();
OPENDRIVEUE_API void Prev();
}
