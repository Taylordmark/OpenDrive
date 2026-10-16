#include "ODWheelDevice.h"
#include "ODWheel.h"

#if PLATFORM_WINDOWS

#include "Containers/Ticker.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Misc/CoreDelegates.h"
#include "Widgets/SWindow.h"
#include "Windows/AllowWindowsPlatformTypes.h"
#define DIRECTINPUT_VERSION 0x0800
#include <dinput.h>
#include "Windows/HideWindowsPlatformTypes.h"

DEFINE_LOG_CATEGORY_STATIC(LogODWheel, Log, All);

namespace
{
	constexpr int32 NumAxes = 8;
	constexpr int32 NumButtons = 128;

	struct FDev
	{
		IDirectInputDevice8W* D = nullptr;
		uint32 VidPid = 0;
		bool bDriving = false;
		IDirectInputEffect* Const = nullptr;
		IDirectInputEffect* Damper = nullptr;
		IDirectInputEffect* Spring = nullptr;
		float Axes[NumAxes] = {};
		float Base[NumAxes] = {};     // capture baseline
		float PrevAxes[NumAxes] = {};
		bool Btn[NumButtons] = {};
		bool PrevBtn[NumButtons] = {};
		int32 Fails = 0;     // consecutive failed reads; ~2 s of them = unplugged
		bool bPolled = false;
	};

	IDirectInput8W* GDI = nullptr;
	TArray<FDev> GDevs;
	int32 GPrimary = INDEX_NONE;
	bool GStarted = false;
	double GNextEnum = 0.0;
	double GLastForceCall = -1.0;
	double GLastActivity = 0.0;
	bool GForceZeroed = true;
	float GLastDamping = -1.0f, GLastSoftLock = -1.0f;

	bool IsSimVid(uint32 Vid)
	{
		// Logitech, Fanatec, Thrustmaster, Moza, Simucube
		return Vid == 0x046D || Vid == 0x0EB7 || Vid == 0x044F || Vid == 0x346E || Vid == 0x16D0;
	}

	void ReleaseEffect(IDirectInputEffect*& E)
	{
		if (E)
		{
			E->Stop();
			E->Release();
			E = nullptr;
		}
	}

	void ReleaseAll()
	{
		for (FDev& Dv : GDevs)
		{
			ReleaseEffect(Dv.Const);
			ReleaseEffect(Dv.Damper);
			ReleaseEffect(Dv.Spring);
			if (Dv.D)
			{
				Dv.D->Unacquire();
				Dv.D->Release();
			}
		}
		GDevs.Reset();
		GPrimary = INDEX_NONE;
		GLastDamping = GLastSoftLock = -1.0f;
	}

	HWND GameHwnd()
	{
		if (GEngine && GEngine->GameViewport)
		{
			const TSharedPtr<SWindow> W = GEngine->GameViewport->GetWindow();
			if (W.IsValid() && W->GetNativeWindow().IsValid())
			{
				return static_cast<HWND>(W->GetNativeWindow()->GetOSWindowHandle());
			}
		}
		return nullptr;
	}

	IDirectInputEffect* MakeEffect(IDirectInputDevice8W* D, REFGUID Type, void* Params, DWORD ParamSize)
	{
		DWORD AxesOfs[1] = {DIJOFS_X};
		LONG Dir[1] = {0};
		DIEFFECT E = {};
		E.dwSize = sizeof(DIEFFECT);
		E.dwFlags = DIEFF_CARTESIAN | DIEFF_OBJECTOFFSETS;
		E.dwDuration = INFINITE;
		E.dwGain = DI_FFNOMINALMAX;
		E.dwTriggerButton = DIEB_NOTRIGGER;
		E.cAxes = 1;
		E.rgdwAxes = AxesOfs;
		E.rglDirection = Dir;
		E.cbTypeSpecificParams = ParamSize;
		E.lpvTypeSpecificParams = Params;
		IDirectInputEffect* Out = nullptr;
		if (FAILED(D->CreateEffect(Type, &E, &Out, nullptr)))
		{
			return nullptr;
		}
		Out->Start(1, 0);
		return Out;
	}

