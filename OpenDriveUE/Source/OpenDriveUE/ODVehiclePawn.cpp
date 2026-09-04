#include "ODVehiclePawn.h"

#include "AssetRegistry/ARFilter.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Camera/CameraComponent.h"
#include "Components/DynamicMeshComponent.h"
#include "Components/InputComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "GameFramework/SpringArmComponent.h"
#include "Kismet/GameplayStatics.h"
#include "GameFramework/PlayerController.h"
#include "Misc/CommandLine.h"
#include "Misc/ConfigCacheIni.h"
#include "ODTuningManager.h"
#include "ODVehicleDef.h"
#include "ODVehicleMovement.h"
#include "UObject/UObjectIterator.h"

namespace
{
const TCHAR* ChassisPath = TEXT("/Game/OpenDrive/Car/chassis.chassis");
const TCHAR* WheelPath = TEXT("/Game/OpenDrive/Car/wheel.wheel");

// pipeline/make_car_meshes.py's SM_CarWheel (procedural 4-car set,
// 2026-08-27) is authored once at this radius and shared by all four new
// UODVehicleDefs; ApplyVehicleDef below visually rescales it per car
// (Def->WheelRadiusCm / this) so e.g. Proto's smaller low-profile wheel and
// Sport's bigger one both read correctly with no extra wheel meshes. The
// older per-car wheel.obj/wheel_gt.obj are each already authored at their
// own radius (both 34), so this scale evaluates to 1.0 for them - no visual
// change to the existing placeholder/GT cars.
constexpr float ReferenceWheelMeshRadiusCm = 34.0f;
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
	BaseCameraPitchDeg = -12.0f;
	SpringArm->SetRelativeRotation(FRotator(BaseCameraPitchDeg, 0.0f, 0.0f));
	SpringArm->bEnableCameraLag = true;
	SpringArm->CameraLagSpeed = 9.0f;
	SpringArm->bEnableCameraRotationLag = true;
	SpringArm->CameraRotationLagSpeed = 9.0f;
	SpringArm->bInheritPitch = false;
	SpringArm->bInheritRoll = false;
	// Lets the normal chase-cam keep settling/lagging into place even while
	// paused - matters for AODMainMenu's OpenGarage() (mid-drive reopen),
	// which still pauses. The fresh-boot showroom no longer goes through
	// this arm at all (see AODVehiclePawn::SetShowroom) and no longer pauses
	// either (see AODMainMenu::OpenMenu()'s comment for why), so this is
	// specifically for the garage case now, not the boot flow that
	// originally motivated it.
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
	// so force a synchronous scan of their folder(s) before querying.
	// /Game/OpenDrive/Car (singular) holds the original placeholder Coupe/GT
	// defs; /Game/OpenDrive/Cars (plural) holds the 2026-08-27 procedural
	// 4-car set, imported into a separate folder specifically so it couldn't
	// collide with the originals - both need to be scanned or the plural
	// folder's defs are invisible to the asset-registry query below (seen
	// live: only 2 of 6 defs found before this fix).
	AR.ScanPathsSynchronous(
		{TEXT("/Game/OpenDrive/Car"), TEXT("/Game/OpenDrive/Cars")},
		/*bForceRescan*/true);

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
	// stable order, base "Coupe" used to sort to index 0 = the spawn car.
	// TObjectPtr Sort dereferences, so the predicate takes the element type
	// by ref.
	Vehicles.Sort([](const UODVehicleDef& A, const UODVehicleDef& B)
	{
		return A.DisplayName < B.DisplayName;
	});
	// 2026-09-03 art-direction pass: the new replica-style 5-car set
	// (import_car_meshes.py) should be the default spawn car, but plain
	// alphabetical order puts the stale placeholder "Coupe" first. Rather
	// than rely on naming to control spawn order, explicitly bubble
	// "Muscle" (the new default) to index 0 if it's present; every other
	// def (including the four other new cars, and the stale placeholders
	// left behind for a later manual cleanup pass) keeps its alphabetical
	// position and stays reachable via CycleVehicle().
	const int32 MuscleIdx = Vehicles.IndexOfByPredicate(
		[](const UODVehicleDef* Def) { return Def && Def->DisplayName == TEXT("Muscle"); });
	if (MuscleIdx > 0)
	{
		Vehicles.Swap(0, MuscleIdx);
	}
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
		// visual-only scale to match this car's WheelRadiusCm against the
		// mesh's own authored radius (see ReferenceWheelMeshRadiusCm above) -
		// does not touch UODVehicleMovement's wheel table, which already
		// takes WheelRadiusCm straight from the def for the physics radius.
		const float WheelVisualScale = Def->WheelRadiusCm / ReferenceWheelMeshRadiusCm;
		for (UStaticMeshComponent* WV : WheelVisuals)
		{
			if (WV)
			{
				WV->SetStaticMesh(Wheel);
				WV->SetRelativeScale3D(FVector(WheelVisualScale));
			}
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODVehiclePawn: wheel visuals set for '%s' - mesh '%s', radius %.0f cm ")
			TEXT("(mesh authored at %.0f cm, visual scale %.2f), %d wheel component(s)"),
			*Def->DisplayName, *Wheel->GetName(), Def->WheelRadiusCm,
			ReferenceWheelMeshRadiusCm, WheelVisualScale, WheelVisuals.Num());
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
	if (!Body || !SpringArm || !Camera)
	{
		return;
	}
	bInShowroom = bOn;
	if (bOn)
	{
		Body->SetSimulatePhysics(false);
		// level the car; AODMainMenu sets a fixed display yaw once (no spin)
		SetActorRotation(FRotator(0.0f, GetActorRotation().Yaw, 0.0f));

		// 2026-07-28: the showroom camera used to ride the same SpringArm as
		// the driving chase-cam (bInheritYaw=false meant to decouple it from
		// the spinning car). That never actually worked as intended - live
		// screenshots (Taylor: "I was still inside/under the car", repeated
		// through several rounds of fixes to arm length/collision/lag) showed
		// the SAME wrong framing regardless of what was changed, and a
		// logged dump proved the spring arm's own inheritance flags weren't
		// producing the documented behavior in practice. Rather than keep
		// chasing USpringArmComponent's inheritance system, the showroom
		// camera is detached from it entirely and placed with plain,
		// fully-deterministic vector math - full control, nothing implicit.
		Camera->DetachFromComponent(FDetachmentTransformRules::KeepWorldTransform);

		const FVector Pivot = GetActorLocation();
		const FVector Forward = GetActorForwardVector();
		const FVector Right = GetActorRightVector();
		const FVector CamPos = Pivot - Forward * ShowroomDistanceCm + FVector(0, 0, ShowroomHeightCm);
		// look at a point offset to one side of the car (not the camera) -
		// biasing the LOOK-AT target left pushes the car right in frame
		const FVector LookAt = Pivot - Right * ShowroomLookAtBiasCm + FVector(0, 0, 40.0f);
		Camera->SetWorldLocationAndRotation(CamPos, (LookAt - CamPos).Rotation());
	}
	else
	{
		// back to the normal chase cam - re-attach with the arm's usual
		// (relative) transform, not the showroom's absolute one
		Camera->AttachToComponent(SpringArm, FAttachmentTransformRules::SnapToTargetIncludingScale);

		// 2026-08-27 fix ("garage round-trip paralyzes the car"): this branch
		// never restored physics, only the camera. AODMainMenu::StartAt()
		// (fresh-boot / map-select path) gets away with it because it always
		// calls TeleportToSpawn() right after, which re-disables physics for
		// its own ground-snap wait and TrySnapToGround() re-enables it once
		// settled. But AODMainMenu::ResumeFromGarage() (pause-menu "Garage"
		// mid-drive round trip) calls ONLY SetShowroom(false) - no teleport,
		// no re-snap - so the car was left permanently kinematic (no physics,
		// so no input could move it) after returning from the garage. The car
		// is already resting on real ground going into the garage in that
		// case (bGroundSnapped stays true throughout), so it's safe to just
		// re-enable physics directly here; TeleportToSpawn's own
		// SetSimulatePhysics(false) immediately after this call in the other
		// path makes this a harmless no-op flicker for that case.
		Body->SetSimulatePhysics(true);
	}
}

