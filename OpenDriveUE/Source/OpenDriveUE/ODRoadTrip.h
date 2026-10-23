#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODRoadTrip.generated.h"

/**
 * Road trip mode (2026-10-22, fun feature #6): picking a destination on the
 * in-game world map (Tab) spawns one of these with Dest + DestName preset; a
 * ?oddest=<x>,<y> URL option (area-local cm, used by -odtripcheck) makes
 * AODGameMode spawn one too. Once the route graph is loaded and the menu has
 * started the drive, it routes there (AODRoadManager::SetRouteDestination),
 * shows a start banner, tracks the trip and shows an arrival card.
 *
 * Headless self-check: -odtripcheck logs "ODRoadTrip: SELFCHECK PASS/FAIL ..."
 * once the route is set (or after TripCheckTimeoutSec) and exits (grep the log;
 * UnrealEditor-Cmd's exit code came back 0 on FAIL too). Needs a saved start town
 * ([OpenDrive.Area] Town= in GameUserSettings.ini), as a menu Drive always has -
 * green_valley's PlayerStart sits on a 164-node graph island, so no route from it:
 *   UnrealEditor-Cmd OpenDriveUE.uproject "/Game/Areas/OpenWorld_green_valley?odengine=0?oddest=1136941,423339"
 *     -game -nullrhi -nosound -unattended -stdout -odtripcheck
 */
UCLASS()
class OPENDRIVEUE_API AODRoadTrip : public AActor
{
	GENERATED_BODY()

public:
	AODRoadTrip();
	virtual void Tick(float DeltaSeconds) override;

	/** "<x>,<y>" (cm) -> Out; false if malformed. */
	static bool ParseDest(const TCHAR* Opt, FVector2D& Out);

	FVector2D Dest = FVector2D::ZeroVector;   // area-local (= world) XY, cm

	enum class EState : uint8 { Waiting, Driving, Arrived };
	EState State = EState::Waiting;
	FString StartName, DestName;
	float RouteKm = 0.0f;          // the route's length when set
	double StartTime = 0.0;        // world seconds (pauses with the game)
	double ArriveTime = 0.0;
	double DistCm = 0.0;           // driven
	float TopCmS = 0.0f;

private:
	void BeginTrip();
	void SelfCheck(bool bPass, const FString& Why);
	TWeakObjectPtr<class AODRoadManager> Road;
	FVector LastPos = FVector::ZeroVector;
	double CreatedReal = 0.0;
	bool bCheck = false;
};

namespace ODRoadTrip
{
/** Start banner + arrival card, called from AODRaceHUD::DrawHUD (S = MenuScale()). */
void DrawHUD(const AActor* Ctx, class UCanvas* Canvas, float S, bool bMetric);
}
