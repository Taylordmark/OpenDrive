#include "ODBakeLink.h"

#include "Containers/Ticker.h"
#include "Dom/JsonObject.h"
#include "Engine/Engine.h"
#include "Engine/GameViewportClient.h"
#include "Engine/World.h"
#include "Framework/Application/SlateApplication.h"
#include "Framework/Notifications/NotificationManager.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformApplicationMisc.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "ODVehiclePawn.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Widgets/Notifications/SNotificationList.h"
#include "Widgets/SWindow.h"
#if PLATFORM_WINDOWS
#include "Windows/AllowWindowsPlatformTypes.h"
#include <windows.h>
#include "Windows/HideWindowsPlatformTypes.h"
#endif

const TCHAR* const ODBakeLink::ConfigSection = TEXT("OpenDrive.Bake");
const TCHAR* const ODBakeLink::QuitBehaviourKey = TEXT("BakeQuitBehaviour");

// The one check (2026-10-21): tile -> cell mapping, incl. the Y = -north flip.
// 2 km tile (0,0): east 0..2 km, north -2..0 km
static_assert(ODBakeLink::TileCells(0, 0, 200000).I0 == 0 && ODBakeLink::TileCells(0, 0, 200000).I1 == 1);
static_assert(ODBakeLink::TileCells(0, 0, 200000).J0 == -2 && ODBakeLink::TileCells(0, 0, 200000).J1 == -1);
// 500 m road tile (1,1): east 0.5..1 km, north -1..-0.5 km - one cell
static_assert(ODBakeLink::TileCells(1, 1, 50000).I0 == 0 && ODBakeLink::TileCells(1, 1, 50000).I1 == 0);
static_assert(ODBakeLink::TileCells(1, 1, 50000).J0 == -1 && ODBakeLink::TileCells(1, 1, 50000).J1 == -1);
// 1 km drawn-area terrain tile (3,-2): exactly cell (3, 1)
static_assert(ODBakeLink::TileCells(3, -2, 100000).I0 == 3 && ODBakeLink::TileCells(3, -2, 100000).I1 == 3);
static_assert(ODBakeLink::TileCells(3, -2, 100000).J0 == 1 && ODBakeLink::TileCells(3, -2, 100000).J1 == 1);

