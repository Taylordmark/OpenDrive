#include "ODGameMode.h"

#include "ODRaceHUD.h"
#include "ODVehiclePawn.h"

AODGameMode::AODGameMode()
{
	DefaultPawnClass = AODVehiclePawn::StaticClass();
	HUDClass = AODRaceHUD::StaticClass();
}
