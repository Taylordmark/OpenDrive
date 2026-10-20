#include "ODGaugeCluster.h"

#include "CanvasItem.h"
#include "Engine/Canvas.h"
#include "GlobalRenderResources.h"
#include "ODHudStyle.h"

using namespace ODHud;
using namespace ODHudDraw;

// Canvas triangles have no anti-aliasing, so every shape here carries its
// own ~1 px feather: an outer strip whose alpha fades to 0.
namespace
{
constexpr float Feather = 1.2f;   // px

FLinearColor WithA(FLinearColor C, float A)
{
	C.A = A;
	return C;
}

// Arc angle: Frac 0 = lower-left (7:30), clockwise 270 deg to lower-right.
float DialAngle(float Frac)
{
	return FMath::DegreesToRadians(-135.0f + 270.0f * FMath::Clamp(Frac, 0.0f, 1.0f));
}

// Screen direction for an angle measured clockwise from straight up (y down).
FVector2D Dir(float A)
{
	return FVector2D(FMath::Sin(A), -FMath::Cos(A));
}

void AddTri(TArray<FCanvasUVTri>& Tris, FVector2D P0, const FLinearColor& C0, FVector2D P1,
	const FLinearColor& C1, FVector2D P2, const FLinearColor& C2)
{
	FCanvasUVTri& T = Tris.AddDefaulted_GetRef();
	T.V0_Pos = P0; T.V0_Color = C0;
	T.V1_Pos = P1; T.V1_Color = C1;
	T.V2_Pos = P2; T.V2_Color = C2;
}

void Flush(UCanvas* C, const TArray<FCanvasUVTri>& Tris)
{
	if (Tris.Num() > 0)
	{
		FCanvasTriangleItem Item(Tris, GWhiteTexture);
		Item.BlendMode = SE_BLEND_Translucent;
		C->DrawItem(Item);
	}
}

// Bar from radius Ra to Rb along D, Width wide.
void Spoke(UCanvas* C, FVector2D M, FVector2D D, float Ra, float Rb, float Width, const FLinearColor& Col)
{
	const FVector2D W = FVector2D(-D.Y, D.X) * Width * 0.5f;
	const FVector2D P[4] = {M + D * Ra - W, M + D * Rb - W, M + D * Rb + W, M + D * Ra + W};
	Poly(C, P, Col);
}

// Rounded-rect pill.
void Pill(UCanvas* C, FVector2D Mid, FVector2D Size, const FLinearColor& Col)
{
	const float Rad = Size.Y * 0.5f;
	const float H = FMath::Max(0.0f, Size.X * 0.5f - Rad);
	TArray<FVector2D> P;
	for (int32 k = 0; k <= 12; ++k)
	{
		P.Add(Mid + FVector2D(H, 0.0f) + Dir(PI * k / 12) * Rad);         // right cap, up -> down
	}
	for (int32 k = 0; k <= 12; ++k)
	{
		P.Add(Mid - FVector2D(H, 0.0f) + Dir(PI + PI * k / 12) * Rad);    // left cap, down -> up
	}
	Poly(C, P, Col);
}

// Centered Rajdhani text, PxSize tall, with a soft offset shadow.
void Text(UCanvas* C, FVector2D Mid, const FString& S, float PxSize, const FLinearColor& Col,
	EMenuWeight W = EMenuWeight::Bold)
{
	FCanvasTextItem T(Mid, FText::FromString(S), MenuFont(W, FMath::Max(6.0f, PxSize * 0.75f)), Col);
	T.bCentreX = true;
	T.bCentreY = true;
	const float Off = FMath::Max(1.0f, PxSize * 0.04f);
	T.EnableShadow(FLinearColor(0, 0, 0, 0.6f * Col.A), FVector2D(Off, Off));
	// HasValidText() wants a UFont even when the Slate font info draws: the
	// file-loaded Rajdhani has none, so without this no cluster text drew
	T.Font = GEngine->GetLargeFont();
	C->DrawItem(T);
}
}

