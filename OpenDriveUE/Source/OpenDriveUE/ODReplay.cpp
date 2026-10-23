#include "ODReplay.h"

#include "Camera/CameraComponent.h"
#include "CanvasItem.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/WorldSettings.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/App.h"
#include "ODHudStyle.h"
#include "ODRaceHUD.h"
#include "ODVehiclePawn.h"

namespace
{
const float Speeds[] = {0.25f, 0.5f, 1.0f, 2.0f};
constexpr float ScrubRate = 4.0f;   // x real time while Left/Right is held
}

// ---------------------------------------------------------------- frame/buffer

FODReplayFrame FODReplayFrame::Lerp(const FODReplayFrame& A, const FODReplayFrame& B, float T)
{
	FODReplayFrame F = T < 0.5f ? A : B;   // discrete fields
	F.Pos = FMath::Lerp(A.Pos, B.Pos, T);
	F.Rot = FQuat::Slerp(A.Rot, B.Rot, T);
	for (int32 i = 0; i < 4; ++i)
	{
		F.Spring[i] = FMath::Lerp(A.Spring[i], B.Spring[i], T);
		F.Steer[i] = FMath::Lerp(A.Steer[i], B.Steer[i], T);
		F.Omega[i] = FMath::Lerp(A.Omega[i], B.Omega[i], T);
	}
	F.Rpm = FMath::Lerp(A.Rpm, B.Rpm, T);
	F.Throttle = FMath::Lerp(A.Throttle, B.Throttle, T);
	F.Brake = FMath::Lerp(A.Brake, B.Brake, T);
	F.EffThrottle = FMath::Lerp(A.EffThrottle, B.EffThrottle, T);
	F.Load = FMath::Lerp(A.Load, B.Load, T);
	F.Speed = FMath::Lerp(A.Speed, B.Speed, T);
	return F;
}

void FODReplayBuffer::Record(const FODReplayFrame& Prev, double PrevT, const FODReplayFrame& Cur, double CurT)
{
	if (Ring.Num() != Capacity)
	{
		Ring.SetNum(Capacity);
	}
	// a gap (showroom, first frame) snaps the grid instead of back-filling it
	NextT = FMath::Max(NextT, PrevT);
	const double Dt = CurT - PrevT;
	while (NextT <= CurT)
	{
		const float A = Dt > 0.0 ? float((NextT - PrevT) / Dt) : 1.0f;
		Ring[Head] = FODReplayFrame::Lerp(Prev, Cur, A);
		Head = (Head + 1) % Capacity;
		Count = FMath::Min(Count + 1, Capacity);
		NextT += 1.0 / Hz;
	}
}

FODReplayFrame FODReplayBuffer::Sample(float T) const
{
	if (Count < 2)
	{
		return Count ? Get(0) : FODReplayFrame();
	}
	const float F = FMath::Clamp(T * Hz, 0.0f, float(Count - 1));
	const int32 I = FMath::Min(FMath::FloorToInt(F), Count - 2);
	return FODReplayFrame::Lerp(Get(I), Get(I + 1), F - I);
}