	BOOL CALLBACK EnumCb(LPCDIDEVICEINSTANCEW Inst, LPVOID Hwnd)
	{
		const uint32 Type = GET_DIDEVICE_TYPE(Inst->dwDevType);
		const uint32 Vid = LOWORD(Inst->guidProduct.Data1), Pid = HIWORD(Inst->guidProduct.Data1);
		const bool bKnownType = Type == DI8DEVTYPE_DRIVING || Type == DI8DEVTYPE_SUPPLEMENTAL
			|| Type == DI8DEVTYPE_JOYSTICK;
		if (!bKnownType && !IsSimVid(Vid))
		{
			return DIENUM_CONTINUE;   // gamepads go through the engine's XInput path
		}
		FDev Dv;
		Dv.VidPid = (Vid << 16) | Pid;
		Dv.bDriving = Type == DI8DEVTYPE_DRIVING;
		if (FAILED(GDI->CreateDevice(Inst->guidInstance, &Dv.D, nullptr)))
		{
			return DIENUM_CONTINUE;
		}
		DIPROPGUIDANDPATH Path = {};
		Path.diph.dwSize = sizeof(Path);
		Path.diph.dwHeaderSize = sizeof(DIPROPHEADER);
		Path.diph.dwHow = DIPH_DEVICE;
		if (!IsSimVid(Vid) && SUCCEEDED(Dv.D->GetProperty(DIPROP_GUIDANDPATH, &Path.diph))
			&& FCString::Strifind(Path.wszPath, TEXT("IG_")))
		{
			Dv.D->Release();   // an XInput pad seen through DirectInput
			return DIENUM_CONTINUE;
		}
		DIDEVCAPS Caps = {};
		Caps.dwSize = sizeof(Caps);
		Dv.D->GetCapabilities(&Caps);
		const bool bFfb = (Caps.dwFlags & DIDC_FORCEFEEDBACK) != 0;
		Dv.D->SetDataFormat(&c_dfDIJoystick2);
		// force feedback needs exclusive access; background keeps the wheel
		// alive while the window is unfocused (the watchdog zeroes the force)
		if (FAILED(Dv.D->SetCooperativeLevel(static_cast<HWND>(Hwnd),
			(bFfb ? DISCL_EXCLUSIVE : DISCL_NONEXCLUSIVE) | DISCL_BACKGROUND)))
		{
			Dv.D->SetCooperativeLevel(static_cast<HWND>(Hwnd), DISCL_NONEXCLUSIVE | DISCL_BACKGROUND);
		}
		DIPROPRANGE Range = {};
		Range.diph.dwSize = sizeof(Range);
		Range.diph.dwHeaderSize = sizeof(DIPROPHEADER);
		Range.diph.dwHow = DIPH_DEVICE;
		Range.lMin = -10000;
		Range.lMax = 10000;
		Dv.D->SetProperty(DIPROP_RANGE, &Range.diph);
		if (bFfb)
		{
			DIPROPDWORD Auto = {};
			Auto.diph.dwSize = sizeof(Auto);
			Auto.diph.dwHeaderSize = sizeof(DIPROPHEADER);
			Auto.diph.dwHow = DIPH_DEVICE;
			Auto.dwData = DIPROPAUTOCENTER_OFF;
			Dv.D->SetProperty(DIPROP_AUTOCENTER, &Auto.diph);
		}
		Dv.D->Acquire();
		if (bFfb)
		{
			DICONSTANTFORCE Cf = {0};
			Dv.Const = MakeEffect(Dv.D, GUID_ConstantForce, &Cf, sizeof(Cf));
			DICONDITION Cond = {};
			Dv.Damper = MakeEffect(Dv.D, GUID_Damper, &Cond, sizeof(Cond));
			Dv.Spring = MakeEffect(Dv.D, GUID_Spring, &Cond, sizeof(Cond));
		}
		UE_LOG(LogODWheel, Log, TEXT("wheel device %s '%s' type %u ffb %d"), *FODRacingWheel::DeviceKey(Dv.VidPid),
			Inst->tszProductName, Type, bFfb ? 1 : 0);
		GDevs.Add(Dv);
		return DIENUM_CONTINUE;
	}

	void Enumerate()
	{
		const HWND Hwnd = GameHwnd();
		if (!Hwnd)
		{
			return;
		}
		if (!GDI && FAILED(DirectInput8Create(GetModuleHandleW(nullptr), DIRECTINPUT_VERSION, IID_IDirectInput8W,
			reinterpret_cast<void**>(&GDI), nullptr)))
		{
			GDI = nullptr;
			return;
		}
		ReleaseAll();
		GDI->EnumDevices(DI8DEVCLASS_GAMECTRL, EnumCb, Hwnd, DIEDFL_ATTACHEDONLY);
		for (int32 i = 0; i < GDevs.Num() && GPrimary == INDEX_NONE; ++i)
		{
			GPrimary = GDevs[i].Const ? i : INDEX_NONE;
		}
		for (int32 i = 0; i < GDevs.Num() && GPrimary == INDEX_NONE; ++i)
		{
			GPrimary = GDevs[i].bDriving ? i : INDEX_NONE;
		}
	}

