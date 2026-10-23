#include "ODMarkers.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "GameFramework/Actor.h"
#include "Materials/MaterialInstanceDynamic.h"

UInstancedStaticMeshComponent* ODMarkers::AddGantries(AActor* Owner, TArrayView<const FTransform> Gates,
	const FLinearColor& Col, float HalfWidthCm, float CullCm)
{
	// engine basic shapes only, like AODVehiclePawn::BuildCockpitWheel
	// ponytail: lit BasicShapeMaterial, not emissive - dim at night; a scripted
	// marker material with a Color param (unreal/*.py) if they need to glow
	UStaticMesh* Cube = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube"));
	UMaterialInterface* Base = LoadObject<UMaterialInterface>(nullptr,
		TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"));
	if (!Owner || !Cube || !Base || Gates.IsEmpty())
	{
		return nullptr;
	}
	UMaterialInstanceDynamic* Mat = UMaterialInstanceDynamic::Create(Base, Owner);
	Mat->SetVectorParameterValue(TEXT("Color"), Col);
	auto* Ism = NewObject<UInstancedStaticMeshComponent>(Owner);
	Ism->SetStaticMesh(Cube);
	Ism->SetMaterial(0, Mat);
	Ism->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	Ism->SetCastShadow(false);
	Ism->SetCullDistances(int32(CullCm * 0.9f), int32(CullCm));
	if (USceneComponent* Root = Owner->GetRootComponent())
	{
		Ism->SetupAttachment(Root);
	}
	else
	{
		Owner->SetRootComponent(Ism);
	}
	Ism->RegisterComponent();

	// the engine cube is 100 cm, centred
	constexpr float Post = 30.0f, Height = 650.0f, Bar = 60.0f, Sink = 50.0f;
	TArray<FTransform> Xfs;
	for (const FTransform& G : Gates)
	{
		const FRotator Yaw(0.0f, G.Rotator().Yaw, 0.0f);
		const FVector Side = Yaw.RotateVector(FVector::RightVector) * HalfWidthCm;
		const FVector At = G.GetLocation();
		const FVector PostMid(0, 0, (Height - Sink) * 0.5f);
		const FVector PostScale = FVector(Post, Post, Height + Sink) / 100.0f;
		Xfs.Add(FTransform(Yaw, At - Side + PostMid, PostScale));
		Xfs.Add(FTransform(Yaw, At + Side + PostMid, PostScale));
		Xfs.Add(FTransform(Yaw, At + FVector(0, 0, Height - Bar * 0.5f),
			FVector(Post, 2.0f * HalfWidthCm + Post, Bar) / 100.0f));
	}
	Ism->AddInstances(Xfs, false, /*bWorldSpace*/true);
	return Ism;
}
