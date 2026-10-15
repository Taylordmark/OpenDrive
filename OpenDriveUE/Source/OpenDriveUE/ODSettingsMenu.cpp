#include "ODSettingsMenu.h"

#include "EngineUtils.h"
#include "GameFramework/GameUserSettings.h"
#include "GenericPlatform/GenericApplication.h"
#include "HAL/IConsoleManager.h"
#include "HAL/PlatformApplicationMisc.h"
#include "Kismet/GameplayStatics.h"

#include "ODAmbientSoundManager.h"
#include "ODAssetDetail.h"
#include "ODCreditsMenu.h"
#include "ODInputConfig.h"
#include "ODRaceHUD.h"
#include "ODRebindMenu.h"
#include "ODTrafficManager.h"
#include "ODVehicleAudio.h"
#include "ODVehicleMovement.h"
#include "ODVehiclePawn.h"
#include "ODVisualStyle.h"

namespace
{
struct FResDef
{
	int32 W, H;
};
// Common 16:9 modes; "Native" is a synthesized extra entry past the end of
// this table (see CurrentResolutionIdx()/ApplyResolution()) rather than a
// row in it, since the native size is read from the display at runtime, not
// a fixed constant.
const FResDef Resolutions[] = {
	{1280, 720}, {1600, 900}, {1920, 1080}, {2560, 1440}, {3840, 2160},
};
constexpr int32 NumFixedRes = UE_ARRAY_COUNT(Resolutions);
constexpr int32 NativeResIdx = NumFixedRes;   // sentinel: "Native" row
constexpr int32 NumResOptions = NumFixedRes + 1;

struct FWinModeDef
{
	EWindowMode::Type Mode;
	const TCHAR* Name;
};
const FWinModeDef WindowModes[] = {
	{EWindowMode::Fullscreen, TEXT("Fullscreen")},
	{EWindowMode::Windowed, TEXT("Windowed")},
	{EWindowMode::WindowedFullscreen, TEXT("Borderless")},
};
constexpr int32 NumWindowModes = UE_ARRAY_COUNT(WindowModes);

const TCHAR* QualityNames[] = {TEXT("Low"), TEXT("Medium"), TEXT("High"), TEXT("Epic")};
const TCHAR* AssetDetailNames[] = {TEXT("Standard"), TEXT("High"), TEXT("Ultra")};
const TCHAR* VisualStyleNames[] = {TEXT("Stylized"), TEXT("Realistic")};
constexpr int32 NumQualityLevels = UE_ARRAY_COUNT(QualityNames);

// r.AntiAliasingMethod's own integer values (stable across UE4/5, not an
// engine header enum this module includes - see AODSettingsMenu.h's comment
// on why only Off/TSR are offered): 0 None, 1 FXAA, 2 TemporalAA (TAA - the
// "Off" row's actual AA method, plain non-upscaling TAA), 3 MSAA, 4 TSR.
// Named Cvar* (not AAM_*) to avoid colliding with the engine's own
// EAntiAliasingMethod enumerators (SceneUtils.h, pulled in transitively) -
// these are plain r.AntiAliasingMethod integer values, not that enum.
constexpr int32 CvarAA_TemporalAA = 2;
constexpr int32 CvarAA_TSR = 4;

FIntPoint NativeResolution()
{
	FDisplayMetrics DM;
	FDisplayMetrics::RebuildDisplayMetrics(DM);
	return FIntPoint(DM.PrimaryDisplayWidth, DM.PrimaryDisplayHeight);
}

template <typename T>
T* FindSettingsActor(UWorld* World)
{
	for (TActorIterator<T> It(World); It; ++It)
	{
		return *It;
	}
	return nullptr;
}

// Row ids: one global GetRow()/AdjustSelected() switch; each tab shows a slice.
enum ESettingsRow : int32
{
	RowResolution, RowWindowMode, RowQuality, RowUpscaling, RowRenderScale, RowVSync, RowAssetDetail, RowVisualStyle,
	RowMaster, RowVehicle, RowAmbient,
	RowTraction, RowAbs, RowMiniMap, RowUnits, RowTraffic, RowCredits,
	RowInputDevice, RowSteerDeadzone, RowSteerSensitivity, RowPedalDeadzone, RowBindings,
	RowClose,
	NumSettingsRows,
};
static_assert(NumSettingsRows == 23, "keep AODSettingsMenu::NumRows() in sync");
const FODMenuTab SettingsTabs[] = {
	{TEXT("DISPLAY"), RowResolution, 8},
	{TEXT("AUDIO"), RowMaster, 3},
	{TEXT("GAME"), RowTraction, 6},
	{TEXT("CONTROLS"), RowInputDevice, 5},
};

FString Percent(float V)
{
	return FString::Printf(TEXT("%d%%"), FMath::RoundToInt(V * 100.0f));
}
}

