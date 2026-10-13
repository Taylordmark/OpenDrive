#include "ODRaceHUD.h"

#include "Application/SlateApplicationBase.h"
#include "Components/InputComponent.h"
#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/Font.h"
#include "Engine/Texture2D.h"
#include "Fonts/CompositeFont.h"
#include "Fonts/FontMeasure.h"
#include "EngineUtils.h"
#include "GameFramework/Pawn.h"
#include "GlobalRenderResources.h"
#include "GameFramework/PlayerController.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/App.h"
#include "Misc/Paths.h"
#include "ODCreditsMenu.h"
#include "ODGaugeCluster.h"
#include "ODGhost.h"
#include "ODInputConfig.h"
#include "ODMainMenu.h"
#include "ODPauseMenu.h"
#include "ODVehicleMovement.h"
#include "ODVehiclePawn.h"
#include "ODRaceManager.h"
#include "ODRebindMenu.h"
#include "ODRoadManager.h"
#include "ODSettingsMenu.h"
#include "ODWeatherController.h"
#include "Rendering/SlateRenderer.h"

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
	GConfig->GetBool(TEXT("OpenDrive.HUD"), TEXT("MiniMapHeadingUp"), bMiniMapHeadingUp,
		GGameUserSettingsIni);
	GConfig->GetBool(TEXT("OpenDrive.HUD"), TEXT("Metric"), bMetric, GGameUserSettingsIni);
}

void AODRaceHUD::SetMiniMapHeadingUp(bool bOn)
{
	bMiniMapHeadingUp = bOn;
	GConfig->SetBool(TEXT("OpenDrive.HUD"), TEXT("MiniMapHeadingUp"), bOn, GGameUserSettingsIni);
	GConfig->Flush(false, GGameUserSettingsIni);
}

void AODRaceHUD::SetMetric(bool bOn)
{
	bMetric = bOn;
	GConfig->SetBool(TEXT("OpenDrive.HUD"), TEXT("Metric"), bOn, GGameUserSettingsIni);
	GConfig->Flush(false, GGameUserSettingsIni);
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
	// heading-up mini-map: world rotated so the car's forward is screen-up,
	// centered ahead of the car so more of the road ahead shows
	const bool bHeadingUp = bMiniMapHeadingUp && bCircular && ZoomRadiusM > 0.0f
		&& Pawn && !CenterOverride;
	FVector2D Rot(1.0f, 0.0f);   // (cos, sin) of the world->screen rotation
	if (ZoomRadiusM > 0.0f && (Pawn || CenterOverride))
	{
		Center = CenterOverride ? *CenterOverride : FVector2D(Pawn->GetActorLocation());
		Scale = (PanelSize * 0.5f - Pad) / (ZoomRadiusM * 100.0f);
		if (bHeadingUp)
		{
			// world X=east, Y=south maps straight to screen, so forward sits at
			// angle Yaw on screen; rotate by -90-Yaw to point it up
			const float Yaw = FMath::DegreesToRadians(Pawn->GetActorRotation().Yaw);
			Rot = FVector2D(FMath::Cos(-HALF_PI - Yaw), FMath::Sin(-HALF_PI - Yaw));
			Center += FVector2D(FMath::Cos(Yaw), FMath::Sin(Yaw)) * (ZoomRadiusM * 100.0f * 0.35f);
		}
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
		const FVector2D D = (FVector2D(W) - Center) * Scale;
		return PanelMid + FVector2D(D.X * Rot.X - D.Y * Rot.Y, D.X * Rot.Y + D.Y * Rot.X);
	};
	// inverse of ToMap (screen offset from PanelMid -> world), for texture UVs
	auto ScreenToWorld = [&](const FVector2D& S)
	{
		const FVector2D D = (S - PanelMid) / Scale;
		return Center + FVector2D(D.X * Rot.X + D.Y * Rot.Y, -D.X * Rot.Y + D.Y * Rot.X);
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
					CenterUV, WorldToUV(ScreenToWorld(P0)),
					WorldToUV(ScreenToWorld(P1)), TexRes);
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
		const FVector2D W(FMath::Cos(Yaw), FMath::Sin(Yaw));
		const FVector2D Fwd(W.X * Rot.X - W.Y * Rot.Y, W.X * Rot.Y + W.Y * Rot.X);
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
		// north tick on the bezel when the map rotates (world north = -Y)
		if (bHeadingUp)
		{
			const FVector2D N = PanelMid + R * FVector2D(Rot.Y, -Rot.X);
			Dot(N, 5.0f, FLinearColor(0.9f, 0.15f, 0.15f));
			Dot(N, 2.0f, FLinearColor::White);
		}
	}
}

// ---- menus (2026-10-09 redesign) -------------------------------------------
// Every menu is one left-hand column on a dark gradient (mirrored right over
// the Scene vista), Rajdhani text, sized from a 1080p layout by MenuScale().
// While drawing, each clickable rect goes into MenuHits; HandleMenuMouse then
// hit-tests the cursor against them and calls the owning menu's public
// SelectRow/SetTab/Activate/Adjust/Back/Up/Down - the same entry points the
// keyboard uses, so mouse and keys can't disagree.

