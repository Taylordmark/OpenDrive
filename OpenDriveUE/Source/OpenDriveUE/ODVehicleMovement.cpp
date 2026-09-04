#include "ODVehicleMovement.h"

#include "ODVehicleDef.h"
#include "Components/PrimitiveComponent.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "Misc/CommandLine.h"
#include "PhysicalMaterials/PhysicalMaterial.h"

UODVehicleMovement::UODVehicleMovement()
{
	PrimaryComponentTick.bCanEverTick = true;
	PrimaryComponentTick.TickGroup = TG_PrePhysics;

	// Placeholder-coupe defaults; AODVehiclePawn overwrites these per car via
	// ApplyVehicleDef(). geometry contract with pipeline/make_car_mesh.py
	// (front axle x=+132, rear x=-128, half-track 84, wheel radius 34, RWD).
	SetupWheels(132.0f, -128.0f, 84.0f, 34.0f, EODDriveLayout::RWD,
		4500.0f, 3000.0f, 6000.0f);
}

void UODVehicleMovement::SetupWheels(float FrontAxleX, float RearAxleX,
	float HalfTrackY, float WheelRadiusCm, EODDriveLayout Layout,
	float FrontBrakeNm, float RearBrakeNm, float HandbrakeNm)
{
	// wheel order: FL, FR, RL, RR (fronts first - audio/visuals assume it).
	// Attach points are the strut tops; static axle height = -static spring len.
	// Fronts must keep Attach.X > 0 and rears < 0 (TickTires infers the axle
	// from the sign), which holds for any real wheelbase.
	Wheels.SetNum(4);
	const FVector Attach[4] = {
		FVector(FrontAxleX, -HalfTrackY, 0.0f), FVector(FrontAxleX, HalfTrackY, 0.0f),
		FVector(RearAxleX, -HalfTrackY, 0.0f), FVector(RearAxleX, HalfTrackY, 0.0f)};
	const bool bFrontDriven =
		Layout == EODDriveLayout::FWD || Layout == EODDriveLayout::AWD;
	const bool bRearDriven =
		Layout == EODDriveLayout::RWD || Layout == EODDriveLayout::AWD;
	for (int32 i = 0; i < 4; ++i)
	{
		const bool bFront = i < 2;
		Wheels[i].Attach = Attach[i];
		Wheels[i].Radius = WheelRadiusCm;
		Wheels[i].bSteer = bFront;
		Wheels[i].bDriven = bFront ? bFrontDriven : bRearDriven;
		Wheels[i].bHandbrake = !bFront;                 // rear handbrake always
		Wheels[i].MaxBrakeNm = bFront ? FrontBrakeNm : RearBrakeNm;
		Wheels[i].MaxHandbrakeNm = bFront ? 0.0f : HandbrakeNm;
		Wheels[i].SpringLen = RestLengthCm;
	}
}

void UODVehicleMovement::SetDriveLayout(EODDriveLayout Layout)
{
	const bool bFrontDriven =
		Layout == EODDriveLayout::FWD || Layout == EODDriveLayout::AWD;
	const bool bRearDriven =
		Layout == EODDriveLayout::RWD || Layout == EODDriveLayout::AWD;
	for (int32 i = 0; i < Wheels.Num(); ++i)
	{
		Wheels[i].bDriven = (i < 2) ? bFrontDriven : bRearDriven;
	}
}

void UODVehicleMovement::ApplyVehicleDef(const UODVehicleDef& Def)
{
	// geometry + drive layout (AWD just marks all four bDriven; the drivetrain
	// already splits torque and reflected inertia by DrivenCount)
	SetupWheels(Def.FrontAxleXCm, Def.RearAxleXCm, Def.HalfTrackYCm,
		Def.WheelRadiusCm, Def.DriveLayout,
		Def.FrontBrakeNm, Def.RearBrakeNm, Def.HandbrakeNm);

	MaxRpm = Def.MaxRpm;
	LimiterRpm = Def.LimiterRpm;
	PeakTorqueNm = Def.PeakTorqueNm;
	PeakTorqueRpm = Def.PeakTorqueRpm;
	EngineInertia = Def.EngineInertia;
	EngineBrakeFrac = Def.EngineBrakeFrac;

	if (Def.GearRatios.Num() > 0)
	{
		GearRatios = Def.GearRatios;
	}
	FinalRatio = Def.FinalRatio;

	MuPeak = Def.MuPeak;
	// Floor these two rather than taking Def's value outright (2026-08-24,
	// Taylor: too much oversteer/slide after braking) - a per-car asset that
	// already serialized its own SlideFrac/PeakSlipAngleDeg would otherwise
	// silently undo this the same way tuning presets undo GripScale above.
	SlideFrac = FMath::Max(Def.SlideFrac, 0.88f);
	PeakSlipRatio = Def.PeakSlipRatio;
	PeakSlipAngleDeg = FMath::Max(Def.PeakSlipAngleDeg, 13.0f);
	WheelInertia = Def.WheelInertia;

	SpringRate = Def.SpringRate;
	DampingRatio = Def.DampingRatio;
	RestLengthCm = Def.RestLengthCm;
	ComLocalZ = Def.ComLocalZ;

	DragCoeff = Def.DragCoeff;
	DownforceCoeff = Def.DownforceCoeff;

	MaxSteerAngleDeg = Def.MaxSteerAngleDeg;

	// preset scales (TorqueScale/Front-RearGripScale/SpringRateScale) stay as
	// they are - they multiply these new baselines. The pawn re-applies the
	// active tuning preset after this so its absolute fields (downforce/COM/
	// damping, interim) land on top. Re-derive mass/COM next tick.
	MarkBodyDirty();
}

UPrimitiveComponent* UODVehicleMovement::GetBody() const
{
	return GetOwner()
		? Cast<UPrimitiveComponent>(GetOwner()->GetRootComponent()) : nullptr;
}

