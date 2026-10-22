#pragma once

#include "CoreMinimal.h"

class UTexture2D;

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

/** What the OS media session (SMTC) reports for the current player. */
struct FNowPlaying
{
	bool bValid = false;     // a media session exists
	bool bPlaying = false;
	FString Title, Artist;
	UTexture2D* Art = nullptr;   // rooted; null until the player publishes a thumbnail
	double ChangedAt = -1.0;     // FPlatformTime::Seconds() of the last track / play-state change
};
/** Latest SMTC state. Game thread only; polls on the thread pool ~1 Hz while it's being called. */
OPENDRIVEUE_API const FNowPlaying& NowPlaying();
}
