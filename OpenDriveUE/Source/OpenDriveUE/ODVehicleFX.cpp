#include "ODVehicleFX.h"

#include "Components/InstancedStaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "EngineUtils.h"
#include "Materials/MaterialInterface.h"
#include "ODVehicleMovement.h"
#include "ODVisualStyle.h"
#include "ODWeatherController.h"
#include "PhysicalMaterials/PhysicalMaterial.h"

namespace
{
	// Rate tuning (puffs per second per wheel). Mirrored by
	// pipeline/tests/test_vehicle_fx_rates.py - change both together.
	constexpr float SmokeMax = 40.0f;
	constexpr float SmokeSlipLo = 3.0f, SmokeSlipHi = 12.0f;   // m/s of contact-patch slide
	constexpr float DustMax = 30.0f;
	constexpr float DustStirLo = 2.0f, DustStirHi = 25.0f;     // m/s of speed + 2 x slip
	constexpr float DustWetCut = 0.85f;                        // wet gravel/dirt is mud, not dust
	constexpr float SprayMax = 35.0f;
	constexpr float SprayWetLo = 0.1f, SprayWetHi = 0.6f;
	constexpr float SpraySpeedLo = 4.0f, SpraySpeedHi = 30.0f; // m/s

	// Pool: 4 wheels x 40/s smoke x ~2.6 s life ~= 420 live at a full
	// burnout. A full pool recycles the oldest slot, which is fps-independent
	// (the spawn count is), so a hard limit only shortens the longest trails.
	constexpr int32 PoolSize = 512;
	constexpr int32 NumCustom = 4;   // R, G, B, Alpha - read by M_FXPuff

	struct FKind
	{
		float Life;        // s (x0.8..1.2 per puff)
		float Size0, Size1;// diameter at birth / death, cm
		float Alpha;       // peak opacity
		float Drag;        // 1/s, exponential velocity decay
		float Inherit;     // fraction of the contact patch's velocity a puff keeps
		float Spread;      // random velocity, cm/s
		float Kick;        // up, cm/s
		FVector Accel;     // cm/s^2 (buoyancy +, droplets -)
		FLinearColor Color;
	};
	// index = 0 smoke, 1 dust, 2 spray (the FODFxRates order)
	const FKind Kinds[3] = {
		{ 2.6f, 60.0f, 340.0f, 0.50f, 1.6f, 0.25f,  70.0f,  30.0f, FVector(0, 0, 40),   FLinearColor(0.80f, 0.80f, 0.82f) },
		{ 1.8f, 50.0f, 280.0f, 0.45f, 2.0f, 0.35f, 110.0f, 120.0f, FVector(0, 0, -30),  FLinearColor(0.52f, 0.45f, 0.35f) },
		{ 0.7f, 30.0f, 170.0f, 0.28f, 2.5f, 0.55f, 120.0f, 140.0f, FVector(0, 0, -350), FLinearColor(0.80f, 0.82f, 0.85f) },
	};
}

UODVehicleFX::UODVehicleFX()
{
	PrimaryComponentTick.bCanEverTick = true;
	// after the pawn has applied this frame's physics pose
	PrimaryComponentTick.TickGroup = TG_PostPhysics;
}

EODFxSurface UODVehicleFX::Classify(const UPhysicalMaterial* Mat)
{
	if (!Mat)
	{
		return EODFxSurface::None;
	}
	const FString Name = Mat->GetName();
	if (Name.Contains(TEXT("Asphalt")))
	{
		return EODFxSurface::Asphalt;
	}
	if (Name.Contains(TEXT("Gravel")) || Name.Contains(TEXT("Dirt")))   // PM_Gravel, PM_DirtRoad, PM_Dirt (landscape)
	{
		return EODFxSurface::Loose;
	}
	return EODFxSurface::None;   // grass, guardrail, anything new
}