namespace
{
const FLinearColor Accent(1.0f, 0.68f, 0.25f);
const FLinearColor TextCol(0.88f, 0.91f, 0.95f);
const FLinearColor DimCol(0.52f, 0.58f, 0.65f);
const FLinearColor Ink(0.012f, 0.016f, 0.028f);   // backdrop, and text on an accent fill

// 1080p layout units (multiplied by MenuScale())
constexpr float ColX = 96.0f;      // column inset from the screen edge
constexpr float ColW = 640.0f;
constexpr float RowH = 54.0f;
constexpr float FooterH = 58.0f;

const FODMenuTab PauseTabs[] = {{TEXT("PAUSED"), AODPauseMenu::RowWorldMap, AODPauseMenu::RowResume}};

// "65%" -> 0.65, anything else -> -1 (not drawn as a slider)
float ParsePercent(const FString& V)
{
	if (!V.EndsWith(TEXT("%")) || !V.LeftChop(1).IsNumeric())
	{
		return -1.0f;
	}
	return FMath::Clamp(FCString::Atof(*V.LeftChop(1)) / 100.0f, 0.0f, 1.0f);
}

enum class EMenuWeight : uint8 { Medium, SemiBold, Bold };

// Rajdhani (SIL OFL, Content/Slate/Fonts) straight from its TTF - no UFont
// asset (editor-agnostic); DefaultGame.ini stages the folder. One composite
// font per weight, built once: the Slate font cache keys on it, so a fresh
// one per call would re-rasterize glyphs every frame.
FSlateFontInfo MenuFont(EMenuWeight Weight, float Size)
{
	static TSharedPtr<const FCompositeFont> Fonts[3];
	static bool bTried[3] = {};
	const int32 I = static_cast<int32>(Weight);
	if (!bTried[I])
	{
		bTried[I] = true;
		static const TCHAR* const Files[] = {
			TEXT("Rajdhani-Medium.ttf"), TEXT("Rajdhani-SemiBold.ttf"), TEXT("Rajdhani-Bold.ttf")};
		const FString Path = FPaths::ProjectContentDir() / TEXT("Slate/Fonts") / Files[I];
		if (FPaths::FileExists(Path))
		{
			Fonts[I] = MakeShared<FCompositeFont>(NAME_None, Path, EFontHinting::Default, EFontLoadingPolicy::LazyLoad);
		}
		else
		{
			UE_LOG(LogTemp, Warning, TEXT("ODRaceHUD: menu font missing (%s) - using the engine font"), *Path);
		}
	}
	if (Fonts[I])
	{
		return FSlateFontInfo(Fonts[I], Size);
	}
	return FSlateFontInfo(GEngine->GetLargeFont(), Size, Weight == EMenuWeight::Medium ? FName("Regular") : FName("Bold"));
}

/** Canvas painter at menu scale S - sizes are 1080p units, positions pixels. */
struct FMenuPaint
{
	UCanvas* C;
	float S;

	void Rect(float X, float Y, float W, float H, const FLinearColor& Col) const
	{
		FCanvasTileItem T(FVector2D(X, Y), FVector2D(W, H), Col);
		T.BlendMode = SE_BLEND_Translucent;
		C->DrawItem(T);
	}

	static FSlateFontInfo Font(float Size, EMenuWeight W, int32 Spacing)
	{
		FSlateFontInfo F = MenuFont(W, Size);
		F.LetterSpacing = Spacing;
		return F;
	}

	// Y is the text's vertical centre
	void Text(const FString& Str, float X, float Y, float Size, EMenuWeight W, const FLinearColor& Col,
		bool bCenterX = false, int32 Spacing = 0) const
	{
		FCanvasTextItem T(FVector2D(X, Y), FText::FromString(Str), Font(Size * S, W, Spacing), Col);
		T.bCentreX = bCenterX;
		T.bCentreY = true;
		T.EnableShadow(FLinearColor(0, 0, 0, 0.45f), FVector2D(S, S));
		// HasValidText() needs a UFont even when SlateFontInfo is used; the
		// file-loaded Rajdhani has no FontObject, so without this nothing drew
		T.Font = GEngine->GetLargeFont();
		C->DrawItem(T);
	}

	float Width(const FString& Str, float Size, EMenuWeight W, int32 Spacing = 0) const
	{
		if (FSlateApplicationBase::IsInitialized() && FSlateApplicationBase::Get().GetRenderer())
		{
			return FSlateApplicationBase::Get().GetRenderer()->GetFontMeasureService()
				->Measure(Str, Font(Size * S, W, Spacing)).X;
		}
		return Str.Len() * Size * S * 0.5f;
	}

	// filled triangle pointing Dir (-1 left, +1 right), centred on X,Y
	void Arrow(float X, float Y, float Dir, const FLinearColor& Col) const
	{
		FCanvasTriangleItem T(FVector2D(X + 5.0f * S * Dir, Y), FVector2D(X - 5.0f * S * Dir, Y - 7.0f * S),
			FVector2D(X - 5.0f * S * Dir, Y + 7.0f * S), GWhiteTexture);
		T.SetColor(Col);
		T.BlendMode = SE_BLEND_Translucent;
		C->DrawItem(T);
	}

