#pragma once

#include "CoreMinimal.h"
#include "Engine/Engine.h"
#include "Engine/Font.h"
#include "Fonts/CompositeFont.h"
#include "Fonts/SlateFontInfo.h"
#include "Misc/Paths.h"

/** One look for the driving HUD and the menus: palette + the Rajdhani font. */
namespace ODHud
{
inline const FLinearColor Accent(1.0f, 0.68f, 0.25f);
inline const FLinearColor TextCol(0.88f, 0.91f, 0.95f);
inline const FLinearColor DimCol(0.52f, 0.58f, 0.65f);
inline const FLinearColor Ink(0.012f, 0.016f, 0.028f);   // backdrop, and text on an accent fill
inline const FLinearColor Warn(1.0f, 0.6f, 0.05f);
inline const FLinearColor Danger(0.95f, 0.16f, 0.1f);

enum class EMenuWeight : uint8 { Medium, SemiBold, Bold };

// Rajdhani (SIL OFL, Content/Slate/Fonts) straight from its TTF - no UFont
// asset (editor-agnostic); DefaultGame.ini stages the folder. One composite
// font per weight, built once: the Slate font cache keys on it, so a fresh
// one per call would re-rasterize glyphs every frame.
inline FSlateFontInfo MenuFont(EMenuWeight Weight, float Size)
{
	static TSharedPtr<const FCompositeFont> Fonts[3];
	static bool bTried[3] = {};
	const int32 I = static_cast<int32>(Weight);
	if (!bTried[I])
	{
		bTried[I] = true;
		static const TCHAR* const Files[] = {
			TEXT("Rajdhani-Medium.ttf"), TEXT("Rajdhani-SemiBold.ttf"), TEXT("Rajdhani-Bold.ttf")};
		const FString Path = FPaths::ProjectContentDir() / TEXT("Slate/Fonts") / Files[I];
		if (FPaths::FileExists(Path))
		{
			Fonts[I] = MakeShared<FCompositeFont>(NAME_None, Path, EFontHinting::Default, EFontLoadingPolicy::LazyLoad);
		}
		else
		{
			UE_LOG(LogTemp, Warning, TEXT("ODHud: menu font missing (%s) - using the engine font"), *Path);
		}
	}
	if (Fonts[I])
	{
		return FSlateFontInfo(Fonts[I], Size);
	}
	return FSlateFontInfo(GEngine->GetLargeFont(), Size, Weight == EMenuWeight::Medium ? FName("Regular") : FName("Bold"));
}
}
