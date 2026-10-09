#include "ODRaceManager.h"

#include "Components/InputComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "DrawDebugHelpers.h"
#include "GameFramework/Pawn.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "Components/StaticMeshComponent.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/ScopeExit.h"
#include "ODGhost.h"

AODRaceManager::AODRaceManager()
{
	PrimaryActorTick.bCanEverTick = true;
}

FString AODRaceManager::SaveSlot() const
{
	return FString::Printf(TEXT("OpenDrive_%s"), *GetWorld()->GetMapName());
}

void AODRaceManager::LoadCenterline()
{
	// same bin-sidecar pattern as ODForestManager's terrain mask: works in
	// editor, standalone, AND packaged builds (no editor-only Texture Source)
	TArray<uint8> Bin;
	const FString Path =
		FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks/centerline.bin");
	if (!FFileHelper::LoadFileToArray(Bin, *Path) || Bin.Num() < 8
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'C' || Bin[3] != '1')
	{
		return;   // HUD falls back to the gate-spline approximation
	}
	const int32 Count = (int32)Bin[4] | (int32)Bin[5] << 8
		| (int32)Bin[6] << 16 | (int32)Bin[7] << 24;
	if (Count <= 0 || Bin.Num() < 8 + (int64)Count * 8)
	{
		return;
	}
	// float32 pairs on disk, but FVector2D's components are double (UE5 LWC)
	// - convert per-element rather than memcpy, which would corrupt the data
	RoadCenterline.SetNumUninitialized(Count);
	const uint8* P = Bin.GetData() + 8;
	for (int32 i = 0; i < Count; ++i)
	{
		float X, Y;
		FMemory::Memcpy(&X, P + i * 8, 4);
		FMemory::Memcpy(&Y, P + i * 8 + 4, 4);
		RoadCenterline[i] = FVector2D(X, Y);
	}
	UE_LOG(LogTemp, Display, TEXT("ODRaceManager: road centerline (%d stations)"),
		Count);
}

void AODRaceManager::BeginPlay()
{
	Super::BeginPlay();
	LoadCenterline();

	if (const UODRaceSave* Save = Cast<UODRaceSave>(
			UGameplayStatics::LoadGameFromSlot(SaveSlot(), 0)))
	{
		BestTime = Save->BestTime;
		BestGateTimes = Save->BestGateTimes;
		if (Save->GhostPositions.Num() >= 2)
		{
			AODGhost* G = GetWorld()->SpawnActor<AODGhost>();
			G->SampleInterval = Save->SampleInterval;
			G->Positions = Save->GhostPositions;
			G->Rotations = Save->GhostRotations;
			if (const APawn* Car = UGameplayStatics::GetPlayerPawn(this, 0))
			{
				if (const auto* Mesh =
						Cast<UStaticMeshComponent>(Car->GetRootComponent()))
				{
					G->Init(Mesh->GetStaticMesh());
				}
			}
			Ghost = G;
		}
	}

	if (APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0))
	{
		EnableInput(PC);
		if (InputComponent)
		{
			InputComponent->BindKey(EKeys::R, IE_Pressed, this,
				&AODRaceManager::RespawnAtLastGate);
		}
	}
}

float AODRaceManager::ElapsedTime() const
{
	return bRunning ? GetWorld()->GetTimeSeconds() - RaceStart : LastTime;
}

void AODRaceManager::RespawnAtLastGate()
{
	APawn* Pawn = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Pawn || Checkpoints.Num() == 0)
	{
		return;
	}
	const int32 Gate = FMath::Clamp(NextGate - 1, 0, Checkpoints.Num() - 1);
	const FTransform& Xf = Checkpoints[Gate];
	Pawn->TeleportTo(Xf.GetLocation() + FVector(0, 0, 150.0f),
		FRotator(0.0f, Xf.Rotator().Yaw, 0.0f));
	if (auto* Root = Cast<UPrimitiveComponent>(Pawn->GetRootComponent()))
	{
		Root->SetPhysicsLinearVelocity(FVector::ZeroVector);
		Root->SetPhysicsAngularVelocityInDegrees(FVector::ZeroVector);
	}
}

void AODRaceManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	const APawn* Pawn = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Pawn || Checkpoints.Num() < 2 || NextGate >= Checkpoints.Num())
	{
		return;
	}

	// pawn can spawn after our BeginPlay; give the ghost its mesh here
	if (Ghost.IsValid() && Ghost->NeedsMesh())
	{
		if (const auto* Mesh =
				Cast<UStaticMeshComponent>(Pawn->GetRootComponent()))
		{
			Ghost->Init(Mesh->GetStaticMesh());
		}
	}

	// Sub-frame timing (2026-10-09, Taylor: "everything in the game should be
	// independent of frame rate"): ghost samples and gate crossings are
	// placed on the straight path from last Tick's pose to this one, not
	// stamped with the frame's time - lap times were +/- a frame.
	const double Now = GetWorld()->GetTimeSeconds();
	const FVector Pos = Pawn->GetActorLocation();
	const FQuat Rot = Pawn->GetActorQuat();
	if (PrevTime < 0.0)
	{
		PrevPos = Pos;
		PrevRot = Rot;
		PrevTime = Now;
	}
	ON_SCOPE_EXIT
	{
		PrevPos = Pos;
		PrevRot = Rot;
		PrevTime = Now;
	};
	const double FrameDt = Now - PrevTime;

	if (bRunning)
	{
		while (Now - RaceStart >= NextSampleTime)
		{
			const float A = FrameDt > 0.0
				? FMath::Clamp(float((RaceStart + NextSampleTime - PrevTime) / FrameDt), 0.0f, 1.0f) : 1.0f;
			RecPositions.Add(FMath::Lerp(PrevPos, Pos, A));
			RecRotations.Add(FQuat::Slerp(PrevRot, Rot, A).Rotator());
			NextSampleTime += SampleInterval;
		}
	}

	const FVector Gate = Checkpoints[NextGate].GetLocation();

	// visible marker on the next gate
	DrawDebugCylinder(GetWorld(), Gate - FVector(0, 0, 100),
		Gate + FVector(0, 0, 700), GateRadius * 0.5f, 20,
		NextGate == 0 ? FColor::Green
		: (NextGate == Checkpoints.Num() - 1 ? FColor::Blue : FColor::Yellow),
		false, -1.0f, 0, 12.0f);

	if (FVector::Dist2D(Pos, Gate) > GateRadius)
	{
		return;
	}
	// when this frame's path entered the gate circle: |F + t*D| = R, first root
	const FVector2D D(Pos - PrevPos), F(PrevPos - Gate);
	const double Qa = D.SizeSquared(), Qb = 2.0 * FVector2D::DotProduct(F, D);
	const double Qc = F.SizeSquared() - FMath::Square(GateRadius);
	const double Disc = Qb * Qb - 4.0 * Qa * Qc;
	const double Enter = Qc <= 0.0 ? 0.0
		: Qa > 1e-6 && Disc >= 0.0 ? FMath::Clamp((-Qb - FMath::Sqrt(Disc)) / (2.0 * Qa), 0.0, 1.0) : 1.0;
	const double CrossTime = PrevTime + Enter * FrameDt;

	// the start gate surrounds the spawn point: arm it only at rolling speed
	// (horizontal - the spawn drop spikes vertical velocity) for a flying start
	if (NextGate == 0 && Pawn->GetVelocity().Size2D() < 300.0f)
	{
		return;
	}

	if (NextGate == 0)
	{
		bRunning = true;
		RaceStart = float(CrossTime);
		NextSampleTime = 0.0f;
		RecPositions.Reset();
		RecRotations.Reset();
		RecGateTimes.Reset();
		if (Ghost.IsValid())
		{
			Ghost->Play(RaceStart);
		}
	}
	else if (bRunning)
	{
		const float Elapsed = float(CrossTime - RaceStart);
		RecGateTimes.Add(Elapsed);
		// gate index in RecGateTimes = NextGate - 1 after the start gate
		if (BestGateTimes.IsValidIndex(RecGateTimes.Num() - 1))
		{
			SplitDelta = Elapsed - BestGateTimes[RecGateTimes.Num() - 1];
			SplitShownAt = GetWorld()->GetTimeSeconds();
		}
	}
	++NextGate;

	if (NextGate == Checkpoints.Num() && bRunning)
	{
		bRunning = false;
		LastTime = float(CrossTime - RaceStart);
		if (BestTime <= 0.0f || LastTime < BestTime)
		{
			BestTime = LastTime;
			SaveBest();
		}
		NextGate = 0;  // ready for another lap/run
	}
}

void AODRaceManager::SaveBest()
{
	UODRaceSave* Save = Cast<UODRaceSave>(
		UGameplayStatics::CreateSaveGameObject(UODRaceSave::StaticClass()));
	Save->BestTime = BestTime;
	Save->SampleInterval = SampleInterval;
	Save->GhostPositions = RecPositions;
	Save->GhostRotations = RecRotations;
	Save->BestGateTimes = RecGateTimes;
	BestGateTimes = RecGateTimes;
	UGameplayStatics::SaveGameToSlot(Save, SaveSlot(), 0);

	// the new best becomes the ghost for the next run
	if (!Ghost.IsValid() && RecPositions.Num() >= 2)
	{
		AODGhost* G = GetWorld()->SpawnActor<AODGhost>();
		if (const APawn* Car = UGameplayStatics::GetPlayerPawn(this, 0))
		{
			if (const auto* Mesh =
					Cast<UStaticMeshComponent>(Car->GetRootComponent()))
			{
				G->Init(Mesh->GetStaticMesh());
			}
		}
		Ghost = G;
	}
	if (Ghost.IsValid())
	{
		Ghost->SampleInterval = SampleInterval;
		Ghost->Positions = RecPositions;
		Ghost->Rotations = RecRotations;
	}
}
