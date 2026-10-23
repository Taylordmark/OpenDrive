#include "ODPauseMenu.h"

#include "Components/InputComponent.h"
#include "Containers/Ticker.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Misc/CommandLine.h"
#include "UnrealClient.h"
#include "ODInputConfig.h"
#include "ODMainMenu.h"
#include "ODMenuTabs.h"
#include "ODPhotoMode.h"
#include "ODRaceHUD.h"
#include "ODRaceManager.h"
#include "ODSettingsMenu.h"
#include "ODVehicleMovement.h"
#include "ODWheelDevice.h"

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
	// Escape is a fixed, always-on cancel/pause key - deliberately NOT
	// rebindable (Arc 4.1, SS7.32) so a player can never lock themselves out
	// of pausing. Pause's OTHER key is the rebindable primary - see
	// RefreshPauseKeyBinding, called here and again live by AODRebindMenu.
	Bind(EKeys::Escape, &AODPauseMenu::Toggle, true);
	RefreshPauseKeyBinding();
	Bind(EKeys::Up, &AODPauseMenu::Up, false);
	Bind(EKeys::Down, &AODPauseMenu::Down, false);
	Bind(EKeys::Left, &AODPauseMenu::Left, false);
	Bind(EKeys::Right, &AODPauseMenu::Right, false);
	Bind(EKeys::Enter, &AODPauseMenu::Activate, false);
	Bind(EKeys::Q, &AODPauseMenu::PrevTab, false);
	Bind(EKeys::E, &AODPauseMenu::NextTab, false);
	Bind(EKeys::Gamepad_LeftShoulder, &AODPauseMenu::PrevTab, false);
	Bind(EKeys::Gamepad_RightShoulder, &AODPauseMenu::NextTab, false);

	// keep the current shift feel now that the gearbox row is gone
	ApplyGearbox();
	ODEnvironment::MaybeStartSelfCheck(GetWorld());
	AODPhotoMode::MaybeStartSelfCheck(GetWorld());

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

void AODPauseMenu::RefreshPauseKeyBinding()
{
	if (!InputComponent)
	{
		return;
	}
	if (LastPauseKey.IsValid())
	{
		InputComponent->KeyBindings.RemoveAll([this](const FInputKeyBinding& B)
		{
			return B.Chord.Key == LastPauseKey;
		});
		LastPauseKey = FKey();
	}
	const FKey PauseKey = FODInputConfig::GetKeyboardKey(EODInputAction::Pause);
	if (PauseKey.IsValid() && PauseKey != EKeys::Escape)
	{
		FInputKeyBinding& B = InputComponent->BindKey(PauseKey, IE_Pressed, this, &AODPauseMenu::Toggle);
		B.bExecuteWhenPaused = true;
		B.bConsumeInput = false;
		LastPauseKey = PauseKey;
	}
}

namespace
{
const FODMenuTab PauseTab = {TEXT("PAUSED"), AODPauseMenu::RowWorldMap, AODPauseMenu::RowResume};
const FODMenuTab EnvTab = {TEXT("ENVIRONMENT"), 0, ODEnvironment::NumRows};
const FODMenuTab PhotoTab = {TEXT("PHOTO"), 0, AODPhotoMode::RowCapture};
}

int32 AODPauseMenu::NumRows() const
{
	return bPhotoOpen ? AODPhotoMode::NumPhotoRows : bEnvOpen ? ODEnvironment::NumRows + 1 : NumPauseRows;
}

int32 AODPauseMenu::GetFooterRow() const
{
	return bPhotoOpen ? AODPhotoMode::RowCapture : bEnvOpen ? ODEnvironment::NumRows : RowResume;
}

void AODPauseMenu::ExitPhoto()
{
	if (bPhotoOpen && PhotoMode)
	{
		PhotoMode->Exit();
	}
	bPhotoOpen = false;
}