AODSettingsMenu::AODSettingsMenu()
{
	PrimaryActorTick.bCanEverTick = false;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODSettingsMenu::BeginPlay()
{
	Super::BeginPlay();
	LoadGraphicsConfig();
	AssetDetailAtLoad = AssetDetail;
	VisualStyleAtLoad = VisualStyle;
	LoadAudioConfig();
	ApplyUpscalerCvars();
	ApplyAudioVolumes();
}

// ---- selection / adjust ---------------------------------------------------

void AODSettingsMenu::MoveSelection(int32 Dir)
{
	if (bRebindOpen)
	{
		if (RebindMenu)
		{
			RebindMenu->MoveSelection(Dir);
		}
		return;
	}
	if (bCreditsOpen)
	{
		if (CreditsMenu)
		{
			CreditsMenu->Scroll(Dir);
		}
		return;
	}
	Selected = ODMenuTabs::Step(SettingsTabs[TabIdx], RowClose, Selected, Dir);
}

void AODSettingsMenu::SwitchTab(int32 Dir)
{
	const int32 N = UE_ARRAY_COUNT(SettingsTabs);
	SetTab((TabIdx + Dir + N) % N);
}

void AODSettingsMenu::SetTab(int32 Tab)
{
	if (bRebindOpen || bCreditsOpen || Tab < 0 || Tab >= (int32)UE_ARRAY_COUNT(SettingsTabs))
	{
		return;   // Q/E may be the key being captured
	}
	TabIdx = Tab;
	Selected = SettingsTabs[TabIdx].First;
}

TArrayView<const FODMenuTab> AODSettingsMenu::GetTabs() const
{
	return SettingsTabs;
}

void AODSettingsMenu::SelectRow(int32 Row, int32 Col)
{
	if (bRebindOpen)
	{
		if (RebindMenu)
		{
			RebindMenu->SelectCell(Row, Col);
		}
		return;
	}
	if (!bCreditsOpen && Row >= 0 && Row < NumSettingsRows)
	{
		Selected = Row;
	}
}

void AODSettingsMenu::AdjustSelected(int32 Dir)
{
	if (bRebindOpen)
	{
		if (RebindMenu)
		{
			RebindMenu->AdjustSelected(Dir);
		}
		return;
	}
	if (bCreditsOpen)
	{
		return;
	}
	switch (Selected)
	{
	case RowResolution: ApplyResolution(Dir); break;
	case RowWindowMode: ApplyWindowMode(Dir); break;
	case RowQuality: ApplyQuality(Dir); break;
	case RowUpscaling: ApplyUpscaler(Dir); break;
	case RowRenderScale: ApplyScreenPercent(Dir); break;
	case RowVSync: ApplyVSync(); break;
	case RowAssetDetail: ApplyAssetDetail(Dir); break;
	case RowVisualStyle: ApplyVisualStyle(Dir); break;
	case RowMaster:
		MasterVolume = FMath::Clamp(MasterVolume + Dir * 0.05f, 0.0f, 1.0f);
		ApplyAudioVolumes();
		SaveAudioConfig();
		break;
	case RowVehicle:
		VehicleVolume = FMath::Clamp(VehicleVolume + Dir * 0.05f, 0.0f, 1.0f);
		ApplyAudioVolumes();
		SaveAudioConfig();
		break;
	case RowAmbient:
		AmbientVolume = FMath::Clamp(AmbientVolume + Dir * 0.05f, 0.0f, 1.0f);
		ApplyAudioVolumes();
		SaveAudioConfig();
		break;
	case RowTraction:   // High -> Low -> Off (moved here from the pause menu, 2026-10-09)
		if (UODVehicleMovement* D = FindMovement())
		{
			D->TractionMode = (EODTractionMode)(((int32)D->TractionMode + Dir + 3) % 3);
		}
		break;
	case RowAbs:
		if (UODVehicleMovement* D = FindMovement())
		{
			D->bAbsEnabled = !D->bAbsEnabled;
		}
		break;
	case RowMiniMap:    // North <-> Travel (heading-up)
		if (AODRaceHUD* Hud = FindHud())
		{
			Hud->SetMiniMapHeadingUp(!Hud->IsMiniMapHeadingUp());
		}
		break;
	case RowUnits:      // Imperial <-> Metric (gauges)
		if (AODRaceHUD* Hud = FindHud())
		{
			Hud->SetMetric(!Hud->IsMetric());
		}
		break;
	case RowTraffic:    // 3e: on / off only (D4: density follows the clock)
		AODTrafficManager::SetEnabledSetting(!AODTrafficManager::IsEnabledSetting());
		break;
	case RowInputDevice: ApplyInputDevice(Dir); break;
	// wheel conditioning (Arc 6) - moved here from the Key Bindings screen
	case RowSteerDeadzone:
		FODInputConfig::SetSteeringDeadzone(FODInputConfig::GetSteeringDeadzone() + Dir * 0.05f);
		break;
	case RowSteerSensitivity:
		FODInputConfig::SetSteeringSensitivity(FODInputConfig::GetSteeringSensitivity() + Dir * 0.25f);
		break;
	case RowPedalDeadzone:
		FODInputConfig::SetThrottleBrakeDeadzone(FODInputConfig::GetThrottleBrakeDeadzone() + Dir * 0.05f);
		break;
	default: break;   // Credits / Key Bindings / Close: Activate() only
	}
}

bool AODSettingsMenu::Activate()
{
	if (bRebindOpen)
	{
		if (RebindMenu && RebindMenu->ActivateSelected())   // true == its Back row
		{
			bRebindOpen = false;
		}
		return false;
	}
	if (bCreditsOpen)
	{
		bCreditsOpen = false;   // no rows - Enter just backs out
		return false;
	}
	switch (Selected)
	{
	case RowClose:
		return true;
	case RowBindings:
		bRebindOpen = EnsureRebindMenu() != nullptr;
		break;
	case RowCredits:
		bCreditsOpen = EnsureCreditsMenu() != nullptr;
		break;
	default:
		AdjustSelected(+1);   // Enter cycles a value row
		break;
	}
	return false;
}

bool AODSettingsMenu::Back()
{
	if (bRebindOpen)
	{
		if (RebindMenu && RebindMenu->IsCapturing())
		{
			RebindMenu->CancelCapture();
		}
		else
		{
			bRebindOpen = false;
		}
		return false;
	}
	if (bCreditsOpen)
	{
		bCreditsOpen = false;
		return false;
	}
	return true;
}

namespace
{
// lazy find-or-spawn, same idiom as AODMainMenu::EnsureGarageEnvironment
template <typename T>
T* EnsureChild(AActor* Self, TObjectPtr<T>& Cached)
{
	if (!Cached)
	{
		Cached = FindSettingsActor<T>(Self->GetWorld());
	}
	if (!Cached && Self->GetWorld())
	{
		Cached = Self->GetWorld()->SpawnActor<T>();
	}
	return Cached;
}
}

AODRebindMenu* AODSettingsMenu::EnsureRebindMenu()
{
	return EnsureChild(this, RebindMenu);
}

AODCreditsMenu* AODSettingsMenu::EnsureCreditsMenu()
{
	return EnsureChild(this, CreditsMenu);
}

UODVehicleMovement* AODSettingsMenu::FindMovement() const
{
	const APawn* Car = UGameplayStatics::GetPlayerPawn(GetWorld(), 0);
	return Car ? Car->FindComponentByClass<UODVehicleMovement>() : nullptr;
}

AODRaceHUD* AODSettingsMenu::FindHud() const
{
	const APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0);
	return PC ? Cast<AODRaceHUD>(PC->GetHUD()) : nullptr;
}