void UODVehicleMovement::InitBody(UPrimitiveComponent* Body)
{
	FBodyInstance* BI = Body->GetBodyInstance();
	if (!BI || !BI->IsValidBodyInstance() || Body->GetMass() < 1.0f)
	{
		return;                       // mesh not loaded yet - retry next tick
	}

	// nudge the physical COM to (0, 0, ComLocalZ) in body space: box-collision
	// COM sits mid-box, far above the real one, and COM height sets how much
	// weight transfer the suspension sees
	const FTransform Xf = Body->GetComponentTransform();
	const FVector ComLocal = Xf.InverseTransformPosition(Body->GetCenterOfMass());
	BI->COMNudge += FVector(-ComLocal.X, -ComLocal.Y, ComLocalZ - ComLocal.Z);
	BI->UpdateMassProperties();

	const float CornerW = Body->GetMass() * 980.0f * 0.25f;
	for (FODWheel& W : Wheels)
	{
		W.FzSm = CornerW;             // seed loads so first-tick grip is sane
	}

	// headless hook: -odtc=high|low|off pins the assist level for tests
	FString TcStr;
	if (FParse::Value(FCommandLine::Get(), TEXT("odtc="), TcStr))
	{
		TractionMode = TcStr.Compare(TEXT("high"), ESearchCase::IgnoreCase) == 0
			? EODTractionMode::High
			: TcStr.Compare(TEXT("low"), ESearchCase::IgnoreCase) == 0
				? EODTractionMode::Low : EODTractionMode::Off;
		UE_LOG(LogTemp, Display, TEXT("ODVehicleMovement: traction %s (cmdline)"),
			TractionModeName(TractionMode));
	}
	int32 AbsArg = -1;
	if (FParse::Value(FCommandLine::Get(), TEXT("odabs="), AbsArg))
	{
		bAbsEnabled = AbsArg != 0;
		UE_LOG(LogTemp, Display, TEXT("ODVehicleMovement: ABS %s (cmdline)"),
			bAbsEnabled ? TEXT("on") : TEXT("off"));
	}
	bBodyInit = true;

	UE_LOG(LogTemp, Display,
		TEXT("ODVehicleMovement: bound to %s (mass=%.0f kg, com z=%.1f -> "
		     "%.1f, %d wheels)"),
		*GetOwner()->GetName(), Body->GetMass(), ComLocal.Z, ComLocalZ,
		Wheels.Num());
}

void UODVehicleMovement::TickComponent(float DeltaSeconds, ELevelTick TickType,
	FActorComponentTickFunction* ThisTickFunction)
{
	Super::TickComponent(DeltaSeconds, TickType, ThisTickFunction);

	UPrimitiveComponent* Body = GetBody();
	if (!Body || !Body->IsSimulatingPhysics())
	{
		return;
	}
	if (!bBodyInit)
	{
		InitBody(Body);
		if (!bBodyInit)
		{
			return;
		}
	}
	const float Dt = FMath::Clamp(DeltaSeconds, 0.001f, 0.05f);
	FwdSpeedCmS = FVector::DotProduct(
		Body->GetPhysicsLinearVelocity(), Body->GetForwardVector());

	TickSuspension(Body, Dt);
	TickDrivetrain(Body, Dt);
	TickTires(Body, Dt);
	TickAero(Body);

	// spawn parking hold, static part: brake torque alone can't hold on a
	// grade - locked wheels still creep on the tire model (Frisco verify
	// drifted ~0.4 m/s downhill under full BrakeNm). Pin the horizontal
	// velocity; Z stays free so the suspension can settle. TickDrivetrain
	// already ran this tick and clears bParkHold on the first pedal input,
	// so the release frame is never pinned.
	if (bParkHold)
	{
		FVector Vel = Body->GetPhysicsLinearVelocity();
		Vel.X = 0.0f;
		Vel.Y = 0.0f;
		Body->SetPhysicsLinearVelocity(Vel);

		// 2026-09-03 fix ("tips over on flat pavement right after settle",
		// Breckenridge/Lincoln Ave): this pin only ever killed horizontal
		// TRANSLATION. A settle that releases with an uneven per-wheel spring
		// load (curb edge, camber, or the ground-snap's off-asphalt fallback -
		// see AODVehiclePawn::TrySnapToGround's timeout branch) puts a real
		// net TORQUE on the body the instant physics goes live, and nothing
		// here was damping that - the car could spin/roll right out from
		// under itself before the first pedal input ever arrived, sliding in
		// place (translation pinned) while rotating onto its roof. Angular
		// velocity isn't load-bearing for anything while parked (the whole
		// point is the car ISN'T moving yet), so zero it outright, same as
		// the linear pin above.
		Body->SetPhysicsAngularVelocityInDegrees(FVector::ZeroVector);
	}

	// airborne attitude damping - see AirborneRollDampPerSec's comment. Only
	// with ALL wheels off the ground, so it can never fight the tire model.
	bool bAnyContact = false;
	for (const FODWheel& W : Wheels)
	{
		bAnyContact |= W.bInContact;
	}
	if (!bAnyContact
		&& (AirborneRollDampPerSec > 0.0f || AirbornePitchDampPerSec > 0.0f))
	{
		const FTransform Xf = Body->GetComponentTransform();
		FVector AngLocal = Xf.InverseTransformVectorNoScale(
			Body->GetPhysicsAngularVelocityInDegrees());
		AngLocal.X *= FMath::Max(1.0f - AirborneRollDampPerSec * Dt, 0.0f);
		AngLocal.Y *= FMath::Max(1.0f - AirbornePitchDampPerSec * Dt, 0.0f);
		Body->SetPhysicsAngularVelocityInDegrees(
			Xf.TransformVectorNoScale(AngLocal));
	}

	// ---- 10 Hz trace, same -odtrace flag as the audio hooks ----
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
			int32 Grounded = 0;
			for (const FODWheel& W : Wheels)
			{
				Grounded += W.bInContact ? 1 : 0;
			}
			const UPhysicalMaterial* Surf = Wheels[2].Surface.Get();
			UE_LOG(LogTemp, Display,
				TEXT("ODDrivetrain: t=%.2f pos=(%.0f,%.0f,%.0f) rpm=%.0f gear=%d thr=%.2f cut=%d "
				     "coupled=%.0f spd=%.0f slip=%.2f fz=%.0f aF=%.1f FyF=%.0f "
				     "FyR=%.0f FxR=%.0f yaw=%.0f susp=%.1f gnd=%d surf=%s "
				     "tc=%.2f esc=%.0f abs=%d relax=%d omega=%.2f park=%d"),
				Now, Body->GetComponentLocation().X, Body->GetComponentLocation().Y,
				Body->GetComponentLocation().Z,
				EngineRpm, Gear, EffectiveThrottle, bTraceCut ? 1 : 0,
				TraceCoupled, FwdSpeedCmS, TraceSlip, Wheels[2].FzSm,
				TraceAlphaF, TraceFyF, TraceFyR, TraceFxR,
				Body->GetPhysicsAngularVelocityInDegrees().Z,
				RestLengthCm + RideHeightDelta - Wheels[0].SpringLen, Grounded,
				Surf ? *Surf->GetName() : TEXT("none"), TcScale, EscBrakeNm,
				bAbsActiveThisTick ? 1 : 0, bRelaxedShift ? 1 : 0,
				Wheels[2].Omega, bParkHold ? 1 : 0);
		}
	}
}