bool FODReplayBuffer::SelfCheck()
{
	// linear signal X = 100 t, yaw = 2 t deg, recorded at uneven frame times
	auto Frame = [](double T)
	{
		FODReplayFrame F;
		F.Pos.X = 100.0 * T;
		F.Rot = FRotator(0.0, 2.0 * T, 0.0).Quaternion();
		F.Gear = int32(T);
		return F;
	};
	auto Run = [&Frame](const TArray<double>& Dts)
	{
		FODReplayBuffer B;
		double T = 0.0;
		for (int32 i = 0; T < 35.0; ++i)
		{
			const double Next = FMath::Min(T + Dts[i % Dts.Num()], 35.0);
			B.Record(Frame(T), T, Frame(Next), Next);
			T = Next;
		}
		return B;
	};
	const FODReplayBuffer Odd = Run({1.0 / 30, 1.0 / 144, 0.05, 0.003, 1.0 / 60});
	const FODReplayBuffer Slow = Run({1.0 / 30});
	const FODReplayBuffer Fast = Run({1.0 / 144});

	bool bOk = Odd.Num() == Capacity && Slow.Num() == Capacity && Fast.Num() == Capacity;
	const double Step = 100.0 / Hz;
	for (int32 i = 0; bOk && i < Capacity; ++i)
	{
		const double X = Odd.Get(i).Pos.X;
		bOk &= i == 0 || FMath::IsNearlyEqual(X - Odd.Get(i - 1).Pos.X, Step, 1e-3);   // even grid
		bOk &= FMath::IsNearlyEqual(X, Slow.Get(i).Pos.X, 1e-3)
			&& FMath::IsNearlyEqual(X, Fast.Get(i).Pos.X, 1e-3);                     // fps-independent
	}
	// newest sample is the last grid point <= the last recorded time
	bOk &= Odd.Get(Capacity - 1).Pos.X <= 3500.0 + 1e-3 && Odd.Get(Capacity - 1).Pos.X > 3500.0 - Step - 1e-3;
	bOk &= FMath::IsNearlyEqual(Odd.Duration(), (Capacity - 1) / float(Hz), 1e-4f);

	// interpolation between grid points
	const FODReplayFrame Mid = Odd.Sample(10.5f / Hz);
	bOk &= FMath::IsNearlyEqual(Mid.Pos.X, 0.5 * (Odd.Get(10).Pos.X + Odd.Get(11).Pos.X), 1e-3);
	bOk &= FMath::IsNearlyEqual(Odd.Sample(-5.0f).Pos.X, Odd.Get(0).Pos.X, 1e-3)
		&& FMath::IsNearlyEqual(Odd.Sample(1e6f).Pos.X, Odd.Get(Capacity - 1).Pos.X, 1e-3);

	// slerp midpoint and nearest-frame discrete fields
	FODReplayFrame A, B;
	A.Rot = FRotator(0, 10, 0).Quaternion();
	B.Rot = FRotator(0, 30, 0).Quaternion();
	A.Gear = 2;
	B.Gear = 3;
	bOk &= FMath::IsNearlyEqual(FODReplayFrame::Lerp(A, B, 0.5f).Rot.Rotator().Yaw, 20.0, 1e-3);
	bOk &= FODReplayFrame::Lerp(A, B, 0.4f).Gear == 2 && FODReplayFrame::Lerp(A, B, 0.6f).Gear == 3;

	UE_LOG(LogTemp, Display, TEXT("ODSelfCheck: replay ring buffer %s"), bOk ? TEXT("PASS") : TEXT("FAIL"));
	return bOk;
}

// ---------------------------------------------------------------- actor

AODReplay::AODReplay()
{
	PrimaryActorTick.bCanEverTick = true;
	Cam = CreateDefaultSubobject<UCameraComponent>(TEXT("Trackside"));
	RootComponent = Cam;
	Cam->SetFieldOfView(40.0f);   // long lens, TV-style
}

AODReplay* AODReplay::Find(const UWorld* World)
{
	if (!World)
	{
		return nullptr;
	}
	TActorIterator<AODReplay> It(const_cast<UWorld*>(World));
	return It ? *It : nullptr;
}

bool AODReplay::IsActiveIn(const UWorld* World)
{
	const AODReplay* R = Find(World);
	return R && R->IsActive();
}

float AODReplay::Rate() const
{
	return Scrub ? Scrub * ScrubRate : bPaused ? 0.0f : Speeds[SpeedIdx];
}

FODReplayFrame AODReplay::Capture() const
{
	FODReplayFrame F;
	const UODVehicleMovement* M = Car->Movement;
	F.Pos = Car->GetActorLocation();
	F.Rot = Car->GetActorQuat();
	for (int32 i = 0; i < FMath::Min(4, M->Wheels.Num()); ++i)
	{
		const FODWheel& W = M->Wheels[i];
		F.Spring[i] = W.SpringLen;
		F.Steer[i] = W.SteerDeg;
		F.Omega[i] = W.Omega;
		F.Contact |= W.bInContact ? 1 << i : 0;
		F.Surface[i] = W.Surface;
	}
	F.Rpm = M->EngineRpm;
	F.Throttle = M->ThrottleIn;
	F.Brake = M->BrakeIn;
	F.EffThrottle = M->EffectiveThrottle;
	F.Load = M->Load;
	F.Speed = M->FwdSpeedCmS;
	F.Gear = M->Gear;
	return F;
}

