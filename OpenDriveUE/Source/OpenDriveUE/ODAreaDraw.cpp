#include "ODAreaDraw.h"

#include "Containers/Ticker.h"
#include "Dom/JsonObject.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Framework/Application/SlateApplication.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/AutomationTest.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Widgets/Input/SEditableTextBox.h"
#include "Widgets/Layout/SBox.h"
#include "ODHudStyle.h"

// ---- geometry (pure) -------------------------------------------------------

namespace
{
constexpr double KmPerDegLat = 110.574, KmPerDegLon = 111.320;

// local km plane around a shape's mean point
struct FKmPlane
{
	double Lat0 = 0.0, Lon0 = 0.0, Cos = 1.0;
	explicit FKmPlane(const TArray<FVector2D>& LL)
	{
		for (const FVector2D& P : LL)
		{
			Lon0 += P.X / LL.Num();
			Lat0 += P.Y / LL.Num();
		}
		Cos = FMath::Cos(FMath::DegreesToRadians(Lat0));
	}
	FVector2D Km(const FVector2D& P) const { return FVector2D((P.X - Lon0) * KmPerDegLon * Cos, (P.Y - Lat0) * KmPerDegLat); }
};

double Cross(const FVector2D& O, const FVector2D& A, const FVector2D& B)
{
	return (A.X - O.X) * (B.Y - O.Y) - (A.Y - O.Y) * (B.X - O.X);
}

// proper crossing only (shared endpoints don't count)
bool SegmentsCross(const FVector2D& A, const FVector2D& B, const FVector2D& C, const FVector2D& D)
{
	const double D1 = Cross(C, D, A), D2 = Cross(C, D, B), D3 = Cross(A, B, C), D4 = Cross(A, B, D);
	return ((D1 > 0) != (D2 > 0)) && ((D3 > 0) != (D4 > 0)) && D1 != 0 && D2 != 0 && D3 != 0 && D4 != 0;
}
}

namespace ODAreaGeo
{
double Km2(const TArray<FVector2D>& Poly)
{
	if (Poly.Num() < 3)
	{
		return 0.0;
	}
	const FKmPlane Pl(Poly);
	double A = 0.0;
	for (int32 i = 0; i < Poly.Num(); ++i)
	{
		A += FVector2D::CrossProduct(Pl.Km(Poly[i]), Pl.Km(Poly[(i + 1) % Poly.Num()]));
	}
	return FMath::Abs(A) * 0.5;
}

FVector2D SidesKm(const TArray<FVector2D>& LL, double PadKm)
{
	if (LL.IsEmpty())
	{
		return FVector2D::ZeroVector;
	}
	const FKmPlane Pl(LL);
	FBox2D Box(ForceInit);
	for (const FVector2D& P : LL)
	{
		Box += Pl.Km(P);
	}
	return Box.GetSize() + FVector2D(2.0 * PadKm);
}

bool InUS(const FVector2D& LL)
{
	// ponytail: lon/lat boxes (a sliver of Canada/Mexico passes); the runner's validation is the real gate
	auto In = [&](double W, double E, double S, double N) { return LL.X >= W && LL.X <= E && LL.Y >= S && LL.Y <= N; };
	return In(-125.0, -66.9, 24.4, 49.4) || In(-170.0, -129.9, 51.0, 71.5) || In(-160.5, -154.5, 18.8, 22.4);
}

bool Inside(const FVector2D& P, const TArray<FVector2D>& Poly)
{
	bool bIn = false;
	for (int32 i = 0, j = Poly.Num() - 1; i < Poly.Num(); j = i++)
	{
		if ((Poly[i].Y > P.Y) != (Poly[j].Y > P.Y)
			&& P.X < (Poly[j].X - Poly[i].X) * (P.Y - Poly[i].Y) / (Poly[j].Y - Poly[i].Y) + Poly[i].X)
		{
			bIn = !bIn;
		}
	}
	return bIn;
}

bool SelfIntersects(const TArray<FVector2D>& Poly)
{
	const int32 N = Poly.Num();
	for (int32 i = 0; i < N; ++i)
	{
		for (int32 j = i + 2; j < N; ++j)
		{
			if ((j + 1) % N == i)
			{
				continue;   // adjacent through the closing edge
			}
			if (SegmentsCross(Poly[i], Poly[(i + 1) % N], Poly[j], Poly[(j + 1) % N]))
			{
				return true;
			}
		}
	}
	return false;
}

double DistKm(const FVector2D& P, const TArray<FVector2D>& Line)
{
	if (Line.IsEmpty())
	{
		return TNumericLimits<double>::Max();
	}
	const FKmPlane Pl(Line);
	const FVector2D Q = Pl.Km(P);
	double Best = FVector2D::Distance(Q, Pl.Km(Line[0]));
	for (int32 i = 1; i < Line.Num(); ++i)
	{
		const FVector2D A = Pl.Km(Line[i - 1]), B = Pl.Km(Line[i]);
		const double L2 = FVector2D::DistSquared(A, B);
		const double T = L2 > 0.0 ? FMath::Clamp(FVector2D::DotProduct(Q - A, B - A) / L2, 0.0, 1.0) : 0.0;
		Best = FMath::Min(Best, FVector2D::Distance(Q, A + (B - A) * T));
	}
	return Best;
}

double CorridorKm2(const TArray<FVector2D>& Line, double HalfKm)
{
	// ponytail: length x width + the round ends; ignores the overlap at bends (over-estimates a little)
	if (Line.Num() < 2)
	{
		return 0.0;
	}
	const FKmPlane Pl(Line);
	double Len = 0.0;
	for (int32 i = 1; i < Line.Num(); ++i)
	{
		Len += FVector2D::Distance(Pl.Km(Line[i - 1]), Pl.Km(Line[i]));
	}
	return Len * 2.0 * HalfKm + PI * HalfKm * HalfKm;
}

FEstimate Estimate(double Km2)
{
	FEstimate E;
	E.Km2 = Km2;
	E.FirstDriveS = 300.0 + 0.5 * Km2 + 150.0;
	E.DoneS = E.FirstDriveS + 30.0 * Km2;   // bake_request.DONE_S_PER_KM2
	E.DiskMb = 50.0 + 1.0 * Km2;
	return E;
}

FString Check(const TArray<FVector2D>& LL, bool bCorridor)
{
	if (LL.Num() > MaxVerts)
	{
		return FString::Printf(TEXT("Too many points: %d (max %d)"), LL.Num(), MaxVerts);
	}
	if (LL.ContainsByPredicate([](const FVector2D& P) { return !InUS(P); }))
	{
		return TEXT("Outside the US: areas must be in the US for now");
	}
	if (LL.Num() < (bCorridor ? 2 : 3))
	{
		return FString();
	}
	const FVector2D S = SidesKm(LL, bCorridor ? CorridorHalfKm : 0.0);
	if (S.GetMax() > MaxSideKm)
	{
		return FString::Printf(TEXT("Too big: %.1f km across (max %.0f km a side)"), S.GetMax(), MaxSideKm);
	}
	if (S.GetMin() < MinSideKm)
	{
		return FString::Printf(TEXT("Too small: %.1f km across (min %.0f km a side)"), S.GetMin(), MinSideKm);
	}
	if (!bCorridor && SelfIntersects(LL))
	{
		return TEXT("Edges cross: drag a corner to untangle them");
	}
	return FString();
}

FVector2D LccInverse(double East, double North, const double Zone[4])
{
	// Snyder 1987 pp. 107-109, as pipeline/world_frame.py
	constexpr double A = 6378137.0, E2 = 0.00669437999014;
	const double E = FMath::Sqrt(E2);
	auto M = [&](double Phi) { return FMath::Cos(Phi) / FMath::Sqrt(1.0 - FMath::Square(E * FMath::Sin(Phi))); };
	auto T = [&](double Phi)
	{
		const double Es = E * FMath::Sin(Phi);
		return FMath::Tan(PI / 4 - Phi / 2) / FMath::Pow((1.0 - Es) / (1.0 + Es), E / 2);
	};
	const double P1 = FMath::DegreesToRadians(Zone[0]), P2 = FMath::DegreesToRadians(Zone[1]), P0 = FMath::DegreesToRadians(Zone[2]);
	const double N = FMath::Abs(P1 - P2) < 1e-12 ? FMath::Sin(P1)
		: (FMath::Loge(M(P1)) - FMath::Loge(M(P2))) / (FMath::Loge(T(P1)) - FMath::Loge(T(P2)));
	const double AF = A * M(P1) / (N * FMath::Pow(T(P1), N));
	const double Rho0 = AF * FMath::Pow(T(P0), N);
	const double Sg = N < 0.0 ? -1.0 : 1.0;
	const double Dy = Rho0 - North;
	const double Rho = Sg * FMath::Sqrt(East * East + Dy * Dy);
	const double Tt = FMath::Pow(Rho / AF, 1.0 / N);
	double Phi = PI / 2 - 2 * FMath::Atan(Tt);
	for (int32 k = 0; k < 15; ++k)
	{
		const double Es = E * FMath::Sin(Phi);
		Phi = PI / 2 - 2 * FMath::Atan(Tt * FMath::Pow((1.0 - Es) / (1.0 + Es), E / 2));
	}
	return FVector2D(Zone[3] + FMath::RadiansToDegrees(FMath::Atan2(Sg * East, Sg * Dy) / N), FMath::RadiansToDegrees(Phi));
}
}