void UODVehicleMovement::TickSuspension(UPrimitiveComponent* Body, float Dt)
{
	const FTransform Xf = Body->GetComponentTransform();
	const FVector Up = Xf.GetUnitAxis(EAxis::Z);
	const float RestLen = FMath::Max(RestLengthCm + RideHeightDelta, 5.0f);
	const float CornerW = Body->GetMass() * 980.0f * 0.25f;
	const float K = SpringRate * SpringRateScale;
	// critical damping vs the corner mass; DampingRatio is the preset knob
	const float C = 2.0f * DampingRatio
		* FMath::Sqrt(K * Body->GetMass() * 0.25f);

	FCollisionQueryParams Query(SCENE_QUERY_STAT(ODSuspension), true, GetOwner());
	Query.bReturnPhysicalMaterial = true;

	// steering angle: raw input times a mild high-speed taper (the tires are
	// the real limit; this only keeps 120 mph lane-keeping humane)
	const float Mph = FMath::Abs(FwdSpeedCmS) * 0.0223694f;
	const float Taper =
		Mph <= 30.0f ? FMath::Lerp(1.0f, 0.85f, Mph / 30.0f)
		: Mph <= 60.0f ? FMath::Lerp(0.85f, 0.65f, (Mph - 30.0f) / 30.0f)
		: Mph <= 120.0f ? FMath::Lerp(0.65f, 0.5f, (Mph - 60.0f) / 60.0f)
		: 0.5f;

	for (FODWheel& W : Wheels)
	{
		W.SteerDeg = W.bSteer ? SteerIn * MaxSteerAngleDeg * Taper : 0.0f;

		const FVector Start = Xf.TransformPosition(W.Attach);
		FHitResult Hit;
		const bool bHit = GetWorld()->LineTraceSingleByChannel(Hit, Start,
			Start - Up * (RestLen + W.Radius), ECC_Visibility, Query);
		if (bHit)
		{
			const float NewLen =
				FMath::Clamp(Hit.Distance - W.Radius, 0.0f, RestLen);
			const float ComprRate = (W.SpringLen - NewLen) / Dt;   // cm/s
			W.SpringLen = NewLen;
			// spring + damper, applied at the strut top so roll/pitch moments
			// come out right; clamped so landings can't explode the solver
			const float F = FMath::Clamp(
				K * (RestLen - NewLen) + C * ComprRate, 0.0f, 6.0f * CornerW);
			Body->AddForceAtLocation(Up * F, Start);

			W.bInContact = true;
			W.ContactPoint = Hit.ImpactPoint;
			W.Surface = Hit.PhysMaterial;
			// tire load: smoothed spring force - REAL weight transfer, no
			// analytic estimate (that was a workaround for Chaos hiding the
			// constraint-side forces)
			W.FzSm += (F - W.FzSm) * FMath::Min(Dt * 25.0f, 1.0f);
		}
		else
		{
			W.bInContact = false;
			W.Surface = nullptr;
			W.SpringLen =
				FMath::FInterpConstantTo(W.SpringLen, RestLen, Dt, 80.0f);
			W.FzSm += (0.0f - W.FzSm) * FMath::Min(Dt * 25.0f, 1.0f);
		}
	}
}

void UODVehicleMovement::PrimeSuspension()
{
	UPrimitiveComponent* Body = GetBody();
	if (!Body || Wheels.IsEmpty())
	{
		return;
	}

	const FTransform Xf = Body->GetComponentTransform();
	const FVector Up = Xf.GetUnitAxis(EAxis::Z);
	const float RestLen = FMath::Max(RestLengthCm + RideHeightDelta, 5.0f);

	FCollisionQueryParams Query(SCENE_QUERY_STAT(ODSuspensionPrime), true, GetOwner());
	Query.bReturnPhysicalMaterial = true;

	for (FODWheel& W : Wheels)
	{
		const FVector Start = Xf.TransformPosition(W.Attach);
		FHitResult Hit;
		const bool bHit = GetWorld()->LineTraceSingleByChannel(Hit, Start,
			Start - Up * (RestLen + W.Radius), ECC_Visibility, Query);
		if (bHit)
		{
			// set the length directly - no ComprRate derived from this, so
			// there's nothing here for a stale prior value to corrupt
			W.SpringLen = FMath::Clamp(Hit.Distance - W.Radius, 0.0f, RestLen);
			W.bInContact = true;
			W.ContactPoint = Hit.ImpactPoint;
			W.Surface = Hit.PhysMaterial;
			// prime the smoothed load to the STATIC spring force this length
			// implies, not 0 - starting FzSm at 0 would itself read as a
			// momentary wheel-lift to anything sampling it before the first
			// real TickSuspension tick smooths it back up
			const float K = SpringRate * SpringRateScale;
			const float CornerW = Body->GetMass() * 980.0f * 0.25f;
			W.FzSm = FMath::Clamp(
				K * (RestLen - W.SpringLen), 0.0f, 6.0f * CornerW);
		}
		else
		{
			W.SpringLen = RestLen;
			W.bInContact = false;
			W.Surface = nullptr;
			W.FzSm = 0.0f;
		}
	}
}

