#include "ODWeatherController.h"
#include "ODTileSource.h"

#include "Camera/CameraComponent.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Dom/JsonObject.h"
#include "Engine/StaticMesh.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "HAL/IConsoleManager.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "ODForestManager.h"
#include "ODRoadManager.h"
#include "ODSeasonController.h"
#include "ODVehicleFX.h"
#include "ODVehiclePawn.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
	constexpr int32 NumStates = 12;

	// ---- weight table: MIRRORED in pipeline/sim_weather.py, same names ----
	// Change both together; that script's sanity asserts (Oahu no snow + rare
	// hurricanes, Palmdale summer dust/no rain, Summit winter snow-heavy,
	// Skagit rainy) are the offline check for this table.
	constexpr float PRECIP_REF_MM = 50.0f;     // monthly precip that gives precip propensity 1
	constexpr float PRECIP_CAP = 3.0f;         // propensity ceiling (Skagit's 300 mm Novembers)
	constexpr float DRY_MM = 30.0f;            // below this, "dry" ramps 0 -> 1 (Clear/Dust boost)
	constexpr float LAPSE_C_PER_KM = 6.5f;
	constexpr float SNOW_T_C = -2.0f, RAIN_T_C = 2.0f;   // all snow below, all rain above, sleet between
	constexpr float W_CLEAR = 1.0f, W_CLEAR_DRY = 1.5f;
	constexpr float W_OVERCAST = 0.4f, W_OVERCAST_WET = 0.2f;
	constexpr float W_PRECIP = 1.0f;
	constexpr float SNOW_BOOST = 2.0f;         // same water falls as more, lighter snow days
	constexpr float RAIN_SPLIT[3] = { 0.6f, 0.25f, 0.15f };   // Rain, HeavyRain, Thunderstorm (x warm)
	constexpr float SNOW_SPLIT[2] = { 0.75f, 0.25f };         // Snow, Blizzard
	constexpr float THUNDER_T_C = 8.0f, THUNDER_RAMP_C = 12.0f;
	constexpr float W_FOG = 0.25f;
	constexpr float W_DUST = 1.0f;
	constexpr float W_TORNADO = 0.5f;          // only rolled out of Thunderstorm
	constexpr float TORNADO_T_C = 12.0f, TORNADO_RAMP_C = 10.0f;
	constexpr float W_HURRICANE = 0.05f;
	constexpr float HURRICANE_T_C = 25.0f, HURRICANE_RAMP_C = 1.5f;
	constexpr float HOLD_MIN_S = 240.0f, HOLD_MAX_S = 720.0f;
	constexpr float TORNADO_HOLD_S[2] = { 180.0f, 300.0f };
	constexpr float HURRICANE_HOLD_S[2] = { 600.0f, 1200.0f };

	// per EODBiome: { precip, fog, dust } multipliers; NEUTRAL_MULT = no biome data
	constexpr float BIOME_MULT[16][3] = {
		{ 1.0f, 1.0f, 0.0f },   // DeciduousForest
		{ 1.1f, 1.3f, 0.0f },   // EvergreenForest
		{ 0.9f, 0.8f, 0.5f },   // Chaparral
		{ 0.6f, 0.3f, 1.5f },   // DesertScrub
		{ 1.0f, 0.8f, 0.7f },   // PlainsGrassland
		{ 1.1f, 2.5f, 0.0f },   // Wetland
		{ 1.1f, 1.2f, 0.0f },   // AlpineBare
		{ 1.0f, 1.2f, 0.0f },   // ArcticTundra
		{ 1.0f, 1.2f, 0.5f },   // Farmland
		{ 0.8f, 0.5f, 1.0f },   // Savanna
		{ 1.3f, 1.5f, 0.0f },   // Rainforest
		{ 1.0f, 1.8f, 0.3f },   // CoastalDune
		{ 0.6f, 0.3f, 1.3f },   // CanyonBadlands
		{ 1.0f, 0.8f, 0.3f },   // RockyFoothill
		{ 1.2f, 1.2f, 0.2f },   // TropicalCoast
		{ 0.9f, 0.8f, 0.6f },   // LavaField (vog haze reads as dust)
	};
	constexpr float NEUTRAL_MULT[3] = { 1.0f, 1.0f, 1.0f };
	static_assert(UE_ARRAY_COUNT(BIOME_MULT) == (int32)EODBiome::LavaField + 1, "one row per EODBiome");

	// Per-state scalar targets (not mirrored - the sim only needs states):
	// { PrecipIntensity, PrecipType, Wind m/s, Visibility m, CloudCover, Dust, StormTint }
	// Tornado visibility is 800 m on purpose (Taylor, 2026-09-28): the funnel
	// is hidden at its 2-5 km spawn and looms out of the fog as it closes in -
	// the HUD warning's bearing is the only early cue. Near-funnel loss
	// down to TORNADO_VIS_M is still applied locally (TickTornado).
	constexpr float STATE_TARGETS[NumStates][7] = {
		{ 0.0f,  0.0f,  3.0f, 20000.0f, 0.10f, 0.0f, 0.0f },   // Clear
		{ 0.0f,  0.0f,  5.0f, 12000.0f, 0.85f, 0.0f, 0.0f },   // Overcast
		{ 0.45f, 0.0f,  6.0f,  4000.0f, 0.90f, 0.0f, 0.0f },   // Rain
		{ 0.9f,  0.0f, 10.0f,  1200.0f, 1.00f, 0.0f, 0.0f },   // HeavyRain
		{ 0.5f,  0.5f,  7.0f,  2500.0f, 0.95f, 0.0f, 0.0f },   // Sleet
		{ 0.5f,  1.0f,  4.0f,  2000.0f, 0.95f, 0.0f, 0.0f },   // Snow
		{ 1.0f,  1.0f, 18.0f,   250.0f, 1.00f, 0.0f, 0.0f },   // Blizzard
		{ 0.0f,  0.0f,  1.0f,   250.0f, 0.70f, 0.0f, 0.0f },   // Fog
		{ 0.0f,  0.0f, 16.0f,   600.0f, 0.30f, 1.0f, 0.0f },   // DustStorm
		{ 0.85f, 0.0f, 12.0f,  1500.0f, 1.00f, 0.0f, 0.0f },   // Thunderstorm
		{ 0.7f,  0.0f, 25.0f,   800.0f, 1.00f, 0.3f, 1.0f },   // Tornado
		{ 1.0f,  0.0f, 35.0f,   300.0f, 1.00f, 0.0f, 0.0f },   // Hurricane
	};

	// ---- W4 tornado (sec 7.42) ----
	constexpr float TORNADO_SPAWN_M[2] = { 2000.0f, 5000.0f };   // funnel spawn distance from the player
	constexpr float TORNADO_RESPAWN_M = 6000.0f;   // walked this far off -> new funnel (long QA holds)
	constexpr float TORNADO_SPEED_MS = 15.0f;
	constexpr float TORNADO_AIM_DEG = 25.0f;       // spawn heading = toward the player +- this
	constexpr float TORNADO_TURN_DEG_S = 0.15f;    // max constant path curvature (~45 deg per 5 min)
	constexpr float TORNADO_REACH_M = 300.0f;      // vortex wind reach, fades out over the outer half
	constexpr float TORNADO_CORE_M = 50.0f;        // Rankine core radius (peak wind)
	constexpr float TORNADO_VMAX_MS = 60.0f;       // peak tangential wind
	constexpr float TORNADO_INFLOW = 0.5f;         // radial inflow / tangential (the pull toward the core)
	constexpr float TORNADO_VIS_M = 40.0f, TORNADO_VIS_REACH_M = 600.0f;
	constexpr float TORNADO_FADE_S = 20.0f;
	constexpr int32 FUNNEL_SEGMENTS = 14;
	constexpr float FUNNEL_HEIGHT_M = 900.0f;
	constexpr float FUNNEL_R_M[2] = { 35.0f, 260.0f };   // bottom, top radius
	constexpr float FUNNEL_SWAY_M = 25.0f;
	constexpr float FUNNEL_OPACITY = 0.55f;        // unreal/apply_tornado_material.py's default
	constexpr int32 DEBRIS_COUNT = 40;

	// ---- W5 hurricane band envelope: MIRRORED in pipeline/sim_weather.py
	// (hurricane_envelope), same names - its asserts check >= 2 bands, a lull
	// and 0..1. Phase = fraction of the hold (forced: HURR_FORCED_LEN_S, looping).
	constexpr float HURR_BANDS[2] = { 0.25f, 0.72f };   // rain band centers
	constexpr float HURR_BAND_HALF = 0.14f;
	constexpr float HURR_LULL = 0.35f;                  // envelope between bands
	constexpr float HURR_EYE_C = 0.485f, HURR_EYE_HALF = 0.06f, HURR_EYE_LEVEL = 0.05f;
	constexpr float HURR_EYE_CHANCE = 0.5f;             // forced (QA) always has the eye
	constexpr float HURR_FORCED_LEN_S = 900.0f;
	constexpr float HURR_GUST = 0.3f;                   // wind boost at a full gust
	constexpr float HURR_VIS_M[2] = { 3000.0f, 300.0f }; // visibility at envelope 0 .. 1
	constexpr float HURR_GUST_VIS = 0.15f;              // x visibility at a full gust in a band

	// ---- on-lens precipitation (TickLens; the tuning knobs are UPROPERTYs) ----
	constexpr float LENS_DISTANCE_CM = 20.0f;    // quads sit this far in front of the camera (near plane is 10 cm)
	constexpr float LENS_RATE_REF_MS = 20.0f;    // landing rate doubles at this camera speed
	constexpr float LENS_SAG = 0.02f;            // water slides down, half-widths/s
	constexpr float LENS_SNOW_GRIP = 0.15f;      // an unmelted flake moves at this fraction of the airflow
	constexpr float LENS_MELT_SHRINK = 0.6f;     // smear radius / flake radius once melted
	constexpr float LENS_FADE_IN_S = 0.08f;      // softens the landing pop
	constexpr float LENS_FADE_OUT = 0.25f;       // fades over the last fraction of its life
	constexpr float LENS_TELEPORT_MS = 150.0f;   // a faster camera "speed" is a teleport/respawn, not driving

	constexpr float PUDDLE_PER_RAIN = 1.4f;      // standing-water level per unit rain rate (Rain 0.45 -> 0.63, HeavyRain full)

	/** Raised-cosine bump, 1 at D = 0, 0 beyond +-H. */
	float Bump(float D, float H)
	{
		return FMath::Abs(D) < H ? 0.5f * (1.0f + FMath::Cos(PI * D / H)) : 0.0f;
	}

	/** 0..1 storm strength over the hold: band -> lull (-> eye) -> band. */
	float HurricaneEnvelope(float Phase, bool bEye)
	{
		float E = HURR_LULL + (1.0f - HURR_LULL)
			* FMath::Max(Bump(Phase - HURR_BANDS[0], HURR_BAND_HALF), Bump(Phase - HURR_BANDS[1], HURR_BAND_HALF));
		if (bEye)
		{
			E = FMath::Lerp(E, HURR_EYE_LEVEL, Bump(Phase - HURR_EYE_C, HURR_EYE_HALF));
		}
		return E;
	}

	/** Linear between month centers (i + 0.5) - sim_weather.py's monthly(). */
	float Monthly(const float (&Vals)[12], float Month)
	{
		const float F = Month - 0.5f;
		const int32 I = FMath::FloorToInt(F);
		const float T = F - I;
		return FMath::Lerp(Vals[((I % 12) + 12) % 12], Vals[(((I + 1) % 12) + 12) % 12], T);
	}

	/** The Auto walk's per-state weights (2026-10-05: shared by Roll and
	 *  CanProduce, so the garage Weather row offers exactly what the walk can
	 *  roll). Prev gates the event states like the walk: Tornado only out of a
	 *  Thunderstorm, no Hurricane out of Tornado/Hurricane. OutT/OutP = the
	 *  month's lapsed temperature and precip, for the log. */
	void StateWeights(const FODClimate& C, float Month, float ElevDeltaM, const float* Mult, EODWeatherState Prev,
		float (&W)[NumStates], float& OutT, float& OutP)
	{
		const float T = Monthly(C.MonthlyTempC, Month) - LAPSE_C_PER_KM * ElevDeltaM / 1000.0f;
		const float P = Monthly(C.MonthlyPrecipMm, Month);
		const float Pw = FMath::Min(P / PRECIP_REF_MM, PRECIP_CAP) * Mult[0];
		const float Dry = FMath::Clamp(1.0f - P / DRY_MM, 0.0f, 1.0f);
		const float S = FMath::Clamp((RAIN_T_C - T) / (RAIN_T_C - SNOW_T_C), 0.0f, 1.0f);
		const float Warm = FMath::Clamp((T - THUNDER_T_C) / THUNDER_RAMP_C, 0.0f, 1.0f);
		const float RainFam = W_PRECIP * Pw * (1.0f - S) * (1.0f - S);
		const float SnowFam = W_PRECIP * Pw * S * S * SNOW_BOOST;

		for (float& V : W) { V = 0.0f; }
		W[(int32)EODWeatherState::Clear] = W_CLEAR + W_CLEAR_DRY * Dry;
		W[(int32)EODWeatherState::Overcast] = W_OVERCAST + W_OVERCAST_WET * FMath::Min(Pw, 1.0f);
		W[(int32)EODWeatherState::Rain] = RainFam * RAIN_SPLIT[0];
		W[(int32)EODWeatherState::HeavyRain] = RainFam * RAIN_SPLIT[1];
		W[(int32)EODWeatherState::Thunderstorm] = RainFam * RAIN_SPLIT[2] * Warm;
		W[(int32)EODWeatherState::Sleet] = W_PRECIP * Pw * 2.0f * S * (1.0f - S);
		W[(int32)EODWeatherState::Snow] = SnowFam * SNOW_SPLIT[0];
		W[(int32)EODWeatherState::Blizzard] = SnowFam * SNOW_SPLIT[1];
		W[(int32)EODWeatherState::Fog] = W_FOG * Mult[1] * (0.5f + 0.5f * FMath::Min(Pw, 1.0f));
		W[(int32)EODWeatherState::DustStorm] = W_DUST * C.DustStorm * Dry * Mult[2];
		if (Prev == EODWeatherState::Thunderstorm)
		{
			W[(int32)EODWeatherState::Tornado] = W_TORNADO * C.Tornado
				* FMath::Clamp((T - TORNADO_T_C) / TORNADO_RAMP_C, 0.0f, 1.0f);
		}
		if (Prev != EODWeatherState::Tornado && Prev != EODWeatherState::Hurricane)
		{
			W[(int32)EODWeatherState::Hurricane] = W_HURRICANE * C.Hurricane
				* FMath::Clamp((T - HURRICANE_T_C) / HURRICANE_RAMP_C, 0.0f, 1.0f);
		}
		OutT = T;
		OutP = P;
	}

	FString StateName(EODWeatherState S)
	{
		return StaticEnum<EODWeatherState>()->GetNameStringByValue((int64)S);
	}

	AODWeatherController* FindWeather(UWorld* World)
	{
		if (World)
		{
			for (TActorIterator<AODWeatherController> It(World); It; ++It) { return *It; }
		}
		return nullptr;
	}

	FAutoConsoleCommandWithWorldAndArgs GODWeatherCmd(
		TEXT("od.Weather"),
		TEXT("od.Weather <Clear|Overcast|Rain|HeavyRain|Sleet|Snow|Blizzard|Fog|DustStorm|Thunderstorm|"
		     "Tornado|Hurricane|Auto> - QA: force and hold a weather state; Auto resumes the random walk. "
		     "od.Weather Depth <cm|Auto> forces/releases road snow depth"),
		FConsoleCommandWithWorldAndArgsDelegate::CreateLambda([](const TArray<FString>& Args, UWorld* World)
		{
			AODWeatherController* Weather = FindWeather(World);
			if (!Weather)
			{
				UE_LOG(LogTemp, Warning, TEXT("od.Weather: no AODWeatherController in this world"));
				return;
			}
			if (Args.Num() == 0)
			{
				UE_LOG(LogTemp, Display, TEXT("od.Weather: %s precip=%.2f type=%.2f wind=%.1f m/s vis=%.0f m wet=%.2f puddles=%.2f ")
					TEXT("air=%.1f C chill=%.2f snow road=%.1f cm side=%.1f cm"),
					*StateName(Weather->State), Weather->PrecipIntensity, Weather->PrecipType,
					Weather->WindSpeedMs, Weather->VisibilityM, Weather->Wetness, Weather->Puddles,
					Weather->AirTempC, Weather->RoadChill, Weather->RoadSnowCm, Weather->SideSnowCm);
				return;
			}
			if (Args[0].Equals(TEXT("Depth"), ESearchCase::IgnoreCase) && Args.Num() >= 2)
			{
				Weather->SetSnowDepth(Args[1].Equals(TEXT("Auto"), ESearchCase::IgnoreCase)
					? -1.0f : FCString::Atof(*Args[1]));
				return;
			}
			if (!Weather->ForceState(Args[0]))
			{
				UE_LOG(LogTemp, Warning, TEXT("od.Weather: unknown state '%s'"), *Args[0]);
			}
		}));
}