void AODReplay::Apply(const FODReplayFrame& F)
{
	Car->SetActorLocationAndRotation(F.Pos, F.Rot, false, nullptr, ETeleportType::TeleportPhysics);
	UODVehicleMovement* M = Car->Movement;
	for (int32 i = 0; i < FMath::Min(4, M->Wheels.Num()); ++i)
	{
		FODWheel& W = M->Wheels[i];
		W.SpringLen = F.Spring[i];
		W.SteerDeg = F.Steer[i];
		W.Omega = F.Omega[i];
		W.bInContact = (F.Contact >> i) & 1;
		W.Surface = F.Surface[i];
	}
	M->EngineRpm = F.Rpm;
	M->ThrottleIn = F.Throttle;
	M->BrakeIn = F.Brake;
	M->EffectiveThrottle = F.EffThrottle;
	M->Load = F.Load;
	M->FwdSpeedCmS = F.Speed;
	M->Gear = F.Gear;
	M->bIgnitionCut = false;
}

void AODReplay::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	if (!bActive)
	{
		Car = Cast<AODVehiclePawn>(UGameplayStatics::GetPlayerPawn(this, 0));
		if (!Car.IsValid() || !Car->Body->IsSimulatingPhysics())
		{
			PrevT = -1.0;
			return;
		}
		const FODReplayFrame Cur = Capture();
		const double Now = GetWorld()->GetTimeSeconds();
		if (PrevT >= 0.0)
		{
			Buf.Record(Prev, PrevT, Cur, Now);
		}
		Prev = Cur;
		PrevT = Now;
		return;
	}
	if (!Car.IsValid())
	{
		Exit();
		return;
	}
	// the world is dilated to ~0; the car (spring arm, wheel spin) and we run
	// on real time
	const float Eff = GetWorldSettings()->GetEffectiveTimeDilation();
	Car->CustomTimeDilation = 1.0f / Eff;
	const float Dur = Buf.Duration();
	Play = FMath::Clamp(Play + FApp::GetDeltaTime() * Rate(), 0.0f, Dur);
	if (Play >= Dur && !Scrub)
	{
		bPaused = true;
	}
	Car->ReplaySpinScale = Rate();
	Apply(Buf.Sample(Play));
	if (bTrackside)
	{
		UpdateTrackside();
	}
}

void AODReplay::Toggle()
{
	if (bActive)
	{
		Exit();
	}
	else
	{
		Enter();
	}
}

void AODReplay::Enter()
{
	Car = Cast<AODVehiclePawn>(UGameplayStatics::GetPlayerPawn(this, 0));
	APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0);
	if (!Car.IsValid() || !PC || Car->IsInShowroom() || !Car->Body->IsSimulatingPhysics()
		|| Buf.Num() < 2 || UGameplayStatics::IsGamePaused(this))
	{
		return;
	}
	UODVehicleMovement* M = Car->Movement;
	SavedXf = Car->GetActorTransform();
	SavedV = Car->Body->GetPhysicsLinearVelocity();
	SavedW = Car->Body->GetPhysicsAngularVelocityInDegrees();
	SavedMove = Capture();
	SavedWheels = M->Wheels;
	bSavedCut = M->bIgnitionCut;
	SavedDilation = UGameplayStatics::GetGlobalTimeDilation(this);

	Car->Body->SetSimulatePhysics(false);
	Car->bInReplay = true;
	UGameplayStatics::SetGlobalTimeDilation(this, 0.0001f);   // WorldSettings' floor
	Car->AddTickPrerequisiteActor(this);   // Apply() before the pawn draws wheels

	bActive = true;
	bPaused = false;
	Scrub = 0;
	SpeedIdx = 2;
	Play = FMath::Max(0.0f, Buf.Duration() - 10.0f);
	SetTrackside(bTrackside);

	EnableInput(PC);
	if (InputComponent && InputComponent->KeyBindings.Num() == 0)
	{
		for (const FKey& K : {EKeys::Left, EKeys::Right, EKeys::Gamepad_DPad_Left, EKeys::Gamepad_DPad_Right})
		{
			InputComponent->BindKey(K, IE_Pressed, this, &AODReplay::OnKey);
			InputComponent->BindKey(K, IE_Released, this, &AODReplay::OnKeyUp);
		}
		for (const FKey& K : {EKeys::Up, EKeys::Down, EKeys::Gamepad_RightShoulder, EKeys::Gamepad_LeftShoulder,
			EKeys::SpaceBar, EKeys::Gamepad_FaceButton_Bottom, EKeys::T, EKeys::Gamepad_FaceButton_Left})
		{
			InputComponent->BindKey(K, IE_Pressed, this, &AODReplay::OnKey);
		}
	}
}

