#pragma once

#include <cmath>

/** Real sun position (2026-10-20, replaces the 6-to-18 sine day): NOAA's
 *  "General Solar Position Calculations" - Spencer (1971) series for the
 *  declination and equation of time. No refraction; within ~0.3 deg of
 *  NOAA's full calculator (tools/solar_check checks it). Plain C++ so that
 *  check compiles this header outside the engine. */
namespace ODSolar
{
	/** US daylight time for a game clock with no year: 2nd Sunday of March to
	 *  1st Sunday of November, taken as day 69 (Mar 10) to 307 (Nov 3).
	 *  ponytail: +-3 days at the switch; a real calendar if the game gets one. */
	inline bool IsUSDaylightTime(double DayOfYear)
	{
		return DayOfYear >= 69.0 && DayOfYear < 307.0;
	}

	/** Sun elevation (deg above the horizon) and compass azimuth (deg clockwise
	 *  from north) at LocalHour on a clock UtcOffset hours from UTC (daylight
	 *  time included, e.g. -7 for PDT). DayOfYear is 1-based, may be fractional. */
	inline void SunPosition(double LatDeg, double LonDeg, double DayOfYear, double LocalHour, double UtcOffset,
		double& OutElevationDeg, double& OutAzimuthDeg)
	{
		const double Pi = 3.14159265358979323846;
		const double D2R = Pi / 180.0;
		const double G = 2.0 * Pi / 365.0 * (DayOfYear - 1.0 + (LocalHour - UtcOffset - 12.0) / 24.0);
		const double EqTimeMin = 229.18 * (0.000075 + 0.001868 * std::cos(G) - 0.032077 * std::sin(G)
			- 0.014615 * std::cos(2.0 * G) - 0.040849 * std::sin(2.0 * G));
		const double Decl = 0.006918 - 0.399912 * std::cos(G) + 0.070257 * std::sin(G)
			- 0.006758 * std::cos(2.0 * G) + 0.000907 * std::sin(2.0 * G)
			- 0.002697 * std::cos(3.0 * G) + 0.00148 * std::sin(3.0 * G);
		const double SolarMin = LocalHour * 60.0 + EqTimeMin + 4.0 * LonDeg - 60.0 * UtcOffset;
		const double Ha = (SolarMin / 4.0 - 180.0) * D2R;   // hour angle, 0 at solar noon
		const double Lat = LatDeg * D2R;
		const double SinEl = std::sin(Lat) * std::sin(Decl) + std::cos(Lat) * std::cos(Decl) * std::cos(Ha);
		OutElevationDeg = std::asin(SinEl < -1.0 ? -1.0 : SinEl > 1.0 ? 1.0 : SinEl) / D2R;
		// from south, westward positive -> + 180 = from north
		OutAzimuthDeg = std::atan2(std::sin(Ha), std::cos(Ha) * std::sin(Lat) - std::tan(Decl) * std::cos(Lat)) / D2R + 180.0;
	}

	/** Local clock hour [0, 24) on DayOfYear when the sun crosses TargetDeg,
	 *  rising (morning) or setting. False if it never does that day (polar
	 *  day/night). Bisects the half day between solar midnight and noon,
	 *  where elevation is monotonic. */
	inline bool HourAtElevation(double LatDeg, double LonDeg, double DayOfYear, double UtcOffset, double TargetDeg,
		bool bRising, double& OutHour)
	{
		// solar noon on this clock, +-16 min equation of time is fine for the bracket
		const double Noon = 12.0 + UtcOffset - LonDeg / 15.0;
		double Lo = bRising ? Noon - 12.0 : Noon, Hi = bRising ? Noon : Noon + 12.0;
		const auto Above = [&](double H)
		{
			double El, Az;
			SunPosition(LatDeg, LonDeg, DayOfYear, H, UtcOffset, El, Az);
			return El > TargetDeg;
		};
		// rising: below at Lo, above at Hi; setting the other way round
		if (Above(Lo) == Above(Hi))
		{
			return false;
		}
		const bool bAboveLo = Above(Lo);
		for (int I = 0; I < 30; ++I)   // 24 h / 2^30: far under a second
		{
			const double Mid = 0.5 * (Lo + Hi);
			(Above(Mid) == bAboveLo ? Lo : Hi) = Mid;
		}
		OutHour = std::fmod(0.5 * (Lo + Hi) + 24.0, 24.0);
		return true;
	}
}
