#include "ODRaceHUD.h"
#include "ODRoadTrip.h"

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
#include "Misc/ScopeExit.h"
#include "ODCreditsMenu.h"
#include "ODDayNightController.h"
#include "ODGaugeCluster.h"
#include "ODGhost.h"
#include "ODInputConfig.h"
#include "ODMainMenu.h"
#include "ODMenuGlobe.h"
#include "ODPauseMenu.h"
#include "ODVehicleMovement.h"
#include "ODVehiclePawn.h"
#include "ODChallengeManager.h"
#include "ODRaceManager.h"
#include "ODReplay.h"
#include "ODRebindMenu.h"
#include "ODRoadManager.h"
#include "ODSettingsMenu.h"
#include "ODPhotoMode.h"
#include "UnrealClient.h"
#include "ODWeatherController.h"
#include "ODHudStyle.h"
#include "ODBakeLink.h"
#include "CompGeom/Delaunay2.h"
#include "Curve/GeneralPolygon2.h"
#include "Rendering/SlateRenderer.h"

using namespace ODHud;

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
		{EODInputAction::PhotoMode, &AODRaceHUD::TogglePhotoMode, true},
		{EODInputAction::MediaPlayPause, &AODRaceHUD::MediaPlayPause, false},
		{EODInputAction::MediaNext, &AODRaceHUD::MediaNext, false},
		{EODInputAction::MediaPrev, &AODRaceHUD::MediaPrev, false},
		{EODInputAction::Replay, &AODRaceHUD::ToggleReplay, false},
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

// Drawn area's outside on the maps (2026-10-23): the region between a box
// 200 km past the outline and the outline itself, as world-space triangles
// (3 points each). Empty when the outline doesn't triangulate (self-crossing).
TArray<FVector2D> OutsideTriangles(const TArray<FVector2D>& Outline)
{
	using namespace UE::Geometry;
	TArray<FVector2d> Hole;
	FBox2D Box(ForceInit);
	for (const FVector2D& P : Outline)
	{
		if (Hole.Num() == 0 || FVector2D::DistSquared(Hole.Last(), P) > 1.0)
		{
			Hole.Add(P);
			Box += P;
		}
	}
	if (Hole.Num() > 1 && FVector2D::DistSquared(Hole[0], Hole.Last()) <= 1.0)
	{
		Hole.Pop();
	}
	TArray<FVector2D> Out;
	if (Hole.Num() < 3)
	{
		return Out;
	}
	Box = Box.ExpandBy(2.0e7);
	FGeneralPolygon2d Poly(FPolygon2d(TArray<FVector2d>{Box.Min, FVector2d(Box.Max.X, Box.Min.Y), Box.Max, FVector2d(Box.Min.X, Box.Max.Y)}));
	FPolygon2d HolePoly(Hole);
	if (HolePoly.IsClockwise() == Poly.OuterIsClockwise())
	{
		HolePoly.Reverse();
	}
	Poly.AddHole(HolePoly, false, false);
	FDelaunay2 Delaunay;
	TArray<FIndex3i> Tris;
	TArray<FVector2d> Verts;
	if (!Delaunay.Triangulate(Poly, &Tris, &Verts))
	{
		return Out;
	}
	for (const FIndex3i& T : Tris)
	{
		Out.Append({Verts[T.A], Verts[T.B], Verts[T.C]});
	}
	return Out;
}

