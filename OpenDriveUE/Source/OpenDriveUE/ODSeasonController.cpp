#include "ODSeasonController.h"
#include "UObject/Package.h" // ODVisualStyle.h uses UPackage (non-unity builds)
#include "ODVisualStyle.h"

#include "Camera/PlayerCameraManager.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "GameFramework/Pawn.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/MaterialInterface.h"
#include "Math/RandomStream.h"
#include "Misc/CommandLine.h"
#include "UObject/UObjectIterator.h"
#include "ODBuildingManager.h"
#include "ODForestManager.h"
#include "ODRoadManager.h"
#include "ODTerrainManager.h"
#include "ODWaterManager.h"
#include "ODWeatherController.h"

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
	// 2026-10-15 (realism eval T1): these are now PER AREA - each terrain
	// manager's FODGroundBands (groundbands_<area>_test.json, pipeline/
	// build_ground_bands.py, climate-derived): SnowSummer* = the SnowCover 0
	// baseline ("Base" below), SnowWinter* = the SnowCover 1 floor ("Floor"
	// below). Oahu/Green Valley/SF get lines above their highest terrain, so
	// no snow in any season; summit's summer line is only patchy from ~3900 m.
	// The comments below still describe summit_county's old fixed values.

	// Floor band the snow line sweeps down to at full winter (SnowCover=1) -
	// same 45000 cm band WIDTH as the baseline (so the material's own 40 m
	// noise patchiness looks the same width sweeping through as it does
	// sitting at the real treeline), positioned low enough that summit_county's
	// real valley floor (Dillon Reservoir, ~2765 m ASL -> world Z ~= -67030 cm)
	// sits comfortably above SnowLineHighFloorCm, i.e. fully inside the "100%
	// covered" band, not partway up the noise-broken transition.

	// ---- 2026-09-07 polish pass: melt-compression fix -------------------
	// Bug found via -odseason=winter -odseasonphase=0.5: with SnowLineLowZCm/
	// HighZCm both swept LINEARLY in SnowCover from their baked baseline
	// straight to the floor above, the valley floor (world Z ~= -67030 cm,
	// same reference used to place the floor) only enters the ~45000 cm-wide
	// band's crossing window once SnowCover has already fallen to ~0.56 -
	// below that the whole visible valley reads pure baseline (raw altitude
	// term clamped to 0), and it re-saturates to fully-covered by ~0.90. That
	// squeezes the ENTIRE visible ground transition into SnowCover's narrow
	// [0.56, 0.90] sub-range, so a melt/accumulation phase that lands outside
	// it (e.g. -odseasonphase=0.5, SnowCover=0.5) shows either full green or
	// full white instead of the required patchwork - the reported defect.
	//
	// Fix: keep SnowLineHighZCm's simple baseline->floor lerp (unchanged -
	// it never rises above SnowLineHighBaseCm, so anything genuinely above
	// treeline - e.g. Quandary Peak - always stays clamped to fully-covered
	// throughout the sweep, no regression there). Replace ONLY the LOW edge's
	// mapping with one solved so that, evaluated at SnowValleyRefZCm, the
	// raw altitude coverage term comes out equal to SnowCover itself (an
	// identity ramp) across the middle of the sweep - i.e. SnowCover=0.25
	// visibly reads as ~25% ground coverage at the vantage, 0.5 as a genuine
	// half-and-half patchwork, 0.75 as ~75%, instead of being compressed into
	// a third of the range. Solved from snow_t_raw's own formula in
	// apply_ground_material.py: clamp01((z - Low) / (High - Low)) = SnowCover
	// => Low = (z - SnowCover * High) / (1 - SnowCover), evaluated with the
	// SAME High(SnowCover) lerp already used below.
	//
	// The identity solve has two structural edge cases, both handled by
	// blending it away over a narrow margin rather than using it raw:
	//  - Near SnowCover=0 it demands Low jump instantly from
	//    SnowLineLowBaseCm to a value far lower (the gap between the baked
	//    treeline snowline and RefZ) - an actual pop, which is exactly what
	//    this fix exists to remove. Blended back to SnowLineLowBaseCm at
	//    SnowCover=0 over [0, SnowMeltBlendInEnd] via smoothstep so the value
	//    is continuous and starts exactly at the baked default (byte-identical
	//    SnowCover=0 baseline preserved).
	//  - As SnowCover approaches the point where High(SnowCover) itself
	//    equals RefZ, the identity solve's implied band width collapses
	//    toward zero (High-Low -> 0), which would read as a hard cliff right
	//    at that SnowCover value. Blended over to the original fixed floor
	//    (SnowLineLowFloorCm - already proven safe/complete-coverage at
	//    SnowCover=1) starting at SnowMeltBlendOutStart, comfortably before
	//    RefZ values in the plausible on-road range ever reach the collapse
	//    point (High(0.85) = -60280 cm; RefZ would have to sit below that -
	//    i.e. below the winter floor's own high edge - to still be
	//    mid-collapse there, which no drivable road surface does).
	//
	// RefZ ITSELF is NOT a hardcoded constant (a 2026-09-07 same-day fix -
	// see the polish-pass QA notes): the first version of this fix pinned it
	// to SnowValleyRefZFallbackCm (Dillon Reservoir's lake-bed Z), which is
	// only correct for a camera actually standing at lake level. Dillon Dam
	// Road itself runs along the dam crest, well above the lake bed, so the
	// embankment visible at that (and every other) QA vantage sat far enough
	// above the lake-bed reference that the identity solve's crossing window
	// was still off-screen for most of the sweep - the SAME compression bug,
	// just re-triggered by a wrong reference elevation instead of a wrong
	// travel distance. Fix: read the actual player pawn's world Z each push
	// and use THAT as RefZ, so the crossing window is always centered on
	// wherever the camera/car actually is, at every vantage, not just the one
	// this pass happened to be tuned against. Falls back to the lake-bed
	// constant only if no pawn exists yet (e.g. the very first push before
	// the vehicle has spawned).
	constexpr float SnowValleyRefZFallbackCm = -67030.0f;   // Dillon Reservoir lake-bed world Z (~2765 m ASL) - BeginPlay-time fallback only, see above.
	constexpr float SnowMeltBlendInEnd = 0.15f;     // below this, blend from SnowLineLowBaseCm into the identity curve
	constexpr float SnowMeltBlendOutStart = 0.85f;  // above this, blend from the identity curve down to SnowLineLowFloorCm (well short of the collapse point)

	float SnowLineHighForCover(const FODGroundBands& B, float SnowCover)
	{
		return FMath::Lerp(B.SnowSummerHighCm, B.SnowWinterHighCm, SnowCover);
	}

	// Identity-ramp solve (see comment block above) - valid away from the
	// SnowCover=1 singularity, which the caller keeps clear of via
	// SnowMeltBlendOutStart.
	float SnowLineLowIdentity(const FODGroundBands& B, float SnowCover, float RefZ)
	{
		const float High = SnowLineHighForCover(B, SnowCover);
		const float Denom = FMath::Max(1.0f - SnowCover, 0.01f);
		return (RefZ - SnowCover * High) / Denom;
	}

	float SmoothStep01(float T)
	{
		T = FMath::Clamp(T, 0.0f, 1.0f);
		return T * T * (3.0f - 2.0f * T);
	}

	// Piecewise SnowLineLowZCm(SnowCover) - see the "melt-compression fix"
	// comment block above: identity ramp in the middle, smoothly blended back
	// to the exact baked baseline at SnowCover=0 and to the exact proven-safe
	// floor at SnowCover=1 so both endpoints stay byte-identical to before.
	float SnowLineLowIdentityRamp(const FODGroundBands& B, float SnowCover, float RefZ)
	{
		if (SnowCover <= SnowMeltBlendInEnd)
		{
			const float W = SmoothStep01(SnowCover / SnowMeltBlendInEnd);
			return FMath::Lerp(B.SnowSummerLowCm, SnowLineLowIdentity(B, SnowMeltBlendInEnd, RefZ), W);
		}
		if (SnowCover >= SnowMeltBlendOutStart)
		{
			const float W = SmoothStep01((SnowCover - SnowMeltBlendOutStart) / (1.0f - SnowMeltBlendOutStart));
			return FMath::Lerp(SnowLineLowIdentity(B, SnowMeltBlendOutStart, RefZ), B.SnowWinterLowCm, W);
		}
		return SnowLineLowIdentity(B, SnowCover, RefZ);
	}

	// The identity ramp forces coverage AT RefZ to equal SnowCover, which is
	// only right where full winter actually covers RefZ - on Oahu it would
	// paint 50% snow under the car mid-transition. So it's weighted by how
	// covered RefZ is at SnowCover 1; below the winter line it's a plain
	// summer->winter lerp (2026-10-15, per-area bands).
	float SnowLineLowForCover(const FODGroundBands& B, float SnowCover, float RefZ)
	{
		const float WinterAtRef = FMath::Clamp((RefZ - B.SnowWinterLowCm)
			/ FMath::Max(B.SnowWinterHighCm - B.SnowWinterLowCm, 1.0f), 0.0f, 1.0f);
		return FMath::Lerp(FMath::Lerp(B.SnowSummerLowCm, B.SnowWinterLowCm, SnowCover),
			SnowLineLowIdentityRamp(B, SnowCover, RefZ), WinterAtRef);
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

	ComputeParamsForClock(ClockSeconds, FallBlend, SnowCover, FreezeBlend, SpringGreen, LeafDrop);
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

void AODSeasonController::SetSeasonLock(const FString& SeasonName)
{
	FString QASeason;
	if (FParse::Value(FCommandLine::Get(), TEXT("odseason="), QASeason))
	{
		return;
	}
	bFrozen = !SeasonName.IsEmpty();
	if (bFrozen)
	{
		ClockSeconds = FMath::Fmod(SeasonHoldStartSeconds(SeasonName) + HoldSeconds * 0.5f, LoopSeconds());
	}
	ComputeParamsForClock(ClockSeconds, FallBlend, SnowCover, FreezeBlend, SpringGreen, LeafDrop);
	PushParamsToMIDs();
	// Weather rolls within the picked season - re-roll now, not at the next
	// hold end (the garage can call this before our first Tick bound it).
	if (!WeatherCtl.IsValid())
	{
		FindManagers();
	}
	if (AODWeatherController* Weather = WeatherCtl.Get())
	{
		Weather->Reroll();
	}
	UE_LOG(LogTemp, Display, TEXT("ODSeasonController: garage season lock '%s' -> frozen=%d ClockSeconds=%.1f"),
		SeasonName.IsEmpty() ? TEXT("Auto") : *SeasonName, bFrozen, ClockSeconds);
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
	for (TActorIterator<AODWeatherController> It(World); It; ++It) { WeatherCtl = *It; break; }
	for (TActorIterator<AODRoadManager> It(World); It; ++It) { RoadMgr = *It; break; }
	UE_LOG(LogTemp, Display,
		TEXT("ODSeasonController: managers found - terrain=%d water=%d forest=%d weather=%d"),
		TerrainMgr.IsValid(), WaterMgr.IsValid(), ForestMgr.IsValid(), WeatherCtl.IsValid());
}

void AODSeasonController::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	// Late-bind managers: AODGameMode's Ensure* spawns run in BeginPlay order
	// and this controller is spawned last (see EnsureSeasonController), but
	// tolerate a missing manager gracefully either way (open-world levels
	// only, per the class comment) rather than requiring exact spawn order.
	if (!TerrainMgr.IsValid() || !WaterMgr.IsValid() || !ForestMgr.IsValid() || !WeatherCtl.IsValid())
	{
		FindManagers();
	}

	if (!bFrozen)
	{
		ClockSeconds = FMath::Fmod(ClockSeconds + DeltaSeconds * TimeScale, LoopSeconds());
		ComputeParamsForClock(ClockSeconds, FallBlend, SnowCover, FreezeBlend, SpringGreen, LeafDrop);
	}

	// Falling snow is weather-owned (Weather W1, next steps.md sec 7.39):
	// snowfall = PrecipIntensity * PrecipType, so rain never shows flakes and
	// sleet shows half. The clock keeps ground cover/freeze/green-up.
	const AODWeatherController* Weather = WeatherCtl.Get();
	SnowfallIntensity = Weather ? Weather->PrecipIntensity * Weather->PrecipType : 0.0f;

	PushAccumSeconds += DeltaSeconds;
	const float PushInterval = ParamPushHz > 0.0f ? 1.0f / ParamPushHz : 0.0f;
	if (PushAccumSeconds >= PushInterval)
	{
		PushAccumSeconds = PushInterval > 0.0f ? FMath::Fmod(PushAccumSeconds, PushInterval) : 0.0f;   // keep the remainder: ParamPushHz at any fps
		PushParamsToMIDs();
	}

	// Falling-snow motion is animated every tick (cheap - a few hundred
	// instances), independent of the throttled material-param push above.
	UpdateSnowfallFX(DeltaSeconds);
}

