#include "ODRaceHUD.h"

#include "Components/InputComponent.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/Font.h"
#include "Engine/Texture2D.h"
#include "EngineUtils.h"
#include "GameFramework/Pawn.h"
#include "GameFramework/PlayerController.h"
#include "ODGhost.h"
#include "ODMainMenu.h"
#include "ODPauseMenu.h"
#include "ODVehiclePawn.h"
#include "ODRaceManager.h"
#include "ODRoadManager.h"
#include "ODTuningManager.h"

static FString FormatTime(float T)
{
	const int32 Mins = FMath::FloorToInt(T / 60.0f);
	return FString::Printf(TEXT("%d:%05.2f"), Mins, T - Mins * 60.0f);
}

void AODRaceHUD::BeginPlay()
{
	Super::BeginPlay();
	if (APlayerController* PC = GetOwningPlayerController())
	{
		EnableInput(PC);
		if (InputComponent)
		{
			InputComponent->BindKey(EKeys::M, IE_Pressed, this,
				&AODRaceHUD::ToggleMiniMap);
			InputComponent->BindKey(EKeys::LeftBracket, IE_Pressed, this,
				&AODRaceHUD::ZoomOut);
			InputComponent->BindKey(EKeys::RightBracket, IE_Pressed, this,
				&AODRaceHUD::ZoomIn);
		}
	}
}

namespace
{
// AODRoadManager::MapCenterline (pipeline/export_road_centerline_bin.py's
// ODM1 sidecar, 2026-07-28) inserts an FVector2D(NAN, NAN) sentinel between
// segments so a multi-segment network doesn't get drawn/bbox-fit as one
// continuous polyline - see that array's own comment for the "connected
// unrelated streets across the map" bug this replaces. RoadCenterline
// (ODC1, no breaks) and Catmull-Rom'd gate polylines never contain NaNs, so
// this is always false for them - safe to call unconditionally.
bool IsCenterlineBreak(const FVector2D& P)
{
	return FMath::IsNaN(P.X) || FMath::IsNaN(P.Y);
}

// Catmull-Rom subdivision so the polyline reads as a smooth road instead of
// straight chords between gates spaced 300 m apart (the "grainy" look).
TArray<FVector2D> SmoothPolyline(const TArray<FTransform>& Gates)
{
	TArray<FVector2D> Pts;
	const int32 N = Gates.Num();
	const int32 Steps = 8;
	for (int32 i = 0; i < N - 1; ++i)
	{
		const FVector2D P0(Gates[FMath::Max(i - 1, 0)].GetLocation());
		const FVector2D P1(Gates[i].GetLocation());
		const FVector2D P2(Gates[i + 1].GetLocation());
		const FVector2D P3(Gates[FMath::Min(i + 2, N - 1)].GetLocation());
		for (int32 s = 0; s < Steps; ++s)
		{
			const float T = (float)s / Steps;
			const float T2 = T * T, T3 = T2 * T;
			Pts.Add(0.5f * ((2.0f * P1) + (-P0 + P2) * T
				+ (2.0f * P0 - 5.0f * P1 + 4.0f * P2 - P3) * T2
				+ (-P0 + 3.0f * P1 - 3.0f * P2 + P3) * T3));
		}
	}
	Pts.Add(FVector2D(Gates.Last().GetLocation()));
	return Pts;
}

// Whole-route bbox fit into a square panel - the exact transform
// DrawStageMap uses for its full-map (ZoomRadius<=0) branch, extracted so the
// town-node overlay can place/hit-test dots in the same screen space.
struct FMapFit
{
	FVector2D Center = FVector2D::ZeroVector;
	float Scale = 1.0f;
	FVector2D PanelMid = FVector2D::ZeroVector;
	FVector2D ToMap(const FVector2D& W) const { return PanelMid + (W - Center) * Scale; }
};

FMapFit ComputeFullMapFit(const TArray<FVector2D>& C,
	float PanelX, float PanelY, float PanelSize)
{
	FVector2D Lo(FLT_MAX, FLT_MAX), Hi(-FLT_MAX, -FLT_MAX);
	for (const FVector2D& P : C)
	{
		if (IsCenterlineBreak(P)) { continue; }
		Lo.X = FMath::Min(Lo.X, P.X); Lo.Y = FMath::Min(Lo.Y, P.Y);
		Hi.X = FMath::Max(Hi.X, P.X); Hi.Y = FMath::Max(Hi.Y, P.Y);
	}
	const float Pad = 14.0f;
	const float Span = FMath::Max(FMath::Max(Hi.X - Lo.X, Hi.Y - Lo.Y), 1.0f);
	FMapFit F;
	F.Scale = (PanelSize - 2.0f * Pad) / Span;
	F.Center = (Lo + Hi) * 0.5f;
	F.PanelMid = FVector2D(PanelX + PanelSize * 0.5f, PanelY + PanelSize * 0.5f);
	return F;
}
}

