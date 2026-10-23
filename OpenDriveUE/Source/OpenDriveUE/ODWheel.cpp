#include "ODWheel.h"
#include "ODVehicleMovement.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/AutomationTest.h"

FODWheelProfile FODRacingWheel::Active;
uint32 FODRacingWheel::ActiveVidPid = 0;
bool FODRacingWheel::bActiveLoaded = false;
int32 FODRacingWheel::ManualGearbox = -1;

namespace
{
	const TCHAR* WheelSection = TEXT("OpenDrive.Wheel");

	float Deadzone01(float A, float Dz)
	{
		return Dz >= 1.0f ? 0.0f : FMath::Clamp((A - Dz) / (1.0f - Dz), 0.0f, 1.0f);
	}

	FString BindStr(const FODWheelBind& B)
	{
		return FString::Printf(TEXT("%08X,%d,%d,%d"), B.Device, B.Index, B.bInvert ? 1 : 0, B.bHalf ? 1 : 0);
	}

	void ParseBind(const FString& S, FODWheelBind& B)
	{
		TArray<FString> P;
		S.ParseIntoArray(P, TEXT(","));
		if (P.Num() == 4)
		{
			B.Device = FParse::HexNumber(*P[0]);
			B.Index = FCString::Atoi(*P[1]);
			B.bInvert = P[2] == TEXT("1");
			B.bHalf = P[3] == TEXT("1");
		}
	}

	FODWheelBind Ax(int32 Index, bool bInvert = false, bool bHalf = false)
	{
		FODWheelBind B;
		B.Index = Index;
		B.bInvert = bInvert;
		B.bHalf = bHalf;
		return B;
	}
}

float FODRacingWheel::Pedal(float V, const FODWheelBind& B, float Deadzone)
{
	if (!B.IsBound())
	{
		return 0.0f;
	}
	const float S = (B.bInvert ? -1.0f : 1.0f) * FMath::Clamp(V, -1.0f, 1.0f);
	const float P = B.bHalf ? FMath::Max(S, 0.0f) : (S + 1.0f) * 0.5f;
	return Deadzone01(P, Deadzone);
}

float FODRacingWheel::Steer(float V, const FODWheelBind& B, float RotationDeg, float CarLockDeg,
	float Deadzone, float Linearity)
{
	if (!B.IsBound() || CarLockDeg <= 1.0f)
	{
		return 0.0f;
	}
	// rim angle / car's half lock: 1:1 with the car's steering wheel
	const float X = FMath::Clamp((B.bInvert ? -V : V) * RotationDeg / CarLockDeg, -1.0f, 1.0f);
	const float A = FMath::Pow(Deadzone01(FMath::Abs(X), Deadzone), FMath::Max(Linearity, 0.1f));
	return FMath::Sign(X) * A;
}

float FODRacingWheel::SoftLockFrac(float RotationDeg, float CarLockDeg)
{
	return RotationDeg > 1.0f ? FMath::Clamp(CarLockDeg / RotationDeg, 0.05f, 1.0f) : 1.0f;
}

float FODRacingWheel::FfbCommand(float RimNm, float Strength, bool bInvert)
{
	const float X = (bInvert ? -RimNm : RimNm) / FfbFullScaleNm * FMath::Max(Strength, 0.0f);
	float A = FMath::Abs(X);
	if (A > 0.8f)
	{
		A = 0.8f + 0.2f * FMath::Tanh((A - 0.8f) / 0.2f);   // soft knee, never clips hard
	}
	return FMath::Sign(X) * FMath::Min(A, 1.0f);
}

