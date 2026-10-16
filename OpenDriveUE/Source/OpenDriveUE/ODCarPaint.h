#pragma once

#include "CoreMinimal.h"
#include "Components/MeshComponent.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/ConfigCacheIni.h"

/**
 * 4.3 cosmetics v1: the player car's paint, picked on the garage's Paint
 * screen (AODMainMenu), saved per car (OpenDrive.Paint / <def asset name> in
 * GameUserSettings.ini) and applied by AODVehiclePawn as MIDs on the chassis
 * "body" slot and every wheel's "rim" slot. The parameters are the Realistic
 * car materials' (unreal/build_car_materials_real.py); grime, snow and rain
 * are drawn on top of the paint there, so ODCarGrime works unchanged.
 * Stylized materials don't have the parameters: the paint shows in Realistic only.
 */
struct FODPaintSwatch
{
	const TCHAR* Name;
	FColor Srgb;
};

namespace ODCarPaint
{
	inline const FODPaintSwatch Swatches[] = {
		{TEXT("Arctic White"), FColor(236, 236, 232)},
		{TEXT("Silver"), FColor(168, 171, 175)},
		{TEXT("Gunmetal"), FColor(68, 72, 77)},
		{TEXT("Jet Black"), FColor(12, 12, 13)},
		{TEXT("Racing Red"), FColor(176, 16, 20)},
		{TEXT("Candy Orange"), FColor(222, 88, 14)},
		{TEXT("Sunburst Yellow"), FColor(238, 186, 18)},
		{TEXT("British Green"), FColor(16, 62, 40)},
		{TEXT("Lime"), FColor(118, 186, 38)},
		{TEXT("Grabber Teal"), FColor(0, 128, 138)},
		{TEXT("Electric Blue"), FColor(18, 78, 198)},
		{TEXT("Midnight Blue"), FColor(14, 24, 58)},
		{TEXT("Plum"), FColor(78, 24, 68)},
		{TEXT("Bronze"), FColor(128, 88, 48)},
	};

	// Metallic = the material's own defaults (the factory look)
	inline const TCHAR* FinishNames[] = {TEXT("Gloss"), TEXT("Metallic"), TEXT("Matte"), TEXT("Pearl")};
	inline const TCHAR* PatternNames[] = {TEXT("None"), TEXT("Two-Tone"), TEXT("Racing Stripes"), TEXT("Side Stripe")};

	/** RimColor.a: 0 factory alloy, 1 tinted metal, 2 painted (Body Color = painted in the body's colour). */
	struct FRim { const TCHAR* Name; FColor Srgb; float Kind; };
	inline const FRim Rims[] = {
		{TEXT("Factory"), FColor::Black, 0.0f},
		{TEXT("Silver"), FColor(205, 207, 212), 1.0f},
		{TEXT("Gunmetal"), FColor(62, 64, 68), 1.0f},
		{TEXT("Gloss Black"), FColor(10, 10, 10), 2.0f},
		{TEXT("Bronze"), FColor(140, 100, 56), 1.0f},
		{TEXT("Gold"), FColor(200, 160, 72), 1.0f},
		{TEXT("White"), FColor(232, 232, 230), 2.0f},
		{TEXT("Body Color"), FColor::Black, 2.0f},
	};
	constexpr int32 RimBodyColor = UE_ARRAY_COUNT(Rims) - 1;

	inline const TCHAR* ConfigSection = TEXT("OpenDrive.Paint");
}

struct FODCarPaint
{
	bool bFactory = true;                          // body keeps the model's palette colour
	float H = 0.0f, S = 0.0f, V = 0.9f;            // custom colour, sRGB HSV (H in degrees)
	int32 Finish = 1;                              // ODCarPaint::FinishNames
	int32 Pattern = 0;                             // ODCarPaint::PatternNames
	int32 Accent = 3;                              // ODCarPaint::Swatches, the two-tone / stripe colour
	int32 Rim = 0;                                 // ODCarPaint::Rims

	FColor BodySrgb() const { return FLinearColor(H, S, V).HSVToLinearRGB().ToFColor(false); }

	void SetSrgb(const FColor& C)
	{
		const FLinearColor Hsv = FLinearColor(C.R / 255.0f, C.G / 255.0f, C.B / 255.0f).LinearRGBToHSV();
		H = Hsv.R; S = Hsv.G; V = Hsv.B;
		bFactory = false;
	}

	/** Index into Swatches the colour matches, INDEX_NONE for a custom colour. */
	int32 SwatchIdx() const
	{
		const FColor C = BodySrgb();
		for (int32 i = 0; i < UE_ARRAY_COUNT(ODCarPaint::Swatches); ++i)
		{
			const FColor& W = ODCarPaint::Swatches[i].Srgb;
			if (FMath::Abs(C.R - W.R) + FMath::Abs(C.G - W.G) + FMath::Abs(C.B - W.B) <= 3)
			{
				return i;
			}
		}
		return INDEX_NONE;
	}

	FString ToString() const
	{
		return FString::Printf(TEXT("%d,%.1f,%.3f,%.3f,%d,%d,%d,%d"), bFactory ? 1 : 0, H, S, V, Finish, Pattern, Accent, Rim);
	}