// OpenDrive.AreaDraw.Geo (2026-10-21): the menu's limits and estimate on known shapes.
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FODAreaGeoCheck, "OpenDrive.AreaDraw.Geo",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext | EAutomationTestFlags::ProductFilter)

bool FODAreaGeoCheck::RunTest(const FString& Parameters)
{
	using namespace ODAreaGeo;
	// a 10 km square near Gatlinburg
	const double Lat = 35.7, DLat = 10.0 / KmPerDegLat, DLon = 10.0 / (KmPerDegLon * FMath::Cos(FMath::DegreesToRadians(Lat + DLat / 2)));
	const TArray<FVector2D> Sq = {{-83.5, Lat}, {-83.5 + DLon, Lat}, {-83.5 + DLon, Lat + DLat}, {-83.5, Lat + DLat}};
	TestEqual(TEXT("square km2"), Km2(Sq), 100.0, 1.0);
	TestEqual(TEXT("square sides"), SidesKm(Sq).X, 10.0, 0.2);
	TestTrue(TEXT("centre inside"), Inside({-83.5 + DLon / 2, Lat + DLat / 2}, Sq));
	TestFalse(TEXT("outside"), Inside({-83.4 + DLon, Lat}, Sq));
	TestFalse(TEXT("square simple"), SelfIntersects(Sq));
	TestTrue(TEXT("bowtie crosses"), SelfIntersects({Sq[0], Sq[2], Sq[1], Sq[3]}));
	TestTrue(TEXT("square OK"), Check(Sq, false).IsEmpty());
	TestFalse(TEXT("bowtie refused"), Check({Sq[0], Sq[2], Sq[1], Sq[3]}, false).IsEmpty());
	TArray<FVector2D> Big = Sq;
	for (FVector2D& P : Big)
	{
		P = Sq[0] + (P - Sq[0]) * 4.0;   // 40 km
	}
	TestFalse(TEXT("40 km refused"), Check(Big, false).IsEmpty());
	TestFalse(TEXT("London refused"), Check({{-0.1, 51.5}, {0.0, 51.5}, {0.0, 51.6}}, false).IsEmpty());
	TestTrue(TEXT("Honolulu, Anchorage in"), InUS({-157.86, 21.31}) && InUS({-149.9, 61.2}));
	TestEqual(TEXT("corridor 10 km"), CorridorKm2({Sq[0], Sq[1]}), 20.0 + PI, 0.3);
	TestEqual(TEXT("dist to line"), DistKm(Sq[3], {Sq[0], Sq[1]}), 10.0, 0.2);
	TestTrue(TEXT("corridor sides padded"), Check({Sq[0], Sq[1]}, true).IsEmpty());
	const FEstimate E = Estimate(100.0);
	TestEqual(TEXT("first drive"), E.FirstDriveS, 500.0);
	TestEqual(TEXT("done"), E.DoneS, 3500.0);
	TestEqual(TEXT("disk"), E.DiskMb, 150.0);
	const double Cal[4] = {34.0, 40.5, 37.25, -120.0};
	const FVector2D O = LccInverse(0.0, 0.0, Cal);
	TestEqual(TEXT("lcc origin"), O.X, -120.0, 1e-9);
	TestEqual(TEXT("lcc origin lat"), O.Y, 37.25, 1e-9);
	TestEqual(TEXT("lcc 10 km north"), LccInverse(0.0, 10000.0, Cal).Y - 37.25, 10.0 / KmPerDegLat, 2e-3);
	return true;
}

