#include "ODScreenshotHook.h"

#include "Misc/CommandLine.h"
#include "UnrealClient.h"

AODScreenshotHook::AODScreenshotHook()
{
	PrimaryActorTick.bCanEverTick = true;
}

void AODScreenshotHook::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	// headless visual QA: `-odshot=25` captures a screenshot 25 s after boot
	// (verbatim copy of the block in ODForestManager::Tick())
	static float ShotAt = -1.0f;
	static bool bShotParsed = false, bShotDone = false;
	if (!bShotParsed)
	{
		bShotParsed = true;
		FParse::Value(FCommandLine::Get(), TEXT("odshot="), ShotAt);
	}
	if (ShotAt > 0.0f && !bShotDone && GetWorld()->GetTimeSeconds() > ShotAt)
	{
		bShotDone = true;
		FScreenshotRequest::RequestScreenshot(false);
		UE_LOG(LogTemp, Display, TEXT("ODScreenshotHook: screenshot requested"));
	}
}
