#include "ODWeatherController.h"

#include "Dom/JsonObject.h"
#include "EngineUtils.h"
#include "HAL/IConsoleManager.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "ODForestManager.h"
#include "ODRoadManager.h"
#include "ODSeasonController.h"
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
	constexpr float BIOME_MULT[14][3] = {
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
	};
	constexpr float NEUTRAL_MULT[3] = { 1.0f, 1.0f, 1.0f };
	static_assert(UE_ARRAY_COUNT(BIOME_MULT) == (int32)EODBiome::RockyFoothill + 1, "one row per EODBiome");

	// Per-state scalar targets (not mirrored - the sim only needs states):
	// { PrecipIntensity, PrecipType, Wind m/s, Visibility m, CloudCover, Dust }
	constexpr float STATE_TARGETS[NumStates][6] = {
		{ 0.0f,  0.0f,  3.0f, 20000.0f, 0.10f, 0.0f },   // Clear
		{ 0.0f,  0.0f,  5.0f, 12000.0f, 0.85f, 0.0f },   // Overcast
		{ 0.45f, 0.0f,  6.0f,  4000.0f, 0.90f, 0.0f },   // Rain
		{ 0.9f,  0.0f, 10.0f,  1200.0f, 1.00f, 0.0f },   // HeavyRain
		{ 0.5f,  0.5f,  7.0f,  2500.0f, 0.95f, 0.0f },   // Sleet
		{ 0.5f,  1.0f,  4.0f,  2000.0f, 0.95f, 0.0f },   // Snow
		{ 1.0f,  1.0f, 18.0f,   250.0f, 1.00f, 0.0f },   // Blizzard
		{ 0.0f,  0.0f,  1.0f,   250.0f, 0.70f, 0.0f },   // Fog
		{ 0.0f,  0.0f, 16.0f,   600.0f, 0.30f, 1.0f },   // DustStorm
		{ 0.85f, 0.0f, 12.0f,  1500.0f, 1.00f, 0.0f },   // Thunderstorm
		{ 0.7f,  0.0f, 25.0f,   800.0f, 1.00f, 0.3f },   // Tornado
		{ 1.0f,  0.0f, 35.0f,   300.0f, 1.00f, 0.0f },   // Hurricane
	};

	/** Linear between month centers (i + 0.5) - sim_weather.py's monthly(). */
	float Monthly(const float (&Vals)[12], float Month)
	{
		const float F = Month - 0.5f;
		const int32 I = FMath::FloorToInt(F);
		const float T = F - I;
		return FMath::Lerp(Vals[((I % 12) + 12) % 12], Vals[(((I + 1) % 12) + 12) % 12], T);
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
		     "Tornado|Hurricane|Auto> - QA: force and hold a weather state; Auto resumes the random walk"),
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
				UE_LOG(LogTemp, Display, TEXT("od.Weather: %s precip=%.2f type=%.2f wind=%.1f m/s vis=%.0f m wet=%.2f"),
					*StateName(Weather->State), Weather->PrecipIntensity, Weather->PrecipType,
					Weather->WindSpeedMs, Weather->VisibilityM, Weather->Wetness);
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
}

void AODWeatherController::LoadClimate()
{
	// Same AODRoadManager-derived area name as every AODGameMode::Ensure*
	// manager: "road_<area>_test" -> Content/OpenDrive/Masks/climate_<area>_test.json
	// (copied there by pipeline/fetch_climate.py, staged like the .bin sidecars).
	FString Area;
	for (TActorIterator<AODRoadManager> It(GetWorld()); It; ++It)
	{
		Area = It->RoadTilePrefix;
		Area.RemoveFromStart(TEXT("road_"));
		Area.RemoveFromEnd(TEXT("_test"));
		break;
	}
	const FString Path = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ FString::Printf(TEXT("climate_%s_test.json"), *Area);

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
		return;
	}
	for (int32 m = 0; m < 12; ++m)
	{
		Climate.MonthlyTempC[m] = (*Temps)[m]->AsNumber();
		Climate.MonthlyPrecipMm[m] = (*Precip)[m]->AsNumber();
	}
	const TSharedPtr<FJsonObject>* Storms = nullptr;
	if (Obj->TryGetObjectField(TEXT("storms"), Storms))
	{
		double V = 0.0;
		if ((*Storms)->TryGetNumberField(TEXT("hurricane"), V)) Climate.Hurricane = V;
		if ((*Storms)->TryGetNumberField(TEXT("tornado"), V)) Climate.Tornado = V;
		if ((*Storms)->TryGetNumberField(TEXT("dust_storm"), V)) Climate.DustStorm = V;
	}
	UE_LOG(LogTemp, Display, TEXT("ODWeatherController: climate '%s' storms hurr=%.2f torn=%.2f dust=%.2f"),
		*Path, Climate.Hurricane, Climate.Tornado, Climate.DustStorm);
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

	// Snow/sleet wet the road too (slush) - W3 decides grip per PrecipType.
	Wetness = PrecipIntensity > 0.05f
		? FMath::Min(Wetness + DeltaSeconds * PrecipIntensity / WetUpSeconds, 1.0f)
		: FMath::Max(Wetness - DeltaSeconds / DrySeconds, 0.0f);
}