void AODVehiclePawn::TeleportToSpawn(const FVector& RoadPoint, float YawDeg)
{
	if (!Body)
	{
		return;
	}
	SpringArm->bInheritYaw = true;   // ensure showroom framing is dropped
	LookYawDeg = 0.0f;   // re-center the freelook orbit behind the car for the new spawn
	LookPitchDeg = 0.0f;

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
	SnapReadyTime = 0.0;
	LastSnapLogTime = 0.0;
}

void AODVehiclePawn::ResetVehicle()
{
	// only meaningful while actually driving - not mid-spawn-settle (the
	// snap owns the body then) and not in the showroom (physics frozen)
	if (!Body || !bGroundSnapped || !Body->IsSimulatingPhysics())
	{
		return;
	}
	const float Yaw = GetActorRotation().Yaw;
	Body->SetSimulatePhysics(false);

	// find a CLEAR spot to right the car in: righting it in place inside a
	// tree trunk (Taylor: "hitting r near a tree merged me into the tree")
	// re-creates the exact interpenetration the lift was meant to avoid.
	// Test the upright hull at the current spot first, then a small ring of
	// sideways/back/forward offsets, and take the first that doesn't overlap
	// anything blocking; if everything is blocked, keep the current spot
	// (the settle's kinematic phase still prevents a violent ejection).
	const FQuat UprightRot = FRotator(0.0f, Yaw, 0.0f).Quaternion();
	const FVector HullExtent = Body->Bounds.BoxExtent * 0.9f;
	const FVector Fwd2D = FRotator(0.0f, Yaw, 0.0f).Vector();
	const FVector Right2D = FVector::CrossProduct(FVector::UpVector, Fwd2D);
	const FVector BaseLoc = GetActorLocation() + FVector(0.0f, 0.0f, 150.0f);
	const FVector Offsets[] = {
		FVector::ZeroVector,
		Right2D * 250.0f, Right2D * -250.0f,
		Fwd2D * -350.0f, Fwd2D * 350.0f,
		Right2D * 500.0f, Right2D * -500.0f,
	};
	FVector ChosenLoc = BaseLoc;
	FCollisionQueryParams Params;
	Params.AddIgnoredActor(this);
	for (const FVector& Off : Offsets)
	{
		if (!GetWorld()->OverlapBlockingTestByProfile(BaseLoc + Off, UprightRot,
			TEXT("Vehicle"), FCollisionShape::MakeBox(HullExtent), Params))
		{
			ChosenLoc = BaseLoc + Off;
			break;
		}
	}
	SetActorLocation(ChosenLoc, false, nullptr, ETeleportType::TeleportPhysics);
	SetActorRotation(FRotator(0.0f, Yaw, 0.0f));

	// re-arm the ground snap, but back-date SpawnTime so the no-asphalt
	// fallback fires after ~3 s instead of the full spawn timeout - a reset
	// often happens OFF the road (that's why the car flipped), where the
	// PM_Asphalt trace will never succeed.
	bGroundSnapped = false;
	SpawnTime = GetWorld()->GetTimeSeconds() - (GroundSnapTimeoutSec - 3.0);
	SnapReadyTime = 0.0;
	LastSnapLogTime = 0.0;
	UE_LOG(LogTemp, Display, TEXT("ODVehiclePawn: flip-recovery reset at %s"),
		*GetActorLocation().ToCompactString());
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

	// controller/wheel device selection (2026-08-27): restore whatever the
	// player last picked in the menu (or Auto, the default for a fresh install)
	DeviceMode = LoadInputDeviceMode();
	ActiveDevice = EODInputDevice::Keyboard;

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
	FParse::Value(FCommandLine::Get(), TEXT("odsetvat="), SetVAt);
	FParse::Value(FCommandLine::Get(), TEXT("odsetv="), SetVCmS);

	// -odvehidx=N: jump straight to vehicle def index N at spawn (headless
	// screenshot verification of a specific car - 2026-08-27, procedural car
	// generator pass). Unlike -odveh=T's single next-car cycle, this picks
	// an absolute index immediately so a batch of -odshot runs can each grab
	// one car without chaining cycle counts.
	int32 VehIdx = -1;
	if (FParse::Value(FCommandLine::Get(), TEXT("odvehidx="), VehIdx) && Vehicles.IsValidIndex(VehIdx))
	{
		SetVehicleByIndex(VehIdx);
	}

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
	// sample every tick (not just post-settle) so hitches during the settle
	// window already count against the quiet period
	if (GetWorld()->GetDeltaSeconds() > SnapHitchFrameSec)
	{
		LastHitchTime = GetWorld()->GetTimeSeconds();
	}

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

	// One-shot nudge to nearby real pavement (2026-09-03 fix, "car tips over
	// after Breckenridge Lincoln Ave settle"): the 20s timeout/fallback below
	// exists for a slow-loading road tile that eventually resolves - it was
	// never meant for a spawn XY that has NO road collision anywhere below it
	// at all, which is what Lincoln Ave turned out to be (confirmed via
	// -odtrace: the trace hit the same off-road DynamicMeshComponent, stable,
	// for the entire 20s wait). Waiting longer never finds pavement that
	// isn't there, and accepting whatever off-road surface IS there releases
	// the car onto ground with no flatness guarantee - which is exactly what
	// flipped it, even after leveling+priming the suspension for that surface
	// (both correctly matched a genuinely bad contact patch instead of fixing
	// it). Once 2s have passed without ever finding asphalt underneath - long
	// enough that this isn't just a not-yet-streamed-in tile - search a small
	// ring of nearby offsets for one that DOES hit real pavement directly,
	// and re-center the spawn XY there before falling through to the normal
	// wait/settle logic. Try only once so a genuinely isolated off-road spawn
	// still degrades to the old timeout/fallback rather than searching
	// forever.
	if (!bOnRoad && !bTriedOffRoadNudge && (Now - SpawnTime) > 2.0)
	{
		bTriedOffRoadNudge = true;
		bool bFoundNearbyRoad = false;
		FVector2D BestXY(Loc.X, Loc.Y);
		static const float Rings[] = { 500.0f, 1000.0f, 2000.0f, 4000.0f, 8000.0f };
		for (int32 RingIdx = 0; RingIdx < UE_ARRAY_COUNT(Rings) && !bFoundNearbyRoad; ++RingIdx)
		{
			for (int32 Dir = 0; Dir < 8; ++Dir)
			{
				const float AngRad = FMath::DegreesToRadians(Dir * 45.0f);
				const FVector2D Offset(FMath::Cos(AngRad) * Rings[RingIdx],
					FMath::Sin(AngRad) * Rings[RingIdx]);
				const FVector ProbeStart(Loc.X + Offset.X, Loc.Y + Offset.Y, Loc.Z + 10000.0f);
				const FVector ProbeEnd(Loc.X + Offset.X, Loc.Y + Offset.Y, Loc.Z - 500000.0f);
				FHitResult ProbeHit;
				if (GetWorld()->LineTraceSingleByChannel(ProbeHit, ProbeStart, ProbeEnd,
						ECC_Visibility, Params)
					&& ProbeHit.PhysMaterial.IsValid()
					&& ProbeHit.PhysMaterial->GetName().Contains(TEXT("Asphalt")))
				{
					BestXY = FVector2D(Loc.X + Offset.X, Loc.Y + Offset.Y);
					bFoundNearbyRoad = true;
					break;
				}
			}
		}
		if (bFoundNearbyRoad)
		{
			UE_LOG(LogTemp, Warning,
				TEXT("ODVehiclePawn: spawn (%.0f,%.0f) never found asphalt directly below "
				     "after %.1fs - nudging to nearby pavement at (%.0f,%.0f)"),
				Loc.X, Loc.Y, Now - SpawnTime, BestXY.X, BestXY.Y);
			SetActorLocation(FVector(BestXY.X, BestXY.Y, Loc.Z),
				false, nullptr, ETeleportType::TeleportPhysics);
			return;   // re-run next tick from the nudged XY
		}
		UE_LOG(LogTemp, Warning,
			TEXT("ODVehiclePawn: spawn (%.0f,%.0f) never found asphalt directly below after "
			     "%.1fs, and none found within an 80m ring search either - falling through to "
			     "the normal %.1fs timeout"),
			Loc.X, Loc.Y, Now - SpawnTime, GroundSnapTimeoutSec);
	}

	// periodic visibility into what we're actually hitting while waiting -
	// without this there's no way to tell "correctly waiting on non-asphalt"
	// from "finding nothing" from "some other silent failure"
	if (Now - LastSnapLogTime > 1.0)
	{
		LastSnapLogTime = Now;

		// 2026-08-26 diagnostic: direct measurement (not an inference from
		// separate subsystem logs) of how many road/terrain/pad collision
		// components are actually resident right now, to test whether the
		// open Chaos/UE5.8 collision-registration bug is keyed on a total
		// scene-wide count. TObjectIterator crosses all worlds, so filter to
		// this one.
		int32 TotalDynMeshCollision = 0;
		for (TObjectIterator<UDynamicMeshComponent> It; It; ++It)
		{
			if (It->GetWorld() == GetWorld() && It->IsRegistered()
				&& It->GetCollisionEnabled() != ECollisionEnabled::NoCollision)
			{
				++TotalDynMeshCollision;
			}
		}

		// Second diagnostic: LineTraceSingleByChannel only reports the FIRST
		// hit, which can't distinguish "the pad's collision genuinely isn't
		// queryable" from "the pad IS there and registered, but terrain
		// (regraded to sit within ~0.25m of the pad at a junction, closer
		// than mid-road's normal clearance) is winning the single-trace
		// ordering instead." A multi-trace along the same ray settles it
		// directly - if a hit whose PhysMaterial is Asphalt shows up in this
		// list at all, the pad's collision is registered and queryable, and
		// the single-trace behavior above is a hit-ordering issue, not the
		// registration bug.
		TArray<FHitResult> AllHits;
		GetWorld()->LineTraceMultiByChannel(AllHits, Start, End, ECC_Visibility, Params);
		FString HitsDesc;
		for (const FHitResult& H : AllHits)
		{
			HitsDesc += FString::Printf(TEXT("[%s z=%.0f mat=%s] "),
				H.Component.IsValid() ? *H.Component->GetName() : TEXT("?"),
				H.ImpactPoint.Z,
				H.PhysMaterial.IsValid() ? *H.PhysMaterial->GetName() : TEXT("none"));
		}

		UE_LOG(LogTemp, Display,
			TEXT("ODVehiclePawn: ground-snap waiting at (%.0f,%.0f,%.0f) t=%.1fs - "
			     "hit=%d onRoad=%d physmat=%s implZ=%.0f totalCollisionComponents=%d "
			     "allHits(%d)=%s"),
			Loc.X, Loc.Y, Loc.Z, Now - SpawnTime, bHit, bOnRoad,
			(bHit && Hit.PhysMaterial.IsValid()) ? *Hit.PhysMaterial->GetName() : TEXT("none"),
			bHit ? Hit.ImpactPoint.Z : 0.0f, TotalDynMeshCollision, AllHits.Num(), *HitsDesc);
	}

	if (!bOnRoad && !bTimedOut)
	{
		SnapReadyTime = 0.0;   // asphalt hit lapsed (or never came) - restart the settle window
		return;   // keep waiting for the road surface specifically - retry next tick
	}

	if (SnapReadyTime == 0.0)
	{
		SnapReadyTime = Now;
		UE_LOG(LogTemp, Display,
			TEXT("ODVehiclePawn: ground-snap resolved after %.1fs - moving from z=%.0f to "
			     "z=%.0f (hit z=%.0f, physmat=%s, onRoad=%d), holding %.1fs settle before "
			     "releasing physics"),
			Now - SpawnTime, Loc.Z, bHit ? Hit.ImpactPoint.Z + GroundClearanceCm : Loc.Z,
			bHit ? Hit.ImpactPoint.Z : 0.0f,
			(bHit && Hit.PhysMaterial.IsValid()) ? *Hit.PhysMaterial->GetName() : TEXT("none"),
			bOnRoad, GroundSnapSettleSec);
		if (bTimedOut && !bOnRoad)
		{
			UE_LOG(LogTemp, Warning,
				TEXT("ODVehiclePawn: ground-snap timed out after %.1fs waiting for the "
				     "road surface specifically - settling for whatever's below (%s)"),
				GroundSnapTimeoutSec,
				(bHit && Hit.PhysMaterial.IsValid()) ? *Hit.PhysMaterial->GetName() : TEXT("none"));
		}
		if (!bHit)
		{
			UE_LOG(LogTemp, Warning,
				TEXT("ODVehiclePawn: ground-snap timed out after %.1fs - no terrain found "
				     "below spawn, will release physics at the original spawn height"),
				GroundSnapTimeoutSec);
		}
	}
	if (bHit)
	{
		// keep tracking the surface each tick through the settle - if the
		// road's height shifts as neighboring tiles finish building, the car
		// follows rather than releasing at a stale height
		SetActorLocation(FVector(Loc.X, Loc.Y, Hit.ImpactPoint.Z + GroundClearanceCm),
			false, nullptr, ETeleportType::TeleportPhysics);
	}
	if (Now - SnapReadyTime < GroundSnapSettleSec)
	{
		return;   // held kinematic on the surface until collision has had time to register
	}

	// frame-stability gate (see header): a streaming hitch at release lets the
	// body tunnel through the road between game-thread wheel traces
	if (Now - LastHitchTime < SnapQuietSec && (Now - SpawnTime) <= GroundSnapTimeoutSec)
	{
		if (Now - LastSnapLogTime > 1.0)
		{
			LastSnapLogTime = Now;
			UE_LOG(LogTemp, Display,
				TEXT("ODVehiclePawn: ground-snap settled but holding through streaming "
				     "hitches (last long frame %.2fs ago, need %.2fs quiet)"),
				Now - LastHitchTime, SnapQuietSec);
		}
		return;
	}

	// Level the pose to the ground actually under all 4 wheels before handing
	// off to physics (2026-09-03 fix, "car tips over on flat pavement right
	// after settle" - reproduced at Breckenridge/Lincoln Ave). Everything
	// above this point only ever sampled ONE point (the body's own pivot)
	// and always released dead flat (0 pitch/roll). That single sample is a
	// fine approximation on an ordinary road, but it can be nowhere near
	// representative of what's under the four wheels on a curb edge, camber,
	// or - worse - the off-asphalt fallback surface above (ground-snap timed
	// out waiting for PM_Asphalt and settled for whatever it found instead,
	// which is exactly what happened at the Lincoln Ave repro: the trace hit
	// a DefaultPhysicalMaterial surface, not the road). Releasing flat onto
	// an uneven contact patch loads two wheels hard and leaves two hanging,
	// and that asymmetric spring force was enough to flip the car before the
	// player ever touched a pedal. Re-cast straight down at each wheel's XY,
	// fit a plane through the 4 hit points, and rotate/re-height the body to
	// sit flush on that plane instead of assuming dead-flat.
	if (Movement && Movement->NumWheels() == 4)
	{
		const FVector CenterLoc = GetActorLocation();
		const FQuat FlatQuat = FRotator(0.0f, GetActorRotation().Yaw, 0.0f).Quaternion();

		FVector WheelGroundPts[4];
		bool bAllWheelsHit = true;
		for (int32 i = 0; i < 4; ++i)
		{
			const FVector2D LocalXY = Movement->GetWheelAttachXY(i);
			const FVector WorldXY = CenterLoc
				+ FlatQuat.RotateVector(FVector(LocalXY.X, LocalXY.Y, 0.0f));
			FHitResult WHit;
			FCollisionQueryParams WParams;
			WParams.AddIgnoredActor(this);
			const bool bWHit = GetWorld()->LineTraceSingleByChannel(WHit,
				FVector(WorldXY.X, WorldXY.Y, CenterLoc.Z + 2000.0f),
				FVector(WorldXY.X, WorldXY.Y, CenterLoc.Z - 2000.0f),
				ECC_Visibility, WParams);
			if (bWHit)
			{
				WheelGroundPts[i] = WHit.ImpactPoint;
			}
			else
			{
				bAllWheelsHit = false;
				break;
			}
		}

		if (bAllWheelsHit)
		{
			// wheel order is FL,FR,RL,RR (ODVehicleMovement::SetupWheels)
			const FVector FrontMid = (WheelGroundPts[0] + WheelGroundPts[1]) * 0.5f;
			const FVector RearMid = (WheelGroundPts[2] + WheelGroundPts[3]) * 0.5f;
			const FVector RightMid = (WheelGroundPts[1] + WheelGroundPts[3]) * 0.5f;
			const FVector LeftMid = (WheelGroundPts[0] + WheelGroundPts[2]) * 0.5f;

			const FVector FwdVec = (FrontMid - RearMid).GetSafeNormal();
			const FVector RightVec = (RightMid - LeftMid).GetSafeNormal();
			FVector UpVec = FVector::CrossProduct(RightVec, FwdVec).GetSafeNormal();
			if (UpVec.Z < 0.0f)
			{
				UpVec = -UpVec;
			}

			// Never trust a near-vertical "ground" normal - that's a wall/curb
			// FACE the corner raycasts grazed, not a drivable surface, and
			// pitching the body to match it would be worse than the flat pose
			// this is replacing. Only re-level within ~60 deg of upright.
			if (!UpVec.ContainsNaN() && UpVec.Z > 0.5f)
			{
				const FQuat LevelQuat = FQuat::FindBetweenNormals(FVector::UpVector, UpVec) * FlatQuat;
				const FVector PlaneCenter = (FrontMid + RearMid) * 0.5f;
				SetActorLocation(
					FVector(CenterLoc.X, CenterLoc.Y, PlaneCenter.Z + GroundClearanceCm),
					false, nullptr, ETeleportType::TeleportPhysics);
				SetActorRotation(LevelQuat.Rotator());
			}
		}
	}

	// Prime the suspension's per-wheel spring state from the FINAL pose above
	// (leveled or not) before physics goes live. TickSuspension only ever
	// runs while the body is already simulating physics, so every wheel's
	// SpringLen has sat frozen at its default (20.0f, i.e. "no compression")
	// for the whole kinematic lift+snap+settle sequence regardless of where
	// the car actually ended up - the very first real tick after
	// SetSimulatePhysics(true) would otherwise read that stale default
	// against the true first-hit distance as a huge one-tick "spring
	// velocity" and fire a real damper-force spike from it. Four corners
	// rarely have identical true gaps, so those spikes land unevenly and
	// that was enough net torque to flip the car before the settle pose
	// (leveled or the plain-flat fallback) ever got a fair test - this is
	// what was still tipping the car at the Breckenridge Lincoln Ave repro
	// even after the leveling pass above started producing a correct plane.
	if (Movement)
	{
		Movement->PrimeSuspension();
	}

	Body->SetSimulatePhysics(true);
	Body->SetPhysicsLinearVelocity(FVector::ZeroVector);
	Body->SetPhysicsAngularVelocityInDegrees(FVector::ZeroVector);
	// parking hold until the driver's first pedal input - a spawn on a real
	// grade must not roll away on its own (see EngageParkHold's comment)
	if (Movement)
	{
		Movement->EngageParkHold();
	}
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
	// R: flip recovery - right the car in place (see ResetVehicle)
	Input->BindKey(EKeys::R, IE_Pressed, this, &AODVehiclePawn::ResetVehicle);

	SetupGamepadAndWheelInput(Input);
}