	// horizontal fade, alpha A0 at X0 -> A1 at X1 (either direction), in strips
	void Fade(float X0, float X1, float Y, float H, FLinearColor Col, float A0, float A1) const
	{
		const int32 N = FMath::Clamp(FMath::RoundToInt(FMath::Abs(X1 - X0) / (6.0f * S)), 1, 256);
		const float Step = (X1 - X0) / N;
		for (int32 i = 0; i < N; ++i)
		{
			Col.A = FMath::Lerp(A0, A1, (i + 0.5f) / N);
			const float SX = X0 + Step * i;
			Rect(FMath::Min(SX, SX + Step), Y, FMath::Abs(Step) + 0.5f, H, Col);
		}
	}

	// a key cap; returns its width
	float Key(const FString& K, float X, float Y) const
	{
		const float W = FMath::Max(Width(K, 15.0f, EMenuWeight::SemiBold) + 14.0f * S, 26.0f * S);
		Rect(X, Y - 13.0f * S, W, 26.0f * S, FLinearColor(1, 1, 1, 0.14f));
		Text(K, X + W * 0.5f, Y, 15.0f, EMenuWeight::SemiBold, TextCol, true);
		return W;
	}
};

struct FMenuHint
{
	const TCHAR* Key;
	const TCHAR* Label;
};

// bottom-of-screen key hints, starting at the column's X
void DrawHints(const FMenuPaint& P, float X, std::initializer_list<FMenuHint> Hints)
{
	const float Y = P.C->SizeY - 56.0f * P.S;
	for (const FMenuHint& H : Hints)
	{
		X += P.Key(H.Key, X, Y) + 8.0f * P.S;
		P.Text(H.Label, X, Y, 16.0f, EMenuWeight::Medium, DimCol);
		X += P.Width(H.Label, 16.0f, EMenuWeight::Medium) + 28.0f * P.S;
	}
}

// mouse -> menu, shared by AODMainMenu and AODPauseMenu (same public API)
template <typename TMenu>
void DispatchMenuMouse(TMenu* Menu, APlayerController* PC, const TArray<FODMenuHit>& Hits,
	FVector2D& LastMouse, uint64& LastFrame)
{
	float Mx = 0.0f, My = 0.0f;
	if (!Menu || !PC || !PC->bShowMouseCursor || !PC->GetMousePosition(Mx, My))
	{
		return;
	}
	const FVector2D Mouse(Mx, My);
	// hover only steals the selection when the mouse actually moves - not on
	// the frame a menu opens under a resting cursor, nor while keys drive it
	const bool bMoved = GFrameCounter - LastFrame <= 1 && !Mouse.Equals(LastMouse, 0.5);
	LastMouse = Mouse;
	LastFrame = GFrameCounter;

	// a key capture owns every key, the mouse buttons included (they're
	// bindable), and the click that ended it must not also hit the menu
	if (const AODSettingsMenu* Set = Menu->IsSettingsOpen() ? Menu->GetSettingsMenu() : nullptr;
		Set && Set->IsRebindOpen())
	{
		if (const AODRebindMenu* R = Set->GetRebindMenu(); R && (R->IsCapturing() || R->CaptureJustEnded()))
		{
			return;
		}
	}

	const FODMenuHit* Hit = nullptr;
	for (int32 i = Hits.Num() - 1; i >= 0 && !Hit; --i)   // last drawn = on top
	{
		const FODMenuHit& H = Hits[i];
		Hit = (Mx >= H.X0 && Mx < H.X1 && My >= H.Y0 && My < H.Y1) ? &H : nullptr;
	}
	if (Hit && bMoved && Hit->Kind != FODMenuHit::Tab)
	{
		Menu->SelectRow(Hit->Id, Hit->Col);
	}
	if (Hit && PC->WasInputKeyJustPressed(EKeys::LeftMouseButton))
	{
		const FODMenuHit H = *Hit;   // Activate may change what's on screen
		switch (H.Kind)
		{
		case FODMenuHit::Tab: Menu->SetTab(H.Id); break;
		case FODMenuHit::Prev: Menu->SelectRow(H.Id); Menu->Adjust(-1); break;
		case FODMenuHit::Next: Menu->SelectRow(H.Id); Menu->Adjust(+1); break;
		default: Menu->SelectRow(H.Id, H.Col); Menu->Activate(); break;
		}
	}
	else if (PC->WasInputKeyJustPressed(EKeys::RightMouseButton))
	{
		Menu->Back();
	}
	if (PC->WasInputKeyJustPressed(EKeys::MouseScrollUp))
	{
		Menu->Up();
	}
	else if (PC->WasInputKeyJustPressed(EKeys::MouseScrollDown))
	{
		Menu->Down();
	}
}
}

float AODRaceHUD::MenuScale() const
{
	return Canvas ? FMath::Clamp(Canvas->SizeY / 1080.0f, 0.6f, 2.5f) : 1.0f;
}

float AODRaceHUD::MenuX(bool bRight, float W) const
{
	const float S = MenuScale();
	return bRight ? Canvas->SizeX - ColX * S - W : ColX * S;
}

void AODRaceHUD::AddMenuHit(FODMenuHit::EKind Kind, int32 Id, int32 Col, float X, float Y, float W, float H) const
{
	MenuHits.Add({Kind, Id, Col, X, Y, X + W, Y + H});
}

