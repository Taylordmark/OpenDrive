#include "ODEnvironment.h"

#include "Containers/Ticker.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/CommandLine.h"
#include "ODDayNightController.h"
#include "ODSeasonController.h"
#include "ODWeatherController.h"

namespace
{
template <typename T>
T* Find(const UWorld* World)
{
	if (World)
	{
		for (TActorIterator<T> It(World); It; ++It)
		{
			return *It;
		}
	}
	return nullptr;
}

// CycleSeconds = real seconds per 24 h; 14400 (1 h per 10 min) is AODDayNightController's default
struct FSpeed
{
	const TCHAR* Name;
	float CycleSeconds;
};
const FSpeed Speeds[] = {
	{TEXT("Frozen"), 0.0f}, {TEXT("Real time"), 86400.0f}, {TEXT("Default"), 14400.0f},
	{TEXT("1 h per min"), 1440.0f}, {TEXT("Time-lapse"), 60.0f},
};
constexpr int32 NumSpeeds = UE_ARRAY_COUNT(Speeds);

// SetSeasonLock names; index 0 = Auto (unlocked)
const TCHAR* const Seasons[] = {TEXT("Auto"), TEXT("Spring"), TEXT("Summer"), TEXT("Autumn"), TEXT("Winter")};
constexpr int32 NumSeasons = UE_ARRAY_COUNT(Seasons);

constexpr int32 NumWeather = (int32)EODWeatherState::Hurricane + 1;

FString WeatherName(EODWeatherState S)
{
	return StaticEnum<EODWeatherState>()->GetNameStringByValue((int64)S);
}

int32 SpeedIdx(const AODDayNightController* DN)
{
	for (int32 i = 0; i < NumSpeeds; ++i)
	{
		if (FMath::IsNearlyEqual(DN->CycleSeconds, Speeds[i].CycleSeconds, 1.0f))
		{
			return i;
		}
	}
	return INDEX_NONE;
}

int32 SeasonIdx(const AODSeasonController* Season)
{
	if (!Season->IsFrozen())
	{
		return 0;
	}
	// a lock sits mid-hold: winter mid-Jan (0.5), spring 3.5, summer 6.5, autumn 9.5
	static const int32 ByQuarter[4] = {4, 1, 2, 3};
	return ByQuarter[FMath::RoundToInt((Season->MonthOfYear() - 0.5f) / 3.0f) % 4];
}

// 0 = Auto, else EODWeatherState + 1
int32 WeatherIdx(const AODWeatherController* W)
{
	return W->IsForced() ? (int32)W->State + 1 : 0;
}
}

void ODEnvironment::GetRow(const UWorld* World, int32 Row, FString& OutLabel, FString& OutValue)
{
	OutValue = TEXT("-");   // controller missing (non open-world level)
	switch (Row)
	{
	case RowTime:
		OutLabel = TEXT("Time of Day");
		if (const AODDayNightController* DN = Find<AODDayNightController>(World))
		{
			const int32 Min = FMath::RoundToInt(DN->TimeOfDay * 60.0f) % 1440;
			OutValue = FString::Printf(TEXT("%02d:%02d"), Min / 60, Min % 60);
		}
		break;
	case RowSpeed:
		OutLabel = TEXT("Time Speed");
		if (const AODDayNightController* DN = Find<AODDayNightController>(World))
		{
			const int32 I = SpeedIdx(DN);
			OutValue = I == INDEX_NONE ? FString::Printf(TEXT("%.0f s / day"), DN->CycleSeconds) : Speeds[I].Name;
		}
		break;
	case RowSeason:
		OutLabel = TEXT("Season");
		if (const AODSeasonController* Season = Find<AODSeasonController>(World))
		{
			OutValue = Seasons[SeasonIdx(Season)];
		}
		break;
	case RowWeather:
		OutLabel = TEXT("Weather");
		if (const AODWeatherController* W = Find<AODWeatherController>(World))
		{
			OutValue = W->IsForced() ? WeatherName(W->State) : FString::Printf(TEXT("Auto (%s)"), *WeatherName(W->State));
		}
		break;
	default:
		OutLabel.Empty();
		OutValue.Empty();
		break;
	}
}

