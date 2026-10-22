#include "ODChallengeManager.h"
#include "CanvasItem.h"
#include "Dom/JsonObject.h"
#include "DrawDebugHelpers.h"
#include "Engine/Canvas.h"
#include "EngineUtils.h"
#include "GlobalRenderResources.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/ScopeExit.h"
#include "ODGaugeCluster.h"
#include "ODHudStyle.h"
#include "ODRoadManager.h"
#include "ODTileSource.h"
#include "ODVehicleMovement.h"
#include "ODVehiclePawn.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
	using ODHud::EMenuWeight;

	constexpr float GateHalfWidthCm = 1500.0f;    // a crossing counts this far either side of the road point
	constexpr float MarkerRangeCm = 150000.0f;    // markers drawn within 1.5 km
	constexpr float DiscoverPadCm = 15000.0f;     // landmark found within its radius + 150 m
	constexpr float ZoneMinAvgCmS = 500.0f;       // a zone run slower than 5 m/s on average is dropped
	constexpr double ToastSeconds = 4.5;
	const FLinearColor TrapCol(1.0f, 0.3f, 0.6f);
	const FLinearColor ZoneCol(0.3f, 0.85f, 1.0f);

	FString SpeedText(float Kmh, bool bMetric)
	{
		return bMetric ? FString::Printf(TEXT("%.0f km/h"), Kmh) : FString::Printf(TEXT("%.0f mph"), Kmh * 0.621371f);
	}

	void Label(UCanvas* C, const FString& Str, float X, float Y, float Size, EMenuWeight W,
		const FLinearColor& Col, bool bCenterX = false)
	{
		FCanvasTextItem T(FVector2D(X, Y), FText::FromString(Str), ODHud::MenuFont(W, Size), Col);
		T.bCentreX = bCenterX;
		T.bCentreY = true;
		T.EnableShadow(FLinearColor(0, 0, 0, 0.45f * Col.A));
		T.Font = GEngine->GetLargeFont();   // HasValidText() needs a UFont (see FMenuPaint::Text)
		C->DrawItem(T);
	}

	void FillRect(UCanvas* C, float X, float Y, float W, float H, const FLinearColor& Col)
	{
		FCanvasTileItem T(FVector2D(X, Y), FVector2D(W, H), Col);
		T.BlendMode = SE_BLEND_Translucent;
		C->DrawItem(T);
	}

	FVector2D Unit2D(const FVector& From, const FVector& To)
	{
		return FVector2D(To - From).GetSafeNormal();
	}
}

AODChallengeManager::AODChallengeManager()
{
	PrimaryActorTick.bCanEverTick = true;
}

AODChallengeManager* AODChallengeManager::Find(const UWorld* World)
{
	if (World)
	{
		for (TActorIterator<AODChallengeManager> It(World); It; ++It)
		{
			return *It;
		}
	}
	return nullptr;
}

int32 AODChallengeManager::GateCross(const FVector& Prev, const FVector& Cur, const FVector& Center,
	const FVector2D& Dir, float HalfWidthCm, double& OutT)
{
	const double S0 = FVector2D::DotProduct(FVector2D(Prev - Center), Dir);
	const double S1 = FVector2D::DotProduct(FVector2D(Cur - Center), Dir);
	if ((S0 < 0.0) == (S1 < 0.0))
	{
		return 0;
	}
	const double T = S0 / (S0 - S1);
	const FVector Hit = FMath::Lerp(Prev, Cur, T);
	if (FMath::Abs(FVector2D::DotProduct(FVector2D(Hit - Center), FVector2D(-Dir.Y, Dir.X))) > HalfWidthCm
		|| FMath::Abs(Hit.Z - Center.Z) > 1000.0)   // an overpass above / below the gate
	{
		return 0;
	}
	OutT = T;
	return S1 >= 0.0 ? 1 : -1;
}

