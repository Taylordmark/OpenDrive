#pragma once

#include "CoreMinimal.h"

class UCanvas;
class UFont;

/** One frame of what the dash shows - filled by AODRaceHUD::DrawHUD. */
struct FODGaugeData
{
	float SpeedCmS = 0.0f;       // unsigned
	float Rpm = 0.0f;
	float RedlineRpm = 7000.0f;  // red band starts at the 500 below this
	float MaxRpm = 7000.0f;
	int32 Gear = 1;              // -1 = R
	bool bHasAirTemp = false;
	float AirTempC = 0.0f;
	double TripCm = 0.0;
	bool bAbsOff = false;
	bool bTcOff = false;
	bool bTcActive = false;      // flashes the TC lamp
	bool bBrake = false;
	bool bMetric = false;
};

/**
 * Bottom-center analog cluster: tach (left, gear LCD) and speedo (right,
 * trip + outside-temp LCD), ABS/TC/BRAKE lamps between them. Pure Canvas
 * geometry - no textures or editor assets. Needles read the 120 Hz sim
 * directly; no extra smoothing.
 */
namespace ODGaugeCluster
{
	void Draw(UCanvas* Canvas, const UFont* Font, const FODGaugeData& D, double Now);
}
