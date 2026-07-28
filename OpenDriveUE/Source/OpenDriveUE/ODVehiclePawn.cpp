#include "ODVehiclePawn.h"

#include "AssetRegistry/ARFilter.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Camera/CameraComponent.h"
#include "Components/InputComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "GameFramework/SpringArmComponent.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/CommandLine.h"
#include "ODTuningManager.h"
#include "ODVehicleDef.h"
#include "ODVehicleMovement.h"

namespace
{
const TCHAR* ChassisPath = TEXT("/Game/OpenDrive/Car/chassis.chassis");
const TCHAR* WheelPath = TEXT("/Game/OpenDrive/Car/wheel.wheel");
}

AODVehiclePawn::AODVehiclePawn()
{
	PrimaryActorTick.bCanEverTick = true;

	Body = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Body"));
	SetRootComponent(Body);
	Body->SetCollisionProfileName(TEXT("Vehicle"));
	Body->SetSimulatePhysics(true);
	Body->BodyInstance.bOverrideMass = true;
	Body->BodyInstance.SetMassOverride(MassKg);
	Body->SetNotifyRigidBodyCollision(true);   // AODVehicleAudio impact hook

	Movement = CreateDefaultSubobject<UODVehicleMovement>(TEXT("Movement"));

	for (int32 i = 0; i < 4; ++i)
	{
		UStaticMeshComponent* Wheel = CreateDefaultSubobject<UStaticMeshComponent>(
			*FString::Printf(TEXT("Wheel%d"), i));
		Wheel->SetupAttachment(Body);
		Wheel->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		WheelVisuals.Add(Wheel);
	}
	WheelSpinDeg.Init(0.0f, 4);

	SpringArm = CreateDefaultSubobject<USpringArmComponent>(TEXT("SpringArm"));
	SpringArm->SetupAttachment(Body);
	SpringArm->TargetArmLength = 640.0f;
	SpringArm->SocketOffset = FVector(0.0f, 0.0f, 150.0f);
	SpringArm->SetRelativeRotation(FRotator(-12.0f, 0.0f, 0.0f));
	SpringArm->bEnableCameraLag = true;
	SpringArm->CameraLagSpeed = 9.0f;
	SpringArm->bEnableCameraRotationLag = true;
	SpringArm->CameraRotationLagSpeed = 9.0f;
	SpringArm->bInheritPitch = false;
	SpringArm->bInheritRoll = false;
	// 2026-07-28: the main-menu showroom runs entirely with the game PAUSED
	// (so the frozen preview pawn can't drive off), and this pawn's own Tick
	// correctly does NOT run while paused (input/physics/ground-snap should
	// all stay frozen) - but that also stops the SpringArm's OWN component
	// tick, which is what actually computes/lags the camera into its "behind
	// and above the car" pose. AODMainMenu::Tick DOES run while paused (it
	// has to, to spin the preview) and rotates the Body directly, which took
	// effect immediately (SetActorRotation doesn't need a tick) - but the
	// camera itself just stayed wherever it was at the last pre-pause frame,
	// which on a fresh boot is before the spring arm ever got to settle:
	// reported live as "the car is rotating but I'm still inside/under it."
	// A component's tick is independent of its owning actor's - enabling
	// this JUST for the spring arm lets the camera keep tracking the
	// spinning body while paused, without waking up any of the pawn's own
	// (correctly-frozen) driving logic.
	SpringArm->PrimaryComponentTick.bTickEvenWhenPaused = true;

	Camera = CreateDefaultSubobject<UCameraComponent>(TEXT("Camera"));
	Camera->SetupAttachment(SpringArm);
}

void AODVehiclePawn::EnsureMeshes()
{
	if (!Body->GetStaticMesh())
	{
		if (UStaticMesh* Chassis = LoadObject<UStaticMesh>(nullptr, ChassisPath))
		{
			Body->SetStaticMesh(Chassis);
			Body->BodyInstance.bOverrideMass = true;
			Body->BodyInstance.SetMassOverride(MassKg);
			Movement->MarkBodyDirty();   // mass/COM re-derived on next tick
		}
		else
		{
			UE_LOG(LogTemp, Error,
				TEXT("ODVehiclePawn: chassis mesh missing (%s) - run "
				     "make_car_mesh.py + apply_vehicle_pawn.py"), ChassisPath);
		}
	}
	if (WheelVisuals.Num() > 0 && !WheelVisuals[0]->GetStaticMesh())
	{
		if (UStaticMesh* Wheel = LoadObject<UStaticMesh>(nullptr, WheelPath))
		{
			for (int32 i = 0; i < WheelVisuals.Num(); ++i)
			{
				WheelVisuals[i]->SetStaticMesh(Wheel);
			}
		}
	}
}