	void PushForce(float Command)
	{
		if (GPrimary == INDEX_NONE || !GDevs[GPrimary].Const)
		{
			return;
		}
		DICONSTANTFORCE Cf = {FMath::RoundToInt(FMath::Clamp(Command, -1.0f, 1.0f) * DI_FFNOMINALMAX)};
		DIEFFECT E = {};
		E.dwSize = sizeof(DIEFFECT);
		E.cbTypeSpecificParams = sizeof(Cf);
		E.lpvTypeSpecificParams = &Cf;
		GDevs[GPrimary].Const->SetParameters(&E, DIEP_TYPESPECIFICPARAMS);
	}

	void PushCondition(IDirectInputEffect* Eff, LONG Coeff, LONG DeadBand)
	{
		if (!Eff)
		{
			return;
		}
		DICONDITION C = {};
		C.lPositiveCoefficient = C.lNegativeCoefficient = Coeff;
		C.dwPositiveSaturation = C.dwNegativeSaturation = DI_FFNOMINALMAX;
		C.lDeadBand = DeadBand;
		DIEFFECT E = {};
		E.dwSize = sizeof(DIEFFECT);
		E.cbTypeSpecificParams = sizeof(C);
		E.lpvTypeSpecificParams = &C;
		Eff->SetParameters(&E, DIEP_TYPESPECIFICPARAMS);
	}

	bool Tick(float)
	{
		const double Now = FPlatformTime::Seconds();
		bool bAnyLost = false;
		for (FDev& Dv : GDevs)
		{
			DIJOYSTATE2 St = {};
			HRESULT Hr = Dv.D->Poll();
			if (Hr == DIERR_INPUTLOST || Hr == DIERR_NOTACQUIRED)
			{
				Dv.D->Acquire();
			}
			Hr = Dv.D->GetDeviceState(sizeof(St), &St);
			FMemory::Memcpy(Dv.PrevBtn, Dv.Btn, sizeof(Dv.Btn));
			FMemory::Memcpy(Dv.PrevAxes, Dv.Axes, sizeof(Dv.Axes));
			if (FAILED(Hr))
			{
				bAnyLost |= ++Dv.Fails > 120;
				FMemory::Memzero(Dv.Btn, sizeof(Dv.Btn));
				continue;
			}
			Dv.Fails = 0;
			const bool bCount = Dv.bPolled;   // first read is not "activity"
			Dv.bPolled = true;
			const LONG Raw[NumAxes] = {St.lX, St.lY, St.lZ, St.lRx, St.lRy, St.lRz, St.rglSlider[0], St.rglSlider[1]};
			for (int32 a = 0; a < NumAxes; ++a)
			{
				Dv.Axes[a] = FMath::Clamp(Raw[a] / 10000.0f, -1.0f, 1.0f);
				GLastActivity = bCount && FMath::Abs(Dv.Axes[a] - Dv.PrevAxes[a]) > 0.02f ? Now : GLastActivity;
			}
			for (int32 b = 0; b < NumButtons; ++b)
			{
				Dv.Btn[b] = (St.rgbButtons[b] & 0x80) != 0;
				GLastActivity = bCount && Dv.Btn[b] && !Dv.PrevBtn[b] ? Now : GLastActivity;
			}
		}
		if ((GDevs.Num() == 0 || bAnyLost) && Now >= GNextEnum)
		{
			GNextEnum = Now + 2.0;   // ponytail: no hot-add while a wheel is open; replug = relaunch
			Enumerate();
		}
		// watchdog: nobody driving the force -> zero it, drop the soft lock
		if (!GForceZeroed && Now - GLastForceCall > 0.1)
		{
			PushForce(0.0f);
			if (GPrimary != INDEX_NONE)
			{
				PushCondition(GDevs[GPrimary].Spring, 0, 0);
				GLastSoftLock = -1.0f;
			}
			GForceZeroed = true;
		}
		return true;
	}

	FDev* Find(uint32 Device)
	{
		if (Device == 0)
		{
			return GPrimary != INDEX_NONE ? &GDevs[GPrimary] : nullptr;
		}
		for (FDev& Dv : GDevs)
		{
			if (Dv.VidPid == Device)
			{
				return &Dv;
			}
		}
		return nullptr;
	}

