#include "ODRaceHUD.h"

#include "Components/InputComponent.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/Font.h"
#include "Engine/Texture2D.h"
#include "EngineUtils.h"
#include "GameFramework/Pawn.h"
#include "GlobalRenderResources.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/App.h"
#include "ODCreditsMenu.h"
#include "ODGhost.h"
#include "ODInputConfig.h"
#include "ODMainMenu.h"
#include "ODPauseMenu.h"
#include "ODVehiclePawn.h"
#include "ODRaceManager.h"
#include "ODRebindMenu.h"
#include "ODRoadManager.h"
#include "ODSettingsMenu.h"
#include "ODWeatherController.h"

// Canvas text at Scale > 1 stretches a glyph bitmap rasterized at the font's
// base size - blocky/blurry. Asking for the scaled point size instead makes
// the runtime font cache rasterize glyphs at that size, so text stays crisp.
static FSlateFontInfo CrispFont(const UFont* Font, float Scale)
{
	return FSlateFontInfo(Font, Font->LegacyFontSize * Scale);
}

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
		RefreshODManagedKeyBindings();
	}
}

void AODRaceHUD::RefreshODManagedKeyBindings()
{
	if (!InputComponent)
	{
		return;
	}
	if (LastODManagedKeys.Num() > 0)
	{
		InputComponent->KeyBindings.RemoveAll([this](const FInputKeyBinding& B)
		{
			return LastODManagedKeys.Contains(B.Chord.Key);
		});
	}
	LastODManagedKeys.Reset();

	struct FActionFn
	{
		EODInputAction Action;
		void (AODRaceHUD::*Fn)();
		bool bWhenPaused;   // world map is open while paused (SS7.37)
	};
	const FActionFn Binds[] = {
		{EODInputAction::ToggleMap, &AODRaceHUD::ToggleMiniMap, false},
		{EODInputAction::ZoomMapOut, &AODRaceHUD::ZoomOut, false},
		{EODInputAction::ZoomMapIn, &AODRaceHUD::ZoomIn, false},
		{EODInputAction::WorldMap, &AODRaceHUD::ToggleWorldMap, true},
		{EODInputAction::ClearRoute, &AODRaceHUD::ClearRouteKey, true},
	};
	for (const FActionFn& B : Binds)
	{
		for (const FKey& Key : {FODInputConfig::GetKeyboardKey(B.Action), FODInputConfig::GetGamepadKey(B.Action)})
		{
			if (!Key.IsValid())
			{
				continue;
			}
			InputComponent->BindKey(Key, IE_Pressed, this, B.Fn).bExecuteWhenPaused = B.bWhenPaused;
			LastODManagedKeys.AddUnique(Key);
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
	float ZoomRadiusM, bool bCircular, const AODRoadManager* RoadMgr,
	const FVector2D* CenterOverride) const
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
	if (ZoomRadiusM > 0.0f && (Pawn || CenterOverride))
	{
		Center = CenterOverride ? *CenterOverride : FVector2D(Pawn->GetActorLocation());
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
			// draw only the view/texture overlap (SS7.37): WorldToUV clamps, so
			// a view reaching past the texture edge used to stretch the edge
			// texels across the rest of the panel instead of leaving it empty
			const FVector2D HalfWorld = FVector2D(PanelSize * 0.5f, PanelSize * 0.5f) / Scale;
			const FVector2D HalfTex(RoadMgr->MapTextureSpanCm * 0.5f, RoadMgr->MapTextureSpanCm * 0.5f);
			const FVector2D Lo = FVector2D::Max(Center - HalfWorld, RoadMgr->MapTextureCenterCm - HalfTex);
			const FVector2D Hi = FVector2D::Min(Center + HalfWorld, RoadMgr->MapTextureCenterCm + HalfTex);
			if (Hi.X > Lo.X && Hi.Y > Lo.Y)
			{
				FCanvasTileItem Tex(ToMap(FVector(Lo, 0)), MapTex->GetResource(),
					(Hi - Lo) * Scale, WorldToUV(Lo), WorldToUV(Hi), FLinearColor::White);
				Tex.BlendMode = SE_BLEND_Translucent;
				Canvas->DrawItem(Tex);
			}
		}
		FCanvasTileItem Bg(FVector2D(PanelX, PanelY),
			FVector2D(PanelSize, PanelSize),
			FLinearColor(0, 0, 0, bHaveMapTexture ? TexDimAlpha : BgAlpha));
		Bg.BlendMode = SE_BLEND_Translucent;
		Canvas->DrawItem(Bg);
	}

	// clip a screen segment to the panel disc (circular) - the old both-
	// endpoints-out skip let a segment with one endpoint inside draw at full
	// length, streaking across the whole screen (live 2026-08-28). Square
	// panels draw unclipped (whole-route fit, or the full-screen world map).
	auto ClipToPanel = [&](FVector2D& A, FVector2D& B) -> bool
	{
		if (!bCircular)
		{
			return true;
		}
		const float R = PanelSize * 0.5f;
		const FVector2D D = B - A;
		const float DD = D.SizeSquared();
		if (DD < KINDA_SMALL_NUMBER)
		{
			return InPanel(A);
		}
		// solve |A + t*D - PanelMid|^2 = R^2 for t, keep [t0,t1] within [0,1]
		const FVector2D F = A - PanelMid;
		const float Bq = 2.0f * FVector2D::DotProduct(F, D);
		const float Cq = F.SizeSquared() - R * R;
		const float Disc = Bq * Bq - 4.0f * DD * Cq;
		if (Disc < 0.0f) { return false; }
		const float SqrtDisc = FMath::Sqrt(Disc);
		const float T0 = FMath::Max((-Bq - SqrtDisc) / (2.0f * DD), 0.0f);
		const float T1 = FMath::Min((-Bq + SqrtDisc) / (2.0f * DD), 1.0f);
		if (T1 <= T0) { return false; }
		B = A + D * T1;
		A = A + D * T0;
		return true;
	};

	// real road centerline (pipeline/export_centerline_bin.py or
	// export_road_centerline_bin.py, 5 m stations) when available - the
	// Catmull-Rom guess between 300 m-apart gates could cut corners the
	// real road doesn't, and is the only option at all when there are no
	// gates (route-only levels). Skip segments fully outside the panel when
	// circular/zoomed so a tight zoom doesn't waste draws.
	const TArray<FVector2D> FallbackSmooth =
		(!bHaveCenterline && bHaveGates) ? SmoothPolyline(*Gates) : TArray<FVector2D>();
	const TArray<FVector2D>& Smooth = bHaveCenterline ? *Centerline : FallbackSmooth;
	auto DrawRoads = [&](const TArray<FVector2D>& Line)
	{
		for (int32 i = 1; i < Line.Num(); ++i)
		{
			// MapCenterline's NAN-sentinel segment breaks (see its own comment) -
			// skip the edge entirely rather than drawing a connector line between
			// two unrelated streets. RoadCenterline/gate polylines never contain
			// these, so this is a no-op cost for those callers.
			if (IsCenterlineBreak(Line[i - 1]) || IsCenterlineBreak(Line[i]))
			{
				continue;
			}
			FVector2D A = ToMap(FVector(Line[i - 1], 0));
			FVector2D B = ToMap(FVector(Line[i], 0));
			if (!ClipToPanel(A, B))
			{
				continue;
			}
			FCanvasLineItem Seg(A, B);
			Seg.SetColor(FLinearColor(0.55f, 0.65f, 0.8f, 0.9f));
			Seg.LineThickness = 3.0f;
			Canvas->DrawItem(Seg);
		}
	};
	DrawRoads(Smooth);
	// every other streamed area's map centerline too (2026-10-05, large
	// world); the bbox fit above and the map texture stay home-only
	if (RoadMgr)
	{
		for (TActorIterator<AODRoadManager> It(GetWorld()); It; ++It)
		{
			if (*It != RoadMgr && &It->MapCenterline != Centerline)
			{
				DrawRoads(It->MapCenterline);
			}
		}
	}

	// navigation route (SS7.37) - on every map panel (mini, pause, world);
	// from the nav (nearest) road manager (2026-10-05, large world)
	if (const AODRoadManager* NavMgr = FindNavRoadMgr(); NavMgr && NavMgr->HasRoute())
	{
		const TArray<FVector2D>& R = NavMgr->GetRoutePoints();
		for (int32 i = 1; i < R.Num(); ++i)
		{
			FVector2D A = ToMap(FVector(R[i - 1], 0));
			FVector2D B = ToMap(FVector(R[i], 0));
			if (!ClipToPanel(A, B))
			{
				continue;
			}
			FCanvasLineItem Seg(A, B);
			Seg.SetColor(FLinearColor(1.0f, 0.3f, 0.85f, 0.95f));
			Seg.LineThickness = 5.0f;
			Canvas->DrawItem(Seg);
		}
		const FVector2D DestM = ToMap(FVector(R.Last(), 0));
		if (!bCircular || InPanel(DestM))
		{
			Dot(DestM, 6.0f, FLinearColor(1.0f, 0.3f, 0.85f));
			Dot(DestM, 2.5f, FLinearColor::White);
		}
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

	// garage-menu vista (2026-10-05): no full-screen dim over the outdoor view,
	// and the panel moves to the right so the car (left of centre) stays clear
	const bool bVista = Menu->GetStage() == EODMenuStage::CarConfig && Menu->IsVistaShown();
	if (!bVista)
	{
		FCanvasTileItem Dim(FVector2D(0, 0), FVector2D(Canvas->SizeX, Canvas->SizeY),
			FLinearColor(0, 0, 0, 0.55f));
		Dim.BlendMode = SE_BLEND_Translucent;
		Canvas->DrawItem(Dim);
	}

	auto Text = [&](const FString& S, float X, float Y, float Sc, const FLinearColor& C)
	{
		FCanvasTextItem T(FVector2D(X, Y), FText::FromString(S), CrispFont(Font, Sc), C);
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

	// ---- car config: tabbed panel on the left, the showroom car behind ----
	if (Menu->GetStage() == EODMenuStage::CarConfig)
	{
		const bool bGarage = Menu->IsGarageMode();
		const TArrayView<const FODMenuTab> Tabs = Menu->GetTabs();
		const float H = TabbedPanelHeight(Tabs);
		DrawTabbedPanel(bVista ? Canvas->SizeX - 70.0f - 640.0f : 70.0f,
			FMath::Max(20.0f, (Canvas->SizeY - H) * 0.5f), 640.0f,
			bGarage ? TEXT("GARAGE") : TEXT("SELECT YOUR CAR"),
			Tabs, Menu->GetTab(), Menu->NumRows() - 1, Menu->GetSelected(),
			[Menu](int32 Row, FString& L, FString& V) { Menu->GetRow(Row, L, V); },
			bGarage ? TEXT("Q/E  tabs     ↑↓  select     ←→  change     Esc  resume")
				: TEXT("Q/E  tabs     ↑↓  select     ←→  change     Enter  confirm"));
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

void AODRaceHUD::DrawSettingsMenu(AODSettingsMenu* Menu) const
{
	if (!Canvas || !Menu)
	{
		return;
	}
	FCanvasTileItem Dim(FVector2D(0, 0), FVector2D(Canvas->SizeX, Canvas->SizeY),
		FLinearColor(0, 0, 0, 0.7f));
	Dim.BlendMode = SE_BLEND_Translucent;
	Canvas->DrawItem(Dim);

	const TArrayView<const FODMenuTab> Tabs = Menu->GetTabs();
	const float W = 680.0f;
	const float H = TabbedPanelHeight(Tabs);
	DrawTabbedPanel((Canvas->SizeX - W) * 0.5f, FMath::Max(20.0f, (Canvas->SizeY - H) * 0.5f), W,
		TEXT("SETTINGS"), Tabs, Menu->GetTab(), Menu->NumRows() - 1, Menu->GetSelected(),
		[Menu](int32 Row, FString& L, FString& V) { Menu->GetRow(Row, L, V); },
		TEXT("Q/E  tabs     ↑↓  select     ←→  change     Esc  close"));

	if (Menu->IsRebindOpen())
	{
		DrawRebindMenu(Menu->GetRebindMenu());
	}
}

void AODRaceHUD::DrawRebindMenu(AODRebindMenu* Menu) const
{
	if (!Canvas || !Menu)
	{
		return;
	}
	UFont* Font = GEngine->GetLargeFont();

	// second dim layer on top of the Settings panel already drawn behind us
	FCanvasTileItem Dim(FVector2D(0, 0), FVector2D(Canvas->SizeX, Canvas->SizeY),
		FLinearColor(0, 0, 0, 0.55f));
	Dim.BlendMode = SE_BLEND_Translucent;
	Canvas->DrawItem(Dim);

	auto Text = [&](const FString& S, float X, float Y, float Sc, const FLinearColor& C)
	{
		FCanvasTextItem T(FVector2D(X, Y), FText::FromString(S), CrispFont(Font, Sc), C);
		T.EnableShadow(FLinearColor::Black);
		Canvas->DrawItem(T);
	};

	const float PanelW = 640.0f;
	const float PanelH = AODRebindMenu::VisibleLines * 32.0f + 130.0f;
	const float Mx = (Canvas->SizeX - PanelW) * 0.5f;
	const float PanelTop = (Canvas->SizeY - PanelH) * 0.5f;

	FCanvasTileItem Panel(FVector2D(Mx, PanelTop), FVector2D(PanelW, PanelH),
		FLinearColor(0.02f, 0.03f, 0.05f, 0.92f));
	Panel.BlendMode = SE_BLEND_Translucent;
	Canvas->DrawItem(Panel);

	float My = PanelTop + 24.0f;
	Text(TEXT("CONTROLS"), Mx + 24.0f, My, 1.8f, FLinearColor(0.75f, 0.9f, 1.0f));
	My += 52.0f;

	const int32 Start = Menu->GetScrollOffset();
	const int32 End = FMath::Min(Start + AODRebindMenu::VisibleLines, Menu->NumRows());
	for (int32 Row = Start; Row < End; ++Row)
	{
		FString Label, Value;
		Menu->GetRow(Row, Label, Value);
		const bool bSel = Row == Menu->GetSelected();
		const bool bCapturingThisRow = Menu->IsCapturing() && Row == Menu->GetCapturingRow();
		const FLinearColor Col = bCapturingThisRow
			? FLinearColor(1.0f, 0.35f, 0.3f)
			: bSel ? FLinearColor(1.0f, 0.75f, 0.4f) : FLinearColor(0.8f, 0.87f, 0.92f);
		Text((bSel ? TEXT("> ") : TEXT("  ")) + Label, Mx + 24.0f, My, 1.15f, Col);
		if (!Value.IsEmpty())
		{
			Text(Value, Mx + 380.0f, My, 1.15f, Col);
		}
		My += 32.0f;
	}

	Text(Menu->IsCapturing()
			? TEXT("press a key or gamepad button... esc cancels")
			: TEXT("arrows navigate    enter select/capture    esc back"),
		Mx + 24.0f, PanelTop + PanelH - 30.0f, 1.0f, FLinearColor(0.55f, 0.6f, 0.65f));
}

void AODRaceHUD::DrawCreditsMenu(AODCreditsMenu* Menu) const
{
	if (!Canvas || !Menu)
	{
		return;
	}
	UFont* Font = GEngine->GetLargeFont();

	FCanvasTileItem Dim(FVector2D(0, 0), FVector2D(Canvas->SizeX, Canvas->SizeY),
		FLinearColor(0, 0, 0, 0.75f));
	Dim.BlendMode = SE_BLEND_Translucent;
	Canvas->DrawItem(Dim);

	auto Text = [&](const FString& S, float X, float Y, float Sc, const FLinearColor& C)
	{
		FCanvasTextItem T(FVector2D(X, Y), FText::FromString(S), CrispFont(Font, Sc), C);
		T.EnableShadow(FLinearColor::Black);
		Canvas->DrawItem(T);
	};

	const float PanelW = FMath::Min(760.0f, Canvas->SizeX - 120.0f);
	const float Mx = (Canvas->SizeX - PanelW) * 0.5f;
	const float PanelTop = Canvas->SizeY * 0.5f - (AODCreditsMenu::VisibleLines * 22.0f + 110.0f) * 0.5f;
	const float PanelH = AODCreditsMenu::VisibleLines * 22.0f + 130.0f;

	FCanvasTileItem Panel(FVector2D(Mx, PanelTop), FVector2D(PanelW, PanelH),
		FLinearColor(0.02f, 0.03f, 0.05f, 0.88f));
	Panel.BlendMode = SE_BLEND_Translucent;
	Canvas->DrawItem(Panel);

	float My = PanelTop + 24.0f;
	Text(TEXT("CREDITS"), Mx + 24.0f, My, 1.8f, FLinearColor(0.75f, 0.9f, 1.0f));
	My += 52.0f;

	const TArray<FString>& Lines = Menu->GetLines();
	const int32 Start = Menu->GetScrollLine();
	const int32 End = FMath::Min(Start + AODCreditsMenu::VisibleLines, Lines.Num());
	for (int32 i = Start; i < End; ++i)
	{
		Text(Lines[i], Mx + 24.0f, My, 0.95f, FLinearColor(0.85f, 0.87f, 0.88f));
		My += 22.0f;
	}

	Text(TEXT("up/down scroll    esc/enter close"), Mx + 24.0f, PanelTop + PanelH - 30.0f, 1.0f,
		FLinearColor(0.55f, 0.6f, 0.65f));
}

// ---- tabbed menu panel (2026-09-28) ----

namespace
{
constexpr float TabRowH = 50.0f;
constexpr float TabHeaderH = 150.0f;   // title + tab bar
constexpr float TabFooterH = 150.0f;   // gap + footer button + hint
const FLinearColor PanelBg(0.015f, 0.02f, 0.035f, 0.88f);
const FLinearColor Accent(1.0f, 0.68f, 0.25f);
const FLinearColor Cyan(0.55f, 0.85f, 1.0f);
const FLinearColor TextCol(0.86f, 0.9f, 0.94f);
const FLinearColor DimCol(0.48f, 0.55f, 0.62f);

// "65%" -> 0.65, anything else -> -1 (not drawn as a slider)
float ParsePercent(const FString& V)
{
	if (!V.EndsWith(TEXT("%")) || !V.LeftChop(1).IsNumeric())
	{
		return -1.0f;
	}
	return FMath::Clamp(FCString::Atof(*V.LeftChop(1)) / 100.0f, 0.0f, 1.0f);
}
}

float AODRaceHUD::TabbedPanelHeight(TArrayView<const FODMenuTab> Tabs)
{
	int32 MaxRows = 0;
	for (const FODMenuTab& T : Tabs)
	{
		MaxRows = FMath::Max(MaxRows, T.Count);
	}
	return TabHeaderH + MaxRows * TabRowH + TabFooterH;
}

void AODRaceHUD::DrawTabbedPanel(float X, float Y, float W, const TCHAR* Title,
	TArrayView<const FODMenuTab> Tabs, int32 ActiveTab, int32 Footer, int32 Selected,
	TFunctionRef<void(int32, FString&, FString&)> GetRow, const TCHAR* Hint) const
{
	// Text at its real pixel size - the old Scale=1.5-2.2 stretched a small
	// raster and read blurry. The engine font is a runtime Slate composite,
	// so any size/typeface rasterizes crisp.
	const UFont* Font = GEngine->GetLargeFont();
	auto Text = [&](const FString& S, float TX, float TY, float Size, const FLinearColor& C,
		bool bBold = false, bool bCenterX = false)
	{
		FCanvasTextItem T(FVector2D(TX, TY), FText::FromString(S),
			FSlateFontInfo(Font, Size, bBold ? FName("Bold") : FName("Regular")), C);
		T.bCentreX = bCenterX;
		T.bCentreY = true;
		T.EnableShadow(FLinearColor(0, 0, 0, 0.6f), FVector2D(1, 1));
		Canvas->DrawItem(T);
	};
	auto Rect = [&](float RX, float RY, float RW, float RH, const FLinearColor& C)
	{
		FCanvasTileItem T(FVector2D(RX, RY), FVector2D(RW, RH), C);
		T.BlendMode = SE_BLEND_Translucent;
		Canvas->DrawItem(T);
	};
	auto Arrow = [&](float AX, float AY, float Dir, const FLinearColor& C)   // Dir -1 left, +1 right
	{
		FCanvasTriangleItem T(FVector2D(AX + 6.0f * Dir, AY), FVector2D(AX - 6.0f * Dir, AY - 8.0f),
			FVector2D(AX - 6.0f * Dir, AY + 8.0f), GWhiteTexture);
		T.SetColor(C);
		T.BlendMode = SE_BLEND_Translucent;
		Canvas->DrawItem(T);
	};

	const float H = TabbedPanelHeight(Tabs);
	const float Pad = 32.0f;
	Rect(X, Y, W, H, PanelBg);
	Rect(X, Y, W, 3.0f, Cyan * FLinearColor(1, 1, 1, 0.7f));

	Text(Title, X + Pad, Y + 44.0f, 30.0f, FLinearColor(0.8f, 0.92f, 1.0f), true);

	// tab bar: equal segments, Q/E cues at the ends, accent underline on the active tab
	const float BarY = Y + 104.0f;
	const float BarX0 = X + Pad + 28.0f, BarX1 = X + W - Pad - 28.0f;
	const float SegW = (BarX1 - BarX0) / FMath::Max(1, Tabs.Num());
	Text(TEXT("Q"), X + Pad + 4.0f, BarY, 15.0f, DimCol, true, true);
	Text(TEXT("E"), X + W - Pad - 4.0f, BarY, 15.0f, DimCol, true, true);
	Rect(BarX0, BarY + 20.0f, BarX1 - BarX0, 1.0f, FLinearColor(1, 1, 1, 0.12f));
	for (int32 i = 0; i < Tabs.Num(); ++i)
	{
		const bool bActive = i == ActiveTab;
		const float SX = BarX0 + SegW * i;
		Text(Tabs[i].Name, SX + SegW * 0.5f, BarY, 17.0f, bActive ? FLinearColor::White : DimCol, true, true);
		if (bActive)
		{
			Rect(SX + 10.0f, BarY + 18.0f, SegW - 20.0f, 3.0f, Accent);
		}
	}

	// rows of the active tab
	const float VX0 = X + W - Pad - 250.0f, VX1 = X + W - Pad - 10.0f;   // value column
	float RowY = Y + TabHeaderH;
	const FODMenuTab& Tab = Tabs[ActiveTab];
	for (int32 Row = Tab.First; Row < Tab.First + Tab.Count; ++Row)
	{
		FString Label, Value;
		GetRow(Row, Label, Value);
		const bool bSel = Row == Selected;
		const float CY = RowY + TabRowH * 0.5f;
		if (bSel)
		{
			Rect(X + Pad - 12.0f, RowY + 3.0f, W - 2.0f * Pad + 24.0f, TabRowH - 6.0f,
				FLinearColor(1.0f, 0.68f, 0.25f, 0.14f));
			Rect(X + Pad - 12.0f, RowY + 3.0f, 4.0f, TabRowH - 6.0f, Accent);
		}
		Text(Label, X + Pad + 6.0f, CY, 20.0f, bSel ? FLinearColor::White : TextCol, bSel);

		const FLinearColor ValCol = bSel ? Accent : TextCol;
		if (Value.IsEmpty())
		{
			Arrow(VX1, CY, +1.0f, bSel ? Accent : DimCol);   // action row: opens a sub-screen
		}
		else if (const float Pct = ParsePercent(Value); Pct >= 0.0f)
		{
			const float TX0 = VX0 + 24.0f, TX1 = VX1 - 76.0f;
			Rect(TX0, CY - 3.0f, TX1 - TX0, 6.0f, FLinearColor(1, 1, 1, 0.14f));
			Rect(TX0, CY - 3.0f, (TX1 - TX0) * Pct, 6.0f, bSel ? Accent : Cyan * FLinearColor(1, 1, 1, 0.8f));
			Rect(TX0 + (TX1 - TX0) * Pct - 2.0f, CY - 9.0f, 4.0f, 18.0f, bSel ? FLinearColor::White : TextCol);
			Text(Value, VX1 - 32.0f, CY, 18.0f, ValCol, bSel, true);
		}
		else
		{
			Text(Value, (VX0 + VX1) * 0.5f, CY, 19.0f, ValCol, bSel, true);
		}
		if (bSel && !Value.IsEmpty())
		{
			Arrow(VX0, CY, -1.0f, Accent);
			Arrow(VX1, CY, +1.0f, Accent);
		}
		RowY += TabRowH;
	}

	// footer button (Drive / Close) - always visible, pinned to the panel bottom
	FString FootLabel, FootValue;
	GetRow(Footer, FootLabel, FootValue);
	const bool bFootSel = Selected == Footer;
	const float FootY = Y + H - TabFooterH + 30.0f;
	Rect(X + Pad - 12.0f, FootY, W - 2.0f * Pad + 24.0f, 54.0f,
		bFootSel ? Accent : FLinearColor(1, 1, 1, 0.07f));
	Text(FootLabel.ToUpper(), X + W * 0.5f, FootY + 27.0f, 21.0f,
		bFootSel ? FLinearColor(0.05f, 0.04f, 0.02f) : TextCol, true, true);

	Text(Hint, X + Pad, Y + H - 32.0f, 14.0f, DimCol);
}

// ---- Arc 4.2 v3: world map + navigation (next steps.md SS7.37) ----

namespace
{
// miles/feet, matching the mph speed readout
FString FormatNavDist(float Cm)
{
	const float M = Cm / 100.0f;
	if (M < 305.0f)   // under ~1000 ft
	{
		return FString::Printf(TEXT("%d ft"), FMath::Max(50, FMath::RoundToInt(M * 3.28084f / 50.0f) * 50));
	}
	return FString::Printf(TEXT("%.1f mi"), M / 1609.34f);
}
}

AODRoadManager* AODRaceHUD::FindNavRoadMgr() const
{
	// Large world (2026-10-05): the road manager nearest the player (the
	// pawn's own 2 Hz out-of-bounds scan), so routes/towns follow the area
	// being driven; else the first with a route graph, as before.
	// ponytail: routes don't cross areas - each area's graph is separate.
	if (const AODVehiclePawn* Veh = Cast<AODVehiclePawn>(GetOwningPawn()))
	{
		if (AODRoadManager* Near = Veh->GetNearestRoadMgr(); Near && Near->HasRouteGraph())
		{
			return Near;
		}
	}
	for (TActorIterator<AODRoadManager> It(GetWorld()); It; ++It)
	{
		if (It->HasRouteGraph())
		{
			return *It;
		}
	}
	return nullptr;
}

void AODRaceHUD::SetMapCursor(bool bVisible)
{
	for (TActorIterator<AODMainMenu> It(GetWorld()); It; ++It)
	{
		It->SetCursorVisible(bVisible);   // same cursor/input-mode handling as the front-end map
		return;
	}
	if (APlayerController* PC = GetOwningPlayerController())
	{
		PC->bShowMouseCursor = bVisible;
	}
}

void AODRaceHUD::OpenWorldMap()
{
	if (bWorldMapOpen)
	{
		return;
	}
	for (TActorIterator<AODMainMenu> It(GetWorld()); It; ++It)
	{
		if (It->IsActive())
		{
			return;   // front-end / garage / loading screen owns the screen
		}
		break;
	}
	for (TActorIterator<AODPauseMenu> It(GetWorld()); It; ++It)
	{
		if (It->IsSettingsOpen())
		{
			return;   // don't yank the screen out from under a rebind capture
		}
		It->HideForOverlay();
		break;
	}
	const AODRoadManager* RoadMgr = FindNavRoadMgr();
	if (!RoadMgr)
	{
		return;   // no route graph on this level (e.g. Tail of the Dragon)
	}
	const APawn* Pawn = GetOwningPawn();
	WorldMapCenter = Pawn ? FVector2D(Pawn->GetActorLocation()) : RoadMgr->MapTextureCenterCm;
	WorldMapRadiusM = 4000.0f;
	bWorldMapDragging = false;
	WorldMapOpenFrame = GFrameCounter;
	bWorldMapOpen = true;
	UGameplayStatics::SetGamePaused(this, true);
	SetMapCursor(true);
}

void AODRaceHUD::CloseWorldMap()
{
	if (!bWorldMapOpen)
	{
		return;
	}
	bWorldMapOpen = false;
	SetMapCursor(false);
	UGameplayStatics::SetGamePaused(this, false);
}

void AODRaceHUD::ClearRouteKey()
{
	if (AODRoadManager* RoadMgr = FindNavRoadMgr())
	{
		RoadMgr->ClearRoute();
	}
}

void AODRaceHUD::DrawWorldMap(AODRoadManager* MapTexMgr, const TArray<FVector2D>* Centerline)
{
	// towns/route/fast travel use the nav (nearest) road manager; the passed-in
	// one only supplies the home map texture (2026-10-05, large world)
	AODRoadManager* RoadMgr = FindNavRoadMgr();
	APlayerController* PC = GetOwningPlayerController();
	if (!PC || !RoadMgr || !RoadMgr->HasRouteGraph())
	{
		CloseWorldMap();
		return;
	}
	UFont* Font = GEngine->GetLargeFont();
	auto Text = [&](const FString& S, float X, float Y, float Sc, const FLinearColor& C)
	{
		FCanvasTextItem T(FVector2D(X, Y), FText::FromString(S), CrispFont(Font, Sc), C);
		T.EnableShadow(FLinearColor::Black);
		Canvas->DrawItem(T);
	};

	// square panel covering the whole screen, centered - DrawStageMap's
	// zoomed branch then gives Scale = (Side/2 - Pad) / radius
	const float Side = FMath::Max(Canvas->SizeX, Canvas->SizeY);
	const FVector2D Mid(Canvas->SizeX * 0.5f, Canvas->SizeY * 0.5f);
	auto Scale = [&]() { return (Side * 0.5f - 14.0f) / (WorldMapRadiusM * 100.0f); };
	auto ToScreen = [&](const FVector2D& W) { return Mid + (W - WorldMapCenter) * Scale(); };
	auto ToWorld = [&](const FVector2D& S) { return WorldMapCenter + (S - Mid) / Scale(); };

	float Mx = 0.0f, My = 0.0f;
	const bool bMouse = PC->GetMousePosition(Mx, My);
	const FVector2D Mouse(Mx, My);
	const TArray<FTownNode>& Towns = RoadMgr->GetTownNodes();
	auto TownNear = [&](const FVector2D& S, float MaxPx) -> int32
	{
		int32 Best = INDEX_NONE;
		float BestD2 = MaxPx * MaxPx;
		for (int32 i = 0; i < Towns.Num(); ++i)
		{
			const float D2 = FVector2D::DistSquared(ToScreen(Towns[i].Pos), S);
			if (D2 < BestD2)
			{
				BestD2 = D2;
				Best = i;
			}
		}
		return Best;
	};
	// hovered town: under the mouse, else under the center crosshair (gamepad)
	int32 Hover = bMouse ? TownNear(Mouse, 18.0f) : INDEX_NONE;
	if (Hover == INDEX_NONE)
	{
		Hover = TownNear(Mid, 18.0f);
	}
	const double RealNow = GetWorld()->GetRealTimeSeconds();
	auto Pick = [&](const FVector2D& S)
	{
		const int32 T = TownNear(S, 18.0f);
		FVector2D Snap;
		const bool bOk = T != INDEX_NONE
			? RoadMgr->SetRouteDestination(Towns[T].Pos, Towns[T].Name)
			: RoadMgr->SnapToRouteGraph(ToWorld(S), Snap)
				&& RoadMgr->SetRouteDestination(Snap, TEXT("Waypoint"));
		if (!bOk)
		{
			NoRouteMsgTime = RealNow;
		}
	};

	// ---- input (polled; the game is paused, so only this map reads it).
	// Skipped on the opening frame so the Enter/Tab that opened it doesn't
	// also count as a pick here.
	if (GFrameCounter > WorldMapOpenFrame + 1)
	{
		const float Dt = FMath::Min((float)FApp::GetDeltaTime(), 0.1f);
		auto Down = [&](const FKey& K) { return PC->IsInputKeyDown(K); };
		auto Pressed = [&](const FKey& K) { return PC->WasInputKeyJustPressed(K); };

		// pan: arrows / WASD / d-pad / left stick, one view radius per second
		FVector2D Pan(0, 0);
		Pan.X += (Down(EKeys::Right) || Down(EKeys::D) || Down(EKeys::Gamepad_DPad_Right)) ? 1.0f : 0.0f;
		Pan.X -= (Down(EKeys::Left) || Down(EKeys::A) || Down(EKeys::Gamepad_DPad_Left)) ? 1.0f : 0.0f;
		Pan.Y += (Down(EKeys::Down) || Down(EKeys::S) || Down(EKeys::Gamepad_DPad_Down)) ? 1.0f : 0.0f;
		Pan.Y -= (Down(EKeys::Up) || Down(EKeys::W) || Down(EKeys::Gamepad_DPad_Up)) ? 1.0f : 0.0f;
		const FVector2D Stick(PC->GetInputAnalogKeyState(EKeys::Gamepad_LeftX),
			-PC->GetInputAnalogKeyState(EKeys::Gamepad_LeftY));   // stick up = north = -Y
		if (Stick.Size() > 0.2f)
		{
			Pan += Stick;
		}
		WorldMapCenter += Pan * WorldMapRadiusM * 100.0f * Dt;

		// zoom: wheel (about the cursor), [ ] / shoulders (about the center)
		float Zoom = 1.0f;
		FVector2D Anchor = Mid;
		if (Pressed(EKeys::MouseScrollUp)) { Zoom *= 0.8f; Anchor = bMouse ? Mouse : Mid; }
		if (Pressed(EKeys::MouseScrollDown)) { Zoom /= 0.8f; Anchor = bMouse ? Mouse : Mid; }
		if (Down(FODInputConfig::GetKeyboardKey(EODInputAction::ZoomMapIn)) || Down(EKeys::Gamepad_RightShoulder))
		{
			Zoom *= FMath::Pow(0.3f, Dt);
		}
		if (Down(FODInputConfig::GetKeyboardKey(EODInputAction::ZoomMapOut)) || Down(EKeys::Gamepad_LeftShoulder))
		{
			Zoom /= FMath::Pow(0.3f, Dt);
		}
		if (Zoom != 1.0f)
		{
			const FVector2D Before = ToWorld(Anchor);
			WorldMapRadiusM = FMath::Clamp(WorldMapRadiusM * Zoom, 150.0f, 60000.0f);
			WorldMapCenter = Before - (Anchor - Mid) / Scale();
		}

		// drag to pan; a click that barely moved = pick at the cursor
		if (bMouse && Pressed(EKeys::LeftMouseButton))
		{
			bWorldMapDragging = true;
			WorldMapDragPx = 0.0f;
			WorldMapLastMouse = Mouse;
		}
		if (bWorldMapDragging && bMouse && Down(EKeys::LeftMouseButton))
		{
			const FVector2D D = Mouse - WorldMapLastMouse;
			WorldMapDragPx += D.Size();
			WorldMapCenter -= D / Scale();
			WorldMapLastMouse = Mouse;
		}
		if (bWorldMapDragging && !Down(EKeys::LeftMouseButton))
		{
			bWorldMapDragging = false;
			if (WorldMapDragPx < 6.0f)
			{
				Pick(WorldMapLastMouse);
			}
		}

		if (Pressed(EKeys::Enter) || Pressed(EKeys::Gamepad_FaceButton_Bottom))
		{
			Pick(Mid);
		}
		if (Pressed(EKeys::RightMouseButton) || Pressed(EKeys::Gamepad_FaceButton_Left))
		{
			RoadMgr->ClearRoute();
		}
		if ((Pressed(EKeys::F) || Pressed(EKeys::Gamepad_FaceButton_Top)) && Hover != INDEX_NONE)
		{
			// fast travel: the front-end map's own teleport + loading path
			for (TActorIterator<AODMainMenu> It(GetWorld()); It; ++It)
			{
				CloseWorldMap();
				It->TravelToTown(Hover, RoadMgr);
				return;
			}
		}
		if (Pressed(EKeys::Gamepad_FaceButton_Right))
		{
			CloseWorldMap();
			return;
		}
	}

	// ---- draw
	FCanvasTileItem Backdrop(FVector2D(0, 0), FVector2D(Canvas->SizeX, Canvas->SizeY),
		FLinearColor(0.04f, 0.05f, 0.07f, 1.0f));
	Canvas->DrawItem(Backdrop);
	DrawStageMap(Centerline, nullptr, false, INDEX_NONE, Mid.X - Side * 0.5f, Mid.Y - Side * 0.5f,
		Side, 0.6f, WorldMapRadiusM, false, MapTexMgr, &WorldMapCenter);

	for (int32 i = 0; i < Towns.Num(); ++i)
	{
		const FVector2D S = ToScreen(Towns[i].Pos);
		const bool bH = i == Hover;
		const float R = bH ? 6.0f : 4.0f;
		FCanvasTileItem Dot(S - FVector2D(R, R), FVector2D(2 * R, 2 * R),
			bH ? FLinearColor(1.0f, 0.85f, 0.3f) : FLinearColor(0.95f, 0.95f, 0.9f));
		Canvas->DrawItem(Dot);
		Text(Towns[i].Name, S.X + 9.0f, S.Y - 9.0f, bH ? 1.25f : 1.0f,
			bH ? FLinearColor(1.0f, 0.9f, 0.5f) : FLinearColor(0.92f, 0.92f, 0.88f));
	}

	// center crosshair (gamepad / Enter pick point)
	for (const FVector2D& D : {FVector2D(1, 0), FVector2D(0, 1)})
	{
		FCanvasLineItem L(Mid - D * 10.0f, Mid + D * 10.0f);
		L.SetColor(FLinearColor(1, 1, 1, 0.8f));
		L.LineThickness = 2.0f;
		Canvas->DrawItem(L);
	}

	FCanvasTileItem Bar(FVector2D(0, 0), FVector2D(Canvas->SizeX, 64.0f), FLinearColor(0.02f, 0.03f, 0.05f, 0.8f));
	Bar.BlendMode = SE_BLEND_Translucent;
	Canvas->DrawItem(Bar);
	Text(TEXT("WORLD MAP"), 24.0f, 14.0f, 1.8f, FLinearColor(0.75f, 0.9f, 1.0f));
	const FString Status = RealNow - NoRouteMsgTime < 3.0
		? FString(TEXT("no road route to there"))
		: RoadMgr->HasRoute()
			? FString::Printf(TEXT("route to %s  ·  %s"), *RoadMgr->GetRouteName(),
				*FormatNavDist(RoadMgr->GetRouteRemainingCm()))
			: FString(TEXT("no destination set"));
	Text(Status, 260.0f, 22.0f, 1.2f, RealNow - NoRouteMsgTime < 3.0
		? FLinearColor(1.0f, 0.45f, 0.35f) : FLinearColor(1.0f, 0.55f, 0.9f));
	if (Hover != INDEX_NONE)
	{
		Text(FString::Printf(TEXT("%s  -  click/enter: route here    F / Y: fast travel"), *Towns[Hover].Name),
			24.0f, Canvas->SizeY - 84.0f, 1.1f, FLinearColor(1.0f, 0.9f, 0.5f));
	}
	Text(TEXT("drag / arrows pan    wheel / [ ] zoom    click: town or road waypoint    right-click / backspace: clear route    tab / esc: close"),
		24.0f, Canvas->SizeY - 56.0f, 1.0f, FLinearColor(0.7f, 0.75f, 0.8f));
	Text(TEXT("pad: stick pan    LB/RB zoom    A route to crosshair    X clear    Y fast travel    B / back: close"),
		24.0f, Canvas->SizeY - 32.0f, 1.0f, FLinearColor(0.55f, 0.6f, 0.65f));
}

void AODRaceHUD::DrawNavPanel(const AODRoadManager* RoadMgr) const
{
	const double Now = GetWorld()->GetTimeSeconds();
	const bool bArrived = RoadMgr->GetArrivedTime() >= 0.0 && Now - RoadMgr->GetArrivedTime() < 4.0;
	if (!RoadMgr->HasRoute() && !bArrived)
	{
		return;
	}
	UFont* Font = GEngine->GetLargeFont();
	const float W = 380.0f, H = 76.0f;
	const float X = Canvas->SizeX * 0.5f - W * 0.5f, Y = 18.0f;
	FCanvasTileItem Bg(FVector2D(X, Y), FVector2D(W, H), FLinearColor(0.02f, 0.03f, 0.05f, 0.72f));
	Bg.BlendMode = SE_BLEND_Translucent;
	Canvas->DrawItem(Bg);
	auto Text = [&](const FString& S, float TX, float TY, float Sc, const FLinearColor& C)
	{
		FCanvasTextItem T(FVector2D(TX, TY), FText::FromString(S), CrispFont(Font, Sc), C);
		T.EnableShadow(FLinearColor::Black);
		Canvas->DrawItem(T);
	};
	if (!RoadMgr->HasRoute())
	{
		Text(FString::Printf(TEXT("ARRIVED  ·  %s"), *RoadMgr->GetArrivedName()), X + 20.0f, Y + 22.0f, 1.5f,
			FLinearColor(0.4f, 1.0f, 0.5f));
		return;
	}

	// turn arrow: stem up from the bottom, then bent by the turn angle
	// (+ = right = clockwise on screen); straight when no turn is coming
	float TurnCm = 0.0f, TurnDeg = 0.0f;
	const bool bTurn = RoadMgr->GetNextTurn(TurnCm, TurnDeg);
	const float A = FMath::DegreesToRadians(bTurn ? FMath::Clamp(TurnDeg, -135.0f, 135.0f) : 0.0f);
	const FVector2D Knee(X + 38.0f, Y + 36.0f);
	const FVector2D Dir(FMath::Sin(A), -FMath::Cos(A));
	const FVector2D Tip = Knee + Dir * 20.0f;
	const FVector2D Side(-Dir.Y, Dir.X);
	const FLinearColor ArrowCol(1.0f, 0.55f, 0.9f);
	const FVector2D Lines[4][2] = {
		{Knee + FVector2D(0, 26.0f), Knee}, {Knee, Tip},
		{Tip, Tip - Dir * 9.0f + Side * 7.0f}, {Tip, Tip - Dir * 9.0f - Side * 7.0f}};
	for (const auto& L : Lines)
	{
		FCanvasLineItem Item(L[0], L[1]);
		Item.SetColor(ArrowCol);
		Item.LineThickness = 4.0f;
		Canvas->DrawItem(Item);
	}

	const float Abs = FMath::Abs(TurnDeg);
	const TCHAR* Side2 = TurnDeg > 0.0f ? TEXT("right") : TEXT("left");
	const FString Line1 = !bTurn ? FString(TEXT("Continue"))
		: FString::Printf(TEXT("%s %s in %s"), Abs >= 120.0f ? TEXT("Sharp") : Abs >= 60.0f ? TEXT("Turn") : TEXT("Keep"),
			Side2, *FormatNavDist(TurnCm));
	Text(Line1, X + 80.0f, Y + 10.0f, 1.5f, FLinearColor::White);
	Text(FString::Printf(TEXT("%s  ·  %s"), *RoadMgr->GetRouteName(), *FormatNavDist(RoadMgr->GetRouteRemainingCm())),
		X + 80.0f, Y + 44.0f, 1.05f, FLinearColor(1.0f, 0.7f, 0.92f));
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
	// Large world (2026-10-05): with several area sets live, prefer the one
	// carrying the (home-only) map texture - actor iteration order isn't a
	// promise that the level-placed home manager comes first.
	AODRoadManager* RoadMgr = nullptr;
	if (!Race)
	{
		for (TActorIterator<AODRoadManager> It(GetWorld()); It; ++It)
		{
			if (!RoadMgr || It->MapTexture)
			{
				RoadMgr = *It;
			}
			if (It->MapTexture)
			{
				break;
			}
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
			// Settings/Credits (Arc 4.4) take over on top of whichever
			// car-config/map panel DrawMainMenu just drew - same layering as
			// the pause-menu block below.
			if (It->IsSettingsOpen())
			{
				DrawSettingsMenu(It->GetSettingsMenu());
			}
			else if (It->IsCreditsOpen())
			{
				DrawCreditsMenu(It->GetCreditsMenu());
			}
			return;
		}
		break;
	}

	// ---- world map (SS7.37) - full screen, game paused; replaces the HUD
	if (bWorldMapOpen)
	{
		DrawWorldMap(RoadMgr, MapCenterline);
		return;
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
			FCanvasTextItem Item(FVector2D(X, Y), FText::FromString(Text), CrispFont(Font, Scale), Color);
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
				CrispFont(Font, 2.2f), FLinearColor(1.0f, 0.35f, 0.2f));
			Warn.EnableShadow(FLinearColor::Black);
			Canvas->DrawItem(Warn);
		}
	}

	// ---- weather event warning (W4/W5, sec 7.42) - AODWeatherController
	// sets the text on tornado spawn / hurricane onset; same banner style as
	// the out-of-bounds countdown, shown for 6 s.
	for (TActorIterator<AODWeatherController> It(GetWorld()); It; ++It)
	{
		if (Now - It->WarningShownAt < 6.0f)
		{
			FCanvasTextItem Warn(FVector2D(Canvas->SizeX * 0.5f - 220.0f, Canvas->SizeY * 0.18f),
				FText::FromString(It->WarningText), CrispFont(Font, 2.0f), FLinearColor(1.0f, 0.8f, 0.2f));
			Warn.EnableShadow(FLinearColor::Black);
			Canvas->DrawItem(Warn);
		}
		break;
	}

	// ---- speed readout - only needs the owning pawn
	if (const APawn* Pawn = GetOwningPawn())
	{
		const float Mph = Pawn->GetVelocity().Size() * 0.02237f;
		FCanvasTextItem Speed(FVector2D(Canvas->SizeX - 200.0f, Canvas->SizeY - 80.0f),
			FText::FromString(FString::Printf(TEXT("%.0f mph"), Mph)),
			CrispFont(Font, 1.8f), FLinearColor::White);
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

	// ---- navigation next-turn / distance panel (SS7.37)
	if (const AODRoadManager* NavMgr = FindNavRoadMgr(); NavMgr && !bMenuOpen)
	{
		DrawNavPanel(NavMgr);   // nearest area's road (2026-10-05, large world)
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
			CrispFont(Font, 2.2f), FLinearColor::White);
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
				CrispFont(Font, 1.5f), Color);
			L.EnableShadow(FLinearColor::Black);
			Canvas->DrawItem(L);

			if (!Value.IsEmpty())
			{
				FCanvasTextItem V(FVector2D(Mx + 220.0f, My),
					FText::FromString(Value),
					CrispFont(Font, 1.5f), Color);
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

		// Settings/Credits (Arc 4.4) take over on top of the pause panel just
		// drawn, same as the front-end menu block above.
		if (Menu->IsSettingsOpen())
		{
			DrawSettingsMenu(Menu->GetSettingsMenu());
		}
		else if (Menu->IsCreditsOpen())
		{
			DrawCreditsMenu(Menu->GetCreditsMenu());
		}
		break;
	}
}
