#include "ODRaceManager.h"
#include "ODTileSource.h"

#include "Algo/BinarySearch.h"
#include "Components/InputComponent.h"
#include "Dom/JsonObject.h"
#include "Components/SkeletalMeshComponent.h"
#include "DrawDebugHelpers.h"
#include "GameFramework/Pawn.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "Components/StaticMeshComponent.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/ScopeExit.h"
#include "ODGhost.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/MemoryWriter.h"

AODRaceManager::AODRaceManager()
{
	PrimaryActorTick.bCanEverTick = true;
}

FString AODRaceManager::SaveSlot() const
{
	return TrackName.IsEmpty()
		? FString::Printf(TEXT("OpenDrive_%s"), *GetWorld()->GetMapName())
		: FString::Printf(TEXT("OpenDrive_track_%s"), *TrackName);
}

FString AODRaceManager::SaveTrack(const FString& Area, const TArray<FVector2D>& XY,
	const TArray<float>& Z, bool bLoop)
{
	// gates mirror pipeline/build_track.py's place_gates + main
	constexpr double SpacingCm = 30000.0, StartCm = 20000.0, LookCm = 1000.0;
	TArray<double> Cum = {0.0};
	for (int32 i = 1; i < XY.Num(); ++i)
	{
		Cum.Add(Cum.Last() + FVector2D::Distance(XY[i - 1], XY[i]));
	}
	const double L = Cum.Last();
	if (XY.Num() < 2 || XY.Num() != Z.Num() || L < 10000.0)
	{
		return FString();   // under 100 m isn't a track
	}
	const double Start = FMath::Min(StartCm, 0.25 * L), End = bLoop ? Start + L : L;
	TArray<double> S;
	for (double s = Start; s < End; s += SpacingCm)
	{
		S.Add(s);
	}
	if (S.Num() > 1 && End - S.Last() < 0.5 * SpacingCm)
	{
		S.Pop();   // no sliver sector before the finish
	}
	S.Add(End);
	auto At = [&](double s)
	{
		s = bLoop ? s - FMath::FloorToDouble(s / L) * L : FMath::Clamp(s, 0.0, L);
		const int32 j = FMath::Clamp((int32)Algo::UpperBound(Cum, s), 1, Cum.Num() - 1);
		const double A = (s - Cum[j - 1]) / FMath::Max(Cum[j] - Cum[j - 1], 1e-6);
		return FVector(FMath::Lerp(XY[j - 1], XY[j], A), FMath::Lerp((double)Z[j - 1], (double)Z[j], A));
	};

	// ponytail: writes into Content/ - fine for dev builds; a packaged build
	// wants FPaths::ProjectSavedDir() (and LoadTrack/LoadTracks reading both)
	const FString Masks = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks");
	const FString ManifestPath = Masks / TEXT("tracks.json");
	FString Text;
	TSharedPtr<FJsonObject> Root;
	if (!FFileHelper::LoadFileToString(Text, *ManifestPath)
		|| !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Root) || !Root.IsValid())
	{
		Root = MakeShared<FJsonObject>();
	}
	int32 Num = 1;
	while (Root->HasField(FString::Printf(TEXT("custom_%d"), Num)))
	{
		++Num;
	}
	const FString Name = FString::Printf(TEXT("custom_%d"), Num);

	TArray<uint8> Bin;
	FMemoryWriter Ar(Bin);
	uint32 Flags = bLoop ? 1 : 0, G = S.Num(), N = XY.Num();
	Ar.Serialize((void*)"ODK1", 4);
	Ar << Flags << G;
	for (const double s : S)
	{
		const FVector P = At(s), D = At(s + LookCm) - At(s - LookCm);
		float V[4] = {(float)P.X, (float)P.Y, (float)P.Z, (float)FMath::RadiansToDegrees(FMath::Atan2(D.Y, D.X))};
		Ar.Serialize(V, sizeof(V));
	}
	Ar << N;
	for (const FVector2D& P : XY)
	{
		float V[2] = {(float)P.X, (float)P.Y};
		Ar.Serialize(V, sizeof(V));
	}
	if (!FFileHelper::SaveArrayToFile(Bin, *(Masks / FString::Printf(TEXT("track_%s.bin"), *Name))))
	{
		return FString();
	}

	const TSharedRef<FJsonObject> Entry = MakeShared<FJsonObject>();
	Entry->SetStringField(TEXT("area"), Area);
	Entry->SetStringField(TEXT("title"), FString::Printf(TEXT("Custom %d"), Num));
	Entry->SetBoolField(TEXT("loop"), bLoop);
	Entry->SetNumberField(TEXT("km"), FMath::RoundToDouble(L / 1000.0) / 100.0);
	Root->SetObjectField(Name, Entry);
	Text.Reset();
	FJsonSerializer::Serialize(Root.ToSharedRef(), TJsonWriterFactory<>::Create(&Text));
	if (!FFileHelper::SaveStringToFile(Text, *ManifestPath))
	{
		return FString();
	}
	UE_LOG(LogTemp, Display, TEXT("ODRaceManager: saved track '%s' (%s) - %.2f km, %d gates, %s"),
		*Name, *Area, L / 1e5, S.Num(), bLoop ? TEXT("loop") : TEXT("point to point"));
	return Name;
}