namespace
{
	TSharedPtr<FJsonObject> LinkReadJson(const FString& Path)
	{
		FString Text;
		TSharedPtr<FJsonObject> Root;
		if (!FFileHelper::LoadFileToString(Text, *Path)
			|| !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Root))
		{
			return nullptr;
		}
		return Root;
	}

	/** Ellipsoidal Lambert Conformal Conic - pipeline/world_frame.py ported
	 *  1:1 (Snyder pp. 107-109, WGS84). */
	struct FLcc
	{
		double E = 0.0, N = 1.0, AF = 0.0, Rho0 = 0.0, Lon0 = 0.0;

		static double M(double Phi, double E) { return FMath::Cos(Phi) / FMath::Sqrt(1.0 - FMath::Square(E * FMath::Sin(Phi))); }
		static double T(double Phi, double E)
		{
			const double Es = E * FMath::Sin(Phi);
			return FMath::Tan(UE_DOUBLE_PI / 4 - Phi / 2) / FMath::Pow((1.0 - Es) / (1.0 + Es), E / 2);
		}
		FLcc(double Lat1, double Lat2, double Lat0, double InLon0)
		{
			constexpr double A = 6378137.0, E2 = 0.00669437999014;
			E = FMath::Sqrt(E2);
			const double P1 = FMath::DegreesToRadians(Lat1), P2 = FMath::DegreesToRadians(Lat2);
			const double M1 = M(P1, E), M2 = M(P2, E), T1 = T(P1, E), T2 = T(P2, E);
			N = FMath::Abs(P1 - P2) < 1e-12 ? FMath::Sin(P1)
				: (FMath::Loge(M1) - FMath::Loge(M2)) / (FMath::Loge(T1) - FMath::Loge(T2));
			AF = A * M1 / (N * FMath::Pow(T1, N));
			Rho0 = AF * FMath::Pow(T(FMath::DegreesToRadians(Lat0), E), N);
			Lon0 = InLon0;
		}
		/** lon/lat degrees -> UE cm (X east, Y = -north). */
		FVector2D ToCm(double Lon, double Lat) const
		{
			const double Rho = AF * FMath::Pow(T(FMath::DegreesToRadians(Lat), E), N);
			const double Theta = N * FMath::DegreesToRadians(Lon - Lon0);
			return FVector2D(Rho * FMath::Sin(Theta) * 100.0, -(Rho0 - Rho * FMath::Cos(Theta)) * 100.0);
		}
		FVector2D ToCm(const TArray<TSharedPtr<FJsonValue>>& LonLat) const
		{
			return LonLat.Num() >= 2 ? ToCm(LonLat[0]->AsNumber(), LonLat[1]->AsNumber()) : FVector2D::ZeroVector;
		}
	};

	/** One status.json, re-read only when its mtime changes (checked <= 1 Hz). */
	struct FStatus
	{
		double CheckedAt = -1.0e9;
		FDateTime Stamp;
		bool bBaking = false;     // state pass1 / pass2 / paused: tiles still to come
		bool bReady = false;
		bool bHasSnapped = false;
		TArray<double> SnappedLL;
		TMap<FIntPoint, bool> CellPending;   // listed cells -> pending|running
	};

	FCriticalSection GLock;
	TMap<FString, FStatus> GStatus;
	uint32 GVersion = 0;
	// drawn keys from user_areas.json, longest first (IsTilePending's prefix match)
	TArray<FString> GDrawnKeys;
	TMap<FString, float> GDrawnTerrainTileCm;
	double GKeysCheckedAt = -1.0e9;
	FDateTime GKeysStamp;
	// active area (game thread writes, any thread reads under GLock)
	FString GActiveKey;
	TSharedPtr<const TArray<FVector2D>, ESPMode::ThreadSafe> GOutline;
	TOptional<FVector2D> GSnappedCm;
	TSharedPtr<FLcc> GActiveLcc;
	// heartbeat (game thread only)
	FVector2D GLastPos = FVector2D::ZeroVector;
	float GLastHeading = 0.0f;
	TOptional<FVector2D> GTarget;
	TMap<FString, bool> GReadySeen;   // ready watcher: key -> last ready_to_drive

	FString StatusPath(const FString& Key)
	{
		return ODBakeLink::RepoRoot() / TEXT("data/bake") / Key / TEXT("status.json");
	}

	/** GLock held. Refreshes Key's status when its file changed. */
	FStatus& StatusLocked(const FString& Key)
	{
		FStatus& S = GStatus.FindOrAdd(Key);
		const double Now = FPlatformTime::Seconds();
		if (Now - S.CheckedAt < 1.0)
		{
			return S;
		}
		S.CheckedAt = Now;
		const FString Path = StatusPath(Key);
		const FDateTime Stamp = IFileManager::Get().GetTimeStamp(*Path);
		if (Stamp == S.Stamp)
		{
			return S;
		}
		S.Stamp = Stamp;
		const TSharedPtr<FJsonObject> Root = Stamp == FDateTime::MinValue() ? nullptr : LinkReadJson(Path);
		if (!Root.IsValid())
		{
			// half-written can't happen (atomic rename); gone = not baking
			S = FStatus();
			S.CheckedAt = Now;
			S.Stamp = Stamp;
			++GVersion;
			return S;
		}
		const FString State = Root->GetStringField(TEXT("state"));
		S.bBaking = State == TEXT("pass1") || State == TEXT("pass2") || State == TEXT("paused");
		S.bReady = Root->GetBoolField(TEXT("ready_to_drive"));
		S.SnappedLL.Reset();
		const TArray<TSharedPtr<FJsonValue>>* LL = nullptr;
		S.bHasSnapped = Root->TryGetArrayField(TEXT("start_snapped_ll"), LL) && LL->Num() >= 2;
		if (S.bHasSnapped)
		{
			S.SnappedLL = { (*LL)[0]->AsNumber(), (*LL)[1]->AsNumber() };
		}
		S.CellPending.Reset();
		const TSharedPtr<FJsonObject>* Cells = nullptr;
		if (Root->TryGetObjectField(TEXT("cells"), Cells))
		{
			for (const TPair<FString, TSharedPtr<FJsonValue>>& C : (*Cells)->Values)
			{
				FString I, J;
				if (C.Key.Split(TEXT("_"), &I, &J))
				{
					const FString V = C.Value->AsString();
					S.CellPending.Add(FIntPoint(FCString::Atoi(*I), FCString::Atoi(*J)),
						V == TEXT("pending") || V == TEXT("running"));
				}
			}
		}
		if (Key == GActiveKey && GActiveLcc.IsValid() && S.bHasSnapped)
		{
			GSnappedCm = GActiveLcc->ToCm(S.SnappedLL[0], S.SnappedLL[1]);
		}
		++GVersion;
		return S;
	}

	/** GLock held: Cell is still to come. Pass 1 before the cell map exists
	 *  = everything is still to come. */
	bool CellPendingLocked(const FStatus& S, int64 I, int64 J)
	{
		if (!S.bBaking)
		{
			return false;
		}
		if (S.CellPending.Num() == 0)
		{
			return true;
		}
		const bool* P = S.CellPending.Find(FIntPoint((int32)I, (int32)J));
		return P && *P;   // cells outside the outline aren't listed: never coming
	}

	/** GLock held: re-read the drawn-area key list when user_areas.json changed. */
	void RefreshKeysLocked()
	{
		const double Now = FPlatformTime::Seconds();
		if (Now - GKeysCheckedAt < 1.0)
		{
			return;
		}
		GKeysCheckedAt = Now;
		const FString Path = ODBakeLink::RepoRoot() / TEXT("data/user_areas.json");
		const FDateTime Stamp = IFileManager::Get().GetTimeStamp(*Path);
		if (Stamp == GKeysStamp)
		{
			return;
		}
		GKeysStamp = Stamp;
		GDrawnKeys.Reset();
		GDrawnTerrainTileCm.Reset();
		const TSharedPtr<FJsonObject> Root = LinkReadJson(Path);
		const TSharedPtr<FJsonObject>* Areas = nullptr;
		if (Root.IsValid() && Root->TryGetObjectField(TEXT("areas"), Areas))
		{
			for (const TPair<FString, TSharedPtr<FJsonValue>>& A : (*Areas)->Values)
			{
				const TSharedPtr<FJsonObject>* Obj = nullptr;
				double TileM = 2000.0;
				if (A.Value->TryGetObject(Obj))
				{
					(*Obj)->TryGetNumberField(TEXT("terrain_tile_m"), TileM);
				}
				GDrawnKeys.Add(A.Key);
				GDrawnTerrainTileCm.Add(A.Key, (float)(TileM * 100.0));
			}
		}
		GDrawnKeys.Sort([](const FString& A, const FString& B) { return A.Len() > B.Len(); });
	}

	void LinkWriteAtomic(const FString& Path, const FString& Text)
	{
		const FString Tmp = Path + TEXT(".tmp");
		if (!FFileHelper::SaveStringToFile(Text, *Tmp, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
		{
			return;
		}
#if PLATFORM_WINDOWS
		// one atomic replace - IFileManager::Move deletes first, and the runner
		// could read "no car.json" in between
		::MoveFileExW(*FPaths::ConvertRelativePathToFull(Tmp), *FPaths::ConvertRelativePathToFull(Path),
			MOVEFILE_REPLACE_EXISTING);
#else
		IFileManager::Get().Move(*Path, *Tmp, true);
#endif
	}

	void Heartbeat()
	{
		UWorld* World = GEngine && GEngine->GameViewport ? GEngine->GameViewport->GetWorld() : nullptr;
		const AODVehiclePawn* Pawn = World ? Cast<AODVehiclePawn>(UGameplayStatics::GetPlayerPawn(World, 0)) : nullptr;
		// (2026-10-21) just where the car is; no drive/menu mode - the runner always
		// bakes at below-normal priority, so the OS keeps the game smooth on any screen
		if (Pawn && !Pawn->IsInShowroom())
		{
			GLastPos = FVector2D(Pawn->GetActorLocation());
			GLastHeading = (float)Pawn->GetActorRotation().Yaw;
		}
		FString Quit = TEXT("stop_after_job");
		GConfig->GetString(ODBakeLink::ConfigSection, ODBakeLink::QuitBehaviourKey, Quit, GGameUserSettingsIni);
		if (Quit != TEXT("keep_baking"))
		{
			Quit = TEXT("stop_after_job");
		}
		const FString Key = ODBakeLink::ActiveKey();
		const FString Text = FString::Printf(
			TEXT("{\"area\": %s, \"x_cm\": %.1f, \"y_cm\": %.1f, \"heading_deg\": %.1f, ")
			TEXT("\"target_cm\": %s, \"quit_behaviour\": \"%s\", \"t\": %.3f}\n"),
			Key.IsEmpty() ? TEXT("null") : *FString::Printf(TEXT("\"%s\""), *Key.ReplaceCharWithEscapedChar()),
			GLastPos.X, GLastPos.Y, GLastHeading,
			GTarget ? *FString::Printf(TEXT("[%.1f, %.1f]"), GTarget->X, GTarget->Y) : TEXT("null"),
			*Quit, FDateTime::UtcNow().ToUnixTimestampDecimal());
		const FString Dir = ODBakeLink::RepoRoot() / TEXT("data/bake");
		IFileManager::Get().MakeDirectory(*Dir, true);
		LinkWriteAtomic(Dir / TEXT("car.json"), Text);
	}

	void AlertReady(const FString& Key, const FString& Name)
	{
		UE_LOG(LogTemp, Display, TEXT("ODBakeLink: '%s' is ready to drive"), *Key);
		if (FSlateApplication::IsInitialized())
		{
			FNotificationInfo Info(FText::FromString(FString::Printf(TEXT("%s is ready to drive"), *Name)));
			Info.ExpireDuration = 8.0f;
			Info.bFireAndForget = true;
			FSlateNotificationManager::Get().AddNotification(Info);
		}
#if PLATFORM_WINDOWS
		if (!FPlatformApplicationMisc::IsThisApplicationForeground() && GEngine && GEngine->GameViewport)
		{
			if (const TSharedPtr<SWindow> Win = GEngine->GameViewport->GetWindow(); Win && Win->GetNativeWindow())
			{
				FLASHWINFO F = { sizeof(FLASHWINFO), (HWND)Win->GetNativeWindow()->GetOSWindowHandle(),
					FLASHW_ALL | FLASHW_TIMERNOFG, 0, 0 };
				::FlashWindowEx(&F);   // flashes until the window comes to the front
			}
		}
#endif
	}

	/** Every data/bake/<key>/status.json: alert on a ready_to_drive false ->
	 *  true flip (a bake already ready when first seen doesn't alert). */
	void WatchReady()
	{
		TArray<FString> Dirs;
		IFileManager::Get().FindFiles(Dirs, *(ODBakeLink::RepoRoot() / TEXT("data/bake/*")), false, true);
		for (const FString& Key : Dirs)
		{
			bool bReady;
			{
				FScopeLock Lock(&GLock);
				bReady = StatusLocked(Key).bReady;
			}
			bool& Seen = GReadySeen.FindOrAdd(Key, bReady);
			if (bReady && !Seen)
			{
				const TSharedPtr<FJsonObject> Root = LinkReadJson(StatusPath(Key));
				FString Name = Key;
				if (Root.IsValid())
				{
					Root->TryGetStringField(TEXT("name"), Name);
				}
				AlertReady(Key, Name);
			}
			Seen = bReady;
		}
	}
}

FString ODBakeLink::RepoRoot()
{
	return FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT(".."));
}

