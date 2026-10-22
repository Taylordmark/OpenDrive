#include "ODMedia.h"

#if PLATFORM_WINDOWS
#include "Windows/AllowWindowsPlatformTypes.h"
#include "Windows/WindowsHWrapper.h"
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