void AODSeasonController::ComputeParamsForClock(float Clock, float& OutFall, float& OutSnow,
	float& OutFreeze, float& OutSpringGreen, float& OutLeafDrop) const
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

	OutFall = 0.0f; OutSnow = 0.0f; OutFreeze = 0.0f; OutSpringGreen = 0.0f;
	OutLeafDrop = 0.0f;

	if (Clock < S1)
	{
		// summer hold: everything at baseline.
	}
	else if (Clock < S2)
	{
		// summer -> fall transition: deciduous canopies patch-turn orange
		// (see M_Foliage's FallBlend wiring in build_flora_materials.py).
		const float T = SmoothStep01((Clock - S1) / TransitionSeconds);
		OutFall = T;
	}
	else if (Clock < S3)
	{
		// fall hold: fully orange, canopy still full (no drop yet).
		OutFall = 1.0f;
	}
	else if (Clock < S4)
	{
		// fall -> winter transition (deciduous arc, 2026-09 follow-up):
		// leaves that remain STAY orange (FallBlend held at 1 - they don't
		// fade back toward green on the way out, they fall off orange) while
		// LeafDrop ramps 0->1, progressively dissolving the canopy cards
		// down to bare twig geometry (see M_Foliage's LeafDrop wiring).
		// Snow grows in, lake starts to skin over, and the same SnowCover
		// ramp also builds "bunches of snow" onto the now-exposed twigs via
		// the deciduous bark MID. (Falling snow is weather's, see Tick.)
		const float T = SmoothStep01((Clock - S3) / TransitionSeconds);
		OutFall = 1.0f;
		OutLeafDrop = T;
		OutSnow = T;
		OutFreeze = T;
	}
	else if (Clock < S5)
	{
		// winter hold: closed snow cover, frozen lake, deciduous canopies
		// fully bare (LeafDrop=1) and snow-capped.
		OutFall = 1.0f;
		OutLeafDrop = 1.0f;
		OutSnow = 1.0f;
		OutFreeze = 1.0f;
	}
	else if (Clock < S6)
	{
		// winter -> spring transition: reverse patchy retreat + thaw,
		// fresh-green ramps in. Deciduous canopies
		// reverse-dissolve back in (LeafDrop 1->0) as fresh GREEN growth, not
		// the fall orange - FallBlend eases back to 0 over the same span so
		// the re-budding leaves render green under SpringGreen's fresh tint.
		const float T = SmoothStep01((Clock - S5) / TransitionSeconds);
		OutSnow = 1.0f - T;
		OutFreeze = 1.0f - T;
		OutSpringGreen = T;
		OutFall = 1.0f - T;
		OutLeafDrop = 1.0f - T;
	}
	else if (Clock < S7)
	{
		// spring hold: melted, fresh-green easing back to summer's baseline
		// over the course of the hold. Deciduous canopy already fully
		// re-leafed (LeafDrop=0) and green (FallBlend=0) by S6.
		const float T = SmoothStep01((Clock - S6) / HoldSeconds);
		OutSpringGreen = 1.0f - T;
	}
	// else spring -> summer transition: everything already at/approaching
	// baseline (SpringGreen reaches 0 by S7 given the decay above), left at
	// the OutX defaults set above.
}