// ---- files -----------------------------------------------------------------

namespace
{
const TCHAR* DraftSection = TEXT("OpenDrive.AreaDraft");

// the repo root (contract: FPaths::ProjectDir() / ".."), where pipeline/ and data/ live
FString Root()
{
	FString R = FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT(".."));
	FPaths::CollapseRelativeDirectories(R);
	return R;
}
FString BakeDir(const FString& Key) { return Root() / TEXT("data/bake") / Key; }
FString MasksDir() { return FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks"); }

TSharedPtr<FJsonObject> ReadJson(const FString& Path)
{
	FString Text;
	TSharedPtr<FJsonObject> J;
	if (!FFileHelper::LoadFileToString(Text, *Path) || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), J))
	{
		return nullptr;
	}
	return J;
}

bool ReadLL(const TSharedPtr<FJsonValue>& V, FVector2D& Out)
{
	const TArray<TSharedPtr<FJsonValue>>* A = nullptr;
	if (!V.IsValid() || !V->TryGetArray(A) || A->Num() < 2)
	{
		return false;
	}
	Out = FVector2D((*A)[0]->AsNumber(), (*A)[1]->AsNumber());
	return true;
}

TArray<FVector2D> ReadRing(const TSharedPtr<FJsonObject>& O, const TCHAR* Field)
{
	TArray<FVector2D> Out;
	const TArray<TSharedPtr<FJsonValue>>* A = nullptr;
	if (O.IsValid() && O->TryGetArrayField(Field, A))
	{
		for (const TSharedPtr<FJsonValue>& V : *A)
		{
			FVector2D P;
			if (ReadLL(V, P))
			{
				Out.Add(P);
			}
		}
	}
	return Out;
}

TSharedRef<FJsonValue> LLValue(const FVector2D& P)
{
	return MakeShared<FJsonValueArray>(TArray<TSharedPtr<FJsonValue>>{MakeShared<FJsonValueNumber>(P.X), MakeShared<FJsonValueNumber>(P.Y)});
}

// contract: write *.tmp, then rename
bool WriteAtomic(const FString& Path, const FString& Text)
{
	const FString Tmp = Path + TEXT(".tmp");
	return FFileHelper::SaveStringToFile(Text, *Tmp, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)
		&& IFileManager::Get().Move(*Path, *Tmp, true);
}

bool Changed(const FString& Path, FDateTime& Stamp)
{
	const FDateTime T = IFileManager::Get().GetTimeStamp(*Path);   // MinValue when missing
	if (T == Stamp)
	{
		return false;
	}
	Stamp = T;
	return true;
}

FString Duration(double S)
{
	const int32 Min = FMath::CeilToInt(S / 60.0);
	return Min < 60 ? FString::Printf(TEXT("%d min"), FMath::Max(1, Min)) : FString::Printf(TEXT("%d h %02d min"), Min / 60, Min % 60);
}

FString LLString(const TArray<FVector2D>& L)
{
	TArray<FString> Parts;
	for (const FVector2D& P : L)
	{
		Parts.Add(FString::Printf(TEXT("%.6f,%.6f"), P.X, P.Y));
	}
	return FString::Join(Parts, TEXT(";"));
}

TArray<FVector2D> ParseLL(const FString& S)
{
	TArray<FVector2D> Out;
	TArray<FString> Parts;
	S.ParseIntoArray(Parts, TEXT(";"));
	for (const FString& P : Parts)
	{
		FString A, B;
		if (P.Split(TEXT(","), &A, &B))
		{
			Out.Add(FVector2D(FCString::Atod(*A), FCString::Atod(*B)));
		}
	}
	return Out;
}

// panel rows: the draft's, then the card's (footers last in each)
enum : int32 { RowTool, RowName, RowClear, RowBake, RowStart, RowCancel, RowRetry, RowDelete, RowDrive };
const FODMenuTab DraftTabs[] = {{TEXT("NEW AREA"), RowTool, 3}};   // footer Bake
const FODMenuTab CardTabs[] = {{TEXT("AREA"), RowStart, 4}};       // footer Drive
}

// ---- draft -----------------------------------------------------------------

void FODAreaDraw::Load()
{
	FString V, S;
	GConfig->GetString(DraftSection, TEXT("Verts"), V, GGameUserSettingsIni);
	Verts = ParseLL(V);
	GConfig->GetBool(DraftSection, TEXT("Closed"), bClosed, GGameUserSettingsIni);
	GConfig->GetBool(DraftSection, TEXT("Corridor"), bCorridor, GGameUserSettingsIni);
	GConfig->GetString(DraftSection, TEXT("Start"), S, GGameUserSettingsIni);
	const TArray<FVector2D> St = ParseLL(S);
	bHasStart = St.Num() == 1;
	Start = bHasStart ? St[0] : FVector2D::ZeroVector;
	GConfig->GetString(DraftSection, TEXT("Name"), Name, GGameUserSettingsIni);
}

void FODAreaDraw::Save() const
{
	GConfig->SetString(DraftSection, TEXT("Verts"), *LLString(Verts), GGameUserSettingsIni);
	GConfig->SetBool(DraftSection, TEXT("Closed"), bClosed, GGameUserSettingsIni);
	GConfig->SetBool(DraftSection, TEXT("Corridor"), bCorridor, GGameUserSettingsIni);
	GConfig->SetString(DraftSection, TEXT("Start"), bHasStart ? *LLString({Start}) : TEXT(""), GGameUserSettingsIni);
	GConfig->SetString(DraftSection, TEXT("Name"), *Name, GGameUserSettingsIni);
	GConfig->Flush(false, GGameUserSettingsIni);
}

void FODAreaDraw::Push()
{
	if (UndoStack.Num() >= 100)
	{
		UndoStack.RemoveAt(0);
	}
	UndoStack.Add({Verts, bClosed, bCorridor, bHasStart, Start});
	bBakeArmed = false;
	Message.Empty();
}