// ===========================================================================
// Controller (gamepad) + racing wheel input (2026-08-27) - see the member
// declarations' block comment in ODVehiclePawn.h for the design rationale.
// ===========================================================================

namespace
{
// FInputAxisKeyBinding's constructor does `ensure(AxisKey.IsAxis1D())` - an
// unregistered key (IsValid() false, e.g. a RawInput GenericUSBController_
// AxisN name the plugin never got to register - see below) OR a registered
// but non-analog key (IsValid() true, IsAxis1D() false - e.g. a digital
// button-style key) both fail that check and fire a "Handled ensure" on
// EVERY BindAxisKey call for that key, every time this pawn spawns. Guard
// every axis-key bind through here instead of trusting the FKey by name.
bool IsBindableAxis1DKey(const FKey& Key, const TCHAR* DebugName)
{
	if (!Key.IsValid() || !Key.IsAxis1D())
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODVehiclePawn: skipping axis bind for '%s' (key '%s') - not a registered 1D axis key "
			     "(IsValid=%d, IsAxis1D=%d). Expected with zero controllers/wheels connected for "
			     "device-specific keys; anything else is a key-name bug."),
			DebugName, *Key.ToString(), Key.IsValid(), Key.IsValid() && Key.IsAxis1D());
		return false;
	}
	return true;
}
}

