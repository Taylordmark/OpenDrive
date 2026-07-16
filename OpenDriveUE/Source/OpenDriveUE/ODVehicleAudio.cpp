#include "ODVehicleAudio.h"

#include "AudioMixerBlueprintLibrary.h"
#include "ChaosVehicleWheel.h"
#include "ChaosWheeledVehicleMovementComponent.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/CommandLine.h"
#include "Misc/Paths.h"
#include "ODEngineSynth.h"
#include "ODRoadNoiseSynth.h"
#include "PhysicalMaterials/PhysicalMaterial.h"
#include "WheeledVehiclePawn.h"

AODVehicleAudio::AODVehicleAudio()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODVehicleAudio::BeginPlay()
{
	Super::BeginPlay();
	FParse::Value(FCommandLine::Get(), TEXT("odrev="), RevAt);
	FParse::Value(FCommandLine::Get(), TEXT("odrec="), RecSeconds);
}

void AODVehicleAudio::EnsureSynths(AWheeledVehiclePawn* Car)
{
	if (Engine && BoundCar == Car)
	{
		return;
	}
	BoundCar = Car;

	Engine = NewObject<UODEngineSynth>(Car);
	Engine->SetupAttachment(Car->GetRootComponent());
	Engine->RegisterComponent();
	Engine->Start();

	Road = NewObject<UODRoadNoiseSynth>(Car);
	Road->SetupAttachment(Car->GetRootComponent());
	Road->RegisterComponent();
	Road->Start();

	if (auto* Mesh = Car->GetMesh())
	{
		Mesh->OnComponentHit.AddUniqueDynamic(this, &AODVehicleAudio::OnVehicleHit);
	}
	UE_LOG(LogTemp, Display, TEXT("ODVehicleAudio: synths attached to %s"),
		*Car->GetName());
}

void AODVehicleAudio::OnVehicleHit(UPrimitiveComponent*, AActor*,
	UPrimitiveComponent*, FVector NormalImpulse, const FHitResult&)
{
	const double Now = GetWorld()->GetTimeSeconds();
	if (Now - LastImpactTime < 0.12)
	{
		return;
	}
	const float Intensity = NormalImpulse.Size() / FullImpactImpulse;
	if (Intensity < 0.02f || !Road)
	{
		return;
	}
	LastImpactTime = Now;
	Road->TriggerImpact(Intensity);
}

void AODVehicleAudio::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	auto* Car = Cast<AWheeledVehiclePawn>(UGameplayStatics::GetPlayerPawn(this, 0));
	if (!Car)
	{
		return;
	}
	EnsureSynths(Car);

	auto* Move = Cast<UChaosWheeledVehicleMovementComponent>(
		Car->GetVehicleMovementComponent());
	if (!Move)
	{
		return;
	}

	const float Rpm = Move->GetEngineRotationSpeed();
	const float MaxRpm = FMath::Max(Move->GetEngineMaxRotationSpeed(), 1.0f);
	const float Throttle = Move->GetThrottleInput();
	// crude load: throttle biased up at low revs (lugging), down near redline
	const float Load = FMath::Clamp(
		Throttle * (1.25f - 0.5f * (Rpm / MaxRpm)), 0.0f, 1.0f);
	Engine->SetEngineState(Rpm, Throttle, Load);

	// surface color from the first grounded wheel's contact material
	float Cutoff = 3200.0f;                 // asphalt hiss default
	bool bGrounded = false;
	for (UChaosVehicleWheel* Wheel : Move->Wheels)
	{
		const UPhysicalMaterial* Mat =
			Wheel ? Wheel->GetContactSurfaceMaterial() : nullptr;
		if (!Mat)
		{
			continue;
		}
		bGrounded = true;
		const FString Name = Mat->GetName();
		if (Name.Contains(TEXT("Dirt")))
		{
			Cutoff = 900.0f;                // gravel rumble
		}
		else if (Name.Contains(TEXT("Grass")))
		{
			Cutoff = 600.0f;                // soft thrum
		}
		break;
	}
	const float Speed01 = FMath::Abs(Move->GetForwardSpeed()) / TopSpeed;
	Road->SetRoadState(Speed01, Cutoff, bGrounded);

	// diagnostic heartbeat (audio bring-up, 2026-07-15)
	static double LastLog = 0.0;
	if (const double NowD = GetWorld()->GetTimeSeconds(); NowD - LastLog > 3.0)
	{
		LastLog = NowD;
		UE_LOG(LogTemp, Display,
			TEXT("ODVehicleAudio: rpm=%.0f thr=%.2f gen=%d rms=%.4f playing=%d"),
			Rpm, Throttle, Engine->GenCalls.load(), Engine->LastRms.load(),
			Engine->IsPlaying() ? 1 : 0);
	}

	// ---- headless test hooks ----
	const float Now = GetWorld()->GetTimeSeconds();
	if (RevAt > 0.0f && Now > RevAt)
	{
		Move->SetThrottleInput(1.0f);
	}
	if (RecSeconds > 0.0f && !bRecDone)
	{
		if (!bRecording && Now > 3.0f)
		{
			bRecording = true;
			UAudioMixerBlueprintLibrary::StartRecordingOutput(this, RecSeconds);
			UE_LOG(LogTemp, Display, TEXT("ODVehicleAudio: recording started"));
		}
		else if (bRecording && Now > 3.0f + RecSeconds)
		{
			bRecDone = true;
			// must be absolute: relative paths get prefixed with
			// Saved/BouncedWavFiles/ inside FSoundWavePCMWriter
			UAudioMixerBlueprintLibrary::StopRecordingOutput(this,
				EAudioRecordingExportType::WavFile, TEXT("odrec"),
				FPaths::ConvertRelativePathToFull(
					FPaths::ProjectSavedDir() / TEXT("AudioTest")));
			UE_LOG(LogTemp, Display, TEXT("ODVehicleAudio: recording saved"));
		}
	}
}