void AODVehiclePawn::LoadVehicleDefs()
{
	Vehicles.Reset();
	FAssetRegistryModule& ARM =
		FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
	IAssetRegistry& AR = ARM.Get();
	// A -game launch can reach this BeginPlay before the async content scan has
	// indexed the vehicle defs (seen live: "0 found" with the assets on disk),
	// so force a synchronous scan of their folder before querying.
	AR.ScanPathsSynchronous({TEXT("/Game/OpenDrive/Car")}, /*bForceRescan*/true);

	FARFilter Filter;
	Filter.ClassPaths.Add(UODVehicleDef::StaticClass()->GetClassPathName());
	Filter.bRecursiveClasses = true;
	TArray<FAssetData> Found;
	AR.GetAssets(Filter, Found);
	for (const FAssetData& A : Found)
	{
		if (UODVehicleDef* Def = Cast<UODVehicleDef>(A.GetAsset()))
		{
			Vehicles.Add(Def);
		}
	}
	// stable order, base "Coupe" sorts to index 0 = the spawn car. TObjectPtr
	// Sort dereferences, so the predicate takes the element type by ref.
	Vehicles.Sort([](const UODVehicleDef& A, const UODVehicleDef& B)
	{
		return A.DisplayName < B.DisplayName;
	});
	UE_LOG(LogTemp, Display, TEXT("ODVehiclePawn: %d vehicle def(s) found"), Vehicles.Num());
}

void AODVehiclePawn::ApplyVehicleDef(UODVehicleDef* Def)
{
	if (!Def || !Body || !Movement)
	{
		return;
	}

	// Preserve motion across the static-mesh swap (SetStaticMesh re-registers
	// the physics body) so a live V-swap keeps velocity instead of jolting;
	// harmless (~0) on the initial spawn call before ground-snap freezes us.
	const bool bWasSimulating = Body->IsSimulatingPhysics();
	const FVector LinVel =
		bWasSimulating ? Body->GetPhysicsLinearVelocity() : FVector::ZeroVector;
	const FVector AngVel = bWasSimulating
		? Body->GetPhysicsAngularVelocityInDegrees() : FVector::ZeroVector;

	if (UStaticMesh* Chassis = Def->ChassisMesh.LoadSynchronous())
	{
		Body->SetStaticMesh(Chassis);
	}
	else
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODVehiclePawn: vehicle '%s' chassis mesh missing - keeping current"),
			*Def->DisplayName);
	}

	MassKg = Def->MassKg;
	Body->BodyInstance.bOverrideMass = true;
	Body->BodyInstance.SetMassOverride(MassKg);

	if (UStaticMesh* Wheel = Def->WheelMesh.LoadSynchronous())
	{
		for (UStaticMeshComponent* WV : WheelVisuals)
		{
			if (WV)
			{
				WV->SetStaticMesh(Wheel);
			}
		}
	}

	if (bWasSimulating)
	{
		Body->SetSimulatePhysics(true);
		Body->SetPhysicsLinearVelocity(LinVel);
		Body->SetPhysicsAngularVelocityInDegrees(AngVel);
	}

	// wheel geometry + drive layout + physics envelope (also MarkBodyDirty)
	Movement->ApplyVehicleDef(*Def);

	// re-apply the active tuning preset so its scales/deltas land on the new
	// baseline (interim: the preset still owns downforce/COM/damping absolutely)
	if (AActor* TM =
		UGameplayStatics::GetActorOfClass(GetWorld(), AODTuningManager::StaticClass()))
	{
		AODTuningManager* Tuning = Cast<AODTuningManager>(TM);
		if (Tuning && Tuning->GetPresetIndex() >= 0)
		{
			Tuning->ApplyPresetByIndex(Tuning->GetPresetIndex());
		}
	}

	UE_LOG(LogTemp, Display, TEXT("ODVehiclePawn: applied vehicle '%s' (%.0f kg)"),
		*Def->DisplayName, MassKg);
}

void AODVehiclePawn::SetVehicleByIndex(int32 Index)
{
	if (!Vehicles.IsValidIndex(Index))
	{
		return;
	}
	CurrentVehicleIdx = Index;
	ApplyVehicleDef(Vehicles[Index]);
}

void AODVehiclePawn::CycleVehicle()
{
	if (Vehicles.Num() < 2)
	{
		return;   // nothing to switch to
	}
	SetVehicleByIndex((CurrentVehicleIdx + 1) % Vehicles.Num());
}