AODWeatherController::AODWeatherController()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODWeatherController::BeginPlay()
{
	Super::BeginPlay();
	LoadClimate();
	Roll();
	// Starting weather is where the session begins, not a transition.
	From = To;
	RampT = 1.0f;

	FParse::Value(FCommandLine::Get(), TEXT("odsnowrate="), SnowTimeScale);
	float DepthCm = -1.0f;
	if (FParse::Value(FCommandLine::Get(), TEXT("odsnowdepth="), DepthCm))
	{
		SetSnowDepth(DepthCm);
	}
}

void AODWeatherController::LoadClimate()
{
	// Same AODRoadManager-derived area name as every AODGameMode::Ensure*
	// manager: "road_<area>_test" -> Content/OpenDrive/Masks/climate_<area>_test.json
	// (copied there by pipeline/fetch_climate.py, staged like the .bin sidecars).
	// No road manager (Tail of the Dragon's hand-built level): the level name
	// in snake case, "TailOfTheDragon" -> climate_tail_of_the_dragon.json.
	FString Area;
	for (TActorIterator<AODRoadManager> It(GetWorld()); It; ++It)
	{
		Area = It->RoadTilePrefix;
		Area.RemoveFromStart(TEXT("road_"));
		break;
	}
	if (Area.IsEmpty())
	{
		for (const TCHAR C : UGameplayStatics::GetCurrentLevelName(this))
		{
			if (FChar::IsUpper(C) && !Area.IsEmpty()) { Area += TEXT('_'); }
			Area += FChar::ToLower(C);
		}
	}
	LoadClimateFile(Area, HomeClimate);
	Climate = HomeClimate;
}