// Sutherland-Hodgman: Poly clipped to the convex polygon Clip (same winding).
TArray<FVector2D> ClipToConvex(TArray<FVector2D> Poly, const TArray<FVector2D>& Clip)
{
	for (int32 e = 0; e < Clip.Num() && Poly.Num() > 0; ++e)
	{
		const FVector2D A = Clip[e], B = Clip[(e + 1) % Clip.Num()];
		auto Side = [&](const FVector2D& P) { return FVector2D::CrossProduct(B - A, P - A); };
		TArray<FVector2D> In = MoveTemp(Poly);
		Poly.Reset();
		for (int32 i = 0; i < In.Num(); ++i)
		{
			const FVector2D P = In[i], Q = In[(i + 1) % In.Num()];
			const double Sp = Side(P), Sq = Side(Q);
			if (Sp >= 0.0)
			{
				Poly.Add(P);
			}
			if ((Sp >= 0.0) != (Sq >= 0.0))
			{
				Poly.Add(P + (Q - P) * (Sp / (Sp - Sq)));
			}
		}
	}
	return Poly;
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

// any world rect (Lo..Hi cm) fit the same way - the Area panel fits its
// relief image's rect so image, roads and dots share one transform
FMapFit ComputeBoxMapFit(const FVector2D& Lo, const FVector2D& Hi,
	float PanelX, float PanelY, float PanelSize)
{
	const float Pad = 14.0f;
	const float Span = FMath::Max(FMath::Max(Hi.X - Lo.X, Hi.Y - Lo.Y), 1.0f);
	FMapFit F;
	F.Scale = (PanelSize - 2.0f * Pad) / Span;
	F.Center = (Lo + Hi) * 0.5f;
	F.PanelMid = FVector2D(PanelX + PanelSize * 0.5f, PanelY + PanelSize * 0.5f);
	return F;
}

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
	return ComputeBoxMapFit(Lo, Hi, PanelX, PanelY, PanelSize);
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
			Center += FVector2D(FMath::Cos(Yaw), FMath::Sin(Yaw)) * (ZoomRadiusM * 100.0f * 0.45f);
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
	const float UiS = MenuScale();
	auto Dot = [&](const FVector2D& M, float R, const FLinearColor& C)
	{
		ODHudDraw::Disc(Canvas, M, R * UiS, C);
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
		const float R = PanelSize * 0.5f;
		// No terrain on the minimap (it's for reading the road ahead): a plain
		// neutral lens, light by day and deep by night. Radial (centre clearer,
		// rim heavier) and lit from the top so it doesn't read as a flat sticker.
		float Night = 0.0f;
		for (TActorIterator<AODDayNightController> It(GetWorld()); It; ++It)
		{
			Night = It->GetNight();
			break;
		}
		const FLinearColor Wash = FMath::Lerp(FLinearColor(0.5f, 0.51f, 0.53f, 0.78f),
			FLinearColor(0.012f, 0.015f, 0.024f, 0.82f), Night);
		ODHudDraw::Ring(Canvas, PanelMid, 0.0f, R, 0.0f, 2.0f * PI, [&](float A, float T)
		{
			const float Lit = FMath::Cos(A) * T;   // +1 at the top rim .. -1 at the bottom
			FLinearColor C = Wash * (1.0f + 0.2f * Lit - 0.15f * T);
			C.A = FMath::Clamp(Wash.A + 0.1f * T, 0.0f, 1.0f);
			return C;
		});
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
	auto DrawRoads = [&](const TArray<FVector2D>& Line, const FLinearColor& Col, float Thick)
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
			Seg.SetColor(Col);
			Seg.LineThickness = Thick;
			Canvas->DrawItem(Seg);
		}
	};
	// Google-style cased roads: every dark casing first, then every light
	// fill, so junctions merge instead of each road cutting across the next
	// plus every other streamed area's map centerline (2026-10-05, large
	// world); the bbox fit above and the map texture stay home-only
	TArray<const TArray<FVector2D>*, TInlineAllocator<8>> RoadLines{&Smooth};
	if (RoadMgr)
	{
		for (TActorIterator<AODRoadManager> It(GetWorld()); It; ++It)
		{
			if (*It != RoadMgr && &It->MapCenterline != Centerline)
			{
				RoadLines.Add(&It->MapCenterline);
			}
		}
	}
	for (const TArray<FVector2D>* L : RoadLines) { DrawRoads(*L, FLinearColor(0.02f, 0.025f, 0.035f, 0.6f), 5.5f * UiS); }
	for (const TArray<FVector2D>* L : RoadLines) { DrawRoads(*L, FLinearColor(0.96f, 0.97f, 0.98f, 0.95f), 3.0f * UiS); }

	// drawn area's edge (2026-10-23): everything outside the outline dimmed,
	// the outline a thin light line over a dark casing
	if (const TSharedPtr<const TArray<FVector2D>, ESPMode::ThreadSafe> Outline = ODBakeLink::ActiveOutline();
		Outline.IsValid() && Outline->Num() >= 3)
	{
		if (OutsideTrisFor != Outline)
		{
			OutsideTris = OutsideTriangles(*Outline);
			OutsideTrisFor = Outline;
		}
		// the panel as a convex clip polygon, wound like ClipToConvex expects
		// (screen y down: clockwise on screen = positive cross)
		TArray<FVector2D> Clip;
		if (bCircular)
		{
			for (int32 k = 0; k < 48; ++k)
			{
				const float A = 2.0f * PI * k / 48;
				Clip.Add(PanelMid + FVector2D(FMath::Cos(A), FMath::Sin(A)) * (PanelSize * 0.5f));
			}
		}
		else
		{
			const FVector2D Lo(FMath::Max(PanelX, 0.0f), FMath::Max(PanelY, 0.0f));
			const FVector2D Hi(FMath::Min(PanelX + PanelSize, Canvas->ClipX), FMath::Min(PanelY + PanelSize, Canvas->ClipY));
			Clip = {Lo, FVector2D(Hi.X, Lo.Y), Hi, FVector2D(Lo.X, Hi.Y)};
		}
		FBox2D ClipBox(Clip);
		const FLinearColor Shade(0.0f, 0.0f, 0.0f, 0.5f);
		TArray<FCanvasUVTri> Tris;
		for (int32 i = 0; i + 2 < OutsideTris.Num(); i += 3)
		{
			TArray<FVector2D> P = {ToMap(FVector(OutsideTris[i], 0)), ToMap(FVector(OutsideTris[i + 1], 0)), ToMap(FVector(OutsideTris[i + 2], 0))};
			if (!ClipBox.Intersect(FBox2D(P)))
			{
				continue;
			}
			if (FVector2D::CrossProduct(P[1] - P[0], P[2] - P[0]) < 0.0)
			{
				Swap(P[1], P[2]);
			}
			P = ClipToConvex(MoveTemp(P), Clip);
			for (int32 k = 2; k < P.Num(); ++k)
			{
				FCanvasUVTri& T = Tris.AddDefaulted_GetRef();
				T.V0_Pos = P[0]; T.V1_Pos = P[k - 1]; T.V2_Pos = P[k];
				T.V0_Color = T.V1_Color = T.V2_Color = Shade;
			}
		}
		if (Tris.Num() > 0)
		{
			FCanvasTriangleItem Item(Tris, GWhiteTexture);
			Item.BlendMode = SE_BLEND_Translucent;
			Canvas->DrawItem(Item);
		}
		TArray<FVector2D> Ring = *Outline;
		Ring.Add((*Outline)[0]);
		DrawRoads(Ring, FLinearColor(0.02f, 0.025f, 0.035f, 0.5f), 4.0f * UiS);
		DrawRoads(Ring, FLinearColor(0.9f, 0.93f, 0.97f, 0.85f), 1.5f * UiS);
	}

	// navigation route (SS7.37) - on every map panel (mini, pause, world);
	// from the nav (nearest) road manager (2026-10-05, large world)
	if (const AODRoadManager* NavMgr = FindNavRoadMgr(); NavMgr && NavMgr->HasRoute())
	{
		const TArray<FVector2D>& R = NavMgr->GetRoutePoints();
		for (int32 Pass = 0; Pass < 2; ++Pass)
		{
			for (int32 i = 1; i < R.Num(); ++i)
			{
				FVector2D A = ToMap(FVector(R[i - 1], 0));
				FVector2D B = ToMap(FVector(R[i], 0));
				if (!ClipToPanel(A, B))
				{
					continue;
				}
				FCanvasLineItem Seg(A, B);
				Seg.SetColor(Pass == 0 ? FLinearColor(0.02f, 0.025f, 0.035f, 0.6f) : Accent.CopyWithNewOpacity(0.95f));
				Seg.LineThickness = (Pass == 0 ? 7.5f : 5.0f) * UiS;
				Canvas->DrawItem(Seg);
			}
		}
		const FVector2D DestM = ToMap(FVector(R.Last(), 0));
		if (!bCircular || InPanel(DestM))
		{
			Dot(DestM, 6.0f, Accent);
			Dot(DestM, 2.5f, FLinearColor::White);
		}
	}

	// speed traps/zones and landmark pins (fun-features #2/#5)
	if (const AODChallengeManager* Challenges = AODChallengeManager::Find(GetWorld()))
	{
		Challenges->DrawMapIcons(Canvas, UiS, ToMap, [&](const FVector2D& M) { return !bCircular || InPanel(M); });
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
		const float A = 10.5f * UiS;
		const FVector2D Tri2[3] = {P + Fwd * A * 1.6f, P - Fwd * A + Side * A, P - Fwd * A - Side * A};
		ODHudDraw::Poly(Canvas, Tri2, FLinearColor(0, 0, 0, 0.55f), 3.0f * UiS);
		ODHudDraw::Poly(Canvas, Tri2, FLinearColor::White);
		// dark core, white edge: reads on the light day lens and the dark night
		// one alike. 0.7 about the centroid leaves a ~2 px edge (inradius 0.69 A)
		const FVector2D Mid3 = (Tri2[0] + Tri2[1] + Tri2[2]) / 3.0f;
		const FVector2D Core[3] = {Mid3 + (Tri2[0] - Mid3) * 0.7f, Mid3 + (Tri2[1] - Mid3) * 0.7f,
			Mid3 + (Tri2[2] - Mid3) * 0.7f};
		ODHudDraw::Poly(Canvas, Core, FLinearColor(0.02f, 0.025f, 0.035f));
	}

	if (bCircular)
	{
		// thin ring bezel over a soft outer shadow
		const float R = PanelSize * 0.5f;
		ODHudDraw::Ring(Canvas, PanelMid, R, R + 10.0f * UiS, 0.0f, 2.0f * PI, [](float, float T)
		{
			return FLinearColor(0, 0, 0, 0.35f * (1.0f - T));
		});
		ODHudDraw::Ring(Canvas, PanelMid, R - 2.0f * UiS, R, 0.0f, 2.0f * PI, [](float, float)
		{
			return FLinearColor(1, 1, 1, 0.85f);
		});
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
// 1080p layout units (multiplied by MenuScale())
constexpr float ColX = 96.0f;      // column inset from the screen edge
constexpr float ColW = 640.0f;
constexpr float RowH = 54.0f;
constexpr float FooterH = 58.0f;

const FODMenuTab PauseTabs[] = {{TEXT("PAUSED"), AODPauseMenu::RowWorldMap, AODPauseMenu::RowResume}};
const FODMenuTab EnvTabs[] = {{TEXT("ENVIRONMENT"), 0, ODEnvironment::NumRows}};   // fun #7
const FODMenuTab PhotoTabs[] = {{TEXT("PHOTO"), 0, AODPhotoMode::RowCapture}};      // fun #4

// "65%" -> 0.65, anything else -> -1 (not drawn as a slider)
float ParsePercent(const FString& V)
{
	if (!V.EndsWith(TEXT("%")) || !V.LeftChop(1).IsNumeric())
	{
		return -1.0f;
	}
	return FMath::Clamp(FCString::Atof(*V.LeftChop(1)) / 100.0f, 0.0f, 1.0f);
}

// First visible row of a Count-row list showing Shown rows: moves only as far
// as needed to keep Sel in view (Sel outside 0..Count-1 = nothing to follow),
// so a hover-select never scrolls the row out from under the mouse.
constexpr int32 ScrollTop(int32 Top, int32 Sel, int32 Count, int32 Shown)
{
	if (Sel >= 0 && Sel < Count)
	{
		Top = FMath::Clamp(Top, Sel - Shown + 1, Sel);
	}
	return FMath::Clamp(Top, 0, FMath::Max(0, Count - Shown));
}
static_assert(ScrollTop(0, 20, 30, 10) == 11, "down just enough");
static_assert(ScrollTop(11, 5, 30, 10) == 5, "up just enough");
static_assert(ScrollTop(7, 12, 30, 10) == 7, "already visible: stays");
static_assert(ScrollTop(25, -1, 30, 10) == 20, "clamped to the end");
static_assert(ScrollTop(3, 2, 5, 10) == 0, "fits: no scroll");

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
		T.EnableShadow(FLinearColor(0, 0, 0, 0.45f * Col.A), FVector2D(S, S));   // fades with the text
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

	// small up (-1) / down (+1) chevron centred on X,Y (2026-10-14: list scroll cue)
	void Chevron(float X, float Y, float Dir, const FLinearColor& Col) const
	{
		FCanvasTriangleItem T(FVector2D(X, Y + 5.0f * S * Dir), FVector2D(X - 8.0f * S, Y - 4.0f * S * Dir),
			FVector2D(X + 8.0f * S, Y - 4.0f * S * Dir), GWhiteTexture);
		T.SetColor(Col);
		T.BlendMode = SE_BLEND_Translucent;
		C->DrawItem(T);
	}

	// horizontal fade, alpha A0 at X0 -> A1 at X1 (either direction): ONE quad
	// with per-vertex alpha. (2026-10-14) It used to be ~6 px translucent
	// strips overlapping by 0.5 px - every overlap column blended twice, so the
	// full-height menu backdrop showed a comb of dark vertical bars across the
	// whole left side, through the text (the pause menu report).
	void Fade(float X0, float X1, float Y, float H, FLinearColor Col, float A0, float A1) const
	{
		FLinearColor C0 = Col, C1 = Col;
		C0.A = A0;
		C1.A = A1;
		TArray<FCanvasUVTri> Tris;
		Tris.SetNum(2);
		Tris[0].V0_Pos = FVector2D(X0, Y);     Tris[0].V0_Color = C0;
		Tris[0].V1_Pos = FVector2D(X1, Y);     Tris[0].V1_Color = C1;
		Tris[0].V2_Pos = FVector2D(X1, Y + H); Tris[0].V2_Color = C1;
		Tris[1].V0_Pos = FVector2D(X0, Y);     Tris[1].V0_Color = C0;
		Tris[1].V1_Pos = FVector2D(X1, Y + H); Tris[1].V1_Color = C1;
		Tris[1].V2_Pos = FVector2D(X0, Y + H); Tris[1].V2_Color = C0;
		FCanvasTriangleItem T(Tris, GWhiteTexture);
		T.BlendMode = SE_BLEND_Translucent;
		C->DrawItem(T);
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

// Top-left-anchored text at Sc x the old Roboto-large size: the world map and
// QA map lines (2026-10-14, moved off the engine font onto Rajdhani)
void HudText(UCanvas* C, const FString& Str, float X, float Y, float Sc, const FLinearColor& Col)
{
	FMenuPaint{C, 1.0f}.Text(Str, X, Y + 13.0f * Sc, 22.0f * Sc, EMenuWeight::SemiBold, Col);
}

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
	FVector2D& LastMouse, uint64& LastFrame, bool bWheel = true,   // bWheel false: the caller owns the wheel
	bool bRightBack = true)                                           // false: the caller owns the right button (2026-10-21)
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
	else if (bRightBack && PC->WasInputKeyJustPressed(EKeys::RightMouseButton))
	{
		Menu->Back();
	}
	if (bWheel && PC->WasInputKeyJustPressed(EKeys::MouseScrollUp))
	{
		Menu->Up();
	}
	else if (bWheel && PC->WasInputKeyJustPressed(EKeys::MouseScrollDown))
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
	// World (2026-10-14): no rows - hover focuses the nearest pin, click picks
	// it, a drag (left on empty space, or right) spins the globe, wheel zooms
	APlayerController* PC = GetOwningPlayerController();
	AODMenuGlobe* Globe = Menu ? Menu->GetGlobe() : nullptr;
	if (Globe && Menu->GetScreen() == EODMenuScreen::World && !Menu->IsSettingsOpen())
	{
		float Mx = 0.0f, My = 0.0f;
		if (!PC || !PC->bShowMouseCursor || !PC->GetMousePosition(Mx, My))
		{
			bGlobeDrag = false;
			return;
		}
		const FVector2D Mouse(Mx, My), Prev = LastMenuMouse;
		const bool bFresh = GFrameCounter - LastMenuMouseFrame <= 1;
		const bool bMoved = bFresh && !Mouse.Equals(Prev, 0.5);
		LastMenuMouse = Mouse;
		LastMenuMouseFrame = GFrameCounter;

		int32 Hover = INDEX_NONE;
		float Best = FMath::Square(18.0f * MenuScale());
		for (int32 i = 0; i < Globe->GetPins().Num(); ++i)
		{
			FVector2D Pt;
			if (Globe->ProjectPin(PC, i, Pt) && FVector2D::DistSquared(Pt, Mouse) < Best)
			{
				Best = FVector2D::DistSquared(Pt, Mouse);
				Hover = i;
			}
		}
		if (Hover != INDEX_NONE && bMoved && !bGlobeDrag)
		{
			Menu->SetFocusedPin(Hover);
		}
		const bool bLeft = PC->WasInputKeyJustPressed(EKeys::LeftMouseButton);
		// (2026-10-21) "+ New Area" and the drawn areas' pins
		if (bLeft && NewAreaBtn.bIsValid && NewAreaBtn.IsInside(Mouse))
		{
			Menu->BeginNewArea();
			return;
		}
		if (const int32 Drawn = bLeft && Hover == INDEX_NONE ? DrawnPinAt(Menu, Mouse) : INDEX_NONE; Drawn != INDEX_NONE)
		{
			Menu->OpenAreaCard(Menu->GetAreaDraw().Coverage[Drawn].Key);
			return;
		}
		if (bLeft && Hover != INDEX_NONE)
		{
			Menu->PickPin(Hover);
			return;
		}
		if (bLeft || PC->WasInputKeyJustPressed(EKeys::RightMouseButton))
		{
			bGlobeDrag = true;
		}
		else if (!PC->IsInputKeyDown(EKeys::LeftMouseButton) && !PC->IsInputKeyDown(EKeys::RightMouseButton))
		{
			bGlobeDrag = false;
		}
		else if (bGlobeDrag && bMoved)
		{
			Globe->Orbit(Mouse - Prev);
		}
		if (PC->WasInputKeyJustPressed(EKeys::MouseScrollUp))
		{
			Globe->Zoom(+1.0f);
		}
		else if (PC->WasInputKeyJustPressed(EKeys::MouseScrollDown))
		{
			Globe->Zoom(-1.0f);
		}
		return;
	}
	// AreaMap (2026-10-14): a left drag on the map (off the dots and the panel)
	// pans the globe camera and the wheel there zooms it; AODMainMenu::
	// ClampAreaView keeps it on the area. Over the panel the wheel selects.
	float Mx = 0.0f, My = 0.0f;
	if (Globe && Menu->GetScreen() == EODMenuScreen::NewArea && !Menu->IsSettingsOpen()
		&& PC && PC->bShowMouseCursor && PC->GetMousePosition(Mx, My))
	{
		HandleNewAreaMouse(Menu, PC, FVector2D(Mx, My));
		return;
	}
	if (Globe && Menu->GetScreen() == EODMenuScreen::AreaMap && !Menu->IsSettingsOpen()
		&& PC && PC->bShowMouseCursor && PC->GetMousePosition(Mx, My))
	{
		const bool bOverMap = Mx < MenuX(true, ColW * MenuScale()) && !MenuHits.ContainsByPredicate(
			[&](const FODMenuHit& H) { return Mx >= H.X0 && Mx < H.X1 && My >= H.Y0 && My < H.Y1; });
		if (PC->WasInputKeyJustPressed(EKeys::LeftMouseButton))
		{
			bGlobeDrag = bOverMap;
		}
		else if (!PC->IsInputKeyDown(EKeys::LeftMouseButton))
		{
			bGlobeDrag = false;
		}
		else if (bGlobeDrag && GFrameCounter - LastMenuMouseFrame <= 1)
		{
			Globe->Orbit(FVector2D(Mx, My) - LastMenuMouse);
		}
		if (bOverMap && PC->WasInputKeyJustPressed(EKeys::MouseScrollUp))
		{
			Globe->Zoom(+1.0f);
		}
		else if (bOverMap && PC->WasInputKeyJustPressed(EKeys::MouseScrollDown))
		{
			Globe->Zoom(-1.0f);
		}
		DispatchMenuMouse(Menu, PC, MenuHits, LastMenuMouse, LastMenuMouseFrame, !bOverMap);
		return;
	}
	if (Menu && Menu->GetScreen() == EODMenuScreen::Paint && !Menu->IsSettingsOpen())
	{
		HandlePickerMouse();
	}
	else
	{
		PickerDrag = 0;
	}
	// garage room (2026-10-16): a drag off the menu (left on empty space, or
	// right anywhere) turns the car; bGlobeDrag doubles as the drag latch
	if (Menu && !Menu->IsVistaShown() && Menu->GetScreen() != EODMenuScreen::Start && !Menu->IsSettingsOpen()
		&& PC && PC->bShowMouseCursor && PC->GetMousePosition(Mx, My))
	{
		const bool bOverUi = PickerDrag != 0 || (PickerSV.bIsValid && Menu->GetScreen() == EODMenuScreen::Paint
			&& (PickerSV.IsInside(FVector2D(Mx, My)) || PickerHue.IsInside(FVector2D(Mx, My))))
			|| MenuHits.ContainsByPredicate([&](const FODMenuHit& H) { return Mx >= H.X0 && Mx < H.X1 && My >= H.Y0 && My < H.Y1; });
		if (PC->WasInputKeyJustPressed(EKeys::LeftMouseButton))
		{
			bGlobeDrag = !bOverUi;
		}
		else if (PC->WasInputKeyJustPressed(EKeys::RightMouseButton))
		{
			bGlobeDrag = true;
		}
		else if (!PC->IsInputKeyDown(EKeys::LeftMouseButton) && !PC->IsInputKeyDown(EKeys::RightMouseButton))
		{
			bGlobeDrag = false;
		}
		else if (bGlobeDrag && GFrameCounter - LastMenuMouseFrame <= 1)
		{
			Menu->SpinShowroomCar((Mx - LastMenuMouse.X) * 0.4f);
		}
	}
	else
	{
		bGlobeDrag = false;
	}
	DispatchMenuMouse(Menu, PC, MenuHits, LastMenuMouse, LastMenuMouseFrame);
}