void AODChallengeManager::BeginPlay()
{
	Super::BeginPlay();
	Load();
	Progress = Cast<UODAreaSave>(UGameplayStatics::LoadGameFromSlot(TEXT("OpenDrive_area_") + AreaKey, 0));
	if (!Progress)
	{
		Progress = Cast<UODAreaSave>(UGameplayStatics::CreateSaveGameObject(UODAreaSave::StaticClass()));
	}
	UE_LOG(LogTemp, Log, TEXT("ODChallengeManager: %s - %d trap(s), %d zone(s), %d landmark(s), %d discovered"),
		*AreaKey, Traps.Num(), Zones.Num(), Landmarks.Num(), Progress->Discovered.Num());
}

void AODChallengeManager::Load()
{
	// ponytail: the home area's sidecars only - a streamed neighbour's traps
	// and landmarks don't show; load per AODRoadManager if that matters
	const FString Masks = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks");
	auto Read = [&](const TCHAR* Kind) -> TSharedPtr<FJsonObject>
	{
		FString Text;
		TSharedPtr<FJsonObject> Root;
		const FString Path = ODTileSource::Fetch(Masks / FString::Printf(TEXT("%s_%s_test.json"), Kind, *AreaKey));
		if (!FFileHelper::LoadFileToString(Text, *Path)
			|| !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Root))
		{
			UE_LOG(LogTemp, Log, TEXT("ODChallengeManager: no %s"), *Path);
			return nullptr;
		}
		return Root;
	};
	auto Vec = [](const TArray<TSharedPtr<FJsonValue>>& A)
	{
		return FVector(A[0]->AsNumber(), A[1]->AsNumber(), A.Num() > 2 ? A[2]->AsNumber() : 0.0);
	};

	if (const TSharedPtr<FJsonObject> Root = Read(TEXT("challenges")))
	{
		for (const TSharedPtr<FJsonValue>& V : Root->GetArrayField(TEXT("traps")))
		{
			const TSharedPtr<FJsonObject>& O = V->AsObject();
			const float Yaw = FMath::DegreesToRadians(O->GetNumberField(TEXT("yaw_deg")));
			Traps.Add({O->GetStringField(TEXT("name")),
				FVector(O->GetNumberField(TEXT("x_cm")), O->GetNumberField(TEXT("y_cm")), O->GetNumberField(TEXT("z_cm"))),
				FVector2D(FMath::Cos(Yaw), FMath::Sin(Yaw))});
		}
		for (const TSharedPtr<FJsonValue>& V : Root->GetArrayField(TEXT("zones")))
		{
			const TSharedPtr<FJsonObject>& O = V->AsObject();
			FZone Z{O->GetStringField(TEXT("name")), float(O->GetNumberField(TEXT("length_m")))};
			for (const TSharedPtr<FJsonValue>& P : O->GetArrayField(TEXT("line_cm")))
			{
				Z.Line.Add(Vec(P->AsArray()));
			}
			if (Z.Line.Num() >= 2 && Z.LengthM > 0.0f)
			{
				Z.StartDir = Unit2D(Z.Line[0], Z.Line[1]);
				Z.EndDir = Unit2D(Z.Line[Z.Line.Num() - 2], Z.Line.Last());
				Zones.Add(MoveTemp(Z));
			}
		}
	}
	if (const TSharedPtr<FJsonObject> Root = Read(TEXT("landmarks")))
	{
		for (const TSharedPtr<FJsonValue>& V : Root->GetArrayField(TEXT("landmarks")))
		{
			const TSharedPtr<FJsonObject>& O = V->AsObject();
			Landmarks.Add({O->GetStringField(TEXT("name")), FVector2D(Vec(O->GetArrayField(TEXT("centroid_cm")))),
				float(O->GetNumberField(TEXT("radius_cm")))});
		}
	}
	SetActorTickEnabled(Traps.Num() + Zones.Num() + Landmarks.Num() > 0);
}

void AODChallengeManager::Save() const
{
	UGameplayStatics::SaveGameToSlot(Progress, TEXT("OpenDrive_area_") + AreaKey, 0);
}

void AODChallengeManager::Toast(const FString& Title, const FString& Detail, float Kmh, float BestKmh)
{
	ToastTitle = Title;
	ToastDetail = Detail;
	ToastKmh = Kmh;
	ToastBestKmh = BestKmh;
	ToastTime = GetWorld()->GetTimeSeconds();
}

void AODChallengeManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	const AODVehiclePawn* Pawn = Cast<AODVehiclePawn>(UGameplayStatics::GetPlayerPawn(this, 0));
	if (!Pawn || !Pawn->Movement || !Progress)
	{
		PrevTime = -1.0;
		return;
	}
	const double Now = GetWorld()->GetTimeSeconds();
	const FVector Pos = Pawn->GetActorLocation();
	const float Speed = FMath::Abs(Pawn->Movement->GetForwardSpeed());
	// first frame, or a teleport (fast travel, respawn): no step to cross on
	if (PrevTime < 0.0 || FVector::DistSquared2D(Pos, PrevPos) > FMath::Square(10000.0))
	{
		PrevPos = Pos;
		PrevSpeed = Speed;
		PrevTime = Now;
		ActiveZone = INDEX_NONE;
		return;
	}
	ON_SCOPE_EXIT
	{
		PrevPos = Pos;
		PrevSpeed = Speed;
		PrevTime = Now;
	};
	const double FrameDt = Now - PrevTime;
	double T = 0.0;

	// ---- speed traps: speed at the sub-frame crossing, either direction
	for (const FTrap& Trap : Traps)
	{
		if (FVector::DistSquared2D(Pos, Trap.Pos) > FMath::Square(MarkerRangeCm))
		{
			continue;
		}
		// ponytail: debug-draw markers, like AODRaceManager's gates (stripped
		// in Shipping) - a mesh post when the race gates get one
		DrawDebugCylinder(GetWorld(), Trap.Pos - FVector(0, 0, 100), Trap.Pos + FVector(0, 0, 700),
			600.0f, 20, TrapCol.ToFColor(true), false, -1.0f, 0, 10.0f);
		if (GateCross(PrevPos, Pos, Trap.Pos, Trap.Dir, GateHalfWidthCm, T))
		{
			const float Kmh = FMath::Lerp(PrevSpeed, Speed, float(T)) * 0.036f;
			float& Best = Progress->TrapBestKmh.FindOrAdd(Trap.Name);
			Toast(TEXT("SPEED TRAP"), Trap.Name, Kmh, Best);
			if (Kmh > Best)
			{
				Best = Kmh;
				Save();
			}
		}
	}

	// ---- speed zones: average over the zone, gate to gate, either way round
	if (ActiveZone != INDEX_NONE && Now - ZoneStart > Zones[ActiveZone].LengthM * 100.0f / ZoneMinAvgCmS)
	{
		ActiveZone = INDEX_NONE;   // stopped or wandered off
	}
	for (int32 i = 0; i < Zones.Num(); ++i)
	{
		const FZone& Z = Zones[i];
		for (const FVector& G : {Z.Line[0], Z.Line.Last()})
		{
			if (FVector::DistSquared2D(Pos, G) < FMath::Square(MarkerRangeCm))
			{
				DrawDebugCylinder(GetWorld(), G - FVector(0, 0, 100), G + FVector(0, 0, 700),
					600.0f, 20, ZoneCol.ToFColor(true), false, -1.0f, 0, 10.0f);
			}
		}
		double T0 = 0.0, T1 = 0.0;
		const int32 C0 = GateCross(PrevPos, Pos, Z.Line[0], Z.StartDir, GateHalfWidthCm, T0);
		const int32 C1 = GateCross(PrevPos, Pos, Z.Line.Last(), Z.EndDir, GateHalfWidthCm, T1);
		if (ActiveZone == i)
		{
			const int32 Finish = ZoneDir > 0 ? C1 : -C0;
			const int32 Back = ZoneDir > 0 ? C0 : -C1;
			if (Finish > 0)
			{
				const double Elapsed = PrevTime + (ZoneDir > 0 ? T1 : T0) * FrameDt - ZoneStart;
				const float Kmh = Z.LengthM / float(Elapsed) * 3.6f;
				float& Best = Progress->ZoneBestKmh.FindOrAdd(Z.Name);
				Toast(TEXT("SPEED ZONE"), Z.Name, Kmh, Best);
				if (Kmh > Best)
				{
					Best = Kmh;
					Save();
				}
				ActiveZone = INDEX_NONE;
			}
			else if (Back < 0)
			{
				ActiveZone = INDEX_NONE;   // turned back out of the start
			}
		}
		else if (C0 > 0 || C1 < 0)
		{
			ActiveZone = i;
			ZoneDir = C0 > 0 ? 1 : -1;
			ZoneStart = PrevTime + (C0 > 0 ? T0 : T1) * FrameDt;
			Toast(TEXT("SPEED ZONE"), Z.Name + TEXT(" - go!"));
		}
	}

	// ---- landmarks: found on getting close
	for (const FLandmark& L : Landmarks)
	{
		if (FVector2D::DistSquared(FVector2D(Pos), L.Pos) < FMath::Square(L.RadiusCm + DiscoverPadCm)
			&& !Progress->Discovered.Contains(L.Name))
		{
			Progress->Discovered.Add(L.Name);
			Save();
			const int32 Found = Landmarks.FilterByPredicate(
				[&](const FLandmark& O) { return Progress->Discovered.Contains(O.Name); }).Num();
			Toast(TEXT("LANDMARK DISCOVERED"), FString::Printf(TEXT("%s   %d / %d"), *L.Name, Found, Landmarks.Num()));
		}
	}
}