namespace ODHudDraw
{
void Ring(UCanvas* C, FVector2D M, float R0, float R1, float A0, float A1,
	TFunctionRef<FLinearColor(float, float)> Col)
{
	const int32 Segs = FMath::Clamp(FMath::CeilToInt(R1 * (A1 - A0) / 4.0f), 8, 256);
	TArray<FCanvasUVTri> Tris;
	Tris.Reserve(Segs * 6);
	for (int32 i = 0; i < Segs; ++i)
	{
		const float Aa = A0 + (A1 - A0) * i / Segs, Ab = A0 + (A1 - A0) * (i + 1) / Segs;
		const FVector2D Da = Dir(Aa), Db = Dir(Ab);
		const FLinearColor Ia = Col(Aa, 0.0f), Ib = Col(Ab, 0.0f), Oa = Col(Aa, 1.0f), Ob = Col(Ab, 1.0f);
		AddTri(Tris, M + Da * R0, Ia, M + Da * R1, Oa, M + Db * R1, Ob);
		if (R0 > 0.0f)
		{
			AddTri(Tris, M + Da * R0, Ia, M + Db * R1, Ob, M + Db * R0, Ib);
			const float Rf = FMath::Max(0.0f, R0 - Feather);
			AddTri(Tris, M + Da * Rf, WithA(Ia, 0), M + Da * R0, Ia, M + Db * R0, Ib);
			AddTri(Tris, M + Da * Rf, WithA(Ia, 0), M + Db * R0, Ib, M + Db * Rf, WithA(Ib, 0));
		}
		const float Rf = R1 + Feather;
		AddTri(Tris, M + Da * R1, Oa, M + Da * Rf, WithA(Oa, 0), M + Db * Rf, WithA(Ob, 0));
		AddTri(Tris, M + Da * R1, Oa, M + Db * Rf, WithA(Ob, 0), M + Db * R1, Ob);
	}
	Flush(C, Tris);
}

void Disc(UCanvas* C, FVector2D M, float R, const FLinearColor& Col)
{
	Ring(C, M, 0.0f, R, 0.0f, 2.0f * PI, [&](float, float) { return Col; });
}

void Poly(UCanvas* C, TArrayView<const FVector2D> P, const FLinearColor& Col, float F)
{
	const int32 N = P.Num();
	FVector2D Mid = FVector2D::ZeroVector;
	float Area = 0.0f;
	for (int32 i = 0; i < N; ++i)
	{
		Mid += P[i] / (float)N;
		Area += FVector2D::CrossProduct(P[i], P[(i + 1) % N]);
	}
	const float S = Area > 0.0f ? 1.0f : -1.0f;
	auto EdgeN = [&](int32 i)   // outward normal of edge i -> i+1
	{
		const FVector2D D = (P[(i + 1) % N] - P[i]).GetSafeNormal();
		return FVector2D(D.Y, -D.X) * S;
	};
	TArray<FVector2D> Out;
	Out.SetNum(N);
	for (int32 i = 0; i < N; ++i)
	{
		const FVector2D N1 = EdgeN((i + N - 1) % N), N2 = EdgeN(i);
		FVector2D Miter = (N1 + N2) / FMath::Max(0.1f, 1.0f + FVector2D::DotProduct(N1, N2));
		Miter = Miter.GetClampedToMaxSize(3.0f);
		Out[i] = P[i] + Miter * F;
	}
	const FLinearColor Clear = WithA(Col, 0);
	TArray<FCanvasUVTri> Tris;
	Tris.Reserve(N * 3);
	for (int32 i = 0; i < N; ++i)
	{
		const int32 j = (i + 1) % N;
		AddTri(Tris, Mid, Col, P[i], Col, P[j], Col);
		AddTri(Tris, P[i], Col, Out[i], Clear, Out[j], Clear);
		AddTri(Tris, P[i], Col, Out[j], Clear, P[j], Col);
	}
	Flush(C, Tris);
}
}