void AODVehiclePawn::SetupGamepadAndWheelInput(UInputComponent* Input)
{
	// --- gamepad: standard XInput keys, no project-settings axis mapping
	// needed (same DIRECT-input philosophy as the keyboard binds above).
	// NOTE (2026-08-27 bug fix): Gamepad_LeftTrigger/Gamepad_RightTrigger are
	// the DIGITAL (ButtonAxis) trigger keys, not analog - EKeys registers
	// those with FKeyDetails::ButtonAxis, NOT Axis1D, so binding them via
	// BindAxisKey ensure-fired on IsAxis1D() every time this pawn spawned,
	// with or without a gamepad plugged in. The actual analog axis keys are
	// Gamepad_LeftTriggerAxis/Gamepad_RightTriggerAxis (flagged Axis1D).
	if (IsBindableAxis1DKey(EKeys::Gamepad_LeftX, TEXT("GamepadSteer")))
	{
		Input->BindAxisKey(EKeys::Gamepad_LeftX, this, &AODVehiclePawn::GamepadSteerAxis);
	}
	if (IsBindableAxis1DKey(EKeys::Gamepad_RightTriggerAxis, TEXT("GamepadThrottle")))
	{
		Input->BindAxisKey(EKeys::Gamepad_RightTriggerAxis, this, &AODVehiclePawn::GamepadThrottleAxis);
	}
	if (IsBindableAxis1DKey(EKeys::Gamepad_LeftTriggerAxis, TEXT("GamepadBrake")))
	{
		Input->BindAxisKey(EKeys::Gamepad_LeftTriggerAxis, this, &AODVehiclePawn::GamepadBrakeAxis);
	}
	if (IsBindableAxis1DKey(EKeys::Gamepad_RightX, TEXT("GamepadLookX")))
	{
		Input->BindAxisKey(EKeys::Gamepad_RightX, this, &AODVehiclePawn::GamepadLookXAxis);
	}
	if (IsBindableAxis1DKey(EKeys::Gamepad_RightY, TEXT("GamepadLookY")))
	{
		Input->BindAxisKey(EKeys::Gamepad_RightY, this, &AODVehiclePawn::GamepadLookYAxis);
	}
	Input->BindKey(EKeys::Gamepad_FaceButton_Bottom, IE_Pressed, this, &AODVehiclePawn::GamepadHandbrakePressed);
	Input->BindKey(EKeys::Gamepad_FaceButton_Bottom, IE_Released, this, &AODVehiclePawn::GamepadHandbrakeReleased);
	// menu navigation while driving-screen input is live is cheap to add here
	// (AODMainMenu binds its OWN separate Dpad/face-button keys for the menu
	// stage itself - these are just so a gamepad-only player isn't stuck
	// needing a keyboard to reach the menu's Drive/Resume row from in-car).
	Input->BindKey(EKeys::Gamepad_Special_Right, IE_Pressed, this, &AODVehiclePawn::CycleVehicle);

	// --- racing wheel: RawInput plugin's GenericUSBController_AxisN, bound
	// via manual-set lambdas (index-parametrized) rather than named per-axis
	// functions - see the RawWheelAxis[] declaration comment for why. These
	// keys only actually fire if the RawInput plugin is enabled (it is, see
	// OpenDriveUE.uproject) AND its default-device auto-registration picked
	// up a connected HID joystick/wheel; with nothing plugged in they simply
	// never fire and RawWheelAxis[] stays at its startup default. The
	// IsBindableAxis1DKey guard covers the case (confirmed live, 2026-08-27)
	// where the plugin hasn't registered these FNames as keys at all yet -
	// FKey::IsValid() is false for an unregistered name, so the ensure that
	// used to fire here is now a one-line warning log instead. */
	for (int32 i = 0; i < NumWheelAxesBound; ++i)
	{
		const FKey WheelAxisKey(FName(*FString::Printf(TEXT("GenericUSBController_Axis%d"), i + 1)));
		if (!IsBindableAxis1DKey(WheelAxisKey, *FString::Printf(TEXT("WheelAxis%d"), i + 1)))
		{
			continue;
		}
		Input->BindAxisKey(WheelAxisKey).AxisDelegate.GetDelegateForManualSet().BindLambda(
			[this, i](float Value)
			{
				RawWheelAxis[i] = Value;
			});
	}
	// Button 1 is a guess at "the first button on the wheel" for the
	// handbrake (most wheels put paddle-shifters/face buttons there) -
	// remap by changing this one key if a specific wheel differs. BindKey
	// (digital) has no IsAxis1D()-style ensure, so no guard needed here.
	Input->BindKey(FKey(FName(TEXT("GenericUSBController_Button1"))), IE_Pressed,
		this, &AODVehiclePawn::WheelHandbrakePressed);
	Input->BindKey(FKey(FName(TEXT("GenericUSBController_Button1"))), IE_Released,
		this, &AODVehiclePawn::WheelHandbrakeReleased);
}