// ---- graphics ---------------------------------------------------------

int32 AODSettingsMenu::CurrentResolutionIdx() const
{
	const UGameUserSettings* GS = UGameUserSettings::GetGameUserSettings();
	if (!GS)
	{
		return NativeResIdx;
	}
	const FIntPoint Cur = GS->GetScreenResolution();
	for (int32 i = 0; i < NumFixedRes; ++i)
	{
		if (Cur.X == Resolutions[i].W && Cur.Y == Resolutions[i].H)
		{
			return i;
		}
	}
	return NativeResIdx;   // no exact match - treat as "Native" for display/cycling purposes
}

void AODSettingsMenu::ApplyResolution(int32 Dir)
{
	UGameUserSettings* GS = UGameUserSettings::GetGameUserSettings();
	if (!GS)
	{
		return;
	}
	const int32 Next = (CurrentResolutionIdx() + Dir + NumResOptions) % NumResOptions;
	const FIntPoint Res = (Next == NativeResIdx) ? NativeResolution()
		: FIntPoint(Resolutions[Next].W, Resolutions[Next].H);
	GS->SetScreenResolution(Res);
	GS->ApplyResolutionSettings(false);
	GS->SaveSettings();
}

int32 AODSettingsMenu::CurrentWindowModeIdx() const
{
	const UGameUserSettings* GS = UGameUserSettings::GetGameUserSettings();
	if (!GS)
	{
		return 0;
	}
	const EWindowMode::Type Cur = GS->GetFullscreenMode();
	for (int32 i = 0; i < NumWindowModes; ++i)
	{
		if (WindowModes[i].Mode == Cur)
		{
			return i;
		}
	}
	return 0;
}