void AODPauseMenu::TogglePhotoMode()
{
	if (bPhotoOpen)
	{
		SetOpen(false);   // straight back to driving
		return;
	}
	if (!bOpen)
	{
		Toggle();         // same guards as the pause key (world map, front-end)
		if (!bOpen)
		{
			return;
		}
	}
	bSettingsOpen = false;
	bEnvOpen = false;
	if (!PhotoMode && GetWorld())
	{
		PhotoMode = GetWorld()->SpawnActor<AODPhotoMode>();
	}
	if (PhotoMode)
	{
		PhotoMode->Enter();
		bPhotoOpen = PhotoMode->IsActive();
		Selected = AODPhotoMode::RowFov;
	}
}

void AODPauseMenu::Toggle()
{
	// world map (SS7.37) is its own full-screen layer - Escape closes it first
	if (APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0))
	{
		if (AODRaceHUD* Hud = Cast<AODRaceHUD>(PC->GetHUD()); Hud && Hud->IsWorldMapOpen())
		{
			Hud->CloseWorldMap();
			return;
		}
	}
	if (bOpen)
	{
		Back();
		return;
	}
	// the front-end menu owns Escape while it's up (it also binds Escape); don't
	// pop the pause overlay under it
	if (const AODMainMenu* Main = FindManager<AODMainMenu>(GetWorld());
		Main && Main->IsActive())
	{
		return;
	}
	SetOpen(true);
}

void AODPauseMenu::SetOpen(bool bInOpen)
{
	ExitPhoto();
	bOpen = bInOpen;
	bSettingsOpen = false;
	bEnvOpen = false;
	bWheelLost = false;
	Selected = RowResume;
	if (bOpen)
	{
		FODWheelDevice::Rescan();   // the only device search besides startup and the input-type row
		if (AODRaceManager* Race = FindManager<AODRaceManager>(GetWorld()))
		{
			Race->RescanGhosts();   // picks up .odghost files dropped in since
		}
	}
	UGameplayStatics::SetGamePaused(this, bOpen);
	// before 2026-10-09 the cursor was never shown here, so the mouse did
	// nothing on this menu - same capture switch the front-end uses
	if (AODMainMenu* Main = FindManager<AODMainMenu>(GetWorld()))
	{
		Main->SetCursorVisible(bOpen);
	}
}

void AODPauseMenu::OpenForWheelLost()
{
	if (!bOpen)
	{
		SetOpen(true);
		bWheelLost = true;
	}
}

void AODPauseMenu::Back()
{
	if (bSettingsOpen)
	{
		if (SettingsMenu && SettingsMenu->Back())   // true == nothing left to back out of
		{
			bSettingsOpen = false;
		}
		return;
	}
	if (bPhotoOpen)
	{
		ExitPhoto();
		Selected = RowPhoto;
		return;
	}
	if (bEnvOpen)
	{
		bEnvOpen = false;
		Selected = RowEnvironment;
		return;
	}
	if (bOpen)
	{
		SetOpen(false);   // resume
	}
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
	if (bOpen)
	{
		Selected = ODMenuTabs::Step(bPhotoOpen ? PhotoTab : bEnvOpen ? EnvTab : PauseTab, GetFooterRow(), Selected, -1);
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
	if (bOpen)
	{
		Selected = ODMenuTabs::Step(bPhotoOpen ? PhotoTab : bEnvOpen ? EnvTab : PauseTab, GetFooterRow(), Selected, +1);
	}
}

void AODPauseMenu::SelectRow(int32 Row, int32 Col)
{
	if (bSettingsOpen)
	{
		if (SettingsMenu)
		{
			SettingsMenu->SelectRow(Row, Col);
		}
		return;
	}
	if (bOpen && Row >= 0 && Row < NumRows())
	{
		Selected = Row;
	}
}

void AODPauseMenu::SetTab(int32 Tab)
{
	if (bSettingsOpen && SettingsMenu)
	{
		SettingsMenu->SetTab(Tab);
	}
}

void AODPauseMenu::PrevTab()
{
	if (bSettingsOpen && SettingsMenu)
	{
		SettingsMenu->SwitchTab(-1);
	}
}

void AODPauseMenu::NextTab()
{
	if (bSettingsOpen && SettingsMenu)
	{
		SettingsMenu->SwitchTab(+1);
	}
}

void AODPauseMenu::Activate()
{
	if (bSettingsOpen)
	{
		if (SettingsMenu && SettingsMenu->Activate())   // true == its Close row
		{
			bSettingsOpen = false;
		}
		return;
	}
	if (!bOpen)
	{
		return;
	}
	if (bPhotoOpen && PhotoMode && Selected == GetFooterRow())
	{
		PhotoMode->Capture();
		return;
	}
	if (bEnvOpen || bPhotoOpen)
	{
		if (Selected == GetFooterRow())
		{
			Back();
		}
		else
		{
			Adjust(+1);   // Enter cycles a value row, like Settings
		}
		return;
	}
	switch (Selected)
	{
	case RowEnvironment:   // fun #7
		bEnvOpen = true;
		Selected = ODEnvironment::RowTime;
		break;
	case RowPhoto:         // fun #4
		TogglePhotoMode();
		break;
	case RowResume:
		SetOpen(false);
		break;
	case RowWorldMap:   // SS7.37
		if (APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0))
		{
			if (AODRaceHUD* Hud = Cast<AODRaceHUD>(PC->GetHUD()))
			{
				Hud->OpenWorldMap();      // hides this overlay, stays paused
			}
		}
		break;
	case RowRaceAgainst:   // share = copy files in/out of this folder
		IFileManager::Get().MakeDirectory(*FODGhostFile::Dir(), true);
		FPlatformProcess::ExploreFolder(*FODGhostFile::Dir());
		break;
	case RowSettings:
		bSettingsOpen = EnsureSettingsMenu() != nullptr;
		break;
	case RowMainMenu:   // front-end Main screen
		if (AODMainMenu* Main = FindManager<AODMainMenu>(GetWorld()))
		{
			bOpen = false;                // hand the screen to the front-end
			Main->OpenMenu();             // unpauses, like a fresh boot
		}
		break;
	case RowQuit:
		UKismetSystemLibrary::QuitGame(this,
			UGameplayStatics::GetPlayerController(this, 0),
			EQuitPreference::Quit, false);
		break;
	default:
		break;
	}
}