void AODVehiclePawn::GamepadSteerAxis(float Value)
{
	// deadzone, then a mild x*|x| response curve (Taylor: "an analog stick
	// ... a mild response curve, x*|x| feels right for steering") - full
	// precision near center, full lock still reachable at full deflection.
	// Deliberately NOT applied to keyboard (SmSteer's own fade/rate logic
	// above is untouched) or to the wheel (1:1, no curve - see class notes).
	const float Mag = FMath::Abs(Value);
	if (Mag < GamepadDeadzone)
	{
		GamepadSteerRaw = 0.0f;
		return;
	}
	// rescale so the curve still reaches +/-1 at full deflection instead of
	// leaving a dead gap just past the deadzone
	const float Rescaled = (Mag - GamepadDeadzone) / (1.0f - GamepadDeadzone);
	GamepadSteerRaw = FMath::Sign(Value) * Rescaled * Rescaled;
}

void AODVehiclePawn::GamepadThrottleAxis(float Value)
{
	GamepadThrottleRaw = Value < GamepadDeadzone ? 0.0f : Value;
}

void AODVehiclePawn::GamepadBrakeAxis(float Value)
{
	GamepadBrakeRaw = Value < GamepadDeadzone ? 0.0f : Value;
}

void AODVehiclePawn::GamepadHandbrakePressed() { bGamepadHandbrake = true; }
void AODVehiclePawn::GamepadHandbrakeReleased() { bGamepadHandbrake = false; }

