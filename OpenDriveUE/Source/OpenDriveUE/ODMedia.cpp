#include "ODMedia.h"

#include "Async/Async.h"
#include "Engine/Texture2D.h"
#include "HAL/PlatformTime.h"
#include "ImageUtils.h"

#if PLATFORM_WINDOWS
#include "Windows/AllowWindowsPlatformTypes.h"
#include "Windows/WindowsHWrapper.h"
THIRD_PARTY_INCLUDES_START
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Media.Control.h>
#include <winrt/Windows.Storage.Streams.h>
THIRD_PARTY_INCLUDES_END
#include "Windows/HideWindowsPlatformTypes.h"
#endif

namespace
{
void TapKey(uint16 Vk)
{
#if PLATFORM_WINDOWS
	INPUT In[2] = {};
	In[0].type = INPUT_KEYBOARD;
	In[0].ki.wVk = Vk;
	In[0].ki.dwFlags = KEYEVENTF_EXTENDEDKEY;
	In[1] = In[0];
	In[1].ki.dwFlags |= KEYEVENTF_KEYUP;
	SendInput(2, In, sizeof(INPUT));
#endif
}

struct FPoll
{
	bool bValid = false;
	bool bPlaying = false;
	FString Title, Artist;
	TArray<uint8> Art;   // encoded image (png/jpg), empty = not fetched
};

ODMedia::FNowPlaying State;
double LastPollAt = -1.0e9;
bool bPollInFlight = false;

// Thread pool. Blocking .get() is fine off the game thread (it only asserts
// on an STA); C++/WinRT initialises the MTA itself on first activation.
FPoll QuerySmtc(const FString& PrevTitle, const FString& PrevArtist, bool bNeedArt)
{
	FPoll R;
#if PLATFORM_WINDOWS
	using namespace winrt::Windows::Media::Control;
	using namespace winrt::Windows::Storage::Streams;
	try
	{
		// ponytail: re-requests the manager every poll (~1 ms); cache it +
		// subscribe to CurrentSessionChanged if 1 Hz ever lags a track change
		const auto Session = GlobalSystemMediaTransportControlsSessionManager::RequestAsync().get().GetCurrentSession();
		if (!Session)
		{
			return R;
		}
		const auto Props = Session.TryGetMediaPropertiesAsync().get();
		R.bValid = true;
		R.Title = Props.Title().c_str();
		R.Artist = Props.Artist().c_str();
		R.bPlaying = Session.GetPlaybackInfo().PlaybackStatus()
			== GlobalSystemMediaTransportControlsSessionPlaybackStatus::Playing;
		// players often publish the thumbnail a beat after the title, so keep
		// asking until we have one for this track
		if ((bNeedArt || R.Title != PrevTitle || R.Artist != PrevArtist) && Props.Thumbnail())
		{
			const auto Stream = Props.Thumbnail().OpenReadAsync().get();
			const uint32 Size = static_cast<uint32>(Stream.Size());
			if (Size > 0 && Size < 16u * 1024 * 1024)
			{
				const DataReader Reader(Stream);
				Reader.LoadAsync(Size).get();
				R.Art.SetNumUninitialized(Size);
				Reader.ReadBytes(winrt::array_view<uint8_t>(R.Art.GetData(), Size));
			}
		}
	}
	catch (...)
	{
		R = FPoll();   // no session manager (old Windows / server SKU) or the player went away mid-query
	}
#endif
	return R;
}

void Apply(FPoll&& R)
{
	const bool bTrack = R.Title != State.Title || R.Artist != State.Artist;
	if (bTrack || R.bPlaying != State.bPlaying || R.bValid != State.bValid)
	{
		State.ChangedAt = FPlatformTime::Seconds();
	}
	UTexture2D* NewArt = R.Art.Num() > 0 ? FImageUtils::ImportBufferAsTexture2D(R.Art) : nullptr;
	if (NewArt || bTrack)
	{
		if (State.Art)
		{
			State.Art->RemoveFromRoot();
		}
		State.Art = NewArt;
		if (NewArt)
		{
			NewArt->AddToRoot();   // not TStrongObjectPtr: a static one would release after UObject shutdown
		}
	}
	State.bValid = R.bValid;
	State.bPlaying = R.bPlaying;
	State.Title = MoveTemp(R.Title);
	State.Artist = MoveTemp(R.Artist);
}
}

#if PLATFORM_WINDOWS
void ODMedia::PlayPause() { TapKey(VK_MEDIA_PLAY_PAUSE); }
void ODMedia::Next() { TapKey(VK_MEDIA_NEXT_TRACK); }
void ODMedia::Prev() { TapKey(VK_MEDIA_PREV_TRACK); }
#else
void ODMedia::PlayPause() {}
void ODMedia::Next() {}
void ODMedia::Prev() {}
#endif

const ODMedia::FNowPlaying& ODMedia::NowPlaying()
{
	check(IsInGameThread());
	const double Now = FPlatformTime::Seconds();
	if (PLATFORM_WINDOWS && !bPollInFlight && Now - LastPollAt >= 1.0)
	{
		LastPollAt = Now;
		bPollInFlight = true;
		Async(EAsyncExecution::ThreadPool, [Title = State.Title, Artist = State.Artist, bNeedArt = State.Art == nullptr]()
		{
			FPoll R = QuerySmtc(Title, Artist, bNeedArt);
			AsyncTask(ENamedThreads::GameThread, [R = MoveTemp(R)]() mutable
			{
				Apply(MoveTemp(R));
				bPollInFlight = false;
			});
		});
	}
	return State;
}