void FODAreaDraw::Undo()
{
	if (!CanEdit() || UndoStack.IsEmpty())
	{
		return;
	}
	const FSnap S = UndoStack.Pop();
	Verts = S.Verts;
	bClosed = S.bClosed;
	bCorridor = S.bCorridor;
	bHasStart = S.bHasStart;
	Start = S.Start;
	bBakeArmed = false;
	Save();
}

void FODAreaDraw::Click(const FVector2D& LL, const FVector2D& ScreenPos, double Now)
{
	if (!CanEdit())
	{
		return;
	}
	const bool bDouble = Now - LastClickT < 0.35 && FVector2D::Distance(ScreenPos, LastClickPos) < 8.0;
	LastClickT = bDouble ? -1.0 : Now;
	LastClickPos = ScreenPos;
	if (!bClosed)
	{
		if (bDouble)
		{
			// the double click's first half already added the last point
			if (Verts.Num() >= (bCorridor ? 2 : 3))
			{
				Push();
				bClosed = true;
				Save();
			}
			return;
		}
		if (Verts.Num() >= ODAreaGeo::MaxVerts)
		{
			Message = FString::Printf(TEXT("At most %d points"), ODAreaGeo::MaxVerts);
			return;
		}
		Push();
		Verts.Add(LL);
		Save();
		return;
	}
	const bool bIn = bCorridor ? ODAreaGeo::DistKm(LL, Verts) <= ODAreaGeo::CorridorHalfKm : ODAreaGeo::Inside(LL, Verts);
	if (!bIn)
	{
		Message = TEXT("The start must be inside the area");
		return;
	}
	Push();
	bHasStart = true;
	Start = LL;
	Save();
}

void FODAreaDraw::ClickVertex(int32 I, const FVector2D& ScreenPos, double Now)
{
	// a double click's second half lands on the vertex its first half added
	const bool bDouble = Now - LastClickT < 0.35 && FVector2D::Distance(ScreenPos, LastClickPos) < 8.0;
	LastClickT = bDouble ? -1.0 : Now;
	LastClickPos = ScreenPos;
	const bool bClose = (!bCorridor && I == 0) || (bDouble && I == Verts.Num() - 1);
	if (CanEdit() && !bClosed && bClose && Verts.Num() >= (bCorridor ? 2 : 3))
	{
		Push();
		bClosed = true;
		Save();
	}
}

void FODAreaDraw::DeleteVertex(int32 I)
{
	if (!CanEdit() || !Verts.IsValidIndex(I))
	{
		return;
	}
	Push();
	Verts.RemoveAt(I);
	bClosed = bClosed && Verts.Num() >= (bCorridor ? 2 : 3);
	Save();
}

FString FODAreaDraw::Problem() const
{
	const int32 Min = bCorridor ? 2 : 3;
	if (Verts.Num() < Min)
	{
		return bCorridor ? TEXT("Click along the road: at least two points") : TEXT("Click to add corners: at least three");
	}
	if (const FString C = ODAreaGeo::Check(Verts, bCorridor); !C.IsEmpty())
	{
		return C;
	}
	if (!bClosed)
	{
		return bCorridor ? TEXT("Double-click to finish the road") : TEXT("Double-click, or click the first corner, to close it");
	}
	if (!bHasStart)
	{
		return TEXT("Click inside the area to place the start");
	}
	if (bCorridor ? ODAreaGeo::DistKm(Start, Verts) > ODAreaGeo::CorridorHalfKm : !ODAreaGeo::Inside(Start, Verts))
	{
		return TEXT("The start is outside the area: click inside to move it");
	}
	return FString();
}

// ---- request + runner -----------------------------------------------------

bool FODAreaDraw::RunRunner(const FString& Args)
{
	// detached, cwd = repo root (contract); "python" from PATH, as the pipeline docs run it
	FProcHandle H = FPlatformProcess::CreateProc(TEXT("python"), *(TEXT("pipeline/bake_request.py ") + Args),
		true, true, true, nullptr, 0, *Root(), nullptr);
	const bool bOk = H.IsValid();
	FPlatformProcess::CloseProc(H);
	UE_LOG(LogTemp, Display, TEXT("ODAreaDraw: bake_request.py %s -> %s"), *Args, bOk ? TEXT("started") : TEXT("FAILED to start"));
	return bOk;
}

bool FODAreaDraw::Send(const FVector2D& StartLL, const FString& Key)
{
	// a re-request (the card's start pick) re-sends the area's stored outline as a polygon
	const bool bNew = Key.IsEmpty();
	const TArray<FVector2D>* Poly = bNew ? &Verts : DrawnPolys.Find(Key);
	if (!Poly)
	{
		Message = TEXT("This area isn't registered yet");
		return false;
	}
	FString K = Key;
	if (bNew)
	{
		// "u_<slug>": the name, else the time; we pick it so we know where status.json will be
		FString Slug;
		for (const TCHAR Ch : Name.ToLower())
		{
			Slug.AppendChar(FChar::IsAlnum(Ch) ? Ch : TEXT('_'));
		}
		while (Slug.ReplaceInline(TEXT("__"), TEXT("_"))) {}
		Slug = Slug.Left(32);
		Slug.TrimCharInline(TEXT('_'), nullptr);
		const FString Base = TEXT("u_") + (Slug.IsEmpty() ? FDateTime::Now().ToString(TEXT("area_%Y%m%d_%H%M%S")) : Slug);
		K = Base;
		for (int32 n = 2; DrawnPolys.Contains(K) || IFileManager::Get().DirectoryExists(*BakeDir(K)); ++n)
		{
			K = FString::Printf(TEXT("%s_%d"), *Base, n);
		}
	}
	const bool bCorr = bNew && bCorridor;
	const FString Nm = bNew ? Name : Status.Name;
	TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
	J->SetStringField(TEXT("key"), K);
	J->SetField(TEXT("name"), Nm.IsEmpty() ? StaticCastSharedRef<FJsonValue>(MakeShared<FJsonValueNull>())
		: StaticCastSharedRef<FJsonValue>(MakeShared<FJsonValueString>(Nm)));
	TArray<TSharedPtr<FJsonValue>> Ring;
	for (const FVector2D& P : *Poly)
	{
		Ring.Add(LLValue(P));
	}
	J->SetField(TEXT("polygon_ll"), bCorr ? StaticCastSharedRef<FJsonValue>(MakeShared<FJsonValueNull>()) : StaticCastSharedRef<FJsonValue>(MakeShared<FJsonValueArray>(Ring)));
	J->SetField(TEXT("corridor_ll"), bCorr ? StaticCastSharedRef<FJsonValue>(MakeShared<FJsonValueArray>(Ring)) : StaticCastSharedRef<FJsonValue>(MakeShared<FJsonValueNull>()));
	J->SetNumberField(TEXT("corridor_half_width_m"), ODAreaGeo::CorridorHalfKm * 1000.0);
	J->SetField(TEXT("start_ll"), LLValue(StartLL));
	FString Text;
	FJsonSerializer::Serialize(J, TJsonWriterFactory<>::Create(&Text));
	const FString Path = Root() / TEXT("data/bake/requests") / K + TEXT(".json");
	IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
	if (!WriteAtomic(Path, Text) || !RunRunner(FString::Printf(TEXT("--run \"%s\""), *Path)))
	{
		Message = TEXT("Couldn't start the bake: is Python on PATH? See the log");
		return false;
	}
	if (bNew)
	{
		// sent: the draft is now an area; its card takes over
		Verts.Reset();
		bClosed = bHasStart = false;
		Name.Empty();
		UndoStack.Reset();
		Save();
		OpenCard(K);
	}
	Message = TEXT("Bake request sent");
	return true;
}

