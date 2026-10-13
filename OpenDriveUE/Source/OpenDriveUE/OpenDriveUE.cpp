#include "OpenDriveUE.h"
#include "Modules/ModuleManager.h"
#include "ODTileSource.h"

// Tile server setup runs here, before any level-placed manager's BeginPlay.
class FOpenDriveUEModule : public FDefaultGameModuleImpl
{
	virtual void StartupModule() override { ODTileSource::Init(); }
};

IMPLEMENT_PRIMARY_GAME_MODULE(FOpenDriveUEModule, OpenDriveUE, "OpenDriveUE");
