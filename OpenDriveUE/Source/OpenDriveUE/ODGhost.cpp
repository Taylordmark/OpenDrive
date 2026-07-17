#include "ODGhost.h"

#include "Components/SkeletalMeshComponent.h"
#include "Engine/SkeletalMesh.h"
#include "Materials/MaterialInterface.h"

AODGhost::AODGhost()
{
	PrimaryActorTick.bCanEverTick = true;

	SetRootComponent(CreateDefaultSubobject<USceneComponent>(TEXT("Root")));
	Mesh = CreateDefaultSubobject<USkeletalMeshComponent>(TEXT("Mesh"));
	Mesh->SetupAttachment(RootComponent);
	Mesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	Mesh->SetCastShadow(false);
}

bool AODGhost::NeedsMesh() const
{
	return Mesh->GetSkeletalMeshAsset() == nullptr;
}

void AODGhost::Init(USkeletalMesh* CarMesh)
{
	if (!CarMesh)
	{
		return;
	}
	Mesh->SetSkeletalMesh(CarMesh);
	if (UMaterialInterface* Ghost = LoadObject<UMaterialInterface>(
			nullptr, TEXT("/Game/OpenDrive/Materials/M_Ghost.M_Ghost")))
	{
		for (int32 i = 0; i < Mesh->GetNumMaterials(); ++i)
		{
			Mesh->SetMaterial(i, Ghost);
		}
	}
	SetActorHiddenInGame(true);
}

void AODGhost::Play(float WorldStartTime)
{
	if (Positions.Num() < 2)
	{
		return;
	}
	StartTime = WorldStartTime;
	bPlaying = true;
	SetActorHiddenInGame(false);
}

void AODGhost::Stop()
{
	bPlaying = false;
	SetActorHiddenInGame(true);
}

void AODGhost::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	if (!bPlaying)
	{
		return;
	}
	// max(0): GetTimeSeconds() is double but StartTime is float - the capture
	// can round UP past "now", and a same-frame tick then sees T = -epsilon
	// (floor -> index -1; crashed 2026-07-16)
	const float T = FMath::Max(0.0f,
		(GetWorld()->GetTimeSeconds() - StartTime) / SampleInterval);
	if (T >= Positions.Num() - 1)
	{
		Stop();
		return;
	}
	const int32 I = FMath::Clamp(FMath::FloorToInt(T), 0, Positions.Num() - 2);
	const float A = T - I;
	SetActorLocationAndRotation(
		FMath::Lerp(Positions[I], Positions[I + 1], A),
		FMath::Lerp(Rotations[I], Rotations[I + 1], A));
}
