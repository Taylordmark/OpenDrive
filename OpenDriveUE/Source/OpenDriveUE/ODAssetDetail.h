#pragma once

#include "CoreMinimal.h"
#include "Engine/StaticMesh.h"
#include "Misc/ConfigCacheIni.h"

/**
 * Graphics menu "Asset Detail" (AODSettingsMenu row; persisted as
 * OpenDrive.Graphics / AssetDetail in GameUserSettings.ini): 0 Standard,
 * 1 High, 2 Ultra. The pipeline generators (make_tree_meshes.py,
 * make_rock_meshes.py, make_car_meshes.py) write <name>_high / <name>_ultra
 * next to every flora/rock/car mesh and the unreal/import_*.py scripts
 * import them beside the Standard asset. Read from the ini (not the lazily
 * spawned menu actor) because the forest/barrier managers and the vehicle
 * pawn load their meshes in BeginPlay - a change applies on the next level
 * load.
 */
namespace ODAssetDetail
{
	inline const TCHAR* ConfigSection = TEXT("OpenDrive.Graphics");
	inline const TCHAR* ConfigKey = TEXT("AssetDetail");

	inline int32 Level()
	{
		int32 L = 0;
		GConfig->GetInt(ConfigSection, ConfigKey, L, GGameUserSettingsIni);
		return FMath::Clamp(L, 0, 2);
	}

	/** The current tier's variant of a Standard mesh ("/Game/X/foo.foo" ->
	 *  "/Game/X/foo_high.foo_high"), or Base itself at Standard, for a null
	 *  Base, or when the variant isn't imported. */
	inline UStaticMesh* Upgrade(UStaticMesh* Base)
	{
		const int32 L = Level();
		if (!Base || L == 0)
		{
			return Base;
		}
		const TCHAR* Suffix = L == 1 ? TEXT("_high") : TEXT("_ultra");
		const FString Pkg = Base->GetOutermost()->GetName();
		const FString Name = Base->GetName() + Suffix;
		UStaticMesh* Variant = LoadObject<UStaticMesh>(
			nullptr, *(Pkg + Suffix + TEXT(".") + Name), nullptr, LOAD_NoWarn | LOAD_Quiet);
		return Variant ? Variant : Base;
	}
}
