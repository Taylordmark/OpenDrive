#include "ODPauseMenu.h"

#include "Components/InputComponent.h"
#include "Containers/Ticker.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Misc/CommandLine.h"
#include "UnrealClient.h"
#include "ODDrivetrainManager.h"
#include "ODEngineSynth.h"
#include "ODTuningManager.h"
#include "ODVehicleAudio.h"

namespace
{
// gearbox characters over the 1.7 shift-controller params
struct FGearboxDef
{
	const TCHAR* Name;
	float ShiftTime, UpWot, UpCruise, KickThrottle, KickTarget;
};
const FGearboxDef Gearboxes[] = {
	{TEXT("Comfort"), 0.22f, 6200.0f, 2600.0f, 0.72f, 5400.0f},
	{TEXT("Sport"),   0.09f, 6650.0f, 3300.0f, 0.55f, 6100.0f},
};

// engine characters over the drivetrain torque curve + synth firing order
struct FEngineDef
{
	const TCHAR* Name;
	int32 Cylinders;
	float PeakNm, PeakRpm, MaxRpm, LimiterRpm, Inertia;
};
const FEngineDef Engines[] = {
	{TEXT("V8 Muscle"),   8, 420.0f, 4600.0f, 7000.0f, 6850.0f, 0.25f},
	{TEXT("V6 Sport"),    6, 335.0f, 5500.0f, 7900.0f, 7750.0f, 0.17f},
	{TEXT("V10 Screamer"), 10, 480.0f, 6800.0f, 9000.0f, 8850.0f, 0.14f},
	{TEXT("V12 GT"),      12, 580.0f, 5800.0f, 8600.0f, 8450.0f, 0.19f},
};

// exhaust characters over the 5.2 synth params
struct FExhaustDef
{
	const TCHAR* Name;
	float Loudness, Rasp, MufflerCutoff;
};
const FExhaustDef Exhausts[] = {
	{TEXT("Stock"), 0.70f, 0.45f, 2600.0f},
	{TEXT("Sport"), 0.85f, 0.62f, 3800.0f},
	{TEXT("Open"),  1.00f, 0.80f, 6800.0f},
};

template <typename T>
T* FindManager(UWorld* World)
{
	for (TActorIterator<T> It(World); It; ++It)
	{
		return *It;
	}
	return nullptr;
}
}

AODPauseMenu::AODPauseMenu()
{
	PrimaryActorTick.bCanEverTick = false;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODPauseMenu::BeginPlay()
{
	Super::BeginPlay();
	APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0);
	if (!PC)
	{
		return;
	}
	EnableInput(PC);
	if (!InputComponent)
	{
		return;
	}

	// menu must work while the game is paused; nav keys don't consume so
	// arrow-key driving is unaffected while the menu is closed
	auto Bind = [&](const FKey& Key, void (AODPauseMenu::*Fn)(),
	                bool bConsume)
	{
		FInputKeyBinding& B = InputComponent->BindKey(Key, IE_Pressed, this, Fn);
		B.bExecuteWhenPaused = true;
		B.bConsumeInput = bConsume;
	};
	Bind(EKeys::Escape, &AODPauseMenu::Toggle, true);
	Bind(EKeys::P, &AODPauseMenu::Toggle, false);
	Bind(EKeys::Up, &AODPauseMenu::Up, false);
	Bind(EKeys::Down, &AODPauseMenu::Down, false);
	Bind(EKeys::Left, &AODPauseMenu::Left, false);
	Bind(EKeys::Right, &AODPauseMenu::Right, false);
	Bind(EKeys::Enter, &AODPauseMenu::Activate, false);

	// menu rows must show the truth: V8/Stock rows equal the drivetrain and
	// synth defaults already; the gearbox row applies its default here
	ApplyGearbox();

	// headless QA: `-odmenu=8` opens the menu at 8 s and screenshots 1.5 s
	// later. Core ticker, not a world timer - those freeze once paused.
	float MenuAt = -1.0f;
	FParse::Value(FCommandLine::Get(), TEXT("odmenu="), MenuAt);
	if (MenuAt > 0.0f)
	{
		FTSTicker::GetCoreTicker().AddTicker(
			FTickerDelegate::CreateWeakLambda(this, [this](float)
			{
				if (!bOpen)
				{
					Toggle();
					UE_LOG(LogTemp, Display, TEXT("ODPauseMenu: test-open"));
				}
				return false;
			}), MenuAt);
		FTSTicker::GetCoreTicker().AddTicker(
			FTickerDelegate::CreateWeakLambda(this, [](float)
			{
				FScreenshotRequest::RequestScreenshot(false);
				return false;
			}), MenuAt + 1.5f);
	}
}

void AODPauseMenu::Toggle()
{
	bOpen = !bOpen;
	Selected = 0;
	UGameplayStatics::SetGamePaused(this, bOpen);
}

void AODPauseMenu::Up()
{
	if (bOpen)
	{
		Selected = (Selected + NumRows() - 1) % NumRows();
	}
}

void AODPauseMenu::Down()
{
	if (bOpen)
	{
		Selected = (Selected + 1) % NumRows();
	}
}

void AODPauseMenu::Left()
{
	Adjust(-1);
}

