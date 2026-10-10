#include "ODTuningManager.h"

#include "Kismet/GameplayStatics.h"
#include "Misc/CommandLine.h"
#include "TimerManager.h"
#include "ODTuningPreset.h"
#include "ODVehicleDef.h"
#include "ODVehicleMovement.h"
#include "ODVehiclePawn.h"

AODTuningManager::AODTuningManager()
{
	PrimaryActorTick.bCanEverTick = false;
}

void AODTuningManager::BeginPlay()
{
	Super::BeginPlay();
	// setups are garage-only (2026-09-25, Taylor) - no in-drive T key
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
	// Relative axle trims over the car's physical tire grip (compound MuPeak,
	// load sensitivity, weight, aero - see TickTires). The old flat x4
	// GripBoost is gone: it hid tire-unlimited brakes and spun-up rears.
	Move->FrontGripScale = Preset->FrontFrictionScale;
	Move->RearGripScale = Preset->RearFrictionScale;

	// Preset downforce is relative to the street 1.1 baseline, scaled onto
	// the active car's own UODVehicleDef::DownforceCoeff (2026-09-28: per-car
	// aero - Concept/LMP/Super carry more; replaces the old GT-only x4 hack).
	float CarDownforce = 1.1f;
	float CarComZ = 10.0f;
	if (const AODVehiclePawn* VehiclePawn = Cast<AODVehiclePawn>(Car))
	{
		const auto& Defs = VehiclePawn->GetVehicleDefs();
		const int32 Idx = VehiclePawn->GetVehicleIndex();
		if (Defs.IsValidIndex(Idx) && Defs[Idx])
		{
			CarDownforce = Defs[Idx]->DownforceCoeff;
			CarComZ = Defs[Idx]->ComLocalZ;
		}
	}
	Move->DownforceCoeff = CarDownforce * Preset->Downforce / 1.1f;
	// preset COM heights were tuned around the template's 42 cm street value;
	// map them as deltas onto the active car's own COM height
	Move->ComLocalZ = CarComZ + (Preset->CenterOfMassZ - 42.0f);

	Move->OnRoadGrip = Preset->OnRoadGrip;
	Move->OffRoadGrip = Preset->OffRoadGrip;
	Move->SlipAngleAddDeg = Preset->SlipAngleAddDeg;
	Move->SlideFracMin = Preset->SlideFracMin;
	Move->TractionMode = Preset->DefaultTractionMode;
	Move->EscSlideAllowLowDeg = Preset->EscSlideAllowLowDeg;
	Move->SteerLockScale = Preset->SteerLockScale;
	Move->RearBrakeScale = Preset->RearBrakeScale;
	Move->HandbrakeScale = Preset->HandbrakeScale;
	Move->TopSpeedCapKph = Preset->TopSpeedCapKph;
	Move->TireDiameterScale = Preset->TireDiameterScale;
	Move->CamberDeg = Preset->CamberDeg;
	Move->FrontTireWidthScale = Preset->FrontTireWidthScale;
	Move->RearTireWidthScale = Preset->RearTireWidthScale;
	Move->MarkBodyDirty();

	CurrentPresetName = Preset->DisplayName;
	UE_LOG(LogTemp, Display, TEXT("ODTuningManager: applied '%s'"),
		*Preset->DisplayName);
}