void AODRaceHUD::HandlePickerMouse()
{
	APlayerController* PC = GetOwningPlayerController();
	AODVehiclePawn* Pawn = PC ? Cast<AODVehiclePawn>(PC->GetPawn()) : nullptr;
	float Mx = 0.0f, My = 0.0f;
	if (!Pawn || !PC->bShowMouseCursor || !PC->GetMousePosition(Mx, My))
	{
		PickerDrag = 0;
		return;
	}
	const FVector2D M(Mx, My);
	if (PC->WasInputKeyJustPressed(EKeys::LeftMouseButton))
	{
		PickerDrag = PickerSV.bIsValid && PickerSV.IsInside(M) ? 1 : PickerHue.bIsValid && PickerHue.IsInside(M) ? 2 : 0;
	}
	if (PickerDrag == 0 || !PickerSV.bIsValid)
	{
		return;
	}
	const bool bDown = PC->IsInputKeyDown(EKeys::LeftMouseButton);
	FODCarPaint Paint = Pawn->GetPaint();
	const FBox2D& Box = PickerDrag == 1 ? PickerSV : PickerHue;
	const FVector2D T = ((M - Box.Min) / Box.GetSize()).ClampAxes(0.0, 1.0);
	if (PickerDrag == 1)
	{
		Paint.S = T.X;
		Paint.V = 1.0f - T.Y;
	}
	else
	{
		Paint.H = FMath::Min(T.Y, 0.9999) * 360.0f;
	}
	Paint.bFactory = false;
	Pawn->SetPaint(Paint, !bDown);   // live while dragging, saved once on release
	if (!bDown)
	{
		PickerDrag = 0;
	}
}

void AODRaceHUD::DrawColorPicker(float X, float Y) const
{
	const AODVehiclePawn* Pawn = Cast<AODVehiclePawn>(GetOwningPawn());
	if (!Pawn)
	{
		return;
	}
	const FODCarPaint& Paint = Pawn->GetPaint();
	const FMenuPaint P{Canvas, MenuScale()};
	const float S = P.S, Sz = 280.0f * S, HW = 28.0f * S, HX = X + Sz + 14.0f * S;
	// sRGB HSV -> the canvas's linear colour (same conversion as FODCarPaint::BodySrgb)
	auto Col = [](float H, float Sa, float V) { return FLinearColor(FLinearColor(H, Sa, V).HSVToLinearRGB().ToFColor(false)); };

	P.Rect(X - 10.0f * S, Y - 10.0f * S, HX + HW - X + 20.0f * S, Sz + 20.0f * S, FLinearColor(0, 0, 0, 0.45f));
	TArray<FCanvasUVTri> Tris;
	auto Quad = [&](float X0, float Y0, float X1, float Y1, FLinearColor C00, FLinearColor C10, FLinearColor C01, FLinearColor C11)
	{
		FCanvasUVTri& A = Tris.AddDefaulted_GetRef();
		A.V0_Pos = FVector2D(X0, Y0); A.V0_Color = C00;
		A.V1_Pos = FVector2D(X1, Y0); A.V1_Color = C10;
		A.V2_Pos = FVector2D(X1, Y1); A.V2_Color = C11;
		FCanvasUVTri& B = Tris.AddDefaulted_GetRef();
		B.V0_Pos = FVector2D(X0, Y0); B.V0_Color = C00;
		B.V1_Pos = FVector2D(X1, Y1); B.V1_Color = C11;
		B.V2_Pos = FVector2D(X0, Y1); B.V2_Color = C01;
	};
	// the square as a 16x16 grid: per-vertex colours interpolate linearly, so
	// one quad would grey out its middle; 16 cells keep it close to true HSV
	constexpr int32 N = 16;
	for (int32 j = 0; j < N; ++j)
	{
		for (int32 i = 0; i < N; ++i)
		{
			const float S0 = float(i) / N, S1 = float(i + 1) / N, V0 = 1.0f - float(j) / N, V1 = 1.0f - float(j + 1) / N;
			Quad(X + S0 * Sz, Y + (1.0f - V0) * Sz, X + S1 * Sz, Y + (1.0f - V1) * Sz,
				Col(Paint.H, S0, V0), Col(Paint.H, S1, V0), Col(Paint.H, S0, V1), Col(Paint.H, S1, V1));
		}
	}
	constexpr int32 HN = 24;
	for (int32 j = 0; j < HN; ++j)
	{
		const FLinearColor C0 = Col(360.0f * j / HN, 1.0f, 1.0f), C1 = Col(360.0f * (j + 1) / HN, 1.0f, 1.0f);
		Quad(HX, Y + Sz * j / HN, HX + HW, Y + Sz * (j + 1) / HN, C0, C0, C1, C1);
	}
	FCanvasTriangleItem T(Tris, GWhiteTexture);
	Canvas->DrawItem(T);

	// markers: ringed square on the square, a bar across the strip (dimmed while Factory)
	const float A = Paint.bFactory ? 0.4f : 1.0f;
	auto Ring = [&](float CX, float CY, float W, float H)
	{
		for (const auto& [Pad, RingCol] : {TPair<float, FLinearColor>(3.0f * S, FLinearColor(0, 0, 0, A)), TPair<float, FLinearColor>(1.5f * S, FLinearColor(1, 1, 1, A))})
		{
			const float L = CX - W * 0.5f - Pad, R = CX + W * 0.5f + Pad, U = CY - H * 0.5f - Pad, D = CY + H * 0.5f + Pad;
			const float B = 1.5f * S;
			P.Rect(L, U, R - L, B, RingCol); P.Rect(L, D - B, R - L, B, RingCol);
			P.Rect(L, U, B, D - U, RingCol); P.Rect(R - B, U, B, D - U, RingCol);
		}
	};
	Ring(X + Paint.S * Sz, Y + (1.0f - Paint.V) * Sz, 8.0f * S, 8.0f * S);
	Ring(HX + HW * 0.5f, Y + Paint.H / 360.0f * Sz, HW, 2.0f * S);
	P.Text(TEXT("Click or drag to pick a colour"), X, Y + Sz + 26.0f * S, 16.0f, EMenuWeight::Medium, DimCol);

	PickerSV = FBox2D(FVector2D(X, Y), FVector2D(X + Sz, Y + Sz));
	PickerHue = FBox2D(FVector2D(HX, Y), FVector2D(HX + HW, Y + Sz));
}

