#include "ODDrivetrainManager.h"

#include "ChaosVehicleWheel.h"
#include "ChaosWheeledVehicleMovementComponent.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/CommandLine.h"
#include "WheeledVehiclePawn.h"

AODDrivetrainManager::AODDrivetrainManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODDrivetrainManager::Bind(AWheeledVehiclePawn* Car,
                                UChaosWheeledVehicleMovementComponent* Move)
{
	BoundCar = Car;

	// mirror the gear ratios the Chaos box was using
	GearRatios.Reset();
	for (const float R : Move->TransmissionSetup.ForwardGearRatios)
	{
		GearRatios.Add(R);
	}
	if (Move->TransmissionSetup.ReverseGearRatios.Num() > 0)
	{
		ReverseRatio = Move->TransmissionSetup.ReverseGearRatios[0];
	}
	FinalRatio = Move->TransmissionSetup.FinalRatio;
	Efficiency = Move->TransmissionSetup.TransmissionEfficiency;

	DrivenWheels.Reset();
	const EVehicleDifferential Diff = Move->DifferentialSetup.DifferentialType;
	for (int32 i = 0; i < Move->Wheels.Num(); ++i)
	{
		UChaosVehicleWheel* W = Move->Wheels[i];   // GetAxleType is non-const
		// the differential defines the drive type on the template car; the
		// per-wheel engine flag is the fallback
		const bool bDriven =
			Diff == EVehicleDifferential::AllWheelDrive ? true
			: Diff == EVehicleDifferential::FrontWheelDrive
				? W->GetAxleType() == EAxleType::Front
			: Diff == EVehicleDifferential::RearWheelDrive
				? W->GetAxleType() == EAxleType::Rear
			: W->bAffectedByEngine;
		if (bDriven)
		{
			DrivenWheels.Add(i);
		}
	}
	if (DrivenWheels.Num() == 0)          // unflagged setup: drive everything
	{
		for (int32 i = 0; i < Move->Wheels.Num(); ++i)
		{
			DrivenWheels.Add(i);
		}
	}

	// our sim replaces engine/transmission/diff; wheels and brakes stay Chaos
	Move->EnableMechanicalSim(false);
	Move->RecreatePhysicsState();

	EngineRpm = IdleRpm;
	Gear = 1;
	UE_LOG(LogTemp, Display,
		TEXT("ODDrivetrainManager: bound to %s (%d gears, %d driven wheels, "
		     "final %.2f)"),
		*Car->GetName(), GearRatios.Num(), DrivenWheels.Num(), FinalRatio);
}

float AODDrivetrainManager::TorqueCurve(float Rpm) const
{
	// normalized flat-ish V8 curve: soft off idle, peak, gentle fall to redline
	const float T = FMath::GetMappedRangeValueClamped(
		FVector2D(IdleRpm, PeakTorqueRpm), FVector2D(0.55f, 1.0f), Rpm);
	const float Fall = FMath::GetMappedRangeValueClamped(
		FVector2D(PeakTorqueRpm, MaxRpm), FVector2D(1.0f, 0.82f), Rpm);
	return PeakTorqueNm * TorqueScale * FMath::Min(T, Fall);
}

void AODDrivetrainManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	auto* Car = Cast<AWheeledVehiclePawn>(UGameplayStatics::GetPlayerPawn(this, 0));
	if (!Car)
	{
		return;
	}
	auto* Move = Cast<UChaosWheeledVehicleMovementComponent>(
		Car->GetVehicleMovementComponent());
	if (!Move || Move->Wheels.Num() == 0)
	{
		return;
	}
	if (BoundCar != Car)
	{
		Bind(Car, Move);
	}

	const float Throttle = FMath::Clamp(Move->GetThrottleInput(), 0.0f, 1.0f);
	const float Brake = FMath::Clamp(Move->GetBrakeInput(), 0.0f, 1.0f);
	const float FwdSpeed = Move->GetForwardSpeed();          // cm/s

	// ---- wheel-coupled rpm: driven-wheel spin through the gearing.
	// GetWheelAngularVelocity is rad/s; wheelspin shows up here directly.
	// Airborne wheels are excluded: Chaos freezes an in-air wheel at its
	// MaxSpinRotation cap forever (negative torque is gated out), so
	// coupling to one would pin the revs - treat airborne as clutch-open.
	float WheelRadSum = 0.0f;
	int32 NumInContact = 0;
	for (const int32 i : DrivenWheels)
	{
		if (Move->GetWheelState(i).bInContact)
		{
			WheelRadSum += Move->Wheels[i]->GetWheelAngularVelocity();
			++NumInContact;
		}
	}
	const float WheelRpm = NumInContact > 0
		? (WheelRadSum / NumInContact) * 60.0f / (2.0f * PI) : 0.0f;
	const float Ratio = Gear > 0 ? GearRatios[Gear - 1]
	                             : (Gear < 0 ? -ReverseRatio : 0.0f);
	const float CoupledRpm = FMath::Abs(WheelRpm * Ratio * FinalRatio);

	// ---- shift controller (upgraded auto v1)
	if (ShiftTimer > 0.0f)
	{
		ShiftTimer -= DeltaSeconds;
	}
	else if (Gear > 0)
	{
		const float UpshiftRpm = FMath::Lerp(
			UpshiftRpmAtCruise, UpshiftRpmAtWot, Throttle);
		if (Throttle > 0.05f && Gear < GearRatios.Num()
			&& EngineRpm > UpshiftRpm)
		{
			++Gear;
			ShiftTimer = ShiftTime;
		}
		// kickdown: drop into the power band on demand
		else if (Throttle > KickdownThrottle && Gear > 1)
		{
			const float RpmInLower =
				CoupledRpm * GearRatios[Gear - 2] / GearRatios[Gear - 1];
			if (EngineRpm < 0.62f * KickdownTargetRpm
				&& RpmInLower < KickdownTargetRpm)
			{
				--Gear;
				ShiftTimer = ShiftTime;
			}
		}
		// coasting: HOLD the gear (engine braking, no rev-match flare);
		// step down only as the coupled rpm sags toward stall - and never
		// while airborne (coupled reads 0 in flight; landing in 1st jolts)
		else if (Throttle <= 0.05f && Gear > 1 && NumInContact > 0
			&& CoupledRpm < CoastDownshiftRpm)
		{
			--Gear;
			ShiftTimer = ShiftTime * 0.5f;   // coast shifts are lazier cuts
		}
	}

	// reverse <-> forward: intent = holding the opposite pedal at standstill
	const bool bStopped = FMath::Abs(FwdSpeed) < 60.0f;
	if (Gear > 0 && bStopped && Brake > 0.3f && Throttle < 0.05f)
	{
		ReverseIntent += DeltaSeconds;
		if (ReverseIntent > 0.35f)
		{
			Gear = -1;
		}
	}
	else if (Gear < 0 && bStopped && Throttle > 0.3f)
	{
		Gear = 1;
		ReverseIntent = 0.0f;
	}
	else
	{
		ReverseIntent = 0.0f;
	}
	// keep Chaos's input processing (reverse-as-brake pedal swap) and the
	// HUD's GetCurrentGear in agreement with our gear
	if (Move->GetTargetGear() != Gear)
	{
		Move->SetTargetGear(Gear, true);
	}
	// while reversing, Chaos's pedal swap feeds reverse demand as brake input
	const float Demand = Gear < 0 ? Brake : Throttle;

	// ---- engine sim
	if (LimiterTimer > 0.0f)
	{
		LimiterTimer -= DeltaSeconds;
	}
	if (EngineRpm >= LimiterRpm)
	{
		LimiterTimer = LimiterCutTime;
	}
	const bool bCut = LimiterTimer > 0.0f || ShiftTimer > 0.0f;
	EffectiveThrottle = bCut ? 0.0f : Demand;

	const float DriveTq = TorqueCurve(EngineRpm) * EffectiveThrottle;
	const float BrakeTq = EngineBrakeFrac * PeakTorqueNm
		* (EngineRpm / MaxRpm) * (1.0f - EffectiveThrottle);

	if (ShiftTimer > 0.0f || Gear == 0 || NumInContact == 0)
	{
		// decoupled (shifting, neutral, or driven wheels airborne):
		// free engine against its own inertia
		const float RadPerS2 = (DriveTq - BrakeTq) / EngineInertia;
		EngineRpm += RadPerS2 * 60.0f / (2.0f * PI) * DeltaSeconds;
	}
	else
	{
		// clutch closed: engine tracks the (possibly spinning) wheels with a
		// short driveline time constant - lift-off falls WITH the wheels now
		EngineRpm = FMath::FInterpTo(EngineRpm,
			FMath::Max(CoupledRpm, IdleRpm), DeltaSeconds, 12.0f);
	}
	EngineRpm = FMath::Clamp(EngineRpm, IdleRpm, MaxRpm);
	// audio load: lugging low-rpm pulls read heavy, near-redline reads free
	Load = FMath::Clamp(
		EffectiveThrottle * (1.25f - 0.5f * EngineRpm / MaxRpm), 0.0f, 1.0f);

	// ---- torque to the driven wheels (engine braking rides along as
	// negative torque, so lift-off decelerates through the driveline)
	float WheelTq = 0.0f;
	if (Gear != 0 && ShiftTimer <= 0.0f)
	{
		// only engine-brake through wheels that are actually coupled
		// (near-idle at standstill = slipping clutch, no drag)
		const float NetTq = DriveTq
			- (CoupledRpm > IdleRpm ? BrakeTq : 0.0f);
		WheelTq = NetTq * Ratio * FinalRatio * Efficiency
			/ DrivenWheels.Num();
	}
	const bool bHandbrake = Move->GetHandbrakeInput();
	// forward pedal brake only - while reversing, brake input IS the drive
	const float PedalBrake = Gear < 0 ? 0.0f : Brake;
	for (const int32 i : DrivenWheels)
	{
		// Override is physics-side state that any RecreatePhysicsState (e.g.
		// tuning preset swaps) resets to None, and SetupVehicle reads wheel
		// CDOs rather than instances - so re-assert it every tick
		Move->SetTorqueCombineMethod(ETorqueCombineMethod::Override, i);
		Move->SetDriveTorque(WheelTq, i);
		// Override owns this wheel's brake torque too: replicate the pedal
		// and handbrake mapping Chaos would have applied
		const UChaosVehicleWheel* W = Move->Wheels[i];
		float BrakeWheelTq =
			(W->bAffectedByBrake ? PedalBrake * W->MaxBrakeTorque : 0.0f)
			+ (bHandbrake && W->bAffectedByHandbrake ? W->MaxHandBrakeTorque
			                                         : 0.0f);
		Move->SetBrakeTorque(BrakeWheelTq, i);
	}

	// trace rides the same flag as the audio hooks (-odrev/-odlift)
	if (LastTraceTime == 0.0)
	{
		LastTraceTime = FParse::Param(FCommandLine::Get(), TEXT("odtrace"))
			? 1e-9 : -1.0;
	}
	if (LastTraceTime >= 0.0)
	{
		const double Now = GetWorld()->GetTimeSeconds();
		if (Now - LastTraceTime > 0.1)
		{
			LastTraceTime = Now;
			UE_LOG(LogTemp, Display,
				TEXT("ODDrivetrain: t=%.2f rpm=%.0f gear=%d thr=%.2f cut=%d "
				     "coupled=%.0f spd=%.0f"),
				Now, EngineRpm, Gear, Demand, bCut ? 1 : 0, CoupledRpm,
				FwdSpeed);
		}
	}
}