FODWheelProfile FODRacingWheel::DefaultProfile(uint32 VidPid)
{
	FODWheelProfile P;
	auto Btn = [&P](EODWheelButton B, int32 Index) { P.Buttons[static_cast<int32>(B)].Index = Index; };
	auto Axis = [&P](EODWheelAxis A, const FODWheelBind& B) { P.Axes[static_cast<int32>(A)] = B; };
	const uint32 Vid = VidPid >> 16, Pid = VidPid & 0xFFFF;

	Axis(EODWheelAxis::Steer, Ax(0));
	if (Vid == 0x046D && (Pid == 0xC24F || Pid == 0xC262 || Pid == 0xC266 || Pid == 0xC267
		|| Pid == 0xC26E || Pid == 0xC29B || Pid == 0xC299))
	{
		// Logitech G29 / G920 / G923 / G27 / G25 (separate pedals mode):
		// pedals rest at +max, pressed toward -max
		P.RotationDeg = 900.0f;
		Axis(EODWheelAxis::Throttle, Ax(1, true));
		Axis(EODWheelAxis::Brake, Ax(5, true));
		Axis(EODWheelAxis::Clutch, Ax(6, true));
		Btn(EODWheelButton::ShiftUp, 4);     // right paddle
		Btn(EODWheelButton::ShiftDown, 5);   // left paddle
		for (int32 g = 0; g < 6; ++g)        // Driving Force Shifter
		{
			Btn(static_cast<EODWheelButton>(static_cast<int32>(EODWheelButton::Gear1) + g), 12 + g);
		}
		Btn(EODWheelButton::GearR, 18);
	}
	else if (Vid == 0x0EB7)
	{
		// Fanatec (CSL / ClubSport / DD bases): UNVERIFIED best guess -
		// check with the rebind menu's capture on real hardware
		P.RotationDeg = 1080.0f;
		Axis(EODWheelAxis::Throttle, Ax(2, true));
		Axis(EODWheelAxis::Brake, Ax(1, true));
		Axis(EODWheelAxis::Clutch, Ax(5, true));
		Btn(EODWheelButton::ShiftUp, 4);
		Btn(EODWheelButton::ShiftDown, 5);
	}
	return P;
}

FString FODRacingWheel::DeviceKey(uint32 VidPid)
{
	return FString::Printf(TEXT("%04X_%04X"), VidPid >> 16, VidPid & 0xFFFF);
}

FString FODRacingWheel::AxisName(int32 Index)
{
	static const TCHAR* Names[] = {TEXT("X"), TEXT("Y"), TEXT("Z"), TEXT("Rx"), TEXT("Ry"), TEXT("Rz"),
		TEXT("Slider1"), TEXT("Slider2")};
	return Index >= 0 && Index < UE_ARRAY_COUNT(Names) ? FString(Names[Index]) : FString(TEXT("?"));
}

FString FODRacingWheel::BindName(const FODWheelBind& B, bool bAxis)
{
	if (!B.IsBound())
	{
		return TEXT("-");
	}
	FString S = bAxis
		? FString::Printf(TEXT("%s axis%s%s"), *AxisName(B.Index), B.bHalf ? TEXT(" half") : TEXT(""),
			B.bInvert ? TEXT(" inv") : TEXT(""))
		: FString::Printf(TEXT("Button %d"), B.Index + 1);
	if (B.Device != 0 && B.Device != ActiveVidPid)
	{
		S = DeviceKey(B.Device) + TEXT(" ") + S;
	}
	return S;
}

const TCHAR* FODRacingWheel::ButtonName(EODWheelButton B)
{
	static const TCHAR* Names[] = {TEXT("ShiftUp"), TEXT("ShiftDown"), TEXT("Handbrake"), TEXT("Reset"),
		TEXT("Gear1"), TEXT("Gear2"), TEXT("Gear3"), TEXT("Gear4"), TEXT("Gear5"), TEXT("Gear6"), TEXT("Gear7"),
		TEXT("GearR"), TEXT("MediaPlayPause"), TEXT("MediaNext"), TEXT("MediaPrev")};
	static_assert(UE_ARRAY_COUNT(Names) == static_cast<int32>(EODWheelButton::Count), "ButtonName table");
	return Names[static_cast<int32>(B)];
}