	uint32 BindDevice(const FDev& Dv)
	{
		return GPrimary != INDEX_NONE && &Dv == &GDevs[GPrimary] ? 0 : Dv.VidPid;
	}
}

void FODWheelDevice::Start()
{
	if (GStarted)
	{
		return;
	}
	GStarted = true;
	FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateStatic(&Tick), 0.0f);
	FCoreDelegates::OnPreExit.AddLambda([]()
	{
		ReleaseAll();
		if (GDI)
		{
			GDI->Release();
			GDI = nullptr;
		}
	});
}

uint32 FODWheelDevice::PrimaryVidPid()
{
	return GPrimary != INDEX_NONE ? GDevs[GPrimary].VidPid : 0;
}

float FODWheelDevice::Axis(uint32 Device, int32 Index)
{
	const FDev* Dv = Find(Device);
	return Dv && Index >= 0 && Index < NumAxes ? Dv->Axes[Index] : 0.0f;
}

bool FODWheelDevice::Button(uint32 Device, int32 Index)
{
	const FDev* Dv = Find(Device);
	return Dv && Index >= 0 && Index < NumButtons && Dv->Btn[Index];
}

bool FODWheelDevice::Pressed(uint32 Device, int32 Index)
{
	const FDev* Dv = Find(Device);
	return Dv && Index >= 0 && Index < NumButtons && Dv->Btn[Index] && !Dv->PrevBtn[Index];
}

double FODWheelDevice::LastActivityTime()
{
	return GLastActivity;
}

void FODWheelDevice::BeginCapture()
{
	for (FDev& Dv : GDevs)
	{
		FMemory::Memcpy(Dv.Base, Dv.Axes, sizeof(Dv.Axes));
	}
}

bool FODWheelDevice::PollCaptureAxis(FODWheelBind& Out)
{
	for (const FDev& Dv : GDevs)
	{
		for (int32 a = 0; a < NumAxes; ++a)
		{
			const float Moved = Dv.Axes[a] - Dv.Base[a];
			if (FMath::Abs(Moved) > 0.5f)
			{
				Out.Device = BindDevice(Dv);
				Out.Index = a;
				Out.bInvert = Moved < 0.0f;
				Out.bHalf = FMath::Abs(Dv.Base[a]) < 0.5f;
				return true;
			}
		}
	}
	return false;
}

bool FODWheelDevice::PollCaptureButton(FODWheelBind& Out)
{
	for (const FDev& Dv : GDevs)
	{
		for (int32 b = 0; b < NumButtons; ++b)
		{
			if (Dv.Btn[b] && !Dv.PrevBtn[b])
			{
				Out.Device = BindDevice(Dv);
				Out.Index = b;
				Out.bInvert = Out.bHalf = false;
				return true;
			}
		}
	}
	return false;
}

void FODWheelDevice::SetForce(float Command, float Damping, float SoftLockFrac)
{
	if (GPrimary == INDEX_NONE)
	{
		return;
	}
	GLastForceCall = FPlatformTime::Seconds();
	GForceZeroed = false;
	PushForce(Command);
	FDev& Dv = GDevs[GPrimary];
	if (!FMath::IsNearlyEqual(Damping, GLastDamping, 0.01f))
	{
		GLastDamping = Damping;
		PushCondition(Dv.Damper, FMath::RoundToInt(FMath::Clamp(Damping, 0.0f, 1.0f) * DI_FFNOMINALMAX), 0);
	}
	if (!FMath::IsNearlyEqual(SoftLockFrac, GLastSoftLock, 0.005f))
	{
		GLastSoftLock = SoftLockFrac;
		PushCondition(Dv.Spring, SoftLockFrac < 0.999f ? DI_FFNOMINALMAX : 0,
			FMath::RoundToInt(FMath::Clamp(SoftLockFrac, 0.0f, 1.0f) * DI_FFNOMINALMAX));
	}
}

#else

void FODWheelDevice::Start() {}
uint32 FODWheelDevice::PrimaryVidPid() { return 0; }
float FODWheelDevice::Axis(uint32, int32) { return 0.0f; }
bool FODWheelDevice::Button(uint32, int32) { return false; }
bool FODWheelDevice::Pressed(uint32, int32) { return false; }
double FODWheelDevice::LastActivityTime() { return 0.0; }
void FODWheelDevice::BeginCapture() {}
bool FODWheelDevice::PollCaptureAxis(FODWheelBind&) { return false; }
bool FODWheelDevice::PollCaptureButton(FODWheelBind&) { return false; }
void FODWheelDevice::SetForce(float, float, float) {}

#endif