void ODBakeLink::Init()
{
	if (IsRunningCommandlet())
	{
		return;
	}
	FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateLambda([](float)
	{
		if (GIsEditor && !(GEngine && GEngine->GameViewport))
		{
			return true;   // the editor itself isn't "the game" - only PIE is
		}
		Heartbeat();
		WatchReady();
		return true;
	}), 1.0f);
}

bool ODBakeLink::LoadUserArea(const FString& Key, FUserArea& Out)
{
	const TSharedPtr<FJsonObject> Root = LinkReadJson(RepoRoot() / TEXT("data/user_areas.json"));
	const TSharedPtr<FJsonObject>* Areas = nullptr;
	const TSharedPtr<FJsonObject>* Worlds = nullptr;
	const TSharedPtr<FJsonObject>* Area = nullptr;
	if (!Root.IsValid() || !Root->TryGetObjectField(TEXT("areas"), Areas) || !(*Areas)->TryGetObjectField(Key, Area)
		|| !Root->TryGetObjectField(TEXT("worlds"), Worlds))
	{
		return false;
	}
	Out = FUserArea();
	Out.Key = Key;
	Out.Name = Key;
	(*Area)->TryGetStringField(TEXT("name"), Out.Name);
	const TArray<TSharedPtr<FJsonValue>>* P = nullptr;
	if (!(*Area)->TryGetStringField(TEXT("world"), Out.World) || !(*Worlds)->TryGetArrayField(Out.World, P) || P->Num() != 4)
	{
		UE_LOG(LogTemp, Warning, TEXT("ODBakeLink: '%s' has no zone frame in user_areas.json"), *Key);
		return false;
	}
	const FLcc Lcc((*P)[0]->AsNumber(), (*P)[1]->AsNumber(), (*P)[2]->AsNumber(), (*P)[3]->AsNumber());
	const TArray<TSharedPtr<FJsonValue>>* Poly = nullptr;
	if ((*Area)->TryGetArrayField(TEXT("polygon_ll"), Poly))
	{
		for (const TSharedPtr<FJsonValue>& V : *Poly)
		{
			Out.OutlineCm.Add(Lcc.ToCm(V->AsArray()));
		}
	}
	const TArray<TSharedPtr<FJsonValue>>* Start = nullptr;
	Out.bHasStart = (*Area)->TryGetArrayField(TEXT("start_ll"), Start) && Start->Num() >= 2;
	if (Out.bHasStart)
	{
		Out.StartCm = Lcc.ToCm(*Start);
	}
	double TileM = 2000.0;
	(*Area)->TryGetNumberField(TEXT("terrain_tile_m"), TileM);
	Out.TerrainTileCm = (float)(TileM * 100.0);

	// the LCC again for start_snapped_ll, which status.json gives in lon/lat
	FScopeLock Lock(&GLock);
	GActiveLcc = MakeShared<FLcc>(Lcc);
	return true;
}

