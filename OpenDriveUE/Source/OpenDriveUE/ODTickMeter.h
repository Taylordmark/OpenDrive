#pragma once

#include "CoreMinimal.h"
#include "HAL/PlatformTime.h"

/** Game-thread cost of one manager's Tick (2026-10-22, item 3 of the
 *  area-load work): every 5 s with a frame over 2 ms it logs
 *  "<Name>: tick 5s total N ms, longest M ms, F frame(s) over 8 ms", so a
 *  live test shows which manager's streaming makes a hitch and how big.
 *  Use as a function-local static in Tick:
 *    static FODTickMeter Meter(TEXT("ODTerrainManager"));
 *    FODTickMeter::FScope MeterScope(Meter); */
struct FODTickMeter
{
	const TCHAR* Name;
	double WindowStart = 0.0, TotalMs = 0.0, MaxMs = 0.0;
	int32 Over8 = 0;

	explicit FODTickMeter(const TCHAR* InName) : Name(InName) {}

	void Add(double Ms)
	{
		const double Now = FPlatformTime::Seconds();
		if (WindowStart == 0.0)
		{
			WindowStart = Now;
		}
		TotalMs += Ms;
		MaxMs = FMath::Max(MaxMs, Ms);
		Over8 += Ms > 8.0;
		if (Now - WindowStart >= 5.0)
		{
			if (MaxMs > 2.0)
			{
				UE_LOG(LogTemp, Display, TEXT("%s: tick %.0fs total %.0f ms, longest %.1f ms, %d frame(s) over 8 ms"),
					Name, Now - WindowStart, TotalMs, MaxMs, Over8);
			}
			WindowStart = Now;
			TotalMs = MaxMs = 0.0;
			Over8 = 0;
		}
	}

	struct FScope
	{
		FODTickMeter& M;
		double T0 = FPlatformTime::Seconds();
		explicit FScope(FODTickMeter& InM) : M(InM) {}
		~FScope() { M.Add((FPlatformTime::Seconds() - T0) * 1000.0); }
	};
};