void AODReplay::Exit()
{
	bActive = false;
	UGameplayStatics::SetGlobalTimeDilation(this, SavedDilation);
	APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0);
	if (PC)
	{
		DisableInput(PC);
	}
	if (!Car.IsValid())
	{
		return;
	}
	if (PC)
	{
		PC->SetViewTarget(Car.Get());
	}
	Car->RemoveTickPrerequisiteActor(this);
	Car->CustomTimeDilation = 1.0f;
	Car->bInReplay = false;
	Car->ReplaySpinScale = 1.0f;

	Apply(SavedMove);
	UODVehicleMovement* M = Car->Movement;
	M->Wheels = SavedWheels;
	M->bIgnitionCut = bSavedCut;
	Car->SetActorTransform(SavedXf, false, nullptr, ETeleportType::TeleportPhysics);
	Car->Body->SetSimulatePhysics(true);
	Car->Body->SetPhysicsLinearVelocity(SavedV);
	Car->Body->SetPhysicsAngularVelocityInDegrees(SavedW);
	M->ResetStepHistory(SavedV, SavedW);
	PrevT = -1.0;   // recorder resyncs on the restored pose
}

void AODReplay::SetTrackside(bool bOn)
{
	bTrackside = bOn;
	bHaveSpot = false;
	APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0);
	if (PC && Car.IsValid())
	{
		PC->SetViewTarget(bOn ? static_cast<AActor*>(this) : Car.Get());
	}
	if (bOn)
	{
		UpdateTrackside();
	}
}

// ponytail: spots come from the recorded path, not the road network - a
// camera 9 m right of where the car went, 60 m ahead. Use road-side points
// (AODRoadManager) if it lands in hedges.
void AODReplay::UpdateTrackside()
{
	const float Dur = Buf.Duration();
	const FVector CarPos = Car->GetActorLocation();
	auto Tangent = [this, Dur](float T)
	{
		return (Buf.Sample(FMath::Min(T + 0.25f, Dur)).Pos - Buf.Sample(FMath::Max(T - 0.25f, 0.0f)).Pos).GetSafeNormal2D();
	};
	const bool bPassed = FVector::DotProduct(CarPos - Spot, Tangent(Play)) > 2500.0f;
	if (!bHaveSpot || bPassed || FVector::Dist(CarPos, Spot) > 15000.0f)
	{
		float T = Play;
		while (T < Dur && FVector::Dist2D(Buf.Sample(T).Pos, CarPos) < 6000.0f)
		{
			T += 0.1f;
		}
		const FVector P = Buf.Sample(T).Pos;
		Spot = P + FVector::CrossProduct(FVector::UpVector, Tangent(T)) * 900.0f;
		FHitResult Hit;
		FCollisionQueryParams Q(SCENE_QUERY_STAT(ODReplaySpot), false, Car.Get());
		if (GetWorld()->LineTraceSingleByChannel(Hit, Spot + FVector(0, 0, 5000), Spot - FVector(0, 0, 5000),
			ECC_Visibility, Q))
		{
			Spot.Z = Hit.ImpactPoint.Z;
		}
		Spot.Z += 250.0f;
		bHaveSpot = true;
	}
	Cam->SetWorldLocationAndRotation(Spot, (CarPos - Spot).Rotation());
}