// ---- HUD

void AODChallengeManager::DrawToast(UCanvas* C, float S, bool bMetric) const
{
	const double Age = GetWorld()->GetTimeSeconds() - ToastTime;
	if (Age < 0.0 || Age > ToastSeconds)
	{
		return;
	}
	const float A = FMath::Clamp(float(FMath::Min(Age, ToastSeconds - Age)) / 0.25f, 0.0f, 1.0f);
	const bool bResult = ToastKmh > 0.0f;
	const float W = 520.0f * S, H = (bResult ? 104.0f : 70.0f) * S;
	const float X = (C->SizeX - W) * 0.5f, Y = 110.0f * S;
	FillRect(C, X, Y, W, H, ODHud::Ink.CopyWithNewOpacity(0.72f * A));
	FillRect(C, X, Y, W, 3.0f * S, ODHud::Accent.CopyWithNewOpacity(A));
	const float Mid = C->SizeX * 0.5f;
	Label(C, ToastTitle, Mid, Y + 20.0f * S, 16.0f * S, EMenuWeight::SemiBold, ODHud::Accent.CopyWithNewOpacity(A), true);
	Label(C, ToastDetail, Mid, Y + 46.0f * S, 22.0f * S, EMenuWeight::SemiBold, ODHud::TextCol.CopyWithNewOpacity(A), true);
	if (bResult)
	{
		const bool bNew = ToastKmh > ToastBestKmh;
		const FString Line = SpeedText(ToastKmh, bMetric) + (bNew
			? (ToastBestKmh > 0.0f ? TEXT("   NEW BEST (was ") + SpeedText(ToastBestKmh, bMetric) + TEXT(")") : FString(TEXT("   NEW BEST")))
			: TEXT("   best ") + SpeedText(ToastBestKmh, bMetric));
		Label(C, Line, Mid, Y + 78.0f * S, 28.0f * S, EMenuWeight::Bold,
			(bNew ? ODHud::Accent : ODHud::TextCol).CopyWithNewOpacity(A), true);
	}
}