void AODSettingsMenu::ApplyWindowMode(int32 Dir)
{
	UGameUserSettings* GS = UGameUserSettings::GetGameUserSettings();
	if (!GS)
	{
		return;
	}
	const int32 Next = (CurrentWindowModeIdx() + Dir + NumWindowModes) % NumWindowModes;
	GS->SetFullscreenMode(WindowModes[Next].Mode);
	GS->ApplyResolutionSettings(false);
	GS->SaveSettings();
}

void AODSettingsMenu::ApplyQuality(int32 Dir)
{
	UGameUserSettings* GS = UGameUserSettings::GetGameUserSettings();
	if (!GS)
	{
		return;
	}
	const int32 Cur = GS->GetOverallScalabilityLevel();   // -1 if levels are mixed
	const int32 Base = (Cur >= 0 && Cur < NumQualityLevels) ? Cur : 0;
	const int32 Next = (Base + Dir + NumQualityLevels) % NumQualityLevels;
	GS->SetOverallScalabilityLevel(Next);
	GS->SaveSettings();
}

void AODSettingsMenu::ApplyUpscaler(int32 Dir)
{
	UpscalerIdx = (UpscalerIdx + Dir + 2) % 2;
	ApplyUpscalerCvars();
	SaveGraphicsConfig();
}

void AODSettingsMenu::ApplyScreenPercent(int32 Dir)
{
	ScreenPercent = FMath::Clamp(ScreenPercent + Dir * 10, 50, 100);
	ApplyUpscalerCvars();   // re-pushes both cvars together, screen % included
	SaveGraphicsConfig();
}

