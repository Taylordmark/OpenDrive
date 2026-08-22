#include "ODScreenshotHook.h"

#include "Misc/App.h"
#include "Misc/CommandLine.h"
#include "UnrealClient.h"

AODScreenshotHook::AODScreenshotHook()
{
	PrimaryActorTick.bCanEverTick = true;
	// 2026-07-28: AODMainMenu pauses the game almost immediately at boot (the
	// whole car-config/map-select flow runs paused), and this actor never had
	// `bTickEvenWhenPaused` set - so on any level with the main menu, this
	// Tick() simply never ran at all while the menu was up, silently
	// no-op'ing `-odshot` for the entire duration of the showroom/map screens
	// (found while trying to screenshot the showroom camera bug - every
	// attempt produced nothing, for this reason, not a slow boot).
	PrimaryActorTick.bTickEvenWhenPaused = true;
}

void AODScreenshotHook::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	// headless visual QA: `-odshot=25` captures a screenshot 25 s after boot.
	// Real elapsed time (FApp::GetCurrentTime(), wall-clock), NOT
	// GetWorld()->GetTimeSeconds() (world/game time, which freezes the
	// instant the world pauses - same "world timers freeze once paused"
	// caveat AODMainMenu's own -odmenumap/-odpick hooks already route around
	// via FTSTicker, see its BeginPlay comment) - otherwise this can never
	// fire at all on a level whose menu pauses at boot.
	static double StartTime = -1.0;
	static float ShotAt = -1.0f;
	static bool bShotParsed = false, bShotDone = false;
	if (StartTime < 0.0)
	{
		StartTime = FApp::GetCurrentTime();
	}
	if (!bShotParsed)
	{
		bShotParsed = true;
		FParse::Value(FCommandLine::Get(), TEXT("odshot="), ShotAt);
	}
	if (ShotAt > 0.0f && !bShotDone && (FApp::GetCurrentTime() - StartTime) > ShotAt)
	{
		bShotDone = true;
		FScreenshotRequest::RequestScreenshot(false);
		UE_LOG(LogTemp, Display, TEXT("ODScreenshotHook: screenshot requested"));
	}
}