void AODRaceHUD::TogglePhotoMode()
{
	for (TActorIterator<AODPauseMenu> It(GetWorld()); It; ++It)
	{
		It->TogglePhotoMode();
		return;
	}
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

float AODRaceHUD::DrawMenuHeader(const FString& Title, TArrayView<const FODMenuTab> Tabs, int32 ActiveTab, float X,
	const FString& Sub) const
{
	const FMenuPaint P{Canvas, MenuScale()};
	const float S = P.S;
	const float TitleY = 150.0f * S;
	P.Text(Title.ToUpper(), X, TitleY, 44.0f, EMenuWeight::Bold, FLinearColor::White, false, 60);
	P.Rect(X, TitleY + 30.0f * S, 56.0f * S, 4.0f * S, Accent);
	const float SubH = Sub.IsEmpty() ? 0.0f : 40.0f * S;   // (2026-10-14) area blurb line
	if (!Sub.IsEmpty())
	{
		P.Text(Sub, X, TitleY + 62.0f * S, 20.0f, EMenuWeight::Medium, DimCol);
	}
	if (Tabs.Num() <= 1)
	{
		return 215.0f * S + SubH;
	}

	// tab strip: Q [TAB] [TAB] ... E, each tab clickable
	const float TabY = 226.0f * S + SubH;
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
	return 275.0f * S + SubH;
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

float AODRaceHUD::DrawMenuPanel(const FString& Title, TArrayView<const FODMenuTab> Tabs, int32 ActiveTab,
	int32 Footer, int32 Selected, TFunctionRef<void(int32, FString&, FString&)> GetRow, bool bRight,
	const FString& Sub) const
{
	const FMenuPaint P{Canvas, MenuScale()};
	const float S = P.S;
	const float W = ColW * S;
	const float X = MenuX(bRight, W);
	float Y = DrawMenuHeader(Title, Tabs, ActiveTab, X, Sub);

	const float H = RowH * S;
	const float VX0 = X + W - 300.0f * S, VX1 = X + W - 16.0f * S;   // value column
	const FODMenuTab& Tab = Tabs[FMath::Clamp(ActiveTab, 0, Tabs.Num() - 1)];
	// scrolling (2026-10-14): as many rows as fit above the footer + key hints;
	// MenuScrollTop moves only as far as needed to keep Selected visible, so a
	// hover-select never scrolls the row out from under the mouse. Hits are
	// added for the drawn rows only, with their global row ids.
	const int32 MaxRows = FMath::Max(3, FMath::FloorToInt((Canvas->SizeY - Y - (22.0f + FooterH + 110.0f) * S) / H));
	const int32 Shown = FMath::Min(Tab.Count, MaxRows);
	MenuScrollTop = ScrollTop(MenuScrollTop, Selected - Tab.First, Tab.Count, Shown);
	if (MenuScrollTop > 0)
	{
		P.Chevron(X + W * 0.5f, Y - 8.0f * S, -1.0f, DimCol);
	}
	if (MenuScrollTop + Shown < Tab.Count)
	{
		P.Chevron(X + W * 0.5f, Y + Shown * H + 8.0f * S, +1.0f, DimCol);
	}
	for (int32 Row = Tab.First + MenuScrollTop; Row < Tab.First + MenuScrollTop + Shown; ++Row, Y += H)
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
		else if (Value.Len() > 8 && Value[0] == TEXT('#') && Value[7] == TEXT(' '))
		{
			// "#RRGGBB Name" (garage Paint screen): a colour chip, then the name
			const FLinearColor Chip = FLinearColor(FColor::FromHex(Value.Mid(1, 6)));
			P.Rect(IX0, CY - 9.0f * S, 18.0f * S, 18.0f * S, bSel ? FLinearColor::White : TextCol);
			P.Rect(IX0 + 2.0f * S, CY - 7.0f * S, 14.0f * S, 14.0f * S, Chip);
			P.Text(Value.Mid(8), (IX0 + 18.0f * S + IX1) * 0.5f, CY, 22.0f, EMenuWeight::SemiBold, ValCol, true);
		}
		else
		{
			P.Text(Value, (IX0 + IX1) * 0.5f, CY, 22.0f, EMenuWeight::SemiBold, ValCol, true);
		}
	}

	FString FootLabel, FootValue;
	GetRow(Footer, FootLabel, FootValue);
	DrawMenuFooter(FootLabel, Footer, Selected == Footer, X, Y + 22.0f * S, W);
	return Y + (22.0f + FooterH) * S;
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
	const int32 NumCols = AODRebindMenu::NumCols;
	const float KX0 = X + W * 0.32f;
	const float CellW = (X + W - KX0) / NumCols;
	for (int32 Col = 0; Col < NumCols; ++Col)
	{
		P.Text(AODRebindMenu::ColumnHeader(Col), KX0 + CellW * (Col + 0.5f), Y + 10.0f * S, 15.0f,
			EMenuWeight::SemiBold, DimCol, true, 120);
	}
	Y += 34.0f * S;

	// scrolling (2026-10-14): the binding rows + Reset scroll as one list
	// above the fixed Back footer, following the selection like DrawMenuPanel
	// (arrows / d-pad move it; the wheel arrives as Up/Down via DispatchMenuMouse)
	const int32 Count = Menu->ResetRow() + 1;
	const int32 Shown = FMath::Min(Count,
		FMath::Max(3, FMath::FloorToInt((Canvas->SizeY - Y - (22.0f + FooterH + 110.0f) * S) / H)));
	RebindScrollTop = ScrollTop(RebindScrollTop, Menu->GetSelected(), Count, Shown);
	if (RebindScrollTop > 0)
	{
		P.Chevron(X + W * 0.5f, Y - 8.0f * S, -1.0f, DimCol);
	}
	if (RebindScrollTop + Shown < Count)
	{
		P.Chevron(X + W * 0.5f, Y + Shown * H + 8.0f * S, +1.0f, DimCol);
	}
	const int32 Reset = Menu->ResetRow();
	const int32 End = RebindScrollTop + Shown;

	// capture pulse on real time - the game is paused, world time stands still
	const float Pulse = 0.55f + 0.35f * FMath::Sin(FPlatformTime::Seconds() * 6.0);
	for (int32 Row = RebindScrollTop; Row < FMath::Min(End, Reset); ++Row, Y += H)
	{
		const bool bRowSel = Row == Menu->GetSelected();
		const float CY = Y + H * 0.5f;
		if (bRowSel)
		{
			P.Rect(X, Y, 4.0f * S, H, Accent);
		}
		P.Text(Menu->GetLabel(Row), X + 22.0f * S, CY, 21.0f, bRowSel ? EMenuWeight::SemiBold : EMenuWeight::Medium,
			bRowSel ? FLinearColor::White : TextCol);
		for (int32 Col = 0; Col < NumCols; ++Col)
		{
			if (!Menu->HasCell(Row, Col))
			{
				continue;
			}
			const float CX = KX0 + CellW * Col;
			const bool bCap = Menu->IsCapturingCell(Row, Col);
			const bool bCellSel = bRowSel && Col == Menu->GetColumn();
			P.Rect(CX + 3.0f * S, Y + 3.0f * S, CellW - 6.0f * S, H - 6.0f * S,
				bCap ? FLinearColor(Accent.R, Accent.G, Accent.B, Pulse * 0.5f)
				: bCellSel ? FLinearColor(Accent.R, Accent.G, Accent.B, 0.25f) : FLinearColor(1, 1, 1, 0.05f));
			FString Bind = Menu->GetBinding(Row, Col);
			Bind.RemoveFromStart(TEXT("Gamepad "));
			P.Text(Bind, CX + CellW * 0.5f, CY, 17.0f, EMenuWeight::SemiBold,
				bCap || bCellSel ? FLinearColor::White : TextCol, true);
			AddMenuHit(FODMenuHit::Cell, Row, Col, CX, Y, CellW, H);
		}
	}

	// Reset row (the list's last, when scrolled into view), then the Back footer
	if (End > Reset)
	{
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
	}
	DrawMenuFooter(Menu->GetLabel(Menu->BackRow()), Menu->BackRow(), Menu->GetSelected() == Menu->BackRow(), X, Y + 22.0f * S, W);

	if (Menu->IsCapturing())
	{
		DrawHints(P, X, {{TEXT("Any key / wheel"), TEXT("Assign")}, {TEXT("Esc"), TEXT("Cancel")}});
	}
	else
	{
		DrawHints(P, X, {{TEXT("Arrows / Wheel"), TEXT("Select")}, {TEXT("Enter"), TEXT("Rebind")}, {TEXT("Esc"), TEXT("Back")}});
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

// AreaMap screen (2026-10-14, was the Area/Conditions slide-in left panel):
// full-screen top-down over the globe camera, which shows the area's relief
// draped on the globe. Roads and a dot per pick (starts / track starts) go
// through AODMainMenu::AreaCmToScreen (cm -> lat/lng -> globe). Each dot is a
// row hit, so hover selects that row and click confirms it (DispatchMenuMouse);
// drawn before the rows panel so the panel stays on top. The selected track's
// line, length and best time too.
void AODRaceHUD::DrawAreaMap(const AODMainMenu* Menu) const
{
	const FMenuPaint P{Canvas, MenuScale()};
	const float S = P.S;
	const APlayerController* PC = GetOwningPlayerController();
	if (!PC)
	{
		return;
	}
	DrawCoverage(Menu, true);   // (2026-10-21) baked / pending / failed tiles under the roads
	auto Line = [&](const TArray<FVector2D>& L, bool bLoop, const FLinearColor& Col, float Thick)
	{
		if (L.Num() < 2)
		{
			return;
		}
		FVector2D A, B = FVector2D::ZeroVector;
		bool bA = false;
		for (int32 i = 0; i < L.Num() + (bLoop ? 1 : 0); ++i)
		{
			const FVector2D& Cm = L[i % L.Num()];
			const bool bB = !FMath::IsNaN(Cm.X) && Menu->AreaCmToScreen(PC, Cm, B);   // NaN = polyline break
			if (bA && bB)
			{
				FCanvasLineItem Seg(A, B);
				Seg.SetColor(Col);
				Seg.LineThickness = Thick;
				Canvas->DrawItem(Seg);
			}
			A = B;
			bA = bB;
		}
	};
	Line(Menu->GetAreaMap(), false, FLinearColor(1, 1, 1, 0.55f), 1.5f * S);

	const TArray<FODMenuPick>& Picks = Menu->GetPicks();
	const int32 Sel = Menu->GetSelectedPick();
	const TArrayView<const FODMenuTab> Tabs = Menu->GetTabs();
	const int32 First = Tabs.IsValidIndex(Menu->GetTab()) ? Tabs[Menu->GetTab()].First : 0;   // row of Picks[0]
	if (Picks.IsValidIndex(Sel) && Picks[Sel].Line.Num() > 1)
	{
		Line(Picks[Sel].Line, Picks[Sel].bLoop, Accent, 4.0f * S);
	}
	auto Disc = [&](const FVector2D& C, float R, const FLinearColor& Col)
	{
		FCanvasNGonItem D(C, FVector2D(R, R), 16, GWhiteTexture, Col);
		D.BlendMode = SE_BLEND_Translucent;
		Canvas->DrawItem(D);
	};
	for (int32 i = 0; i < Picks.Num(); ++i)
	{
		FVector2D D;
		if (!Picks[i].bHasPos || !Menu->AreaCmToScreen(PC, Picks[i].Pos, D))
		{
			continue;
		}
		AddMenuHit(FODMenuHit::Row, First + i, -1, D.X - 14.0f * S, D.Y - 14.0f * S, 28.0f * S, 28.0f * S);
		if (i != Sel)
		{
			Disc(D, 6.0f * S, FLinearColor(0, 0, 0, 0.5f));
			Disc(D, 4.0f * S, TextCol);
		}
	}
	FVector2D D;
	if (Picks.IsValidIndex(Sel) && Picks[Sel].bHasPos && Menu->AreaCmToScreen(PC, Picks[Sel].Pos, D))   // selected last, on top
	{
		const FODMenuPick& K = Picks[Sel];
		Disc(D, 10.0f * S, FLinearColor(0, 0, 0, 0.5f));
		Disc(D, 8.0f * S, Accent);
		const bool bTrack = !K.Track.IsEmpty();
		P.Text(K.Label, D.X + 16.0f * S, D.Y - (bTrack ? 9.0f : 0.0f) * S, 20.0f, EMenuWeight::Bold, FLinearColor::White);
		if (bTrack)
		{
			P.Text(FString::Printf(TEXT("%.1f km %s  ·  %s"), K.Km, K.bLoop ? TEXT("loop") : TEXT("point to point"),
					K.BestTime > 0.0f ? *(TEXT("Best ") + FormatTime(K.BestTime)) : TEXT("No time set")),
				D.X + 16.0f * S, D.Y + 12.0f * S, 15.0f, EMenuWeight::Medium, TextCol);
		}
	}
}

// World screen (2026-10-14): the globe is 3D (AODMenuGlobe, the view target);
// this is the old globe.gl page's overlay in its layout (vh -> 1080p units):
// title, a small gold dot per area cluster at ProjectPin(); only the focused
// (hovered / arrowed) one gets a white dot and its name + sub label
// (2026-10-14 play-test: labels on every pin were too big). Mouse: HandleMenuMouse.
void AODRaceHUD::DrawGlobePins(const AODMainMenu* Menu) const
{
	const FMenuPaint P{Canvas, MenuScale()};
	const float S = P.S;
	const float X = Canvas->SizeX * 0.05f, TitleY = Canvas->SizeY * 0.13f + 24.0f * S;
	P.Text(Menu->GetScreenTitle().ToUpper(), X, TitleY, 44.0f, EMenuWeight::Bold, FLinearColor::White, false, 60);
	P.Rect(X, TitleY + 30.0f * S, 56.0f * S, 4.0f * S, Accent);

	// (2026-10-21) "+ New Area" under the title
	const FString NewLabel = TEXT("+  NEW AREA");
	const float BW = P.Width(NewLabel, 18.0f, EMenuWeight::Bold, 40) + 36.0f * S, BY = TitleY + 52.0f * S;
	NewAreaBtn = FBox2D(FVector2D(X, BY), FVector2D(X + BW, BY + 40.0f * S));
	float Mx = 0.0f, My = 0.0f;
	const bool bMouse = GetOwningPlayerController() && GetOwningPlayerController()->GetMousePosition(Mx, My);
	const bool bBtnHot = bMouse && NewAreaBtn.IsInside(FVector2D(Mx, My));
	P.Rect(X, BY, BW, 40.0f * S, bBtnHot ? Accent : FLinearColor(1, 1, 1, 0.12f));
	P.Text(NewLabel, X + 18.0f * S, BY + 20.0f * S, 18.0f, EMenuWeight::Bold, bBtnHot ? Ink : TextCol, false, 40);

	const AODMenuGlobe* Globe = Menu->GetGlobe();
	const APlayerController* PC = GetOwningPlayerController();
	if (Globe && PC)
	{
		auto Disc = [&](const FVector2D& C, float R, const FLinearColor& Col)
		{
			FCanvasNGonItem D(C, FVector2D(R, R), 24, GWhiteTexture, Col);
			D.BlendMode = SE_BLEND_Translucent;
			Canvas->DrawItem(D);
		};
		// (2026-10-21) every area's outline; drawn areas get a white pin (click: its card)
		DrawCoverage(Menu, false);
		const TArray<FODAreaDraw::FCovArea>& Cov = Menu->GetAreaDraw().Coverage;
		const int32 HotDrawn = bMouse ? DrawnPinAt(Menu, FVector2D(Mx, My)) : INDEX_NONE;
		for (int32 i = 0; i < Cov.Num(); ++i)
		{
			FVector2D Pt;
			if (!Cov[i].bDrawn || !Globe->LatLngToScreen(PC, Cov[i].Centre.Y, Cov[i].Centre.X, Pt))
			{
				continue;
			}
			Disc(Pt, 7.0f * S, FLinearColor(0, 0, 0, 0.5f));
			Disc(Pt, (i == HotDrawn ? 5.5f : 4.0f) * S, FLinearColor::White);
			if (i == HotDrawn)
			{
				const FString Name = Cov[i].Name.IsEmpty() ? Cov[i].Key : Cov[i].Name;
				P.Text(Name, Pt.X + 12.0f * S, Pt.Y - 7.0f * S, 16.0f, EMenuWeight::Bold, FLinearColor::White, false, 40);
				P.Text(FString::Printf(TEXT("%.0f of %.0f km² baked"), Cov[i].BakedKm2, Cov[i].TotalKm2),
					Pt.X + 12.0f * S, Pt.Y + 9.0f * S, 12.0f, EMenuWeight::SemiBold, DimCol);
			}
		}
		const TArray<FODGlobePin>& Pins = Globe->GetPins();
		const int32 Focus = Menu->GetFocusedPin();
		auto DrawPin = [&](int32 i)
		{
			FVector2D Pt;
			if (!Globe->ProjectPin(PC, i, Pt))
			{
				return;
			}
			const bool bOn = i == Focus;
			// picked (2026-10-22): an accent dot with a ring pulsing out of it and
			// "Loading" in place of the sub line until the fly-in lands on the area
			const bool bPicked = i == Menu->GetPickingPin();
			const float R = (bPicked ? 7.0f : bOn ? 5.5f : 3.5f) * S;
			if (bPicked)
			{
				const float Pulse = float(FMath::Frac(FPlatformTime::Seconds() * 1.5));
				Disc(Pt, R + (3.0f + 14.0f * Pulse) * S, FLinearColor(Accent.R, Accent.G, Accent.B, 0.45f * (1.0f - Pulse)));
			}
			Disc(Pt, R + 2.5f * S, FLinearColor(Accent.R, Accent.G, Accent.B, bOn ? 0.3f : 0.18f));
			Disc(Pt, R, bPicked ? Accent : bOn ? FLinearColor::White : Accent);
			if (!bOn)
			{
				return;
			}
			const float LX = Pt.X + (bPicked ? 15.0f : 12.0f) * S;
			const FString Sub = bPicked ? TEXT("Loading…") : Pins[i].Sub;
			const bool bSub = !Sub.IsEmpty();
			P.Text(Pins[i].Name, LX, Pt.Y - (bSub ? 7.0f : 0.0f) * S, 16.0f, EMenuWeight::Bold, Accent, false, 40);
			if (bSub)
			{
				P.Text(Sub, LX, Pt.Y + 9.0f * S, 12.0f, EMenuWeight::SemiBold, bPicked ? FLinearColor::White : DimCol);
			}
		};
		for (int32 i = 0; i < Pins.Num(); ++i)
		{
			if (i != Focus)
			{
				DrawPin(i);
			}
		}
		if (Pins.IsValidIndex(Focus))   // focused last, on top
		{
			DrawPin(Focus);
		}
	}
	DrawHints(P, X, {{TEXT("Click"), TEXT("Pick")}, {TEXT("Drag"), TEXT("Spin")}, {TEXT("Wheel"), TEXT("Zoom")},
		{TEXT("Arrows"), TEXT("Browse")}, {TEXT("N"), TEXT("New Area")}, {TEXT("Esc"), TEXT("Back")}});
}

// ---- area picker (2026-10-21, docs/area-picker-contract.md) -----------------

namespace
{
// lon/lat -> screen through the globe; false for a NaN break, behind the globe or off screen
struct FGlobeLL
{
	const AODMenuGlobe* G;
	const APlayerController* PC;
	bool operator()(const FVector2D& LL, FVector2D& Out) const
	{
		return !FMath::IsNaN(LL.X) && G->LatLngToScreen(PC, LL.Y, LL.X, Out);
	}
};

void Seg(UCanvas* C, const FVector2D& A, const FVector2D& B, const FLinearColor& Col, float Thick)
{
	FCanvasLineItem L(A, B);
	L.SetColor(Col);
	L.LineThickness = Thick;
	C->DrawItem(L);
}

// a lon/lat polyline (NaN = break); dashed when Dash > 0 (px)
void PolyLL(UCanvas* C, const FGlobeLL& ToS, const TArray<FVector2D>& L, bool bLoop, const FLinearColor& Col, float Thick, float Dash = 0.0f)
{
	FVector2D A = FVector2D::ZeroVector, B = FVector2D::ZeroVector;
	bool bA = false;
	for (int32 i = 0; L.Num() > 1 && i < L.Num() + (bLoop && L.Num() > 2 ? 1 : 0); ++i)
	{
		const bool bB = ToS(L[i % L.Num()], B);
		if (bA && bB && Dash <= 0.0f)
		{
			Seg(C, A, B, Col, Thick);
		}
		else if (bA && bB)
		{
			const float Len = FVector2D::Distance(A, B);
			for (float T = 0.0f; T < Len; T += 2.0f * Dash)
			{
				Seg(C, A + (B - A) * (T / Len), A + (B - A) * (FMath::Min(T + Dash, Len) / Len), Col, Thick);
			}
		}
		A = B;
		bA = bB;
	}
}

// a filled screen quad (corners in order)
void Quad(UCanvas* C, const FVector2D (&Q)[4], const FLinearColor& Col)
{
	TArray<FCanvasUVTri> Tris;
	Tris.SetNum(2);
	Tris[0].V0_Pos = Q[0]; Tris[0].V1_Pos = Q[1]; Tris[0].V2_Pos = Q[2];
	Tris[1].V0_Pos = Q[0]; Tris[1].V1_Pos = Q[2]; Tris[1].V2_Pos = Q[3];
	for (FCanvasUVTri& T : Tris)
	{
		T.V0_Color = T.V1_Color = T.V2_Color = Col;
	}
	FCanvasTriangleItem T(Tris, GWhiteTexture);
	T.BlendMode = SE_BLEND_Translucent;
	C->DrawItem(T);
}

void Dot(UCanvas* C, const FVector2D& P, float R, const FLinearColor& Col)
{
	FCanvasNGonItem D(P, FVector2D(R, R), 16, GWhiteTexture, Col);
	D.BlendMode = SE_BLEND_Translucent;
	C->DrawItem(D);
}

FLinearColor Alpha(const FLinearColor& C, float A)
{
	return FLinearColor(C.R, C.G, C.B, A);
}

// the corridor's ~1 km band around segment A-B, in lon/lat (local km plane at A)
void CorridorOffsets(const FVector2D& A, const FVector2D& B, FVector2D& OutOff)
{
	const double KmLon = 111.320 * FMath::Cos(FMath::DegreesToRadians(A.Y)), KmLat = 110.574;
	const FVector2D Dir = FVector2D((B.X - A.X) * KmLon, (B.Y - A.Y) * KmLat).GetSafeNormal();
	OutOff = FVector2D(-Dir.Y * ODAreaGeo::CorridorHalfKm / KmLon, Dir.X * ODAreaGeo::CorridorHalfKm / KmLat);
}
}

void AODRaceHUD::DrawCoverage(const AODMainMenu* Menu, bool bTiles) const
{
	const AODMenuGlobe* Globe = Menu->GetGlobe();
	const APlayerController* PC = GetOwningPlayerController();
	if (!Globe || !PC)
	{
		return;
	}
	const float S = MenuScale();
	const FGlobeLL ToS{Globe, PC};
	for (const FODAreaDraw::FCovArea& A : Menu->GetAreaDraw().Coverage)
	{
		for (int32 i = 0; bTiles && i < A.Tiles.Num(); ++i)
		{
			FVector2D Q[4];
			if (!ToS(A.Tiles[i][0], Q[0]) || !ToS(A.Tiles[i][1], Q[1]) || !ToS(A.Tiles[i][2], Q[2]) || !ToS(A.Tiles[i][3], Q[3])
				|| FVector2D::DistSquared(Q[0], Q[2]) < 4.0f)   // sub-pixel from far out
			{
				continue;
			}
			const uint8 St = A.TileState[i];
			Quad(Canvas, Q, St == 0 ? Alpha(Accent, 0.25f) : St == 2 ? Alpha(Danger, 0.45f) : FLinearColor(1, 1, 1, 0.05f));
			if (St == 1)
			{
				Seg(Canvas, Q[0], Q[2], FLinearColor(1, 1, 1, 0.3f), 1.0f);   // pending: hatched (the diagonals line up tile to tile)
			}
		}
		PolyLL(Canvas, ToS, A.Outline, true, A.bDrawn ? Alpha(Accent, 0.9f) : FLinearColor(1, 1, 1, 0.45f), 1.5f * S);
	}
}

int32 AODRaceHUD::DrawnPinAt(const AODMainMenu* Menu, const FVector2D& Mouse) const
{
	const AODMenuGlobe* Globe = Menu->GetGlobe();
	const APlayerController* PC = GetOwningPlayerController();
	const TArray<FODAreaDraw::FCovArea>& Cov = Menu->GetAreaDraw().Coverage;
	int32 Best = INDEX_NONE;
	float BestD2 = FMath::Square(14.0f * MenuScale());
	for (int32 i = 0; Globe && PC && i < Cov.Num(); ++i)
	{
		FVector2D Pt;
		if (Cov[i].bDrawn && Globe->LatLngToScreen(PC, Cov[i].Centre.Y, Cov[i].Centre.X, Pt) && FVector2D::DistSquared(Pt, Mouse) < BestD2)
		{
			BestD2 = FVector2D::DistSquared(Pt, Mouse);
			Best = i;
		}
	}
	return Best;
}

// New Area: the draft (polygon or corridor band, edges, midpoint handles,
// vertices, start) or a drawn area's card (pass-1 roads, holes dashed, towns,
// the snapped start), over the coverage tiles. Readout + hints: DrawMainMenu.
void AODRaceHUD::DrawNewArea(AODMainMenu* Menu) const
{
	const FODAreaDraw& D = Menu->GetAreaDraw();
	const AODMenuGlobe* Globe = Menu->GetGlobe();
	const APlayerController* PC = GetOwningPlayerController();
	if (!Globe || !PC)
	{
		return;
	}
	const FMenuPaint P{Canvas, MenuScale()};
	const float S = P.S;
	const FGlobeLL ToS{Globe, PC};
	DrawCoverage(Menu, true);
	FVector2D Pt;
	auto StartPin = [&](const FVector2D& LL, const FString& Label)
	{
		if (ToS(LL, Pt))
		{
			Dot(Canvas, Pt, 10.0f * S, FLinearColor(0, 0, 0, 0.5f));
			Dot(Canvas, Pt, 8.0f * S, Accent);
			P.Text(Label, Pt.X + 16.0f * S, Pt.Y, 18.0f, EMenuWeight::Bold, FLinearColor::White, false, 40);
		}
	};
	if (!D.CanEdit())
	{
		PolyLL(Canvas, ToS, D.RoadsLL, false, FLinearColor(1, 1, 1, 0.6f), 1.5f * S);
		for (const TArray<FVector2D>& H : D.Holes)
		{
			PolyLL(Canvas, ToS, H, true, Danger, 1.5f * S, 5.0f * S);
		}
		if (const TArray<FVector2D>* O = D.JobOutline())
		{
			PolyLL(Canvas, ToS, *O, true, Accent, 2.5f * S);
		}
		for (int32 i = 0; i < D.TownLL.Num(); ++i)
		{
			if (ToS(D.TownLL[i], Pt))
			{
				Dot(Canvas, Pt, 3.5f * S, TextCol);
				P.Text(D.TownNames[i], Pt.X + 8.0f * S, Pt.Y, 13.0f, EMenuWeight::SemiBold, DimCol);
			}
		}
		if (D.Status.bSnapped)
		{
			StartPin(D.Status.SnappedLL, TEXT("START"));
		}
		return;
	}
	const TArray<FVector2D>& V = D.Verts;
	const FLinearColor Edge = ODAreaGeo::Check(V, D.bCorridor).IsEmpty() ? Accent : Danger;
	if (D.bCorridor && V.Num() >= 2)
	{
		// the ~1 km band the request bakes: a quad per segment + a disc per point
		for (int32 i = 1; i < V.Num(); ++i)
		{
			FVector2D Off;
			CorridorOffsets(V[i - 1], V[i], Off);
			FVector2D Q[4];
			if (ToS(V[i - 1] + Off, Q[0]) && ToS(V[i] + Off, Q[1]) && ToS(V[i] - Off, Q[2]) && ToS(V[i - 1] - Off, Q[3]))
			{
				Quad(Canvas, Q, Alpha(Edge, 0.18f));
			}
		}
		for (const FVector2D& LL : V)
		{
			FVector2D Up;
			if (ToS(LL, Pt) && ToS(LL + FVector2D(0.0, ODAreaGeo::CorridorHalfKm / 110.574), Up))
			{
				Dot(Canvas, Pt, FVector2D::Distance(Pt, Up), Alpha(Edge, 0.12f));
			}
		}
	}
	PolyLL(Canvas, ToS, V, D.bClosed && !D.bCorridor, Edge, 2.5f * S);
	if (!D.bClosed && !D.bCorridor && V.Num() >= 3)
	{
		PolyLL(Canvas, ToS, {V.Last(), V[0]}, false, Alpha(Edge, 0.6f), 1.5f * S, 6.0f * S);   // where it will close
	}
	// midpoint handles: drag one to add a point there
	const int32 NEdges = V.Num() < 2 ? 0 : V.Num() - (D.bClosed && !D.bCorridor ? 0 : 1);
	for (int32 i = 0; i < NEdges; ++i)
	{
		if (ToS((V[i] + V[(i + 1) % V.Num()]) * 0.5, Pt))
		{
			Dot(Canvas, Pt, 4.0f * S, FLinearColor(0, 0, 0, 0.45f));
			Dot(Canvas, Pt, 2.5f * S, FLinearColor(1, 1, 1, 0.7f));
		}
	}
	for (int32 i = 0; i < V.Num(); ++i)
	{
		if (ToS(V[i], Pt))
		{
			const bool bCloser = i == 0 && !D.bClosed && !D.bCorridor && V.Num() >= 3;   // click it to close
			Dot(Canvas, Pt, 7.0f * S, FLinearColor(0, 0, 0, 0.55f));
			Dot(Canvas, Pt, 5.0f * S, bCloser ? Accent : FLinearColor::White);
		}
	}
	if (D.bHasStart)
	{
		StartPin(D.Start, TEXT("START"));
	}
}

void AODRaceHUD::HandleNewAreaMouse(AODMainMenu* Menu, APlayerController* PC, const FVector2D& Mouse)
{
	FODAreaDraw& D = Menu->GetAreaDraw();
	AODMenuGlobe* Globe = Menu->GetGlobe();
	const float S = MenuScale();
	const FGlobeLL ToS{Globe, PC};
	const FVector2D Prev = LastMenuMouse;
	const bool bFresh = GFrameCounter - LastMenuMouseFrame <= 1;
	const bool bOverMap = Mouse.X < MenuX(true, ColW * S) && !MenuHits.ContainsByPredicate(
		[&](const FODMenuHit& H) { return Mouse.X >= H.X0 && Mouse.X < H.X1 && Mouse.Y >= H.Y0 && Mouse.Y < H.Y1; });
	const double Now = FPlatformTime::Seconds();
	TArray<FVector2D>& V = D.Verts;

	// what's under the cursor: a vertex, else an edge's midpoint handle
	int32 OnVert = INDEX_NONE, OnMid = INDEX_NONE;
	const float Grab = FMath::Square(12.0f * S);
	FVector2D Pt;
	for (int32 i = 0; D.CanEdit() && bOverMap && i < V.Num() && OnVert == INDEX_NONE; ++i)
	{
		OnVert = ToS(V[i], Pt) && FVector2D::DistSquared(Pt, Mouse) < Grab ? i : INDEX_NONE;
	}
	const int32 NEdges = V.Num() < 2 ? 0 : V.Num() - (D.bClosed && !D.bCorridor ? 0 : 1);
	for (int32 i = 0; D.CanEdit() && bOverMap && OnVert == INDEX_NONE && i < NEdges && OnMid == INDEX_NONE; ++i)
	{
		OnMid = ToS((V[i] + V[(i + 1) % V.Num()]) * 0.5, Pt) && FVector2D::DistSquared(Pt, Mouse) < Grab ? i : INDEX_NONE;
	}

	if (PC->WasInputKeyJustPressed(EKeys::LeftMouseButton) && bOverMap)
	{
		bAreaPress = true;
		bAreaMoved = false;
		AreaPressAt = Mouse;
		AreaDragVert = OnVert;
		AreaDragMid = OnMid;
	}
	else if (bAreaPress && PC->IsInputKeyDown(EKeys::LeftMouseButton))
	{
		if (!bAreaMoved && FVector2D::Distance(Mouse, AreaPressAt) > 4.0f * S)
		{
			bAreaMoved = true;
			if (AreaDragVert != INDEX_NONE || AreaDragMid != INDEX_NONE)
			{
				D.Push();   // one undo step per drag
			}
			if (AreaDragMid != INDEX_NONE && V.Num() < ODAreaGeo::MaxVerts)
			{
				AreaDragVert = AreaDragMid + 1;
				V.Insert((V[AreaDragMid] + V[(AreaDragMid + 1) % V.Num()]) * 0.5, AreaDragVert);
			}
			AreaDragMid = INDEX_NONE;
		}
		double Lat = 0.0, Lng = 0.0;
		if (bAreaMoved && V.IsValidIndex(AreaDragVert))
		{
			if (Globe->ScreenToLatLng(PC, Mouse, Lat, Lng))
			{
				V[AreaDragVert] = FVector2D(Lng, Lat);
			}
		}
		else if (bAreaMoved && bFresh)
		{
			Globe->Orbit(Mouse - Prev);   // empty space: spin, as on World
		}
	}
	else if (bAreaPress)
	{
		// released
		bAreaPress = false;
		double Lat = 0.0, Lng = 0.0;
		if (bAreaMoved && V.IsValidIndex(AreaDragVert))
		{
			D.Save();
		}
		else if (!bAreaMoved && V.IsValidIndex(AreaDragVert))
		{
			D.ClickVertex(AreaDragVert, Mouse, Now);
		}
		else if (!bAreaMoved && Globe->ScreenToLatLng(PC, Mouse, Lat, Lng))
		{
			D.Click(FVector2D(Lng, Lat), Mouse, Now);
		}
		AreaDragVert = AreaDragMid = INDEX_NONE;
	}
	if (bOverMap && OnVert != INDEX_NONE && PC->WasInputKeyJustPressed(EKeys::RightMouseButton))
	{
		D.DeleteVertex(OnVert);
	}
	if (bOverMap && PC->WasInputKeyJustPressed(EKeys::MouseScrollUp))
	{
		Globe->Zoom(+1.0f);
	}
	else if (bOverMap && PC->WasInputKeyJustPressed(EKeys::MouseScrollDown))
	{
		Globe->Zoom(-1.0f);
	}
	// rows: as everywhere (right click backs out only over the panel)
	DispatchMenuMouse(Menu, PC, MenuHits, LastMenuMouse, LastMenuMouseFrame, !bOverMap, !bOverMap);
}

// Loading screen (2026-10-14, "hold back until everything is up, no half-
// loaded screens"): AODMainMenu::Tick gates the release on ground + terrain +
// roads + buildings + an idle renderer; this keeps the screen itself calm -
// opaque at once (the teleport stays hidden), its text eases in rather than
// popping, the bar only ever eases forward (tiles 0-80 %, buildings 85,
// renderer 95), and after release DrawHUD calls it again with Veil < 1 so the
// whole thing fades off the finished world instead of cutting. Real time
// throughout - world time may be paused or hitch while streaming.
void AODRaceHUD::DrawLoadingScreen(const AODMainMenu* Menu, float Veil) const
{
	const FMenuPaint P{Canvas, MenuScale()};
	const float S = P.S;
	const double Now = FPlatformTime::Seconds();
	if (Veil >= 1.0f && LoadingShownAt < 0.0)
	{
		LoadingShownAt = Now;
		LoadingEndedAt = -1.0;
		LoadingBarFrac = 0.0f;
		LoadingLastPhase = 0;
	}
	P.Rect(0, 0, Canvas->SizeX, Canvas->SizeY, FLinearColor(Ink.R, Ink.G, Ink.B, Veil));

	float Target = 1.0f;
	FString Status = TEXT("Ready");
	if (Veil >= 1.0f && Menu && Menu->IsStartLoading())
	{
		Target = 0.9f;   // the start vista streaming behind the screen (no tile counts for it)
		Status = TEXT("Preparing start");
	}
	else if (Veil >= 1.0f && Menu)
	{
		int32 TerrainReady = 0, TerrainTotal = 0, RoadReady = 0, RoadTotal = 0;
		Menu->GetLoadingProgress(TerrainReady, TerrainTotal, RoadReady, RoadTotal);
		LoadingLastPhase = Menu->GetLoadingPhase();
		const int32 Total = TerrainTotal + RoadTotal;
		Target = LoadingLastPhase == 0 ? 0.8f * (Total > 0 ? float(TerrainReady + RoadReady) / Total : 0.0f)
			: LoadingLastPhase == 1 ? 0.85f : 0.95f;
		Status = LoadingLastPhase == 0 ? TEXT("Building terrain and roads")
			: LoadingLastPhase == 1 ? TEXT("Placing buildings") : TEXT("Streaming textures");
	}
	const float Dt = FMath::Min((float)FApp::GetDeltaTime(), 0.1f);
	LoadingBarFrac = FMath::Max(LoadingBarFrac, FMath::Lerp(LoadingBarFrac, Target, 1.0f - FMath::Exp(-Dt / 0.3f)));

	const float In = Veil < 1.0f ? 1.0f : FMath::SmoothStep(0.0f, 0.4f, float(Now - LoadingShownAt));
	const float A = In * Veil * Veil;   // text leaves a little ahead of the backdrop
	const float CX = Canvas->SizeX * 0.5f;
	const float CY = Canvas->SizeY * 0.5f;
	P.Text(TEXT("LOADING WORLD"), CX, CY - 24.0f * S, 40.0f, EMenuWeight::Bold, FLinearColor(1, 1, 1, A), true, 200);
	const float BarW = 360.0f * S;
	P.Rect(CX - BarW * 0.5f, CY + 16.0f * S, BarW, 3.0f * S, FLinearColor(1, 1, 1, 0.12f * A));
	P.Rect(CX - BarW * 0.5f, CY + 16.0f * S, BarW * LoadingBarFrac, 3.0f * S, FLinearColor(Accent.R, Accent.G, Accent.B, A));
	P.Text(Status.ToUpper(), CX, CY + 46.0f * S, 16.0f, EMenuWeight::SemiBold,
		FLinearColor(DimCol.R, DimCol.G, DimCol.B, A), true, 120);
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
	// ...and the Start screen's vista (Taylor, 2026-10-16): the globe used to sit
	// there half-interactive while the vista streamed, then cut; now the screen
	// holds until AODMainMenu's fade-cut, whose camera fade-in reveals the vista
	if (Menu->IsLoading() || Menu->IsStartLoading())
	{
		DrawLoadingScreen(Menu, 1.0f);
		return;
	}

	// ---- menu screens: left column (right over the Scene vista, so the car stays clear)
	if (Menu->GetStage() == EODMenuStage::Menu)
	{
		const EODMenuScreen Screen = Menu->GetScreen();
		if (Screen == EODMenuScreen::World && !Menu->IsSettingsOpen())
		{
			DrawGlobePins(Menu);   // no backdrop: the globe is the screen
			return;
		}
		// AreaMap / Start (2026-10-14): rows right, no dim - the globe drape
		// (AreaMap: roads + start dots drawn over it first) or the live vista at
		// the confirmed start shows to the left
		const bool bAreaMap = Screen == EODMenuScreen::AreaMap;
		const bool bNewArea = Screen == EODMenuScreen::NewArea;
		const bool bVista = Menu->IsVistaShown() || bAreaMap || bNewArea || Screen == EODMenuScreen::Start;
		DrawMenuBackdrop(bVista ? 0.0f : 0.2f, bVista);
		if (Menu->IsSettingsOpen())
		{
			DrawSettingsPanel(Menu->GetSettingsMenu(), bVista);
			return;
		}
		// AreaMap: the active tab is the area (one tab per clustered area), its blurb under it
		const TArrayView<const FODMenuTab> Tabs = Menu->GetTabs();
		const FString Title = bAreaMap && Tabs.IsValidIndex(Menu->GetTab()) ? FString(Tabs[Menu->GetTab()].Name) : Menu->GetScreenTitle();
		if (bAreaMap)
		{
			DrawAreaMap(Menu);
		}
		else if (bNewArea)
		{
			DrawNewArea(Menu);
		}
		const float PanelBottom = DrawMenuPanel(Title, Tabs, Menu->GetTab(), Menu->GetFooterRow(), Menu->GetSelected(),
			[Menu](int32 Row, FString& L, FString& V) { Menu->GetRow(Row, L, V); }, bVista,
			bAreaMap ? Menu->GetAreaBlurb() : FString());
		const float HX = MenuX(bVista, ColW * S);
		if (Screen == EODMenuScreen::Paint)
		{
			// under the rows in the menu column, clear of the car (Taylor 2026-10-16)
			DrawColorPicker(HX + 10.0f * S, PanelBottom + 34.0f * S);
		}
		if (Screen == EODMenuScreen::Main)
		{
			DrawHints(P, HX, {{TEXT("Arrows"), TEXT("Select")}, {TEXT("Enter"), TEXT("Confirm")}});
		}
		else if (bNewArea)
		{
			// (2026-10-21) the live estimate / bake progress under the rows; hints at the left, under the map
			TArray<FODAreaDraw::FLine> Lines;
			Menu->GetAreaDraw().Readout(Lines);
			for (int32 i = 0; i < Lines.Num(); ++i)
			{
				P.Text(Lines[i].Text, HX + 10.0f * S, PanelBottom + (30.0f + 26.0f * i) * S, 18.0f, EMenuWeight::SemiBold,
					Lines[i].Kind == 2 ? Danger : Lines[i].Kind == 1 ? DimCol : TextCol);
			}
			if (Menu->GetAreaDraw().CanEdit())
			{
				DrawHints(P, MenuX(false, ColW * S), {{TEXT("Click"), TEXT("Add Point")}, {TEXT("Double-click"), TEXT("Finish")},
					{TEXT("Drag"), TEXT("Move / Spin")}, {TEXT("Right-click"), TEXT("Delete Point")}, {TEXT("Ctrl+Z"), TEXT("Undo")},
					{TEXT("Esc"), TEXT("World Map")}});
			}
			else
			{
				DrawHints(P, MenuX(false, ColW * S), {{TEXT("Arrows"), TEXT("Select / Change")}, {TEXT("Enter"), TEXT("Confirm")},
					{TEXT("Esc"), TEXT("World Map")}});
			}
		}
		else if (bAreaMap)
		{
			DrawHints(P, HX, {{TEXT("Q/E"), TEXT("Area")}, {TEXT("Arrows"), TEXT("Select")}, {TEXT("Drag / Wheel"), TEXT("Pan / Zoom")},
				{TEXT("Enter"), TEXT("Pick")}, {TEXT("Esc"), TEXT("World Map")}});
		}
		else
		{
			DrawHints(P, HX, {{TEXT("Arrows"), TEXT("Select / Change")},
				{TEXT("Enter"), Screen == EODMenuScreen::Start ? TEXT("Drive") : TEXT("Confirm")},
				{TEXT("Esc"), TEXT("Back")}});
		}
		return;
	}

	// ---- map select (QA hook only): full route + clickable town nodes ----
	P.Rect(0, 0, Canvas->SizeX, Canvas->SizeY, FLinearColor(0, 0, 0, 0.55f));
	auto Text = [&](const FString& Str, float X, float Y, float Sc, const FLinearColor& C)
	{
		HudText(Canvas, Str, X, Y, Sc, C);
	};
	const float FullSize = FMath::Min(Canvas->SizeY - 170.0f, Canvas->SizeX * 0.6f);
	const float PanelX = (Canvas->SizeX - FullSize) * 0.5f;
	const float PanelY = (Canvas->SizeY - FullSize) * 0.5f + 12.0f;
	Text(TEXT("CHOOSE A TURNOUT"), PanelX, PanelY - 48.0f, 1.8f, FLinearColor::White);

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
		: TEXT("hover a turnout, or press enter to start here");
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
	if (bWorldMapOpen && bTrackEdit)
	{
		if (EditWaypoints.Num() > 0)
		{
			EditWaypoints.Pop();   // undo the last point
			RebuildEditTrack(FindNavRoadMgr());
		}
		return;
	}
	if (AODRoadManager* RoadMgr = FindNavRoadMgr())
	{
		RoadMgr->ClearRoute();
	}
}

void AODRaceHUD::RebuildEditTrack(const AODRoadManager* RoadMgr)
{
	bEditSaveFailed = false;
	EditFailLeg = INDEX_NONE;
	EditXY.Reset();
	EditZ.Reset();
	if (RoadMgr && EditWaypoints.Num() >= 2)
	{
		RoadMgr->RouteThrough(EditWaypoints, bEditLoop, EditXY, EditZ, EditFailLeg);
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
	auto Text = [&](const FString& S, float X, float Y, float Sc, const FLinearColor& C)
	{
		HudText(Canvas, S, X, Y, Sc, C);
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
		if (bTrackEdit)
		{
			EditWaypoints.Add(ToWorld(S));
			RebuildEditTrack(RoadMgr);
			return;
		}
		const int32 T = TownNear(S, 18.0f);
		FVector2D Dest;
		FString Name;
		const AODChallengeManager* Challenges = AODChallengeManager::Find(GetWorld());
		if (Challenges && Challenges->LandmarkNear(ToWorld(S), 18.0f / Scale(), Dest, Name))
		{
			// Dest + Name set: the landmark's snapped road point
		}
		else if (T != INDEX_NONE)
		{
			Dest = Towns[T].Pos;
			Name = Towns[T].Name;
		}
		else if (RoadMgr->SnapToRouteGraph(ToWorld(S), Dest))
		{
			Name = TEXT("Waypoint");
		}
		if (Name.IsEmpty() || !RoadMgr->SetRouteDestination(Dest, Name))
		{
			NoRouteMsgTime = RealNow;
			return;
		}
		// every map route is a road trip (2026-10-22): start banner + arrival card
		for (TActorIterator<AODRoadTrip> It(GetWorld()); It; ++It)
		{
			It->Destroy();
		}
		AODRoadTrip* Trip = GetWorld()->SpawnActor<AODRoadTrip>();
		Trip->Dest = Dest;
		Trip->DestName = Name;
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
			if (bTrackEdit)
			{
				ClearRouteKey();   // undo the last point
			}
			else
			{
				RoadMgr->ClearRoute();
			}
		}
		if (Pressed(EKeys::T) || Pressed(EKeys::Gamepad_LeftThumbstick))
		{
			bTrackEdit = !bTrackEdit;   // points are kept until saved
		}
		if (bTrackEdit && (Pressed(EKeys::L) || Pressed(EKeys::Gamepad_RightThumbstick)))
		{
			bEditLoop = !bEditLoop;
			RebuildEditTrack(RoadMgr);
		}
		const bool bF = Pressed(EKeys::F) || Pressed(EKeys::Gamepad_FaceButton_Top);
		if (bF && bTrackEdit && EditXY.Num() >= 2)
		{
			const FString Name = AODRaceManager::SaveTrack(RoadMgr->GetAreaKey(), EditXY, EditZ, bEditLoop);
			bEditSaveFailed = Name.IsEmpty();
			for (TActorIterator<AODMainMenu> It(GetWorld()); It && !bEditSaveFailed; ++It)
			{
				CloseWorldMap();
				bTrackEdit = false;
				EditWaypoints.Reset();
				RebuildEditTrack(nullptr);
				It->DriveTrack(Name, RoadMgr);
				return;
			}
		}
		if (bF && !bTrackEdit && Hover != INDEX_NONE)
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

	if (bTrackEdit)
	{
		auto Line = [&](const FVector2D& A, const FVector2D& B, const FLinearColor& C, float W)
		{
			FCanvasLineItem L(ToScreen(A), ToScreen(B));
			L.SetColor(C);
			L.LineThickness = W;
			Canvas->DrawItem(L);
		};
		for (int32 i = 1; i < EditXY.Num(); ++i)
		{
			Line(EditXY[i - 1], EditXY[i], FLinearColor(1.0f, 0.55f, 0.1f), 4.0f);
		}
		const int32 K = EditWaypoints.Num();
		if (EditFailLeg != INDEX_NONE)
		{
			Line(EditWaypoints[EditFailLeg], EditWaypoints[(EditFailLeg + 1) % K], FLinearColor(1.0f, 0.2f, 0.15f), 2.0f);
		}
		for (int32 i = 0; i < K; ++i)
		{
			const FVector2D S = ToScreen(EditWaypoints[i]);
			FCanvasTileItem Dot(S - FVector2D(6, 6), FVector2D(12, 12),
				i == 0 ? FLinearColor(0.3f, 1.0f, 0.4f) : FLinearColor(1.0f, 0.85f, 0.4f));
			Canvas->DrawItem(Dot);
			Text(FString::FromInt(i + 1), S.X + 8.0f, S.Y - 20.0f, 1.0f, FLinearColor::White);
		}
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
	if (bTrackEdit)
	{
		double LenCm = 0.0;
		for (int32 i = 1; i < EditXY.Num(); ++i)
		{
			LenCm += FVector2D::Distance(EditXY[i - 1], EditXY[i]);
		}
		const int32 K = EditWaypoints.Num();
		const FString EditStatus = bEditSaveFailed ? FString(TEXT("couldn't save - under 100 m, or no road height data here"))
			: EditFailLeg != INDEX_NONE ? FString::Printf(TEXT("no road from point %d to %d"), EditFailLeg + 1, (EditFailLeg + 1) % K + 1)
			: EditXY.Num() >= 2 ? FString::Printf(TEXT("%d points  ·  %s  ·  %s"), K, *FormatNavDist(LenCm, bMetric),
				bEditLoop ? TEXT("loop") : TEXT("point to point"))
			: FString::Printf(TEXT("click roads to add points  ·  %s"), bEditLoop ? TEXT("loop") : TEXT("point to point"));
		Text(TEXT("TRACK EDITOR"), 24.0f, 14.0f, 1.8f, FLinearColor(1.0f, 0.75f, 0.4f));
		Text(EditStatus, 300.0f, 22.0f, 1.2f, bEditSaveFailed || EditFailLeg != INDEX_NONE
			? FLinearColor(1.0f, 0.45f, 0.35f) : FLinearColor(1.0f, 0.8f, 0.5f));
		Text(TEXT("click: add point    right-click / backspace: undo    L: loop    F: save + drive    T: world map    drag / wheel: pan / zoom    esc: close"),
			24.0f, Canvas->SizeY - 56.0f, 1.0f, FLinearColor(0.7f, 0.75f, 0.8f));
		Text(TEXT("pad: A add at crosshair    X undo    R3 loop    Y save + drive    L3 world map    B / back: close"),
			24.0f, Canvas->SizeY - 32.0f, 1.0f, FLinearColor(0.55f, 0.6f, 0.65f));
		return;
	}
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
	Text(TEXT("drag / arrows pan    wheel / [ ] zoom    click: turnout or road waypoint    right-click / backspace: clear route    T: track editor    tab / esc: close"),
		24.0f, Canvas->SizeY - 56.0f, 1.0f, FLinearColor(0.7f, 0.75f, 0.8f));
	Text(TEXT("pad: stick pan    LB/RB zoom    A route to crosshair    X clear    Y fast travel    L3 track editor    B / back: close"),
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
	// (2026-10-14) restyled to the gauge cluster's look: Rajdhani, ink panel
	// with a route-coloured edge bar, scaled from 1080p like the menus
	const FMenuPaint P{Canvas, MenuScale()};
	const float S = P.S;
	const float W = 440.0f * S, H = 80.0f * S;
	const float X = Canvas->SizeX * 0.5f - W * 0.5f, Y = 22.0f * S;
	const FLinearColor RouteCol(1.0f, 0.55f, 0.9f);   // the map's route line
	P.Fade(X, X + W, Y, H, Ink, 0.78f, 0.5f);
	if (!RoadMgr->HasRoute())
	{
		const FLinearColor Ok(0.4f, 1.0f, 0.5f);
		P.Rect(X, Y, 4.0f * S, H, Ok);
		P.Text(TEXT("ARRIVED"), X + 24.0f * S, Y + 28.0f * S, 28.0f, EMenuWeight::Bold, Ok, false, 80);
		P.Text(RoadMgr->GetArrivedName(), X + 24.0f * S, Y + 58.0f * S, 18.0f, EMenuWeight::SemiBold, TextCol);
		return;
	}
	P.Rect(X, Y, 4.0f * S, H, RouteCol);

	// turn arrow: stem up from the bottom, then bent by the turn angle
	// (+ = right = clockwise on screen); straight when no turn is coming
	float TurnCm = 0.0f, TurnDeg = 0.0f;
	FString TurnRoad;
	const bool bTurn = RoadMgr->GetNextTurn(TurnCm, TurnDeg, &TurnRoad);
	const float A = FMath::DegreesToRadians(bTurn ? FMath::Clamp(TurnDeg, -135.0f, 135.0f) : 0.0f);
	const FVector2D Knee(X + 48.0f * S, Y + 40.0f * S);
	const FVector2D Dir(FMath::Sin(A), -FMath::Cos(A));
	const FVector2D Tip = Knee + Dir * 14.0f * S;
	const FVector2D Side(-Dir.Y, Dir.X);
	auto Stroke = [&](const FVector2D& From, const FVector2D& To)
	{
		FCanvasLineItem Item(From, To);
		Item.SetColor(RouteCol);
		Item.LineThickness = 5.0f * S;
		Canvas->DrawItem(Item);
	};
	Stroke(Knee + FVector2D(0, 26.0f * S), Knee);
	Stroke(Knee, Tip);
	const FVector2D Head[3] = {Tip + Dir * 12.0f * S, Tip + Side * 10.0f * S, Tip - Side * 10.0f * S};
	ODHudDraw::Poly(Canvas, Head, RouteCol);

	const float Abs = FMath::Abs(TurnDeg);
	const TCHAR* Side2 = TurnDeg > 0.0f ? TEXT("right") : TEXT("left");
	const FString Line1 = !bTurn ? FString(TEXT("Continue"))
		: FString::Printf(TEXT("%s %s in %s"), Abs >= 120.0f ? TEXT("Sharp") : Abs >= 60.0f ? TEXT("Turn") : TEXT("Keep"),
			Side2, *FormatNavDist(TurnCm, bMetric));
	P.Text(Line1.ToUpper(), X + 92.0f * S, Y + 28.0f * S, 28.0f, EMenuWeight::Bold, FLinearColor::White, false, 40);
	P.Text(FString::Printf(TEXT("%s%s  ·  %s"), bTurn && !TurnRoad.IsEmpty() ? *(TEXT("onto ") + TurnRoad + TEXT("  ·  ")) : TEXT(""),
			*RoadMgr->GetRouteName(), *FormatNavDist(RoadMgr->GetRouteRemainingCm(), bMetric)),
		X + 92.0f * S, Y + 58.0f * S, 18.0f, EMenuWeight::SemiBold, DimCol);
}

// ---- now-playing card (fun-features #1 v2): bottom-left, slides in for a
// few seconds on a track / play-state change and stays while the music is
// paused. Bottom = the card's bottom edge in pixels.
void DrawNowPlayingCard(UCanvas* C, float S, float Bottom)
{
	const ODMedia::FNowPlaying& NP = ODMedia::NowPlaying();
	const double Now = FPlatformTime::Seconds();
	const bool bShow = NP.bValid && !NP.Title.IsEmpty() && (!NP.bPlaying || Now - NP.ChangedAt < 4.0);
	// eased toward shown/hidden; exp of real Dt, so the same at any fps
	static float Shown = 0.0f;
	Shown = FMath::Lerp(bShow ? 1.0f : 0.0f, Shown, FMath::Exp(-FApp::GetDeltaTime() / 0.12f));
	if (Shown < 0.01f)
	{
		return;
	}
	const FMenuPaint P{C, S};
	const float W = 380.0f * S, H = 84.0f * S, Pad = 10.0f * S, ArtSz = H - 2.0f * Pad;
	const float X = FMath::Lerp(-W - 8.0f * S, 40.0f * S, FMath::SmoothStep(0.0f, 1.0f, Shown));
	const float Y = Bottom - H;
	P.Fade(X, X + W, Y, H, Ink, 0.82f, 0.55f);
	P.Rect(X, Y, 4.0f * S, H, Accent);

	float TX = X + 4.0f * S + Pad;
	if (NP.Art && NP.Art->GetResource())
	{
		// centre-square crop: browsers publish 16:9 video thumbnails
		const float Aspect = float(NP.Art->GetSizeX()) / FMath::Max(1, NP.Art->GetSizeY());
		const float U = Aspect > 1.0f ? 0.5f * (1.0f - 1.0f / Aspect) : 0.0f;
		const float V = Aspect < 1.0f ? 0.5f * (1.0f - Aspect) : 0.0f;
		FCanvasTileItem T(FVector2D(TX, Y + Pad), NP.Art->GetResource(), FVector2D(ArtSz, ArtSz),
			FVector2D(U, V), FVector2D(1.0f - U, 1.0f - V), FLinearColor::White);
		C->DrawItem(T);
		TX += ArtSz + Pad * 1.4f;
	}
	// clip long names with an ellipsis to the card's width
	const float MaxW = X + W - Pad - TX;
	auto Fit = [&P, MaxW](FString Str, float Size, EMenuWeight Wt)
	{
		if (P.Width(Str, Size, Wt) <= MaxW)
		{
			return Str;
		}
		while (Str.Len() > 1 && P.Width(Str + TEXT("..."), Size, Wt) > MaxW)
		{
			Str.LeftChopInline(1);
		}
		return Str + TEXT("...");
	};
	P.Text(NP.bPlaying ? TEXT("NOW PLAYING") : TEXT("PAUSED"), TX, Y + 18.0f * S, 13.0f, EMenuWeight::SemiBold,
		NP.bPlaying ? DimCol : Accent, false, 80);
	P.Text(Fit(NP.Title, 22.0f, EMenuWeight::Bold), TX, Y + 42.0f * S, 22.0f, EMenuWeight::Bold, FLinearColor::White);
	P.Text(Fit(NP.Artist, 17.0f, EMenuWeight::Medium), TX, Y + 65.0f * S, 17.0f, EMenuWeight::Medium, TextCol);
}

void AODRaceHUD::DrawHUD()
{
	Super::DrawHUD();
	if (!Canvas)
	{
		return;
	}
	MenuHits.Reset();   // refilled by whichever menu draws this frame
	PickerSV = PickerHue = FBox2D(ForceInit);

	// loading-screen fade-out (2026-10-14): once AODMainMenu releases to
	// Playing, a fading copy of the loading screen draws over everything else
	// this frame (scope exit = last), over LoadingFadeSec of real time
	if (LoadingShownAt >= 0.0)
	{
		const AODMainMenu* LoadMenu = nullptr;
		for (TActorIterator<AODMainMenu> It(GetWorld()); It; ++It)
		{
			LoadMenu = *It;
			break;
		}
		if (!LoadMenu || !LoadMenu->IsActive())
		{
			LoadingShownAt = -1.0;
			LoadingEndedAt = FPlatformTime::Seconds();
		}
		else if (!LoadMenu->IsLoading() && !LoadMenu->IsStartLoading())
		{
			LoadingShownAt = -1.0;   // back to a menu screen: no fade over it
		}
	}
	ON_SCOPE_EXIT
	{
		constexpr float LoadingFadeSec = 0.7f;
		const float T = LoadingEndedAt < 0.0 ? 1.0f : float(FPlatformTime::Seconds() - LoadingEndedAt) / LoadingFadeSec;
		if (T < 1.0f)
		{
			DrawLoadingScreen(nullptr, 1.0f - FMath::SmoothStep(0.0f, 1.0f, T));
		}
		else
		{
			LoadingEndedAt = -1.0;
		}
	};

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
		if (!Race->TrackName.IsEmpty())
		{
			break;   // a picked custom track over a level-placed race
		}
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

	// draws PlaceLabelText just above the mini-map's top edge (the pause
	// map draws its own in the menu font); no-ops on an empty
	// label (RoadMgr missing, prefix unset, or no nearby label point yet).
	auto DrawPlaceLabel = [&](float PanelX, float PanelTopY)
	{
		if (PlaceLabelText.IsEmpty())
		{
			return;
		}
		// via FMenuPaint (2026-10-14): a bare MenuFont text item has no UFont,
		// fails HasValidText() and silently never drew
		const FMenuPaint P{Canvas, MenuScale()};
		P.Text(PlaceLabelText.ToUpper(), PanelX, PanelTopY - 22.0f * P.S, 17.0f, EMenuWeight::SemiBold, TextCol, false, 60);
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
		if (Menu->IsPhotoOpen())
		{
			// fun #4: the scene stays clear - no backdrop, H hides the panel, and
			// the frame a screenshot reads back draws nothing
			const AODPhotoMode* Photo = Menu->GetPhotoMode();
			if (Photo && !Photo->IsUiHidden() && !FScreenshotRequest::IsScreenshotRequested())
			{
				DrawMenuPanel(Menu->GetTitle(), PhotoTabs, 0, Menu->GetFooterRow(), Menu->GetSelected(),
					[Menu](int32 Row, FString& L, FString& V) { Menu->GetRow(Row, L, V); }, false);
				const FMenuPaint P{Canvas, MenuScale()};
				const float X = MenuX(false, ColW * P.S);
				DrawHints(P, X, {{TEXT("WASD"), TEXT("Move")}, {TEXT("R/F"), TEXT("Up / Down")}, {TEXT("Right Drag"), TEXT("Look")},
					{TEXT("Space"), TEXT("Photo")}, {TEXT("H"), TEXT("Hide")}, {TEXT("Esc"), TEXT("Back")}});
				if (const FString Toast = Photo->GetToast(); !Toast.IsEmpty())
				{
					P.Text(Toast, X, Canvas->SizeY - 100.0f * P.S, 18.0f, EMenuWeight::SemiBold, Accent);
				}
				// right-drag looks, so the right button doesn't back out here
				DispatchMenuMouse(Menu, GetOwningPlayerController(), MenuHits, LastMenuMouse, LastMenuMouseFrame, true, false);
			}
			return;
		}
		DrawMenuBackdrop(0.35f, false);
		if (Menu->IsSettingsOpen())
		{
			DrawSettingsPanel(Menu->GetSettingsMenu(), false);
		}
		else
		{
			DrawMenuPanel(Menu->GetTitle(), Menu->IsEnvOpen() ? EnvTabs : PauseTabs, 0, Menu->GetFooterRow(), Menu->GetSelected(),
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
			if (const AODChallengeManager* Challenges = AODChallengeManager::Find(GetWorld()))
			{
				const float ListY = FullMapY + FullSize + 12.0f * S;
				Challenges->DrawChecklist(Canvas, S, FullMapX, ListY, FullSize, Canvas->SizeY - ListY - 12.0f * S, bMetric);
			}
		}
		HandleMenuMouse(Menu);
		return;   // drawn instead of the in-game HUD, like the main menu
	}

	const float Now = GetWorld()->GetTimeSeconds();

	// race timer, warnings (2026-10-14): Rajdhani + the cluster palette,
	// centred and scaled from 1080p like the menus
	const FMenuPaint HP{Canvas, MenuScale()};
	if (const AODReplay* Replay = AODReplay::Find(GetWorld()); Replay && Replay->IsActive())
	{
		Replay->DrawOverlay(Canvas, MenuScale());
	}
	const FLinearColor Go(0.3f, 1.0f, 0.4f);
	if (const AODChallengeManager* Challenges = AODChallengeManager::Find(GetWorld()))
	{
		Challenges->DrawToast(Canvas, HP.S, bMetric);
	}

	// ---- race timer / gate progress (needs an actual race)
	if (Race)
	{
		const float X = Canvas->SizeX * 0.5f;
		float Y = 30.0f * HP.S;
		auto Line = [&](const FString& Text, const FLinearColor& Color, float Size, EMenuWeight W, int32 Spacing)
		{
			HP.Text(Text, X, Y + Size * 0.6f * HP.S, Size, W, Color, true, Spacing);
			Y += Size * 1.2f * HP.S;
		};

		if (Race->bRunning)
		{
			Line(FormatTime(Race->ElapsedTime()), FLinearColor::White, 46.0f, EMenuWeight::Bold, 40);
			Line(FString::Printf(TEXT("GATE %d / %d"), Race->NextGate, Race->Checkpoints.Num()),
				Accent, 18.0f, EMenuWeight::SemiBold, 120);
			if (Race->ElapsedTime() < 1.5f)
			{
				Line(TEXT("GO!"), Go, 52.0f, EMenuWeight::Bold, 80);
			}
			else if (Now - Race->SplitShownAt < 3.0f)
			{
				// sector split vs best run: green = ahead, red = behind
				Line(FString::Printf(TEXT("%+.2f"), Race->SplitDelta), Race->SplitDelta <= 0.0f ? Go : Danger,
					30.0f, EMenuWeight::Bold, 40);
			}
		}
		else
		{
			Line(TEXT("CROSS THE GREEN GATE TO START"), Go, 20.0f, EMenuWeight::SemiBold, 80);
			if (Race->LastTime > 0.0f)
			{
				Line(FString::Printf(TEXT("LAST  %s"), *FormatTime(Race->LastTime)), TextCol, 20.0f, EMenuWeight::SemiBold, 60);
			}
		}
		if (Race->BestTime > 0.0f)
		{
			Line(FString::Printf(TEXT("BEST  %s"), *FormatTime(Race->BestTime)), Accent, 20.0f, EMenuWeight::SemiBold, 60);
		}
	}

	// centred warning line on a soft ink band that fades out to both sides
	auto Banner = [&](const FString& Text, float CY, const FLinearColor& Col)
	{
		const float CX = Canvas->SizeX * 0.5f, HW = 460.0f * HP.S, BH = 64.0f * HP.S;
		HP.Fade(CX - HW, CX, CY - BH * 0.5f, BH, Ink, 0.0f, 0.6f);
		HP.Fade(CX, CX + HW, CY - BH * 0.5f, BH, Ink, 0.6f, 0.0f);
		HP.Text(Text.ToUpper(), CX, CY, 36.0f, EMenuWeight::Bold, Col, true, 80);
	};

	// ---- out-of-bounds countdown (2026-09-06) - warns while the car is past
	// AODVehiclePawn::OobDistCm, before the reset teleports it back. Centered
	// and big: the player needs to know WHY they're about to move.
	if (const AODVehiclePawn* Veh = Cast<AODVehiclePawn>(GetOwningPawn()))
	{
		const float Cd = Veh->GetOobCountdownSec();
		if (Cd >= 0.0f)
		{
			Banner(FString::Printf(TEXT("%s  ·  RETURNING IN %.0f"), Veh->IsOobStuckReset() ? TEXT("STUCK") : TEXT("OFF ROAD"),
				FMath::CeilToFloat(Cd)), Canvas->SizeY * 0.28f, Danger);
		}
	}

	// ---- weather event warning (W4/W5, sec 7.42) - AODWeatherController
	// sets the text on tornado spawn / hurricane onset; same banner style as
	// the out-of-bounds countdown, shown for 6 s.
	for (TActorIterator<AODWeatherController> It(GetWorld()); It; ++It)
	{
		if (Now - It->WarningShownAt < 6.0f)
		{
			Banner(It->WarningText, Canvas->SizeY * 0.18f, Warn);
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
			ODGaugeCluster::Draw(Canvas, G, Now);
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
		const float MapS = MenuScale();
		const float MapSize = MiniMapSize * MapS, MapX = 40.0f * MapS, MapY = Canvas->SizeY - MapSize - 28.0f * MapS;
		DrawPlaceLabel(MapX, MapY);
		// zoom out with speed so the road ahead covers a similar look-ahead
		// time (x2 by ~65 mph, x3 by ~130); eased so it breathes, not jumps
		const APawn* MapPawn = GetOwningPawn();
		const float SpeedMs = MapPawn ? MapPawn->GetVelocity().Size() * 0.01f : 0.0f;
		const float Target = 1.0f + SpeedMs / 29.0f;
		MiniMapSpeedZoom = FMath::Lerp(Target, MiniMapSpeedZoom,
			FMath::Exp(-GetWorld()->GetDeltaSeconds() / 0.8f));
		DrawStageMap(MapCenterline, MapGates, Race && Race->bRunning,
			Race ? Race->NextGate : INDEX_NONE, MapX, MapY, MapSize, 0.55f,
			MiniMapZoomM * MiniMapSpeedZoom, true, RoadMgr);
	}

	// ---- navigation next-turn / distance panel (SS7.37)
	if (const AODRoadManager* NavMgr = FindNavRoadMgr(); NavMgr)
	{
		DrawNavPanel(NavMgr);   // nearest area's road (2026-10-05, large world)
	}

	// ponytail: fixed offset above the mini-map + its place label; move into a
	// shared bottom-left toast stack if more cards land there
	DrawNowPlayingCard(Canvas, MenuScale(), bShowMiniMap
		? Canvas->SizeY - (MiniMapSize + 28.0f + 44.0f) * MenuScale() : Canvas->SizeY - 28.0f * MenuScale());
	ODRoadTrip::DrawHUD(this, Canvas, MenuScale(), bMetric);   // ?oddest= start banner / arrival card
}