void AODRaceHUD::HandleMenuMouse(AODMainMenu* Menu)
{
	DispatchMenuMouse(Menu, GetOwningPlayerController(), MenuHits, LastMenuMouse, LastMenuMouseFrame);
}

void AODRaceHUD::HandleMenuMouse(AODPauseMenu* Menu)
{
	DispatchMenuMouse(Menu, GetOwningPlayerController(), MenuHits, LastMenuMouse, LastMenuMouseFrame);
}

void AODRaceHUD::DrawMenuBackdrop(float Dim, bool bRight) const
{
	const FMenuPaint P{Canvas, MenuScale()};
	if (Dim > 0.0f)
	{
		P.Rect(0, 0, Canvas->SizeX, Canvas->SizeY, FLinearColor(0, 0, 0, Dim));
	}
	const float Reach = (ColX + ColW + 260.0f) * P.S;
	if (bRight)
	{
		P.Fade(Canvas->SizeX, Canvas->SizeX - Reach, 0, Canvas->SizeY, Ink, 0.9f, 0.0f);
	}
	else
	{
		P.Fade(0, Reach, 0, Canvas->SizeY, Ink, 0.9f, 0.0f);
	}
}

float AODRaceHUD::DrawMenuHeader(const FString& Title, TArrayView<const FODMenuTab> Tabs, int32 ActiveTab, float X) const
{
	const FMenuPaint P{Canvas, MenuScale()};
	const float S = P.S;
	const float TitleY = 150.0f * S;
	P.Text(Title.ToUpper(), X, TitleY, 44.0f, EMenuWeight::Bold, FLinearColor::White, false, 60);
	P.Rect(X, TitleY + 30.0f * S, 56.0f * S, 4.0f * S, Accent);
	if (Tabs.Num() <= 1)
	{
		return 215.0f * S;
	}

	// tab strip: Q [TAB] [TAB] ... E, each tab clickable
	const float TabY = 226.0f * S;
	float TX = X + P.Key(TEXT("Q"), X, TabY) + 18.0f * S;
	for (int32 i = 0; i < Tabs.Num(); ++i)
	{
		const bool bActive = i == ActiveTab;
		const float TW = P.Width(Tabs[i].Name, 20.0f, EMenuWeight::SemiBold, 80);
		P.Text(Tabs[i].Name, TX, TabY, 20.0f, EMenuWeight::SemiBold, bActive ? FLinearColor::White : DimCol, false, 80);
		if (bActive)
		{
			P.Rect(TX, TabY + 16.0f * S, TW, 3.0f * S, Accent);
		}
		AddMenuHit(FODMenuHit::Tab, i, -1, TX - 10.0f * S, TabY - 20.0f * S, TW + 20.0f * S, 40.0f * S);
		TX += TW + 30.0f * S;
	}
	P.Key(TEXT("E"), TX - 12.0f * S, TabY);
	return 275.0f * S;
}

void AODRaceHUD::DrawMenuFooter(const FString& Label, int32 Id, bool bSel, float X, float Y, float W) const
{
	const FMenuPaint P{Canvas, MenuScale()};
	const float H = FooterH * P.S;
	P.Rect(X, Y, W, H, bSel ? Accent : FLinearColor(1, 1, 1, 0.08f));
	P.Text(Label.ToUpper(), X + W * 0.5f, Y + H * 0.5f, 22.0f, EMenuWeight::Bold,
		bSel ? Ink : TextCol, true, 120);
	AddMenuHit(FODMenuHit::Row, Id, -1, X, Y, W, H);
}