// ---- card ------------------------------------------------------------------

void FODAreaDraw::OpenCard(const FString& Key)
{
	JobKey = Key;
	Status = FStatus();
	RoadsLL.Reset();
	Holes.Reset();
	TownNames.Reset();
	TownLL.Reset();
	CellState.Reset();
	StatusStamp = RoadsStamp = FDateTime();
	NextPoll = 0.0;
	StartChoice = 0;
	bBakeArmed = bDeleteArmed = false;
	Message.Empty();
}

const TArray<FVector2D>* FODAreaDraw::JobOutline() const
{
	if (const FCovArea* A = Coverage.FindByPredicate([&](const FCovArea& C) { return C.Key == JobKey; }); A && A->Outline.Num() >= 3)
	{
		return &A->Outline;
	}
	return DrawnPolys.Find(JobKey);
}

FString FODAreaDraw::Title() const
{
	if (CanEdit())
	{
		return TEXT("New Area");
	}
	if (!Status.Name.IsEmpty())
	{
		return Status.Name;
	}
	const FString* N = DrawnNames.Find(JobKey);
	return N && !N->IsEmpty() ? *N : JobKey;
}

void FODAreaDraw::Poll(double Now)
{
	if (Now < NextPoll)
	{
		return;
	}
	NextPoll = Now + 1.0;
	bool bCov = false;
	if (Changed(Root() / TEXT("data/user_areas.json"), UserAreasStamp))
	{
		LoadUserAreas();
		bCov = true;
	}
	if (Changed(MasksDir() / TEXT("coverage.json"), CoverageStamp) || bCov)
	{
		LoadCoverage();
		StatusStamp = FDateTime();   // re-check the holes against the new tiles
	}
	if (JobKey.IsEmpty())
	{
		return;
	}
	if (Changed(BakeDir(JobKey) / TEXT("status.json"), StatusStamp))
	{
		LoadStatus();
	}
	// pass 1 writes the whole polygon's map centerline first (round 10 #3): show it as soon as it lands
	if (Changed(MasksDir() / FString::Printf(TEXT("centerline_map_%s_test.bin"), *JobKey), RoadsStamp))
	{
		LoadRoads();
	}
}

void FODAreaDraw::LoadUserAreas()
{
	// {"worlds": {"zone_x": [lat1, lat2, lat0, lon0]}, "areas": {"u_<slug>": {"world", "name", "polygon_ll", ...}}}
	Zones.Reset();
	AreaZone.Reset();
	DrawnPolys.Reset();
	DrawnNames.Reset();
	const TSharedPtr<FJsonObject> J = ReadJson(Root() / TEXT("data/user_areas.json"));
	const TSharedPtr<FJsonObject>* W = nullptr;
	const TSharedPtr<FJsonObject>* A = nullptr;
	if (J.IsValid() && J->TryGetObjectField(TEXT("worlds"), W))
	{
		for (const auto& [Zone, V] : (*W)->Values)
		{
			TArray<double>& Z = Zones.Add(FString(Zone));
			for (const TSharedPtr<FJsonValue>& N : V->AsArray())
			{
				Z.Add(N->AsNumber());
			}
		}
	}
	if (J.IsValid() && J->TryGetObjectField(TEXT("areas"), A))
	{
		for (const auto& [Key, V] : (*A)->Values)
		{
			const TSharedPtr<FJsonObject> O = V->AsObject();
			AreaZone.Add(FString(Key), O.IsValid() ? O->GetStringField(TEXT("world")) : FString());
			DrawnPolys.Add(FString(Key), ReadRing(O, TEXT("polygon_ll")));
			DrawnNames.Add(FString(Key), O.IsValid() ? O->GetStringField(TEXT("name")) : FString());
		}
	}
}

void FODAreaDraw::LoadCoverage()
{
	// {"areas": {"<key>": {"name", "drawn", "outline_ll", "tiles": [{"ll": [[lon, lat] x4], "state"}]}}} (build_world_manifest.py)
	Coverage.Reset();
	const TSharedPtr<FJsonObject> J = ReadJson(MasksDir() / TEXT("coverage.json"));
	const TSharedPtr<FJsonObject>* Areas = nullptr;
	if (J.IsValid() && J->TryGetObjectField(TEXT("areas"), Areas))
	{
		for (const auto& [Key, V] : (*Areas)->Values)
		{
			const TSharedPtr<FJsonObject> O = V->AsObject();
			if (!O.IsValid())
			{
				continue;
			}
			FCovArea& C = Coverage.AddDefaulted_GetRef();
			C.Key = Key;
			C.Name = O->GetStringField(TEXT("name"));
			O->TryGetBoolField(TEXT("drawn"), C.bDrawn);
			C.Outline = ReadRing(O, TEXT("outline_ll"));
			const TArray<TSharedPtr<FJsonValue>>* Tiles = nullptr;
			if (O->TryGetArrayField(TEXT("tiles"), Tiles))
			{
				for (const TSharedPtr<FJsonValue>& T : *Tiles)
				{
					const TArray<FVector2D> Q = ReadRing(T->AsObject(), TEXT("ll"));
					if (Q.Num() != 4)
					{
						continue;
					}
					const FString St = T->AsObject()->GetStringField(TEXT("state"));
					const uint8 S = St == TEXT("failed") ? 2 : St == TEXT("pending") ? 1 : 0;
					C.Tiles.Add(Q);
					C.TileState.Add(S);
					C.BakedKm2 += S == 0 ? ODAreaGeo::Km2(Q) : 0.0;
				}
			}
		}
	}
	// a drawn area registered but not in coverage.json yet still gets its outline and pin
	for (const auto& [Key, Poly] : DrawnPolys)
	{
		if (!Coverage.ContainsByPredicate([&](const FCovArea& C) { return C.Key == Key; }))
		{
			FCovArea& C = Coverage.AddDefaulted_GetRef();
			C.Key = Key;
			C.Name = DrawnNames.FindRef(Key);
			C.bDrawn = true;
			C.Outline = Poly;
		}
	}
	for (FCovArea& C : Coverage)
	{
		C.TotalKm2 = ODAreaGeo::Km2(C.Outline);
		for (const FVector2D& P : C.Outline)
		{
			C.Centre += P / C.Outline.Num();
		}
	}
}