void AODChallengeManager::DrawMapIcons(UCanvas* C, float S, TFunctionRef<FVector2D(const FVector&)> ToMap,
	TFunctionRef<bool(const FVector2D&)> Visible) const
{
	for (const FZone& Z : Zones)
	{
		for (int32 i = 1; i < Z.Line.Num(); ++i)
		{
			const FVector2D A = ToMap(Z.Line[i - 1]), B = ToMap(Z.Line[i]);
			if (Visible(A) && Visible(B))
			{
				FCanvasLineItem Seg(A, B);
				Seg.SetColor(ZoneCol.CopyWithNewOpacity(0.9f));
				Seg.LineThickness = 3.0f * S;
				C->DrawItem(Seg);
			}
		}
	}
	for (const FTrap& Trap : Traps)
	{
		const FVector2D M = ToMap(Trap.Pos);
		if (Visible(M))
		{
			const float R = 6.0f * S;
			const FVector2D P[] = {M + FVector2D(0, -R), M + FVector2D(R, 0), M + FVector2D(0, R), M + FVector2D(-R, 0)};
			ODHudDraw::Poly(C, P, TrapCol);
		}
	}
	// hollow until discovered
	for (const FLandmark& L : Landmarks)
	{
		const FVector2D M = ToMap(FVector(L.Pos, 0.0));
		if (!Visible(M))
		{
			continue;
		}
		const bool bFound = Progress && Progress->Discovered.Contains(L.Name);
		ODHudDraw::Ring(C, M, bFound ? 0.0f : 4.0f * S, 7.0f * S, 0.0f, 2.0f * PI,
			[](float, float) { return ODHud::Accent; });
	}
}

void AODChallengeManager::DrawChecklist(UCanvas* C, float S, float X, float Y, float W, float MaxH, bool bMetric) const
{
	if (Landmarks.IsEmpty() || !Progress)
	{
		return;
	}
	const float LineH = 22.0f * S;
	const APawn* Pawn = UGameplayStatics::GetPlayerPawn(this, 0);
	int32 Found = 0;
	for (const FLandmark& L : Landmarks)
	{
		Found += Progress->Discovered.Contains(L.Name) ? 1 : 0;
	}
	Label(C, FString::Printf(TEXT("LANDMARKS  %d / %d"), Found, Landmarks.Num()), X, Y + LineH * 0.5f,
		16.0f * S, EMenuWeight::SemiBold, ODHud::DimCol);
	const int32 Rows = FMath::Min(Landmarks.Num(), int32(MaxH / LineH) - 1);
	for (int32 i = 0; i < Rows; ++i)
	{
		const FLandmark& L = Landmarks[i];
		const float Ry = Y + LineH * (i + 1.5f);
		if (Progress->Discovered.Contains(L.Name))
		{
			Label(C, L.Name, X, Ry, 18.0f * S, EMenuWeight::SemiBold, ODHud::TextCol);
			continue;
		}
		FString Where;
		if (Pawn)
		{
			// world X = east, Y = south
			const FVector2D D = L.Pos - FVector2D(Pawn->GetActorLocation());
			static const TCHAR* const Dirs[] = {TEXT("N"), TEXT("NE"), TEXT("E"), TEXT("SE"), TEXT("S"), TEXT("SW"), TEXT("W"), TEXT("NW")};
			const int32 Dir = FMath::RoundToInt(FMath::RadiansToDegrees(FMath::Atan2(D.X, -D.Y)) / 45.0f + 8.0f) % 8;
			const float Km = D.Size() / 100000.0f;
			Where = FString::Printf(TEXT("%.1f %s %s"), bMetric ? Km : Km * 0.621371f, bMetric ? TEXT("km") : TEXT("mi"), Dirs[Dir]);
		}
		Label(C, TEXT("???"), X, Ry, 18.0f * S, EMenuWeight::SemiBold, ODHud::DimCol);
		Label(C, Where, X + W * 0.5f, Ry, 18.0f * S, EMenuWeight::Medium, ODHud::DimCol);
	}
}

bool AODChallengeManager::LandmarkNear(const FVector2D& World, float RadiusCm, FVector2D& OutPos, FString& OutName) const
{
	for (const FLandmark& L : Landmarks)
	{
		if (FVector2D::DistSquared(World, L.Pos) < RadiusCm * RadiusCm)
		{
			OutPos = L.Pos;
			OutName = L.Name;
			return true;
		}
	}
	return false;
}

// ---- headless self-check: Automation RunTests OpenDrive.Challenges.TrapCrossing

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FODChallengeCrossing, "OpenDrive.Challenges.TrapCrossing",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext | EAutomationTestFlags::ProductFilter)