bool AODWeatherController::LoadClimateFile(const FString& Area, FODClimate& Out)
{
	const FString Path = ODTileSource::Fetch(FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ FString::Printf(TEXT("climate_%s.json"), *Area));

	FString Text;
	TSharedPtr<FJsonObject> Obj;
	const TArray<TSharedPtr<FJsonValue>>* Temps = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* Precip = nullptr;
	if (Area.IsEmpty() || !FFileHelper::LoadFileToString(Text, *Path)
		|| !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Obj) || !Obj.IsValid()
		|| !Obj->TryGetArrayField(TEXT("monthly_temp_c"), Temps) || Temps->Num() != 12
		|| !Obj->TryGetArrayField(TEXT("monthly_precip_mm"), Precip) || Precip->Num() != 12)
	{
		UE_LOG(LogTemp, Warning, TEXT("ODWeatherController: no usable climate at '%s' - mild default climate"), *Path);
		return false;
	}
	for (int32 m = 0; m < 12; ++m)
	{
		Out.MonthlyTempC[m] = (*Temps)[m]->AsNumber();
		Out.MonthlyPrecipMm[m] = (*Precip)[m]->AsNumber();
	}
	const TSharedPtr<FJsonObject>* Storms = nullptr;
	if (Obj->TryGetObjectField(TEXT("storms"), Storms))
	{
		double V = 0.0;
		if ((*Storms)->TryGetNumberField(TEXT("hurricane"), V)) Out.Hurricane = V;
		if ((*Storms)->TryGetNumberField(TEXT("tornado"), V)) Out.Tornado = V;
		if ((*Storms)->TryGetNumberField(TEXT("dust_storm"), V)) Out.DustStorm = V;
	}
	UE_LOG(LogTemp, Display, TEXT("ODWeatherController: climate '%s' storms hurr=%.2f torn=%.2f dust=%.2f"),
		*Path, Out.Hurricane, Out.Tornado, Out.DustStorm);
	return true;
}

