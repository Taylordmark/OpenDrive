#include "ODGaugeCluster.h"

#include "CanvasItem.h"
#include "Engine/Canvas.h"
#include "Engine/Font.h"
#include "GlobalRenderResources.h"

// Canvas triangles have no anti-aliasing, so every shape here carries its
// own ~1 px feather: an outer strip whose alpha fades to 0. Shading is
// per-vertex color (face/bezel/needle gradients, glare, inner shadow).
namespace
{
constexpr float Feather = 1.2f;   // px

const FLinearColor InkCol(0.95f, 0.95f, 0.91f, 1.0f);
const FLinearColor RedCol(0.88f, 0.08f, 0.04f, 1.0f);
const FLinearColor LcdCol(1.0f, 0.62f, 0.16f, 1.0f);   // amber segment glow

FLinearColor WithA(FLinearColor C, float A)
{
	C.A = A;
	return C;
}

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

// Annulus R0..R1 over angles A0..A1 (R0 = 0 -> disc), colored by
// Col(angle, t) with t 0 at R0 .. 1 at R1; both curved edges feathered.
// ponytail: the straight ends of a partial arc are not feathered - only the
// red band has visible ends; add an end strip if it ever reads as jagged.
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

void Disc(UCanvas* C, FVector2D M, float R, const FLinearColor& Center, const FLinearColor& Rim)
{
	Ring(C, M, 0.0f, R, 0.0f, 2.0f * PI, [&](float, float T) { return FMath::Lerp(Center, Rim, T); });
}

// Convex polygon, per-vertex colors, edges feathered outward by F px
// (a large F makes a soft glow / drop shadow).
void Poly(UCanvas* C, TArrayView<const FVector2D> P, TArrayView<const FLinearColor> Col, float F = Feather)
{
	const int32 N = P.Num();
	FVector2D Mid = FVector2D::ZeroVector;
	FLinearColor MidCol(0, 0, 0, 0);
	float Area = 0.0f;
	for (int32 i = 0; i < N; ++i)
	{
		Mid += P[i] / (float)N;
		MidCol += Col[i] / (float)N;
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
	TArray<FCanvasUVTri> Tris;
	Tris.Reserve(N * 3);
	for (int32 i = 0; i < N; ++i)
	{
		const int32 j = (i + 1) % N;
		AddTri(Tris, Mid, MidCol, P[i], Col[i], P[j], Col[j]);
		AddTri(Tris, P[i], Col[i], Out[i], WithA(Col[i], 0), Out[j], WithA(Col[j], 0));
		AddTri(Tris, P[i], Col[i], Out[j], WithA(Col[j], 0), P[j], Col[j]);
	}
	Flush(C, Tris);
}

// Rounded rect, vertical gradient Top -> Bot.
void RoundRect(UCanvas* C, FVector2D Mid, FVector2D Size, float Rad, const FLinearColor& Top,
	const FLinearColor& Bot, float F = Feather)
{
	Rad = FMath::Min(Rad, FMath::Min(Size.X, Size.Y) * 0.5f);
	const FVector2D H = Size * 0.5f - FVector2D(Rad, Rad);
	const FVector2D Corners[4] = {{H.X, -H.Y}, {H.X, H.Y}, {-H.X, H.Y}, {-H.X, -H.Y}};
	TArray<FVector2D> P;
	TArray<FLinearColor> Col;
	for (int32 c = 0; c < 4; ++c)
	{
		for (int32 k = 0; k <= 5; ++k)
		{
			const FVector2D Pt = Mid + Corners[c] + Dir(FMath::DegreesToRadians(90.0f * c + 18.0f * k)) * Rad;
			P.Add(Pt);
			Col.Add(FMath::Lerp(Top, Bot, FMath::Clamp((Pt.Y - (Mid.Y - Size.Y * 0.5f)) / Size.Y, 0.0f, 1.0f)));
		}
	}
	Poly(C, P, Col, F);
}

// Bar from radius Ra to Rb along D, Width wide.
void Spoke(UCanvas* C, FVector2D M, FVector2D D, float Ra, float Rb, float Width, const FLinearColor& Col)
{
	const FVector2D W = FVector2D(-D.Y, D.X) * Width * 0.5f;
	const FVector2D P[4] = {M + D * Ra - W, M + D * Rb - W, M + D * Rb + W, M + D * Ra + W};
	const FLinearColor Cs[4] = {Col, Col, Col, Col};
	Poly(C, P, Cs);
}

// Centered text, roughly PxSize tall (Slate sizes are points at 96 dpi).
void Text(UCanvas* C, const UFont* Font, FVector2D Mid, const FString& S, float PxSize, const FLinearColor& Col,
	bool bBold = false)
{
	FCanvasTextItem T(Mid, FText::FromString(S),
		FSlateFontInfo(Font, FMath::Max(6.0f, PxSize * 0.75f), bBold ? FName(TEXT("Bold")) : NAME_None), Col);
	T.bCentreX = true;
	T.bCentreY = true;
	C->DrawItem(T);
}

// Bezel, recessed face, red band, ticks every Minor (long + numbered every
// Major, number = value / LabelDiv), caption.
void DialFace(UCanvas* C, const UFont* Font, FVector2D M, float R, float MaxVal, float Major, float Minor,
	float RedFrom, float LabelDiv, const FString& Caption)
{
	// bezel: lit from above; inner lip lit from below so the face reads recessed
	Ring(C, M, R * 0.985f, R * 1.08f, 0.0f, 2.0f * PI, [](float A, float T)
	{
		const float K = 0.5f + 0.5f * FMath::Cos(A);
		return WithA(FMath::Lerp(FLinearColor(0.07f, 0.075f, 0.08f), FLinearColor(0.6f, 0.62f, 0.66f), K)
			* FMath::Lerp(1.0f, 0.55f, T), 1.0f);
	});
	Ring(C, M, R * 0.962f, R * 0.985f, 0.0f, 2.0f * PI, [](float A, float)
	{
		const float K = 0.5f - 0.5f * FMath::Cos(A);
		return FMath::Lerp(FLinearColor(0.1f, 0.1f, 0.11f), FLinearColor(0.7f, 0.72f, 0.75f), K);
	});
	Disc(C, M, R * 0.962f, FLinearColor(0.085f, 0.09f, 0.1f, 0.96f), FLinearColor(0.012f, 0.012f, 0.016f, 0.96f));
	Ring(C, M, R * 0.8f, R * 0.962f, 0.0f, 2.0f * PI, [](float A, float T)
	{
		return FLinearColor(0, 0, 0, T * (0.35f + 0.25f * FMath::Cos(A)));
	});

	if (RedFrom < MaxVal)
	{
		Ring(C, M, R * 0.82f, R * 0.91f, DialAngle(RedFrom / MaxVal), DialAngle(1.0f),
			[](float, float T) { return FMath::Lerp(FLinearColor(0.6f, 0.03f, 0.02f), RedCol, T); });
	}

	const int32 PerMajor = FMath::RoundToInt(Major / Minor);
	const int32 NumTicks = FMath::RoundToInt(MaxVal / Minor);
	for (int32 i = 0; i <= NumTicks; ++i)
	{
		const float V = i * Minor;
		const bool bMajor = i % PerMajor == 0;
		const FVector2D D = Dir(DialAngle(V / MaxVal));
		const FLinearColor& Col = V >= RedFrom ? RedCol : InkCol;
		Spoke(C, M, D, R * (bMajor ? 0.77f : 0.85f), R * 0.92f, bMajor ? R * 0.028f : R * 0.012f, Col);
		if (bMajor)
		{
			Text(C, Font, M + D * R * 0.63f, FString::FromInt(FMath::RoundToInt(V / LabelDiv)), R * 0.16f, Col, true);
		}
	}
	Text(C, Font, M + FVector2D(0.0f, -R * 0.32f), Caption, R * 0.09f, WithA(InkCol, 0.7f));
}

// Needle (soft shadow, tip-bright gradient), hub cap, then glass glare on top.
void DialNeedle(UCanvas* C, FVector2D M, float R, float Frac)
{
	const FVector2D D = Dir(DialAngle(Frac)), W(-D.Y, D.X);
	auto Shape = [&](FVector2D Off, FVector2D* P)
	{
		P[0] = M + Off + D * R * 0.9f + W * R * 0.005f;
		P[1] = M + Off - D * R * 0.17f + W * R * 0.032f;
		P[2] = M + Off - D * R * 0.17f - W * R * 0.032f;
		P[3] = M + Off + D * R * 0.9f - W * R * 0.005f;
	};
	FVector2D P[4];
	Shape(FVector2D(R * 0.025f, R * 0.035f), P);
	const FLinearColor Sh(0, 0, 0, 0.45f), Shs[4] = {Sh, Sh, Sh, Sh};
	Poly(C, P, Shs, R * 0.03f);
	Shape(FVector2D::ZeroVector, P);
	const FLinearColor Tip(1.0f, 0.55f, 0.15f), Base(0.7f, 0.2f, 0.02f);
	const FLinearColor Cs[4] = {Tip, Base, Base, Tip};
	Poly(C, P, Cs);

	Disc(C, M + FVector2D(R * 0.015f, R * 0.025f), R * 0.1f, FLinearColor(0, 0, 0, 0.4f), FLinearColor(0, 0, 0, 0));
	Ring(C, M, 0.0f, R * 0.09f, 0.0f, 2.0f * PI, [](float A, float)
	{
		return FMath::Lerp(FLinearColor(0.15f, 0.15f, 0.17f), FLinearColor(0.75f, 0.77f, 0.8f), 0.5f + 0.5f * FMath::Cos(A));
	});
	Disc(C, M, R * 0.055f, FLinearColor(0.12f, 0.12f, 0.13f), FLinearColor(0.05f, 0.05f, 0.06f));

	// glare: faint sheen across the upper glass, fading toward the center and the sides
	Ring(C, M, R * 0.35f, R * 0.95f, -1.2f, 1.2f, [](float A, float T)
	{
		return FLinearColor(1, 1, 1, 0.07f * T * FMath::Square(FMath::Cos(A * 1.25f)));
	});
}

// Inset amber LCD, one or two centered lines over unlit "8" ghost segments.
void Lcd(UCanvas* C, const UFont* Font, FVector2D Mid, FVector2D Size, const FString& A, const FString& B, float PxSize)
{
	RoundRect(C, Mid, Size + FVector2D(5.0f, 5.0f), PxSize * 0.35f,
		FLinearColor(0.04f, 0.04f, 0.045f), FLinearColor(0.35f, 0.36f, 0.38f));
	RoundRect(C, Mid, Size, PxSize * 0.25f, FLinearColor(0.025f, 0.018f, 0.005f), FLinearColor(0.07f, 0.05f, 0.015f));
	auto Line = [&](FVector2D At, const FString& S)
	{
		FString Ghost = S;
		for (TCHAR& Ch : Ghost)
		{
			Ch = FChar::IsDigit(Ch) ? TEXT('8') : Ch;   // Roboto digits are tabular, so this overlays exactly
		}
		Text(C, Font, At, Ghost, PxSize, WithA(LcdCol, 0.07f));
		Text(C, Font, At, S, PxSize, LcdCol);
	};
	if (B.IsEmpty())
	{
		Line(Mid, A);
		return;
	}
	Line(Mid - FVector2D(0.0f, Size.Y * 0.24f), A);
	Line(Mid + FVector2D(0.0f, Size.Y * 0.24f), B);
}

// Telltale: dark when off; lit fill, soft glow and dark legend when on.
void Lamp(UCanvas* C, const UFont* Font, FVector2D Mid, FVector2D Size, const TCHAR* Label, bool bOn,
	const FLinearColor& OnCol)
{
	const float Rad = Size.Y * 0.3f;
	RoundRect(C, Mid, Size + FVector2D(4.0f, 4.0f), Rad + 2.0f, FLinearColor(0.02f, 0.02f, 0.025f),
		FLinearColor(0.2f, 0.21f, 0.22f));
	if (bOn)
	{
		RoundRect(C, Mid, Size, Rad, WithA(OnCol, 0.35f), WithA(OnCol, 0.35f), Size.Y * 0.6f);
		RoundRect(C, Mid, Size, Rad, WithA(OnCol * 1.15f, 1.0f), WithA(OnCol * 0.75f, 1.0f));
	}
	else
	{
		RoundRect(C, Mid, Size, Rad, FLinearColor(0.06f, 0.06f, 0.065f), FLinearColor(0.03f, 0.03f, 0.035f));
	}
	Text(C, Font, Mid, Label, Size.Y * 0.62f,
		bOn ? FLinearColor(0.05f, 0.03f, 0.0f, 1.0f) : FLinearColor(0.2f, 0.2f, 0.21f, 1.0f), true);
}

// One housing behind both dials and the lamps: a stadium hull with a soft
// drop shadow, lighter along the top edge.
void Housing(UCanvas* C, FVector2D L, FVector2D Rt, float Rad)
{
	TArray<FVector2D> P;
	for (int32 k = 0; k <= 24; ++k)
	{
		P.Add(L + Dir(PI + PI * k / 24) * Rad);    // down -> left -> up
	}
	for (int32 k = 0; k <= 24; ++k)
	{
		P.Add(Rt + Dir(PI * k / 24) * Rad);        // up -> right -> down
	}
	TArray<FLinearColor> Shadow, Body;
	for (const FVector2D& Pt : P)
	{
		const float T = FMath::Clamp((Pt.Y - (L.Y - Rad)) / (2.0f * Rad), 0.0f, 1.0f);
		Shadow.Add(FLinearColor(0, 0, 0, 0.5f));
		Body.Add(FMath::Lerp(FLinearColor(0.11f, 0.115f, 0.125f, 0.97f), FLinearColor(0.02f, 0.02f, 0.025f, 0.97f), T));
	}
	Poly(C, P, Shadow, Rad * 0.12f);
	Poly(C, P, Body);
}
}

void ODGaugeCluster::Draw(UCanvas* Canvas, const UFont* Font, const FODGaugeData& D, double Now)
{
	if (!Canvas || !Font)
	{
		return;
	}
	const float R = FMath::Clamp(Canvas->SizeY * 0.12f, 70.0f, 180.0f);
	const float Y = Canvas->SizeY - R * 1.08f - 14.0f;
	const float MidX = Canvas->SizeX * 0.5f;
	const FVector2D TachM(MidX - R * 1.3f, Y), SpeedM(MidX + R * 1.3f, Y);

	Housing(Canvas, TachM, SpeedM, R * 1.17f);

	// ---- tach: x1000, red band from the round 500 below the limiter
	const float TachMax = FMath::CeilToFloat(D.MaxRpm / 1000.0f) * 1000.0f;
	const float RedFrom = FMath::FloorToFloat(D.RedlineRpm / 500.0f) * 500.0f;
	DialFace(Canvas, Font, TachM, R, TachMax, 1000.0f, 500.0f, RedFrom, 1000.0f, TEXT("x1000 r/min"));
	Lcd(Canvas, Font, TachM + FVector2D(0.0f, R * 0.56f), FVector2D(R * 0.3f, R * 0.26f),
		D.Gear < 0 ? TEXT("R") : FString::FromInt(D.Gear), FString(), R * 0.2f);
	DialNeedle(Canvas, TachM, R, D.Rpm / TachMax);

	// ---- speedo
	// dial ends on the major tick at or past the car's geared top speed,
	// never shorter than the old fixed 280 km/h / 160 mph face
	const float ToUnit = D.bMetric ? 0.036f : 0.0223694f;
	const float Major = D.bMetric ? 40.0f : 20.0f;
	const float SpeedMax = FMath::Max(D.bMetric ? 280.0f : 160.0f,
		FMath::CeilToFloat(D.TopSpeedCmS * ToUnit / Major) * Major);
	const float Speed = D.SpeedCmS * ToUnit;
	DialFace(Canvas, Font, SpeedM, R, SpeedMax, Major, 10.0f,
		1.0e9f, 1.0f, D.bMetric ? TEXT("km/h") : TEXT("MPH"));
	const FString Trip = D.bMetric
		? FString::Printf(TEXT("%.1f km"), D.TripCm / 100000.0)
		: FString::Printf(TEXT("%.1f mi"), D.TripCm / 160934.4);
	const FString Temp = !D.bHasAirTemp ? FString(TEXT("--\u00B0"))
		: D.bMetric ? FString::Printf(TEXT("%.0f\u00B0C"), D.AirTempC)
		: FString::Printf(TEXT("%.0f\u00B0F"), D.AirTempC * 1.8f + 32.0f);
	Lcd(Canvas, Font, SpeedM + FVector2D(0.0f, R * 0.58f), FVector2D(R * 0.56f, R * 0.3f), Trip, Temp, R * 0.11f);
	DialNeedle(Canvas, SpeedM, R, Speed / SpeedMax);

	// ---- telltales between the dials
	const FVector2D LampSize(R * 0.4f, R * 0.17f);
	const float Step = LampSize.Y + 7.0f;
	const FLinearColor Amber(1.0f, 0.6f, 0.05f, 1.0f);
	const bool bTcLit = D.bTcOff || (D.bTcActive && FMath::Frac(Now * 4.0) < 0.5);
	Lamp(Canvas, Font, FVector2D(MidX, Y - Step), LampSize, TEXT("ABS"), D.bAbsOff, Amber);
	Lamp(Canvas, Font, FVector2D(MidX, Y), LampSize, TEXT("TC"), bTcLit, Amber);
	Lamp(Canvas, Font, FVector2D(MidX, Y + Step), LampSize, TEXT("BRAKE"), D.bBrake, RedCol);
}
