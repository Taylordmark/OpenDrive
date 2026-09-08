#include "ODPauseMenu.h"

#include "Components/InputComponent.h"
#include "Containers/Ticker.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Misc/CommandLine.h"
#include "UnrealClient.h"
#include "ODCreditsMenu.h"
#include "ODMainMenu.h"
#include "ODSettingsMenu.h"
#include "ODVehicleMovement.h"

namespace
{
// gearbox character over the 1.7 shift-controller params - applied once at
// BeginPlay to keep the current shift feel; no menu row (Taylor: pause menu
// changes ABS/traction only).
struct FGearboxDef
{
	const TCHAR* Name;
	float ShiftTime, UpWot, UpCruise, KickThrottle, KickTarget;
};
const FGearboxDef Gearboxes[] = {
	{TEXT("Comfort"), 0.22f, 6200.0f, 2600.0f, 0.72f, 5400.0f},
	{TEXT("Sport"),   0.09f, 6650.0f, 3300.0f, 0.55f, 6100.0f},
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
	auto Bind = [&](const FKey& Key, void (AODPauseMenu::*Fn)(), bool bConsume)
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

	// keep the current shift feel now that the gearbox row is gone
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
	// Settings/Credits back out one level first (Escape), same as any other
	// modal-inside-a-modal convention - see this class's header comment.
	if (bSettingsOpen)
	{
		bSettingsOpen = false;
		return;
	}
	if (bCreditsOpen)
	{
		bCreditsOpen = false;
		return;
	}
	// the front-end menu owns Escape while it's up (it also binds Escape); don't
	// pop the pause overlay under it
	if (const AODMainMenu* Main = FindManager<AODMainMenu>(GetWorld());
		Main && Main->IsActive())
	{
		return;
	}
	bOpen = !bOpen;
	Selected = 0;
	UGameplayStatics::SetGamePaused(this, bOpen);
}

void AODPauseMenu::Up()
{
	if (bSettingsOpen)
	{
		if (SettingsMenu)
		{
			SettingsMenu->MoveSelection(-1);
		}
		return;
	}
	if (bCreditsOpen)
	{
		if (CreditsMenu)
		{
			CreditsMenu->Scroll(-1);
		}
		return;
	}
	if (bOpen)
	{
		Selected = (Selected + NumRows() - 1) % NumRows();
	}
}

void AODPauseMenu::Down()
{
	if (bSettingsOpen)
	{
		if (SettingsMenu)
		{
			SettingsMenu->MoveSelection(+1);
		}
		return;
	}
	if (bCreditsOpen)
	{
		if (CreditsMenu)
		{
			CreditsMenu->Scroll(+1);
		}
		return;
	}
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
	if (bSettingsOpen)
	{
		if (SettingsMenu)
		{
			if (SettingsMenu->GetSelected() == SettingsMenu->NumRows() - 1)   // "Close" row
			{
				bSettingsOpen = false;
			}
			else
			{
				SettingsMenu->AdjustSelected(+1);   // Enter cycles a value row, like the row list below
			}
		}
		return;
	}
	if (bCreditsOpen)
	{
		bCreditsOpen = false;   // no rows to activate - Enter also just backs out
		return;
	}
	if (!bOpen)
	{
		return;
	}
	if (Selected == 0)                    // Resume
	{
		Toggle();
	}
	else if (Selected == 3)               // Garage (car/engine/drivetrain/tuning, in place)
	{
		if (AODMainMenu* Main = FindManager<AODMainMenu>(GetWorld()))
		{
			bOpen = false;                // hand the screen to the garage
			Main->OpenGarage();           // keeps the game paused; "Drive"/esc resumes here
		}
	}
	else if (Selected == 4)               // Settings
	{
		EnsureSettingsMenu();
		bSettingsOpen = true;
	}
	else if (Selected == 5)               // Credits
	{
		EnsureCreditsMenu();
		bCreditsOpen = true;
	}
	else if (Selected == 6)               // Main Menu (car/location front-end)
	{
		if (AODMainMenu* Main = FindManager<AODMainMenu>(GetWorld()))
		{
			bOpen = false;                // hand the screen to the front-end
			Main->OpenMenu();             // keeps the game paused
		}
	}
	else if (Selected == 7)               // Quit Game
	{
		UKismetSystemLibrary::QuitGame(this,
			UGameplayStatics::GetPlayerController(this, 0),
			EQuitPreference::Quit, false);
	}
	else
	{
		Adjust(+1);                       // Enter cycles the assist rows too
	}
}

void AODPauseMenu::Adjust(int32 Dir)
{
	if (bSettingsOpen)
	{
		if (SettingsMenu)
		{
			SettingsMenu->AdjustSelected(Dir);
		}
		return;
	}
	if (bCreditsOpen)
	{
		return;   // credits has no adjustable rows, only scroll (Up/Down above)
	}
	if (!bOpen)
	{
		return;
	}
	switch (Selected)
	{
	case 1:  // traction: High -> Low -> Off
		if (UODVehicleMovement* D = FindMovement())
		{
			D->TractionMode = (EODTractionMode)(((int32)D->TractionMode + Dir + 3) % 3);
			UE_LOG(LogTemp, Display, TEXT("ODPauseMenu: traction '%s'"),
				UODVehicleMovement::TractionModeName(D->TractionMode));
		}
		break;
	case 2:  // ABS on/off
		if (UODVehicleMovement* D = FindMovement())
		{
			D->bAbsEnabled = !D->bAbsEnabled;
			UE_LOG(LogTemp, Display, TEXT("ODPauseMenu: ABS %s"),
				D->bAbsEnabled ? TEXT("on") : TEXT("off"));
		}
		break;
	default:
		break;
	}
}

UODVehicleMovement* AODPauseMenu::FindMovement() const
{
	APawn* Car = UGameplayStatics::GetPlayerPawn(GetWorld(), 0);
	return Car ? Car->FindComponentByClass<UODVehicleMovement>() : nullptr;
}

void AODPauseMenu::ApplyGearbox()
{
	if (UODVehicleMovement* D = FindMovement())
	{
		const FGearboxDef& G = Gearboxes[GearboxIdx];
		D->ShiftTime = G.ShiftTime;
		D->UpshiftRpmAtWot = G.UpWot;
		D->UpshiftRpmAtCruise = G.UpCruise;
		D->KickdownThrottle = G.KickThrottle;
		D->KickdownTargetRpm = G.KickTarget;
		UE_LOG(LogTemp, Display, TEXT("ODPauseMenu: gearbox init '%s'"), G.Name);
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
		OutLabel = TEXT("Traction");
		OutValue = TEXT("?");
		if (const UODVehicleMovement* D = FindMovement())
		{
			OutValue = UODVehicleMovement::TractionModeName(D->TractionMode);
		}
		break;
	case 2:
		OutLabel = TEXT("ABS");
		OutValue = TEXT("?");
		if (const UODVehicleMovement* D = FindMovement())
		{
			OutValue = D->bAbsEnabled ? TEXT("On") : TEXT("Off");
		}
		break;
	case 3:
		OutLabel = TEXT("Garage");
		OutValue.Empty();
		break;
	case 4:
		OutLabel = TEXT("Settings");
		OutValue.Empty();
		break;
	case 5:
		OutLabel = TEXT("Credits");
		OutValue.Empty();
		break;
	case 6:
		OutLabel = TEXT("Main Menu");
		OutValue.Empty();
		break;
	case 7:
		OutLabel = TEXT("Quit Game");
		OutValue.Empty();
		break;
	default:
		OutLabel.Empty();
		OutValue.Empty();
		break;
	}
}

AODSettingsMenu* AODPauseMenu::EnsureSettingsMenu()
{
	if (SettingsMenu)
	{
		return SettingsMenu;
	}
	if (AODSettingsMenu* Found = FindManager<AODSettingsMenu>(GetWorld()))
	{
		SettingsMenu = Found;
		return SettingsMenu;
	}
	if (UWorld* World = GetWorld())
	{
		SettingsMenu = World->SpawnActor<AODSettingsMenu>();
	}
	return SettingsMenu;
}

AODCreditsMenu* AODPauseMenu::EnsureCreditsMenu()
{
	if (CreditsMenu)
	{
		return CreditsMenu;
	}
	if (AODCreditsMenu* Found = FindManager<AODCreditsMenu>(GetWorld()))
	{
		CreditsMenu = Found;
		return CreditsMenu;
	}
	if (UWorld* World = GetWorld())
	{
		CreditsMenu = World->SpawnActor<AODCreditsMenu>();
	}
	return CreditsMenu;
}