void AODVehiclePawn::SetShowroom(bool bOn)
{
	if (!Body || !SpringArm)
	{
		return;
	}
	if (bOn)
	{
		Body->SetSimulatePhysics(false);
		// level the car; AODMainMenu sets a fixed display yaw once (no spin)
		SetActorRotation(FRotator(0.0f, GetActorRotation().Yaw, 0.0f));
		// stop the spring arm inheriting the body's yaw, so the fixed display
		// angle actually shows a fixed angle instead of the camera orbiting
		// to match whatever heading the pawn happened to spawn facing
		SpringArm->bInheritYaw = false;
		// shift the car toward screen-right, text panel toward screen-left
		SpringArm->SocketOffset = FVector(0.0f, ShowroomLateralOffsetCm, 150.0f);
	}
	else
	{
		SpringArm->bInheritYaw = true;   // back to the chase cam
		SpringArm->SocketOffset = FVector(0.0f, 0.0f, 150.0f);   // restore centered chase-cam framing
	}
}

void AODVehiclePawn::TeleportToSpawn(const FVector& RoadPoint, float YawDeg)
{
	if (!Body)
	{
		return;
	}
	SpringArm->bInheritYaw = true;   // ensure showroom framing is dropped

	// Lift to a high absolute altitude (not a relative +offset) so the
	// asphalt ground-snap trace always starts ABOVE the real road surface -
	// the centerline data is XY-only, so RoadPoint.Z carries no useful height,
	// and the road corridor in these areas sits well under this ceiling.
	Body->SetSimulatePhysics(false);
	SetActorLocation(FVector(RoadPoint.X, RoadPoint.Y, 100000.0f),
		false, nullptr, ETeleportType::TeleportPhysics);
	SetActorRotation(FRotator(0.0f, YawDeg, 0.0f));

	// re-arm the exact BeginPlay ground-snap wait (PM_Asphalt trace) at the
	// new spot; TrySnapToGround handles the settle from here
	bGroundSnapped = false;
	SpawnTime = GetWorld()->GetTimeSeconds();
	LastSnapLogTime = 0.0;
}

void AODVehiclePawn::PlaceInShowroom(const FVector& Loc)
{
	if (!Body)
	{
		return;
	}
	Body->SetSimulatePhysics(false);
	SetActorLocation(Loc, false, nullptr, ETeleportType::TeleportPhysics);
	SetActorRotation(FRotator(0.0f, GetActorRotation().Yaw, 0.0f));
	// no real ground under this location, and none needed - it's fixed and
	// self-contained by design, so there's nothing for TrySnapToGround to
	// wait on (and it wouldn't run again anyway once the menu pauses Tick).
	bGroundSnapped = true;
}

void AODVehiclePawn::BeginPlay()
{
	Super::BeginPlay();

	// selectable cars: discover defs, spawn as index 0 (the base coupe). Falls
	// back to the hardcoded coupe meshes if no def assets exist yet.
	LoadVehicleDefs();
	if (Vehicles.Num() > 0)
	{
		CurrentVehicleIdx = 0;
		ApplyVehicleDef(Vehicles[0]);
	}
	else
	{
		EnsureMeshes();
	}

	for (int32 i = 0; i < WheelVisuals.Num(); ++i)
	{
		WheelVisuals[i]->SetRelativeLocation(Movement->GetWheelLocalPos(i));
	}
	FParse::Value(FCommandLine::Get(), TEXT("odrev="), RevAt);
	FParse::Value(FCommandLine::Get(), TEXT("odlift="), LiftAt);
	FParse::Value(FCommandLine::Get(), TEXT("odsteer="), SteerAt);
	FParse::Value(FCommandLine::Get(), TEXT("odthr="), HookThrottle);
	FParse::Value(FCommandLine::Get(), TEXT("odbrake="), BrakeAt);
	FParse::Value(FCommandLine::Get(), TEXT("odveh="), VehSwapAt);

	// held kinematic until TrySnapToGround confirms real ground beneath us -
	// see the declaration comment for why (PlayerStart's baked height can
	// disagree with actual streamed terrain by tens of meters - measured as
	// much as 1.2 m BELOW actual ground in one case, which would leave the
	// car visibly clipped into the terrain/road mesh for the whole wait if
	// left in place). Lift clear first so the wait itself is never visible,
	// regardless of which direction the offline number was wrong in.
	Body->SetSimulatePhysics(false);
	SetActorLocation(GetActorLocation() + FVector(0.0f, 0.0f, 2000.0f),
		false, nullptr, ETeleportType::TeleportPhysics);
	SpawnTime = GetWorld()->GetTimeSeconds();
}

