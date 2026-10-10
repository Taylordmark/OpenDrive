#include "ODContactShadow.h"

#include "Components/DecalComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "Materials/MaterialInterface.h"
#include "ODVehicleMovement.h"
#include "ODVehiclePawn.h"
#include "ODVisualStyle.h"

namespace
{
// body blob: rounded rectangle on the chassis footprint
constexpr float BodyOpacity = 0.6f;
constexpr float BodyInner = 0.55f;          // flat core, as a fraction of the half-size
constexpr float BodyGrow = 1.12f;           // blob half-size / chassis bounds half-size
constexpr float BodyFadeStartCm = 15.0f;    // gap above the rest ride height
constexpr float BodyFadeEndCm = 150.0f;
constexpr float BodyHalfDepthCm = 60.0f;
// tyre blobs: soft ellipse at each contact patch
constexpr float WheelOpacity = 0.75f;
constexpr float WheelAlongFrac = 0.8f;      // half-length / tyre radius
constexpr float WheelAcrossGrow = 1.5f;     // half-width / tyre half-width
constexpr float WheelFadeStartCm = 2.0f;    // gap under the tyre
constexpr float WheelFadeEndCm = 60.0f;
constexpr float WheelHalfDepthCm = 30.0f;
// tilt fade (UpZ = body up . world up)
constexpr float TiltFullUpZ = 0.8f;
constexpr float TiltZeroUpZ = 0.5f;
constexpr float ColorEpsilon = 0.02f;       // skip SetDecalColor (a render-state rebuild) below this change
constexpr int32 BlobSortOrder = 10;         // over the road wear decals (ODRoadDecals, 0)
const TCHAR* BlobMaterialPath =
	TEXT("/Game/OpenDrive/Realistic/CarShadow/M_CarContactShadow.M_CarContactShadow");
}

UODContactShadow::UODContactShadow()
{
	PrimaryComponentTick.bCanEverTick = true;
	PrimaryComponentTick.TickGroup = TG_PostPhysics;   // the transform this frame renders
}

float UODContactShadow::Strength(float GapCm, float FadeStartCm, float FadeEndCm, float UpZ)
{
	const float H = 1.0f - FMath::SmoothStep(FadeStartCm, FadeEndCm, GapCm);
	const float T = FMath::Clamp((UpZ - TiltZeroUpZ) / (TiltFullUpZ - TiltZeroUpZ), 0.0f, 1.0f);
	return H * T;
}

FVector UODContactShadow::AlongOnPlane(const FVector& Fwd, const FVector& N)
{
	const FVector A = Fwd - N * FVector::DotProduct(Fwd, N);
	if (A.SizeSquared() > 1e-6f)
	{
		return A.GetUnsafeNormal();
	}
	FVector Y, Z;
	N.FindBestAxisVectors(Y, Z);
	return Y;
}

void UODContactShadow::BeginPlay()
{
	Super::BeginPlay();
	AODVehiclePawn* Pawn = Cast<AODVehiclePawn>(GetOwner());
	if (!ODVisualStyle::IsRealistic() || !Pawn || !Pawn->Body)
	{
		SetComponentTickEnabled(false);
		return;
	}

	// open low-poly shells: back faces must cast too, or light leaks into
	// the cabin and through the wheel wells
	Pawn->Body->bCastShadowAsTwoSided = true;
	Pawn->Body->MarkRenderStateDirty();
	for (UStaticMeshComponent* W : Pawn->WheelVisuals)
	{
		W->bCastShadowAsTwoSided = true;
		W->MarkRenderStateDirty();
	}

	UMaterialInterface* Mat = LoadObject<UMaterialInterface>(nullptr, BlobMaterialPath);
	if (!Mat)
	{
		UE_LOG(LogTemp, Warning, TEXT("ODContactShadow: M_CarContactShadow missing "
			"(unreal/build_car_contact_shadow_material.py) - no contact shadow"));
		SetComponentTickEnabled(false);
		return;
	}
	for (int32 i = 0; i < 1 + Pawn->WheelVisuals.Num(); ++i)
	{
		UDecalComponent* D = NewObject<UDecalComponent>(Pawn);
		D->SetDecalMaterial(Mat);
		D->SetSortOrder(BlobSortOrder);
		D->SetFadeScreenSize(0.0f);   // the player car is always near the camera
		D->SetUsingAbsoluteLocation(true);
		D->SetUsingAbsoluteRotation(true);
		D->SetUsingAbsoluteScale(true);
		D->SetupAttachment(Pawn->Body);
		D->SetVisibility(false);
		D->RegisterComponent();
		D->DecalColor = FLinearColor(i == 0 ? BodyInner : 0.0f, 0.0f, 0.0f, 0.0f);
		Blobs.Add(D);
	}
	UE_LOG(LogTemp, Display, TEXT("ODContactShadow: on, %d blobs"), Blobs.Num());
}

