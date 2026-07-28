#include "ODTuningManager.h"

#include "Components/InputComponent.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/CommandLine.h"
#include "TimerManager.h"
#include "ODTuningPreset.h"
#include "ODVehicleMovement.h"

AODTuningManager::AODTuningManager()
{
	PrimaryActorTick.bCanEverTick = false;
}

void AODTuningManager::BeginPlay()
{
	Super::BeginPlay();
	if (APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0))
	{
		EnableInput(PC);
		if (InputComponent)
		{
			InputComponent->BindKey(EKeys::T, IE_Pressed, this,
				&AODTuningManager::CyclePreset);
		}
	}

	// headless verification: `-odtune=5` cycles a preset every 5 s
	float Interval = 0.0f;
	if (FParse::Value(FCommandLine::Get(), TEXT("odtune="), Interval)
		&& Interval > 0.0f)
	{
		FTimerHandle Handle;
		GetWorldTimerManager().SetTimer(Handle, this,
			&AODTuningManager::CyclePreset, Interval, true, Interval);
	}
}

void AODTuningManager::CyclePreset()
{
	if (Presets.Num() > 0)
	{
		ApplyPresetByIndex((PresetIndex + 1) % Presets.Num());
	}
}

void AODTuningManager::ApplyPresetByIndex(int32 Index)
{
	if (Presets.IsValidIndex(Index))
	{
		PresetIndex = Index;
		ApplyPreset(Presets[Index]);
	}
}

void AODTuningManager::ApplyPreset(UODTuningPreset* Preset)
{
	APawn* Car = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Preset || !Car)
	{
		return;
	}
	auto* Move = Car->FindComponentByClass<UODVehicleMovement>();
	if (!Move)
	{
		return;
	}

	// presets are scales/deltas over the component's own C++ defaults, so
	// no baseline capture is needed - the fields below never accumulate
	Move->DampingRatio = Preset->DampingRatio;
	Move->SpringRateScale = Preset->SpringRateScale;
	Move->RideHeightDelta = Preset->RideHeightDelta;
	Move->TorqueScale = Preset->TorqueScale;
	Move->FrontGripScale = Preset->FrontFrictionScale;
	Move->RearGripScale = Preset->RearFrictionScale;
	Move->DownforceCoeff = Preset->Downforce;
	// preset COM heights were tuned around the template's 42 cm street value;
	// map them as deltas onto the new body's 10 cm default (stage 3 retunes)
	Move->ComLocalZ = 10.0f + (Preset->CenterOfMassZ - 42.0f);
	Move->MarkBodyDirty();

	CurrentPresetName = Preset->DisplayName;
	SwapShownAt = GetWorld()->GetTimeSeconds();
	UE_LOG(LogTemp, Display, TEXT("ODTuningManager: applied '%s'"),
		*Preset->DisplayName);
}
