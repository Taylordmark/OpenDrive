#include "ODRoadTrip.h"
#include "ODHudStyle.h"
#include "ODMainMenu.h"
#include "ODRoadManager.h"
#include "ODVehiclePawn.h"
#include "Application/SlateApplicationBase.h"
#include "CanvasItem.h"
#include "Engine/Engine.h"
#include "Fonts/FontMeasure.h"
#include "Rendering/SlateRenderer.h"
#include "Engine/Canvas.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"

namespace
{
constexpr double BannerSec = 7.0;            // start banner on screen
constexpr double CardSec = 15.0;             // arrival card, then the actor goes
constexpr double TripCheckTimeoutSec = 300.0;   // -odtripcheck: real seconds to wait for Playing + graph
constexpr float TeleportCmS = 15000.0f;      // a step faster than 540 km/h is a fast travel, not driving
}

AODRoadTrip::AODRoadTrip()
{
	PrimaryActorTick.bCanEverTick = true;
	PrimaryActorTick.bTickEvenWhenPaused = true;   // the -odtripcheck timeout counts through the menu
}

bool AODRoadTrip::ParseDest(const TCHAR* Opt, FVector2D& Out)
{
	FString X, Y;
	if (!Opt || !FString(Opt).Split(TEXT(","), &X, &Y) || !X.IsNumeric() || !Y.IsNumeric())
	{
		return false;
	}
	Out = FVector2D(FCString::Atod(*X), FCString::Atod(*Y));
	return true;
}

void AODRoadTrip::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	UWorld* World = GetWorld();
	if (CreatedReal == 0.0)
	{
		CreatedReal = World->GetRealTimeSeconds();
		bCheck = FParse::Param(FCommandLine::Get(), TEXT("odtripcheck"));
	}
	if (State == EState::Waiting)
	{
		BeginTrip();
		if (State == EState::Waiting && bCheck && World->GetRealTimeSeconds() - CreatedReal > TripCheckTimeoutSec)
		{
			SelfCheck(false, FString::Printf(TEXT("timed out: road graph %s, menu %s"),
				Road.IsValid() && Road->HasRouteGraph() ? TEXT("loaded") : TEXT("missing"),
				TActorIterator<AODMainMenu>(World) && TActorIterator<AODMainMenu>(World)->IsActive() ? TEXT("still up") : TEXT("done")));
		}
		return;
	}
	if (World->IsPaused())
	{
		return;
	}
	const double Now = World->GetTimeSeconds();
	if (State == EState::Arrived)
	{
		if (Now - ArriveTime > CardSec)
		{
			Destroy();
		}
		return;
	}
	const AODRoadManager* R = Road.Get();
	const APawn* Pawn = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!R || !Pawn)
	{
		return;
	}
	// distance from position steps, speed from the steps too: the same at any
	// frame rate (a long frame is just a longer chord)
	const FVector Pos = Pawn->GetActorLocation();
	const double Step = FVector::Dist2D(Pos, LastPos);
	LastPos = Pos;
	if (DeltaSeconds > 0.0f && Step < TeleportCmS * DeltaSeconds)
	{
		DistCm += Step;
		TopCmS = FMath::Max(TopCmS, Pawn->GetVelocity().Size2D());
	}
	if (R->GetArrivedTime() >= StartTime && R->GetArrivedName() == DestName)
	{
		State = EState::Arrived;
		ArriveTime = Now;
		UE_LOG(LogTemp, Display, TEXT("ODRoadTrip: arrived at %s - %.1f km in %.0f s, top %.0f km/h"),
			*DestName, DistCm / 1.0e5, ArriveTime - StartTime, TopCmS * 0.036f);
	}
	else if (R->GetRouteName() != DestName)
	{
		UE_LOG(LogTemp, Display, TEXT("ODRoadTrip: route to %s cleared or replaced - trip over"), *DestName);
		Destroy();
	}
}