void AODSeasonController::PushParamsToMIDs()
{
	// Pushes to EVERY terrain/water/forest/road manager, not just the cached
	// first-found ones (2026-10-05, large world: AODGameMode spawns whole area
	// sets mid-drive, each with its own MIDs). The cached weak ptrs remain
	// FindManagers()' "anything to bind yet" check.
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}
	// Fresh weather snow (2026-10-06) on top of the season snow line: the
	// roadside depth reaches full ground/foliage cover at 15 cm. Grip keeps
	// reading the season SnowCover member (road depth has its own term).
	const AODWeatherController* Wx = WeatherCtl.Get();
	const float Cover = FMath::Max(SnowCover, Wx ? FMath::Clamp(Wx->SideSnowCm / 15.0f, 0.0f, 1.0f) : 0.0f);
	// Wet weather (PLAN-REALISM Phase 6, 2026-10-09): the road's Wetness also
	// darkens/glosses the ground and the forest MIDs ("Wetness" in
	// apply_ground_material.py / build_flora_materials.py, 0 = dry).
	const float Wet = Wx ? Wx->Wetness : 0.0f;
	// 2026-10-16: rain rate (ripples) and standing water (puddles fill over
	// minutes, drain slower than the surface dries) - AODWeatherController.
	const float Rain = Wx ? Wx->RainRate : 0.0f;
	const float Puddles = Wx ? Wx->Puddles : 0.0f;
	for (TActorIterator<AODTerrainManager> It(World); It; ++It)
	{
		AODTerrainManager* Terrain = *It;
		if (UMaterialInstanceDynamic* GroundMID = Terrain->GetGroundMID())
		{
			// Reference elevation for the melt-compression fix's identity
			// ramp: the player pawn's own current world Z, not a fixed
			// guessed constant (see the big comment above SnowLineLowIdentity).
			// This keeps the visible altitude-band crossing centered on
			// wherever the camera/car actually is, at any QA vantage or any
			// real drive, instead of only working near one hardcoded
			// elevation. Falls back to the lake-bed constant before the
			// pawn exists yet (e.g. the very first param push at BeginPlay).
			float RefZ = SnowValleyRefZFallbackCm;
			if (APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0))
			{
				RefZ = Player->GetActorLocation().Z;
			}
			const FODGroundBands& Bands = Terrain->GetGroundBandsWorld();
			const float Low = SnowLineLowForCover(Bands, Cover, RefZ);
			const float High = SnowLineHighForCover(Bands, Cover);
			if (FParse::Param(FCommandLine::Get(), TEXT("odtrace")) && !bLoggedSnowDebugOnce)
			{
				bLoggedSnowDebugOnce = true;
				UE_LOG(LogTemp, Display,
					TEXT("ODSeasonController: [snowdbg] SnowCover=%.3f RefZ=%.1f Low=%.1f High=%.1f Band=%.1f"),
					SnowCover, RefZ, Low, High, High - Low);
			}
			GroundMID->SetScalarParameterValue(TEXT("SnowLineLowZCm"), Low);
			GroundMID->SetScalarParameterValue(TEXT("SnowLineHighZCm"), High);
			GroundMID->SetScalarParameterValue(TEXT("SpringGreen"), SpringGreen);
			GroundMID->SetScalarParameterValue(TEXT("Wetness"), Wet);
			GroundMID->SetScalarParameterValue(TEXT("Puddles"), Puddles);
		}
	}
	for (TActorIterator<AODWaterManager> It(World); It; ++It)
	{
		AODWaterManager* Water = *It;
		if (UMaterialInstanceDynamic* WaterMID = Water->GetWaterMID())
		{
			WaterMID->SetScalarParameterValue(TEXT("FreezeBlend"), FreezeBlend);
			WaterMID->SetScalarParameterValue(TEXT("SnowCover"), Cover);   // snow lying on the ice
			// Weather W5 (sec 7.42): wind-driven chop, 10 m/s calm .. 35 m/s
			// (Hurricane) full (apply_water_material.py's "WaveRoughness").
			const AODWeatherController* Weather = WeatherCtl.Get();
			WaterMID->SetScalarParameterValue(TEXT("WaveRoughness"),
				Weather ? FMath::Clamp((Weather->WindSpeedMs - 10.0f) / 25.0f, 0.0f, 1.0f) : 0.0f);
		}
	}
	for (TActorIterator<AODForestManager> It(World); It; ++It)
	{
		AODForestManager* Forest = *It;
		if (UMaterialInstanceDynamic* FoliageMID = Forest->GetFoliageSeasonMID())
		{
			FoliageMID->SetScalarParameterValue(TEXT("SnowCover"), Cover);
			FoliageMID->SetScalarParameterValue(TEXT("Wetness"), Wet);
			// FallBlend is deliberately NOT pushed here (2026-09 deciduous-arc
			// follow-up): this MID is the Conifer/evergreen species batch
			// (see ConiferSpeciesIndex in ODForestManager.cpp), and
			// build_flora_materials.py's fall-color wiring now actually
			// blends toward orange when FallBlend > 0 on WHATEVER MID it's
			// set on - the shared M_Foliage graph can't tell species apart,
			// only per-MID parameter values can. Leaving this MID's
			// FallBlend at its material default (0.0) is what keeps
			// conifers evergreen; only DeciduousFoliageMID below gets a
			// real FallBlend push. (Before this follow-up wired FallBlend
			// into the graph, pushing it here was a harmless no-op; it isn't
			// anymore.)
		}
		// Deciduous-arc follow-up (2026-09, next steps.md sec 7.17): the
		// Blob/tree_blob-only MIDs get FallBlend/LeafDrop as well as
		// SnowCover (canopy dissolve + twig snow) - the Conifer MID above
		// never receives LeafDrop, so evergreen canopies can't dissolve.
		if (UMaterialInstanceDynamic* DeciduousFoliageMID = Forest->GetDeciduousFoliageMID())
		{
			DeciduousFoliageMID->SetScalarParameterValue(TEXT("SnowCover"), Cover);
			DeciduousFoliageMID->SetScalarParameterValue(TEXT("FallBlend"), FallBlend);
			DeciduousFoliageMID->SetScalarParameterValue(TEXT("LeafDrop"), LeafDrop);
			DeciduousFoliageMID->SetScalarParameterValue(TEXT("Wetness"), Wet);
		}
		if (UMaterialInstanceDynamic* DeciduousBarkMID = Forest->GetDeciduousBarkMID())
		{
			DeciduousBarkMID->SetScalarParameterValue(TEXT("SnowCover"), Cover);
			DeciduousBarkMID->SetScalarParameterValue(TEXT("Wetness"), Wet);
		}
	}
	// Weather W2 (sec 7.40): wet road = darker + glossier (unreal/
	// apply_road_material.py's "Wetness" param, 0 = the dry baseline).
	bool bAnyRoadMgr = false;
	for (TActorIterator<AODRoadManager> It(World); It; ++It)
	{
		It->SetWetness(Wet, Rain, Puddles);
		bAnyRoadMgr = true;
	}
	if (!bAnyRoadMgr)
	{
		BindStaticRoadMID();
		if (StaticRoadMID)
		{
			StaticRoadMID->SetScalarParameterValue(TEXT("Wetness"), Wet);
			StaticRoadMID->SetScalarParameterValue(TEXT("Rain"), Rain);
		}
	}
	// 2026-10-16: wet masonry darkens, snow settles on roofs by pitch
	// (apply_building_material*.py "Wetness"/"SnowCover", 0 = dry/bare).
	for (TActorIterator<AODBuildingManager> It(World); It; ++It)
	{
		It->SetWeather(Wet, Cover);
	}
}

