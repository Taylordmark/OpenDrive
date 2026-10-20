#pragma once

#include "CoreMinimal.h"

class UCanvas;

/** One frame of what the dash shows - filled by AODRaceHUD::DrawHUD. */
struct FODGaugeData
{
	float SpeedCmS = 0.0f;       // unsigned
	float Rpm = 0.0f;
	float RedlineRpm = 7000.0f;  // red zone starts at the 500 below this
	float MaxRpm = 7000.0f;
	int32 Gear = 1;              // -1 = R, 0 = N
	bool bHasAirTemp = false;
	float AirTempC = 0.0f;
	double TripCm = 0.0;
	bool bAbsOff = false;
	bool bTcOff = false;
	bool bTcActive = false;      // flashes the TC tag
	bool bBrake = false;
	bool bMetric = false;
};

/**
 * Bottom-right arcade cluster (NFS Rivals / The Crew 2 look): big digital
 * speed inside a 270 deg rpm arc with a red zone and a needle tip riding it,
 * gear in a ring that flashes at the shift point, warning tags only when
 * something is on. No housing - a soft shadow pool keeps it readable. Pure
 * Canvas geometry, sized from the viewport height.
 */
namespace ODGaugeCluster
{
	void Draw(UCanvas* Canvas, const FODGaugeData& D, double Now);
}

/** Anti-aliased Canvas shapes (each edge carries a ~1 px alpha feather). */
namespace ODHudDraw
{
	/** Annulus R0..R1 over angles A0 < A1 (radians clockwise from up; R0 = 0 -> disc),
	 *  colored by Col(angle, t), t 0 at R0 .. 1 at R1. */
	void Ring(UCanvas* C, FVector2D M, float R0, float R1, float A0, float A1,
		TFunctionRef<FLinearColor(float, float)> Col);
	void Disc(UCanvas* C, FVector2D M, float R, const FLinearColor& Col);
	/** Convex polygon; edges feathered outward by F px (large F = soft shadow). */
	void Poly(UCanvas* C, TArrayView<const FVector2D> P, const FLinearColor& Col, float F = 1.2f);
}