namespace
{
const TCHAR* InputDeviceConfigSection = TEXT("OpenDrive.Input");
const TCHAR* InputDeviceConfigKey = TEXT("DeviceMode");
}

void AODVehiclePawn::SaveInputDeviceMode(EODInputDevice Mode)
{
	GConfig->SetInt(InputDeviceConfigSection, InputDeviceConfigKey,
		static_cast<int32>(Mode), GGameUserSettingsIni);
	GConfig->Flush(false, GGameUserSettingsIni);
}

EODInputDevice AODVehiclePawn::LoadInputDeviceMode()
{
	int32 Saved = static_cast<int32>(EODInputDevice::Auto);
	GConfig->GetInt(InputDeviceConfigSection, InputDeviceConfigKey, Saved, GGameUserSettingsIni);
	if (Saved < 0 || Saved > static_cast<int32>(EODInputDevice::Wheel))
	{
		Saved = static_cast<int32>(EODInputDevice::Auto);
	}
	return static_cast<EODInputDevice>(Saved);
}

void AODVehiclePawn::SetInputDeviceMode(EODInputDevice Mode)
{
	DeviceMode = Mode;
	SaveInputDeviceMode(Mode);
}

FString AODVehiclePawn::InputDeviceModeToString(EODInputDevice Mode)
{
	switch (Mode)
	{
	case EODInputDevice::Auto: return TEXT("Auto");
	case EODInputDevice::Keyboard: return TEXT("Keyboard");
	case EODInputDevice::Gamepad: return TEXT("Gamepad");
	case EODInputDevice::Wheel: return TEXT("Wheel");
	default: return TEXT("?");
	}
}

void AODVehiclePawn::UpdateActiveInputDevice()
{
	const double Now = GetWorld()->GetTimeSeconds();

	// keyboard: any drive key currently held counts as "just used"
	if (bThrottleKey || bBrakeKey || bLeftKey || bRightKey)
	{
		LastKeyboardInputTime = Now;
	}

	// gamepad: latched value changed since last tick (covers stick return-
	// to-center too, which is itself a real "still touching it" signal)
	if (GamepadSteerRaw != PrevGamepadSteerRaw || GamepadThrottleRaw != PrevGamepadThrottleRaw
		|| GamepadBrakeRaw != PrevGamepadBrakeRaw || bGamepadHandbrake)
	{
		LastGamepadInputTime = Now;
	}
	PrevGamepadSteerRaw = GamepadSteerRaw;
	PrevGamepadThrottleRaw = GamepadThrottleRaw;
	PrevGamepadBrakeRaw = GamepadBrakeRaw;

	// wheel: any bound axis moved since last tick (pedals commonly idle at a
	// hard extreme rather than 0.5, so "away from center" can't tell idle
	// from active - a CHANGE can)
	bool bWheelChanged = bWheelHandbrake;
	for (int32 i = 0; i < NumWheelAxesBound; ++i)
	{
		if (!FMath::IsNearlyEqual(RawWheelAxis[i], PrevWheelAxis[i], 0.0005f))
		{
			bWheelChanged = true;
		}
		PrevWheelAxis[i] = RawWheelAxis[i];
	}
	if (bWheelChanged)
	{
		LastWheelInputTime = Now;
	}

	if (DeviceMode != EODInputDevice::Auto)
	{
		ActiveDevice = DeviceMode;
		return;
	}
	// Auto: last device wins. Ties (all zero, e.g. right at BeginPlay) fall
	// back to Keyboard so a keyboard-only player sees exactly today's
	// behavior with no gamepad/wheel plugged in.
	ActiveDevice = EODInputDevice::Keyboard;
	double Best = LastKeyboardInputTime;
	if (LastGamepadInputTime > Best)
	{
		Best = LastGamepadInputTime;
		ActiveDevice = EODInputDevice::Gamepad;
	}
	if (LastWheelInputTime > Best)
	{
		ActiveDevice = EODInputDevice::Wheel;
	}
}