const TCHAR* FODRacingWheel::AxisLabel(EODWheelAxis A)
{
	static const TCHAR* Names[] = {TEXT("Steer"), TEXT("Throttle"), TEXT("Brake"), TEXT("Clutch")};
	static_assert(UE_ARRAY_COUNT(Names) == static_cast<int32>(EODWheelAxis::Count), "AxisLabel table");
	return Names[static_cast<int32>(A)];
}

void FODRacingWheel::UseDevice(uint32 VidPid)
{
	if (!bActiveLoaded || VidPid != ActiveVidPid)
	{
		Load(VidPid);
	}
}

void FODRacingWheel::Load(uint32 VidPid)
{
	ActiveVidPid = VidPid;
	bActiveLoaded = true;
	Active = DefaultProfile(VidPid);
	if (!GConfig)
	{
		return;
	}
	const FString Sec = FString(WheelSection) + TEXT(".") + DeviceKey(VidPid);
	FODWheelProfile& P = Active;
	GConfig->GetFloat(*Sec, TEXT("Rotation"), P.RotationDeg, GGameUserSettingsIni);
	GConfig->GetFloat(*Sec, TEXT("SteerDeadzone"), P.SteerDeadzone, GGameUserSettingsIni);
	GConfig->GetFloat(*Sec, TEXT("Linearity"), P.Linearity, GGameUserSettingsIni);
	GConfig->GetFloat(*Sec, TEXT("PedalDeadzone"), P.PedalDeadzone, GGameUserSettingsIni);
	GConfig->GetFloat(*Sec, TEXT("FfbStrength"), P.FfbStrength, GGameUserSettingsIni);
	GConfig->GetFloat(*Sec, TEXT("FfbDamping"), P.FfbDamping, GGameUserSettingsIni);
	GConfig->GetBool(*Sec, TEXT("FfbInvert"), P.bFfbInvert, GGameUserSettingsIni);
	FString S;
	for (int32 i = 0; i < static_cast<int32>(EODWheelAxis::Count); ++i)
	{
		if (GConfig->GetString(*Sec, *FString::Printf(TEXT("Axis_%s"), AxisLabel(static_cast<EODWheelAxis>(i))), S,
			GGameUserSettingsIni))
		{
			ParseBind(S, P.Axes[i]);
		}
	}
	for (int32 i = 0; i < static_cast<int32>(EODWheelButton::Count); ++i)
	{
		if (GConfig->GetString(*Sec, *FString::Printf(TEXT("Button_%s"), ButtonName(static_cast<EODWheelButton>(i))),
			S, GGameUserSettingsIni))
		{
			ParseBind(S, P.Buttons[i]);
		}
	}
}

void FODRacingWheel::Save()
{
	if (!GConfig)
	{
		return;
	}
	const FString Sec = FString(WheelSection) + TEXT(".") + DeviceKey(ActiveVidPid);
	const FODWheelProfile& P = Active;
	GConfig->SetFloat(*Sec, TEXT("Rotation"), P.RotationDeg, GGameUserSettingsIni);
	GConfig->SetFloat(*Sec, TEXT("SteerDeadzone"), P.SteerDeadzone, GGameUserSettingsIni);
	GConfig->SetFloat(*Sec, TEXT("Linearity"), P.Linearity, GGameUserSettingsIni);
	GConfig->SetFloat(*Sec, TEXT("PedalDeadzone"), P.PedalDeadzone, GGameUserSettingsIni);
	GConfig->SetFloat(*Sec, TEXT("FfbStrength"), P.FfbStrength, GGameUserSettingsIni);
	GConfig->SetFloat(*Sec, TEXT("FfbDamping"), P.FfbDamping, GGameUserSettingsIni);
	GConfig->SetBool(*Sec, TEXT("FfbInvert"), P.bFfbInvert, GGameUserSettingsIni);
	for (int32 i = 0; i < static_cast<int32>(EODWheelAxis::Count); ++i)
	{
		GConfig->SetString(*Sec, *FString::Printf(TEXT("Axis_%s"), AxisLabel(static_cast<EODWheelAxis>(i))),
			*BindStr(P.Axes[i]), GGameUserSettingsIni);
	}
	for (int32 i = 0; i < static_cast<int32>(EODWheelButton::Count); ++i)
	{
		GConfig->SetString(*Sec, *FString::Printf(TEXT("Button_%s"), ButtonName(static_cast<EODWheelButton>(i))),
			*BindStr(P.Buttons[i]), GGameUserSettingsIni);
	}
	GConfig->Flush(false, GGameUserSettingsIni);
}