void AODPauseMenu::Adjust(int32 Dir)
{
	// Settings, Environment, Photo and the Race-against row take left/right
	if (bSettingsOpen && SettingsMenu)
	{
		SettingsMenu->AdjustSelected(Dir);
	}
	else if (bOpen && bPhotoOpen && PhotoMode)
	{
		PhotoMode->Adjust(Selected, Dir);
	}
	else if (bOpen && bEnvOpen)
	{
		ODEnvironment::Adjust(GetWorld(), Selected, Dir);
	}
	else if (bOpen && Selected == RowRaceAgainst)
	{
		if (AODRaceManager* Race = FindManager<AODRaceManager>(GetWorld()))
		{
			Race->CycleGhost(Dir);
		}
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
	if (bPhotoOpen && PhotoMode)
	{
		PhotoMode->GetRow(Row, OutLabel, OutValue);
		return;
	}
	if (bEnvOpen)
	{
		if (Row == GetFooterRow())
		{
			OutLabel = TEXT("Back");
			OutValue.Empty();
		}
		else
		{
			ODEnvironment::GetRow(GetWorld(), Row, OutLabel, OutValue);
		}
		return;
	}
	static const TCHAR* const Labels[NumPauseRows] = {
		TEXT("World Map"), TEXT("Environment"), TEXT("Photo Mode"), TEXT("Race against"), TEXT("Settings"), TEXT("Main Menu"), TEXT("Quit"), TEXT("Resume")};
	OutLabel = (Row >= 0 && Row < NumPauseRows) ? Labels[Row] : TEXT("");
	OutValue.Empty();
	if (Row == RowRaceAgainst)
	{
		const AODRaceManager* Race = FindManager<AODRaceManager>(GetWorld());
		OutValue = Race ? Race->GetGhostChoiceLabel() : TEXT("No race");
	}
}

AODSettingsMenu* AODPauseMenu::EnsureSettingsMenu()
{
	if (!SettingsMenu)
	{
		SettingsMenu = FindManager<AODSettingsMenu>(GetWorld());
	}
	if (!SettingsMenu && GetWorld())
	{
		SettingsMenu = GetWorld()->SpawnActor<AODSettingsMenu>();
	}
	return SettingsMenu;
}