void AODVehiclePawn::TrySnapToGround()
{
	const FVector Loc = GetActorLocation();
	FHitResult Hit;
	const FVector Start(Loc.X, Loc.Y, Loc.Z + 10000.0f);     // 100 m above current
	const FVector End(Loc.X, Loc.Y, Loc.Z - 500000.0f);      // 5 km below - generous
	FCollisionQueryParams Params;
	Params.AddIgnoredActor(this);
	Params.bReturnPhysicalMaterial = true;
	const bool bHit = GetWorld()->LineTraceSingleByChannel(Hit, Start, End, ECC_Visibility, Params);

	// wait for the actual ROAD surface (PM_Asphalt - same physmat check
	// AODForestManager::GroundAt already uses to detect pavement), not just
	// any collision: raw streamed terrain resolves first and can sit at a
	// different height than where the road tile's OWN ground-snap ends up
	// placing its pavement once that (separately gated, tile-by-tile)
	// process finishes - snapping onto whatever's hit first left the car
	// buried once the road caught up and built on top of/around it.
	const bool bOnRoad = bHit && Hit.PhysMaterial.IsValid()
		&& Hit.PhysMaterial->GetName().Contains(TEXT("Asphalt"));

	const double Now = GetWorld()->GetTimeSeconds();
	const bool bTimedOut = (Now - SpawnTime) > GroundSnapTimeoutSec;

	// periodic visibility into what we're actually hitting while waiting -
	// without this there's no way to tell "correctly waiting on non-asphalt"
	// from "finding nothing" from "some other silent failure"
	if (Now - LastSnapLogTime > 1.0)
	{
		LastSnapLogTime = Now;
		UE_LOG(LogTemp, Display,
			TEXT("ODVehiclePawn: ground-snap waiting at (%.0f,%.0f,%.0f) t=%.1fs - "
			     "hit=%d onRoad=%d physmat=%s implZ=%.0f"),
			Loc.X, Loc.Y, Loc.Z, Now - SpawnTime, bHit, bOnRoad,
			(bHit && Hit.PhysMaterial.IsValid()) ? *Hit.PhysMaterial->GetName() : TEXT("none"),
			bHit ? Hit.ImpactPoint.Z : 0.0f);
	}

	if (!bOnRoad && !bTimedOut)
	{
		return;   // keep waiting for the road surface specifically - retry next tick
	}

	if (bHit)
	{
		UE_LOG(LogTemp, Display,
			TEXT("ODVehiclePawn: ground-snap resolved after %.1fs - moving from z=%.0f to "
			     "z=%.0f (hit z=%.0f, physmat=%s, onRoad=%d)"),
			Now - SpawnTime, Loc.Z, Hit.ImpactPoint.Z + GroundClearanceCm, Hit.ImpactPoint.Z,
			Hit.PhysMaterial.IsValid() ? *Hit.PhysMaterial->GetName() : TEXT("none"), bOnRoad);
		if (bTimedOut && !bOnRoad)
		{
			UE_LOG(LogTemp, Warning,
				TEXT("ODVehiclePawn: ground-snap timed out after %.1fs waiting for the "
				     "road surface specifically - settling for whatever's below (%s)"),
				GroundSnapTimeoutSec,
				Hit.PhysMaterial.IsValid() ? *Hit.PhysMaterial->GetName() : TEXT("none"));
		}
		SetActorLocation(FVector(Loc.X, Loc.Y, Hit.ImpactPoint.Z + GroundClearanceCm),
			false, nullptr, ETeleportType::TeleportPhysics);
	}
	else
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODVehiclePawn: ground-snap timed out after %.1fs - no terrain found "
			     "below spawn, releasing physics at the original spawn height"),
			GroundSnapTimeoutSec);
	}

	Body->SetSimulatePhysics(true);
	Body->SetPhysicsLinearVelocity(FVector::ZeroVector);
	Body->SetPhysicsAngularVelocityInDegrees(FVector::ZeroVector);
	bGroundSnapped = true;
}

