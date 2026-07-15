#pragma once

#include "CoreMinimal.h"
#include "GameFramework/HUD.h"
#include "ODRaceHUD.generated.h"

/** Canvas race HUD: timer, best, gate progress, speed. */
UCLASS()
class OPENDRIVEUE_API AODRaceHUD : public AHUD
{
	GENERATED_BODY()

public:
	virtual void DrawHUD() override;
};