int32 UODVehicleMovement::GearForSpeed(float AbsSpeedCmS) const
{
	// highest gear that still keeps the engine meaningfully above idle/lug
	// at this road speed (representative wheel radius: a rear/driven wheel)
	const float R = Wheels.Num() > 2 ? Wheels[2].Radius / 100.0f : 0.34f;   // m
	const float WheelRpm =
		(AbsSpeedCmS / 100.0f) / FMath::Max(2.0f * PI * R, 0.1f) * 60.0f;
	for (int32 g = GearRatios.Num(); g >= 1; --g)
	{
		if (WheelRpm * GearRatios[g - 1] * FinalRatio >= CoastDownshiftRpm * 1.4f)
		{
			return g;
		}
	}
	return 1;
}

float UODVehicleMovement::TorqueCurve(float Rpm) const
{
	// normalized flat-ish curve: soft off idle, peak, gentle fall to redline.
	// Floor raised 0.55->0.85 (2026-08-25, Taylor: launches felt dead until
	// ~20 mph then hit like a boost right as coupled rpm climbed toward
	// PeakTorqueRpm in 1st gear) - a peaky low end reads as "no torque, then
	// sudden torque" once a car is moving instead of revving freely.
	const float T = FMath::GetMappedRangeValueClamped(
		FVector2D(IdleRpm, PeakTorqueRpm), FVector2D(0.85f, 1.0f), Rpm);
	const float Fall = FMath::GetMappedRangeValueClamped(
		FVector2D(PeakTorqueRpm, MaxRpm), FVector2D(1.0f, 0.82f), Rpm);
	return PeakTorqueNm * TorqueScale * FMath::Min(T, Fall);
}