void AODSeasonController::BindStaticRoadMID()
{
	if (bStaticRoadBound)
	{
		return;
	}
	bStaticRoadBound = true;
	UMaterialInterface* RoadMat = LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_Road.M_Road"));
	if (!RoadMat)
	{
		return;
	}
	int32 Swapped = 0;
	for (TObjectIterator<UStaticMeshComponent> It; It; ++It)
	{
		if (It->GetWorld() != GetWorld())
		{
			continue;
		}
		for (int32 i = 0; i < It->GetNumMaterials(); ++i)
		{
			if (It->GetMaterial(i) == RoadMat)
			{
				if (!StaticRoadMID)
				{
					// level meshes carry plain M_Road; the MID takes its Realistic twin
					StaticRoadMID = UMaterialInstanceDynamic::Create(ODVisualStyle::Pick(RoadMat), this);
					StaticRoadMID->SetScalarParameterValue(TEXT("PuddleAmount"), 0.0f);
				}
				It->SetMaterial(i, StaticRoadMID);
				++Swapped;
			}
		}
	}
	UE_LOG(LogTemp, Display, TEXT("ODSeasonController: no AODRoadManager - %d static M_Road slot(s) on a wetness MID"), Swapped);
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
	// World-aligned box (2026-10-06): UpdateSnowfallFX moves it, nothing rotates it.
	SnowFxHism->SetUsingAbsoluteLocation(true);
	SnowFxHism->SetUsingAbsoluteRotation(true);
	SnowFxHism->RegisterComponent();

	// A box of tiny flakes around the camera (see UpdateSnowfallFX). Fixed
	// pool, seeded once; only visibility + per-instance fall/drift motion
	// change at runtime.
	//
	// 2026-09-07 tuning note: the first pass used a 5 cm scale over a 28 m x
	// 28 m x 18 m box (220 flakes) - technically animating (confirmed via a
	// pixel-diff against the zero-snowfall baseline showing real per-frame
	// motion), but at driving distance in a 1280x720 screenshot it read as
	// essentially invisible noise, not "visibly snowing." Bumped scale and
	// tightened the box (denser per unit volume) so light snowfall reads
	// clearly in a still frame without turning into a blizzard.
	// Weather W2: pool doubled to 520 - the visible count is Num *
	// PrecipIntensity, so the Snow state (0.5) still shows the tuned 260.
	// 2026-10-16: + a far layer of 300 bigger, fainter instances (custom data
	// 1 = fade) in a 3x wider box, drawn only outside the near box, so heavy
	// precipitation thickens toward the horizon instead of stopping at 9 m.
	constexpr int32 NumFlakes = 520;
	constexpr int32 NumFar = 300;
	constexpr float HalfExtentXYCm = 900.0f;
	constexpr float HalfHeightCm = 700.0f;
	FRandomStream Rng(12345);
	SnowFxNumNear = NumFlakes;
	SnowFxLocalOffsets.Reserve(NumFlakes + NumFar);
	PrecipFxSnowRoll.Reserve(NumFlakes + NumFar);
	PrecipFxRoll.Reserve(NumFlakes + NumFar);
	TArray<FTransform> Transforms;
	Transforms.Reserve(NumFlakes + NumFar);
	for (int32 i = 0; i < NumFlakes + NumFar; ++i)
	{
		const float Ext = i < NumFlakes ? 1.0f : 3.0f;
		const FVector Offset(
			Rng.FRandRange(-HalfExtentXYCm, HalfExtentXYCm) * Ext,
			Rng.FRandRange(-HalfExtentXYCm, HalfExtentXYCm) * Ext,
			Rng.FRandRange(-HalfHeightCm, HalfHeightCm) * (i < NumFlakes ? 1.0f : 1.5f));
		SnowFxLocalOffsets.Add(Offset);
		PrecipFxSnowRoll.Add(Rng.FRand());
		PrecipFxRoll.Add(FVector3f(Rng.FRandRange(0.6f, 1.4f), Rng.FRandRange(0.8f, 1.2f), Rng.FRandRange(0.0f, 2.0f * PI)));
		FTransform T;
		T.SetLocation(Offset);
		T.SetScale3D(FVector(0.11f));   // ~11 cm sphere - reads as a soft flake at driving distance
		Transforms.Add(T);
	}
	SnowFxHism->SetNumCustomDataFloats(2);
	SnowFxHism->AddInstances(Transforms, false);
	PrecipFxIsSnow.Init(1, NumFlakes + NumFar);
	for (int32 i = 0; i < NumFlakes + NumFar; ++i)
	{
		SnowFxHism->SetCustomDataValue(i, 0, 1.0f, false);
		SnowFxHism->SetCustomDataValue(i, 1, i < NumFlakes ? 1.0f : 0.35f, false);
	}
}