void ODEnvironment::Adjust(UWorld* World, int32 Row, int32 Dir)
{
	switch (Row)
	{
	case RowTime:
		if (AODDayNightController* DN = Find<AODDayNightController>(World))
		{
			// snap to the quarter-hour grid, then step
			const int32 Q = (FMath::RoundToInt(DN->TimeOfDay * 4.0f) + Dir + 96) % 96;
			DN->TimeOfDay = Q * 0.25f;
		}
		break;
	case RowSpeed:
		if (AODDayNightController* DN = Find<AODDayNightController>(World))
		{
			const int32 I = SpeedIdx(DN);
			DN->CycleSeconds = Speeds[((I == INDEX_NONE ? 0 : I) + Dir + NumSpeeds) % NumSpeeds].CycleSeconds;
		}
		break;
	case RowSeason:
		if (AODSeasonController* Season = Find<AODSeasonController>(World))
		{
			const int32 I = (SeasonIdx(Season) + Dir + NumSeasons) % NumSeasons;
			Season->SetSeasonLock(I == 0 ? FString() : FString(Seasons[I]));   // no-op under -odseason= (QA wins)
		}
		break;
	case RowWeather:
		if (AODWeatherController* W = Find<AODWeatherController>(World))
		{
			// Auto plus the states this climate can produce (the garage's WeatherPossible filter)
			int32 I = WeatherIdx(W);
			do
			{
				I = (I + Dir + NumWeather + 1) % (NumWeather + 1);
			} while (I != 0 && !W->CanProduce((EODWeatherState)(I - 1)));
			W->ForceState(I == 0 ? FString(TEXT("Auto")) : WeatherName((EODWeatherState)(I - 1)), /*bInstant*/true);
		}
		break;
	default:
		break;
	}
}

void ODEnvironment::MaybeStartSelfCheck(UWorld* World)
{
	float At = -1.0f;
	if (!World || !FParse::Value(FCommandLine::Get(), TEXT("odenvcheck="), At) || At <= 0.0f)
	{
		return;
	}
	// one step per real second: 0 pause + scrub to 12:15 / Clear, 1 record and
	// scrub to 23:00 / Overcast, 2 check the paused tick showed it with the
	// clock held, then unpause, 3 check the clock runs again
	TWeakObjectPtr<UWorld> WeakWorld(World);
	const FTickerDelegate Steps = FTickerDelegate::CreateLambda(
		[WeakWorld, Step = 0, Cloud1 = 0.0f, Fails = FString()](float) mutable
		{
			UWorld* W = WeakWorld.Get();
			AODDayNightController* DN = Find<AODDayNightController>(W);
			AODWeatherController* Weather = Find<AODWeatherController>(W);
			if (!DN || !Weather)
			{
				UE_LOG(LogTemp, Display, TEXT("ODEnvCheck: FAIL (no DayNight/Weather controller)"));
				return false;
			}
			auto Expect = [&Fails](bool bOk, const FString& What)
			{
				UE_LOG(LogTemp, Display, TEXT("ODEnvCheck:   %s %s"), bOk ? TEXT("ok  ") : TEXT("FAIL"), *What);
				Fails += bOk ? TEXT("") : What + TEXT("; ");
			};
			FString L, V;
			switch (Step++)
			{
			case 0:
				UGameplayStatics::SetGamePaused(W, true);
				DN->TimeOfDay = 12.0f;
				ODEnvironment::Adjust(W, RowTime, +1);
				Weather->ForceState(TEXT("Clear"), true);
				return true;
			case 1:
				Expect(FMath::IsNearlyEqual(DN->TimeOfDay, 12.25f, 1e-4f),
					FString::Printf(TEXT("clock held at 12:15 while paused (%.4f)"), DN->TimeOfDay));
				Cloud1 = Weather->CloudCover;
				DN->TimeOfDay = 22.75f;
				ODEnvironment::Adjust(W, RowTime, +1);
				Weather->ForceState(TEXT("Overcast"), true);
				return true;
			case 2:
				ODEnvironment::GetRow(W, RowTime, L, V);
				Expect(V == TEXT("23:00"), FString::Printf(TEXT("time row reads 23:00 (%s)"), *V));
				Expect(FMath::IsNearlyEqual(DN->TimeOfDay, 23.0f, 1e-4f),
					FString::Printf(TEXT("clock held at 23:00 while paused (%.4f)"), DN->TimeOfDay));
				Expect(DN->GetNight() > 0.5f && DN->bHeadlightsOn,
					FString::Printf(TEXT("sun/night visuals followed the paused scrub (night %.2f, lights %d)"),
						DN->GetNight(), DN->bHeadlightsOn));
				Expect(Weather->CloudCover > Cloud1,
					FString::Printf(TEXT("weather visuals followed the paused force (cloud %.2f -> %.2f)"),
						Cloud1, Weather->CloudCover));
				ODEnvironment::GetRow(W, RowWeather, L, V);
				Expect(V == TEXT("Overcast"), FString::Printf(TEXT("weather row reads Overcast (%s)"), *V));
				UGameplayStatics::SetGamePaused(W, false);
				return true;
			default:
				Expect(DN->TimeOfDay > 23.0f || DN->CycleSeconds <= 0.0f,
					FString::Printf(TEXT("clock runs again after unpause (%.4f)"), DN->TimeOfDay));
				UE_LOG(LogTemp, Display, TEXT("ODEnvCheck: %s %s"), Fails.IsEmpty() ? TEXT("PASS") : TEXT("FAIL"), *Fails);
				return false;
			}
		});
	// a one-shot at At registers the 1 s stepper
	FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateLambda([Steps](float)
	{
		FTSTicker::GetCoreTicker().AddTicker(Steps, 1.0f);
		return false;
	}), At);
}