void AODRaceHUD::DrawMenuPanel(const FString& Title, TArrayView<const FODMenuTab> Tabs, int32 ActiveTab,
	int32 Footer, int32 Selected, TFunctionRef<void(int32, FString&, FString&)> GetRow, bool bRight) const
{
	const FMenuPaint P{Canvas, MenuScale()};
	const float S = P.S;
	const float W = ColW * S;
	const float X = MenuX(bRight, W);
	float Y = DrawMenuHeader(Title, Tabs, ActiveTab, X);

	const float H = RowH * S;
	const float VX0 = X + W - 300.0f * S, VX1 = X + W - 16.0f * S;   // value column
	const FODMenuTab& Tab = Tabs[FMath::Clamp(ActiveTab, 0, Tabs.Num() - 1)];
	for (int32 Row = Tab.First; Row < Tab.First + Tab.Count; ++Row, Y += H)
	{
		FString Label, Value;
		GetRow(Row, Label, Value);
		const bool bSel = Row == Selected;
		const float CY = Y + H * 0.5f;
		if (bSel)
		{
			P.Fade(X, X + W, Y, H, Accent, 0.2f, 0.03f);
			P.Rect(X, Y, 4.0f * S, H, Accent);
		}
		P.Text(Label, X + 22.0f * S, CY, 24.0f, bSel ? EMenuWeight::SemiBold : EMenuWeight::Medium,
			bSel ? FLinearColor::White : TextCol);
		AddMenuHit(FODMenuHit::Row, Row, -1, X, Y, W, H);

		if (Value.IsEmpty())   // action row: opens something
		{
			P.Arrow(VX1 - 6.0f * S, CY, +1.0f, bSel ? Accent : DimCol);
			continue;
		}
		const FLinearColor ArrowCol = bSel ? Accent : DimCol * FLinearColor(1, 1, 1, 0.6f);
		P.Arrow(VX0 + 8.0f * S, CY, -1.0f, ArrowCol);
		P.Arrow(VX1 - 8.0f * S, CY, +1.0f, ArrowCol);
		AddMenuHit(FODMenuHit::Prev, Row, -1, VX0 - 10.0f * S, Y, 40.0f * S, H);
		AddMenuHit(FODMenuHit::Next, Row, -1, VX1 - 30.0f * S, Y, 40.0f * S, H);

		const float IX0 = VX0 + 30.0f * S, IX1 = VX1 - 30.0f * S;
		const FLinearColor ValCol = bSel ? Accent : TextCol;
		if (const float Pct = ParsePercent(Value); Pct >= 0.0f)
		{
			const float TX1 = IX1 - 62.0f * S;
			P.Rect(IX0, CY - 2.0f * S, TX1 - IX0, 4.0f * S, FLinearColor(1, 1, 1, 0.14f));
			P.Rect(IX0, CY - 2.0f * S, (TX1 - IX0) * Pct, 4.0f * S, bSel ? Accent : TextCol * FLinearColor(1, 1, 1, 0.7f));
			P.Rect(IX0 + (TX1 - IX0) * Pct - 2.0f * S, CY - 8.0f * S, 4.0f * S, 16.0f * S, bSel ? FLinearColor::White : TextCol);
			P.Text(Value, IX1 - 26.0f * S, CY, 20.0f, EMenuWeight::SemiBold, ValCol, true);
		}
		else
		{
			P.Text(Value, (IX0 + IX1) * 0.5f, CY, 22.0f, EMenuWeight::SemiBold, ValCol, true);
		}
	}

	FString FootLabel, FootValue;
	GetRow(Footer, FootLabel, FootValue);
	DrawMenuFooter(FootLabel, Footer, Selected == Footer, X, Y + 22.0f * S, W);
}

void AODRaceHUD::DrawSettingsPanel(AODSettingsMenu* Menu, bool bRight) const
{
	if (!Menu)
	{
		return;
	}
	if (Menu->IsRebindOpen())
	{
		DrawRebindPanel(Menu->GetRebindMenu(), bRight);
		return;
	}
	if (Menu->IsCreditsOpen())
	{
		DrawCreditsPanel(Menu->GetCreditsMenu(), bRight);
		return;
	}
	DrawMenuPanel(TEXT("Settings"), Menu->GetTabs(), Menu->GetTab(), Menu->NumRows() - 1, Menu->GetSelected(),
		[Menu](int32 Row, FString& L, FString& V) { Menu->GetRow(Row, L, V); }, bRight);
	const FMenuPaint P{Canvas, MenuScale()};
	DrawHints(P, MenuX(bRight, ColW * P.S), {{TEXT("Q/E"), TEXT("Tabs")}, {TEXT("Arrows"), TEXT("Select / Change")},
		{TEXT("Enter"), TEXT("Confirm")}, {TEXT("Esc"), TEXT("Back")}});
}

void AODRaceHUD::DrawRebindPanel(AODRebindMenu* Menu, bool bRight) const
{
	if (!Menu)
	{
		return;
	}
	const FMenuPaint P{Canvas, MenuScale()};
	const float S = P.S;
	const float W = ColW * S;
	const float X = MenuX(bRight, W);
	float Y = DrawMenuHeader(TEXT("Key Bindings"), {}, 0, X);

	const float H = 42.0f * S;
	const float KX0 = X + W * 0.40f;
	const float CellW = (X + W - KX0) * 0.5f;
	P.Text(TEXT("KEYBOARD"), KX0 + CellW * 0.5f, Y + 10.0f * S, 15.0f, EMenuWeight::SemiBold, DimCol, true, 120);
	P.Text(TEXT("GAMEPAD"), KX0 + CellW * 1.5f, Y + 10.0f * S, 15.0f, EMenuWeight::SemiBold, DimCol, true, 120);
	Y += 34.0f * S;

	// capture pulse on real time - the game is paused, world time stands still
	const float Pulse = 0.55f + 0.35f * FMath::Sin(FPlatformTime::Seconds() * 6.0);
	for (int32 Row = 0; Row < Menu->ResetRow(); ++Row, Y += H)
	{
		const bool bRowSel = Row == Menu->GetSelected();
		const float CY = Y + H * 0.5f;
		if (bRowSel)
		{
			P.Rect(X, Y, 4.0f * S, H, Accent);
		}
		P.Text(Menu->GetLabel(Row), X + 22.0f * S, CY, 21.0f, bRowSel ? EMenuWeight::SemiBold : EMenuWeight::Medium,
			bRowSel ? FLinearColor::White : TextCol);
		for (int32 Col = 0; Col < 2; ++Col)
		{
			const float CX = KX0 + CellW * Col;
			const bool bCap = Menu->IsCapturingCell(Row, Col);
			const bool bCellSel = bRowSel && Col == Menu->GetColumn();
			P.Rect(CX + 3.0f * S, Y + 3.0f * S, CellW - 6.0f * S, H - 6.0f * S,
				bCap ? FLinearColor(Accent.R, Accent.G, Accent.B, Pulse * 0.5f)
				: bCellSel ? FLinearColor(Accent.R, Accent.G, Accent.B, 0.25f) : FLinearColor(1, 1, 1, 0.05f));
			FString Bind = Menu->GetBinding(Row, Col);
			Bind.RemoveFromStart(TEXT("Gamepad "));
			P.Text(Bind, CX + CellW * 0.5f, CY, 19.0f, EMenuWeight::SemiBold,
				bCap || bCellSel ? FLinearColor::White : TextCol, true);
			AddMenuHit(FODMenuHit::Cell, Row, Col, CX, Y, CellW, H);
		}
	}

	// Reset row, then the Back footer
	const int32 Reset = Menu->ResetRow();
	const bool bResetSel = Menu->GetSelected() == Reset;
	if (bResetSel)
	{
		P.Fade(X, X + W, Y, H, Accent, 0.2f, 0.03f);
		P.Rect(X, Y, 4.0f * S, H, Accent);
	}
	P.Text(Menu->GetLabel(Reset), X + 22.0f * S, Y + H * 0.5f, 21.0f,
		bResetSel ? EMenuWeight::SemiBold : EMenuWeight::Medium, bResetSel ? FLinearColor::White : TextCol);
	AddMenuHit(FODMenuHit::Row, Reset, -1, X, Y, W, H);
	Y += H;
	DrawMenuFooter(Menu->GetLabel(Menu->BackRow()), Menu->BackRow(), Menu->GetSelected() == Menu->BackRow(), X, Y + 22.0f * S, W);

	if (Menu->IsCapturing())
	{
		DrawHints(P, X, {{TEXT("Any key"), TEXT("Assign")}, {TEXT("Esc"), TEXT("Cancel")}});
	}
	else
	{
		DrawHints(P, X, {{TEXT("Arrows"), TEXT("Select")}, {TEXT("Enter"), TEXT("Rebind")}, {TEXT("Esc"), TEXT("Back")}});
	}
}

