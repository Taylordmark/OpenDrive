#include "ODVehicleAudio.h"

#include "AudioMixerBlueprintLibrary.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/CommandLine.h"
#include "Misc/Paths.h"
#include "ODEngineSynth.h"
#include "ODRoadNoiseSynth.h"
#include "ODVehicleMovement.h"
#include "PhysicalMaterials/PhysicalMaterial.h"

AODVehicleAudio::AODVehicleAudio()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODVehicleAudio::BeginPlay()
{
	Super::BeginPlay();
	FParse::Value(FCommandLine::Get(), TEXT("odrec="), RecSeconds);
}

void AODVehicleAudio::EnsureSynths(APawn* Car)
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

	if (auto* Root = Cast<UPrimitiveComponent>(Car->GetRootComponent()))
	{
		Root->OnComponentHit.AddUniqueDynamic(this, &AODVehicleAudio::OnVehicleHit);
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

	APawn* Car = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Car)
	{
		return;
	}
	auto* Move = Car->FindComponentByClass<UODVehicleMovement>();
	if (!Move)
	{
		return;
	}
	EnsureSynths(Car);

	// the movement sim owns engine telemetry; fuel cuts and shift cuts
	// arrive via EffectiveThrottle so they're audible for free
	const float Rpm = Move->GetRpm();
	Engine->SetEngineState(Rpm, Move->GetEffectiveThrottle(), Move->GetLoad());
	Engine->SetIgnitionCut(Move->IsIgnitionCut());   // hard edge, unsmoothed

	// surface color from the first grounded wheel's contact material
	float Cutoff = 3200.0f;                 // asphalt hiss default
	bool bGrounded = false;
	for (int32 i = 0; i < Move->NumWheels(); ++i)
	{
		const UPhysicalMaterial* Mat = Move->GetContactSurface(i);
		if (!Move->IsWheelInContact(i) || !Mat)
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
			Rpm, Move->GetEffectiveThrottle(), Engine->GenCalls.load(),
			Engine->LastRms.load(), Engine->IsPlaying() ? 1 : 0);
	}

	// ---- -odrec hook ----
	const float Now = GetWorld()->GetTimeSeconds();
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