void AODVehiclePawn::SetupPlayerInputComponent(UInputComponent* Input)
{
	Super::SetupPlayerInputComponent(Input);
	struct FKeyFn
	{
		FKey Key;
		void (AODVehiclePawn::*Press)();
		void (AODVehiclePawn::*Release)();
	};
	const FKeyFn Binds[] = {
		{EKeys::W, &AODVehiclePawn::ThrottlePressed, &AODVehiclePawn::ThrottleReleased},
		{EKeys::Up, &AODVehiclePawn::ThrottlePressed, &AODVehiclePawn::ThrottleReleased},
		{EKeys::S, &AODVehiclePawn::BrakePressed, &AODVehiclePawn::BrakeReleased},
		{EKeys::Down, &AODVehiclePawn::BrakePressed, &AODVehiclePawn::BrakeReleased},
		{EKeys::A, &AODVehiclePawn::LeftPressed, &AODVehiclePawn::LeftReleased},
		{EKeys::Left, &AODVehiclePawn::LeftPressed, &AODVehiclePawn::LeftReleased},
		{EKeys::D, &AODVehiclePawn::RightPressed, &AODVehiclePawn::RightReleased},
		{EKeys::Right, &AODVehiclePawn::RightPressed, &AODVehiclePawn::RightReleased},
		{EKeys::SpaceBar, &AODVehiclePawn::HandbrakePressed, &AODVehiclePawn::HandbrakeReleased},
	};
	for (const FKeyFn& B : Binds)
	{
		Input->BindKey(B.Key, IE_Pressed, this, B.Press);
		Input->BindKey(B.Key, IE_Released, this, B.Release);
	}
	// V: cycle to the next car (garage groundwork - dev-facing live hot-swap)
	Input->BindKey(EKeys::V, IE_Pressed, this, &AODVehiclePawn::CycleVehicle);
}

void AODVehiclePawn::HandbrakePressed()
{
	Movement->SetHandbrakeInput(true);
}

void AODVehiclePawn::HandbrakeReleased()
{
	Movement->SetHandbrakeInput(false);
}

void AODVehiclePawn::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	if (!bGroundSnapped)
	{
		TrySnapToGround();
		if (!bGroundSnapped)
		{
			return;   // still frozen waiting for a real ground reading
		}
	}

	// scripted-drive hooks override the keys (headless verification)
	const float Now = GetWorld()->GetTimeSeconds();
	if (VehSwapAt > 0.0f && Now > VehSwapAt)
	{
		VehSwapAt = -1.0f;   // once
		CycleVehicle();
	}
	if (RevAt > 0.0f && Now > RevAt)
	{
		bThrottleKey = true;
	}
	if (LiftAt > 0.0f && Now > LiftAt)   // after odrev so lift wins
	{
		bThrottleKey = false;
	}
	if (BrakeAt > 0.0f && Now > BrakeAt)   // after odlift so brake wins
	{
		bBrakeKey = true;
	}

	// keyboard keys -> smoothed axes; the ONLY input shaping in the game
	const float SteerTarget =
		(bRightKey ? 1.0f : 0.0f) - (bLeftKey ? 1.0f : 0.0f);
	const float Rate = FMath::Abs(SteerTarget) > 0.01f &&
		FMath::Sign(SteerTarget) != FMath::Sign(SmSteer)
			? SteerRate + SteerReturnRate
			: (FMath::Abs(SteerTarget) > 0.01f ? SteerRate : SteerReturnRate);
	SmSteer = FMath::FInterpConstantTo(SmSteer, SteerTarget, DeltaSeconds, Rate);
	SmThrottle = FMath::FInterpConstantTo(
		SmThrottle, bThrottleKey ? HookThrottle : 0.0f, DeltaSeconds, PedalRate);
	SmBrake = FMath::FInterpConstantTo(
		SmBrake, bBrakeKey ? 1.0f : 0.0f, DeltaSeconds, PedalRate);
	if (SteerAt > 0.0f && Now > SteerAt)   // half steer (tire lateral tests)
	{
		SmSteer = 0.5f;
	}

	Movement->SetSteeringInput(SmSteer);
	Movement->SetThrottleInput(SmThrottle);
	Movement->SetBrakeInput(SmBrake);

	// wheel visuals: suspension travel + steer + spin from our sim
	for (int32 i = 0; i < WheelVisuals.Num(); ++i)
	{
		WheelSpinDeg[i] = FMath::Fmod(WheelSpinDeg[i]
			+ FMath::RadiansToDegrees(Movement->GetWheelOmega(i)) * DeltaSeconds,
			360.0f);
		WheelVisuals[i]->SetRelativeLocation(Movement->GetWheelLocalPos(i));
		WheelVisuals[i]->SetRelativeRotation(
			FRotator(-WheelSpinDeg[i], Movement->GetWheelSteerDeg(i), 0.0f));
	}
}