void AODWeatherController::SetVistaClimate(const FODClimate* InClimate, AODForestManager* Forest, const FVector2D& SpotCm)
{
	bVista = InClimate != nullptr;
	Climate = bVista ? *InClimate : HomeClimate;
	VistaForest = Forest;
	VistaSpotCm = SpotCm;
}

void AODWeatherController::SampleBiome(const float*& OutMult, float& OutElevDeltaM, int32& OutBiomeIdx) const
{
	// Biome + elevation at the player (nearest classified biome point).
	// No point-biome data (Tail of the Dragon, Palmdale) -> neutral
	// multipliers and no lapse, same as sim_weather.py's None biome.
	OutMult = NEUTRAL_MULT;
	OutElevDeltaM = 0.0f;
	OutBiomeIdx = -1;
	EODBiome Biome;
	float Delta = 0.0f;
	if (bVista)
	{
		// garage vista (2026-10-05): its own forest manager at the spot, in
		// area-local cm - the far-offset car is in no area's frame
		if (VistaForest.IsValid() && VistaForest->BiomeSampleAt(VistaSpotCm.X, VistaSpotCm.Y, Biome, Delta))
		{
			OutElevDeltaM = Delta;
			OutBiomeIdx = (int32)Biome;
			OutMult = BIOME_MULT[OutBiomeIdx];
		}
		return;
	}
	if (const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0))
	{
		// nearest forest manager's sample (2026-10-05, large world: one per
		// streamed area set); climate itself stays the home area's
		float BestD2 = TNumericLimits<float>::Max();
		for (TActorIterator<AODForestManager> It(GetWorld()); It; ++It)
		{
			float D2 = 0.0f;
			if (It->BiomeSampleAt(Player->GetActorLocation().X, Player->GetActorLocation().Y, Biome, Delta, &D2)
				&& D2 < BestD2)
			{
				BestD2 = D2;
				OutElevDeltaM = Delta;
				OutBiomeIdx = (int32)Biome;
				OutMult = BIOME_MULT[OutBiomeIdx];
			}
		}
	}
}

bool AODWeatherController::CanProduce(EODWeatherState S) const
{
	const float* Mult = NEUTRAL_MULT;
	float ElevDeltaM = 0.0f;
	int32 BiomeIdx = -1;
	SampleBiome(Mult, ElevDeltaM, BiomeIdx);
	// A locked season (garage Season row, -odseason=) freezes the clock on one
	// month, so only that month's weather can roll (2026-10-09, Taylor: pick
	// the season first, then the weather it allows). Auto = any month.
	float Months[12];
	int32 NumMonths = 12;
	for (int32 m = 0; m < 12; ++m) { Months[m] = m + 0.5f; }
	for (TActorIterator<AODSeasonController> It(GetWorld()); It; ++It)
	{
		if (It->IsFrozen()) { Months[0] = It->MonthOfYear(); NumMonths = 1; }
		break;
	}
	for (int32 m = 0; m < NumMonths; ++m)
	{
		float W[NumStates];
		float T = 0.0f, P = 0.0f;
		// Prev = Thunderstorm opens both event gates; a Tornado also needs that
		// month's Thunderstorm to roll out of
		StateWeights(Climate, Months[m], ElevDeltaM, Mult, EODWeatherState::Thunderstorm, W, T, P);
		if (W[(int32)S] > 0.0f
			&& (S != EODWeatherState::Tornado || W[(int32)EODWeatherState::Thunderstorm] > 0.0f))
		{
			return true;
		}
	}
	return false;
}

void AODWeatherController::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	if (!bForced)
	{
		HoldLeft -= DeltaSeconds;
		if (HoldLeft <= 0.0f)
		{
			Roll();
		}
	}

	RampT = FMath::Min(RampT + DeltaSeconds / FMath::Max(RampSeconds, 0.01f), 1.0f);
	const float A = FMath::SmoothStep(0.0f, 1.0f, RampT);
	PrecipIntensity = FMath::Lerp(From.Precip, To.Precip, A);
	PrecipType = FMath::Lerp(From.PrecipType, To.PrecipType, A);
	WindSpeedMs = FMath::Lerp(From.Wind, To.Wind, A);
	WindDirDeg = From.WindDir + FMath::FindDeltaAngleDegrees(From.WindDir, To.WindDir) * A;
	VisibilityM = FMath::Lerp(From.Visibility, To.Visibility, A);
	CloudCover = FMath::Lerp(From.Cloud, To.Cloud, A);
	DustAmount = FMath::Lerp(From.Dust, To.Dust, A);
	StormTint = FMath::Lerp(From.Tint, To.Tint, A);

	// W5: the bands modulate the Hurricane targets. Blended in by the ramp so
	// entering from any state stays continuous; leaving starts From here.
	if (State == EODWeatherState::Hurricane)
	{
		HurricaneAge += DeltaSeconds;
		const float E = HurricaneEnvelope(FMath::Fmod(HurricaneAge / HurricaneLen, 1.0f), bHurricaneEye);
		const float Gust = FMath::Clamp(FMath::PerlinNoise1D(HurricaneAge * 0.3f) * 2.0f, 0.0f, 1.0f) * E;
		PrecipIntensity = FMath::Lerp(PrecipIntensity, PrecipIntensity * E, A);
		WindSpeedMs = FMath::Lerp(WindSpeedMs, WindSpeedMs * FMath::Sqrt(E) * (1.0f + HURR_GUST * Gust), A);
		VisibilityM = FMath::Lerp(VisibilityM,
			FMath::Lerp(HURR_VIS_M[0], HURR_VIS_M[1], E) * FMath::Lerp(1.0f, HURR_GUST_VIS, Gust), A);
	}
	TickTornado(DeltaSeconds);
	TickCover(DeltaSeconds);
	if (!bIndoors)
	{
		TickLens(DeltaSeconds);
	}

	// Snow/sleet wet the road too (slush) - W3 decides grip per PrecipType.
	Wetness = PrecipIntensity > 0.05f
		? FMath::Min(Wetness + DeltaSeconds * PrecipIntensity / WetUpSeconds, 1.0f)
		: FMath::Max(Wetness - DeltaSeconds * Evaporation() / DrySeconds, 0.0f);
	// Standing water (2026-10-16): eases toward the rain rate's level (light
	// rain = a few rut puddles, a downpour floods the sags), then drains far
	// slower than the film dries. Exponential fill, linear drain: both
	// frame-rate independent (pipeline/checks/check_wet_dryout.py mirrors this).
	RainRate = PrecipIntensity * (1.0f - PrecipType);
	const float PuddleTarget = FMath::Clamp(RainRate * PUDDLE_PER_RAIN, 0.0f, 1.0f);
	Puddles = Puddles < PuddleTarget
		? FMath::Lerp(Puddles, PuddleTarget, 1.0f - FMath::Exp(-DeltaSeconds / FMath::Max(PuddleFillSeconds, 1.0f)))
		: FMath::Max(Puddles - DeltaSeconds * Evaporation() / PuddleDrainSeconds, PuddleTarget);
	TickSnow(DeltaSeconds);
}

