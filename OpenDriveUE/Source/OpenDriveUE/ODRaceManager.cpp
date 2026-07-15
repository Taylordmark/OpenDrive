#include "ODRaceManager.h"

#include "Components/InputComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "DrawDebugHelpers.h"
#include "GameFramework/Pawn.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "ODGhost.h"
#include "WheeledVehiclePawn.h"

AODRaceManager::AODRaceManager()
{
	PrimaryActorTick.bCanEverTick = true;
}

FString AODRaceManager::SaveSlot() const
{
	return FString::Printf(TEXT("OpenDrive_%s"), *GetWorld()->GetMapName());
}

void AODRaceManager::BeginPlay()
{
	Super::BeginPlay();

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
			if (const AWheeledVehiclePawn* Car = Cast<AWheeledVehiclePawn>(
					UGameplayStatics::GetPlayerPawn(this, 0)))
			{
				G->Init(Car->GetMesh()->GetSkeletalMeshAsset());
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
		if (const AWheeledVehiclePawn* Car = Cast<AWheeledVehiclePawn>(Pawn))
		{
			Ghost->Init(Car->GetMesh()->GetSkeletalMeshAsset());
		}
	}

	if (bRunning)
	{
		while (GetWorld()->GetTimeSeconds() - RaceStart >= NextSampleTime)
		{
			RecPositions.Add(Pawn->GetActorLocation());
			RecRotations.Add(Pawn->GetActorRotation());
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

	if (FVector::Dist2D(Pawn->GetActorLocation(), Gate) > GateRadius)
	{
		return;
	}

	if (NextGate == 0)
	{
		bRunning = true;
		RaceStart = GetWorld()->GetTimeSeconds();
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
		const float Elapsed = GetWorld()->GetTimeSeconds() - RaceStart;
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
		LastTime = GetWorld()->GetTimeSeconds() - RaceStart;
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
		if (const AWheeledVehiclePawn* Car = Cast<AWheeledVehiclePawn>(
				UGameplayStatics::GetPlayerPawn(this, 0)))
		{
			G->Init(Car->GetMesh()->GetSkeletalMeshAsset());
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