	static FODCarPaint FromString(const FString& Str)
	{
		FODCarPaint P;
		TArray<FString> F;
		if (Str.ParseIntoArray(F, TEXT(",")) != 8)
		{
			return P;   // missing or from another version: factory
		}
		P.bFactory = FCString::Atoi(*F[0]) != 0;
		P.H = FMath::Fmod(FMath::Abs(FCString::Atof(*F[1])), 360.0f);
		P.S = FMath::Clamp(FCString::Atof(*F[2]), 0.0f, 1.0f);
		P.V = FMath::Clamp(FCString::Atof(*F[3]), 0.0f, 1.0f);
		P.Finish = FMath::Clamp(FCString::Atoi(*F[4]), 0, UE_ARRAY_COUNT(ODCarPaint::FinishNames) - 1);
		P.Pattern = FMath::Clamp(FCString::Atoi(*F[5]), 0, UE_ARRAY_COUNT(ODCarPaint::PatternNames) - 1);
		P.Accent = FMath::Clamp(FCString::Atoi(*F[6]), 0, UE_ARRAY_COUNT(ODCarPaint::Swatches) - 1);
		P.Rim = FMath::Clamp(FCString::Atoi(*F[7]), 0, UE_ARRAY_COUNT(ODCarPaint::Rims) - 1);
		return P;
	}

	static FODCarPaint Load(const FString& CarKey)
	{
		FString Str;
		GConfig->GetString(ODCarPaint::ConfigSection, *CarKey, Str, GGameUserSettingsIni);
		return FromString(Str);
	}

	void Save(const FString& CarKey) const
	{
		GConfig->SetString(ODCarPaint::ConfigSection, *CarKey, *ToString(), GGameUserSettingsIni);
		GConfig->Flush(false, GGameUserSettingsIni);
	}

	/** The MID on Mesh's named slot (made on first use; a mesh swap's PickSlots clears it). */
	static UMaterialInstanceDynamic* SlotMID(UMeshComponent* Mesh, FName Slot)
	{
		const int32 i = Mesh ? Mesh->GetMaterialIndex(Slot) : INDEX_NONE;
		if (i == INDEX_NONE)
		{
			return nullptr;
		}
		UMaterialInstanceDynamic* MID = Cast<UMaterialInstanceDynamic>(Mesh->GetMaterial(i));
		return MID ? MID : Mesh->CreateDynamicMaterialInstance(i);
	}

	void ApplyBody(UMeshComponent* Body) const
	{
		UMaterialInstanceDynamic* MID = SlotMID(Body, TEXT("body"));
		if (!MID || !MID->Parent)
		{
			return;
		}
		MID->SetVectorParameterValue(TEXT("PaintColor"),
			bFactory ? FLinearColor::Transparent : FLinearColor(BodySrgb()).CopyWithNewOpacity(1.0f));
		MID->SetVectorParameterValue(TEXT("AccentColor"), FLinearColor(ODCarPaint::Swatches[Accent].Srgb));
		MID->SetScalarParameterValue(TEXT("PaintPattern"), Pattern);

		// finishes relative to the parent's values (the Standard tier's own instance is rougher)
		auto Parent = [&](const TCHAR* Name)
		{
			float X = 0.0f;
			MID->Parent->GetScalarParameterValue(FHashedMaterialParameterInfo(Name), X);
			return X;
		};
		const float BaseR = Parent(TEXT("BaseRoughness")), CoatR = Parent(TEXT("CoatRoughness"));
		//                          Gloss  Metallic                          Matte                      Pearl
		const float Metal[] =     { 0.0f,  Parent(TEXT("BaseMetallic")),    0.15f,                     0.3f };
		const float Rough[] =     { BaseR, BaseR,                           FMath::Max(BaseR, 0.55f),  BaseR };
		const float Coat[] =      { CoatR, CoatR,                           FMath::Max(CoatR, 0.45f),  CoatR };
		MID->SetScalarParameterValue(TEXT("BaseMetallic"), Metal[Finish]);
		MID->SetScalarParameterValue(TEXT("BaseRoughness"), Rough[Finish]);
		MID->SetScalarParameterValue(TEXT("CoatRoughness"), Coat[Finish]);
		MID->SetScalarParameterValue(TEXT("Pearl"), Finish == 3 ? 1.0f : 0.0f);
	}

	void ApplyRim(UMeshComponent* Wheel) const
	{
		if (UMaterialInstanceDynamic* MID = SlotMID(Wheel, TEXT("rim")))
		{
			const ODCarPaint::FRim& R = ODCarPaint::Rims[Rim];
			const bool bBody = Rim == ODCarPaint::RimBodyColor;
			// Body Color on a factory body: nothing to match, keep the alloy
			const float Kind = bBody && bFactory ? 0.0f : R.Kind;
			MID->SetVectorParameterValue(TEXT("RimColor"),
				FLinearColor(bBody ? BodySrgb() : R.Srgb).CopyWithNewOpacity(Kind));
		}
	}
};