void ODGaugeCluster::Draw(UCanvas* Canvas, const FODGaugeData& D, double Now)
{
	if (!Canvas)
	{
		return;
	}
	const float S = Canvas->SizeY / 1080.0f;
	const float R = 150.0f * S;   // rpm arc outer radius
	const FVector2D M(Canvas->SizeX - R * 1.3f - 40.0f * S, Canvas->SizeY - R * 1.12f - 28.0f * S);
	const FLinearColor White(1, 1, 1, 1);

	// soft shadow pool, not a housing: keeps white on bright sky/road legible
	Ring(Canvas, M, 0.0f, R * 1.35f, 0.0f, 2.0f * PI, [](float, float T)
	{
		return FLinearColor(0, 0, 0, 0.6f * (1.0f - T * T));
	});

	// ---- rpm arc: track, red zone, fill (white, red past the red line)
	const float TachMax = FMath::CeilToFloat(D.MaxRpm / 1000.0f) * 1000.0f;
	const float RedFrac = FMath::FloorToFloat(D.RedlineRpm / 500.0f) * 500.0f / TachMax;
	const float Frac = FMath::Clamp(D.Rpm / TachMax, 0.0f, 1.0f);
	const float R0 = R * 0.9f;
	auto Arc = [&](float F0, float F1, const FLinearColor& Col)
	{
		if (F1 > F0)
		{
			Ring(Canvas, M, R0, R, DialAngle(F0), DialAngle(F1), [&](float, float) { return Col; });
		}
	};
	Arc(0.0f, RedFrac, WithA(White, 0.16f));
	Arc(RedFrac, 1.0f, WithA(Danger, 0.4f));
	Arc(0.0f, FMath::Min(Frac, RedFrac), White);
	Arc(RedFrac, Frac, Danger);

	// ticks + x1000 numbers outside the arc
	for (int32 k = 0; k * 1000.0f <= TachMax + 1.0f; ++k)
	{
		const float F = k * 1000.0f / TachMax;
		const FVector2D Dk = Dir(DialAngle(F));
		const FLinearColor Col = F >= RedFrac ? Danger : WithA(White, 0.8f);
		Spoke(Canvas, M, Dk, R * 1.03f, R * 1.09f, 2.5f * S, Col);
		Text(Canvas, M + Dk * R * 1.22f, FString::FromInt(k), R * 0.14f, Col, EMenuWeight::SemiBold);
	}
	for (int32 k = 0; k * 500.0f < TachMax; ++k)   // half-thousand minors
	{
		if (k % 2)
		{
			Spoke(Canvas, M, Dir(DialAngle(k * 500.0f / TachMax)), R * 1.03f, R * 1.06f, 1.5f * S, WithA(White, 0.5f));
		}
	}

	// needle tip riding the arc
	const FVector2D Dn = Dir(DialAngle(Frac));
	Spoke(Canvas, M + FVector2D(1.5f, 2.0f) * S, Dn, R * 0.74f, R * 1.1f, 9.0f * S, FLinearColor(0, 0, 0, 0.5f));
	Spoke(Canvas, M, Dn, R * 0.74f, R * 1.1f, 7.0f * S, Accent);

	// ---- speed: the hero number
	const float Speed = D.SpeedCmS * (D.bMetric ? 0.036f : 0.0223694f);
	Text(Canvas, M + FVector2D(0.0f, -R * 0.08f), FString::FromInt(FMath::RoundToInt(Speed)), R * 0.6f, White);
	Text(Canvas, M + FVector2D(0.0f, R * 0.3f), D.bMetric ? TEXT("KM/H") : TEXT("MPH"), R * 0.16f, WithA(White, 0.7f),
		EMenuWeight::SemiBold);

	// ---- gear ring in the arc's bottom gap; fills and flashes at the shift point
	const FVector2D G = M + FVector2D(0.0f, R * 0.72f);
	const bool bShift = Frac >= RedFrac - 0.03f && D.Gear > 0;
	const bool bFlash = bShift && FMath::Frac(Now * 8.0) < 0.5;
	if (bFlash)
	{
		Disc(Canvas, G, R * 0.16f, Accent);
	}
	Ring(Canvas, G, R * 0.145f, R * 0.16f, 0.0f, 2.0f * PI, [&](float, float)
	{
		return bShift ? Accent : WithA(White, 0.75f);
	});
	const FString GearS = D.Gear < 0 ? TEXT("R") : D.Gear == 0 ? TEXT("N") : FString::FromInt(D.Gear);
	Text(Canvas, G, GearS, R * 0.2f, bFlash ? Ink : White);

	// ---- trip / outside temp under the gear
	const FString Trip = D.bMetric
		? FString::Printf(TEXT("%.1f km"), D.TripCm / 100000.0)
		: FString::Printf(TEXT("%.1f mi"), D.TripCm / 160934.4);
	const FString Temp = !D.bHasAirTemp ? FString()
		: D.bMetric ? FString::Printf(TEXT("    %.0f°C"), D.AirTempC)
		: FString::Printf(TEXT("    %.0f°F"), D.AirTempC * 1.8f + 32.0f);
	Text(Canvas, G + FVector2D(0.0f, R * 0.3f), Trip + Temp, R * 0.14f, WithA(White, 0.7f), EMenuWeight::SemiBold);

	// ---- warning tags, only while on, stacked up from the arc's lower-left end
	struct FTag { const TCHAR* Label; FLinearColor Col; };
	TArray<FTag, TInlineAllocator<4>> Tags;
	if (D.bBrake) { Tags.Add({TEXT("BRAKE"), Danger}); }
	if (D.bAbsOff) { Tags.Add({TEXT("ABS OFF"), Warn}); }
	if (D.bTcOff) { Tags.Add({TEXT("TC OFF"), Warn}); }
	else if (D.bTcActive && FMath::Frac(Now * 4.0) < 0.5) { Tags.Add({TEXT("TC"), Warn}); }
	const FVector2D TagSize(R * 0.46f, R * 0.14f);
	for (int32 i = 0; i < Tags.Num(); ++i)
	{
		const FVector2D At = M + FVector2D(-R * 1.0f - TagSize.X * 0.5f, R * 0.85f - i * TagSize.Y * 1.3f);
		Pill(Canvas, At, TagSize, Tags[i].Col);
		Text(Canvas, At, Tags[i].Label, TagSize.Y * 0.72f, Ink);
	}
}