float AODWeatherController::Evaporation() const
{
	// 1 at 15 C in calm air; 0.3 near freezing, 2x at a hot 35 C; wind
	// doubles it by 15 m/s (pipeline/checks/check_wet_dryout.py).
	return FMath::Clamp((AirTempC + 5.0f) / 20.0f, 0.3f, 2.0f) * (1.0f + WindSpeedMs / 15.0f);
}

namespace
{
	/** 1 at -2 C, more when colder - speeds contact chill, slows melt. */
	float SnowCold(float TempC, float Lo) { return FMath::Clamp(1.0f - (TempC + 2.0f) / 8.0f, Lo, 1.5f); }
}

void AODWeatherController::TickSnow(float DeltaSeconds)
{
	if (bSnowHeld)
	{
		RoadSnowRateCmS = SideSnowRateCmS = 0.0f;
		return;
	}
	const float K = SnowTimeScale;
	const float Snow = PrecipIntensity * PrecipType;
	const float Rain = PrecipIntensity * (1.0f - PrecipType);
	const bool bSnowing = Snow > 0.05f;
	// While snowing the air is at or below ~0 C, whatever the last roll's
	// month temp said (a forced Blizzard in July still sticks).
	const float ChillCold = SnowCold(FMath::Min(AirTempC, 0.0f), 0.75f);
	RoadChill = bSnowing
		? FMath::Min(RoadChill + DeltaSeconds * K * Snow * ChillCold / SnowContactMeltSeconds, 1.0f)
		: FMath::Max(RoadChill - DeltaSeconds * K / SnowContactMeltSeconds, 0.0f);
	const float Build = Snow * RoadSnowCmPerMin;
	const float Melt = (bSnowing ? 0.0f : SnowMeltCmPerMin / SnowCold(AirTempC, 0.25f)) + Rain * RainMeltCmPerMin;
	RoadSnowRateCmS = K * ((RoadChill >= 1.0f ? Build : 0.0f) - Melt) / 60.0f;
	SideSnowRateCmS = K * (Build * SideSnowMult - Melt) / 60.0f;
	RoadSnowCm = FMath::Clamp(RoadSnowCm + RoadSnowRateCmS * DeltaSeconds, 0.0f, RoadSnowCapCm);
	SideSnowCm = FMath::Clamp(SideSnowCm + SideSnowRateCmS * DeltaSeconds, 0.0f, SideSnowCapCm);
}

void AODWeatherController::SetSnowDepth(float Cm)
{
	bSnowHeld = Cm >= 0.0f;
	if (bSnowHeld)
	{
		RoadSnowCm = FMath::Min(Cm, RoadSnowCapCm);
		SideSnowCm = FMath::Min(RoadSnowCm * SideSnowCapCm / FMath::Max(RoadSnowCapCm, 1.0f), SideSnowCapCm);
		RoadChill = 1.0f;
		++SnowDepthSerial;
	}
	UE_LOG(LogTemp, Display, TEXT("ODWeatherController: QA snow depth %s (road %.1f cm, side %.1f cm)"),
		bSnowHeld ? TEXT("held") : TEXT("released"), RoadSnowCm, SideSnowCm);
}

void AODWeatherController::Roll()
{
	float Month = 6.5f;
	for (TActorIterator<AODSeasonController> It(GetWorld()); It; ++It) { Month = It->MonthOfYear(); break; }

	const float* Mult = NEUTRAL_MULT;
	float ElevDeltaM = 0.0f;
	int32 BiomeIdx = -1;
	SampleBiome(Mult, ElevDeltaM, BiomeIdx);
	float W[NumStates];
	float T = 0.0f, P = 0.0f;
	StateWeights(Climate, Month, ElevDeltaM, Mult, State, W, T, P);
	AirTempC = T;

	float Total = 0.0f;
	for (float V : W) { Total += V; }
	float Pick = FMath::FRand() * Total;
	int32 Next = 0;
	for (; Next < NumStates - 1; ++Next)
	{
		Pick -= W[Next];
		if (Pick < 0.0f) { break; }
	}
	const EODWeatherState NewState = (EODWeatherState)Next;

	float Hold = FMath::FRandRange(HOLD_MIN_S, HOLD_MAX_S);
	if (NewState == EODWeatherState::Tornado) Hold = FMath::FRandRange(TORNADO_HOLD_S[0], TORNADO_HOLD_S[1]);
	if (NewState == EODWeatherState::Hurricane) Hold = FMath::FRandRange(HURRICANE_HOLD_S[0], HURRICANE_HOLD_S[1]);

	// !HasActorBegunPlay(): the BeginPlay roll logs the starting state too.
	if (NewState != State || !HasActorBegunPlay())
	{
		UE_LOG(LogTemp, Display,
			TEXT("ODWeatherController: %s -> %s (hold %.1f min; month %.1f, T %.1f C, P %.0f mm, biome %d)"),
			*StateName(State), *StateName(NewState), Hold / 60.0f, Month, T, P, BiomeIdx);
	}
	EnterState(NewState, Hold);
}

AODWeatherController::FTargets AODWeatherController::Published() const
{
	FTargets P;
	P.Precip = PrecipIntensity; P.PrecipType = PrecipType; P.Wind = WindSpeedMs; P.WindDir = WindDirDeg;
	P.Visibility = VisibilityM; P.Cloud = CloudCover; P.Dust = DustAmount; P.Tint = StormTint;
	return P;
}

void AODWeatherController::EnterState(EODWeatherState NewState, float HoldSeconds)
{
	const float* Tg = STATE_TARGETS[(int32)NewState];
	From = Published();
	To.Precip = Tg[0]; To.PrecipType = Tg[1]; To.Wind = Tg[2];
	To.Visibility = Tg[3]; To.Cloud = Tg[4]; To.Dust = Tg[5]; To.Tint = Tg[6];
	// Wind direction wanders a little each roll instead of jumping.
	To.WindDir = FMath::UnwindDegrees(From.WindDir + FMath::FRandRange(-40.0f, 40.0f));
	// Precip type only ramps while something is falling - a Clear roll keeps
	// the last type so a snow->clear fade doesn't turn into rain on the way out.
	if (Tg[0] <= 0.0f)
	{
		To.PrecipType = From.PrecipType;
	}
	if (NewState != State && NewState == EODWeatherState::Tornado)
	{
		SpawnFunnel();
	}
	if (NewState != State && NewState == EODWeatherState::Hurricane)
	{
		HurricaneAge = 0.0f;
		HurricaneLen = HoldSeconds > 0.0f ? HoldSeconds : HURR_FORCED_LEN_S;
		bHurricaneEye = bForced || FMath::FRand() < HURR_EYE_CHANCE;
		Warn(TEXT("HURRICANE WARNING"));
	}
	State = NewState;
	HoldLeft = HoldSeconds;
	RampT = 0.0f;
}

void AODWeatherController::Warn(const FString& Text)
{
	WarningText = Text;
	WarningShownAt = GetWorld()->GetTimeSeconds();
	UE_LOG(LogTemp, Display, TEXT("ODWeatherController: HUD warning '%s'"), *Text);
}