float UODContactShadow::Place(UDecalComponent* D, const FVector& Anchor, const FVector& Up,
	const FVector& Fwd, float MaxCm, float HalfAlong, float HalfAcross, float HalfDepth)
{
	FHitResult Hit;
	FCollisionQueryParams Query(SCENE_QUERY_STAT(ODContactShadow), false, GetOwner());
	if (!GetWorld()->LineTraceSingleByChannel(Hit, Anchor, Anchor - Up * MaxCm, ECC_Visibility, Query))
	{
		D->SetVisibility(false);
		return -1.0f;
	}
	// decal X = projection direction (into the ground), Z = along the car
	// (same convention as ODRoadDecals: DecalSize = depth, across, along)
	const FVector N = Hit.ImpactNormal;
	D->DecalSize = FVector(HalfDepth, HalfAcross, HalfAlong);
	D->SetWorldLocationAndRotation(Hit.ImpactPoint,
		FRotationMatrix::MakeFromXZ(-N, AlongOnPlane(Fwd, N)).Rotator());
	return Hit.Distance;
}

void UODContactShadow::SetStrength(UDecalComponent* D, float Inner, float S)
{
	const bool bOn = S > 0.005f;
	if (D->GetVisibleFlag() != bOn)
	{
		D->SetVisibility(bOn);
	}
	if (bOn && FMath::Abs(D->DecalColor.A - S) > ColorEpsilon)
	{
		D->SetDecalColor(FLinearColor(Inner, 0.0f, 0.0f, S));
	}
}

void UODContactShadow::TickComponent(float DeltaTime, ELevelTick TickType,
	FActorComponentTickFunction* ThisTickFunction)
{
	Super::TickComponent(DeltaTime, TickType, ThisTickFunction);
	AODVehiclePawn* Pawn = Cast<AODVehiclePawn>(GetOwner());
	UODVehicleMovement* Move = Pawn ? Pawn->Movement.Get() : nullptr;
	UStaticMesh* Chassis = Pawn ? Pawn->Body->GetStaticMesh() : nullptr;
	if (!Move || !Chassis || Blobs.IsEmpty())
	{
		return;
	}

	const FTransform Xf = Pawn->Body->GetComponentTransform();
	const FVector Up = Xf.GetUnitAxis(EAxis::Z);
	const FVector Fwd = Xf.GetUnitAxis(EAxis::X);

	// body: from the pivot height at the footprint centre; gap is measured
	// from the rest ride height, so suspension compression reads as 0
	const FBoxSphereBounds B = Chassis->GetBounds();
	const float Rest = Move->GetRestGroundClearanceCm();
	const float BodyDist = Place(Blobs[0], Xf.TransformPosition(FVector(B.Origin.X, B.Origin.Y, 0.0f)),
		Up, Fwd, Rest + BodyFadeEndCm, B.BoxExtent.X * BodyGrow, B.BoxExtent.Y * BodyGrow, BodyHalfDepthCm);
	SetStrength(Blobs[0], BodyInner,
		BodyDist < 0.0f ? 0.0f : BodyOpacity * Strength(BodyDist - Rest, BodyFadeStartCm, BodyFadeEndCm, Up.Z));

	// tyres: from the axle (suspension travel included); gap = below the tread
	for (int32 i = 0; i < Pawn->WheelVisuals.Num() && i + 1 < Blobs.Num() && i < Move->NumWheels(); ++i)
	{
		UStaticMeshComponent* WV = Pawn->WheelVisuals[i];
		const UStaticMesh* WMesh = WV->GetStaticMesh();
		const float R = Move->GetWheelRadius(i);
		const float HalfW = (WMesh ? WMesh->GetBounds().BoxExtent.Y : R * 0.35f) * WV->GetRelativeScale3D().Y;
		const FVector WFwd = Xf.TransformVectorNoScale(
			FRotator(0.0f, Move->GetWheelSteerDeg(i), 0.0f).Vector());
		const float Dist = Place(Blobs[i + 1], Xf.TransformPosition(Move->GetWheelLocalPos(i)),
			Up, WFwd, R + WheelFadeEndCm, R * WheelAlongFrac, HalfW * WheelAcrossGrow, WheelHalfDepthCm);
		SetStrength(Blobs[i + 1], 0.0f,
			Dist < 0.0f ? 0.0f : WheelOpacity * Strength(Dist - R, WheelFadeStartCm, WheelFadeEndCm, Up.Z));
	}
}