void AODVehiclePawn::HandbrakePressed()
{
	Movement->SetHandbrakeInput(true);
}

void AODVehiclePawn::HandbrakeReleased()
{
	Movement->SetHandbrakeInput(false);
}

void AODVehiclePawn::UpdateFreeLook(float DeltaSeconds)
{
	// raw poll, same DIRECT-input philosophy as the keyboard axes below - no
	// axis mapping needed, so nothing to configure in project settings. Signs
	// match the classic UE Turn/LookUp convention (Turn uses raw MouseX
	// as-is; LookUp negates raw MouseY) even though there's no axis mapping
	// actually applying that scale for us here.
	APlayerController* PC = Cast<APlayerController>(GetController());
	if (!PC)
	{
		return;
	}
	float MouseX = 0.0f, MouseY = 0.0f;
	PC->GetInputMouseDelta(MouseX, MouseY);
	// gamepad right stick (2026-08-27): same freelook, fed as an additional
	// delta source alongside the mouse - a stick reports a held POSITION, not
	// a per-frame delta like the mouse, so it's scaled by DeltaSeconds here
	// (a small deadzone keeps stick noise from fighting the recenter timer).
	if (FMath::Abs(GamepadLookX) > GamepadDeadzone)
	{
		MouseX += GamepadLookX * GamepadLookSensitivityDegPerSec * DeltaSeconds;
	}
	if (FMath::Abs(GamepadLookY) > GamepadDeadzone)
	{
		MouseY += -GamepadLookY * GamepadLookSensitivityDegPerSec * DeltaSeconds;
	}
	if (MouseX != 0.0f || MouseY != 0.0f)
	{
		LastLookInputTime = GetWorld()->GetTimeSeconds();
		LookYawDeg = FMath::UnwindDegrees(LookYawDeg + MouseX * MouseLookSensitivity);
		// vertical freelook is inverted vs. the classic LookUp convention noted
		// above: pushing the mouse/stick up now pans the freelook view down.
		LookPitchDeg = FMath::Clamp(
			LookPitchDeg + MouseY * MouseLookSensitivity, LookPitchMinDeg, LookPitchMaxDeg);
	}
	else if (GetWorld()->GetTimeSeconds() - LastLookInputTime > LookRecenterDelaySec)
	{
		// hands off the mouse for a beat: ease back to the straight-ahead
		// chase view. FInterpTo on the unwound yaw takes the short way
		// around (e.g. from 350 deg it eases through 360, not back across
		// the car), because UnwindDegrees keeps yaw in [-180, 180].
		if (LookYawDeg == 0.0f && LookPitchDeg == 0.0f)
		{
			return;
		}
		LookYawDeg = FMath::FInterpTo(LookYawDeg, 0.0f, DeltaSeconds, LookRecenterSpeed);
		LookPitchDeg = FMath::FInterpTo(LookPitchDeg, 0.0f, DeltaSeconds, LookRecenterSpeed);
		if (FMath::Abs(LookYawDeg) < 0.05f && FMath::Abs(LookPitchDeg) < 0.05f)
		{
			LookYawDeg = 0.0f;
			LookPitchDeg = 0.0f;
		}
	}
	else
	{
		return;
	}
	SpringArm->SetRelativeRotation(FRotator(BaseCameraPitchDeg + LookPitchDeg, LookYawDeg, 0.0f));
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

	// only orbit the camera once the car is actually driveable - polling
	// GetInputMouseDelta() from BeginPlay onward (as this used to) soaks up
	// whatever mouse movement happens during the menu-to-drive handoff (e.g.
	// clicking "Drive" while the cursor is still visible/free for UI), so by
	// the time the car appears LookYawDeg already has a large accumulated
	// offset baked in - looked exactly like a frozen, off-to-the-side camera
	// that mouse nudges barely dented.
	UpdateFreeLook(DeltaSeconds);

	// controller/wheel device selection (2026-08-27) - resolves ActiveDevice
	// (Auto = last-device-wins) from this frame's latched axis/key state.
	UpdateActiveInputDevice();

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
	if (SetVAt > 0.0f && Now > SetVAt && Body->IsSimulatingPhysics())
	{
		SetVAt = -1.0f;   // once
		Body->SetPhysicsLinearVelocity(GetActorForwardVector() * SetVCmS);
		UE_LOG(LogTemp, Display,
			TEXT("ODVehiclePawn SCRIPTDRIVE: velocity hard-set to %.0f cm/s"),
			SetVCmS);
	}

	// keyboard keys -> smoothed axes; the ONLY input shaping in the game
	float SteerTarget =
		(bRightKey ? 1.0f : 0.0f) - (bLeftKey ? 1.0f : 0.0f);
	// speed-sensitive scale (see the UPROPERTY comment): both the lock the
	// key can command AND the rate it ramps there shrink with speed; the
	// return-to-center rate is deliberately left at full strength.
	const float FadeT = FMath::Clamp(
		(FMath::Abs(Movement->GetForwardSpeed()) - SteerFadeStartCmS) /
			FMath::Max(SteerFadeEndCmS - SteerFadeStartCmS, 1.0f),
		0.0f, 1.0f);
	const float FadeS = FadeT * FadeT * (3.0f - 2.0f * FadeT);
	const float SteerScale = FMath::Lerp(1.0f, HighSpeedSteerFrac, FadeS);
	SteerTarget *= SteerScale;
	const float Rate = FMath::Abs(SteerTarget) > 0.01f &&
		FMath::Sign(SteerTarget) != FMath::Sign(SmSteer)
			? SteerRate * SteerScale + SteerReturnRate
			: (FMath::Abs(SteerTarget) > 0.01f ? SteerRate * SteerScale : SteerReturnRate);
	SmSteer = FMath::FInterpConstantTo(SmSteer, SteerTarget, DeltaSeconds, Rate);
	SmThrottle = FMath::FInterpConstantTo(
		SmThrottle, bThrottleKey ? HookThrottle : 0.0f, DeltaSeconds, PedalRate);
	SmBrake = FMath::FInterpConstantTo(
		SmBrake, bBrakeKey ? 1.0f : 0.0f, DeltaSeconds, PedalRate);
	if (SteerAt > 0.0f && Now > SteerAt)   // half steer (tire lateral tests)
	{
		SmSteer = 0.5f;
	}

	// controller/wheel device selection (2026-08-27): feed Movement from
	// whichever device is active this tick. Gamepad/wheel axes are already
	// their own final values (deadzone+curve applied in the Gamepad*Axis
	// callbacks above; wheel is 1:1 below) - they do NOT go through
	// SmSteer/SmThrottle/SmBrake, which stay exactly the keyboard path.
	if (ActiveDevice == EODInputDevice::Gamepad)
	{
		Movement->SetSteeringInput(GamepadSteerRaw);
		Movement->SetThrottleInput(GamepadThrottleRaw);
		Movement->SetBrakeInput(GamepadBrakeRaw);
		Movement->SetHandbrakeInput(bGamepadHandbrake);
	}
	else if (ActiveDevice == EODInputDevice::Wheel)
	{
		// wheel: full range, 1:1, NO deadzone and NO response curve (unlike
		// the gamepad stick above) - only the configurable degrees-of-
		// rotation scale and per-pedal invert flags, per the task spec.
		auto WheelAxis = [this](int32 OneBasedIndex) -> float
		{
			const int32 Idx = FMath::Clamp(OneBasedIndex - 1, 0, NumWheelAxesBound - 1);
			return RawWheelAxis[Idx];
		};
		const float SteerRaw01 = WheelAxis(WheelSteerAxisIndex);
		const float WheelSteer = FMath::Clamp(
			(SteerRaw01 * 2.0f - 1.0f) * WheelDegreesOfRotationScale, -1.0f, 1.0f);
		float WheelThrottle = WheelAxis(WheelThrottleAxisIndex);
		float WheelBrake = WheelAxis(WheelBrakeAxisIndex);
		if (bWheelInvertThrottle) { WheelThrottle = 1.0f - WheelThrottle; }
		if (bWheelInvertBrake) { WheelBrake = 1.0f - WheelBrake; }
		Movement->SetSteeringInput(WheelSteer);
		Movement->SetThrottleInput(WheelThrottle);
		Movement->SetBrakeInput(WheelBrake);
		Movement->SetHandbrakeInput(bWheelHandbrake);
	}
	else   // Keyboard (also the Auto-mode default with nothing else plugged in)
	{
		Movement->SetSteeringInput(SmSteer);
		Movement->SetThrottleInput(SmThrottle);
		Movement->SetBrakeInput(SmBrake);
		// handbrake already applied directly by HandbrakePressed/Released
	}

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

	// rollover tripwire (2026-08-27): Taylor reported hard braking at speed
	// launching multi-rotation barrel rolls. Log the full state the moment
	// roll goes wild so headless scripted-drive repros (-odrev/-odbrake) can
	// pin down WHICH force starts it (tire Fy vs chassis-nose ground contact
	// vs a carve edge). Throttled; silent in normal driving.
	if (bGroundSnapped && Body->IsSimulatingPhysics())
	{
		const FVector AngVelLocal = Body->GetComponentTransform()
			.InverseTransformVectorNoScale(Body->GetPhysicsAngularVelocityInDegrees());
		const float RollDeg = GetActorRotation().Roll;
		if ((FMath::Abs(AngVelLocal.X) > 300.0f || FMath::Abs(RollDeg) > 60.0f)
			&& GetWorld()->GetTimeSeconds() - LastRollLogTime > 0.25)
		{
			LastRollLogTime = GetWorld()->GetTimeSeconds();
			UE_LOG(LogTemp, Warning,
				TEXT("ODVehiclePawn ROLLOVER: v=%.0f cm/s roll=%.0f pitch=%.0f "
				     "angvel(local xyz)=%.0f/%.0f/%.0f brake=%.2f steer=%.2f "
				     "contact=%d%d%d%d z=%.0f"),
				Movement->GetForwardSpeed(), RollDeg, GetActorRotation().Pitch,
				AngVelLocal.X, AngVelLocal.Y, AngVelLocal.Z, SmBrake, SmSteer,
				Movement->IsWheelInContact(0) ? 1 : 0,
				Movement->IsWheelInContact(1) ? 1 : 0,
				Movement->IsWheelInContact(2) ? 1 : 0,
				Movement->IsWheelInContact(3) ? 1 : 0,
				GetActorLocation().Z);
		}

		// 1 Hz scripted-drive telemetry: only when a headless hook is armed
		// (-odrev/-odbrake/...), so real play stays silent. Exists because the
		// first BrakeRoll repro ran 60 s "clean" while the car was actually
		// stuck off-road at ~10% speed the whole time - a repro is only
		// trustworthy if the log proves the speed it braked from.
		if ((RevAt > 0.0f || BrakeAt > 0.0f || SteerAt > 0.0f || LiftAt > 0.0f)
			&& GetWorld()->GetTimeSeconds() - LastScriptLogTime > 1.0)
		{
			LastScriptLogTime = GetWorld()->GetTimeSeconds();
			UE_LOG(LogTemp, Display,
				TEXT("ODVehiclePawn SCRIPTDRIVE: t=%.1f v=%.0f cm/s roll=%.1f "
				     "pitch=%.1f thr=%.2f brake=%.2f steer=%.2f contact=%d%d%d%d "
				     "pos=%.0f/%.0f/%.0f"),
				GetWorld()->GetTimeSeconds(), Movement->GetForwardSpeed(),
				GetActorRotation().Roll, GetActorRotation().Pitch,
				SmThrottle, SmBrake, SmSteer,
				Movement->IsWheelInContact(0) ? 1 : 0,
				Movement->IsWheelInContact(1) ? 1 : 0,
				Movement->IsWheelInContact(2) ? 1 : 0,
				Movement->IsWheelInContact(3) ? 1 : 0,
				GetActorLocation().X, GetActorLocation().Y, GetActorLocation().Z);
		}
	}
}
