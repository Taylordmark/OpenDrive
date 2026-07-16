#include "ODTuningManager.h"

#include "ChaosVehicleWheel.h"
#include "ChaosWheeledVehicleMovementComponent.h"
#include "Components/InputComponent.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/CommandLine.h"
#include "TimerManager.h"
#include "ODSurfaceTractionManager.h"
#include "ODTuningPreset.h"
#include "WheeledVehiclePawn.h"

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
	if (Presets.Num() == 0)
	{
		return;
	}
	PresetIndex = (PresetIndex + 1) % Presets.Num();
	ApplyPreset(Presets[PresetIndex]);
}

void AODTuningManager::ApplyPreset(UODTuningPreset* Preset)
{
	auto* Car = Cast<AWheeledVehiclePawn>(UGameplayStatics::GetPlayerPawn(this, 0));
	if (!Preset || !Car)
	{
		return;
	}
	auto* Move = Cast<UChaosWheeledVehicleMovementComponent>(
		Car->GetVehicleMovementComponent());
	if (!Move)
	{
		return;
	}

	// capture this car's stock values once; presets are deltas over these
	FBaseline& Base = Baselines.FindOrAdd(Car);
	if (Base.Friction.Num() != Move->Wheels.Num())
	{
		Base = FBaseline{};
		for (const UChaosVehicleWheel* W : Move->Wheels)
		{
			Base.SpringRate.Add(W->SpringRate);
			Base.MaxRaise.Add(W->SuspensionMaxRaise);
			Base.MaxDrop.Add(W->SuspensionMaxDrop);
			Base.Friction.Add(W->FrictionForceMultiplier);
		}
		Base.MaxTorque = Move->EngineSetup.MaxTorque;
	}

	for (int32 i = 0; i < Move->Wheels.Num(); ++i)
	{
		UChaosVehicleWheel* W = Move->Wheels[i];
		const bool bFront = W->GetAxleType() == EAxleType::Front || i < 2;
		W->SuspensionDampingRatio = Preset->DampingRatio;
		W->SpringRate = Base.SpringRate[i] * Preset->SpringRateScale;
		W->SuspensionMaxRaise = Base.MaxRaise[i] + Preset->RideHeightDelta;
		W->SuspensionMaxDrop = Base.MaxDrop[i] + Preset->RideHeightDelta;
		W->FrictionForceMultiplier = Base.Friction[i]
			* (bFront ? Preset->FrontFrictionScale : Preset->RearFrictionScale);
	}
	Move->EngineSetup.MaxTorque = Base.MaxTorque * Preset->TorqueScale;
	Move->DownforceCoefficient = Preset->Downforce;
	Move->bEnableCenterOfMassOverride = true;
	Move->CenterOfMassOverride = FVector(0, 0, Preset->CenterOfMassZ);

	// rebuild the vehicle sim so setup changes actually take
	Move->RecreatePhysicsState();

	// traction manager caches per-wheel base friction - make it re-read
	for (TActorIterator<AODSurfaceTractionManager> It(GetWorld()); It; ++It)
	{
		It->InvalidateBaseFriction();
	}

	CurrentPresetName = Preset->DisplayName;
	SwapShownAt = GetWorld()->GetTimeSeconds();
	UE_LOG(LogTemp, Display, TEXT("ODTuningManager: applied '%s'"),
		*Preset->DisplayName);
}