void ODBakeLink::SetActiveArea(const FUserArea* Area)
{
	FScopeLock Lock(&GLock);
	GActiveKey = Area ? Area->Key : FString();
	GOutline.Reset();
	if (Area && Area->OutlineCm.Num() >= 3)
	{
		GOutline = MakeShared<const TArray<FVector2D>, ESPMode::ThreadSafe>(Area->OutlineCm);
	}
	GSnappedCm.Reset();
	if (Area)
	{
		GStatus.Remove(Area->Key);   // re-read, so the snapped start uses this LCC
		StatusLocked(Area->Key);
	}
}

FString ODBakeLink::ActiveKey()
{
	FScopeLock Lock(&GLock);
	return GActiveKey;
}

TSharedPtr<const TArray<FVector2D>, ESPMode::ThreadSafe> ODBakeLink::ActiveOutline()
{
	FScopeLock Lock(&GLock);
	return GOutline;
}

double ODBakeLink::SignedDistanceCm(const TArray<FVector2D>& Outline, const FVector2D& P)
{
	double Best = TNumericLimits<double>::Max();
	bool bInside = false;
	for (int32 i = 0, j = Outline.Num() - 1; i < Outline.Num(); j = i++)
	{
		const FVector2D& A = Outline[j];
		const FVector2D& B = Outline[i];
		if ((B.Y > P.Y) != (A.Y > P.Y) && P.X < (A.X - B.X) * (P.Y - B.Y) / (A.Y - B.Y) + B.X)
		{
			bInside = !bInside;
		}
		const FVector2D AB = B - A;
		const double T = FMath::Clamp(FVector2D::DotProduct(P - A, AB) / FMath::Max(AB.SizeSquared(), 1.0), 0.0, 1.0);
		Best = FMath::Min(Best, FVector2D::DistSquared(P, A + AB * T));
	}
	return bInside ? FMath::Sqrt(Best) : -FMath::Sqrt(Best);
}