void AODRaceHUD::DrawCreditsPanel(AODCreditsMenu* Menu, bool bRight) const
{
	if (!Menu)
	{
		return;
	}
	const FMenuPaint P{Canvas, MenuScale()};
	const float S = P.S;
	const float W = FMath::Min(900.0f * S, Canvas->SizeX - 2.0f * ColX * S);
	const float X = MenuX(bRight, W);
	float Y = DrawMenuHeader(TEXT("Credits"), {}, 0, X);

	const float LineH = 30.0f * S;
	const TArray<FString>& Lines = Menu->GetLines();
	const int32 Start = Menu->GetScrollLine();
	const int32 End = FMath::Min(Start + AODCreditsMenu::VisibleLines, Lines.Num());
	const float Y0 = Y;
	for (int32 i = Start; i < End; ++i, Y += LineH)
	{
		P.Text(Lines[i], X, Y + LineH * 0.5f, 17.0f, EMenuWeight::Medium, TextCol);
	}

	// scroll bar
	const float TrackH = AODCreditsMenu::VisibleLines * LineH;
	if (const int32 MaxScroll = Lines.Num() - AODCreditsMenu::VisibleLines; MaxScroll > 0)
	{
		const float ThumbH = TrackH * AODCreditsMenu::VisibleLines / Lines.Num();
		P.Rect(X + W - 3.0f * S, Y0, 3.0f * S, TrackH, FLinearColor(1, 1, 1, 0.1f));
		P.Rect(X + W - 3.0f * S, Y0 + (TrackH - ThumbH) * Start / MaxScroll, 3.0f * S, ThumbH, Accent);
	}
	DrawMenuFooter(TEXT("Back"), INDEX_NONE, true, X, Y0 + TrackH + 22.0f * S, FMath::Min(W, ColW * S));
	DrawHints(P, X, {{TEXT("Arrows / Wheel"), TEXT("Scroll")}, {TEXT("Esc"), TEXT("Back")}});
}