bool AODRaceManager::LoadTrack()
{
	// pipeline/build_track.py: 'ODK1' + uint32 flags (bit 0 loop) + uint32 G
	// + G * (x, y, z cm, yaw deg) + uint32 N + N * (x, y cm), all float32
	TArray<uint8> Bin;
	const FString Path = FPaths::ProjectContentDir()
		/ FString::Printf(TEXT("OpenDrive/Masks/track_%s.bin"), *TrackName);
	if (!FFileHelper::LoadFileToArray(Bin, *Path) || Bin.Num() < 12
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'K' || Bin[3] != '1')
	{
		UE_LOG(LogTemp, Warning, TEXT("ODRaceManager: no track at %s"), *Path);
		return false;
	}
	auto U32 = [&](int64 At) { uint32 V; FMemory::Memcpy(&V, Bin.GetData() + At, 4); return V; };
	auto F32 = [&](int64 At) { float V; FMemory::Memcpy(&V, Bin.GetData() + At, 4); return V; };
	const int64 G = U32(8);
	const int64 LineAt = 12 + G * 16;
	if (G < 2 || Bin.Num() < LineAt + 4 || Bin.Num() < LineAt + 4 + (int64)U32(LineAt) * 8)
	{
		UE_LOG(LogTemp, Warning, TEXT("ODRaceManager: %s is truncated"), *Path);
		return false;
	}
	bLoop = (U32(4) & 1) != 0;
	Checkpoints.Reset(G);
	for (int64 i = 0; i < G; ++i)
	{
		const int64 At = 12 + i * 16;
		Checkpoints.Add(FTransform(FRotator(0.0f, F32(At + 12), 0.0f),
			FVector(F32(At), F32(At + 4), F32(At + 8))));
	}
	const int64 N = U32(LineAt);
	TrackLine.SetNumUninitialized(N);
	for (int64 i = 0; i < N; ++i)
	{
		TrackLine[i] = FVector2D(F32(LineAt + 4 + i * 8), F32(LineAt + 8 + i * 8));
	}
	UE_LOG(LogTemp, Display, TEXT("ODRaceManager: track '%s' - %d gates, %s"),
		*TrackName, Checkpoints.Num(), bLoop ? TEXT("loop") : TEXT("point to point"));
	return true;
}

void AODRaceManager::LoadCenterline()
{
	// same bin-sidecar pattern as ODForestManager's terrain mask: works in
	// editor, standalone, AND packaged builds (no editor-only Texture Source)
	TArray<uint8> Bin;
	const FString Path = ODTileSource::Fetch(
		FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks/centerline.bin"));
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
	FParse::Value(FCommandLine::Get(), TEXT("odtrack="), TrackName);
	if (!TrackName.IsEmpty())
	{
		// open-world track: the map keeps drawing the area's roads (empty
		// RoadCenterline = ODRaceHUD falls back to AODRoadManager's)
		if (!LoadTrack())
		{
			TrackName.Empty();
			Checkpoints.Reset();
		}
	}
	else
	{
		LoadCenterline();
	}

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

void AODRaceManager::EndPlay(const EEndPlayReason::Type Reason)
{
	// the menu swaps tracks by destroying this actor - the PB ghost goes with it
	if (Ghost.IsValid())
	{
		Ghost->Destroy();
	}
	Super::EndPlay(Reason);
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
		StartRun(CrossTime);
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
		if (bLoop)
		{
			// circuit: the finish line is the start line - the next lap
			// starts at this same crossing, no re-arm or speed check
			StartRun(CrossTime);
			NextGate = 1;
		}
	}
}

void AODRaceManager::StartRun(double CrossTime)
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