FODFxRates UODVehicleFX::Rates(EODFxSurface Surface, float SlipMs, float SpeedMs, float Wetness)
{
	FODFxRates R;
	const float Wet = FMath::Clamp(Wetness, 0.0f, 1.0f);
	if (Surface == EODFxSurface::Asphalt)
	{
		R.Smoke = SmokeMax * FMath::SmoothStep(SmokeSlipLo, SmokeSlipHi, SlipMs) * (1.0f - Wet);
		R.Spray = SprayMax * FMath::SmoothStep(SprayWetLo, SprayWetHi, Wet)
			* FMath::Clamp((SpeedMs - SpraySpeedLo) / (SpraySpeedHi - SpraySpeedLo), 0.0f, 1.0f);
	}
	else if (Surface == EODFxSurface::Loose)
	{
		const float Stir = SpeedMs + 2.0f * SlipMs;
		R.Dust = DustMax * FMath::Clamp((Stir - DustStirLo) / (DustStirHi - DustStirLo), 0.0f, 1.0f)
			* (1.0f - DustWetCut * Wet);
	}
	return R;
}

void UODVehicleFX::BeginPlay()
{
	Super::BeginPlay();
	if (!ODVisualStyle::IsRealistic())
	{
		SetComponentTickEnabled(false);
		return;
	}
	UStaticMesh* Sphere = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Sphere.Sphere"));
	UMaterialInterface* Mat = LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Realistic/FX/M_FXPuff.M_FXPuff"));
	AActor* Owner = GetOwner();
	if (!Sphere || !Mat || !Owner || !Owner->GetRootComponent())
	{
		UE_LOG(LogTemp, Warning, TEXT("ODVehicleFX: M_FXPuff or Sphere missing (unreal/build_vehicle_fx_material.py) - no vehicle effects"));
		return;   // keep ticking: PushCarState still feeds the car materials
	}

	Pool = NewObject<UInstancedStaticMeshComponent>(Owner, TEXT("VehicleFXPool"));
	Pool->SetMobility(EComponentMobility::Movable);
	Pool->SetStaticMesh(Sphere);
	Pool->SetMaterial(0, Mat);
	Pool->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	Pool->SetCanEverAffectNavigation(false);
	Pool->SetCastShadow(false);
	Pool->SetAffectDynamicIndirectLighting(false);
	// world-anchored: Draw() moves it to the car and writes puffs relative to it
	Pool->SetUsingAbsoluteLocation(true);
	Pool->SetUsingAbsoluteRotation(true);
	Pool->SetUsingAbsoluteScale(true);
	Pool->SetupAttachment(Owner->GetRootComponent());
	Pool->RegisterComponent();
	Pool->SetNumCustomDataFloats(NumCustom);

	Puffs.SetNum(PoolSize);
	TArray<FTransform> Hidden;
	Hidden.Init(FTransform(FQuat::Identity, FVector::ZeroVector, FVector::ZeroVector), PoolSize);
	Pool->AddInstances(Hidden, false, false, false);
	UE_LOG(LogTemp, Display, TEXT("ODVehicleFX: on, pool %d"), PoolSize);
}

void UODVehicleFX::Integrate(FPuff& P, float Dt) const
{
	// exact solution of v' = a - k v over Dt, so motion doesn't depend on
	// how the time is cut into frames
	const FKind& K = Kinds[P.Kind];
	const float E = FMath::Exp(-K.Drag * Dt);
	const FVector VInf = K.Accel / K.Drag;
	P.Pos += VInf * Dt + (P.Vel - VInf) * ((1.0f - E) / K.Drag);
	P.Vel = VInf + (P.Vel - VInf) * E;
}

void UODVehicleFX::Spawn(int32 Kind, const FVector& Contact, const FVector& CarVel,
	const FVector& Fwd, float Tau)
{
	const FKind& K = Kinds[Kind];
	FPuff& P = Puffs[Next];
	Next = (Next + 1) % Puffs.Num();

	FVector Rand = Rng.VRand() * (K.Spread * Rng.FRandRange(0.3f, 1.0f));
	Rand.Z = FMath::Abs(Rand.Z);
	P.Kind = (uint8)Kind;
	P.Life = K.Life * Rng.FRandRange(0.8f, 1.2f);
	const float SizeJitter = Rng.FRandRange(0.8f, 1.2f);
	P.Size0 = K.Size0 * SizeJitter;
	P.Size1 = K.Size1 * SizeJitter;
	P.Vel = CarVel * K.Inherit + Rand + FVector(0.0f, 0.0f, K.Kick);
	// just behind the patch; the patch was CarVel x Tau back when this puff was due
	P.Pos = Contact - Fwd * 20.0f + FVector(0.0f, 0.0f, 15.0f) - CarVel * Tau;
	Integrate(P, Tau);
	P.Age = Tau;
}