void AODWeatherController::SpawnFunnel()
{
	// Components once, reused by every later tornado (hidden in between).
	if (!FunnelISM)
	{
		UMaterialInterface* Mat = LoadObject<UMaterialInterface>(
			nullptr, TEXT("/Game/OpenDrive/Materials/M_TornadoFunnel.M_TornadoFunnel"));
		if (!Mat)
		{
			UE_LOG(LogTemp, Warning, TEXT("ODWeatherController: M_TornadoFunnel missing (unreal/apply_tornado_material.py) - default material"));
		}
		FunnelMID = Mat ? UMaterialInstanceDynamic::Create(Mat, this) : nullptr;
		auto Make = [this](const TCHAR* MeshPath, int32 Count)
		{
			UInstancedStaticMeshComponent* C = NewObject<UInstancedStaticMeshComponent>(this);
			C->SetupAttachment(RootComponent);
			C->SetUsingAbsoluteLocation(true);
			C->SetStaticMesh(LoadObject<UStaticMesh>(nullptr, MeshPath));
			if (FunnelMID)
			{
				C->SetMaterial(0, FunnelMID);
			}
			C->SetCollisionEnabled(ECollisionEnabled::NoCollision);
			C->SetCastShadow(false);
			C->RegisterComponent();
			TArray<FTransform> Xf;
			Xf.Init(FTransform::Identity, Count);
			C->AddInstances(Xf, false);
			return C;
		};
		FunnelISM = Make(TEXT("/Engine/BasicShapes/Cylinder.Cylinder"), FUNNEL_SEGMENTS);
		DebrisISM = Make(TEXT("/Engine/BasicShapes/Cube.Cube"), DEBRIS_COUNT);
	}

	// 2-5 km out, heading roughly at the player, curving gently.
	const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0);
	const FVector P = Player ? Player->GetActorLocation() : GetActorLocation();
	const float Bearing = FMath::FRandRange(0.0f, 360.0f);
	const float DistM = FMath::FRandRange(TORNADO_SPAWN_M[0], TORNADO_SPAWN_M[1]);
	const float Rad = FMath::DegreesToRadians(Bearing);
	FunnelPos = FVector2D(P) + FVector2D(FMath::Cos(Rad), FMath::Sin(Rad)) * DistM * 100.0f;
	FunnelHeadingDeg = Bearing + 180.0f + FMath::FRandRange(-TORNADO_AIM_DEG, TORNADO_AIM_DEG);
	FunnelTurnDegS = FMath::FRandRange(-TORNADO_TURN_DEG_S, TORNADO_TURN_DEG_S);
	FunnelGroundZ = P.Z;
	// World frame is X = east, Y = south (ODRaceHUD), so yaw 0 = E, 90 = S.
	static const TCHAR* Compass[8] = { TEXT("E"), TEXT("SE"), TEXT("S"), TEXT("SW"), TEXT("W"), TEXT("NW"), TEXT("N"), TEXT("NE") };
	Warn(FString::Printf(TEXT("TORNADO WARNING - %.1f km %s"), DistM / 1000.0f,
		Compass[FMath::RoundToInt(Bearing / 45.0f) % 8]));
}

void AODWeatherController::TickTornado(float DeltaSeconds)
{
	const bool bOn = State == EODWeatherState::Tornado;
	TornadoFade = FMath::Clamp(TornadoFade + (bOn ? DeltaSeconds : -DeltaSeconds) / TORNADO_FADE_S, 0.0f, 1.0f);
	if (!FunnelISM)
	{
		return;
	}
	const bool bVisible = TornadoFade > 0.0f;
	if (FunnelISM->IsVisible() != bVisible)
	{
		FunnelISM->SetVisibility(bVisible);
		DebrisISM->SetVisibility(bVisible);
	}
	if (!bVisible)
	{
		return;
	}

	FunnelHeadingDeg += FunnelTurnDegS * DeltaSeconds;
	const float Rad = FMath::DegreesToRadians(FunnelHeadingDeg);
	FunnelPos += FVector2D(FMath::Cos(Rad), FMath::Sin(Rad)) * TORNADO_SPEED_MS * 100.0f * DeltaSeconds;
	FunnelSpin += DeltaSeconds;

	if (const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0))
	{
		const float DistM = FVector2D::Distance(FunnelPos, FVector2D(Player->GetActorLocation())) / 100.0f;
		if (bOn && DistM > TORNADO_RESPAWN_M)
		{
			SpawnFunnel();   // walked off the stage during a long (forced) hold
		}
		// Heavy visibility loss near the funnel (rain wrap + debris).
		VisibilityM = FMath::Lerp(VisibilityM,
			FMath::Lerp(TORNADO_VIS_M, VisibilityM, FMath::SmoothStep(0.0f, TORNADO_VIS_REACH_M, DistM)), TornadoFade);
	}
	FHitResult Hit;
	if (GetWorld()->LineTraceSingleByChannel(Hit, FVector(FunnelPos, 1.0e6), FVector(FunnelPos, -1.0e6), ECC_WorldStatic))
	{
		FunnelGroundZ = Hit.ImpactPoint.Z;
	}

	const FVector Base(FunnelPos, FunnelGroundZ);
	FunnelISM->SetWorldLocation(Base);
	DebrisISM->SetWorldLocation(Base);
	if (FunnelMID)
	{
		FunnelMID->SetScalarParameterValue(TEXT("Opacity"), FUNNEL_OPACITY * TornadoFade);
	}
	// Funnel: cylinders (1 m basic shape) widening upward; each segment
	// orbits the axis a little, phase-shifted by height, so the column twists.
	const float SegH = FUNNEL_HEIGHT_M / FUNNEL_SEGMENTS;
	for (int32 i = 0; i < FUNNEL_SEGMENTS; ++i)
	{
		const float F = (i + 0.5f) / FUNNEL_SEGMENTS;
		const float R = FMath::Lerp(FUNNEL_R_M[0], FUNNEL_R_M[1], F * F);
		const float A = FunnelSpin * 0.8f + i * 0.45f;
		const FVector Off(FMath::Cos(A) * FUNNEL_SWAY_M * F, FMath::Sin(A) * FUNNEL_SWAY_M * F, F * FUNNEL_HEIGHT_M);
		FunnelISM->UpdateInstanceTransform(i, FTransform(FQuat::Identity, Off * 100.0f,
			FVector(2.0f * R, 2.0f * R, SegH * 1.15f)), false, i == FUNNEL_SEGMENTS - 1, true);
	}
	// Debris ring: tumbling cubes orbiting the base (golden-ratio spread).
	for (int32 j = 0; j < DEBRIS_COUNT; ++j)
	{
		const float U1 = FMath::Frac(j * 0.618034f), U2 = FMath::Frac(j * 0.414214f), U3 = FMath::Frac(j * 0.732051f);
		const float OrbitM = FMath::Lerp(40.0f, 130.0f, U1);
		const float A = j * 2.4f + FunnelSpin * FMath::Lerp(0.5f, 1.1f, U3) * 60.0f / OrbitM;
		const FVector Off(FMath::Cos(A) * OrbitM, FMath::Sin(A) * OrbitM, FMath::Lerp(2.0f, 80.0f, U2 * U2));
		DebrisISM->UpdateInstanceTransform(j, FTransform(FRotator(FunnelSpin * 97.0f * U1, FunnelSpin * 131.0f * U2, 0.0f),
			Off * 100.0f, FVector(FMath::Lerp(0.6f, 2.5f, U3))), false, j == DEBRIS_COUNT - 1, true);
	}
}

