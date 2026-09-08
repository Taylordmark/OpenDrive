#include "ODSeasonController.h"

#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/MaterialInterface.h"
#include "Math/RandomStream.h"
#include "Misc/CommandLine.h"
#include "ODForestManager.h"
#include "ODTerrainManager.h"
#include "ODWaterManager.h"

namespace
{
	// ---- ground snow-line sweep (2026-09-07) ----------------------------
	// Baked defaults from unreal/apply_ground_material.py for the active area
	// (summit_county, ORIGIN_HEIGHT_M=3435.3): SNOW_START_M=3500,
	// SNOW_FULL_M=3950 -> (m - ORIGIN_HEIGHT_M) * 100. Duplicated here as
	// plain constants (no python/JSON read at runtime) - matches this
	// project's existing convention of C++ owning its own copy of pipeline
	// constants it needs (e.g. AODTerrainManager's own carve-radius
	// constants), with a comment tying the two together so a future area
	// swap is at least discoverable from either side.
	constexpr float SnowLineLowBaseCm = 6470.0f;
	constexpr float SnowLineHighBaseCm = 51470.0f;

	// Floor band the snow line sweeps down to at full winter (SnowCover=1) -
	// same 45000 cm band WIDTH as the baseline (so the material's own 40 m
	// noise patchiness looks the same width sweeping through as it does
	// sitting at the real treeline), positioned low enough that summit_county's
	// real valley floor (Dillon Reservoir, ~2765 m ASL -> world Z ~= -67030 cm)
	// sits comfortably above SnowLineHighFloorCm, i.e. fully inside the "100%
	// covered" band, not partway up the noise-broken transition.
	constexpr float SnowLineLowFloorCm = -125000.0f;
	constexpr float SnowLineHighFloorCm = -80000.0f;

	// "Light" continuous snowfall level held through the whole winter hold -
	// spec: "snowing during fall->winter transition, light within winter, off
	// elsewhere".
	constexpr float WinterLightSnowfall = 0.2f;

	float SmoothStep01(float T)
	{
		T = FMath::Clamp(T, 0.0f, 1.0f);
		return T * T * (3.0f - 2.0f * T);
	}
}

AODSeasonController::AODSeasonController()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODSeasonController::BeginPlay()
{
	Super::BeginPlay();

	FindManagers();
	EnsureSnowfallComponent();

	// ---- QA freeze flags (2026-09-07) ------------------------------------
	// -odseason=<summer|fall|winter|spring>: jump to that season's stable
	// midpoint and FREEZE the clock there (no further ticking advance).
	// -odseasonphase=<0..1> (only meaningful paired with -odseason=): instead
	// freeze mid-TRANSITION *into the next season* at that progress - e.g.
	// -odseason=fall -odseasonphase=0.25 freezes 25% of the way through the
	// fall->winter transition, not 25% into fall's own hold.
	// -odseasonrate=N: time-scale for watching transitions play out live.
	FString SeasonName;
	if (FParse::Value(FCommandLine::Get(), TEXT("odseason="), SeasonName))
	{
		const float HoldStart = SeasonHoldStartSeconds(SeasonName);
		float Phase = -1.0f;
		if (FParse::Value(FCommandLine::Get(), TEXT("odseasonphase="), Phase) && Phase >= 0.0f)
		{
			const float TransitionStart = HoldStart + HoldSeconds;
			ClockSeconds = FMath::Fmod(TransitionStart + FMath::Clamp(Phase, 0.0f, 1.0f) * TransitionSeconds,
				LoopSeconds());
		}
		else
		{
			ClockSeconds = FMath::Fmod(HoldStart + HoldSeconds * 0.5f, LoopSeconds());
		}
		bFrozen = true;
		UE_LOG(LogTemp, Display,
			TEXT("ODSeasonController: QA freeze -odseason=%s%s -> ClockSeconds=%.1f"),
			*SeasonName, Phase >= 0.0f ? *FString::Printf(TEXT(" -odseasonphase=%.2f"), Phase) : TEXT(""),
			ClockSeconds);
	}

	float Rate = TimeScale;
	if (FParse::Value(FCommandLine::Get(), TEXT("odseasonrate="), Rate))
	{
		TimeScale = Rate;
	}

	ComputeParamsForClock(ClockSeconds, FallBlend, SnowCover, SnowfallIntensity, FreezeBlend, SpringGreen);
	PushParamsToMIDs();
}