bool ODBakeLink::IsTilePending(const FString& ContentPath)
{
	// "<family>_<key>[_test]_tile_<x>_<y>.bin"
	const FString File = FPaths::GetCleanFilename(ContentPath);
	const int32 At = File.Find(TEXT("_tile_"), ESearchCase::CaseSensitive, ESearchDir::FromEnd);
	FString XS, YS;
	if (At == INDEX_NONE || !File.Mid(At + 6).LeftChop(4).Split(TEXT("_"), &XS, &YS, ESearchCase::CaseSensitive, ESearchDir::FromEnd))
	{
		return false;
	}
	const FString Prefix = File.Left(At);
	FScopeLock Lock(&GLock);
	RefreshKeysLocked();
	for (const FString& Key : GDrawnKeys)
	{
		const int32 K = Prefix.Find(TEXT("_") + Key, ESearchCase::CaseSensitive);
		if (K == INDEX_NONE)
		{
			continue;
		}
		const FStatus& S = StatusLocked(Key);
		if (!S.bBaking)
		{
			return false;
		}
		// ponytail: tile size by family name, not by asking the manager - road
		// (v2 + v3) 500 m, terrain (+coarse) the area's terrain_tile_m, every
		// other family 2 km. A manager with a non-default TileSizeCm needs a lookup.
		const FString Family = Prefix.Left(K);
		const int64 TileCm = Family.StartsWith(TEXT("road")) ? 50000
			: Family.StartsWith(TEXT("terrain")) ? (int64)GDrawnTerrainTileCm.FindRef(Key) : 200000;
		const ODBakeLink::FCellRange R = TileCells(FCString::Atoi64(*XS), FCString::Atoi64(*YS), FMath::Max<int64>(TileCm, 1000));
		for (int64 I = R.I0; I <= R.I1; ++I)
		{
			for (int64 J = R.J0; J <= R.J1; ++J)
			{
				if (CellPendingLocked(S, I, J))
				{
					return true;
				}
			}
		}
		return false;
	}
	return false;
}

bool ODBakeLink::IsPointPending(const FVector2D& P)
{
	FScopeLock Lock(&GLock);
	if (GActiveKey.IsEmpty())
	{
		return false;
	}
	const FStatus& S = StatusLocked(GActiveKey);
	return CellPendingLocked(S, FloorDiv((int64)FMath::FloorToDouble(P.X), CellCm),
		FloorDiv((int64)FMath::FloorToDouble(-P.Y), CellCm));
}

uint32 ODBakeLink::StatusVersion()
{
	FScopeLock Lock(&GLock);
	if (!GActiveKey.IsEmpty())
	{
		StatusLocked(GActiveKey);
	}
	return GVersion;
}

bool ODBakeLink::SnappedStartCm(FVector2D& Out)
{
	FScopeLock Lock(&GLock);
	if (!GActiveKey.IsEmpty())
	{
		StatusLocked(GActiveKey);
	}
	if (GSnappedCm)
	{
		Out = *GSnappedCm;
	}
	return GSnappedCm.IsSet();
}

void ODBakeLink::SetTarget(const FVector2D* TargetCm)
{
	if (TargetCm)
	{
		GTarget = *TargetCm;
	}
	else
	{
		GTarget.Reset();
	}
}