void AODSettingsMenu::ApplyUpscalerCvars() const
{
	if (IConsoleVariable* CVarAA = IConsoleManager::Get().FindConsoleVariable(TEXT("r.AntiAliasingMethod")))
	{
		CVarAA->Set(UpscalerIdx == 1 ? CvarAA_TSR : CvarAA_TemporalAA, ECVF_SetByGameSetting);
	}
	if (IConsoleVariable* CVarSP = IConsoleManager::Get().FindConsoleVariable(TEXT("r.ScreenPercentage")))
	{
		CVarSP->Set((float)ScreenPercent, ECVF_SetByGameSetting);
	}
}

void AODSettingsMenu::ApplyVSync()
{
	UGameUserSettings* GS = UGameUserSettings::GetGameUserSettings();
	if (!GS)
	{
		return;
	}
	GS->SetVSyncEnabled(!GS->IsVSyncEnabled());
	GS->ApplySettings(false);
	GS->SaveSettings();
}

void AODSettingsMenu::ApplyAssetDetail(int32 Dir)
{
	AssetDetail = (AssetDetail + Dir + 3) % 3;
	SaveGraphicsConfig();
}

void AODSettingsMenu::ApplyVisualStyle(int32 Dir)
{
	VisualStyle = (VisualStyle + Dir + 2) % 2;
	SaveGraphicsConfig();
}

// ---- audio --------------------------------------------------------------

void AODSettingsMenu::ApplyAudioVolumes() const
{
	// Single application points (see AODVehicleAudio::SetVehicleVolumeScale
	// and AODAmbientSoundManager::SetVolumeScale's own comments) - each is a
	// plain float multiply at the actor's one existing volume-application
	// spot, not a per-frame cost added here: this only runs on load and on a
	// slider nudge, not every tick.
	if (AODVehicleAudio* Audio = FindSettingsActor<AODVehicleAudio>(GetWorld()))
	{
		Audio->SetVehicleVolumeScale(MasterVolume * VehicleVolume);
	}
	if (AODAmbientSoundManager* Ambient = FindSettingsActor<AODAmbientSoundManager>(GetWorld()))
	{
		Ambient->SetVolumeScale(MasterVolume * AmbientVolume);
	}
}

void AODSettingsMenu::ApplyInputDevice(int32 Dir)
{
	APawn* Pawn = UGameplayStatics::GetPlayerPawn(GetWorld(), 0);
	AODVehiclePawn* VehiclePawn = Cast<AODVehiclePawn>(Pawn);
	if (!VehiclePawn)
	{
		return;
	}
	constexpr int32 NumModes = static_cast<int32>(EODInputDevice::Wheel) + 1;
	const int32 Next = (static_cast<int32>(VehiclePawn->GetInputDeviceMode()) + Dir + NumModes) % NumModes;
	VehiclePawn->SetInputDeviceMode(static_cast<EODInputDevice>(Next));
}

// ---- persistence ----------------------------------------------------------

namespace
{
const TCHAR* GraphicsConfigSection = TEXT("OpenDrive.Graphics");
const TCHAR* AudioConfigSection = TEXT("OpenDrive.Audio");
}

void AODSettingsMenu::SaveGraphicsConfig() const
{
	GConfig->SetInt(GraphicsConfigSection, TEXT("UpscalerIdx"), UpscalerIdx, GGameUserSettingsIni);
	GConfig->SetInt(GraphicsConfigSection, TEXT("ScreenPercent"), ScreenPercent, GGameUserSettingsIni);
	GConfig->SetInt(ODAssetDetail::ConfigSection, ODAssetDetail::ConfigKey, AssetDetail, GGameUserSettingsIni);
	GConfig->SetInt(ODVisualStyle::ConfigSection, ODVisualStyle::ConfigKey, VisualStyle, GGameUserSettingsIni);
	GConfig->Flush(false, GGameUserSettingsIni);
}