void FODAreaDraw::LoadStatus()
{
	const FString Was = Status.State;
	Status = FStatus();
	CellState.Reset();
	const TSharedPtr<FJsonObject> J = ReadJson(BakeDir(JobKey) / TEXT("status.json"));
	if (!J.IsValid())
	{
		Holes.Reset();
		return;
	}
	Status.bValid = true;
	J->TryGetStringField(TEXT("name"), Status.Name);
	J->TryGetStringField(TEXT("state"), Status.State);
	J->TryGetStringField(TEXT("stage"), Status.Stage);
	J->TryGetStringField(TEXT("error"), Status.Error);   // null -> stays empty
	J->TryGetBoolField(TEXT("pass1_done"), Status.bPass1Done);
	J->TryGetBoolField(TEXT("ready_to_drive"), Status.bReady);
	J->TryGetNumberField(TEXT("eta_first_drive_s"), Status.EtaFirstS);
	J->TryGetNumberField(TEXT("eta_done_s"), Status.EtaDoneS);
	Status.bSnapped = ReadLL(J->TryGetField(TEXT("start_snapped_ll")), Status.SnappedLL);
	const TSharedPtr<FJsonObject>* Cells = nullptr;
	if (J->TryGetObjectField(TEXT("cells"), Cells))
	{
		for (const auto& [Cell, V] : (*Cells)->Values)
		{
			const FString S = V->AsString();
			CellState.Add(FString(Cell), S);
			++Status.Cells;
			Status.Baked += S == TEXT("baked");
			Status.Failed += S == TEXT("failed");
		}
	}
	if (Status.bPass1Done && TownNames.IsEmpty())
	{
		LoadTowns();
	}
	if (Was != Status.State)
	{
		bDeleteArmed = false;
	}
	FindHoles();
}

void FODAreaDraw::FindHoles()
{
	// round 9: a cell the status calls baked with no terrain tile on disk is a hole (dashed)
	Holes.Reset();
	const TArray<double>* Z = Zones.Find(AreaZone.FindRef(JobKey));
	const FCovArea* Cov = Coverage.FindByPredicate([&](const FCovArea& C) { return C.Key == JobKey; });
	if (!Z || Z->Num() != 4)
	{
		return;
	}
	TArray<FVector2D> Centres;
	for (int32 i = 0; Cov && i < Cov->Tiles.Num(); ++i)
	{
		const TArray<FVector2D>& T = Cov->Tiles[i];
		Centres.Add((T[0] + T[1] + T[2] + T[3]) * 0.25);
	}
	for (const auto& [Cell, S] : CellState)
	{
		FString I, J;
		if (S != TEXT("baked") || !Cell.Split(TEXT("_"), &I, &J))
		{
			continue;
		}
		const double X = FCString::Atod(*I) * 1000.0, Y = FCString::Atod(*J) * 1000.0;   // cell_m 1000, zone metres
		TArray<FVector2D> Q = {ODAreaGeo::LccInverse(X, Y, Z->GetData()), ODAreaGeo::LccInverse(X + 1000.0, Y, Z->GetData()),
			ODAreaGeo::LccInverse(X + 1000.0, Y + 1000.0, Z->GetData()), ODAreaGeo::LccInverse(X, Y + 1000.0, Z->GetData())};
		if (!Centres.ContainsByPredicate([&](const FVector2D& C) { return ODAreaGeo::Inside(C, Q); }))
		{
			Holes.Add(MoveTemp(Q));
		}
	}
}

void FODAreaDraw::LoadRoads()
{
	// centerline_map_<key>_test.bin, ODM1 (as AODMainMenu::FocusArea): area cm = zone metres x 100, UE y = -north
	RoadsLL.Reset();
	const TArray<double>* Z = Zones.Find(AreaZone.FindRef(JobKey));
	TArray<uint8> Bin;
	if (!Z || Z->Num() != 4
		|| !FFileHelper::LoadFileToArray(Bin, *(MasksDir() / FString::Printf(TEXT("centerline_map_%s_test.bin"), *JobKey)))
		|| Bin.Num() < 8 || FMemory::Memcmp(Bin.GetData(), "ODM1", 4) != 0)
	{
		if (!Z)
		{
			RoadsStamp = FDateTime();   // user_areas.json may not name the zone yet: retry next poll
		}
		return;
	}
	uint32 N = 0;
	FMemory::Memcpy(&N, Bin.GetData() + 4, 4);
	for (int64 i = 0; i < N && 8 + (i + 1) * 8 <= Bin.Num(); ++i)
	{
		float XY[2];
		FMemory::Memcpy(XY, Bin.GetData() + 8 + i * 8, 8);
		RoadsLL.Add(FMath::IsNaN(XY[0]) ? FVector2D(XY[0], XY[1]) : ODAreaGeo::LccInverse(XY[0] / 100.0, -XY[1] / 100.0, Z->GetData()));
	}
}

