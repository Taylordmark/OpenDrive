#include "ODGaugeCluster.h"

#include "CanvasItem.h"
#include "Engine/Canvas.h"
#include "Engine/Font.h"
#include "GlobalRenderResources.h"

namespace
{
const FLinearColor FaceCol(0.015f, 0.015f, 0.02f, 0.93f);
const FLinearColor BezelCol(0.16f, 0.17f, 0.18f, 1.0f);
const FLinearColor ChromeCol(0.62f, 0.64f, 0.67f, 1.0f);
const FLinearColor InkCol(0.94f, 0.94f, 0.9f, 1.0f);
const FLinearColor RedCol(0.85f, 0.07f, 0.04f, 1.0f);
const FLinearColor NeedleCol(1.0f, 0.4f, 0.04f, 1.0f);
const FLinearColor LcdCol(1.0f, 0.62f, 0.16f, 1.0f);   // amber segment glow
const FLinearColor LcdBgCol(0.05f, 0.035f, 0.01f, 1.0f);

// Dial angle: Frac 0 = lower-left (7:30), clockwise 270 deg to lower-right.
float DialAngle(float Frac)
{
	return FMath::DegreesToRadians(-135.0f + 270.0f * FMath::Clamp(Frac, 0.0f, 1.0f));
}

// Screen direction for an angle measured clockwise from straight up (y down).
FVector2D Dir(float A)
{
	return FVector2D(FMath::Sin(A), -FMath::Cos(A));
}

// Filled annulus R0..R1 between angles A0..A1 (R0 = 0 -> disc / pie).
void Ring(UCanvas* C, FVector2D M, float R0, float R1, float A0, float A1, const FLinearColor& Col)
{
	const int32 Segs = FMath::Max(4, FMath::CeilToInt(64.0f * (A1 - A0) / (2.0f * PI)));
	TArray<FCanvasUVTri> Tris;
	Tris.Reserve(Segs * 2);
	for (int32 i = 0; i < Segs; ++i)
	{
		const FVector2D D0 = Dir(A0 + (A1 - A0) * i / Segs), D1 = Dir(A0 + (A1 - A0) * (i + 1) / Segs);
		FCanvasUVTri T;
		T.V0_Pos = M + D0 * R0; T.V1_Pos = M + D0 * R1; T.V2_Pos = M + D1 * R1;
		Tris.Add(T);
		if (R0 > 0.0f)
		{
			T.V0_Pos = M + D0 * R0; T.V1_Pos = M + D1 * R1; T.V2_Pos = M + D1 * R0;
			Tris.Add(T);
		}
	}
	FCanvasTriangleItem Item(Tris, GWhiteTexture);
	Item.SetColor(Col);
	Item.BlendMode = SE_BLEND_Translucent;
	C->DrawItem(Item);
}

void Disc(UCanvas* C, FVector2D M, float R, const FLinearColor& Col)
{
	Ring(C, M, 0.0f, R, 0.0f, 2.0f * PI, Col);
}

void Rect(UCanvas* C, FVector2D Mid, FVector2D Size, const FLinearColor& Col)
{
	FCanvasTileItem T(Mid - Size * 0.5f, GWhiteTexture, Size, Col);
	T.BlendMode = SE_BLEND_Translucent;
	C->DrawItem(T);
}

void Line(UCanvas* C, FVector2D A, FVector2D B, float Thick, const FLinearColor& Col)
{
	FCanvasLineItem L(A, B);
	L.LineThickness = Thick;
	L.SetColor(Col);
	C->DrawItem(L);
}

// Centered text, roughly PxSize tall (Slate sizes are points at 96 dpi).
void Text(UCanvas* C, const UFont* Font, FVector2D Mid, const FString& S, float PxSize, const FLinearColor& Col)
{
	FCanvasTextItem T(Mid, FText::FromString(S), FSlateFontInfo(Font, FMath::Max(6.0f, PxSize * 0.75f)), Col);
	T.bCentreX = true;
	T.bCentreY = true;
	C->DrawItem(T);
}

// Bezel, face, red band, ticks every Minor (long + numbered every Major,
// number = value / LabelDiv), caption, needle at Value.
void Dial(UCanvas* C, const UFont* Font, FVector2D M, float R, float MaxVal, float Major, float Minor,
	float RedFrom, float LabelDiv, const FString& Caption, float Value)
{
	Disc(C, M, R * 1.07f, BezelCol);
	Ring(C, M, R * 0.985f, R * 1.01f, 0.0f, 2.0f * PI, ChromeCol);
	Disc(C, M, R * 0.985f, FaceCol);
	if (RedFrom < MaxVal)
	{
		Ring(C, M, R * 0.82f, R * 0.92f, DialAngle(RedFrom / MaxVal), DialAngle(1.0f), RedCol);
	}

	const int32 PerMajor = FMath::RoundToInt(Major / Minor);
	const int32 NumTicks = FMath::RoundToInt(MaxVal / Minor);
	for (int32 i = 0; i <= NumTicks; ++i)
	{
		const float V = i * Minor;
		const bool bMajor = i % PerMajor == 0;
		const FVector2D D = Dir(DialAngle(V / MaxVal));
		const FLinearColor& Col = V >= RedFrom ? RedCol : InkCol;
		Line(C, M + D * R * (bMajor ? 0.78f : 0.85f), M + D * R * 0.93f, bMajor ? R * 0.025f : R * 0.012f, Col);
		if (bMajor)
		{
			Text(C, Font, M + D * R * 0.63f, FString::FromInt(FMath::RoundToInt(V / LabelDiv)), R * 0.15f, Col);
		}
	}
	Text(C, Font, M + FVector2D(0.0f, -R * 0.32f), Caption, R * 0.09f, InkCol);

	// needle: tapered spike with a short tail, over a hub cap
	const float A = DialAngle(Value / MaxVal);
	const FVector2D D = Dir(A), P(-D.Y, D.X);
	FCanvasTriangleItem Needle(M + D * R * 0.9f, M - D * R * 0.16f + P * R * 0.03f,
		M - D * R * 0.16f - P * R * 0.03f, GWhiteTexture);
	Needle.SetColor(NeedleCol);
	Needle.BlendMode = SE_BLEND_Translucent;
	C->DrawItem(Needle);
	Disc(C, M, R * 0.085f, ChromeCol);
	Disc(C, M, R * 0.065f, BezelCol);
}

// Inset amber LCD with one or two centered lines.
void Lcd(UCanvas* C, const UFont* Font, FVector2D Mid, FVector2D Size, const FString& A, const FString& B, float PxSize)
{
	Rect(C, Mid, Size + FVector2D(4.0f, 4.0f), ChromeCol * FLinearColor(0.5f, 0.5f, 0.5f, 1.0f));
	Rect(C, Mid, Size, LcdBgCol);
	if (B.IsEmpty())
	{
		Text(C, Font, Mid, A, PxSize, LcdCol);
		return;
	}
	Text(C, Font, Mid - FVector2D(0.0f, Size.Y * 0.24f), A, PxSize, LcdCol);
	Text(C, Font, Mid + FVector2D(0.0f, Size.Y * 0.24f), B, PxSize, LcdCol);
}

// Telltale: dark when off, lit fill + dark legend when on.
void Lamp(UCanvas* C, const UFont* Font, FVector2D Mid, FVector2D Size, const TCHAR* Label, bool bOn,
	const FLinearColor& OnCol)
{
	Rect(C, Mid, Size + FVector2D(3.0f, 3.0f), BezelCol);
	Rect(C, Mid, Size, bOn ? OnCol : FLinearColor(0.05f, 0.05f, 0.05f, 0.93f));
	Text(C, Font, Mid, Label, Size.Y * 0.62f,
		bOn ? FLinearColor(0.05f, 0.03f, 0.0f, 1.0f) : FLinearColor(0.22f, 0.22f, 0.22f, 1.0f));
}
}