void AODRaceHUD::DrawMainMenu(AODMainMenu* Menu, const TArray<FVector2D>* Centerline,
	const TArray<FTransform>* Gates, AODRoadManager* RoadMgr) const
{
	if (!Canvas || !Menu)
	{
		return;
	}
	const FMenuPaint P{Canvas, MenuScale()};
	const float S = P.S;

	// ---- loading: waiting on IsGroundSnapped() after a teleport (2026-07-26),
	// PLUS AODTerrainManager/AODRoadManager's own initial-streaming settle
	// (2026-08-27, spawn-loading-screen task) - fully opaque so the player
	// never sees the car airborne/popping in, or the world visibly filling in
	// around it, while a fresh area's road/terrain tiles are still streaming.
	if (Menu->IsLoading())
	{
		P.Rect(0, 0, Canvas->SizeX, Canvas->SizeY, FLinearColor(Ink.R, Ink.G, Ink.B, 1.0f));
		const float CX = Canvas->SizeX * 0.5f;
		const float CY = Canvas->SizeY * 0.5f;
		P.Text(TEXT("LOADING WORLD"), CX, CY - 24.0f * S, 40.0f, EMenuWeight::Bold, FLinearColor::White, true, 200);

		int32 TerrainReady = 0, TerrainTotal = 0, RoadReady = 0, RoadTotal = 0;
		Menu->GetLoadingProgress(TerrainReady, TerrainTotal, RoadReady, RoadTotal);
		const float BarW = 360.0f * S;
		P.Rect(CX - BarW * 0.5f, CY + 16.0f * S, BarW, 3.0f * S, FLinearColor(1, 1, 1, 0.12f));
		if (TerrainTotal + RoadTotal > 0)
		{
			const float Frac = float(TerrainReady + RoadReady) / float(TerrainTotal + RoadTotal);
			P.Rect(CX - BarW * 0.5f, CY + 16.0f * S, BarW * FMath::Clamp(Frac, 0.0f, 1.0f), 3.0f * S, Accent);
			P.Text(FString::Printf(TEXT("Terrain %d/%d  ·  Roads %d/%d"), TerrainReady, TerrainTotal, RoadReady, RoadTotal),
				CX, CY + 46.0f * S, 18.0f, EMenuWeight::Medium, DimCol, true);
		}
		return;
	}

	// ---- menu screens: left column (right over the Scene vista, so the car stays clear)
	if (Menu->GetStage() == EODMenuStage::Menu)
	{
		const bool bVista = Menu->IsVistaShown();
		DrawMenuBackdrop(bVista ? 0.0f : 0.2f, bVista);
		if (Menu->IsSettingsOpen())
		{
			DrawSettingsPanel(Menu->GetSettingsMenu(), bVista);
			return;
		}
		const EODMenuScreen Screen = Menu->GetScreen();
		DrawMenuPanel(Screen == EODMenuScreen::Garage ? TEXT("Garage")
				: Screen == EODMenuScreen::Sound ? TEXT("Exhaust Sound")
				: Screen == EODMenuScreen::Scene ? TEXT("Select Scene") : TEXT("OpenDrive"),
			Menu->GetTabs(), Menu->GetTab(), Menu->GetFooterRow(), Menu->GetSelected(),
			[Menu](int32 Row, FString& L, FString& V) { Menu->GetRow(Row, L, V); }, bVista);
		const float HX = MenuX(bVista, ColW * S);
		if (Screen == EODMenuScreen::Main)
		{
			DrawHints(P, HX, {{TEXT("Arrows"), TEXT("Select")}, {TEXT("Enter"), TEXT("Confirm")}});
		}
		else
		{
			DrawHints(P, HX, {{TEXT("Arrows"), TEXT("Select / Change")}, {TEXT("Enter"), TEXT("Confirm")},
				{TEXT("Esc"), Screen == EODMenuScreen::Garage && Menu->IsGarageMode() ? TEXT("Resume") : TEXT("Back")}});
		}
		return;
	}

	// ---- map select (QA hook only): full route + clickable town nodes ----
	UFont* Font = GEngine->GetLargeFont();
	P.Rect(0, 0, Canvas->SizeX, Canvas->SizeY, FLinearColor(0, 0, 0, 0.55f));
	auto Text = [&](const FString& Str, float X, float Y, float Sc, const FLinearColor& C)
	{
		FCanvasTextItem T(FVector2D(X, Y), FText::FromString(Str), CrispFont(Font, Sc), C);
		T.EnableShadow(FLinearColor::Black);
		Canvas->DrawItem(T);
	};
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
			const FVector2D Pt = Fit.ToMap(Towns[i].Pos);
			const bool bH = i == Hover;
			const float R = bH ? 6.0f : 3.5f;
			FCanvasTileItem Dot(Pt - FVector2D(R, R), FVector2D(2 * R, 2 * R),
				bH ? FLinearColor(1.0f, 0.85f, 0.3f) : FLinearColor(0.6f, 0.75f, 0.95f));
			Dot.BlendMode = SE_BLEND_Translucent;
			Canvas->DrawItem(Dot);
			if (bH)
			{
				Text(Towns[i].Name, Pt.X + 12.0f, Pt.Y - 10.0f, 1.2f,
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

// ---- Arc 4.2 v3: world map + navigation (next steps.md SS7.37) ----

namespace
{
// miles/feet or km/m, matching the speedo's units
FString FormatNavDist(float Cm, bool bMetric)
{
	const float M = Cm / 100.0f;
	if (bMetric)
	{
		return M < 1000.0f
			? FString::Printf(TEXT("%d m"), FMath::Max(10, FMath::RoundToInt(M / 10.0f) * 10))
			: FString::Printf(TEXT("%.1f km"), M / 1000.0f);
	}
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
				*FormatNavDist(RoadMgr->GetRouteRemainingCm(), bMetric))
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
			Side2, *FormatNavDist(TurnCm, bMetric));
	Text(Line1, X + 80.0f, Y + 10.0f, 1.5f, FLinearColor::White);
	Text(FString::Printf(TEXT("%s  ·  %s"), *RoadMgr->GetRouteName(), *FormatNavDist(RoadMgr->GetRouteRemainingCm(), bMetric)),
		X + 80.0f, Y + 44.0f, 1.05f, FLinearColor(1.0f, 0.7f, 0.92f));
}

void AODRaceHUD::DrawHUD()
{
	Super::DrawHUD();
	if (!Canvas)
	{
		return;
	}
	MenuHits.Reset();   // refilled by whichever menu draws this frame

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
	const bool bRaceMap = Race && Race->RoadCenterline.Num() > 0;   // a custom track has none
	if (!bRaceMap)
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
	const TArray<FVector2D>* MapCenterline = bRaceMap ? &Race->RoadCenterline
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
			DrawMainMenu(*It, MapCenterline, MapGates, RoadMgr);   // Settings included
			if (It->GetStage() == EODMenuStage::Menu)
			{
				HandleMenuMouse(*It);
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

	// draws PlaceLabelText just above the mini-map's top edge (the pause
	// map draws its own in the menu font); no-ops on an empty
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

	// ---- pause menu (AODPauseMenu owns state): the shared left column, the
	// full stage map on the right - it draws from whichever route source
	// (Race or RoadMgr) is available, and no-ops when there's none
	for (TActorIterator<AODPauseMenu> It(GetWorld()); It; ++It)
	{
		AODPauseMenu* Menu = *It;
		if (!Menu->IsOpen())
		{
			break;
		}
		DrawMenuBackdrop(0.35f, false);
		if (Menu->IsSettingsOpen())
		{
			DrawSettingsPanel(Menu->GetSettingsMenu(), false);
		}
		else
		{
			DrawMenuPanel(TEXT("Paused"), PauseTabs, 0, Menu->GetFooterRow(), Menu->GetSelected(),
				[Menu](int32 Row, FString& L, FString& V) { Menu->GetRow(Row, L, V); }, false);
			const float S = MenuScale();
			DrawHints(FMenuPaint{Canvas, S}, MenuX(false, ColW * S),
				{{TEXT("Arrows"), TEXT("Select")}, {TEXT("Enter"), TEXT("Confirm")}, {TEXT("Esc"), TEXT("Resume")}});

			const float FullSize = FMath::Min(520.0f * S, Canvas->SizeY - 300.0f * S);
			const float FullMapX = Canvas->SizeX - ColX * S - FullSize;
			const float FullMapY = (Canvas->SizeY - FullSize) * 0.5f;
			if (!PlaceLabelText.IsEmpty())
			{
				const FMenuPaint P{Canvas, S};
				P.Text(PlaceLabelText.ToUpper(), FullMapX, FullMapY - 24.0f * S, 18.0f, EMenuWeight::SemiBold, DimCol, false, 60);
				P.Rect(FullMapX, FullMapY - 10.0f * S, 56.0f * S, 4.0f * S, Accent);
			}
			DrawStageMap(MapCenterline, MapGates, Race && Race->bRunning,
				Race ? Race->NextGate : INDEX_NONE, FullMapX, FullMapY, FullSize,
				0.55f, 0.0f, false, RoadMgr);
		}
		HandleMenuMouse(Menu);
		return;   // drawn instead of the in-game HUD, like the main menu
	}

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

	// ---- gauge cluster (ODGaugeCluster.cpp) - only needs the owning car
	if (const AODVehiclePawn* Veh = Cast<AODVehiclePawn>(GetOwningPawn()))
	{
		if (const UODVehicleMovement* Mv = Veh->Movement)
		{
			FODGaugeData G;
			G.SpeedCmS = FMath::Abs(Mv->GetForwardSpeed());
			// sum of v*dt: frame-rate independent; world dt is 0 while paused
			TripCm += G.SpeedCmS * GetWorld()->GetDeltaSeconds();
			G.TripCm = TripCm;
			G.Rpm = Mv->GetRpm();
			G.RedlineRpm = Mv->LimiterRpm;
			G.MaxRpm = Mv->MaxRpm;
			G.Gear = Mv->GetGear();
			G.bAbsOff = !Mv->bAbsEnabled;
			G.bTcOff = Mv->TractionMode == EODTractionMode::Off;
			G.bTcActive = Mv->IsTractionIntervening();
			G.bBrake = Mv->IsHandbrakeOn();
			G.bMetric = bMetric;
			for (TActorIterator<AODWeatherController> It(GetWorld()); It; ++It)
			{
				G.bHasAirTemp = true;
				G.AirTempC = It->AirTempC;
				break;
			}
			ODGaugeCluster::Draw(Canvas, Font, G, Now);
		}
	}

	// ---- stage mini-map (Arc 4.2 v1; M toggles, hidden behind the pause
	// map) - draws from Race if a checkpoint race exists, else falls back to
	// AODRoadManager's route-only centerline (open-world levels); a
	// real open-world map (Arc 7.4's planned NA map view) is a separate,
	// bigger feature, not something this stage-shaped map can grow into
	// as-is.
	if (bShowMiniMap)
	{
		DrawPlaceLabel(24.0f, Canvas->SizeY - MiniMapSize - 24.0f);
		DrawStageMap(MapCenterline, MapGates, Race && Race->bRunning,
			Race ? Race->NextGate : INDEX_NONE, 24.0f,
			Canvas->SizeY - MiniMapSize - 24.0f, MiniMapSize, 0.55f,
			MiniMapZoomM, true, RoadMgr);
	}

	// ---- navigation next-turn / distance panel (SS7.37)
	if (const AODRoadManager* NavMgr = FindNavRoadMgr(); NavMgr)
	{
		DrawNavPanel(NavMgr);   // nearest area's road (2026-10-05, large world)
	}
}