void AODPauseMenu::Right()
{
	Adjust(+1);
}

void AODPauseMenu::Activate()
{
	if (!bOpen)
	{
		return;
	}
	if (Selected == 0)                    // Resume
	{
		Toggle();
	}
	else if (Selected == 5)               // Quit Game
	{
		UKismetSystemLibrary::QuitGame(this,
			UGameplayStatics::GetPlayerController(this, 0),
			EQuitPreference::Quit, false);
	}
	else
	{
		Adjust(+1);                       // Enter cycles value rows too
	}
}

void AODPauseMenu::Adjust(int32 Dir)
{
	if (!bOpen)
	{
		return;
	}
	switch (Selected)
	{
	case 1:  // tuning preset (kept in sync with the T key via the manager)
		if (AODTuningManager* Tuning = FindManager<AODTuningManager>(GetWorld()))
		{
			const int32 Num = Tuning->Presets.Num();
			if (Num > 0)
			{
				const int32 Cur = Tuning->GetPresetIndex();
				Tuning->ApplyPresetByIndex(
					Cur < 0 ? (Dir > 0 ? 0 : Num - 1) : (Cur + Dir + Num) % Num);
			}
		}
		break;
	case 2:
		GearboxIdx = (GearboxIdx + Dir + UE_ARRAY_COUNT(Gearboxes))
			% UE_ARRAY_COUNT(Gearboxes);
		ApplyGearbox();
		break;
	case 3:
		EngineIdx = (EngineIdx + Dir + UE_ARRAY_COUNT(Engines))
			% UE_ARRAY_COUNT(Engines);
		ApplyEngine();
		break;
	case 4:
		ExhaustIdx = (ExhaustIdx + Dir + UE_ARRAY_COUNT(Exhausts))
			% UE_ARRAY_COUNT(Exhausts);
		ApplyExhaust();
		break;
	default:
		break;
	}
}

void AODPauseMenu::ApplyGearbox()
{
	if (AODDrivetrainManager* D = FindManager<AODDrivetrainManager>(GetWorld()))
	{
		const FGearboxDef& G = Gearboxes[GearboxIdx];
		D->ShiftTime = G.ShiftTime;
		D->UpshiftRpmAtWot = G.UpWot;
		D->UpshiftRpmAtCruise = G.UpCruise;
		D->KickdownThrottle = G.KickThrottle;
		D->KickdownTargetRpm = G.KickTarget;
		UE_LOG(LogTemp, Display, TEXT("ODPauseMenu: gearbox '%s'"), G.Name);
	}
}

void AODPauseMenu::ApplyEngine()
{
	const FEngineDef& E = Engines[EngineIdx];
	if (AODDrivetrainManager* D = FindManager<AODDrivetrainManager>(GetWorld()))
	{
		D->PeakTorqueNm = E.PeakNm;
		D->PeakTorqueRpm = E.PeakRpm;
		D->MaxRpm = E.MaxRpm;
		D->LimiterRpm = E.LimiterRpm;
		D->EngineInertia = E.Inertia;
	}
	if (AODVehicleAudio* Audio = FindManager<AODVehicleAudio>(GetWorld()))
	{
		if (UODEngineSynth* Synth = Audio->GetEngineSynth())
		{
			Synth->Cylinders = E.Cylinders;
		}
	}
	UE_LOG(LogTemp, Display, TEXT("ODPauseMenu: engine '%s'"), E.Name);
}

void AODPauseMenu::ApplyExhaust()
{
	if (AODVehicleAudio* Audio = FindManager<AODVehicleAudio>(GetWorld()))
	{
		if (UODEngineSynth* Synth = Audio->GetEngineSynth())
		{
			const FExhaustDef& X = Exhausts[ExhaustIdx];
			Synth->Loudness = X.Loudness;
			Synth->Rasp = X.Rasp;
			Synth->MufflerCutoff = X.MufflerCutoff;
			UE_LOG(LogTemp, Display, TEXT("ODPauseMenu: exhaust '%s'"), X.Name);
		}
	}
}

void AODPauseMenu::GetRow(int32 Row, FString& OutLabel, FString& OutValue) const
{
	switch (Row)
	{
	case 0:
		OutLabel = TEXT("Resume");
		OutValue.Empty();
		break;
	case 1:
	{
		OutLabel = TEXT("Tuning");
		OutValue = TEXT("(stock)");
		if (const AODTuningManager* Tuning =
				FindManager<AODTuningManager>(GetWorld());
			Tuning && !Tuning->CurrentPresetName.IsEmpty())
		{
			OutValue = Tuning->CurrentPresetName;
		}
		break;
	}
	case 2:
		OutLabel = TEXT("Gearbox");
		OutValue = Gearboxes[GearboxIdx].Name;
		break;
	case 3:
		OutLabel = TEXT("Engine");
		OutValue = Engines[EngineIdx].Name;
		break;
	case 4:
		OutLabel = TEXT("Exhaust");
		OutValue = Exhausts[ExhaustIdx].Name;
		break;
	case 5:
		OutLabel = TEXT("Quit Game");
		OutValue.Empty();
		break;
	default:
		OutLabel.Empty();
		OutValue.Empty();
		break;
	}
}