void AODRoadTrip::BeginTrip()
{
	UWorld* World = GetWorld();
	const AODVehiclePawn* Pawn = Cast<AODVehiclePawn>(UGameplayStatics::GetPlayerPawn(this, 0));
	// the drive has started: Drive's menu took the car out of Loading at its start town
	for (TActorIterator<AODMainMenu> It(World); It; ++It)
	{
		if (It->IsActive())
		{
			return;
		}
	}
	// same pick as AODRaceHUD::FindNavRoadMgr: the area being driven, else the first with a graph
	AODRoadManager* R = Pawn ? Pawn->GetNearestRoadMgr() : nullptr;
	for (TActorIterator<AODRoadManager> It(World); It && !(R && R->HasRouteGraph()); ++It)
	{
		R = *It;
	}
	Road = R;
	if (!Pawn || !R || !R->HasRouteGraph())
	{
		return;
	}
	auto NearestTown = [R](const FVector2D& P)
	{
		const FTownNode* Best = nullptr;
		for (const FTownNode& T : R->GetTownNodes())
		{
			if (!Best || FVector2D::DistSquared(T.Pos, P) < FVector2D::DistSquared(Best->Pos, P))
			{
				Best = &T;
			}
		}
		return Best ? Best->Name : FString();
	};
	// ponytail: names from the nearest turnout (towns-only picks); any-point picks would want a "near <town>"
	DestName = NearestTown(Dest);
	DestName = DestName.IsEmpty() ? FString(TEXT("Destination")) : DestName;
	StartName = NearestTown(FVector2D(Pawn->GetActorLocation()));
	StartName = StartName.IsEmpty() ? R->CurrentCityLabel : StartName;
	if (!R->SetRouteDestination(Dest, DestName))
	{
		UE_LOG(LogTemp, Warning, TEXT("ODRoadTrip: no route to %s (%.0f, %.0f)"), *DestName, Dest.X, Dest.Y);
		SelfCheck(false, TEXT("SetRouteDestination failed"));
		Destroy();
		return;
	}
	State = EState::Driving;
	StartTime = World->GetTimeSeconds();
	LastPos = Pawn->GetActorLocation();
	RouteKm = R->GetRouteRemainingCm() / 1.0e5f;
	UE_LOG(LogTemp, Display, TEXT("ODRoadTrip: %s -> %s, %.1f km"), *StartName, *DestName, RouteKm);

	FVector2D Snap;
	float TurnCm, TurnDeg;
	FString TurnRoad;
	const bool bTurn = R->GetNextTurn(TurnCm, TurnDeg, &TurnRoad);
	const bool bEnd = R->SnapToRouteGraph(Dest, Snap) && R->HasRoute()
		&& FVector2D::Distance(R->GetRoutePoints().Last(), Snap) < 100.0;
	SelfCheck(bEnd && RouteKm > 0.0f && R->GetRouteName() == DestName, FString::Printf(
		TEXT("dest (%.0f, %.0f) -> %s, %.1f km, ends on the snapped dest: %s, next turn: %s"),
		Dest.X, Dest.Y, *DestName, RouteKm, bEnd ? TEXT("yes") : TEXT("NO"),
		bTurn ? *FString::Printf(TEXT("%.0f m %s onto '%s'"), TurnCm / 100.0f, TurnDeg > 0.0f ? TEXT("right") : TEXT("left"), *TurnRoad)
			: TEXT("none")));
}

void AODRoadTrip::SelfCheck(bool bPass, const FString& Why)
{
	if (!bCheck)
	{
		return;
	}
	bCheck = false;
	UE_LOG(LogTemp, Display, TEXT("ODRoadTrip: SELFCHECK %s - %s"), bPass ? TEXT("PASS") : TEXT("FAIL"), *Why);
	FPlatformMisc::RequestExitWithStatus(false, bPass ? 0 : 1);
}

// ---- HUD ---------------------------------------------------------------------

