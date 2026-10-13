#include "ODTileSource.h"

#include "Engine/World.h"
#include "HAL/Event.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformFileManager.h"
#include "HttpModule.h"
#include "Interfaces/IHttpRequest.h"
#include "Interfaces/IHttpResponse.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "TimerManager.h"

namespace
{
	// Written once by Init() on the game thread before any tile load is
	// dispatched (managers only start loading from Tick), read-only after.
	FString GServerUrl;
}

void ODTileSource::Init()
{
	FString Url;
	if (FParse::Value(FCommandLine::Get(), TEXT("ODTileServer="), Url) && !Url.IsEmpty())
	{
		if (!Url.EndsWith(TEXT("/")))
		{
			Url += TEXT("/");
		}
		FHttpModule::Get();   // load on the game thread, never from a worker
		UE_LOG(LogTemp, Display, TEXT("ODTileSource: streaming missing tiles from %s"), *Url);
	}
	GServerUrl = Url;
}

EODTileSource ODTileSource::Resolve(const FString& ContentPath, FString& OutReadPath)
{
	IPlatformFile& PF = FPlatformFileManager::Get().GetPlatformFile();
	if (PF.FileExists(*ContentPath))
	{
		OutReadPath = ContentPath;
		return EODTileSource::Ready;
	}
	FString Rel = ContentPath;
	if (GServerUrl.IsEmpty() || !Rel.RemoveFromStart(FPaths::ProjectContentDir() / TEXT("OpenDrive/")))
	{
		return EODTileSource::Missing;
	}

	// ponytail: cache never invalidates - key the dir by world generation id
	// once the server re-bakes areas in place.
	const FString CachePath = FPaths::ProjectSavedDir() / TEXT("TileCache") / Rel;
	if (PF.FileExists(*CachePath))
	{
		OutReadPath = CachePath;
		return EODTileSource::Ready;
	}

	// Blocking GET on this worker: completion fires on the HTTP thread, so
	// the game thread is never involved. Shared event so a late completion
	// after our timeout still has something valid to trigger.
	TSharedRef<FEventRef> Done = MakeShared<FEventRef>(EEventMode::ManualReset);
	TSharedRef<IHttpRequest, ESPMode::ThreadSafe> Req = FHttpModule::Get().CreateRequest();
	Req->SetURL(GServerUrl + Rel.Replace(TEXT("\\"), TEXT("/")));
	Req->SetVerb(TEXT("GET"));
	Req->SetTimeout(30.0f);
	Req->SetDelegateThreadPolicy(EHttpRequestDelegateThreadPolicy::CompleteOnHttpThread);
	Req->OnProcessRequestComplete().BindLambda(
		[Done](FHttpRequestPtr, FHttpResponsePtr, bool) { (*Done)->Trigger(); });
	Req->ProcessRequest();
	if (!(*Done)->Wait(FTimespan::FromSeconds(35.0)))
	{
		Req->CancelRequest();
		return EODTileSource::Pending;
	}

	const FHttpResponsePtr Resp = Req->GetResponse();
	const int32 Code = Resp.IsValid() ? Resp->GetResponseCode() : 0;
	if (Code == 404)
	{
		return EODTileSource::Missing;
	}
	if (Code != 200)
	{
		return EODTileSource::Pending;   // 202 "still baking", 5xx, or no connection
	}

	// Write-then-rename so a concurrent reader never sees a half-written tile.
	const FString TmpPath = CachePath + FString::Printf(TEXT(".%u.tmp"), FPlatformTLS::GetCurrentThreadId());
	if (!FFileHelper::SaveArrayToFile(Resp->GetContent(), *TmpPath)
		|| !IFileManager::Get().Move(*CachePath, *TmpPath, /*bReplace=*/true))
	{
		UE_LOG(LogTemp, Warning, TEXT("ODTileSource: could not cache %s"), *CachePath);
		return EODTileSource::Pending;
	}
	OutReadPath = CachePath;
	return EODTileSource::Ready;
}

void ODTileSource::RetryLater(UObject* Owner, TSet<FIntPoint>& Pending, const FIntPoint& Key)
{
	// Weak lambda: Pending is a member of Owner, valid exactly as long as Owner is.
	FTimerHandle Unused;
	Owner->GetWorld()->GetTimerManager().SetTimer(Unused,
		FTimerDelegate::CreateWeakLambda(Owner, [&Pending, Key]() { Pending.Remove(Key); }),
		RetryDelaySeconds, false);
}
