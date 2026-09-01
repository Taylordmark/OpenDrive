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

	virtual void BeginPlay() override;

private:
	/** Open-world forest bootstrap (2026-08-26): Tail of the Dragon has its
	 *  AODForestManager hand-placed in the .umap (Landscape-mask path,
	 *  DefaultBiome=DeciduousForest); OpenWorldTest never got one, so
	 *  open-world drives showed zero trees/vegetation. Editing a .umap
	 *  headlessly is fragile, and this is the one GameMode class every level
	 *  in the project already shares (GlobalDefaultGameMode in
	 *  DefaultEngine.ini) - runtime-spawning here, guarded by "does a forest
	 *  manager already exist", is the same shape as everything else that
	 *  varies by level (AODRoadManager/AODTerrainManager are placed once per
	 *  map with their tile-prefix UPROPERTYs already baked in) without
	 *  needing a second hand-authored placement for every open-world area. */
	void EnsureForestManager();

	/** Open-world water bootstrap (2026-08-27, WATER feature) - same shape
	 *  as EnsureForestManager() above: no open-world level has an
	 *  AODWaterManager hand-placed yet, so this runtime-spawns one, guarded
	 *  by "does a water manager already exist" the same way forest is,
	 *  deriving its tile prefix from the level's own AODRoadManager rather
	 *  than requiring a second hand-authored placement per area. */
	void EnsureWaterManager();
};