void ODRoadTrip::DrawHUD(const AActor* Ctx, UCanvas* Canvas, float S, bool bMetric)
{
	using namespace ODHud;
	if (!Canvas || !Ctx)
	{
		return;
	}
	const AODRoadTrip* Trip = nullptr;
	for (TActorIterator<AODRoadTrip> It(Ctx->GetWorld()); It; ++It)
	{
		Trip = *It;
	}
	if (!Trip || Trip->State == AODRoadTrip::EState::Waiting)
	{
		return;
	}
	const double Now = Ctx->GetWorld()->GetTimeSeconds();
	auto Rect = [&](float X, float Y, float W, float H, const FLinearColor& Col)
	{
		FCanvasTileItem T(FVector2D(X, Y), FVector2D(W, H), Col);
		T.BlendMode = SE_BLEND_Translucent;
		Canvas->DrawItem(T);
	};
	// Align: 0 left, 1 centred, 2 right of X (mirrors ODRaceHUD's FMenuPaint::Text / Width)
	auto Text = [&](const FString& Str, float X, float Y, float Size, EMenuWeight Wt, const FLinearColor& Col, int32 Align)
	{
		const FSlateFontInfo F = MenuFont(Wt, Size * S);
		if (Align == 2)
		{
			X -= FSlateApplicationBase::IsInitialized() && FSlateApplicationBase::Get().GetRenderer()
				? FSlateApplicationBase::Get().GetRenderer()->GetFontMeasureService()->Measure(Str, F).X
				: Str.Len() * Size * S * 0.5f;
		}
		FCanvasTextItem T(FVector2D(X, Y), FText::FromString(Str), F, Col);
		T.bCentreX = Align == 1;
		T.bCentreY = true;
		T.EnableShadow(FLinearColor(0, 0, 0, 0.45f * Col.A), FVector2D(S, S));
		T.Font = GEngine->GetLargeFont();   // HasValidText() needs a UFont even with a SlateFontInfo
		Canvas->DrawItem(T);
	};
	auto Dist = [bMetric](double Cm)
	{
		return bMetric ? FString::Printf(TEXT("%.1f km"), Cm / 1.0e5) : FString::Printf(TEXT("%.1f mi"), Cm / 160934.0);
	};
	auto Speed = [bMetric](double CmS)
	{
		return FString::Printf(TEXT("%d %s"), FMath::RoundToInt(CmS * (bMetric ? 0.036 : 0.0223694)), bMetric ? TEXT("km/h") : TEXT("mph"));
	};
	const float Cx = Canvas->SizeX * 0.5f;

	if (Trip->State == AODRoadTrip::EState::Driving)
	{
		const double Age = Now - Trip->StartTime;
		if (Age > BannerSec)
		{
			return;
		}
		// below the nav panel (Y 22..102), fading out over its last second
		const float A = (float)FMath::Clamp(BannerSec - Age, 0.0, 1.0);
		const float W = 560.0f * S, H = 72.0f * S, X = Cx - W * 0.5f, Y = 118.0f * S;
		Rect(X, Y, W, H, Ink.CopyWithNewOpacity(0.78f * A));
		Rect(X, Y, W, 3.0f * S, Accent.CopyWithNewOpacity(A));
		Text(TEXT("ROAD TRIP"), Cx, Y + 20.0f * S, 16.0f, EMenuWeight::Bold, Accent.CopyWithNewOpacity(A), 1);
		Text(FString::Printf(TEXT("%s  to  %s  ·  %s"), *Trip->StartName, *Trip->DestName, *Dist(Trip->RouteKm * 1.0e5)),
			Cx, Y + 48.0f * S, 24.0f, EMenuWeight::SemiBold, TextCol.CopyWithNewOpacity(A), 1);
		return;
	}

	// arrival card
	const double Sec = FMath::Max(Trip->ArriveTime - Trip->StartTime, 1.0);
	const int32 T = FMath::RoundToInt(Sec);
	const FString Time = T >= 3600 ? FString::Printf(TEXT("%d:%02d:%02d"), T / 3600, T / 60 % 60, T % 60)
		: FString::Printf(TEXT("%d:%02d"), T / 60, T % 60);
	const TPair<const TCHAR*, FString> Rows[] = {
		{TEXT("Distance"), Dist(Trip->DistCm)},
		{TEXT("Time"), Time},
		{TEXT("Average speed"), Speed(Trip->DistCm / Sec)},
		{TEXT("Top speed"), Speed(Trip->TopCmS)},
	};
	const float W = 460.0f * S, RowH = 34.0f * S, H = 96.0f * S + RowH * UE_ARRAY_COUNT(Rows);
	const float X = Cx - W * 0.5f, Y = Canvas->SizeY * 0.28f;
	const FLinearColor Ok(0.4f, 1.0f, 0.5f);
	Rect(X, Y, W, H, Ink.CopyWithNewOpacity(0.82f));
	Rect(X, Y, W, 4.0f * S, Ok);
	Text(TEXT("TRIP COMPLETE"), Cx, Y + 30.0f * S, 28.0f, EMenuWeight::Bold, Ok, 1);
	Text(FString::Printf(TEXT("%s  to  %s"), *Trip->StartName, *Trip->DestName), Cx, Y + 62.0f * S, 18.0f, EMenuWeight::SemiBold, DimCol, 1);
	for (int32 i = 0; i < UE_ARRAY_COUNT(Rows); ++i)
	{
		const float Ry = Y + 100.0f * S + RowH * i;
		Text(Rows[i].Key, X + 32.0f * S, Ry, 20.0f, EMenuWeight::Medium, DimCol, 0);
		Text(Rows[i].Value, X + W - 32.0f * S, Ry, 22.0f, EMenuWeight::Bold, TextCol, 2);
	}
}