void AODSeasonController::UpdateSnowfallFX(float DeltaSeconds)
{
	if (!SnowFxHism)
	{
		return;
	}

	// 2026-10-06: the pool is world-aligned and world-anchored, centered just
	// ahead of the camera. It used to be attached to the pawn root, so its
	// local frame (offsets, wrap axes) rolled/pitched with the car body and
	// the whole snowfall twisted on banked/steep roads. Now flakes keep their
	// world positions while the box re-centers (Shift), so fall stays
	// world-down + wind and driving carries the camera through them.
	const APlayerCameraManager* CamMgr = UGameplayStatics::GetPlayerCameraManager(this, 0);
	const AODWeatherController* Weather = WeatherCtl.Get();
	const float Precip = Weather ? Weather->PrecipIntensity : 0.0f;
	const bool bVisible = Precip > 0.02f && CamMgr != nullptr && !(Weather && Weather->IsSheltered());
	if (SnowFxHism->IsVisible() != bVisible)
	{
		SnowFxHism->SetVisibility(bVisible);
	}
	if (!bVisible)
	{
		bSnowFxCentered = false;
		return;
	}

	// Weather W2 + 2026-10-16 realism pass. Snow falls ~1.7 m/s (real
	// aggregates: 1-2 m/s) and rides the wind almost fully (0.8x), so a
	// blizzard goes sideways; rain falls ~9 m/s at 0.9x wind. Each flake has a
	// fixed size/fall roll and flutters on an absolute-time sine (a drawn
	// offset, not integrated - frame-rate independent). Base flake size
	// shrinks with wind (big soft calm-air flakes, fine blizzard grit). Sleet
	// (PrecipType ~0.5) turns the snow-kind instances into small fast ice
	// pellets. Rain - and snow at speed - is a sphere stretched along its
	// velocity relative to the car, so streaks lengthen and rake toward the
	// lens as you drive.
	constexpr float SnowFallCmS = 170.0f;
	constexpr float PelletFallCmS = 550.0f;
	constexpr float RainFallCmS = 900.0f;
	constexpr double HalfHeightCm = 700.0;
	constexpr double HalfExtentXYCm = 900.0;
	constexpr float AheadCm = 600.0f;           // box center ahead of the camera (little falls behind it)
	constexpr float StreakSeconds = 0.055f;     // streak length = relative speed x this (50 cm at a parked 9 m/s fall)
	constexpr float StreakMaxCm = 250.0f;
	constexpr float FlutterCm = 18.0f;
	constexpr float FarScale = 2.2f;            // far layer: bigger so it reads at 10-27 m

	const FVector CamLoc = CamMgr->GetCameraLocation();
	const FVector Center = CamLoc + CamMgr->GetCameraRotation().Vector().GetSafeNormal2D() * AheadCm;
	FVector Shift = Center - SnowFxCenter;
	FVector CamVel = (CamLoc - SnowFxPrevCam) / FMath::Max(DeltaSeconds, 1.0e-3f);
	// a teleport is a camera SPEED no car reaches (150 m/s, as the lens
	// wipe's LENS_TELEPORT_MS) - a per-frame distance fired on every frame
	// at low fps and highway speed
	if (!bSnowFxCentered || CamVel.SizeSquared() > FMath::Square(15000.0f))
	{
		Shift = CamVel = FVector::ZeroVector;   // first frame or a teleport: keep the pattern, just move the box
	}
	SnowFxCenter = Center;
	SnowFxPrevCam = CamLoc;
	bSnowFxCentered = true;
	SnowFxHism->SetWorldLocation(Center);

	const float WindMs = Weather ? Weather->WindSpeedMs : 0.0f;
	const float WindYaw = FMath::DegreesToRadians(Weather ? Weather->WindDirDeg : 0.0f);
	const FVector WindDir(FMath::Cos(WindYaw), FMath::Sin(WindYaw), 0.0f);
	const float SnowType = Weather ? Weather->PrecipType : 1.0f;
	const float Pellet = 1.0f - FMath::SmoothStep(0.55f, 0.85f, SnowType);   // sleet: snow-kind = ice pellets
	const float SnowFall = FMath::Lerp(SnowFallCmS, PelletFallCmS, Pellet);
	const float FlakeScale = FMath::Lerp(FMath::Lerp(0.11f, 0.06f, FMath::Clamp(WindMs / 20.0f, 0.0f, 1.0f)), 0.035f, Pellet);
	const FVector SnowVel = WindDir * (0.8f * WindMs * 100.0f) - FVector(0, 0, SnowFall);
	const FVector RainVel = WindDir * (0.9f * WindMs * 100.0f) - FVector(0, 0, RainFallCmS);
	// Streaks rake along velocity relative to the CAR (PLAN-REALISM Phase 6,
	// 2026-10-14): the body's velocity comes from the fixed 120 Hz physics
	// step, so the slant no longer jitters with the frame-to-frame camera
	// delta (chase-cam lag, uneven frame times). No car (menus): the camera
	// delta, as before.
	const APawn* Car = UGameplayStatics::GetPlayerPawn(this, 0);
	const FVector Frame = Car ? Car->GetVelocity() : CamVel;
	const FVector RainRel = RainVel - Frame;
	const FVector SnowRel = SnowVel - Frame;
	const FQuat RainRot = FRotationMatrix::MakeFromZ(RainRel).ToQuat();
	const FQuat SnowRot = FRotationMatrix::MakeFromZ(SnowRel).ToQuat();
	const float StreakCm = FMath::Clamp(RainRel.Size() * StreakSeconds, 50.0f, StreakMaxCm);
	// motion blur on a flake: the distance it sweeps in ~1/3 of the streak time
	const float SnowSmearCm = FMath::Min(SnowRel.Size() * StreakSeconds * 0.35f, 120.0f);
	const float Time = GetWorld()->GetTimeSeconds();

	const int32 Num = SnowFxLocalOffsets.Num();
	const int32 NumNear = SnowFxNumNear;
	const float Active = FMath::Clamp(Precip, 0.0f, 1.0f);
	const int32 NumActiveNear = FMath::RoundToInt(NumNear * Active);
	// far layer only once it is properly coming down (none in drizzle)
	const int32 NumActiveFar = FMath::RoundToInt((Num - NumNear) * FMath::Clamp((Active - 0.25f) / 0.5f, 0.0f, 1.0f));
	for (int32 i = 0; i < Num; ++i)
	{
		const bool bFar = i >= NumNear;
		const double Ext = bFar ? 3.0 : 1.0;
		const double HalfH = HalfHeightCm * (bFar ? 1.5 : 1.0);
		const bool bSnow = PrecipFxSnowRoll[i] < SnowType;
		if (PrecipFxIsSnow[i] != (uint8)bSnow)
		{
			PrecipFxIsSnow[i] = bSnow;
			SnowFxHism->SetCustomDataValue(i, 0, bSnow ? 1.0f : 0.0f, false);
		}
		const FVector3f& Roll = PrecipFxRoll[i];
		FVector& Offset = SnowFxLocalOffsets[i];
		Offset += (bSnow ? SnowVel : RainVel) * (DeltaSeconds * Roll.Y) - Shift;
		Offset.X = FMath::Wrap(Offset.X, -HalfExtentXYCm * Ext, HalfExtentXYCm * Ext);
		Offset.Y = FMath::Wrap(Offset.Y, -HalfExtentXYCm * Ext, HalfExtentXYCm * Ext);
		Offset.Z = FMath::Wrap(Offset.Z, -HalfH, HalfH);

		FTransform T;
		FVector Drawn = Offset;
		const bool bSlotOff = bFar
			? (i - NumNear >= NumActiveFar
				|| (FMath::Abs(Offset.X) < HalfExtentXYCm && FMath::Abs(Offset.Y) < HalfExtentXYCm))   // the near box covers it
			: i >= NumActiveNear;
		const float Scale = bFar ? FarScale : 1.0f;
		if (bSlotOff)
		{
			T.SetScale3D(FVector::ZeroVector);   // pool slot unused at this intensity
		}
		else if (bSnow)
		{
			const float Size = FlakeScale * Roll.X * Scale;
			const float Flutter = FlutterCm * (1.0f - Pellet);
			Drawn += FVector(FMath::Sin(Time * 1.7f + Roll.Z), FMath::Cos(Time * 1.3f + 2.0f * Roll.Z), 0.0f) * Flutter;
			T.SetRotation(SnowRot);
			T.SetScale3D(FVector(Size, Size, FMath::Max(Size, SnowSmearCm / 100.0f * Scale)));
		}
		else
		{
			T.SetRotation(RainRot);
			T.SetScale3D(FVector(0.015f * Scale, 0.015f * Scale, StreakCm / 100.0f * Roll.Y * Scale));   // ~1.5 cm wide streak (Sphere is 100 cm)
		}
		T.SetLocation(Drawn);
		SnowFxHism->UpdateInstanceTransform(i, T, false, i == Num - 1, true);
	}
}

void AODSeasonController::HidePrecipFx()
{
	if (SnowFxHism)
	{
		SnowFxHism->SetVisibility(false);
	}
	bSnowFxCentered = false;
}
