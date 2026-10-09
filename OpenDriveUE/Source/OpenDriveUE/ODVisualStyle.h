#pragma once

#include "CoreMinimal.h"
#include "Components/MeshComponent.h"
#include "Materials/MaterialInterface.h"
#include "Misc/ConfigCacheIni.h"

/**
 * Graphics menu "Visual Style" (AODSettingsMenu row; persisted as
 * OpenDrive.Graphics / VisualStyle in GameUserSettings.ini): 0 Stylized,
 * 1 Realistic - the PLAN-VISUALS.md trial. Same shape as ODAssetDetail.h and
 * independent of it: Asset Detail picks polygon count, this picks the look.
 *
 * Realistic assets sit beside their stylized twin with a "_real" suffix
 * ("/Game/X/M_Road.M_Road" -> "/Game/X/M_Road_real.M_Road_real") and must
 * keep its parameter names, UV/vertex-color channels and physical material
 * (surface grip reads the phys-mat name). Pick() falls back to the stylized
 * asset when no _real twin is imported, so Realistic with nothing built yet
 * looks exactly like Stylized. Read from the ini because the managers load
 * their materials in BeginPlay - a change applies on the next level load.
 */
namespace ODVisualStyle
{
	inline const TCHAR* ConfigSection = TEXT("OpenDrive.Graphics");
	inline const TCHAR* ConfigKey = TEXT("VisualStyle");

	inline int32 Level()
	{
		int32 L = 0;
		GConfig->GetInt(ConfigSection, ConfigKey, L, GGameUserSettingsIni);
		return FMath::Clamp(L, 0, 1);
	}

	inline bool IsRealistic() { return Level() == 1; }

	/** The current style's variant of Base (mesh, material, data asset), or
	 *  Base itself when Stylized, for a null Base, or when no _real twin is
	 *  imported. Lookups (misses included) are cached, since tile builds call
	 *  this per slot while driving and a miss is a package lookup on disk. */
	template <typename T>
	T* Pick(T* Base)
	{
		if (!Base || !IsRealistic())
		{
			return Base;
		}
		static TMap<TWeakObjectPtr<UObject>, TWeakObjectPtr<UObject>> Cache;
		if (const TWeakObjectPtr<UObject>* Hit = Cache.Find(Base))
		{
			if (T* Cached = Cast<T>(Hit->Get()))
			{
				return Cached;
			}
		}
		const FString Pkg = Base->GetOutermost()->GetName();
		const FString Name = Base->GetName() + TEXT("_real");
		T* Variant = LoadObject<T>(
			nullptr, *(Pkg + TEXT("_real.") + Name), nullptr, LOAD_NoWarn | LOAD_Quiet);
		T* Result = Variant ? Variant : Base;
		Cache.Add(Base, Result);
		return Result;
	}

	/** Pick() on every material slot of a mesh whose materials come from the
	 *  mesh asset itself (the car chassis and wheels) - call after
	 *  SetStaticMesh. Only slots with a _real twin are overridden. Clears
	 *  old overrides first: SetStaticMesh keeps them, so a car swap would
	 *  otherwise carry the previous car's slots over (the lamp MID is rebuilt
	 *  by AODDayNightController on its next tick). */
	inline void PickSlots(UMeshComponent* Mesh)
	{
		if (!Mesh || !IsRealistic())
		{
			return;
		}
		Mesh->EmptyOverrideMaterials();
		for (int32 i = 0; i < Mesh->GetNumMaterials(); ++i)
		{
			UMaterialInterface* Base = Mesh->GetMaterial(i);
			UMaterialInterface* Variant = Pick(Base);
			if (Variant != Base)
			{
				Mesh->SetMaterial(i, Variant);
			}
		}
	}
}