float AODSeasonController::SeasonHoldStartSeconds(const FString& SeasonName) const
{
	const FString S = SeasonName.ToLower();
	if (S == TEXT("summer")) return 0.0f;
	if (S == TEXT("fall") || S == TEXT("autumn")) return HoldSeconds + TransitionSeconds;
	if (S == TEXT("winter")) return 2.0f * (HoldSeconds + TransitionSeconds);
	if (S == TEXT("spring")) return 3.0f * (HoldSeconds + TransitionSeconds);
	UE_LOG(LogTemp, Warning,
		TEXT("ODSeasonController: -odseason=%s not recognized (expected summer/fall/winter/spring) - "
		     "defaulting to summer"), *SeasonName);
	return 0.0f;
}

void AODSeasonController::FindManagers()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}
	for (TActorIterator<AODTerrainManager> It(World); It; ++It) { TerrainMgr = *It; break; }
	for (TActorIterator<AODWaterManager> It(World); It; ++It) { WaterMgr = *It; break; }
	for (TActorIterator<AODForestManager> It(World); It; ++It) { ForestMgr = *It; break; }
	UE_LOG(LogTemp, Display,
		TEXT("ODSeasonController: managers found - terrain=%d water=%d forest=%d"),
		TerrainMgr.IsValid(), WaterMgr.IsValid(), ForestMgr.IsValid());
}

void AODSeasonController::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	// Late-bind managers: AODGameMode's Ensure* spawns run in BeginPlay order
	// and this controller is spawned last (see EnsureSeasonController), but
	// tolerate a missing manager gracefully either way (open-world levels
	// only, per the class comment) rather than requiring exact spawn order.
	if (!TerrainMgr.IsValid() || !WaterMgr.IsValid() || !ForestMgr.IsValid())
	{
		FindManagers();
	}

	if (!bFrozen)
	{
		ClockSeconds = FMath::Fmod(ClockSeconds + DeltaSeconds * TimeScale, LoopSeconds());
		ComputeParamsForClock(ClockSeconds, FallBlend, SnowCover, SnowfallIntensity, FreezeBlend, SpringGreen);
	}

	PushAccumSeconds += DeltaSeconds;
	const float PushInterval = ParamPushHz > 0.0f ? 1.0f / ParamPushHz : 0.0f;
	if (PushAccumSeconds >= PushInterval)
	{
		PushAccumSeconds = 0.0f;
		PushParamsToMIDs();
	}

	// Falling-snow motion is animated every tick (cheap - a few hundred
	// instances), independent of the throttled material-param push above.
	UpdateSnowfallFX(DeltaSeconds);
}