void AODWeatherController::TickCover(float DeltaSeconds)
{
	// Straight up from the camera hits a tunnel bore (AODWallManager
	// "tunnel_*", BlockAll) or the road ribbon of an overpass (bridge decks
	// themselves don't collide, the ribbon on them does). 5 Hz, and 3 polls
	// in a row (~0.6 s) must disagree before it flips, so a sign gantry or a
	// tunnel mouth doesn't flicker the rain.
	constexpr float POLL_S = 0.2f;
	constexpr int32 VOTES = 3;
	CoverPollT += DeltaSeconds;
	if (CoverPollT < POLL_S)
	{
		return;
	}
	CoverPollT = FMath::Fmod(CoverPollT, POLL_S);
	bool bHit = false;
	if (const APlayerCameraManager* Cam = UGameplayStatics::GetPlayerCameraManager(this, 0))
	{
		FCollisionQueryParams Params(SCENE_QUERY_STAT(ODWeatherCover), true, UGameplayStatics::GetPlayerPawn(this, 0));
		const FVector Eye = Cam->GetCameraLocation();
		bHit = GetWorld()->LineTraceTestByChannel(Eye, Eye + FVector(0.0, 0.0, 3000.0), ECC_Visibility, Params);
	}
	CoverVotes = bHit == bCovered ? 0 : CoverVotes + 1;
	if (CoverVotes < VOTES)
	{
		return;
	}
	CoverVotes = 0;
	bCovered = bHit;
	UE_LOG(LogTemp, Display, TEXT("ODWeatherController: camera %s cover"), bCovered ? TEXT("under") : TEXT("out from"));
	for (TActorIterator<AODSeasonController> It(GetWorld()); It && bCovered; ++It)
	{
		It->HidePrecipFx();
	}
}

void AODWeatherController::SetIndoors(bool bOn)
{
	bIndoors = bOn;
	if (bOn)
	{
		LensDrops.Reset();
		if (LensISM)
		{
			LensISM->SetVisibility(false);
		}
	}
	// the garage may pause the world (pause menu > Garage): apply now, not on the next tick
	for (TActorIterator<AODSeasonController> It(GetWorld()); It && bOn; ++It)
	{
		It->HidePrecipFx();
	}
	if (APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0))
	{
		if (UODVehicleFX* Fx = Player->FindComponentByClass<UODVehicleFX>())
		{
			Fx->PushCarState();
		}
	}
}

void AODWeatherController::TickLens(float DeltaSeconds)
{
	// (Re)attach to the player's camera: first pawn, or a new one after a car swap.
	APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0);
	if (Player && (!LensCam.IsValid() || LensCam->GetOwner() != Player))
	{
		if (UCameraComponent* Cam = Player->FindComponentByClass<UCameraComponent>())
		{
			if (!LensISM)
			{
				UMaterialInterface* Mat = LoadObject<UMaterialInterface>(
					nullptr, TEXT("/Game/OpenDrive/Materials/M_LensDrop.M_LensDrop"));
				if (!Mat)
				{
					UE_LOG(LogTemp, Warning, TEXT("ODWeatherController: M_LensDrop missing (unreal/apply_lens_drop_material.py) - no on-lens drops"));
					LensCam = Cam;   // don't retry every tick
					return;
				}
				LensISM = NewObject<UInstancedStaticMeshComponent>(this);
				LensISM->SetStaticMesh(LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Plane.Plane")));
				LensISM->SetMaterial(0, Mat);
				LensISM->SetCollisionEnabled(ECollisionEnabled::NoCollision);
				LensISM->SetCastShadow(false);
				LensISM->SetAffectDynamicIndirectLighting(false);
				LensISM->TranslucencySortPriority = 100;   // over the falling pool and other translucency
				LensISM->SetNumCustomDataFloats(2);         // 0 = Kind (1 flake .. 0 water), 1 = Fade
				LensISM->RegisterComponent();
				TArray<FTransform> Xf;
				Xf.Init(FTransform(FQuat::Identity, FVector::ZeroVector, FVector::ZeroVector), FMath::Max(LensMaxDrops, 1));
				LensISM->AddInstances(Xf, false);
			}
			LensISM->AttachToComponent(Cam, FAttachmentTransformRules::SnapToTargetNotIncludingScale);
			LensCam = Cam;
			LensPrevCamLoc = Cam->GetComponentLocation();
		}
	}
	UCameraComponent* Cam = LensCam.Get();
	if (!Cam || !LensISM || LensISM->GetAttachParent() != Cam)
	{
		return;
	}

	// Camera speed from its own motion (the chase cam's, which is what the
	// airflow over the "lens" follows); a jump is a teleport, not speed.
	const FVector CamLoc = Cam->GetComponentLocation();
	const float RawMs = FVector::Dist(CamLoc, LensPrevCamLoc) / 100.0f / FMath::Max(DeltaSeconds, 1.0e-3f);
	LensPrevCamLoc = CamLoc;
	if (RawMs < LENS_TELEPORT_MS)
	{
		LensSpeedMs = FMath::Lerp(LensSpeedMs, RawMs, 1.0f - FMath::Exp(-4.0f * DeltaSeconds));
	}

	int32 VpW = 16, VpH = 9;
	if (const APlayerController* PC = Player ? Cast<APlayerController>(Player->GetController()) : nullptr)
	{
		PC->GetViewportSize(VpW, VpH);
	}
	const float HalfH = VpW > 0 && VpH > 0 ? (float)VpH / VpW : 9.0f / 16.0f;   // screen half-height, half-widths

	// Landings: none in the garage showroom (indoors / menus).
	const AODVehiclePawn* Car = Cast<AODVehiclePawn>(Player);
	const int32 Pool = LensISM->GetInstanceCount();
	if (PrecipIntensity > 0.02f && !bCovered && !(Car && Car->IsInShowroom()))   // covered: the drops already on run off
	{
		LensSpawnDebt += DeltaSeconds * LensDropsPerSecond * PrecipIntensity * (1.0f + LensSpeedMs / LENS_RATE_REF_MS);
		for (; LensSpawnDebt >= 1.0f; LensSpawnDebt -= 1.0f)
		{
			if (LensDrops.Num() >= Pool)
			{
				continue;
			}
			FLensDrop& D = LensDrops.AddDefaulted_GetRef();
			D.bSnow = FMath::FRand() < PrecipType;   // sleet = a mix
			D.Kind = D.bSnow ? 1.0f : 0.0f;
			D.Pos = FVector2D(FMath::FRandRange(-1.0f, 1.0f), FMath::FRandRange(-HalfH, HalfH));
			D.Bias = FVector2D(FMath::FRandRange(-0.15f, 0.15f), FMath::FRandRange(-0.15f, 0.15f));
			D.Radius = LensDropSize * FMath::FRandRange(0.5f, 1.4f);
			D.Life = D.bSnow ? LensSnowHoldSeconds + LensMeltSeconds + 0.5f * LensDropLifeSeconds
				: LensDropLifeSeconds * FMath::FRandRange(0.7f, 1.3f);
		}
	}
	else
	{
		LensSpawnDebt = 0.0f;
	}

	// Motion: airflow pushes radially outward (faster with speed and with
	// distance from center), crosswind pushes sideways, water sags. A flake
	// clings until it melts, then rides the flow like water.
	const FVector Right = Cam->GetRightVector();
	const FVector2D Wind = WindAtMs(CamLoc);
	const float WindX = (Wind.X * Right.X + Wind.Y * Right.Y) * LensWindDrift;
	const float Drift = LensDriftPerMs * LensSpeedMs;
	for (int32 i = LensDrops.Num() - 1; i >= 0; --i)
	{
		FLensDrop& D = LensDrops[i];
		D.Age += DeltaSeconds;
		if (D.bSnow)
		{
			D.Kind = 1.0f - FMath::Clamp((D.Age - LensSnowHoldSeconds) / FMath::Max(LensMeltSeconds, 0.01f), 0.0f, 1.0f);
		}
		const float Grip = FMath::Lerp(1.0f, LENS_SNOW_GRIP, D.Kind);
		D.Pos += ((D.Pos + D.Bias) * Drift + FVector2D(WindX, -LENS_SAG * (1.0f - D.Kind))) * (Grip * DeltaSeconds);
		if (D.Age >= D.Life || FMath::Abs(D.Pos.X) > 1.0f + D.Radius || FMath::Abs(D.Pos.Y) > HalfH + D.Radius)
		{
			LensDrops.RemoveAtSwap(i);
		}
	}

	const bool bVisible = LensDrops.Num() > 0;
	if (LensISM->IsVisible() != bVisible)
	{
		LensISM->SetVisibility(bVisible);
	}
	if (!bVisible)
	{
		return;
	}

	// Camera-local quads (X = view axis, Y = right, Z = up); the Plane faces
	// +Z, pitched 90 so it faces back at the camera. Unused slots scale to 0.
	const float HalfW = LENS_DISTANCE_CM * FMath::Tan(FMath::DegreesToRadians(Cam->FieldOfView * 0.5f));
	const FQuat Facing = FRotator(90.0f, 0.0f, 0.0f).Quaternion();
	for (int32 i = 0; i < Pool; ++i)
	{
		FTransform T(Facing, FVector::ZeroVector, FVector::ZeroVector);
		if (i < LensDrops.Num())
		{
			const FLensDrop& D = LensDrops[i];
			const float Fade = FMath::Min(D.Age / LENS_FADE_IN_S, 1.0f)
				* FMath::Clamp((D.Life - D.Age) / (LENS_FADE_OUT * D.Life), 0.0f, 1.0f);
			const float R = D.Radius * (D.bSnow ? FMath::Lerp(LENS_MELT_SHRINK, 1.0f, D.Kind) : 1.0f);
			T.SetLocation(FVector(LENS_DISTANCE_CM, D.Pos.X * HalfW, D.Pos.Y * HalfW));
			T.SetScale3D(FVector(2.0f * R * HalfW / 100.0f));   // Plane is 100 cm across
			LensISM->SetCustomDataValue(i, 0, D.Kind, false);
			LensISM->SetCustomDataValue(i, 1, Fade, false);
		}
		LensISM->UpdateInstanceTransform(i, T, false, i == Pool - 1, true);
	}
}