void UODVehicleFX::TickComponent(float DeltaTime, ELevelTick TickType,
	FActorComponentTickFunction* ThisTickFunction)
{
	Super::TickComponent(DeltaTime, TickType, ThisTickFunction);
	PushCarState(DeltaTime);
	AActor* Owner = GetOwner();
	if (!Pool || !Owner || DeltaTime <= 0.0f)
	{
		return;
	}

	bool bAnyLive = false;
	for (FPuff& P : Puffs)
	{
		if (P.Life <= 0.0f)
		{
			continue;
		}
		P.Age += DeltaTime;
		if (P.Age >= P.Life)
		{
			P.Life = 0.0f;
			continue;
		}
		Integrate(P, DeltaTime);
		bAnyLive = true;
	}

	// Spray needs water ON the road (2026-10-16): a drying, merely damp
	// surface throws little; falling rain or standing puddles throw the full
	// rooster tail, and loose road snow kicks up a white plume the same way.
	const AODWeatherController* Wx = WeatherCtl.Get();
	const float Wet = Wx ? FMath::Max3(Wx->Wetness * (0.4f + 0.6f * FMath::Min(Wx->RainRate * 2.0f, 1.0f)),
		Wx->Puddles, FMath::Min(Wx->RoadSnowCm / 3.0f, 1.0f)) : 0.0f;

	const UODVehicleMovement* Move = Owner->FindComponentByClass<UODVehicleMovement>();
	const UPrimitiveComponent* Body = Cast<UPrimitiveComponent>(Owner->GetRootComponent());
	// physics off = garage/showroom, where the sim's telemetry is frozen
	if (Move && Body && Body->IsSimulatingPhysics())
	{
		const int32 NumWheels = Move->NumWheels();
		if (Debt.Num() != NumWheels * 3)
		{
			Debt.Init(0.0f, NumWheels * 3);
		}
		const FTransform BodyXf = Body->GetComponentTransform();
		const FVector Up = BodyXf.GetUnitAxis(EAxis::Z);
		for (int32 i = 0; i < NumWheels; ++i)
		{
			if (!Move->IsWheelInContact(i))
			{
				continue;
			}
			const float RadiusCm = Move->GetWheelRadius(i);
			const FVector Contact = BodyXf.TransformPosition(Move->GetWheelLocalPos(i)) - Up * RadiusCm;
			const FVector Fwd = BodyXf.TransformVectorNoScale(FRotator(0.0f, Move->GetWheelSteerDeg(i), 0.0f).Vector());
			const FVector Right = FVector::CrossProduct(Up, Fwd);
			const FVector V = Body->GetPhysicsLinearVelocityAtPoint(Contact);
			// contact-patch sliding speed: tire surface vs ground, both axes
			const float SlipMs = (float)FVector2D(Move->GetWheelOmega(i) * RadiusCm - FVector::DotProduct(V, Fwd),
				FVector::DotProduct(V, Right)).Size() / 100.0f;
			const FODFxRates R = Rates(Classify(Move->GetContactSurface(i)), SlipMs, (float)V.Size() / 100.0f, Wet);
			const float PerKind[3] = { R.Smoke, R.Dust, R.Spray };
			for (int32 k = 0; k < 3; ++k)
			{
				if (PerKind[k] <= 0.0f)
				{
					continue;
				}
				float& D = Debt[i * 3 + k];
				D += PerKind[k] * DeltaTime;
				for (int32 Budget = Puffs.Num(); D >= 1.0f && Budget > 0; --Budget)
				{
					D -= 1.0f;
					Spawn(k, Contact, V, Fwd, D / PerKind[k]);   // D / rate = time since this puff was due
					bAnyLive = true;
				}
				D = FMath::Min(D, 1.0f);   // a hitch past the pool size drops the excess
			}
		}
	}

	Draw(bAnyLive);
}