void AODSeasonController::ComputeParamsForClock(float Clock, float& OutFall, float& OutSnow,
	float& OutSnowfall, float& OutFreeze, float& OutSpringGreen) const
{
	const float S0 = 0.0f;
	const float S1 = HoldSeconds;                       // summer hold end / summer->fall transition start
	const float S2 = S1 + TransitionSeconds;             // fall hold start
	const float S3 = S2 + HoldSeconds;                   // fall hold end / fall->winter transition start
	const float S4 = S3 + TransitionSeconds;             // winter hold start
	const float S5 = S4 + HoldSeconds;                   // winter hold end / winter->spring transition start
	const float S6 = S5 + TransitionSeconds;             // spring hold start
	const float S7 = S6 + HoldSeconds;                   // spring hold end / spring->summer transition start
	// S7 + TransitionSeconds == LoopSeconds() (== S0 of the next lap)

	OutFall = 0.0f; OutSnow = 0.0f; OutSnowfall = 0.0f; OutFreeze = 0.0f; OutSpringGreen = 0.0f;

	if (Clock < S1)
	{
		// summer hold: everything at baseline.
	}
	else if (Clock < S2)
	{
		// summer -> fall transition
		const float T = SmoothStep01((Clock - S1) / TransitionSeconds);
		OutFall = T;
	}
	else if (Clock < S3)
	{
		// fall hold
		OutFall = 1.0f;
	}
	else if (Clock < S4)
	{
		// fall -> winter transition: leaves fade out (deciduous arc lands
		// later, this is just a graceful FallBlend handoff so the exposed
		// param doesn't hang at 1.0 forever), snow grows in, lake starts to
		// skin over, snow starts falling.
		const float T = SmoothStep01((Clock - S3) / TransitionSeconds);
		OutFall = 1.0f - T;
		OutSnow = T;
		OutFreeze = T;
		OutSnowfall = T;   // ramps 0->1 as winter arrives
	}
	else if (Clock < S5)
	{
		// winter hold: closed snow cover, frozen lake, light continuous snow.
		OutSnow = 1.0f;
		OutFreeze = 1.0f;
		OutSnowfall = WinterLightSnowfall;
	}
	else if (Clock < S6)
	{
		// winter -> spring transition: reverse patchy retreat + thaw, snowfall
		// tapers off with the melt, fresh-green ramps in.
		const float T = SmoothStep01((Clock - S5) / TransitionSeconds);
		OutSnow = 1.0f - T;
		OutFreeze = 1.0f - T;
		OutSnowfall = WinterLightSnowfall * (1.0f - T);
		OutSpringGreen = T;
	}
	else if (Clock < S7)
	{
		// spring hold: melted, fresh-green easing back to summer's baseline
		// over the course of the hold.
		const float T = SmoothStep01((Clock - S6) / HoldSeconds);
		OutSpringGreen = 1.0f - T;
	}
	// else spring -> summer transition: everything already at/approaching
	// baseline (SpringGreen reaches 0 by S7 given the decay above), left at
	// the OutX defaults set above.
}

void AODSeasonController::PushParamsToMIDs()
{
	if (AODTerrainManager* Terrain = TerrainMgr.Get())
	{
		if (UMaterialInstanceDynamic* GroundMID = Terrain->GetGroundMID())
		{
			const float Low = FMath::Lerp(SnowLineLowBaseCm, SnowLineLowFloorCm, SnowCover);
			const float High = FMath::Lerp(SnowLineHighBaseCm, SnowLineHighFloorCm, SnowCover);
			GroundMID->SetScalarParameterValue(TEXT("SnowLineLowZCm"), Low);
			GroundMID->SetScalarParameterValue(TEXT("SnowLineHighZCm"), High);
			GroundMID->SetScalarParameterValue(TEXT("SpringGreen"), SpringGreen);
		}
	}
	if (AODWaterManager* Water = WaterMgr.Get())
	{
		if (UMaterialInstanceDynamic* WaterMID = Water->GetWaterMID())
		{
			WaterMID->SetScalarParameterValue(TEXT("FreezeBlend"), FreezeBlend);
		}
	}
	if (AODForestManager* Forest = ForestMgr.Get())
	{
		if (UMaterialInstanceDynamic* FoliageMID = Forest->GetFoliageSeasonMID())
		{
			FoliageMID->SetScalarParameterValue(TEXT("SnowCover"), SnowCover);
			FoliageMID->SetScalarParameterValue(TEXT("FallBlend"), FallBlend);
		}
	}
}