void AODWeatherController::Roll()
{
	float Month = 6.5f;
	for (TActorIterator<AODSeasonController> It(GetWorld()); It; ++It) { Month = It->MonthOfYear(); break; }

	// Biome + elevation at the player (nearest classified biome point).
	// No point-biome data (Tail of the Dragon, Palmdale) -> neutral
	// multipliers and no lapse, same as sim_weather.py's None biome.
	const float* Mult = NEUTRAL_MULT;
	float ElevDeltaM = 0.0f;
	int32 BiomeIdx = -1;
	if (const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0))
	{
		for (TActorIterator<AODForestManager> It(GetWorld()); It; ++It)
		{
			EODBiome Biome;
			if (It->BiomeSampleAt(Player->GetActorLocation().X, Player->GetActorLocation().Y, Biome, ElevDeltaM))
			{
				BiomeIdx = (int32)Biome;
				Mult = BIOME_MULT[BiomeIdx];
			}
			break;
		}
	}

	const float T = Monthly(Climate.MonthlyTempC, Month) - LAPSE_C_PER_KM * ElevDeltaM / 1000.0f;
	const float P = Monthly(Climate.MonthlyPrecipMm, Month);
	const float Pw = FMath::Min(P / PRECIP_REF_MM, PRECIP_CAP) * Mult[0];
	const float Dry = FMath::Clamp(1.0f - P / DRY_MM, 0.0f, 1.0f);
	const float S = FMath::Clamp((RAIN_T_C - T) / (RAIN_T_C - SNOW_T_C), 0.0f, 1.0f);
	const float Warm = FMath::Clamp((T - THUNDER_T_C) / THUNDER_RAMP_C, 0.0f, 1.0f);
	const float RainFam = W_PRECIP * Pw * (1.0f - S) * (1.0f - S);
	const float SnowFam = W_PRECIP * Pw * S * S * SNOW_BOOST;

	float W[NumStates] = {};
	W[(int32)EODWeatherState::Clear] = W_CLEAR + W_CLEAR_DRY * Dry;
	W[(int32)EODWeatherState::Overcast] = W_OVERCAST + W_OVERCAST_WET * FMath::Min(Pw, 1.0f);
	W[(int32)EODWeatherState::Rain] = RainFam * RAIN_SPLIT[0];
	W[(int32)EODWeatherState::HeavyRain] = RainFam * RAIN_SPLIT[1];
	W[(int32)EODWeatherState::Thunderstorm] = RainFam * RAIN_SPLIT[2] * Warm;
	W[(int32)EODWeatherState::Sleet] = W_PRECIP * Pw * 2.0f * S * (1.0f - S);
	W[(int32)EODWeatherState::Snow] = SnowFam * SNOW_SPLIT[0];
	W[(int32)EODWeatherState::Blizzard] = SnowFam * SNOW_SPLIT[1];
	W[(int32)EODWeatherState::Fog] = W_FOG * Mult[1] * (0.5f + 0.5f * FMath::Min(Pw, 1.0f));
	W[(int32)EODWeatherState::DustStorm] = W_DUST * Climate.DustStorm * Dry * Mult[2];
	if (State == EODWeatherState::Thunderstorm)
	{
		W[(int32)EODWeatherState::Tornado] = W_TORNADO * Climate.Tornado
			* FMath::Clamp((T - TORNADO_T_C) / TORNADO_RAMP_C, 0.0f, 1.0f);
	}
	if (State != EODWeatherState::Tornado && State != EODWeatherState::Hurricane)
	{
		W[(int32)EODWeatherState::Hurricane] = W_HURRICANE * Climate.Hurricane
			* FMath::Clamp((T - HURRICANE_T_C) / HURRICANE_RAMP_C, 0.0f, 1.0f);
	}

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
	P.Visibility = VisibilityM; P.Cloud = CloudCover; P.Dust = DustAmount;
	return P;
}

void AODWeatherController::EnterState(EODWeatherState NewState, float HoldSeconds)
{
	const float* Tg = STATE_TARGETS[(int32)NewState];
	From = Published();
	To.Precip = Tg[0]; To.PrecipType = Tg[1]; To.Wind = Tg[2];
	To.Visibility = Tg[3]; To.Cloud = Tg[4]; To.Dust = Tg[5];
	// Wind direction wanders a little each roll instead of jumping.
	To.WindDir = FMath::UnwindDegrees(From.WindDir + FMath::FRandRange(-40.0f, 40.0f));
	// Precip type only ramps while something is falling - a Clear roll keeps
	// the last type so a snow->clear fade doesn't turn into rain on the way out.
	if (Tg[0] <= 0.0f)
	{
		To.PrecipType = From.PrecipType;
	}
	State = NewState;
	HoldLeft = HoldSeconds;
	RampT = 0.0f;
}

bool AODWeatherController::ForceState(const FString& Name)
{
	if (Name.IsEmpty() || Name.Equals(TEXT("Auto"), ESearchCase::IgnoreCase))
	{
		bForced = false;
		UE_LOG(LogTemp, Display, TEXT("ODWeatherController: QA force released - random walk resumes"));
		Roll();
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