void FODAreaDraw::LoadTowns()
{
	// data/raw/<key>_test/osm_places.json [{"name", "lat", "lon"}] (fetch_osm_places.py, pass 1)
	FString Text;
	TArray<TSharedPtr<FJsonValue>> Places;
	const TArray<FVector2D>* Outline = JobOutline();
	if (!Outline || !FFileHelper::LoadFileToString(Text, *(Root() / TEXT("data/raw") / JobKey + TEXT("_test/osm_places.json")))
		|| !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Places))
	{
		return;
	}
	for (const TSharedPtr<FJsonValue>& V : Places)
	{
		const TSharedPtr<FJsonObject> O = V->AsObject();
		double Lat = 0.0, Lon = 0.0;
		FString Nm;
		if (O.IsValid() && O->TryGetStringField(TEXT("name"), Nm) && O->TryGetNumberField(TEXT("lat"), Lat)
			&& O->TryGetNumberField(TEXT("lon"), Lon) && ODAreaGeo::Inside(FVector2D(Lon, Lat), *Outline) && TownNames.Num() < 40)
		{
			TownNames.Add(Nm);
			TownLL.Add(FVector2D(Lon, Lat));
		}
	}
}

// ---- name box (Slate, 2026-10-21: the one text field in the menus) ---------

void FODAreaDraw::OpenNameBox()
{
	if (NameBox.IsValid() || !GEngine || !GEngine->GameViewport)
	{
		return;
	}
	TSharedRef<SEditableTextBox> Box = SNew(SEditableTextBox)
		.Text(FText::FromString(Name))
		.HintText(FText::FromString(TEXT("Auto: named after the biggest town")))
		.Font(ODHud::MenuFont(ODHud::EMenuWeight::SemiBold, 22.0f))
		.SelectAllTextWhenFocused(true)
		.RevertTextOnEscape(true)
		.OnTextCommitted_Lambda([this](const FText& T, ETextCommit::Type How)
		{
			if (!NameBox.IsValid())
			{
				return;   // closing: the focus loss commits once more
			}
			if (How != ETextCommit::OnCleared)
			{
				Name = T.ToString().TrimStartAndEnd().Left(48);
				Save();
			}
			CloseNameBox();
		});
	NameBox = SNew(SBox).HAlign(HAlign_Center).VAlign(VAlign_Center)[SNew(SBox).WidthOverride(480.0f)[Box]];
	GEngine->GameViewport->AddViewportWidgetContent(NameBox.ToSharedRef(), 100);
	FSlateApplication::Get().SetKeyboardFocus(Box, EFocusCause::SetDirectly);
}

void FODAreaDraw::CloseNameBox()
{
	if (!NameBox.IsValid())
	{
		return;
	}
	// next tick: this runs inside the box's own commit callback
	FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateLambda([W = NameBox.ToSharedRef()](float)
	{
		if (GEngine && GEngine->GameViewport)
		{
			GEngine->GameViewport->RemoveViewportWidgetContent(W);
		}
		FSlateApplication::Get().SetAllUserFocusToGameViewport();
		return false;
	}));
	NameBox.Reset();
}

// ---- panel -----------------------------------------------------------------

TArrayView<const FODMenuTab> FODAreaDraw::GetTabs() const
{
	return CanEdit() ? TArrayView<const FODMenuTab>(DraftTabs) : TArrayView<const FODMenuTab>(CardTabs);
}

int32 FODAreaDraw::Footer() const
{
	return CanEdit() ? RowBake : RowDrive;
}

void FODAreaDraw::GetRow(int32 Row, FString& OutLabel, FString& OutValue) const
{
	OutValue.Empty();
	switch (Row)
	{
	case RowTool: OutLabel = TEXT("Tool"); OutValue = bCorridor ? TEXT("Road Corridor") : TEXT("Polygon"); break;
	case RowName: OutLabel = TEXT("Name"); OutValue = Name.IsEmpty() ? TEXT("Auto") : Name; break;
	case RowClear: OutLabel = TEXT("Clear Drawing"); break;
	case RowBake: OutLabel = bBakeArmed ? TEXT("Confirm Bake") : TEXT("Bake"); break;
	case RowStart:
		OutLabel = TEXT("Start");
		OutValue = !Status.bPass1Done ? TEXT("After Pass 1") : TownNames.IsValidIndex(StartChoice - 1) ? TownNames[StartChoice - 1]
			: Status.bSnapped ? TEXT("Snapped to Road") : TEXT("As Drawn");
		break;
	case RowCancel: OutLabel = TEXT("Cancel Bake"); break;
	case RowRetry: OutLabel = TEXT("Retry"); break;
	case RowDelete: OutLabel = bDeleteArmed ? TEXT("Confirm Delete") : TEXT("Delete Area"); break;
	case RowDrive: OutLabel = Status.bReady ? TEXT("Drive") : TEXT("Drive (not ready)"); break;
	default: OutLabel.Empty(); break;
	}
}

void FODAreaDraw::Adjust(int32 Row, int32 Dir)
{
	if (Row == RowTool && CanEdit())
	{
		Push();
		bCorridor = !bCorridor;
		Save();
	}
	else if (Row == RowStart && Status.bPass1Done)
	{
		const int32 N = TownNames.Num() + 1;   // 0 = the runner's snapped start
		StartChoice = (StartChoice + Dir + N) % N;
	}
}