void FODRacingWheel::ResetProfile()
{
	if (GConfig)
	{
		GConfig->EmptySection(*(FString(WheelSection) + TEXT(".") + DeviceKey(ActiveVidPid)), GGameUserSettingsIni);
		GConfig->Flush(false, GGameUserSettingsIni);
	}
	Load(ActiveVidPid);
}

bool FODRacingWheel::IsManualGearbox()
{
	if (ManualGearbox < 0)
	{
		bool bManual = false;
		if (GConfig)
		{
			GConfig->GetBool(WheelSection, TEXT("ManualGearbox"), bManual, GGameUserSettingsIni);
		}
		ManualGearbox = bManual ? 1 : 0;
	}
	return ManualGearbox == 1;
}

void FODRacingWheel::SetManualGearbox(bool bManual)
{
	ManualGearbox = bManual ? 1 : 0;
	if (GConfig)
	{
		GConfig->SetBool(WheelSection, TEXT("ManualGearbox"), bManual, GGameUserSettingsIni);
		GConfig->Flush(false, GGameUserSettingsIni);
	}
}

// ---- device-free self-check (no wheel needed) --------------------------
// UnrealEditor-Cmd OpenDriveUE.uproject -ExecCmds="Automation RunTests
// OpenDrive.Wheel;Quit" -unattended -nullrhi: prints the axis pipeline on
// synthetic input and FFB rim torque vs slip angle from the real tyre model.
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FODWheelSelfCheck, "OpenDrive.Wheel.SelfCheck",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext | EAutomationTestFlags::ProductFilter)

