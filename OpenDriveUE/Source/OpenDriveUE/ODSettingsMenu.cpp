#include "ODSettingsMenu.h"

#include "EngineUtils.h"
#include "GameFramework/GameUserSettings.h"
#include "GenericPlatform/GenericApplication.h"
#include "HAL/IConsoleManager.h"
#include "HAL/PlatformApplicationMisc.h"
#include "Kismet/GameplayStatics.h"

#include "ODAmbientSoundManager.h"
#include "ODAssetDetail.h"
#include "ODRebindMenu.h"
#include "ODVehicleAudio.h"
#include "ODVehiclePawn.h"

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
T* FindActor(UWorld* World)
{
	for (TActorIterator<T> It(World); It; ++It)
	{
		return *It;
	}
	return nullptr;
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
	Selected = (Selected + Dir + NumRows()) % NumRows();
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
	switch (Selected)
	{
	case 0: ApplyResolution(Dir); break;
	case 1: ApplyWindowMode(Dir); break;
	case 2: ApplyQuality(Dir); break;
	case 3: ApplyUpscaler(Dir); break;
	case 4: ApplyScreenPercent(Dir); break;
	case 5: ApplyVSync(); break;
	case 6: ApplyAssetDetail(Dir); break;
	case 7:
		MasterVolume = FMath::Clamp(MasterVolume + Dir * 0.05f, 0.0f, 1.0f);
		ApplyAudioVolumes();
		SaveAudioConfig();
		break;
	case 8:
		VehicleVolume = FMath::Clamp(VehicleVolume + Dir * 0.05f, 0.0f, 1.0f);
		ApplyAudioVolumes();
		SaveAudioConfig();
		break;
	case 9:
		AmbientVolume = FMath::Clamp(AmbientVolume + Dir * 0.05f, 0.0f, 1.0f);
		ApplyAudioVolumes();
		SaveAudioConfig();
		break;
	case 10: ApplyInputDevice(Dir); break;
	default: break;   // row 11 "Controls" / row 12 "Close" - no value, handled by the parent menu's Activate()
	}
}

void AODSettingsMenu::OpenRebind()
{
	EnsureRebindMenu();
	bRebindOpen = true;
}

AODRebindMenu* AODSettingsMenu::EnsureRebindMenu()
{
	if (RebindMenu)
	{
		return RebindMenu;
	}
	if (AODRebindMenu* Found = FindActor<AODRebindMenu>(GetWorld()))
	{
		RebindMenu = Found;
		return RebindMenu;
	}
	if (UWorld* World = GetWorld())
	{
		RebindMenu = World->SpawnActor<AODRebindMenu>();
	}
	return RebindMenu;
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

// ---- audio --------------------------------------------------------------

void AODSettingsMenu::ApplyAudioVolumes() const
{
	// Single application points (see AODVehicleAudio::SetVehicleVolumeScale
	// and AODAmbientSoundManager::SetVolumeScale's own comments) - each is a
	// plain float multiply at the actor's one existing volume-application
	// spot, not a per-frame cost added here: this only runs on load and on a
	// slider nudge, not every tick.
	if (AODVehicleAudio* Audio = FindActor<AODVehicleAudio>(GetWorld()))
	{
		Audio->SetVehicleVolumeScale(MasterVolume * VehicleVolume);
	}
	if (AODAmbientSoundManager* Ambient = FindActor<AODAmbientSoundManager>(GetWorld()))
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
	GConfig->Flush(false, GGameUserSettingsIni);
}

void AODSettingsMenu::LoadGraphicsConfig()
{
	GConfig->GetInt(GraphicsConfigSection, TEXT("UpscalerIdx"), UpscalerIdx, GGameUserSettingsIni);
	GConfig->GetInt(GraphicsConfigSection, TEXT("ScreenPercent"), ScreenPercent, GGameUserSettingsIni);
	AssetDetail = ODAssetDetail::Level();
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
	switch (Row)
	{
	case 0:
	{
		OutLabel = TEXT("Resolution");
		const int32 Idx = CurrentResolutionIdx();
		if (Idx == NativeResIdx)
		{
			const FIntPoint Native = NativeResolution();
			OutValue = FString::Printf(TEXT("Native (%dx%d)"), Native.X, Native.Y);
		}
		else
		{
			OutValue = FString::Printf(TEXT("%dx%d"), Resolutions[Idx].W, Resolutions[Idx].H);
		}
		break;
	}
	case 1:
		OutLabel = TEXT("Window Mode");
		OutValue = WindowModes[CurrentWindowModeIdx()].Name;
		break;
	case 2:
	{
		OutLabel = TEXT("Quality");
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
	case 3:
		OutLabel = TEXT("Upscaler");
		OutValue = (UpscalerIdx == 1) ? TEXT("TSR") : TEXT("Off (TAA)");
		break;
	case 4:
		OutLabel = TEXT("  Screen %");
		OutValue = FString::Printf(TEXT("%d%%"), ScreenPercent);
		break;
	case 5:
	{
		OutLabel = TEXT("VSync");
		OutValue = TEXT("?");
		if (const UGameUserSettings* GS = UGameUserSettings::GetGameUserSettings())
		{
			OutValue = GS->IsVSyncEnabled() ? TEXT("On") : TEXT("Off");
		}
		break;
	}
	case 6:
		OutLabel = TEXT("Asset Detail");
		OutValue = AssetDetailNames[AssetDetail];
		if (AssetDetail != AssetDetailAtLoad)
		{
			OutValue += TEXT(" (next load)");
		}
		break;
	case 7:
		OutLabel = TEXT("Master Volume");
		OutValue = FString::Printf(TEXT("%d%%"), FMath::RoundToInt(MasterVolume * 100.0f));
		break;
	case 8:
		OutLabel = TEXT("Vehicle Volume");
		OutValue = FString::Printf(TEXT("%d%%"), FMath::RoundToInt(VehicleVolume * 100.0f));
		break;
	case 9:
		OutLabel = TEXT("Ambient Volume");
		OutValue = FString::Printf(TEXT("%d%%"), FMath::RoundToInt(AmbientVolume * 100.0f));
		break;
	case 10:
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
	case 11:
		OutLabel = TEXT("Controls");
		OutValue.Empty();
		break;
	case 12:
		OutLabel = TEXT("Close");
		OutValue.Empty();
		break;
	default:
		OutLabel.Empty();
		OutValue.Empty();
		break;
	}
}