void AODRaceHUD::DrawStageMap(const TArray<FVector2D>* Centerline,
	const TArray<FTransform>* Gates, bool bRunning, int32 NextGate,
	float PanelX, float PanelY, float PanelSize, float BgAlpha,
	float ZoomRadiusM, bool bCircular, const AODRoadManager* RoadMgr) const
{
	const bool bHaveGates = Gates && Gates->Num() >= 2;
	const bool bHaveCenterline = Centerline && Centerline->Num() > 1;
	if ((!bHaveGates && !bHaveCenterline) || !Canvas)
	{
		return;
	}
	const APawn* Pawn = GetOwningPawn();

	// either fit the whole route (full map) or center on the player at a
	// fixed radius (mini-map zoom, [ / ] adjust)
	FVector2D Center;
	float Scale;
	const float Pad = 14.0f;
	if (ZoomRadiusM > 0.0f && Pawn)
	{
		Center = FVector2D(Pawn->GetActorLocation());
		Scale = (PanelSize * 0.5f - Pad) / (ZoomRadiusM * 100.0f);
	}
	else
	{
		FVector2D Lo(FLT_MAX, FLT_MAX), Hi(-FLT_MAX, -FLT_MAX);
		auto Grow = [&](const FVector2D& P)
		{
			Lo.X = FMath::Min(Lo.X, P.X); Lo.Y = FMath::Min(Lo.Y, P.Y);
			Hi.X = FMath::Max(Hi.X, P.X); Hi.Y = FMath::Max(Hi.Y, P.Y);
		};
		// prefer the full-density centerline for the bbox fit when we have
		// one - the gates are 300 m apart and can under-fit a route that
		// curves between them
		if (bHaveCenterline)
		{
			for (const FVector2D& P : *Centerline)
			{
				if (!IsCenterlineBreak(P)) { Grow(P); }
			}
		}
		else
		{
			for (const FTransform& G : *Gates) { Grow(FVector2D(G.GetLocation())); }
		}
		const float Span = FMath::Max(FMath::Max(Hi.X - Lo.X, Hi.Y - Lo.Y), 1.0f);
		Scale = (PanelSize - 2.0f * Pad) / Span;
		Center = (Lo + Hi) * 0.5f;
	}
	const FVector2D PanelMid(PanelX + PanelSize * 0.5f, PanelY + PanelSize * 0.5f);
	auto ToMap = [&](const FVector& W)
	{
		return PanelMid + (FVector2D(W) - Center) * Scale;
	};
	auto Dot = [&](const FVector2D& M, float R, const FLinearColor& C)
	{
		FCanvasTileItem T(M - FVector2D(R, R), FVector2D(2 * R, 2 * R), C);
		T.BlendMode = SE_BLEND_Translucent;
		Canvas->DrawItem(T);
	};
	auto InPanel = [&](const FVector2D& M)
	{
		return (M - PanelMid).SizeSquared() <= FMath::Square(PanelSize * 0.5f);
	};

	// Stylized terrain-color background (2026-07-26, pipeline/
	// build_map_texture.py + unreal/apply_map_texture.py) - water/grass/
	// forest/rock drawn behind the road line instead of a bare line on flat
	// black. Optional per RoadMgr, same convention as CenterlineFilePrefix -
	// null/unset just falls through to the original solid-color background.
	UTexture2D* MapTex = RoadMgr ? RoadMgr->MapTexture : nullptr;
	const bool bHaveMapTexture = MapTex && RoadMgr->MapTextureSpanCm > 0.0f;
	// dim overlay alpha when a texture IS present - just enough to keep the
	// route line/dots/labels readable over busy terrain colors; BgAlpha
	// (much higher, ~0.55-0.6) is reserved for the no-texture fallback below.
	const float TexDimAlpha = 0.22f;
	auto WorldToUV = [&](const FVector2D& W)
	{
		const FVector2D TexCenter = RoadMgr->MapTextureCenterCm;
		const float TexSpan = RoadMgr->MapTextureSpanCm;
		return FVector2D(FMath::Clamp((W.X - TexCenter.X) / TexSpan + 0.5f, 0.0f, 1.0f),
			FMath::Clamp((W.Y - TexCenter.Y) / TexSpan + 0.5f, 0.0f, 1.0f));
	};

	if (bCircular)
	{
		// filled disc via triangle fan - corners stay untouched/transparent,
		// so the 3D scene shows through instead of a square black tile
		const int32 Segs = 40;
		const float R = PanelSize * 0.5f;
		const FTexture* WhiteTex =
			Canvas->DefaultTexture ? Canvas->DefaultTexture->GetResource() : nullptr;
		if (bHaveMapTexture)
		{
			const FTexture* TexRes = MapTex->GetResource();
			const FVector2D CenterUV = WorldToUV(Center);
			for (int32 i = 0; i < Segs && TexRes; ++i)
			{
				const float A0 = 2.0f * PI * i / Segs, A1 = 2.0f * PI * (i + 1) / Segs;
				const FVector2D P0 = PanelMid + R * FVector2D(FMath::Cos(A0), FMath::Sin(A0));
				const FVector2D P1 = PanelMid + R * FVector2D(FMath::Cos(A1), FMath::Sin(A1));
				FCanvasTriangleItem TriItem(PanelMid, P0, P1,
					CenterUV, WorldToUV(Center + (P0 - PanelMid) / Scale),
					WorldToUV(Center + (P1 - PanelMid) / Scale), TexRes);
				TriItem.BlendMode = SE_BLEND_Translucent;
				Canvas->DrawItem(TriItem);
			}
		}
		for (int32 i = 0; i < Segs && WhiteTex; ++i)
		{
			const float A0 = 2.0f * PI * i / Segs, A1 = 2.0f * PI * (i + 1) / Segs;
			FCanvasTriangleItem TriItem(PanelMid,
				PanelMid + R * FVector2D(FMath::Cos(A0), FMath::Sin(A0)),
				PanelMid + R * FVector2D(FMath::Cos(A1), FMath::Sin(A1)),
				WhiteTex);
			TriItem.BlendMode = SE_BLEND_Translucent;
			TriItem.SetColor(FLinearColor(0, 0, 0, bHaveMapTexture ? TexDimAlpha : BgAlpha));
			Canvas->DrawItem(TriItem);
		}
	}
	else
	{
		if (bHaveMapTexture)
		{
			const FVector2D HalfWorld = FVector2D(PanelSize * 0.5f, PanelSize * 0.5f) / Scale;
			FCanvasTileItem Tex(FVector2D(PanelX, PanelY), MapTex->GetResource(),
				FVector2D(PanelSize, PanelSize), WorldToUV(Center - HalfWorld),
				WorldToUV(Center + HalfWorld), FLinearColor::White);
			Tex.BlendMode = SE_BLEND_Translucent;
			Canvas->DrawItem(Tex);
		}
		FCanvasTileItem Bg(FVector2D(PanelX, PanelY),
			FVector2D(PanelSize, PanelSize),
			FLinearColor(0, 0, 0, bHaveMapTexture ? TexDimAlpha : BgAlpha));
		Bg.BlendMode = SE_BLEND_Translucent;
		Canvas->DrawItem(Bg);
	}

	// real road centerline (pipeline/export_centerline_bin.py or
	// export_road_centerline_bin.py, 5 m stations) when available - the
	// Catmull-Rom guess between 300 m-apart gates could cut corners the
	// real road doesn't, and is the only option at all when there are no
	// gates (route-only levels). Skip segments fully outside the panel when
	// circular/zoomed so a tight zoom doesn't waste draws.
	const TArray<FVector2D> FallbackSmooth =
		(!bHaveCenterline && bHaveGates) ? SmoothPolyline(*Gates) : TArray<FVector2D>();
	const TArray<FVector2D>& Smooth = bHaveCenterline ? *Centerline : FallbackSmooth;
	for (int32 i = 1; i < Smooth.Num(); ++i)
	{
		// MapCenterline's NAN-sentinel segment breaks (see its own comment) -
		// skip the edge entirely rather than drawing a connector line between
		// two unrelated streets. RoadCenterline/gate polylines never contain
		// these, so this is a no-op cost for those callers.
		if (IsCenterlineBreak(Smooth[i - 1]) || IsCenterlineBreak(Smooth[i]))
		{
			continue;
		}
		FVector2D A = ToMap(FVector(Smooth[i - 1], 0));
		FVector2D B = ToMap(FVector(Smooth[i], 0));
		if (bCircular)
		{
			// clip the segment to the panel disc - the old both-endpoints-out
			// skip let a segment with one endpoint inside draw at full length,
			// streaking across the whole screen (live 2026-08-28)
			const float R = PanelSize * 0.5f;
			const FVector2D D = B - A;
			const float DD = D.SizeSquared();
			if (DD < KINDA_SMALL_NUMBER)
			{
				if (!InPanel(A)) { continue; }
			}
			else
			{
				// solve |A + t*D - PanelMid|^2 = R^2 for t, keep [t0,t1] within [0,1]
				const FVector2D F = A - PanelMid;
				const float Bq = 2.0f * FVector2D::DotProduct(F, D);
				const float Cq = F.SizeSquared() - R * R;
				const float Disc = Bq * Bq - 4.0f * DD * Cq;
				if (Disc < 0.0f) { continue; }
				const float SqrtDisc = FMath::Sqrt(Disc);
				const float T0 = FMath::Max((-Bq - SqrtDisc) / (2.0f * DD), 0.0f);
				const float T1 = FMath::Min((-Bq + SqrtDisc) / (2.0f * DD), 1.0f);
				if (T1 <= T0) { continue; }
				B = A + D * T1;
				A = A + D * T0;
			}
		}
		FCanvasLineItem Seg(A, B);
		Seg.SetColor(FLinearColor(0.55f, 0.65f, 0.8f, 0.9f));
		Seg.LineThickness = 3.0f;
		Canvas->DrawItem(Seg);
	}

	// start/finish/next-gate markers and the PB ghost need actual checkpoint
	// data - skipped entirely on route-only levels (no AODRaceManager)
	if (bHaveGates)
	{
		if (!bCircular || InPanel(ToMap((*Gates)[0].GetLocation())))
		{
			Dot(ToMap((*Gates)[0].GetLocation()), 4.0f, FLinearColor(0.3f, 1, 0.3f));
		}
		if (!bCircular || InPanel(ToMap(Gates->Last().GetLocation())))
		{
			Dot(ToMap(Gates->Last().GetLocation()), 4.0f, FLinearColor(1, 0.35f, 0.3f));
		}
		if (bRunning && Gates->IsValidIndex(NextGate))
		{
			const FVector2D M = ToMap((*Gates)[NextGate].GetLocation());
			if (!bCircular || InPanel(M))
			{
				Dot(M, 3.5f, FLinearColor(1, 1, 0.3f));
			}
		}

		// PB ghost (spectral replay) while racing
		if (bRunning)
		{
			for (TActorIterator<AODGhost> It(GetWorld()); It; ++It)
			{
				const FVector2D M = ToMap(It->GetActorLocation());
				if (!bCircular || InPanel(M))
				{
					Dot(M, 3.0f, FLinearColor(0.5f, 0.8f, 1, 0.8f));
				}
				break;
			}
		}
	}

	// player arrow (heading = world yaw; axes map straight through)
	if (Pawn)
	{
		const FVector2D P = ToMap(Pawn->GetActorLocation());
		const float Yaw = FMath::DegreesToRadians(Pawn->GetActorRotation().Yaw);
		const FVector2D Fwd(FMath::Cos(Yaw), FMath::Sin(Yaw));
		const FVector2D Side(-Fwd.Y, Fwd.X);
		const FVector2D Tri2[3] = {P + Fwd * 9.0f,
			P - Fwd * 5.0f + Side * 5.0f, P - Fwd * 5.0f - Side * 5.0f};
		for (int32 i = 0; i < 3; ++i)
		{
			FCanvasLineItem L(Tri2[i], Tri2[(i + 1) % 3]);
			L.SetColor(FLinearColor::White);
			L.LineThickness = 2.0f;
			Canvas->DrawItem(L);
		}
	}

	if (bCircular)
	{
		// bezel: a colored ring on top of everything, segment-drawn (Canvas
		// has no native circle primitive)
		const int32 Segs = 48;
		const float R = PanelSize * 0.5f - 1.5f;
		FVector2D Prev = PanelMid + R * FVector2D(1, 0);
		for (int32 i = 1; i <= Segs; ++i)
		{
			const float A = 2.0f * PI * i / Segs;
			const FVector2D Cur = PanelMid + R * FVector2D(FMath::Cos(A), FMath::Sin(A));
			FCanvasLineItem L(Prev, Cur);
			L.SetColor(FLinearColor(1.0f, 0.8f, 0.3f, 0.9f));
			L.LineThickness = 3.0f;
			Canvas->DrawItem(L);
			Prev = Cur;
		}
	}
}

