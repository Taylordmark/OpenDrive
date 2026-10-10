#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODCreditsMenu.generated.h"

/**
 * Credits screen (Arc 4.4, next steps.md SS4.4/SS7.31) - the full data-
 * attribution list, required for public release. Content is GENERATED, not
 * hand-typed: pipeline/build_credits.py walks every content_src manifest.json
 * (recursively) license record in the repo plus a small hand-maintained
 * header block, and writes one plain-UTF-8 text sidecar (Content/OpenDrive/
 * Masks/credits.txt) that this actor loads verbatim at BeginPlay - see that
 * script's own module docstring for exactly what goes in and why plain text
 * was chosen over a packed binary format.
 *
 * Same "plain logic/state object, no independent input binding" shape as
 * AODSettingsMenu (see that class's comment for why): scrolling is driven
 * by AODSettingsMenu (its GAME tab opens it),
 * calling Scroll() from their own already-input-bound Up()/Down().
 */
UCLASS()
class OPENDRIVEUE_API AODCreditsMenu : public AActor
{
	GENERATED_BODY()

public:
	AODCreditsMenu();
	virtual void BeginPlay() override;

	void Scroll(int32 Dir);
	const TArray<FString>& GetLines() const { return Lines; }
	int32 GetScrollLine() const { return ScrollLine; }

	/** How many text lines the HUD panel shows at once - GetScrollLine()
	 *  clamps so the last page never scrolls past the final line. */
	static constexpr int32 VisibleLines = 20;

private:
	TArray<FString> Lines;
	int32 ScrollLine = 0;
};