FString FODAreaDraw::Activate(int32 Row)
{
	Message.Empty();
	switch (Row)
	{
	case RowTool: Adjust(Row, +1); break;
	case RowName: OpenNameBox(); break;
	case RowClear:
		Push();
		Verts.Reset();
		bClosed = bHasStart = false;
		Save();
		break;
	case RowBake:
		if (!bClosed && Verts.Num() >= (bCorridor ? 2 : 3))
		{
			Push();   // Enter finishes the shape, like a double click
			bClosed = true;
			Save();
		}
		if (const FString P = Problem(); !P.IsEmpty())
		{
			Message = P;
		}
		else if (!bBakeArmed)
		{
			bBakeArmed = true;   // the readout above is the confirm card; Bake again sends it
		}
		else
		{
			bBakeArmed = false;
			Send(Start, FString());
		}
		break;
	case RowStart:
		if (TownNames.IsValidIndex(StartChoice - 1) && Send(TownLL[StartChoice - 1], JobKey))
		{
			Message = FString::Printf(TEXT("Start moved to %s: that cell bakes next"), *TownNames[StartChoice - 1]);
		}
		else if (!TownNames.IsValidIndex(StartChoice - 1))
		{
			Message = Status.bPass1Done ? TEXT("Left / Right: pick a town to start from") : TEXT("Towns arrive with pass 1");
		}
		break;
	case RowCancel:
		// contract: the game creates data/bake/<key>/cancel; the runner stops after its current step
		IFileManager::Get().MakeDirectory(*BakeDir(JobKey), true);
		Message = FFileHelper::SaveStringToFile(FString(), *(BakeDir(JobKey) / TEXT("cancel"))) ? TEXT("Cancelling") : TEXT("Couldn't write the cancel file");
		break;
	case RowRetry:
		Message = RunRunner(TEXT("--retry ") + JobKey) ? TEXT("Retrying") : TEXT("Couldn't start the bake: is Python on PATH?");
		break;
	case RowDelete:
		if (!bDeleteArmed)
		{
			bDeleteArmed = true;
			Message = TEXT("Delete removes this area's baked tiles. Shared downloads stay.");
		}
		else
		{
			// ponytail: --delete is not in the contract yet (round 7 "Delete area"); bake_request.py owns user_areas.json
			bDeleteArmed = false;
			Message = RunRunner(TEXT("--delete ") + JobKey) ? TEXT("Deleting") : TEXT("Couldn't start the delete");
		}
		break;
	case RowDrive:
		if (Status.bReady)
		{
			return JobKey;
		}
		Message = Status.bValid ? FString::Printf(TEXT("Not ready yet: first drive in ~%s"), *Duration(Status.EtaFirstS)) : TEXT("Not ready yet");
		break;
	default: break;
	}
	return FString();
}

void FODAreaDraw::Readout(TArray<FLine>& Out) const
{
	auto Add = [&](const FString& T, uint8 Kind = 0) { Out.Add({T, Kind}); };
	if (CanEdit())
	{
		if (Verts.Num() >= (bCorridor ? 2 : 3))
		{
			const ODAreaGeo::FEstimate E = ODAreaGeo::Estimate(bCorridor ? ODAreaGeo::CorridorKm2(Verts) : ODAreaGeo::Km2(Verts));
			const FVector2D S = ODAreaGeo::SidesKm(Verts, bCorridor ? ODAreaGeo::CorridorHalfKm : 0.0);
			Add(FString::Printf(TEXT("%.1f km²  ·  %.1f × %.1f km"), E.Km2, S.X, S.Y));
			Add(FString::Printf(TEXT("First drive in ~%s  ·  all done in ~%s"), *Duration(E.FirstDriveS), *Duration(E.DoneS)));
			Add(FString::Printf(TEXT("Disk ~%s"), E.DiskMb < 1024.0 ? *FString::Printf(TEXT("%.0f MB"), E.DiskMb)
				: *FString::Printf(TEXT("%.1f GB"), E.DiskMb / 1024.0)));
			// overlap with what's already baked (round 9): drawn tiles are reused, a hand area bakes its own copy
			double Reused = 0.0;
			TArray<FString> Hand;
			for (const FCovArea& C : Coverage)
			{
				for (int32 i = 0; i < C.Tiles.Num(); ++i)
				{
					const TArray<FVector2D>& T = C.Tiles[i];
					const FVector2D Mid = (T[0] + T[1] + T[2] + T[3]) * 0.25;
					const bool bIn = bCorridor ? ODAreaGeo::DistKm(Mid, Verts) <= ODAreaGeo::CorridorHalfKm : ODAreaGeo::Inside(Mid, Verts);
					if (bIn && C.TileState[i] == 0)
					{
						if (C.bDrawn)
						{
							Reused += ODAreaGeo::Km2(T);
						}
						else
						{
							Hand.AddUnique(C.Name.IsEmpty() ? C.Key : C.Name);
						}
					}
				}
			}
			if (Reused > 0.0)
			{
				Add(FString::Printf(TEXT("%.1f km² already baked, will be reused"), Reused), 1);
			}
			for (const FString& H : Hand)
			{
				Add(FString::Printf(TEXT("Overlaps %s: bakes its own copy, most downloads reused"), *H), 1);
			}
		}
		if (const FString P = Problem(); !P.IsEmpty())
		{
			Add(P, 2);
		}
		else if (bBakeArmed)
		{
			Add(TEXT("Bake again to confirm"), 0);
		}
	}
	else if (!Status.bValid)
	{
		Add(TEXT("Waiting for the bake to start..."), 1);
	}
	else
	{
		const FString& S = Status.State;
		Add(S == TEXT("pass1") ? TEXT("Pass 1: ") + Status.Stage : S == TEXT("pass2") ? TEXT("Baking cells: ") + Status.Stage
			: S == TEXT("done") ? FString(TEXT("Done")) : S == TEXT("failed") ? FString(TEXT("Failed"))
			: S == TEXT("cancelled") ? FString(TEXT("Cancelled")) : S == TEXT("paused") ? FString(TEXT("Paused: another area has priority")) : S);
		Add(FString::Printf(TEXT("Cells %d / %d baked%s"), Status.Baked, Status.Cells,
			Status.Failed ? *FString::Printf(TEXT("  ·  %d failed"), Status.Failed) : TEXT("")), Status.Failed ? 2 : 0);
		if (Status.bReady)
		{
			Add(TEXT("Ready to drive"));
		}
		else if (S == TEXT("pass1") || S == TEXT("pass2"))
		{
			Add(FString::Printf(TEXT("First drive in ~%s"), *Duration(Status.EtaFirstS)));
		}
		if (S == TEXT("pass1") || S == TEXT("pass2"))
		{
			Add(FString::Printf(TEXT("All done in ~%s"), *Duration(Status.EtaDoneS)), 1);
		}
		if (const FCovArea* C = Coverage.FindByPredicate([&](const FCovArea& A) { return A.Key == JobKey; }); C && C->TotalKm2 > 0.0)
		{
			Add(FString::Printf(TEXT("%.1f of %.1f km² baked"), C->BakedKm2, C->TotalKm2), 1);
		}
		if (!Holes.IsEmpty())
		{
			Add(FString::Printf(TEXT("%d baked cell(s) missing on disk: Retry"), Holes.Num()), 2);
		}
		if (!Status.Error.IsEmpty())
		{
			Add(Status.Error, 2);
		}
	}
	if (!Message.IsEmpty())
	{
		Add(Message, 1);
	}
}