void AODSettingsMenu::LoadGraphicsConfig()
{
	GConfig->GetInt(GraphicsConfigSection, TEXT("UpscalerIdx"), UpscalerIdx, GGameUserSettingsIni);
	GConfig->GetInt(GraphicsConfigSection, TEXT("ScreenPercent"), ScreenPercent, GGameUserSettingsIni);
	AssetDetail = ODAssetDetail::Level();
	VisualStyle = ODVisualStyle::Level();
	UpscalerIdx = FMath::Clamp(UpscalerIdx, 0, 1);
	ScreenPercent = FMath::Clamp(ScreenPercent, 50, 100);
}

void AODSettingsMenu::SaveAudioConfig() const
{
	GConfig->SetFloat(AudioConfigSection, TEXT("MasterVolume"), MasterVolume, GGameUserSettingsIni);
	GConfig->SetFloat(AudioConfigSection, TEXT("VehicleVolume"), VehicleVolume, GGameUserSettingsIni);
	GConfig->SetFloat(AudioConfigSection, TEXT("AmbientVolume"), AmbientVolume, GGameUserSettingsIni);
	GConfig->Flush(false, GGameUserSettingsIni);
}

void AODSettingsMenu::LoadAudioConfig()
{
	GConfig->GetFloat(AudioConfigSection, TEXT("MasterVolume"), MasterVolume, GGameUserSettingsIni);
	GConfig->GetFloat(AudioConfigSection, TEXT("VehicleVolume"), VehicleVolume, GGameUserSettingsIni);
	GConfig->GetFloat(AudioConfigSection, TEXT("AmbientVolume"), AmbientVolume, GGameUserSettingsIni);
	MasterVolume = FMath::Clamp(MasterVolume, 0.0f, 1.0f);
	VehicleVolume = FMath::Clamp(VehicleVolume, 0.0f, 1.0f);
	AmbientVolume = FMath::Clamp(AmbientVolume, 0.0f, 1.0f);
}

// ---- HUD-facing -------------------------------------------------------