bool FODChallengeCrossing::RunTest(const FString& Parameters)
{
	// a car on +X from x = -200 m, 20 m/s, accelerating 3 m/s^2; trap at x = 0
	const double V0 = 2000.0, Acc = 300.0, X0 = -20000.0;
	auto PosAt = [&](double t) { return FVector(X0 + V0 * t + 0.5 * Acc * t * t, 0.0, 50.0); };
	auto SpeedAt = [&](double t) { return V0 + Acc * t; };
	const double Want = FMath::Sqrt(V0 * V0 + 2.0 * Acc * -X0) * 0.036;   // km/h at the gate
	FRandomStream Rng(42);
	const double Steps[] = {1.0 / 20.0, 1.0 / 30.0, 1.0 / 60.0, 1.0 / 144.0, -1.0};   // -1: jittered 20-200 fps
	for (const double Step : Steps)
	{
		double t = 0.0, Got = -1.0, Naive = -1.0;
		while (t < 30.0 && Got < 0.0)
		{
			const double Dt = Step > 0.0 ? Step : Rng.FRandRange(1.0 / 200.0, 1.0 / 20.0);
			double T = 0.0;
			if (AODChallengeManager::GateCross(PosAt(t), PosAt(t + Dt), FVector::ZeroVector, FVector2D(1, 0),
					GateHalfWidthCm, T) == 1)
			{
				Got = FMath::Lerp(SpeedAt(t), SpeedAt(t + Dt), T) * 0.036;
				Naive = SpeedAt(t + Dt) * 0.036;   // frame-stamped, what sub-frame timing avoids
			}
			t += Dt;
		}
		AddInfo(FString::Printf(TEXT("step %s: %.3f km/h (want %.3f, frame-stamped %.3f)"),
			Step > 0.0 ? *FString::Printf(TEXT("1/%.0f"), 1.0 / Step) : TEXT("jitter"), Got, Want, Naive));
		TestTrue(TEXT("trap speed within 0.05 km/h at any frame rate"), FMath::Abs(Got - Want) < 0.05);
	}

	double T = 0.0;
	const FVector A(-100, 0, 50), B(100, 0, 50);
	TestEqual(TEXT("reverse crossing is -1"), AODChallengeManager::GateCross(B, A, FVector::ZeroVector, FVector2D(1, 0), 1500.0f, T), -1);
	TestEqual(TEXT("20 m to the side doesn't count"),
		AODChallengeManager::GateCross(A + FVector(0, 2000, 0), B + FVector(0, 2000, 0), FVector::ZeroVector, FVector2D(1, 0), 1500.0f, T), 0);
	TestEqual(TEXT("an overpass 15 m up doesn't count"),
		AODChallengeManager::GateCross(A + FVector(0, 0, 1500), B + FVector(0, 0, 1500), FVector::ZeroVector, FVector2D(1, 0), 1500.0f, T), 0);
	TestEqual(TEXT("no crossing, no hit"), AODChallengeManager::GateCross(A, A * 0.5, FVector::ZeroVector, FVector2D(1, 0), 1500.0f, T), 0);

	// zone: 1 km gate to gate at a steady 25 m/s = 40 s / 90 km/h at any frame rate
	for (const double Step : {1.0 / 24.0, 1.0 / 61.0, 1.0 / 165.0})
	{
		auto P = [](double t) { return FVector(-5000.0 + 2500.0 * t, 0.0, 50.0); };
		double t = 0.0, Start = -1.0, End = -1.0;
		while (t < 60.0 && End < 0.0)
		{
			if (AODChallengeManager::GateCross(P(t), P(t + Step), FVector(0, 0, 0), FVector2D(1, 0), 1500.0f, T) == 1)
			{
				Start = t + T * Step;
			}
			if (AODChallengeManager::GateCross(P(t), P(t + Step), FVector(100000, 0, 0), FVector2D(1, 0), 1500.0f, T) == 1)
			{
				End = t + T * Step;
			}
			t += Step;
		}
		const double Kmh = 1000.0 / (End - Start) * 3.6;
		AddInfo(FString::Printf(TEXT("zone at 1/%.0f: %.4f km/h"), 1.0 / Step, Kmh));
		TestTrue(TEXT("zone average 90 km/h"), FMath::Abs(Kmh - 90.0) < 1e-3);
	}
	return true;
}