void AODReplay::OnKey(FKey Key)
{
	if (Key == EKeys::Left || Key == EKeys::Gamepad_DPad_Left)
	{
		Scrub = -1;
	}
	else if (Key == EKeys::Right || Key == EKeys::Gamepad_DPad_Right)
	{
		Scrub = +1;
	}
	else if (Key == EKeys::Up || Key == EKeys::Gamepad_RightShoulder)
	{
		SpeedIdx = FMath::Min(SpeedIdx + 1, int32(UE_ARRAY_COUNT(Speeds)) - 1);
	}
	else if (Key == EKeys::Down || Key == EKeys::Gamepad_LeftShoulder)
	{
		SpeedIdx = FMath::Max(SpeedIdx - 1, 0);
	}
	else if (Key == EKeys::SpaceBar || Key == EKeys::Gamepad_FaceButton_Bottom)
	{
		if (bPaused && Play >= Buf.Duration())
		{
			Play = 0.0f;   // play again from the oldest sample
		}
		bPaused = !bPaused;
	}
	else
	{
		SetTrackside(!bTrackside);
	}
}

void AODReplay::OnKeyUp(FKey Key)
{
	Scrub = 0;
}

void AODReplay::DrawOverlay(UCanvas* Canvas, float S) const
{
	using namespace ODHud;
	auto Rect = [Canvas](float X, float Y, float W, float H, const FLinearColor& Col)
	{
		FCanvasTileItem T(FVector2D(X, Y), FVector2D(W, H), Col);
		T.BlendMode = SE_BLEND_Translucent;
		Canvas->DrawItem(T);
	};
	auto Text = [Canvas, S](const FString& Str, float X, float Y, float Size, const FLinearColor& Col, bool bCenter)
	{
		FCanvasTextItem T(FVector2D(X, Y), FText::FromString(Str), MenuFont(EMenuWeight::SemiBold, Size * S), Col);
		T.bCentreX = bCenter;
		T.bCentreY = true;
		T.EnableShadow(FLinearColor(0, 0, 0, 0.45f), FVector2D(S, S));
		T.Font = GEngine->GetLargeFont();   // see FMenuPaint::Text
		Canvas->DrawItem(T);
	};

	const float W = Canvas->ClipX, H = Canvas->ClipY;
	const float BarW = 900.0f * S, BarX = (W - BarW) * 0.5f, BarY = H - 90.0f * S;
	const float Dur = FMath::Max(Buf.Duration(), KINDA_SMALL_NUMBER);
	Rect(BarX - 16 * S, BarY - 44 * S, BarW + 32 * S, 80 * S, FLinearColor(Ink.R, Ink.G, Ink.B, 0.7f));
	Rect(BarX, BarY, BarW, 6 * S, FLinearColor(DimCol.R, DimCol.G, DimCol.B, 0.5f));
	Rect(BarX, BarY, BarW * Play / Dur, 6 * S, Accent);

	const FString State = Scrub < 0 ? TEXT("<< scrub") : Scrub > 0 ? TEXT("scrub >>")
		: bPaused ? TEXT("PAUSED") : FString::Printf(TEXT("%gx"), Speeds[SpeedIdx]);
	Text(FString::Printf(TEXT("REPLAY  -%.1fs  %s%s"), Buf.Duration() - Play, *State,
		bTrackside ? TEXT("  TRACKSIDE") : TEXT("")), BarX, BarY - 22 * S, 22, Accent, false);
	Text(TEXT("<- -> scrub   up/down speed   space pause   T trackside   C view   I exit"),
		BarX, BarY + 20 * S, 16, DimCol, false);
}

// ---------------------------------------------------------------- HUD hook

void AODRaceHUD::ToggleReplay()
{
	if (AODReplay* R = AODReplay::Find(GetWorld()))
	{
		R->Toggle();
	}
}