void UODVehicleMovement::TickDrivetrain(UPrimitiveComponent* Body, float Dt)
{
	int32 DrivenCount = 0, DrivenInContact = 0;
	float WheelRadSum = 0.0f;
	for (const FODWheel& W : Wheels)
	{
		if (W.bDriven)
		{
			++DrivenCount;
			DrivenInContact += W.bInContact ? 1 : 0;
			WheelRadSum += W.Omega;
		}
	}
	const float WheelRpm = DrivenCount > 0
		? (WheelRadSum / DrivenCount) * 60.0f / (2.0f * PI) : 0.0f;
	const float Ratio = Gear > 0 ? GearRatios[Gear - 1]
	                             : (Gear < 0 ? -ReverseRatio : 0.0f);
	const float CoupledRpm = FMath::Abs(WheelRpm * Ratio * FinalRatio);
	TraceCoupled = CoupledRpm;

	// ---- attack mode: while the driver is pressing on, HOLD the current
	// gear (no auto-upshift) so a committed run through a corner or a
	// burnout doesn't get short-shifted mid-corner. Sustained LOW throttle
	// (settled cruising, not just a lift for a shift) is what earns normal
	// auto-shifting back - CruiseTimer gates it so a single throttle blip
	// doesn't flip modes.
	const bool bCruisingThrottle = ThrottleIn < 0.35f;
	CruiseTimer = bCruisingThrottle ? CruiseTimer + Dt : 0.0f;
	bRelaxedShift = CruiseTimer > 1.0f;

	// ---- shift controller (stage 1, drive-test passed 2026-07-17)
	if (ShiftTimer > 0.0f)
	{
		ShiftTimer -= Dt;
	}
	else if (Gear > 0)
	{
		const float UpshiftRpm = FMath::Lerp(
			UpshiftRpmAtCruise, UpshiftRpmAtWot, ThrottleIn);
		// attack mode still upshifts AT the limiter - "hold the gear" means
		// don't short-shift early, not "never leave 1st". Without this a
		// floored car just bounces off the limiter forever and never climbs
		// through the box.
		const bool bMaxedInGear = EngineRpm > LimiterRpm - 150.0f;
		// grade guard: don't take a WOT upshift into a gear that can't hold
		// the current wheel speed at all - that gear's coupled rpm would sag
		// straight past CoastDownshiftRpm and kick back down once rpm
		// rebuilds, forever. Found 2026-08-23 (Taylor: hill starts got stuck
		// revving 1st<->2nd around a fixed low speed, never gaining ground) -
		// gear 1 hits the limiter fast under WOT and upshifts on bMaxedInGear
		// alone with no regard for whether gear 2 can actually keep climbing
		// the grade. Only gates the WOT path; bRelaxedShift's UpshiftRpm
		// already targets a sane rpm on flat/cruise throttle.
		const float RpmInHigher = Gear < GearRatios.Num()
			? CoupledRpm * GearRatios[Gear] / GearRatios[Gear - 1] : 0.0f;
		const bool bHigherGearViable = RpmInHigher > CoastDownshiftRpm * 1.3f;
		// slip guard: spinning wheels drive the rpm, not road speed - hold
		// the gear through a burnout instead of short-shifting to 5th
		if (ThrottleIn > 0.05f && Gear < GearRatios.Num() && TraceSlip < 0.5f
			&& (bRelaxedShift ? EngineRpm > UpshiftRpm
			                  : (bMaxedInGear && bHigherGearViable)))
		{
			++Gear;
			ShiftTimer = ShiftTime;
			LastShiftTime = GetWorld()->GetTimeSeconds();
		}
		// kickdown: drop into the power band on demand. The cooldown stops
		// hunting - a fresh upshift's rev dip must not immediately kick back
		else if (ThrottleIn > KickdownThrottle && Gear > 1
			&& GetWorld()->GetTimeSeconds() - LastShiftTime > 1.2)
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
		else if (ThrottleIn <= 0.05f && Gear > 1 && DrivenInContact > 0
			&& CoupledRpm < CoastDownshiftRpm)
		{
			--Gear;
			ShiftTimer = ShiftTime * 0.5f;   // coast shifts are lazier cuts
		}
		// settled cruise: correct straight to the gear road speed calls for,
		// keyed off the SLOWEST wheel (immune to a spinning driven wheel
		// inflating the speed estimate) rather than engine/coupled rpm
		else if (bRelaxedShift)
		{
			float SlowestSpeed = FLT_MAX;
			for (const FODWheel& W : Wheels)
			{
				SlowestSpeed = FMath::Min(SlowestSpeed,
					FMath::Abs(W.Omega) * (W.Radius / 100.0f));
			}
			const int32 Target = GearForSpeed(SlowestSpeed * 100.0f);
			if (Target != Gear)
			{
				Gear = Target;
				ShiftTimer = ShiftTime * 0.5f;
			}
		}
	}

	// reverse <-> forward: intent = holding the opposite pedal below walking
	// speed, not literally standstill (2026-08-25, Taylor: under 4mph,
	// holding forward or back should shift) - 4mph = 178.8 cm/s
	const bool bStopped = FMath::Abs(FwdSpeedCmS) < 178.8f;
	if (Gear > 0 && bStopped && BrakeIn > 0.3f && ThrottleIn < 0.05f)
	{
		ReverseIntent += Dt;
		if (ReverseIntent > 0.35f)
		{
			Gear = -1;
		}
	}
	else if (Gear < 0 && bStopped && ThrottleIn > 0.3f)
	{
		Gear = 1;
		ReverseIntent = 0.0f;
	}
	else
	{
		ReverseIntent = 0.0f;
	}
	// while reversing the pedals swap: brake input drives, throttle brakes
	const float Demand = Gear < 0 ? BrakeIn : ThrottleIn;

	// ---- engine sim
	if (LimiterTimer > 0.0f)
	{
		LimiterTimer -= Dt;
	}
	if (EngineRpm >= LimiterRpm)
	{
		LimiterTimer = LimiterCutTime;
	}
	// limiter chatter: the physics limiter is a soft torque fade (stability),
	// so held-at-redline never produced hard cuts - the EAR needs the bounce.
	// While pinned near the limiter, cycle ignition at LimiterBounceHz; the
	// only physics ripple is engine-brake torque (~small), the brap is audio.
	// engage across the soft-fade zone: WOT equilibrium pins rpm ~100-150
	// below LimiterRpm (the fade), which IS "held at the limiter"
	const bool bOnLimiter = Demand > 0.5f && EngineRpm > LimiterRpm - 200.0f;
	ChatterTime = bOnLimiter ? FMath::Max(ChatterTime, 0.0f) + Dt : -1.0f;
	const bool bChatterCut = ChatterTime >= 0.0f
		&& FMath::Fmod(ChatterTime * LimiterBounceHz, 1.0f) < LimiterBounceDuty;
	const bool bCut = LimiterTimer > 0.0f || ShiftTimer > 0.0f || bChatterCut;
	bTraceCut = bCut;
	EffectiveThrottle = bCut ? 0.0f : Demand;   // audio hears hard cuts
	bIgnitionCut = bCut && Demand > 0.25f;      // synth gates firings on this

	// physics torque uses a SOFT limiter (fade over the last 250 rpm) -
	// the hard cut's bang-bang made the driveline oscillate violently
	const float LimiterScale = FMath::Clamp(
		(LimiterRpm - EngineRpm) / 250.0f, 0.0f, 1.0f);
	const float DriveTq = TorqueCurve(EngineRpm)
		* (ShiftTimer > 0.0f ? 0.0f : Demand) * LimiterScale;
	const float BrakeTq = EngineBrakeFrac * PeakTorqueNm
		* (EngineRpm / MaxRpm) * (1.0f - EffectiveThrottle);

	// launch clutch bite: a standing start slips the clutch rather than
	// locking instantly - flare up free, then the bite drags rpm down to
	// meet the (still-slow) wheels, then it climbs with road speed. Fires
	// once per stop (guarded by EngineRpm still being near idle) so an
	// uphill launch can genuinely bog instead of starting pre-matched to
	// the powerband.
	if (LaunchTimer <= 0.0f && Gear > 0 && ShiftTimer <= 0.0f
		&& FMath::Abs(FwdSpeedCmS) < 80.0f && ThrottleIn > 0.15f
		&& EngineRpm < IdleRpm * 1.3f)
	{
		LaunchTimer = LaunchClutchTime;
	}
	LaunchTimer = FMath::Max(LaunchTimer - Dt, 0.0f);

	if (ShiftTimer > 0.0f || Gear == 0)
	{
		// decoupled (shifting or neutral): free engine on its own inertia
		const float RadPerS2 = (DriveTq - BrakeTq) / EngineInertia;
		EngineRpm += RadPerS2 * 60.0f / (2.0f * PI) * Dt;
	}
	else if (LaunchTimer > 0.0f)
	{
		// slipping clutch: blend a free-revving estimate (the flare) with
		// the normal coupled target (still low - the car isn't moving yet),
		// bite ramping in on an ease-in curve so the flare has a moment to
		// build before it gets dragged down
		const float FreeRadPerS2 = (DriveTq - BrakeTq) / EngineInertia;
		const float FreeRpm =
			EngineRpm + FreeRadPerS2 * 60.0f / (2.0f * PI) * Dt;
		const float CoupledTarget = FMath::FInterpTo(EngineRpm,
			FMath::Max(CoupledRpm, IdleRpm), Dt, 12.0f);
		const float Bite = 1.0f - LaunchTimer / LaunchClutchTime;   // 0->1
		EngineRpm = FMath::Lerp(FreeRpm, CoupledTarget, Bite * Bite);
	}
	else
	{
		// clutch closed: engine tracks the (possibly spinning) wheels with a
		// short driveline time constant - lift-off falls WITH the wheels
		EngineRpm = FMath::FInterpTo(EngineRpm,
			FMath::Max(CoupledRpm, IdleRpm), Dt, 12.0f);
	}
	EngineRpm = FMath::Clamp(EngineRpm, IdleRpm, MaxRpm);
	// audio load: lugging low-rpm pulls read heavy, near-redline reads free
	Load = FMath::Clamp(
		EffectiveThrottle * (1.25f - 0.5f * EngineRpm / MaxRpm), 0.0f, 1.0f);

	// ---- torque to the driven wheels (engine braking rides along as
	// negative torque, so lift-off decelerates through the driveline)
	float WheelTq = 0.0f;
	if (Gear != 0 && ShiftTimer <= 0.0f && DrivenCount > 0)
	{
		// only engine-brake through wheels that are actually coupled
		// (near-idle at standstill = slipping clutch, no drag)
		const float NetTq = DriveTq - (CoupledRpm > IdleRpm ? BrakeTq : 0.0f);
		WheelTq = NetTq * Ratio * FinalRatio * Efficiency / DrivenCount;
	}

	// ---- TC High: closed-loop drive-torque authority. Cut fast when the
	// worst driven wheel slips past the grip peak, give it back slowly.
	if (TractionMode == EODTractionMode::High && WheelTq > 0.0f)
	{
		// Floor raised 0.15->0.35 and cut/recovery rates softened
		// (2026-08-26, alongside TcSlipTarget's increase) - a 0.15 floor let
		// TC hold an 85% torque cut for seconds on a normal WOT launch;
		// this keeps it a backstop against runaway wheelspin rather than a
		// hard choke on every launch.
		TcScale += TraceSlip > TcSlipTarget
			? -(TraceSlip - TcSlipTarget) * 10.0f * Dt
			: 3.0f * Dt;
		TcScale = FMath::Clamp(TcScale, 0.35f, 1.0f);
		WheelTq *= TcScale;
	}
	else
	{
		TcScale = 1.0f;
	}

	// ---- ESC Low: slides are allowed, spins are not - excess yaw over what
	// the steering (friction-clamped) asks for gets damped by braking one
	// front wheel. Handbrake pulls disable it (rally flicks stay clean).
	float EscTarget = 0.0f;
	if (TractionMode == EODTractionMode::Low && !bHandbrakeIn)
	{
		const float VxMs = FwdSpeedCmS / 100.0f;
		if (VxMs > 4.0f)
		{
			const float SteerRad =
				FMath::DegreesToRadians(Wheels[0].SteerDeg);
			// kinematic yaw demand, clamped to what tires could ever deliver
			const float MaxYaw = 9.5f / VxMs;                       // rad/s
			const float DesiredYaw = FMath::Clamp(
				VxMs / 2.6f * FMath::Tan(SteerRad), -MaxYaw, MaxYaw);
			const float ActualYaw = FMath::DegreesToRadians(
				(float)Body->GetPhysicsAngularVelocityInDegrees().Z);
			const float YawErr = ActualYaw - DesiredYaw;
			const float Excess = FMath::Abs(YawErr) - EscYawDeadband;
			if (Excess > 0.0f)
			{
				EscTarget = FMath::Min(Excess * 5000.0f, 3200.0f);
				// rotating right too hard -> brake the left (outside) front
				EscWheel = YawErr > 0.0f ? 0 : 1;
			}
		}
	}
	EscBrakeNm += (EscTarget - EscBrakeNm) * FMath::Min(Dt * 12.0f, 1.0f);

	// spawn parking hold: full brakes until the driver's first pedal input
	// (brake counts - that's the reverse request in this control scheme)
	if (bParkHold && (ThrottleIn > 0.05f || BrakeIn > 0.05f || bHandbrakeIn))
	{
		bParkHold = false;
	}

	const float PedalBrake = Gear < 0 ? ThrottleIn : BrakeIn;
	for (int32 i = 0; i < Wheels.Num(); ++i)
	{
		FODWheel& W = Wheels[i];
		W.DriveNm = W.bDriven ? WheelTq : 0.0f;
		W.BrakeNm = PedalBrake * W.MaxBrakeNm
			+ (bHandbrakeIn ? W.MaxHandbrakeNm : 0.0f)
			+ (bParkHold ? W.MaxBrakeNm : 0.0f)
			+ (i == EscWheel && EscBrakeNm > 1.0f ? EscBrakeNm : 0.0f);
	}

	CurTotalRatio = Ratio * FinalRatio;
	bDrivelineCoupled = Gear != 0 && ShiftTimer <= 0.0f;
}