bool FODWheelSelfCheck::RunTest(const FString& Parameters)
{
	// ---- axis pipeline ----
	const FODWheelProfile G29 = FODRacingWheel::DefaultProfile(0x046DC24F);
	const FODWheelBind& SteerB = G29.Axes[static_cast<int32>(EODWheelAxis::Steer)];
	const FODWheelBind& ThrB = G29.Axes[static_cast<int32>(EODWheelAxis::Throttle)];
	const UODVehicleMovement* Cdo = GetDefault<UODVehicleMovement>();
	const float CarLock = Cdo->GetSteeringLockToLockDeg();
	AddInfo(FString::Printf(TEXT("car lock-to-lock %.0f deg, G29 rotation %.0f, soft lock frac %.3f"),
		CarLock, G29.RotationDeg, FODRacingWheel::SoftLockFrac(G29.RotationDeg, CarLock)));
	AddInfo(TEXT("raw    steer(900) steer(dz.1,lin2) thr(full,inv) pedal(half)"));
	float Prev = -2.0f;
	for (int32 i = -10; i <= 10; i += 2)
	{
		const float V = i / 10.0f;
		const float St = FODRacingWheel::Steer(V, SteerB, 900.0f, CarLock, 0.0f, 1.0f);
		AddInfo(FString::Printf(TEXT("%+.2f  %+.3f      %+.3f            %.3f          %.3f"), V, St,
			FODRacingWheel::Steer(V, SteerB, 900.0f, CarLock, 0.1f, 2.0f), FODRacingWheel::Pedal(V, ThrB, 0.0f), FODRacingWheel::Pedal(V, Ax(1, false, true), 0.0f)));
		TestTrue(TEXT("steer monotonic"), St >= Prev);
		Prev = St;
	}
	TestEqual(TEXT("steer centre"), FODRacingWheel::Steer(0.0f, SteerB, 900.0f, CarLock, 0.0f, 1.0f), 0.0f);
	TestTrue(TEXT("steer full lock"), FODRacingWheel::Steer(1.0f, SteerB, 900.0f, CarLock, 0.0f, 1.0f) > 0.98f);
	TestTrue(TEXT("steer left lock"), FODRacingWheel::Steer(-1.0f, SteerB, 900.0f, CarLock, 0.0f, 1.0f) < -0.98f);
	TestTrue(TEXT("steer inverted"), FODRacingWheel::Steer(0.5f, Ax(0, true), 900.0f, CarLock, 0.0f, 1.0f) < 0.0f);
	TestEqual(TEXT("1080 rim clamps at car lock"), FODRacingWheel::Steer(1.0f, SteerB, 1080.0f, CarLock, 0.0f, 1.0f), 1.0f);
	TestEqual(TEXT("steer 1:1 at 900"), FODRacingWheel::Steer(0.5f, SteerB, 900.0f, 900.0f, 0.0f, 1.0f), 0.5f, 1e-4f);
	TestEqual(TEXT("steer deadzone"), FODRacingWheel::Steer(0.05f, SteerB, 900.0f, 900.0f, 0.1f, 1.0f), 0.0f);
	TestEqual(TEXT("linearity 2"), FODRacingWheel::Steer(0.5f, SteerB, 900.0f, 900.0f, 0.0f, 2.0f), 0.25f, 1e-4f);
	TestEqual(TEXT("inverted pedal released"), FODRacingWheel::Pedal(1.0f, ThrB, 0.03f), 0.0f);
	TestEqual(TEXT("inverted pedal floored"), FODRacingWheel::Pedal(-1.0f, ThrB, 0.03f), 1.0f);
	TestEqual(TEXT("half axis rest"), FODRacingWheel::Pedal(0.0f, Ax(1, false, true), 0.0f), 0.0f);
	TestEqual(TEXT("half axis other side"), FODRacingWheel::Pedal(-0.7f, Ax(1, false, true), 0.0f), 0.0f);
	TestEqual(TEXT("half axis inverted (brake side)"), FODRacingWheel::Pedal(-0.6f, Ax(1, true, true), 0.0f), 0.6f, 1e-4f);
	TestEqual(TEXT("pedal deadzone"), FODRacingWheel::Pedal(0.95f, ThrB, 0.03f), 0.0f);
	TestEqual(TEXT("unbound pedal"), FODRacingWheel::Pedal(-1.0f, FODWheelBind(), 0.0f), 0.0f);
	TestEqual(TEXT("soft lock 900 vs 912 = none"), FODRacingWheel::SoftLockFrac(900.0f, 912.0f), 1.0f);
	TestEqual(TEXT("soft lock 1080"), FODRacingWheel::SoftLockFrac(1080.0f, 912.0f), 912.0f / 1080.0f, 1e-3f);

	// ---- FFB vs slip angle, real tyre model, both front tyres ----
	const float PeakDeg = Cdo->PeakSlipAngleDeg + Cdo->SlipAngleAddDeg;
	const float SlideKeep = FMath::Max(Cdo->SlideFrac, Cdo->SlideFracMin);
	const float FMax = Cdo->MuPeak * 400000.0f;   // ~400 kg front corner, kg cm/s^2
	auto RimNm = [&](float AlphaDeg, float Sx, float Load)
	{
		const float Sy = AlphaDeg / PeakDeg;
		float Fx, Fy;
		UODVehicleMovement::TyreForce(Sx, Sy, FMax * Load, SlideKeep, Fx, Fy);
		return 2.0f * Cdo->AligningRimNm(Fy, FMath::Sqrt(Sx * Sx + Sy * Sy));
	};
	AddInfo(FString::Printf(TEXT("alpha  rimNm   cmd(str .75)   (peak slip %.1f deg, trail %.1f+%.1f cm, ratio %.0f)"),
		PeakDeg, Cdo->PneumaticTrailCm, Cdo->CasterTrailCm, Cdo->SteeringRatio));
	float MaxAbs = 0.0f, MaxAt = 0.0f;
	for (float A = 0.0f; A <= 2.0f * PeakDeg + 0.01f; A += PeakDeg / 8.0f)
	{
		const float T = RimNm(A, 0.0f, 1.0f);
		AddInfo(FString::Printf(TEXT("%5.2f  %+6.2f  %+.3f"), A, T, FODRacingWheel::FfbCommand(T, 0.75f, false)));
		if (A > 0.0f)
		{
			TestTrue(TEXT("aligning torque opposes steer"), T < 0.0f);
		}
		if (FMath::Abs(T) > MaxAbs)
		{
			MaxAbs = FMath::Abs(T);
			MaxAt = A;
		}
	}
	AddInfo(FString::Printf(TEXT("peak %.2f Nm at %.2f deg"), MaxAbs, MaxAt));
	TestTrue(TEXT("torque peaks before the grip peak"), MaxAt < PeakDeg);
	TestTrue(TEXT("wheel goes light past the peak"), FMath::Abs(RimNm(1.6f * PeakDeg, 0.0f, 1.0f)) < 0.75f * MaxAbs);
	TestTrue(TEXT("lockup lightens"),
		FMath::Abs(RimNm(0.5f * PeakDeg, -1.0f / Cdo->PeakSlipRatio, 1.0f)) < 0.5f * FMath::Abs(RimNm(0.5f * PeakDeg, 0.0f, 1.0f)));
	TestTrue(TEXT("more load, more torque"),
		FMath::Abs(RimNm(0.5f * PeakDeg, 0.0f, 1.5f)) > FMath::Abs(RimNm(0.5f * PeakDeg, 0.0f, 1.0f)));
	TestTrue(TEXT("peak torque sane (3..40 Nm)"), MaxAbs > 3.0f && MaxAbs < 40.0f);

	TestEqual(TEXT("ffb zero"), FODRacingWheel::FfbCommand(0.0f, 1.0f, false), 0.0f);
	TestEqual(TEXT("ffb linear"), FODRacingWheel::FfbCommand(10.0f, 1.0f, false), 0.5f, 1e-4f);
	TestEqual(TEXT("ffb invert"), FODRacingWheel::FfbCommand(10.0f, 1.0f, true), -0.5f, 1e-4f);
	TestEqual(TEXT("ffb strength"), FODRacingWheel::FfbCommand(10.0f, 0.5f, false), 0.25f, 1e-4f);
	TestTrue(TEXT("ffb clamps"), FODRacingWheel::FfbCommand(500.0f, 1.5f, false) <= 1.0f && FODRacingWheel::FfbCommand(-500.0f, 1.5f, false) >= -1.0f);
	TestEqual(TEXT("ffb off"), FODRacingWheel::FfbCommand(15.0f, 0.0f, false), 0.0f);

	// ---- manual gearbox sequencing ----
	UODVehicleMovement* M = NewObject<UODVehicleMovement>();
	M->bManualGearbox = true;
	M->RequestGear(0);
	TestEqual(TEXT("neutral"), M->GetGear(), 0);
	M->ShiftUp();
	TestEqual(TEXT("N -> 1"), M->GetGear(), 1);
	M->ShiftDown();
	M->ShiftDown();
	TestEqual(TEXT("1 -> N -> R"), M->GetGear(), -1);
	M->ShiftDown();
	TestEqual(TEXT("R is the floor"), M->GetGear(), -1);
	M->RequestGear(99);
	TestEqual(TEXT("top gear clamp"), M->GetGear(), M->GearRatios.Num());
	return true;
}