void AODSeasonController::EnsureSnowfallComponent()
{
	UStaticMesh* SphereMesh = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Sphere.Sphere"));
	UMaterialInterface* SnowFxMat = LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_SnowFlake.M_SnowFlake"));
	if (!SphereMesh)
	{
		UE_LOG(LogTemp, Warning, TEXT("ODSeasonController: /Engine/BasicShapes/Sphere missing - no snowfall FX"));
		return;
	}

	SnowFxHism = NewObject<UHierarchicalInstancedStaticMeshComponent>(this);
	SnowFxHism->SetupAttachment(RootComponent);
	SnowFxHism->SetStaticMesh(SphereMesh);
	if (SnowFxMat)
	{
		SnowFxHism->SetMaterial(0, UMaterialInstanceDynamic::Create(SnowFxMat, this));
	}
	SnowFxHism->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	SnowFxHism->SetCastShadow(false);
	SnowFxHism->SetAffectDynamicIndirectLighting(false);
	SnowFxHism->SetVisibility(false);
	SnowFxHism->RegisterComponent();

	// A box of tiny flakes around wherever the effect is attached (the player
	// pawn, once one exists - see UpdateSnowfallFX). Fixed pool, seeded once;
	// only visibility + per-instance fall/drift motion change at runtime.
	//
	// 2026-09-07 tuning note: the first pass used a 5 cm scale over a 28 m x
	// 28 m x 18 m box (220 flakes) - technically animating (confirmed via a
	// pixel-diff against the zero-snowfall baseline showing real per-frame
	// motion), but at driving distance in a 1280x720 screenshot it read as
	// essentially invisible noise, not "visibly snowing." Bumped scale and
	// tightened the box (denser per unit volume) so light snowfall reads
	// clearly in a still frame without turning into a blizzard.
	constexpr int32 NumFlakes = 260;
	constexpr float HalfExtentXYCm = 900.0f;
	constexpr float HalfHeightCm = 700.0f;
	FRandomStream Rng(12345);
	SnowFxLocalOffsets.Reserve(NumFlakes);
	TArray<FTransform> Transforms;
	Transforms.Reserve(NumFlakes);
	for (int32 i = 0; i < NumFlakes; ++i)
	{
		const FVector Offset(
			Rng.FRandRange(-HalfExtentXYCm, HalfExtentXYCm),
			Rng.FRandRange(-HalfExtentXYCm, HalfExtentXYCm),
			Rng.FRandRange(-HalfHeightCm, HalfHeightCm));
		SnowFxLocalOffsets.Add(Offset);
		FTransform T;
		T.SetLocation(Offset);
		T.SetScale3D(FVector(0.11f));   // ~11 cm sphere - reads as a soft flake at driving distance
		Transforms.Add(T);
	}
	SnowFxHism->AddInstances(Transforms, false);
}

void AODSeasonController::UpdateSnowfallFX(float DeltaSeconds)
{
	if (!SnowFxHism)
	{
		return;
	}

	// Attach to the player pawn once one exists (camera-attached bounds, per
	// spec) - open-world levels spawn AODVehiclePawn asynchronously relative
	// to this actor's own BeginPlay, so this is checked lazily rather than
	// assumed available up front.
	if (!bSnowFxAttached)
	{
		if (APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0))
		{
			SnowFxHism->AttachToComponent(Player->GetRootComponent(),
				FAttachmentTransformRules::KeepWorldTransform);
			SnowFxHism->SetRelativeLocation(FVector(0.0f, 0.0f, 300.0f));   // roughly windshield height and above
			bSnowFxAttached = true;
		}
	}

	const bool bVisible = SnowfallIntensity > 0.02f;
	if (SnowFxHism->IsVisible() != bVisible)
	{
		SnowFxHism->SetVisibility(bVisible);
	}
	if (!bVisible)
	{
		return;
	}

	constexpr float FallSpeedCmS = 260.0f;
	constexpr float DriftXCmS = 28.0f;
	constexpr float DriftYCmS = 14.0f;
	constexpr float HalfHeightCm = 700.0f;
	constexpr float HalfExtentXYCm = 900.0f;

	const int32 Num = SnowFxLocalOffsets.Num();
	for (int32 i = 0; i < Num; ++i)
	{
		FVector& Offset = SnowFxLocalOffsets[i];
		Offset.Z -= FallSpeedCmS * DeltaSeconds;
		Offset.X += DriftXCmS * DeltaSeconds;
		Offset.Y += DriftYCmS * DeltaSeconds;
		if (Offset.Z < -HalfHeightCm)
		{
			Offset.Z += 2.0f * HalfHeightCm;
		}
		if (Offset.X > HalfExtentXYCm) Offset.X -= 2.0f * HalfExtentXYCm;
		if (Offset.Y > HalfExtentXYCm) Offset.Y -= 2.0f * HalfExtentXYCm;

		FTransform T;
		T.SetLocation(Offset);
		T.SetScale3D(FVector(0.11f));
		SnowFxHism->UpdateInstanceTransform(i, T, false, i == Num - 1, true);
	}
}
