#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "ODGameMode.generated.h"

/**
 * OD game mode (template exorcism, 2026-07-17): spawns AODVehiclePawn and
 * wires ODRaceHUD. Replaces the VehicleTemplate BP game mode entirely.
 */
UCLASS()
class OPENDRIVEUE_API AODGameMode : public AGameModeBase
{
	GENERATED_BODY()

public:
	AODGameMode();
};