void UODVehicleFX::PushCarState(float Dt)
{
	AActor* Owner = GetOwner();
	UWorld* World = GetWorld();
	const UODVehicleMovement* Move = Owner ? Owner->FindComponentByClass<UODVehicleMovement>() : nullptr;
	if (!Move || !World || Move->NumWheels() < 3)
	{
		return;
	}
	if (!WeatherCtl.IsValid() && World->GetTimeSeconds() - LastWeatherSearch > 2.0)
	{
		LastWeatherSearch = World->GetTimeSeconds();
		for (TActorIterator<AODWeatherController> It(World); It; ++It) { WeatherCtl = *It; break; }
	}
	const AODWeatherController* Wx = WeatherCtl.Get();
	// snow on the car is its own load, not the ground's cover (a driven car
	// in a white landscape is clean, Taylor 2026-10-16): full after ~4 min of
	// heavy snowfall, sheds above ~30 km/h (half in ~30 s at 100 km/h), melts
	// over ~10 min above freezing. Exponential steps, so frame-rate independent.
	if (Dt > 0.0f && Wx)
	{
		const float Fall = Wx->IsSheltered() ? 0.0f : Wx->PrecipIntensity * Wx->PrecipType;   // covered: keeps its load, no new snow
		const float SpeedMs = Owner->GetVelocity().Size() / 100.0f;
		const float Off = FMath::Max(SpeedMs - 8.0f, 0.0f) / 900.0f + (Wx->AirTempC > 0.5f ? 1.0f / 600.0f : 0.0f);
		const float On = Fall / 240.0f;
		const float Target = On / FMath::Max(On + Off, 1e-6f);
		CarSnow = Target + (CarSnow - Target) * FMath::Exp(-(On + Off) * Dt);
	}
	const float Snow = CarSnow;
	const bool bOut = !(Wx && Wx->IsIndoors());   // the garage shows the car clean and dry
	const float State[6] = { bOut ? Move->GetDirt() : 0.0f, Wx && bOut ? Wx->Wetness : 0.0f, bOut ? Snow : 0.0f,
		Move->GetWheelAttachXY(0).X, Move->GetWheelAttachXY(2).X, Move->GetWheelRadius(0) };

	TInlineComponentArray<UStaticMeshComponent*> Meshes(Owner);
	for (UStaticMeshComponent* M : Meshes)
	{
		if (M == Pool)
		{
			continue;
		}
		const int32 N = M == Owner->GetRootComponent() ? 6 : 3;   // the chassis also gets its axles
		const TArray<float>& Cur = M->GetCustomPrimitiveData().Data;
		bool bChanged = Cur.Num() < N;
		for (int32 i = 0; i < N && !bChanged; ++i)
		{
			bChanged = FMath::Abs(Cur[i] - State[i]) > 0.002f;   // each write re-sends render state
		}
		for (int32 i = 0; bChanged && i < N; ++i)
		{
			M->SetCustomPrimitiveDataFloat(i, State[i]);
		}
	}
}

void UODVehicleFX::Draw(bool bAnyLive)
{
	if (!bAnyLive && !bDrawnLive)
	{
		return;   // nothing on screen and nothing to clear
	}
	bDrawnLive = bAnyLive;

	const FVector Origin = GetOwner()->GetActorLocation();
	Pool->SetWorldLocation(Origin);   // instances are relative: float precision far from the world origin
	const int32 N = Puffs.Num();
	Xf.SetNum(N);
	Custom.SetNum(N * NumCustom);
	for (int32 i = 0; i < N; ++i)
	{
		const FPuff& P = Puffs[i];
		float* C = &Custom[i * NumCustom];
		if (P.Life <= 0.0f)
		{
			Xf[i] = FTransform(FQuat::Identity, FVector::ZeroVector, FVector::ZeroVector);
			C[3] = 0.0f;
			continue;
		}
		const FKind& K = Kinds[P.Kind];
		const float T = P.Age / P.Life;
		const float Size = FMath::Lerp(P.Size0, P.Size1, 1.0f - FMath::Square(1.0f - T));   // grows fast, then slows
		Xf[i] = FTransform(FQuat::Identity, P.Pos - Origin, FVector(Size / 100.0f));   // Sphere is 100 cm
		C[0] = K.Color.R;
		C[1] = K.Color.G;
		C[2] = K.Color.B;
		C[3] = K.Alpha * FMath::Min(T * 8.0f, 1.0f) * FMath::Square(1.0f - T);   // quick fade-in, slow fade-out
	}
	// ponytail: ISM instances aren't depth-sorted against each other; fine
	// for soft low-alpha puffs, a sorted sprite renderer if it ever shows.
	Pool->BatchUpdateInstancesTransforms(0, Xf, false, false, true);
	Pool->SetCustomData(0, N - 1, Custom, true);
}
