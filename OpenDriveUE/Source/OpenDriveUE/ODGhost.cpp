#include "ODGhost.h"

#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "HAL/FileManager.h"
#include "Materials/MaterialInterface.h"
#include "Misc/Crc.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/MemoryReader.h"
#include "Serialization/MemoryWriter.h"

AODGhost::AODGhost()
{
	PrimaryActorTick.bCanEverTick = true;

	SetRootComponent(CreateDefaultSubobject<USceneComponent>(TEXT("Root")));
	Mesh = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Mesh"));
	Mesh->SetupAttachment(RootComponent);
	Mesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	Mesh->SetCastShadow(false);
}

bool AODGhost::NeedsMesh() const
{
	return Mesh->GetStaticMesh() == nullptr;
}

void AODGhost::Init(UStaticMesh* CarMesh)
{
	if (!CarMesh)
	{
		return;
	}
	Mesh->SetStaticMesh(CarMesh);
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
		FQuat::Slerp(Rotations[I], Rotations[I + 1], A));
}

FString FODGhostFile::Dir()
{
	return FPaths::ProjectSavedDir() / TEXT("Ghosts");
}

uint32 FODGhostFile::HashTrack(const TArray<FTransform>& Gates)
{
	// rounded to 10 cm / 0.1 deg: the gates are float32 on disk, so exact
	// bits agree across machines, but rounding keeps a re-export stable too
	uint32 H = 0;
	for (const FTransform& G : Gates)
	{
		const FVector P = G.GetLocation();
		const int32 V[4] = {FMath::RoundToInt(P.X / 10.0), FMath::RoundToInt(P.Y / 10.0),
			FMath::RoundToInt(P.Z / 10.0), FMath::RoundToInt(G.Rotator().Yaw * 10.0)};
		H = FCrc::MemCrc32(V, sizeof(V), H);
	}
	return H;
}

bool FODGhostFile::Save(const FString& InPath) const
{
	TArray<uint8> Bin;
	FMemoryWriter Ar(Bin);
	uint32 V = Version, H = TrackHash;
	FString A = Area, C = CarId;
	float T = Time, Dt = SampleInterval;
	int32 N = FMath::Min(Positions.Num(), Rotations.Num());
	Ar.Serialize((void*)"ODGH", 4);
	Ar << V << A << H << C << T << Dt << N;
	for (int32 i = 0; i < N; ++i)
	{
		const FQuat Q = Rotations[i].GetNormalized();
		float F[7] = {(float)Positions[i].X, (float)Positions[i].Y, (float)Positions[i].Z,
			(float)Q.X, (float)Q.Y, (float)Q.Z, (float)Q.W};
		Ar.Serialize(F, sizeof(F));
	}
	return FFileHelper::SaveArrayToFile(Bin, *InPath);
}

bool FODGhostFile::Load(const FString& InPath)
{
	TArray<uint8> Bin;
	if (!FFileHelper::LoadFileToArray(Bin, *InPath) || Bin.Num() < 8
		|| FMemory::Memcmp(Bin.GetData(), "ODGH", 4) != 0)
	{
		return false;
	}
	FMemoryReader Ar(Bin);
	Ar.ArMaxSerializeSize = 1024;   // a corrupt string length errors instead of allocating GBs
	Ar.Seek(4);
	uint32 V = 0;
	int32 N = 0;
	Ar << V;
	if (V != Version)
	{
		return false;
	}
	Ar << Area << TrackHash << CarId << Time << SampleInterval << N;
	// shared files are untrusted: bound N by what's actually left
	if (Ar.IsError() || N < 2 || (int64)N * 28 != Bin.Num() - Ar.Tell() || SampleInterval <= 0.0f)
	{
		return false;
	}
	Positions.SetNumUninitialized(N);
	Rotations.SetNumUninitialized(N);
	for (int32 i = 0; i < N; ++i)
	{
		float F[7];
		Ar.Serialize(F, sizeof(F));
		Positions[i] = FVector(F[0], F[1], F[2]);
		Rotations[i] = FQuat(F[3], F[4], F[5], F[6]).GetNormalized();
	}
	Path = InPath;
	return !Ar.IsError();
}

bool FODGhostFile::SelfCheck()
{
	FODGhostFile Out;
	Out.Area = TEXT("selfcheck");
	Out.CarId = TEXT("DA_Coupe");
	Out.TrackHash = HashTrack({FTransform(FRotator(0, 37.5f, 0), FVector(1e5, -2e5, 300)),
		FTransform(FRotator(0, -90, 0), FVector(3e5, 4e5, 1200))});
	Out.Time = 83.25f;
	for (int32 i = 0; i < 50; ++i)
	{
		Out.Positions.Add(FVector(i * 1234.5, -i * 77.0, 100.0 + i));
		Out.Rotations.Add(FQuat(FRotator(i * 3.0f, i * 7.0f - 170.0f, i * -1.5f)));
	}
	const FString P = FPaths::ProjectSavedDir() / TEXT("selfcheck.odghost");
	FODGhostFile In;
	bool bOk = Out.Save(P) && In.Load(P);
	bOk = bOk && In.Area == Out.Area && In.CarId == Out.CarId && In.TrackHash == Out.TrackHash
		&& In.Time == Out.Time && In.SampleInterval == Out.SampleInterval
		&& In.Positions.Num() == Out.Positions.Num();
	for (int32 i = 0; bOk && i < Out.Positions.Num(); ++i)
	{
		bOk = In.Positions[i].Equals(Out.Positions[i], 0.01) && In.Rotations[i].Equals(Out.Rotations[i], 1e-5);
	}
	// truncated file must be rejected, not read past the end
	TArray<uint8> Bin;
	FFileHelper::LoadFileToArray(Bin, *P);
	Bin.SetNum(Bin.Num() - 5);
	FFileHelper::SaveArrayToFile(Bin, *P);
	bOk = bOk && !In.Load(P);
	IFileManager::Get().Delete(*P);
	UE_LOG(LogTemp, Display, TEXT("ODSelfCheck: ghost file round-trip %s"), bOk ? TEXT("PASS") : TEXT("FAIL"));
	return bOk;
}