void AODRaceHUD::DrawMainMenu(AODMainMenu* Menu, const TArray<FVector2D>* Centerline,
	const TArray<FTransform>* Gates, AODRoadManager* RoadMgr) const
{
	if (!Canvas || !Menu)
	{
		return;
	}
	UFont* Font = GEngine->GetLargeFont();

	FCanvasTileItem Dim(FVector2D(0, 0), FVector2D(Canvas->SizeX, Canvas->SizeY),
		FLinearColor(0, 0, 0, 0.55f));
	Dim.BlendMode = SE_BLEND_Translucent;
	Canvas->DrawItem(Dim);

	auto Text = [&](const FString& S, float X, float Y, float Sc, const FLinearColor& C)
	{
		FCanvasTextItem T(FVector2D(X, Y), FText::FromString(S), Font, C);
		T.Scale = FVector2D(Sc, Sc);
		T.EnableShadow(FLinearColor::Black);
		Canvas->DrawItem(T);
	};

	// ---- loading: waiting on IsGroundSnapped() after a teleport (2026-07-26),
	// PLUS AODTerrainManager/AODRoadManager's own initial-streaming settle
	// (2026-08-27, spawn-loading-screen task) - fully opaque so the player
	// never sees the car airborne/popping in, or the world visibly filling in
	// around it, while a fresh area's road/terrain tiles are still streaming.
	if (Menu->GetStage() == EODMenuStage::Loading)
	{
		FCanvasTileItem Black(FVector2D(0, 0), FVector2D(Canvas->SizeX, Canvas->SizeY),
			FLinearColor(0, 0, 0, 1.0f));
		Black.BlendMode = SE_BLEND_Translucent;
		Canvas->DrawItem(Black);

		const float CX = Canvas->SizeX * 0.5f;
		const float CY = Canvas->SizeY * 0.5f;

		// animated dots (0-3, ~2 per second) so a long hold still reads as
		// "working", not a frozen/hung screen
		const double Now = GetWorld() ? GetWorld()->GetTimeSeconds() : 0.0;
		const int32 NumDots = FMath::FloorToInt(FMath::Fmod(Now, 2.0) / 2.0 * 4.0);
		FString Dots;
		for (int32 i = 0; i < NumDots; ++i)
		{
			Dots += TEXT(".");
		}

		Text(TEXT("LOADING WORLD") + Dots, CX - 150.0f, CY - 30.0f, 2.2f, FLinearColor::White);

		int32 TerrainReady = 0, TerrainTotal = 0, RoadReady = 0, RoadTotal = 0;
		Menu->GetLoadingProgress(TerrainReady, TerrainTotal, RoadReady, RoadTotal);
		if (TerrainTotal > 0 || RoadTotal > 0)
		{
			const FString Progress = FString::Printf(TEXT("terrain %d/%d · roads %d/%d"),
				TerrainReady, TerrainTotal, RoadReady, RoadTotal);
			Text(Progress, CX - 110.0f, CY + 30.0f, 1.1f, FLinearColor(0.7f, 0.75f, 0.8f));
		}
		return;
	}

	// ---- car config: keyboard rows on the left, the spinning car behind ----
	// Palette restyled 2026-08 to match the new garage set (cool-white LED
	// hex ceiling, black walls, glossy dark floor) - colors/decoration only,
	// no row/logic/layout changes below. A dark translucent panel sits
	// behind the text block so it reads as part of the room instead of
	// floating text over whatever's rendering behind it; the selected-row
	// highlight stays a warm accent (now a cooled amber rather than gold) so
	// it still pops against the cool garage backdrop instead of blending in.
	if (Menu->GetStage() == EODMenuStage::CarConfig)
	{
		const bool bGarage = Menu->IsGarageMode();
		const float Mx = 90.0f;
		float My = Canvas->SizeY * 0.5f - 180.0f;

		// panel background - dark blue-black, matching the garage's black
		// walls, with a thin cool-cyan top/bottom rule for a hex-ceiling-ish
		// accent line rather than a plain flat rectangle.
		const float PanelW = 620.0f;
		const float PanelTop = My - 30.0f;
		const float PanelH = 44.0f * Menu->NumRows() + 150.0f;
		FCanvasTileItem Panel(FVector2D(Mx - 30.0f, PanelTop), FVector2D(PanelW, PanelH),
			FLinearColor(0.02f, 0.03f, 0.05f, 0.78f));
		Panel.BlendMode = SE_BLEND_Translucent;
		Canvas->DrawItem(Panel);
		FCanvasTileItem RuleTop(FVector2D(Mx - 30.0f, PanelTop), FVector2D(PanelW, 2.0f),
			FLinearColor(0.55f, 0.85f, 1.0f, 0.55f));
		RuleTop.BlendMode = SE_BLEND_Translucent;
		Canvas->DrawItem(RuleTop);
		FCanvasTileItem RuleBottom(FVector2D(Mx - 30.0f, PanelTop + PanelH - 2.0f), FVector2D(PanelW, 2.0f),
			FLinearColor(0.55f, 0.85f, 1.0f, 0.55f));
		RuleBottom.BlendMode = SE_BLEND_Translucent;
		Canvas->DrawItem(RuleBottom);

		Text(bGarage ? TEXT("GARAGE") : TEXT("SELECT YOUR CAR"), Mx, My, 2.2f,
			FLinearColor(0.75f, 0.9f, 1.0f));
		My += 80.0f;
		for (int32 Row = 0; Row < Menu->NumRows(); ++Row)
		{
			FString Label, Value;
			Menu->GetRow(Row, Label, Value);
			const bool bSel = Row == Menu->GetSelected();
			const FLinearColor Col = bSel
				? FLinearColor(1.0f, 0.75f, 0.4f) : FLinearColor(0.8f, 0.87f, 0.92f);
			Text((bSel ? TEXT("> ") : TEXT("  ")) + Label, Mx, My, 1.5f, Col);
			if (!Value.IsEmpty())
			{
				Text(FString::Printf(TEXT("< %s >"), *Value), Mx + 270.0f, My, 1.5f, Col);
			}
			My += 44.0f;
		}
		Text(bGarage
				? TEXT("arrows change    enter select    esc/Resume Driving to continue")
				: TEXT("arrows change    enter select    (Drive to pick a location)"),
			Mx, My + 24.0f, 1.0f, FLinearColor(0.5f, 0.62f, 0.7f));
		return;
	}

	// ---- map select: full route + clickable town nodes ----
	const float FullSize = FMath::Min(Canvas->SizeY - 170.0f, Canvas->SizeX * 0.6f);
	const float PanelX = (Canvas->SizeX - FullSize) * 0.5f;
	const float PanelY = (Canvas->SizeY - FullSize) * 0.5f + 12.0f;
	Text(TEXT("CHOOSE A START"), PanelX, PanelY - 48.0f, 1.8f, FLinearColor::White);

	// route polyline + player arrow (no gate markers - bRunning=false)
	DrawStageMap(Centerline, Gates, false, INDEX_NONE, PanelX, PanelY, FullSize,
		0.6f, 0.0f, false, RoadMgr);

	int32 Hover = INDEX_NONE;
	if (RoadMgr && Centerline && Centerline->Num() > 1)
	{
		const FMapFit Fit = ComputeFullMapFit(*Centerline, PanelX, PanelY, FullSize);
		float Mx = 0.0f, My = 0.0f;
		bool bMouse = false;
		if (APlayerController* PC = GetOwningPlayerController())
		{
			bMouse = PC->GetMousePosition(Mx, My);
		}
		const TArray<FTownNode>& Towns = RoadMgr->GetTownNodes();

		// nearest town to the cursor (always the closest node, per the design)
		float BestD2 = FLT_MAX;
		if (bMouse)
		{
			for (int32 i = 0; i < Towns.Num(); ++i)
			{
				const float D2 = FVector2D::DistSquared(Fit.ToMap(Towns[i].Pos),
					FVector2D(Mx, My));
				if (D2 < BestD2)
				{
					BestD2 = D2;
					Hover = i;
				}
			}
		}

		for (int32 i = 0; i < Towns.Num(); ++i)
		{
			const FVector2D S = Fit.ToMap(Towns[i].Pos);
			const bool bH = i == Hover;
			const float R = bH ? 6.0f : 3.5f;
			FCanvasTileItem Dot(S - FVector2D(R, R), FVector2D(2 * R, 2 * R),
				bH ? FLinearColor(1.0f, 0.85f, 0.3f) : FLinearColor(0.6f, 0.75f, 0.95f));
			Dot.BlendMode = SE_BLEND_Translucent;
			Canvas->DrawItem(Dot);
			if (bH)
			{
				Text(Towns[i].Name, S.X + 12.0f, S.Y - 10.0f, 1.2f,
					FLinearColor(1.0f, 0.9f, 0.5f));
			}
		}
	}
	Menu->SetHoveredTown(Hover);

	const FString Foot = (Hover != INDEX_NONE && RoadMgr)
		? FString::Printf(TEXT("click to start near %s"),
			*RoadMgr->GetTownNodes()[Hover].Name)
		: TEXT("hover a town, or press enter to start here");
	Text(Foot, PanelX, PanelY + FullSize + 16.0f, 1.1f, FLinearColor(0.85f, 0.9f, 0.85f));
	Text(TEXT("esc  back"), PanelX, PanelY + FullSize + 42.0f, 1.0f,
		FLinearColor(0.6f, 0.6f, 0.6f));
}