void AODSettingsMenu::GetRow(int32 Row, FString& OutLabel, FString& OutValue) const
{
	OutValue.Empty();
	switch (Row)
	{
	case RowResolution:
	{
		OutLabel = TEXT("Resolution");
		const int32 Idx = CurrentResolutionIdx();
		if (Idx == NativeResIdx)
		{
			const FIntPoint Native = NativeResolution();
			OutValue = FString::Printf(TEXT("Native %dx%d"), Native.X, Native.Y);
		}
		else
		{
			OutValue = FString::Printf(TEXT("%dx%d"), Resolutions[Idx].W, Resolutions[Idx].H);
		}
		break;
	}
	case RowWindowMode:
		OutLabel = TEXT("Window Mode");
		OutValue = WindowModes[CurrentWindowModeIdx()].Name;
		break;
	case RowQuality:
	{
		OutLabel = TEXT("Graphics");
		OutValue = TEXT("Custom");
		if (const UGameUserSettings* GS = UGameUserSettings::GetGameUserSettings())
		{
			const int32 Level = GS->GetOverallScalabilityLevel();
			if (Level >= 0 && Level < NumQualityLevels)
			{
				OutValue = QualityNames[Level];
			}
		}
		break;
	}
	case RowUpscaling:
		OutLabel = TEXT("Upscaling");
		OutValue = (UpscalerIdx == 1) ? TEXT("TSR") : TEXT("Off");
		break;
	case RowRenderScale:
		OutLabel = TEXT("Render Scale");
		OutValue = FString::Printf(TEXT("%d%%"), ScreenPercent);
		break;
	case RowVSync:
	{
		OutLabel = TEXT("VSync");
		OutValue = TEXT("?");
		if (const UGameUserSettings* GS = UGameUserSettings::GetGameUserSettings())
		{
			OutValue = GS->IsVSyncEnabled() ? TEXT("On") : TEXT("Off");
		}
		break;
	}
	case RowAssetDetail:
		OutLabel = TEXT("Model Detail");   // ODAssetDetail
		OutValue = AssetDetailNames[AssetDetail];
		if (AssetDetail != AssetDetailAtLoad)
		{
			OutValue += TEXT(" (next load)");
		}
		break;
	case RowVisualStyle:
		OutLabel = TEXT("Visual Style");
		OutValue = VisualStyleNames[VisualStyle];
		if (VisualStyle != VisualStyleAtLoad)
		{
			OutValue += TEXT(" (next load)");
		}
		break;
	case RowMaster:
		OutLabel = TEXT("Master Volume");
		OutValue = Percent(MasterVolume);
		break;
	case RowVehicle:
		OutLabel = TEXT("Car Volume");
		OutValue = Percent(VehicleVolume);
		break;
	case RowAmbient:
		OutLabel = TEXT("Ambient Volume");
		OutValue = Percent(AmbientVolume);
		break;
	case RowTraction:
		OutLabel = TEXT("Traction Control");
		OutValue = TEXT("?");
		if (const UODVehicleMovement* D = FindMovement())
		{
			OutValue = UODVehicleMovement::TractionModeName(D->TractionMode);
		}
		break;
	case RowAbs:
		OutLabel = TEXT("ABS");
		OutValue = TEXT("?");
		if (const UODVehicleMovement* D = FindMovement())
		{
			OutValue = D->bAbsEnabled ? TEXT("On") : TEXT("Off");
		}
		break;
	case RowMiniMap:
		OutLabel = TEXT("Mini-Map");
		OutValue = TEXT("?");
		if (const AODRaceHUD* Hud = FindHud())
		{
			OutValue = Hud->IsMiniMapHeadingUp() ? TEXT("Travel Up") : TEXT("North Up");
		}
		break;
	case RowUnits:
		OutLabel = TEXT("Units");
		OutValue = TEXT("?");
		if (const AODRaceHUD* Hud = FindHud())
		{
			OutValue = Hud->IsMetric() ? TEXT("Metric") : TEXT("Imperial");
		}
		break;
	case RowTraffic:
		OutLabel = TEXT("Traffic");
		OutValue = AODTrafficManager::IsEnabledSetting() ? TEXT("On") : TEXT("Off");
		break;
	case RowCredits:
		OutLabel = TEXT("Credits");
		break;
	case RowInputDevice:
	{
		OutLabel = TEXT("Input Device");
		OutValue = TEXT("Auto");
		if (const AODVehiclePawn* Pawn = Cast<AODVehiclePawn>(UGameplayStatics::GetPlayerPawn(GetWorld(), 0)))
		{
			const EODInputDevice Mode = Pawn->GetInputDeviceMode();
			OutValue = AODVehiclePawn::InputDeviceModeToString(Mode);
			if (Mode == EODInputDevice::Auto)
			{
				OutValue += FString::Printf(TEXT(" (%s)"),
					*AODVehiclePawn::InputDeviceModeToString(Pawn->GetActiveInputDevice()));
			}
		}
		break;
	}
	case RowSteerDeadzone:
		OutLabel = TEXT("Steering Deadzone");
		OutValue = Percent(FODInputConfig::GetSteeringDeadzone());
		break;
	case RowSteerSensitivity:
		OutLabel = TEXT("Steering Response");
		OutValue = FString::Printf(TEXT("%.2f"), FODInputConfig::GetSteeringSensitivity());
		break;
	case RowPedalDeadzone:
		OutLabel = TEXT("Pedal Deadzone");
		OutValue = Percent(FODInputConfig::GetThrottleBrakeDeadzone());
		break;
	case RowBindings:
		OutLabel = TEXT("Key Bindings");
		break;
	case RowClose:
		OutLabel = TEXT("Close");
		break;
	default:
		OutLabel.Empty();
		break;
	}
}