FVector2D AODWeatherController::WindAtMs(const FVector& WorldPos) const
{
	const float Rad = FMath::DegreesToRadians(WindDirDeg);
	FVector2D W = FVector2D(FMath::Cos(Rad), FMath::Sin(Rad)) * WindSpeedMs;
	if (TornadoFade > 0.0f)
	{
		// Rankine vortex (solid core, 1/r outside) faded to 0 at the reach,
		// cyclonic (CCW from above; Y = south) plus an inflow toward the core.
		const FVector2D D = (FVector2D(WorldPos) - FunnelPos) / 100.0f;
		const float R = D.Size();
		if (R > 1.0f && R < TORNADO_REACH_M)
		{
			const float Vt = TORNADO_VMAX_MS * (R < TORNADO_CORE_M ? R / TORNADO_CORE_M : TORNADO_CORE_M / R)
				* (1.0f - FMath::SmoothStep(0.5f * TORNADO_REACH_M, TORNADO_REACH_M, R)) * TornadoFade;
			const FVector2D Out = D / R;
			W += FVector2D(Out.Y, -Out.X) * Vt - Out * (Vt * TORNADO_INFLOW);
		}
	}
	return W;
}

bool AODWeatherController::ForceState(const FString& Name, bool bInstant)
{
	auto Snap = [this, bInstant]()
	{
		if (bInstant)
		{
			From = To;
			RampT = 1.0f;
			Wetness = To.Precip > 0.05f ? 1.0f : 0.0f;
			Puddles = FMath::Clamp(To.Precip * (1.0f - To.PrecipType) * PUDDLE_PER_RAIN, 0.0f, 1.0f);
			// 2026-10-09 (Taylor: Summit set to Summer was "still snowing in the
			// view"): an instant switch to weather that isn't snowing also drops
			// the lying snow instead of melting it at sim speed
			if (To.Precip * To.PrecipType <= 0.05f && !bSnowHeld)
			{
				RoadSnowCm = SideSnowCm = RoadChill = 0.0f;
			}
		}
	};
	if (Name.IsEmpty() || Name.Equals(TEXT("Auto"), ESearchCase::IgnoreCase))
	{
		bForced = false;
		UE_LOG(LogTemp, Display, TEXT("ODWeatherController: QA force released - random walk resumes"));
		Roll();
		Snap();
		return true;
	}
	for (int32 i = 0; i < NumStates; ++i)
	{
		if (StateName((EODWeatherState)i).Equals(Name, ESearchCase::IgnoreCase))
		{
			UE_LOG(LogTemp, Display, TEXT("ODWeatherController: QA force %s -> %s (held until od.Weather Auto)"),
				*StateName(State), *StateName((EODWeatherState)i));
			bForced = true;
			EnterState((EODWeatherState)i, 0.0f);
			Snap();
			return true;
		}
	}
	return false;
}

void AODWeatherController::Reroll()
{
	if (!bForced)
	{
		Roll();
	}
}

void AODWeatherController::ResumeAuto(EODWeatherState S)
{
	ForceState(StateName(S), /*bInstant*/true);
	bForced = false;
	HoldLeft = FMath::FRandRange(HOLD_MIN_S, HOLD_MAX_S);
}