void ODGaugeCluster::Draw(UCanvas* Canvas, const UFont* Font, const FODGaugeData& D, double Now)
{
	if (!Canvas || !Font)
	{
		return;
	}
	const float R = FMath::Clamp(Canvas->SizeY * 0.12f, 70.0f, 180.0f);
	const float Y = Canvas->SizeY - R * 1.07f - 14.0f;
	const float MidX = Canvas->SizeX * 0.5f;
	const FVector2D TachM(MidX - R * 1.3f, Y), SpeedM(MidX + R * 1.3f, Y);

	// ---- tach: x1000, red band from the round 500 below the limiter
	const float TachMax = FMath::CeilToFloat(D.MaxRpm / 1000.0f) * 1000.0f;
	const float RedFrom = FMath::FloorToFloat(D.RedlineRpm / 500.0f) * 500.0f;
	Dial(Canvas, Font, TachM, R, TachMax, 1000.0f, 500.0f, RedFrom, 1000.0f, TEXT("x1000 r/min"), D.Rpm);
	Lcd(Canvas, Font, TachM + FVector2D(0.0f, R * 0.56f), FVector2D(R * 0.3f, R * 0.26f),
		D.Gear < 0 ? TEXT("R") : FString::FromInt(D.Gear), FString(), R * 0.2f);

	// ---- speedo
	const float Speed = D.SpeedCmS * (D.bMetric ? 0.036f : 0.0223694f);
	Dial(Canvas, Font, SpeedM, R, D.bMetric ? 280.0f : 160.0f, D.bMetric ? 40.0f : 20.0f, 10.0f,
		1.0e9f, 1.0f, D.bMetric ? TEXT("km/h") : TEXT("MPH"), Speed);
	const FString Trip = D.bMetric
		? FString::Printf(TEXT("%.1f km"), D.TripCm / 100000.0)
		: FString::Printf(TEXT("%.1f mi"), D.TripCm / 160934.4);
	const FString Temp = !D.bHasAirTemp ? FString(TEXT("--\u00B0"))
		: D.bMetric ? FString::Printf(TEXT("%.0f\u00B0C"), D.AirTempC)
		: FString::Printf(TEXT("%.0f\u00B0F"), D.AirTempC * 1.8f + 32.0f);
	Lcd(Canvas, Font, SpeedM + FVector2D(0.0f, R * 0.58f), FVector2D(R * 0.56f, R * 0.3f), Trip, Temp, R * 0.11f);

	// ---- telltales between the dials
	const FVector2D LampSize(R * 0.4f, R * 0.17f);
	const float Step = LampSize.Y + 6.0f;
	const FLinearColor Amber(1.0f, 0.6f, 0.05f, 1.0f);
	const bool bTcLit = D.bTcOff || (D.bTcActive && FMath::Frac(Now * 4.0) < 0.5);
	Lamp(Canvas, Font, FVector2D(MidX, Y - Step), LampSize, TEXT("ABS"), D.bAbsOff, Amber);
	Lamp(Canvas, Font, FVector2D(MidX, Y), LampSize, TEXT("TC"), bTcLit, Amber);
	Lamp(Canvas, Font, FVector2D(MidX, Y + Step), LampSize, TEXT("BRAKE"), D.bBrake, RedCol);
}