void AODRaceHUD::DrawHUD()
{
	Super::DrawHUD();
	if (!Canvas)
	{
		return;
	}

	// AODRaceManager only exists on stages with a race/checkpoint framework
	// (Tail of the Dragon) - nullptr on free-roam/open-world levels. Used to
	// gate this WHOLE function, which meant tuning toast, speed readout, and
	// the pause menu (none of which have anything to do with a race) also
	// silently never drew there (found 2026-07-20 investigating why none of
	// the Dragon's HUD features showed up on the open-world level). Only the
	// timer/gate-progress text genuinely needs an actual race and stays
	// gated below; the map now draws from AODRoadManager's route-only
	// centerline when there's no Race at all (see MapCenterline/MapGates).
	AODRaceManager* Race = nullptr;
	for (TActorIterator<AODRaceManager> It(GetWorld()); It; ++It)
	{
		Race = *It;
		break;
	}

	// AODRoadManager exists on any level with a road, race or not
	// (open-world levels included) - its own CenterlineFilePrefix, when set,
	// lets the map draw a route there even with no checkpoint race. Only
	// needed as a fallback when there's no Race to draw from instead.
	AODRoadManager* RoadMgr = nullptr;
	if (!Race)
	{
		for (TActorIterator<AODRoadManager> It(GetWorld()); It; ++It)
		{
			RoadMgr = *It;
			break;
		}
	}
	// Prefer RoadMgr's map-only simplified/segment-safe centerline (see its
	// own comment) over the raw driving centerline when both exist - this is
	// what actually fixes the Oahu "connects unrelated streets + ~780,000
	// line draws a frame" map lag. Race (Tail of the Dragon) has no such
	// distinction - a single small loop was never in trouble either way.
	const TArray<FVector2D>* MapCenterline = Race ? &Race->RoadCenterline
		: (RoadMgr && RoadMgr->MapCenterline.Num() > 0) ? &RoadMgr->MapCenterline
		: (RoadMgr ? &RoadMgr->RoadCenterline : nullptr);
	const TArray<FTransform>* MapGates = Race ? &Race->Checkpoints : nullptr;

	// ---- front-end menu takes over the whole screen when active (car config
	// or the spawn-location map). Drawn instead of the in-game HUD, and only
	// on levels where AODMainMenu was placed (open-world); returns early so
	// the race/speed/mini-map/pause blocks below never fight it.
	for (TActorIterator<AODMainMenu> It(GetWorld()); It; ++It)
	{
		if (It->IsActive())
		{
			DrawMainMenu(*It, MapCenterline, MapGates, RoadMgr);
			return;
		}
		break;
	}

	// road-name/city label (2026-07-23) - RoadMgr-only feature (see its own
	// class comment on PlaceLabelFilePrefix); empty on Race/Dragon levels
	// and on RoadMgr levels with the prefix unset, both of which just mean
	// "draw nothing" below, not an error.
	const FString PlaceLabelText = [&]() -> FString
	{
		if (!RoadMgr)
		{
			return FString();
		}
		const FString& R = RoadMgr->CurrentRoadLabel;
		const FString& C = RoadMgr->CurrentCityLabel;
		if (!R.IsEmpty() && !C.IsEmpty())
		{
			return R + TEXT("  ·  ") + C;
		}
		return R.IsEmpty() ? C : R;
	}();

	UFont* Font = GEngine->GetLargeFont();

	// draws PlaceLabelText just above a map panel's top edge - shared by the
	// mini-map and the full pause-screen map below; no-ops on an empty
	// label (RoadMgr missing, prefix unset, or no nearby label point yet).
	auto DrawPlaceLabel = [&](float PanelX, float PanelTopY)
	{
		if (PlaceLabelText.IsEmpty())
		{
			return;
		}
		FCanvasTextItem LabelItem(FVector2D(PanelX, PanelTopY - 26.0f),
			FText::FromString(PlaceLabelText), Font, FLinearColor(0.9f, 0.9f, 0.85f));
		LabelItem.EnableShadow(FLinearColor::Black);
		Canvas->DrawItem(LabelItem);
	};

	const float Now = GetWorld()->GetTimeSeconds();

	// ---- race timer / gate progress (needs an actual race)
	if (Race)
	{
		const float X = Canvas->SizeX * 0.5f - 90.0f;
		float Y = 30.0f;

		auto Line = [&](const FString& Text, const FLinearColor& Color, float Scale)
		{
			FCanvasTextItem Item(FVector2D(X, Y), FText::FromString(Text), Font, Color);
			Item.Scale = FVector2D(Scale, Scale);
			Item.EnableShadow(FLinearColor::Black);
			Canvas->DrawItem(Item);
			Y += 26.0f * Scale;
		};

		if (Race->bRunning)
		{
			Line(FormatTime(Race->ElapsedTime()), FLinearColor::White, 2.0f);
			Line(FString::Printf(TEXT("gate %d / %d"), Race->NextGate,
				Race->Checkpoints.Num()), FLinearColor(1, 1, 0.3f), 1.2f);
			if (Race->ElapsedTime() < 1.5f)
			{
				Line(TEXT("GO!"), FLinearColor(0.3f, 1, 0.3f), 2.4f);
			}
			else if (Now - Race->SplitShownAt < 3.0f)
			{
				// sector split vs best run: green = ahead, red = behind
				const bool bAhead = Race->SplitDelta <= 0.0f;
				Line(FString::Printf(TEXT("%+.2f"), Race->SplitDelta),
					bAhead ? FLinearColor(0.3f, 1, 0.3f) : FLinearColor(1, 0.35f, 0.3f),
					1.6f);
			}
		}
		else
		{
			Line(TEXT("cross the green gate to start"), FLinearColor(0.4f, 1, 0.4f), 1.2f);
			if (Race->LastTime > 0.0f)
			{
				Line(FString::Printf(TEXT("last  %s"), *FormatTime(Race->LastTime)),
					FLinearColor::White, 1.2f);
			}
		}
		if (Race->BestTime > 0.0f)
		{
			Line(FString::Printf(TEXT("best  %s"), *FormatTime(Race->BestTime)),
				FLinearColor(0.5f, 0.8f, 1), 1.2f);
		}
	}

	// ---- tuning preset toast (T to cycle) - only needs AODTuningManager
	for (TActorIterator<AODTuningManager> It(GetWorld()); It; ++It)
	{
		if (Now - It->SwapShownAt < 3.0f && !It->CurrentPresetName.IsEmpty())
		{
			FCanvasTextItem Toast(
				FVector2D(Canvas->SizeX * 0.5f - 60.0f, Canvas->SizeY - 130.0f),
				FText::FromString(It->CurrentPresetName), Font,
				FLinearColor(1.0f, 0.8f, 0.3f));
			Toast.Scale = FVector2D(1.6f, 1.6f);
			Toast.EnableShadow(FLinearColor::Black);
			Canvas->DrawItem(Toast);
		}
		break;
	}

	// ---- out-of-bounds countdown (2026-09-06) - warns while the car is past
	// AODVehiclePawn::OobDistCm, before the reset teleports it back. Centered
	// and big: the player needs to know WHY they're about to move.
	if (const AODVehiclePawn* Veh = Cast<AODVehiclePawn>(GetOwningPawn()))
	{
		const float Cd = Veh->GetOobCountdownSec();
		if (Cd >= 0.0f)
		{
			FCanvasTextItem Warn(
				FVector2D(Canvas->SizeX * 0.5f - 220.0f, Canvas->SizeY * 0.28f),
				FText::FromString(FString::Printf(
					TEXT("%s - RETURNING IN %.0f"),
					Veh->IsOobStuckReset() ? TEXT("STUCK") : TEXT("OFF ROAD"),
					FMath::CeilToFloat(Cd))),
				Font, FLinearColor(1.0f, 0.35f, 0.2f));
			Warn.Scale = FVector2D(2.2f, 2.2f);
			Warn.EnableShadow(FLinearColor::Black);
			Canvas->DrawItem(Warn);
		}
	}

	// ---- speed readout - only needs the owning pawn
	if (const APawn* Pawn = GetOwningPawn())
	{
		const float Mph = Pawn->GetVelocity().Size() * 0.02237f;
		FCanvasTextItem Speed(FVector2D(Canvas->SizeX - 200.0f, Canvas->SizeY - 80.0f),
			FText::FromString(FString::Printf(TEXT("%.0f mph"), Mph)),
			Font, FLinearColor::White);
		Speed.Scale = FVector2D(1.8f, 1.8f);
		Speed.EnableShadow(FLinearColor::Black);
		Canvas->DrawItem(Speed);
	}

	// ---- stage mini-map (Arc 4.2 v1; M toggles, hidden behind the pause
	// map) - draws from Race if a checkpoint race exists, else falls back to
	// AODRoadManager's route-only centerline (open-world levels); a
	// real open-world map (Arc 7.4's planned NA map view) is a separate,
	// bigger feature, not something this stage-shaped map can grow into
	// as-is.
	bool bMenuOpen = false;
	for (TActorIterator<AODPauseMenu> It(GetWorld()); It; ++It)
	{
		bMenuOpen = It->IsOpen();
		break;
	}
	if (bShowMiniMap && !bMenuOpen)
	{
		DrawPlaceLabel(24.0f, Canvas->SizeY - MiniMapSize - 24.0f);
		DrawStageMap(MapCenterline, MapGates, Race && Race->bRunning,
			Race ? Race->NextGate : INDEX_NONE, 24.0f,
			Canvas->SizeY - MiniMapSize - 24.0f, MiniMapSize, 0.55f,
			MiniMapZoomM, true, RoadMgr);
	}

	// ---- pause menu (stopgap shell; ESC toggles, AODPauseMenu owns state) -
	// only needs AODPauseMenu; the full stage map inside it draws from
	// whichever route source (Race or RoadMgr) is available, same as above.
	for (TActorIterator<AODPauseMenu> It(GetWorld()); It; ++It)
	{
		AODPauseMenu* Menu = *It;
		if (!Menu->IsOpen())
		{
			break;
		}

		FCanvasTileItem Dim(FVector2D(0, 0),
			FVector2D(Canvas->SizeX, Canvas->SizeY),
			FLinearColor(0, 0, 0, 0.55f));
		Dim.BlendMode = SE_BLEND_Translucent;
		Canvas->DrawItem(Dim);

		const float Mx = Canvas->SizeX * 0.5f - 220.0f;
		float My = Canvas->SizeY * 0.5f - 150.0f;

		FCanvasTextItem Title(FVector2D(Mx, My), FText::FromString(TEXT("PAUSED")),
			Font, FLinearColor::White);
		Title.Scale = FVector2D(2.2f, 2.2f);
		Title.EnableShadow(FLinearColor::Black);
		Canvas->DrawItem(Title);
		My += 70.0f;

		for (int32 Row = 0; Row < Menu->NumRows(); ++Row)
		{
			FString Label, Value;
			Menu->GetRow(Row, Label, Value);
			const bool bSel = Row == Menu->GetSelected();
			const FLinearColor Color = bSel
				? FLinearColor(1.0f, 0.8f, 0.3f) : FLinearColor(0.85f, 0.85f, 0.85f);

			FCanvasTextItem L(FVector2D(Mx, My),
				FText::FromString((bSel ? TEXT("> ") : TEXT("  ")) + Label),
				Font, Color);
			L.Scale = FVector2D(1.5f, 1.5f);
			L.EnableShadow(FLinearColor::Black);
			Canvas->DrawItem(L);

			if (!Value.IsEmpty())
			{
				FCanvasTextItem V(FVector2D(Mx + 220.0f, My),
					FText::FromString(FString::Printf(TEXT("< %s >"), *Value)),
					Font, Color);
				V.Scale = FVector2D(1.5f, 1.5f);
				V.EnableShadow(FLinearColor::Black);
				Canvas->DrawItem(V);
			}
			My += 42.0f;
		}

		FCanvasTextItem Hint(FVector2D(Mx, My + 20.0f),
			FText::FromString(TEXT("arrows change   enter select   esc resume")),
			Font, FLinearColor(0.6f, 0.6f, 0.6f));
		Hint.EnableShadow(FLinearColor::Black);
		Canvas->DrawItem(Hint);

		// full stage map beside the menu - DrawStageMap itself no-ops if
		// there's no route to show (both MapCenterline and MapGates unset)
		{
			const float FullSize = FMath::Min(380.0f, Canvas->SizeY - 160.0f);
			const float FullMapX = Canvas->SizeX * 0.5f + 80.0f;
			const float FullMapY = Canvas->SizeY * 0.5f - FullSize * 0.5f;
			DrawPlaceLabel(FullMapX, FullMapY);
			DrawStageMap(MapCenterline, MapGates, Race && Race->bRunning,
				Race ? Race->NextGate : INDEX_NONE, FullMapX, FullMapY, FullSize,
				0.55f, 0.0f, false, RoadMgr);
		}
		break;
	}
}