float UODVehicleMovement::SurfaceGrip(const UPhysicalMaterial* Mat) const
{
	// Two-tier, not per-material (was Dirt=0.72/Grass=0.60/Asphalt=1.0/
	// unknown=0.85) - Taylor, 2026-07-20: the player is meant to stay on
	// the road (the whole open-world corridor design assumes this), so
	// off-road grip only needs to read as "you left the road, that was a
	// mistake," not simulate a specific ground texture. Biome ground
	// materials can still carry distinct names/textures for visuals/audio;
	// this just stops branching physics on them.
	if (Mat && Mat->GetName().Contains(TEXT("Asphalt")))
	{
		return 1.0f;   // on-road
	}
	return 0.6f;   // off-road (any surface that isn't the road)
}

void UODVehicleMovement::TickTires(UPrimitiveComponent* Body, float Dt)
{
	const float PeakSlipAngleRad = FMath::DegreesToRadians(PeakSlipAngleDeg);
	const FVector Up = Body->GetUpVector();
	const FVector BodyFwd = Body->GetForwardVector();
	const float QuarterMassKg = Body->GetMass() * 0.25f;
	TraceSlip = 0.0f;
	TraceAlphaF = TraceFyF = TraceFyR = TraceFxR = 0.0f;
	bAbsActiveThisTick = false;

	int32 DrivenCount = 0;
	for (const FODWheel& W : Wheels)
	{
		DrivenCount += W.bDriven ? 1 : 0;
	}

	// filled per wheel below, consumed after the loop by the axle-paired ABS
	// pass (brake integration itself happens in that second pass)
	float SlipRatios[4] = {0.0f, 0.0f, 0.0f, 0.0f};
	float EffInertias[4] = {0.0f, 0.0f, 0.0f, 0.0f};

	for (int32 i = 0; i < Wheels.Num(); ++i)
	{
		FODWheel& W = Wheels[i];
		const float ReM = W.Radius / 100.0f;                       // m
		const bool bFront = W.Attach.X > 0.0f;

		float ReactionNm = 0.0f;
		float WheelSlipRatio = 0.0f;   // braking slip; ABS reads this below
		if (W.bInContact)
		{
			// contact-patch frame: wheel forward = body forward yawed by the
			// steer angle; the body plane approximates the road plane
			const FVector Fwd =
				BodyFwd.RotateAngleAxis(W.SteerDeg, Up).GetSafeNormal();
			const FVector Right = FVector::CrossProduct(Up, Fwd).GetSafeNormal();

			const FVector Vel =
				Body->GetPhysicsLinearVelocityAtPoint(W.ContactPoint); // cm/s
			const float Vx = FVector::DotProduct(Vel, Fwd) / 100.0f;   // m/s
			const float Vy = FVector::DotProduct(Vel, Right) / 100.0f;

			// real suspension load, kg cm/s^2 - but CAPPED for grip purposes.
			// The suspension force is clamped at 6x corner weight (needed so
			// landings don't explode the solver), and a hard brake-dive's
			// spring+damper spike really reaches it - which fed the friction
			// circle up to ~4g of decel, and with any steer input a matching
			// lateral spike at ground level that catapulted the car into
			// multi-rotation barrel rolls (2026-08-27, reproduced headless:
			// brake+half-steer at 70 mph -> airborne, thrown 5 m UP, roll
			// wrapped past 180). Tires can't use load a real contact patch
			// never sees: 2.5x static corner weight covers real weight
			// transfer (a 1g stop puts ~1.7-2x on the front axle) with margin.
			const float Fz = FMath::Min(
				W.FzSm, 2.5f * QuarterMassKg * 980.0f);
			const float Mu = MuPeak * SurfaceGrip(W.Surface.Get())
				* (bFront ? FrontGripScale : RearGripScale);

			// combined slip, normalized so 1.0 = the peak of the curve.
			// Denominator is max(|Vx|, |wheel speed|, 1) rather than a bare
			// 1 m/s floor - at near-zero road speed (launch) a bare 1 m/s
			// floor let any wheel spin-up read as a huge multiple (e.g.
			// slip=10 at a dead stop), which fed TC High's controller a
			// nonsense value and slammed WheelTq to its 0.15 floor for
			// several seconds of every forward launch (2026-08-26, Taylor:
			// "forward start feels terrible, reverse feels fine" - reverse
			// dodged this only because TC High's WheelTq>0 gate never
			// engages for reverse's negated Ratio, not because reverse
			// didn't hit the same bad reading). This keeps SlipRatio
			// physically bounded to roughly [-1,1] and leaves normal-speed
			// driving unchanged, since Vx already dominates there.
			const float WheelSpeedMs = W.Omega * ReM;
			const float SlipRatio = (WheelSpeedMs - Vx)
				/ FMath::Max(FMath::Max(FMath::Abs(Vx), FMath::Abs(WheelSpeedMs)), 1.0f);
			const float SlipAngle = FMath::Atan2(-Vy, FMath::Abs(Vx) + 0.7f);
			const float Sx = SlipRatio / PeakSlipRatio;
			const float Sy = SlipAngle / PeakSlipAngleRad;
			const float Sc = FMath::Sqrt(Sx * Sx + Sy * Sy);

			// grip curve: smooth rise to peak at Sc=1, fade to sliding grip
			const float Shape = Sc <= 1.0f
				? 1.5f * Sc - 0.5f * Sc * Sc * Sc
				: FMath::Lerp(1.0f, SlideFrac,
					FMath::Min((Sc - 1.0f) / 1.5f, 1.0f));

			WheelSlipRatio = SlipRatio;

			float Fx = 0.0f, Fy = 0.0f;
			if (Sc > KINDA_SMALL_NUMBER)
			{
				const float F = Mu * Fz * Shape;   // friction circle
				Fx = F * Sx / Sc;
				Fy = F * Sy / Sc;
			}

			// stability clamps: never push the contact patch past zero in
			// one frame (kills parking jitter without touching real slides).
			//
			// bParkHold (see EngageParkHold) is a special case both clamps
			// below get wrong: it pins the body's linear AND angular
			// velocity to zero every tick, but that pin runs in
			// TickComponent AFTER this tire pass, so Vx/Vy here can carry
			// mid-tick leftovers from whatever force THIS SAME pass computed
			// last frame - a proportional clamp (FyStop/FxStop scale with
			// |Vy|/|Vx|) doesn't bound that, it rides along with it. Under
			// full park-hold brake the old `W.BrakeNm < 1.0f` guard also
			// disabled the Fx clamp entirely (ParkHold's forced
			// W.MaxBrakeNm made it permanently false), so nothing capped
			// either axis. Together that formed a genuine per-tick feedback
			// loop that never settled - reproduced headless with zero
			// player input (thr=0.00 throughout): FxR swinging +/-3e6 N and
			// spd alternating +/-55 cm/s at t=35s before the fix, then (once
			// Fx alone was gated in) the same loop just moved to Fy -
			// FyF/FyR swinging +/-2-3e6 N with a 50+ deg "slip angle" on a
			// stationary car. A parked car should feel zero net tire slip
			// force at all - the brake torque is what holds it - so while
			// held, skip tire force generation outright rather than trying
			// to size a clamp against a measurement this same loop keeps
			// perturbing.
			if (bParkHold)
			{
				Fx = 0.0f;
				Fy = 0.0f;
			}
			else
			{
				const float FyStop =
					QuarterMassKg * FMath::Abs(Vy) * 100.0f / Dt * 0.45f;
				Fy = FMath::Clamp(Fy, -FyStop, FyStop);
				if (FMath::Abs(Vx) < 1.5f && EffectiveThrottle < 0.05f
					&& W.BrakeNm < 1.0f)
				{
					const float FxStop =
						QuarterMassKg * FMath::Abs(Vx) * 100.0f / Dt * 0.45f;
					Fx = FMath::Clamp(Fx, -FxStop, FxStop);
				}
			}

			// Fx at the true contact (brake dive / squat should feel real);
			// Fy raised toward the roll center - see LateralForceLiftCm.
			Body->AddForceAtLocation(Fwd * Fx, W.ContactPoint);
			Body->AddForceAtLocation(Right * Fy,
				W.ContactPoint + Up * LateralForceLiftCm);

			if (bFront)
			{
				TraceAlphaF = FMath::RadiansToDegrees(SlipAngle);
				TraceFyF += Fy;
			}
			else
			{
				TraceFyR += Fy;
				TraceFxR += Fx;
			}

			// ground reaction + rolling resistance spin the wheel down
			ReactionNm = (Fx / 100.0f) * ReM
				+ RollingResistance * (Fz / 100.0f) * ReM
					* FMath::Sign(W.Omega);

			if (W.bDriven)
			{
				TraceSlip = FMath::Max(TraceSlip, FMath::Abs(SlipRatio));
			}
		}

		// integrate the drive/reaction half of wheel spin here; the brake
		// half (ABS-modulated, axle-paired) happens in the pass below.
		// A coupled driven wheel carries the engine's REFLECTED inertia
		// (I*ratio^2 ~ 12x the wheel's own in 1st) - without it the
		// driveline spin-up/down is unphysically twitchy.
		const bool bDrivenCoupled = bDrivelineCoupled && W.bDriven;
		const float EffInertia = WheelInertia + (bDrivenCoupled && DrivenCount > 0
			? EngineInertia * FMath::Square(CurTotalRatio) * Efficiency
				/ DrivenCount
			: 0.0f);
		if (bParkHold)
		{
			// The tire-force clamp above already zeroes Fx/Fy while parked
			// (no ground reaction to fight), but W.DriveNm still carries
			// whatever torque the driveline has coupled through at idle
			// (near-idle standstill traces show ~370-600 Nm coupled). With
			// ReactionNm zeroed alongside Fx there was nothing left to
			// balance that torque, so this same integration
			// (W.Omega += DriveNm/EffInertia*Dt) spun the wheel up every
			// tick; the brake pass below only trims a fixed Nm-scaled delta
			// per tick, not a hard clamp, so it couldn't reliably outrun a
			// steady DriveNm feed. Parked wheels are physically locked -
			// pin Omega at zero directly instead of integrating anything.
			W.Omega = 0.0f;
		}
		else
		{
			W.Omega += (W.DriveNm - ReactionNm) / EffInertia * Dt;
		}

		SlipRatios[i] = WheelSlipRatio;
		EffInertias[i] = EffInertia;
	}

	// ---- ABS: select-low per axle. Braking slip is longitudinal-only
	// (negative = wheel slower than road, i.e. locking under brakes) -
	// release torque once past AbsSlipTarget so the wheel keeps rolling near
	// peak grip instead of sliding at SlideFrac. Each axle's two wheels share
	// whichever reduction is more aggressive, same as real ABS pairing an
	// axle: independent per-wheel control brake-pulls the car off a straight
	// line the instant one wheel locks a frame before its partner, which a
	// keyboard's all-or-nothing brake input can't trail off to avoid the way
	// a wheel/pedal driver would. Off = full lock reachable, same as before.
	float AbsFactor[4] = {1.0f, 1.0f, 1.0f, 1.0f};
	if (bAbsEnabled)
	{
		for (int32 Axle = 0; Axle < 2; ++Axle)   // 0 = front (FL,FR), 1 = rear
		{
			const int32 A = Axle * 2, B = Axle * 2 + 1;
			float FactorA = 1.0f, FactorB = 1.0f;
			if (Wheels[A].bInContact && Wheels[A].BrakeNm > 1.0f
				&& SlipRatios[A] < -AbsSlipTarget)
			{
				const float Over = (-SlipRatios[A]) - AbsSlipTarget;
				FactorA = FMath::Clamp(1.0f - Over * 6.0f, 0.05f, 1.0f);
			}
			if (Wheels[B].bInContact && Wheels[B].BrakeNm > 1.0f
				&& SlipRatios[B] < -AbsSlipTarget)
			{
				const float Over = (-SlipRatios[B]) - AbsSlipTarget;
				FactorB = FMath::Clamp(1.0f - Over * 6.0f, 0.05f, 1.0f);
			}
			const float Paired = FMath::Min(FactorA, FactorB);
			AbsFactor[A] = AbsFactor[B] = Paired;
			bAbsActiveThisTick |= Paired < 0.999f;
		}
	}

	for (int32 i = 0; i < Wheels.Num(); ++i)
	{
		FODWheel& W = Wheels[i];
		const float EffBrakeNm = W.BrakeNm * AbsFactor[i];
		const float BrakeDelta = EffBrakeNm / EffInertias[i] * Dt;
		W.Omega = FMath::Sign(W.Omega)
			* FMath::Max(FMath::Abs(W.Omega) - BrakeDelta, 0.0f);
		if (bDrivelineCoupled && W.bDriven
			&& FMath::Abs(CurTotalRatio) > KINDA_SMALL_NUMBER)
		{
			// the engine can't spin the driveline past its own redline
			const float MaxOmega = MaxRpm * 1.15f
				/ FMath::Abs(CurTotalRatio) * (2.0f * PI) / 60.0f;
			W.Omega = FMath::Clamp(W.Omega, -MaxOmega, MaxOmega);
		}
	}
}

void UODVehicleMovement::TickAero(UPrimitiveComponent* Body)
{
	const FVector Vel = Body->GetPhysicsLinearVelocity();
	const float SpeedMs = Vel.Size() / 100.0f;
	if (SpeedMs > 1.0f)
	{
		// quadratic drag at the COM (N -> engine units via *100)
		Body->AddForce(
			-Vel / (SpeedMs * 100.0f) * DragCoeff * SpeedMs * SpeedMs * 100.0f);
	}
	const float FwdMs = FwdSpeedCmS / 100.0f;
	Body->AddForce(
		-Body->GetUpVector() * DownforceCoeff * FwdMs * FwdMs * 100.0f);
}
