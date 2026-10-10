#include "ODVehiclePawn.h"
#include "ODAssetDetail.h"

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
#include "EngineUtils.h"
#include "ODInputConfig.h"
#include "ODRoadManager.h"
#include "ODRoadNoiseSynth.h"
#include "PhysicalMaterials/PhysicalMaterial.h"
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
		if (UStaticMesh* Chassis = ODAssetDetail::Upgrade(LoadObject<UStaticMesh>(nullptr, ChassisPath)))
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
		if (UStaticMesh* Wheel = ODAssetDetail::Upgrade(LoadObject<UStaticMesh>(nullptr, WheelPath)))
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
	// so force a synchronous scan of their folder before querying. Only
	// /Game/OpenDrive/Cars (plural, import_car_meshes.py's current set) -
	// /Game/OpenDrive/Car (singular) holds the original placeholder Coupe/GT
	// defs, dropped from the menu 2026-09-28 (its chassis/wheel meshes stay:
	// EnsureMeshes' no-def fallback uses them).
	AR.ScanPathsSynchronous({TEXT("/Game/OpenDrive/Cars")}, /*bForceRescan*/true);

	// Superseded 2026-09-03 but still on disk (Content isn't in git, so they
	// weren't deleted - see import_car_meshes.py's docstring).
	static const FName StaleDefs[] = {
		TEXT("DA_Vehicle_Sedan"), TEXT("DA_Vehicle_CoupeS"),
		TEXT("DA_Vehicle_Sport"), TEXT("DA_Vehicle_Proto"),
	};

	FARFilter Filter;
	Filter.ClassPaths.Add(UODVehicleDef::StaticClass()->GetClassPathName());
	Filter.bRecursiveClasses = true;
	Filter.PackagePaths.Add(TEXT("/Game/OpenDrive/Cars"));
	TArray<FAssetData> Found;
	AR.GetAssets(Filter, Found);
	for (const FAssetData& A : Found)
	{
		if (MakeArrayView(StaleDefs).Contains(A.AssetName))
		{
			continue;
		}
		if (UODVehicleDef* Def = Cast<UODVehicleDef>(A.GetAsset()))
		{
			Vehicles.Add(Def);
		}
	}
	// stable alphabetical order; Muscle is bubbled to index 0 below.
	// TObjectPtr Sort dereferences, so the predicate takes the element type
	// by ref.
	Vehicles.Sort([](const UODVehicleDef& A, const UODVehicleDef& B)
	{
		return A.DisplayName < B.DisplayName;
	});
	// 2026-09-03 art-direction pass: the new replica-style 5-car set
	// (import_car_meshes.py) should be the default spawn car, but plain
	// alphabetical order would put "Concept" first. Rather than rely on
	// naming to control spawn order, explicitly bubble "Muscle" (the new
	// default) to index 0 if it's present; every other def keeps its
	// alphabetical position and stays reachable via CycleVehicle().
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

	if (UStaticMesh* Chassis = ODAssetDetail::Upgrade(Def->ChassisMesh.LoadSynchronous()))
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

	if (UStaticMesh* Wheel = ODAssetDetail::Upgrade(Def->WheelMesh.LoadSynchronous()))
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
	// 6 km, was 1 km (2026-10-05, large world): California's frame is sea-level
	// datum with 4.4 km peaks; TrySnapToGround's trace reaches 10 km down.
	Body->SetSimulatePhysics(false);
	SetActorLocation(FVector(RoadPoint.X, RoadPoint.Y, 600000.0f),
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
	bCrashTrace = FParse::Param(FCommandLine::Get(), TEXT("odcrashtrace"));

	// crash response (PLAN-REALISM.md Phase 0): CCD so a 100 mph hit (~75 cm
	// a frame) can't tunnel into a building tile's thin trimesh, and a capped
	// initial-overlap push-out for whatever still gets in. Set here rather
	// than as constructor defaults so they win over anything serialized; both
	// are BodyInstance fields, so they survive ApplyVehicleDef's SetStaticMesh.
	Body->SetUseCCD(true);
	Body->BodyInstance.SetMaxDepenetrationVelocity(CrashMaxDepenetrationCmS);
	Body->OnComponentHit.AddUniqueDynamic(this, &AODVehiclePawn::OnBodyHit);

	// -odvehidx=N: jump straight to vehicle def index N at spawn (headless
	// screenshot verification of a specific car - 2026-08-27, procedural car
	// generator pass). Unlike -odveh=T's single next-car cycle, this picks
	// an absolute index immediately so a batch of -odshot runs can each grab
	// one car without chaining cycle counts.
	// last car picked in the menu's Garage (AODMainMenu::SavePicks); invalid -> stays 0
	int32 VehIdx = -1;
	GConfig->GetInt(TEXT("OpenDrive.Car"), TEXT("VehicleIdx"), VehIdx, GGameUserSettingsIni);
	FParse::Value(FCommandLine::Get(), TEXT("odvehidx="), VehIdx);
	// same index as a travel-URL option (2026-10-05): the garage's Area row
	// carries the picked car across OpenLevel this way (AODMainMenu::OpenArea)
	if (const TCHAR* Opt = GetWorld()->URL.GetOption(TEXT("odvehidx="), nullptr))
	{
		VehIdx = FCString::Atoi(Opt);
	}
	if (Vehicles.IsValidIndex(VehIdx))
	{
		SetVehicleByIndex(VehIdx);
	}

	// -odcar=<DisplayName>: same idea as -odvehidx= but by name, so a QA
	// script doesn't need to know (or hardcode) the current alphabetical/
	// Muscle-bubbled index of a given preset (2026-09-03, Super car
	// in-game verification pass, §7.15). Case-insensitive exact match on
	// UODVehicleDef::DisplayName; no-op (falls back to the default spawn
	// car) if not found.
	FString CarName;
	if (FParse::Value(FCommandLine::Get(), TEXT("odcar="), CarName))
	{
		const int32 CarIdx = Vehicles.IndexOfByPredicate(
			[&CarName](const UODVehicleDef* Def)
			{ return Def && Def->DisplayName.Equals(CarName, ESearchCase::IgnoreCase); });
		if (Vehicles.IsValidIndex(CarIdx))
		{
			SetVehicleByIndex(CarIdx);
		}
		else
		{
			UE_LOG(LogTemp, Warning, TEXT("ODVehiclePawn: -odcar=%s not found among %d vehicle def(s)"),
				*CarName, Vehicles.Num());
		}
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
	const FVector End(Loc.X, Loc.Y, Loc.Z - 1000000.0f);     // 10 km: TeleportToSpawn lifts to +6 km and Summit ground sits ~-0.7 km (world Z = elev - datum), so 6.1 km fell short
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
	// PM_Gravel / PM_DirtRoad = unpaved road chunks (2026-09-30), still the road
	const bool bOnRoad = bHit && Hit.PhysMaterial.IsValid()
		&& (Hit.PhysMaterial->GetName().Contains(TEXT("Asphalt"))
			|| Hit.PhysMaterial->GetName().Contains(TEXT("Gravel"))
			|| Hit.PhysMaterial->GetName().Contains(TEXT("DirtRoad")));

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
				const FVector ProbeEnd(Loc.X + Offset.X, Loc.Y + Offset.Y, Loc.Z - 1000000.0f);
				FHitResult ProbeHit;
				if (GetWorld()->LineTraceSingleByChannel(ProbeHit, ProbeStart, ProbeEnd,
						ECC_Visibility, Params)
					&& ProbeHit.PhysMaterial.IsValid()
					&& (ProbeHit.PhysMaterial->GetName().Contains(TEXT("Asphalt"))
						|| ProbeHit.PhysMaterial->GetName().Contains(TEXT("Gravel"))
						|| ProbeHit.PhysMaterial->GetName().Contains(TEXT("DirtRoad"))))
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
	// Warm settle (2026-09-07, "respawn takes forever"): the full 3s settle
	// exists for the Chaos registration race on freshly-streamed tiles. When
	// the asphalt trace connected almost immediately after the teleport
	// (mid-drive OOB/stuck reset onto collision that's been resident and
	// simulated for a while), that race can't apply - release after the
	// short window instead. bOnRoad is required so a timeout fallback onto
	// arbitrary ground never takes the fast path, and bEverGroundSnapped so
	// the INITIAL spawn never does either: a cold boot's road tile can be
	// queryable 0.1s after the teleport (built during the loading screen,
	// observed live 2026-09-07) while its physics body is exactly the
	// maybe-not-simulation-ready case the 3s settle was added for.
	const bool bWarmSnap = bEverGroundSnapped && bOnRoad
		&& (SnapReadyTime - SpawnTime) < WarmSnapWindowSec;
	const double SettleSec = bWarmSnap ? WarmSnapSettleSec : GroundSnapSettleSec;
	if (Now - SnapReadyTime < SettleSec)
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
	bEverGroundSnapped = true;   // real-world snap completed - arms the warm
	                              // settle for later mid-drive resets (NOT set
	                              // by PlaceInShowroom's fake snap)
}

void AODVehiclePawn::SetupPlayerInputComponent(UInputComponent* Input)
{
	Super::SetupPlayerInputComponent(Input);
	CachedInputComponent = Input;

	// Arrow keys are a fixed, always-on ALIAS for Throttle/Brake/Steer -
	// deliberately NOT rebindable (see RefreshODManagedKeyBindings) so a
	// player who reassigns W/A/S/D can't lock themselves out of driving, and
	// so the pre-existing "WASD or arrows" convenience keeps working exactly
	// as before regardless of what's bound to the config-driven primary key.
	struct FKeyFn
	{
		FKey Key;
		void (AODVehiclePawn::*Press)();
		void (AODVehiclePawn::*Release)();
	};
	const FKeyFn ArrowAliases[] = {
		{EKeys::Up, &AODVehiclePawn::ThrottlePressed, &AODVehiclePawn::ThrottleReleased},
		{EKeys::Down, &AODVehiclePawn::BrakePressed, &AODVehiclePawn::BrakeReleased},
		{EKeys::Left, &AODVehiclePawn::LeftPressed, &AODVehiclePawn::LeftReleased},
		{EKeys::Right, &AODVehiclePawn::RightPressed, &AODVehiclePawn::RightReleased},
	};
	for (const FKeyFn& B : ArrowAliases)
	{
		Input->BindKey(B.Key, IE_Pressed, this, B.Press);
		Input->BindKey(B.Key, IE_Released, this, B.Release);
	}
	// V: cycle to the next car (garage groundwork - dev-facing live hot-swap,
	// not a player-facing rebindable action - see next steps.md SS7.32)
	Input->BindKey(EKeys::V, IE_Pressed, this, &AODVehiclePawn::CycleVehicle);

	RefreshODManagedKeyBindings(Input);
	SetupGamepadAndWheelInput(Input);
}

void AODVehiclePawn::RefreshODManagedKeyBindings(UInputComponent* Input)
{
	if (!Input)
	{
		Input = CachedInputComponent.Get();
	}
	if (!Input)
	{
		return;
	}

	// remove exactly the keys we bound last call (arrow aliases and V are
	// never in this list - see SetupPlayerInputComponent)
	if (LastODManagedKeys.Num() > 0)
	{
		Input->KeyBindings.RemoveAll([this](const FInputKeyBinding& B)
		{
			return LastODManagedKeys.Contains(B.Chord.Key);
		});
	}
	LastODManagedKeys.Reset();

	struct FActionFn
	{
		EODInputAction Action;
		void (AODVehiclePawn::*Press)();
		void (AODVehiclePawn::*Release)();
	};
	const FActionFn KeyboardBinds[] = {
		{EODInputAction::Throttle, &AODVehiclePawn::ThrottlePressed, &AODVehiclePawn::ThrottleReleased},
		{EODInputAction::Brake, &AODVehiclePawn::BrakePressed, &AODVehiclePawn::BrakeReleased},
		{EODInputAction::SteerLeft, &AODVehiclePawn::LeftPressed, &AODVehiclePawn::LeftReleased},
		{EODInputAction::SteerRight, &AODVehiclePawn::RightPressed, &AODVehiclePawn::RightReleased},
		{EODInputAction::Handbrake, &AODVehiclePawn::HandbrakePressed, &AODVehiclePawn::HandbrakeReleased},
		{EODInputAction::ResetVehicle, &AODVehiclePawn::ResetVehicle, nullptr},
	};
	for (const FActionFn& B : KeyboardBinds)
	{
		const FKey Key = FODInputConfig::GetKeyboardKey(B.Action);
		if (!Key.IsValid())
		{
			continue;   // explicitly unbound
		}
		Input->BindKey(Key, IE_Pressed, this, B.Press);
		if (B.Release)
		{
			Input->BindKey(Key, IE_Released, this, B.Release);
		}
		LastODManagedKeys.AddUnique(Key);
	}

	const FKey GamepadHandbrakeKey = FODInputConfig::GetGamepadKey(EODInputAction::Handbrake);
	if (GamepadHandbrakeKey.IsValid())
	{
		Input->BindKey(GamepadHandbrakeKey, IE_Pressed, this, &AODVehiclePawn::GamepadHandbrakePressed);
		Input->BindKey(GamepadHandbrakeKey, IE_Released, this, &AODVehiclePawn::GamepadHandbrakeReleased);
		LastODManagedKeys.AddUnique(GamepadHandbrakeKey);
	}
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
	// gamepad Handbrake bind lives in RefreshODManagedKeyBindings (Arc 4.1,
	// SS7.32) - it's rebindable, unlike everything else in this function.
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
	// Arc 4.1 (SS7.32): deadzone + response-curve exponent are now the
	// persisted FODInputConfig sliders instead of a hardcoded constant/x*|x|
	// literal - ConditionSteering with the default 0.15 deadzone / 2.0
	// exponent reproduces the exact pre-existing curve (Taylor: "an analog
	// stick ... a mild response curve, x*|x| feels right for steering").
	// Deliberately NOT applied to keyboard (SmSteer's own fade/rate logic
	// above is untouched) or to the wheel (1:1, no curve - see class notes) -
	// this is specifically the gamepad-stick conditioning point, and (per
	// the task brief) the exact point a future Arc 6 wheel steering axis
	// would plug into the same FODInputConfig::ConditionSteering call.
	GamepadSteerRaw = FODInputConfig::ConditionSteering(
		Value, FODInputConfig::GetSteeringDeadzone(), FODInputConfig::GetSteeringSensitivity());
}

void AODVehiclePawn::GamepadThrottleAxis(float Value)
{
	GamepadThrottleRaw = FODInputConfig::ConditionThrottleBrake(Value, FODInputConfig::GetThrottleBrakeDeadzone());
}

void AODVehiclePawn::GamepadBrakeAxis(float Value)
{
	GamepadBrakeRaw = FODInputConfig::ConditionThrottleBrake(Value, FODInputConfig::GetThrottleBrakeDeadzone());
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
		// vs the last value that counted, not last tick's: a slow squeeze
		// moves < 0.0005 per frame at high fps and never registered
		if (!FMath::IsNearlyEqual(RawWheelAxis[i], PrevWheelAxis[i], 0.0005f))
		{
			bWheelChanged = true;
			PrevWheelAxis[i] = RawWheelAxis[i];
		}
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

namespace
{
// Keyboard axis ramp, exact over Dt: away from zero at InRate, toward zero
// (release, or a smaller same-sign target) at OutRate, both 1/s. A reversal
// spends only the time it needs to reach zero at OutRate, then ramps out the
// other way at InRate for the rest of Dt - piecewise-exact, so the result is
// the same however Dt is sliced (pipeline/check_kbm_steer_ramp.py).
float RampKeyAxis(float R, float Target, float Dt, float InRate, float OutRate)
{
	const bool bSameSide = Target != 0.0f && FMath::Sign(Target) == FMath::Sign(R);
	if (R != 0.0f && !(bSameSide && FMath::Abs(Target) >= FMath::Abs(R)))
	{
		const float Stop = bSameSide ? Target : 0.0f;
		const float Need = FMath::Abs(R - Stop) / OutRate;
		if (Dt <= Need)
		{
			return R + FMath::Sign(Stop - R) * OutRate * Dt;
		}
		R = Stop;
		Dt -= Need;
	}
	return FMath::FInterpConstantTo(R, Target, Dt, InRate);
}

// Keyboard steer state, exact over Dt (key fixed for the frame): no key ->
// exponential decay toward 0 (Tau); opposite key -> linear unwind at
// CounterRate to 0, then out at InRate; same key -> out at InRate to +-1.
float KbSteerStep(float R, float Key, float Dt, float InRate, float CounterRate, float Tau)
{
	if (Key == 0.0f)
	{
		return R * FMath::Exp(-Dt / Tau);
	}
	if (R != 0.0f && FMath::Sign(R) != FMath::Sign(Key))
	{
		const float Need = FMath::Abs(R) / CounterRate;
		if (Dt <= Need)
		{
			return R - FMath::Sign(R) * CounterRate * Dt;
		}
		R = 0.0f;
		Dt -= Need;
	}
	return FMath::FInterpConstantTo(R, Key, Dt, InRate);
}
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

	// crash shake (OnBodyHit): smooth noise on the camera's own relative
	// rotation - the spring arm above it is the freelook's - ~0.25 s at full
	// strength; the last tick writes it back to zero
	if (CrashShake > 0.0f && !bInShowroom)
	{
		CrashShake = FMath::Max(CrashShake - DeltaSeconds * 4.0f, 0.0f);
		const float A = CrashShakeDeg * CrashShake;
		const float T = GetWorld()->GetTimeSeconds() * 30.0f;
		Camera->SetRelativeRotation(FRotator(FMath::PerlinNoise1D(T) * A,
			FMath::PerlinNoise1D(T + 17.0f) * A, FMath::PerlinNoise1D(T + 41.0f) * A * 0.5f));
	}

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
		CrashPreVel = Body->GetPhysicsLinearVelocity();   // not a "velocity jump" for -odcrashtrace
		UE_LOG(LogTemp, Display,
			TEXT("ODVehiclePawn SCRIPTDRIVE: velocity hard-set to %.0f cm/s"),
			SetVCmS);
	}

	// keyboard keys -> ramped axes (tuning + rationale: "Keyboard driving"
	// block in ODVehiclePawn.h). Steer: time-held ramp -> curve -> speed cap.
	const float SpeedCmS = FMath::Abs(Movement->GetForwardSpeed());
	KbSteerRamp = KbSteerStep(KbSteerRamp,
		(bRightKey ? 1.0f : 0.0f) - (bLeftKey ? 1.0f : 0.0f), DeltaSeconds,
		1.0f / FMath::Max(KbSteerRampInSec, 0.01f),
		1.0f / FMath::Max(KbSteerCounterSec, 0.01f),
		FMath::Max(KbSteerReleaseTau, 0.01f));
	const float VMs = FMath::Max(SpeedCmS * 0.01f, 0.1f);
	const float KbCapDeg = FMath::RadiansToDegrees(
			KbSteerWheelbaseCm * 0.01f * KbSteerTargetG * 9.81f / (VMs * VMs))
		+ KbSteerSlipFrac * Movement->PeakSlipAngleDeg;
	const float LockDeg = Movement->MaxSteerAngleDeg * Movement->SteerLockScale
		* UODVehicleMovement::SteerTaper(SpeedCmS);
	const float KbSteerCap = FMath::Clamp(KbCapDeg / FMath::Max(LockDeg, 1.0f), 0.0f, 1.0f);
	SmSteer = FMath::Sign(KbSteerRamp)
		* FMath::Pow(FMath::Abs(KbSteerRamp), FMath::Max(KbSteerCurveExp, 0.1f)) * KbSteerCap;
	const float PedalIn = 1.0f / FMath::Max(PedalRampInSec, 0.01f);
	const float PedalOut = 1.0f / FMath::Max(PedalReleaseSec, 0.01f);
	SmThrottle = RampKeyAxis(SmThrottle, bThrottleKey ? HookThrottle : 0.0f,
		DeltaSeconds, PedalIn, PedalOut);
	SmBrake = RampKeyAxis(SmBrake, bBrakeKey ? 1.0f : 0.0f, DeltaSeconds, PedalIn, PedalOut);

	// gamepad stick lock cap (unchanged; see SteerFadeStartCmS)
	const float FadeT = FMath::Clamp(
		(SpeedCmS - SteerFadeStartCmS) /
			FMath::Max(SteerFadeEndCmS - SteerFadeStartCmS, 1.0f),
		0.0f, 1.0f);
	const float FadeS = FadeT * FadeT * (3.0f - 2.0f * FadeT);
	const float SteerScale = FMath::Lerp(1.0f, HighSpeedSteerFrac, FadeS);
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
		// speed-sensitive stick lock (2026-09-25; keyboard has its own): a firm stick
		// flick at 70 mph was ~24 deg of road wheel, enough to spin the car
		// under braking with every assist on (planar sim, see SteerFadeStartCmS)
		Movement->SetSteeringInput(GamepadSteerRaw * SteerScale);
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
	// the front-end menu runs unpaused (AODMainMenu::OpenMenu) and shares the
	// arrow keys, so menu navigation lit the brake lamps and turned the
	// wheels - the showroom car takes no driving input from any device
	if (bInShowroom)
	{
		Movement->SetSteeringInput(0.0f);
		Movement->SetThrottleInput(0.0f);
		Movement->SetBrakeInput(0.0f);
		Movement->SetHandbrakeInput(false);
	}

	// wheel visuals: suspension travel + steer + spin from our sim, plus the
	// garage setup's tire diameter/width and camber (no new art - the one
	// wheel mesh scaled on its axle axis Y and tilted top-inward)
	for (int32 i = 0; i < WheelVisuals.Num(); ++i)
	{
		// shown spin: rolling with the road, so a stopped car's wheels sit
		// still and a moving one's turn true; the sim's own wheel speed only
		// for real wheelspin / lock-up (>1 m/s slip) or in the air. Physics
		// off (menu showroom) = still - the sim stops ticking there and its
		// frozen Omega spun the wheels nonstop (2026-10-09, Taylor: "why
		// dont the tires just normally sit still ... spinning like crazy").
		float SpinRadS = 0.0f;
		if (Body->IsSimulatingPhysics())
		{
			const float RadiusCm = Movement->GetWheelRadius(i);
			const float RollRadS = Movement->GetForwardSpeed() / RadiusCm;
			SpinRadS = Movement->GetWheelOmega(i);
			if (Movement->IsWheelInContact(i)
				&& FMath::Abs(SpinRadS - RollRadS) * RadiusCm < 100.0f)
			{
				SpinRadS = RollRadS;
			}
		}
		WheelSpinDeg[i] = FMath::Fmod(WheelSpinDeg[i]
			+ FMath::RadiansToDegrees(SpinRadS) * DeltaSeconds, 360.0f);
		const float S = Movement->GetWheelRadius(i) / ReferenceWheelMeshRadiusCm;
		const float Width = i < 2
			? Movement->FrontTireWidthScale : Movement->RearTireWidthScale;
		WheelVisuals[i]->SetRelativeScale3D(FVector(S, S * Width, S));
		WheelVisuals[i]->SetRelativeLocation(Movement->GetWheelLocalPos(i));
		// steer, then camber (+roll tips the top toward +Y = inward on the
		// left), then spin about the tilted axle - one FRotator would apply
		// roll first and wobble the camber as the wheel spins
		const float Camber = (i % 2 == 0 ? 1.0f : -1.0f) * Movement->CamberDeg;
		WheelVisuals[i]->SetRelativeRotation(
			FQuat(FRotator(0.0f, Movement->GetWheelSteerDeg(i), Camber))
			* FQuat(FRotator(-WheelSpinDeg[i], 0.0f, 0.0f)));
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

	// pre-step velocity for OnBodyHit - last, after the -odsetv hook above
	if (Body->IsSimulatingPhysics())
	{
		if (bCrashTrace)
		{
			TickCrashTrace(GetWorld()->GetTimeSeconds());
		}
		CrashPreVel = Body->GetPhysicsLinearVelocity();   // -odcrashtrace's velocity-jump probe only
	}

	TickOutOfBounds(DeltaSeconds);
}

void AODVehiclePawn::OnBodyHit(UPrimitiveComponent*, AActor* OtherActor,
	UPrimitiveComponent* OtherComp, FVector NormalImpulse, const FHitResult& Hit)
{
	if (!bGroundSnapped || bInShowroom || !Body->IsSimulatingPhysics())
	{
		return;
	}
	// contact normal pointing out of the wall toward the car. Chaos's sign
	// depends on the pair's internal order, so take it from geometry.
	FVector N = Hit.ImpactNormal;
	if (FVector::DotProduct(N, Body->GetCenterOfMass() - Hit.ImpactPoint) < 0.0f)
	{
		N = -N;
	}
	FVector V0, W0;
	Movement->PreHitVelocity(N, V0, W0);
	const float Vn = -FVector::DotProduct(V0, N);   // speed into the wall
	// Walls only. Buildings, retaining walls, rails, trunks AND the road and
	// terrain are all the same BlockAll WorldStatic, so the normal is what
	// tells them apart: steeper than ~53 deg is a wall. Ground contacts
	// (bottoming out, landing on the roof/side) stay with the solver.
	const bool bWall = FMath::Abs(N.Z) < 0.6f;
	const bool bCrash = bWall && Vn > CrashMinNormalSpeedCmS;
	const FVector VSolver = Body->GetPhysicsLinearVelocity();
	const FVector WSolver = Body->GetPhysicsAngularVelocityInDegrees();
	const double Now = GetWorld()->GetTimeSeconds();
	if (bCrash)
	{
		// Built from the PRE-step velocity, so whatever the solver did this
		// step can't leak out. Keep CrashRestitution of the into-wall speed
		// as bounce; scrub tangential speed Coulomb-style with the surface's
		// own friction (a glancing hit keeps most of it, and PM_GuardrailSlick
		// rails still deflect); cap the spin the hit adds.
		const FVector Vt = V0 + N * Vn;
		const float VtLen = Vt.Size();
		const float Mu = Hit.PhysMaterial.IsValid() ? Hit.PhysMaterial->Friction : 0.7f;
		const float Keep = VtLen > 1.0f
			? FMath::Max(VtLen - Mu * (1.0f + CrashRestitution) * Vn, 0.0f) / VtLen
			: 0.0f;
		const FVector VOut = Vt * Keep + N * (Vn * CrashRestitution);
		const FVector WOut = W0 + (WSolver - W0).GetClampedToMaxSize(CrashMaxSpinDegS);
		Body->SetPhysicsLinearVelocity(VOut);
		Body->SetPhysicsAngularVelocityInDegrees(WOut);
		// a second wall in the same step (a corner) builds on this result
		Movement->ResetStepHistory(VOut, WOut);
		CrashPreVel = VOut;

		// feedback (D1): the road synth's impact thump - no crash sample in
		// content_src/audio yet - plus a small camera shake, both by Vn
		const float Strength = FMath::Clamp(Vn / CrashFullSpeedCmS, 0.0f, 1.0f);
		if (UODRoadNoiseSynth* Road = FindComponentByClass<UODRoadNoiseSynth>())
		{
			Road->TriggerImpact(Strength);
		}
		CrashShake = FMath::Max(CrashShake, Strength);

		if (bCrashTrace)
		{
			OpenCrashTraceWindow(Now, V0.Size(), TEXT("wall hit"));
		}
	}
	// belly contacts (2026-10-09, stuck high-centered off road with the rear
	// wheels hanging and spinning): name what the underside rests on, 1/s
	if (bCrashTrace && !bWall && Now - LastBellyLog > 1.0)
	{
		LastBellyLog = Now;
		UE_LOG(LogTemp, Display, TEXT("ODBelly: t=%.2f other=%s/%s n=(%.2f,%.2f,%.2f) local=%s spd=%.0f"),
			Now, OtherActor ? *OtherActor->GetName() : TEXT("none"),
			OtherComp ? *OtherComp->GetName() : TEXT("none"), N.X, N.Y, N.Z,
			*GetActorTransform().InverseTransformPosition(Hit.ImpactPoint).ToCompactString(),
			VSolver.Size());
	}
	if (bCrashTrace && Now < CrashTraceUntil)
	{
		UE_LOG(LogTemp, Display,
			TEXT("ODCrashTrace: HIT t=%.3f crash=%d other=%s/%s n=(%.2f,%.2f,%.2f) "
			     "pen=%.1f impulse=%.0f mu=%.2f vn=%.0f pre=%.0f solver=%.0f "
			     "out=%.0f w_pre=%.0f w_solver=%.0f w_out=%.0f"),
			Now, bCrash ? 1 : 0, OtherActor ? *OtherActor->GetName() : TEXT("none"),
			OtherComp ? *OtherComp->GetName() : TEXT("none"), N.X, N.Y, N.Z,
			Hit.PenetrationDepth, NormalImpulse.Size(),
			Hit.PhysMaterial.IsValid() ? Hit.PhysMaterial->Friction : -1.0f,
			Vn, V0.Size(), VSolver.Size(), Body->GetPhysicsLinearVelocity().Size(),
			W0.Size(), WSolver.Size(),
			Body->GetPhysicsAngularVelocityInDegrees().Size());
	}
}

void AODVehiclePawn::OpenCrashTraceWindow(double Now, float ImpactCmS, const TCHAR* Why)
{
	// a window that ran out but hasn't printed its SUMMARY yet (that happens
	// next Tick) just extends, merging the two impacts
	if (CrashTraceUntil <= 0.0)
	{
		UE_LOG(LogTemp, Display, TEXT("ODCrashTrace: OPEN (%s) at %.0f cm/s - pre-impact frames:"),
			Why, ImpactCmS);
		for (int32 i = 0; i < CrashTraceRing.Num(); ++i)
		{
			UE_LOG(LogTemp, Display, TEXT("ODCrashTrace: pre %s"),
				*CrashTraceRing[(CrashTraceRingNext + i) % CrashTraceRing.Num()]);
		}
		CrashTraceRing.Reset();
		CrashTraceRingNext = 0;
		CrashTraceImpactCmS = 0.0f;
		CrashTracePeakCmS = 0.0f;
	}
	CrashTraceImpactCmS = FMath::Max(CrashTraceImpactCmS, ImpactCmS);
	CrashTraceUntil = Now + 2.0;
}

void AODVehiclePawn::TickCrashTrace(double Now)
{
	const FVector V = Body->GetPhysicsLinearVelocity();
	const FVector W = Body->GetPhysicsAngularVelocityInDegrees();
	int32 Contacts = 0;
	for (int32 i = 0; i < Movement->NumWheels(); ++i)
	{
		Contacts += Movement->IsWheelInContact(i) ? 1 : 0;
	}
	const FVector Loc = GetActorLocation();
	const FString Line = FString::Printf(
		TEXT("t=%.3f spd=%.0f v=(%.0f,%.0f,%.0f) w=(%.0f,%.0f,%.0f) wheels=%d pos=(%.0f,%.0f,%.0f)"),
		Now, V.Size(), V.X, V.Y, V.Z, W.X, W.Y, W.Z, Contacts, Loc.X, Loc.Y, Loc.Z);

	// a one-frame jump no wall hit explains (a launch off a roof or a gentle
	// face, a depenetration pop) opens a window too - CrashPreVel is still
	// last frame's here
	if (Now >= CrashTraceUntil && (V - CrashPreVel).Size() > 1500.0f)
	{
		OpenCrashTraceWindow(Now, CrashPreVel.Size(), TEXT("velocity jump"));
	}
	if (Now < CrashTraceUntil)
	{
		UE_LOG(LogTemp, Display, TEXT("ODCrashTrace: %s"), *Line);
		CrashTracePeakCmS = FMath::Max(CrashTracePeakCmS, V.Size());
		return;
	}
	if (CrashTraceUntil > 0.0)
	{
		// Phase 0 check: the car may stop or glance off, never leave faster
		// than it arrived
		UE_LOG(LogTemp, Display,
			TEXT("ODCrashTrace: SUMMARY impact=%.0f cm/s (%.0f mph) peak after=%.0f cm/s "
			     "(%.0f mph) -> %s"),
			CrashTraceImpactCmS, CrashTraceImpactCmS * 0.0223694f, CrashTracePeakCmS,
			CrashTracePeakCmS * 0.0223694f,
			CrashTracePeakCmS <= CrashTraceImpactCmS ? TEXT("PASS") : TEXT("FAIL"));
		CrashTraceUntil = 0.0;
	}
	constexpr int32 RingSize = 10;
	if (CrashTraceRing.Num() < RingSize)
	{
		CrashTraceRing.Add(Line);
	}
	else
	{
		CrashTraceRing[CrashTraceRingNext] = Line;
		CrashTraceRingNext = (CrashTraceRingNext + 1) % RingSize;
	}
}

void AODVehiclePawn::TickOutOfBounds(float DeltaSeconds)
{
	// AUDIT.md 5.4/rec. 5 (2026-09-06): the invisible barrier wall is gone
	// (AODBarrierManager keeps only the visual tree/bush boundary); this is
	// its replacement. Two independent triggers, both requiring SUSTAINED
	// violation so slides, crests, and brief shoulder excursions never fire:
	// distance-from-centerline (wandering off into the world) and
	// all-wheels-airborne (falling through/off no-collision far terrain).
	OobCountdownSec = -1.0f;
	if (bInShowroom || !bGroundSnapped || !Body || !Body->IsSimulatingPhysics())
	{
		OobOffRoadSec = OobAirSec = OobStuckSec = 0.0f;
		return;
	}
	const FVector Loc = GetActorLocation();
	const double Now = GetWorld()->GetTimeSeconds();
	if (Now - LastOobScanTime > 0.5)   // O(centerline) scan, 2 Hz is plenty
	{
		LastOobScanTime = Now;
		// Large world (2026-10-05): min distance over EVERY road manager (one
		// per streamed area set, see AODGameMode::SpawnAreaSet), not the first
		// one found - otherwise driving into a second area reads as "far from
		// the (home) road" and resets the car. The nearest manager is also the
		// one the reset below snaps to.
		double BestCm = TNumericLimits<double>::Max();
		for (TActorIterator<AODRoadManager> It(GetWorld()); It; ++It)
		{
			FVector2D Nearest;
			float NearestYaw = 0.0f;
			if (It->NearestRoadPoint(FVector2D(Loc.X, Loc.Y), Nearest, NearestYaw))
			{
				const double DistCm = FVector2D::Distance(FVector2D(Loc.X, Loc.Y), Nearest);
				if (DistCm < BestCm)
				{
					BestCm = DistCm;
					OobRoadMgr = *It;
				}
			}
		}
		if (BestCm < TNumericLimits<double>::Max())
		{
			OobDistNowCm = (float)BestCm;
		}
	}
	// No road manager = no centerline to measure against or reset to (Tail
	// of the Dragon keeps its own race-gate respawn) - feature fully inert.
	const AODRoadManager* Road = OobRoadMgr.Get();
	if (!Road)
	{
		return;
	}

	const bool bAnyContact = Movement->IsWheelInContact(0)
		|| Movement->IsWheelInContact(1) || Movement->IsWheelInContact(2)
		|| Movement->IsWheelInContact(3);
	OobAirSec = bAnyContact ? 0.0f : OobAirSec + DeltaSeconds;
	OobOffRoadSec = OobDistNowCm > OobDistCm ? OobOffRoadSec + DeltaSeconds : 0.0f;

	// Stuck trigger (2026-09-07, see StuckThrottleFrac's UPROPERTY doc):
	// full throttle into a guardrail/tree/ditch within OobDistCm, where the
	// distance trigger above can never fire. Ground contact required (the
	// airborne trigger owns the no-contact case) and brake must be released
	// - holding throttle+brake together is a deliberate burnout/launch, not
	// a player trying and failing to move.
	const bool bStuckNow = bAnyContact
		&& Movement->GetThrottleInput() > StuckThrottleFrac
		&& Movement->GetBrakeInput() < 0.1f
		&& Body->GetPhysicsLinearVelocity().Size() < StuckSpeedCmS;
	OobStuckSec = bStuckNow ? OobStuckSec + DeltaSeconds : 0.0f;

	bOobStuckReset = OobStuckSec > 0.0f && OobOffRoadSec <= 0.0f;
	if (OobOffRoadSec > 0.0f)
	{
		OobCountdownSec = OobGraceSec - OobOffRoadSec;   // HUD warning
	}
	else if (OobStuckSec > 0.5f)
	{
		// 0.5s lead-in before the HUD warning: a launch from standstill
		// spends a frame or two below StuckSpeedCmS and would otherwise
		// flash the countdown on every pull-away.
		OobCountdownSec = StuckGraceSec - OobStuckSec;   // HUD "STUCK" warning
	}
	if (OobOffRoadSec > OobGraceSec || OobAirSec > OobAirborneSec
		|| OobStuckSec > StuckGraceSec)
	{
		UE_LOG(LogTemp, Display,
			TEXT("ODVehiclePawn OOB RESET: pos=%.0f/%.0f/%.0f dist=%.0fcm "
			     "offroad=%.1fs air=%.1fs stuck=%.1fs -> nearest road point"),
			Loc.X, Loc.Y, Loc.Z, OobDistNowCm, OobOffRoadSec, OobAirSec,
			OobStuckSec);
		OobOffRoadSec = OobAirSec = OobStuckSec = 0.0f;
		OobDistNowCm = 0.0f;
		OobCountdownSec = -1.0f;
		FVector2D Snap;
		float SnapYaw = 0.0f;
		if (Road->NearestRoadPoint(FVector2D(Loc.X, Loc.Y), Snap, SnapYaw))
		{
			// TeleportToSpawn re-arms the proven PM_Asphalt ground snap, so
			// the car re-enters frozen and settles exactly like a map spawn.
			TeleportToSpawn(FVector(Snap.X, Snap.Y, 0.0f), SnapYaw);
		}
	}
}
