#include "ODCreditsMenu.h"

#include "Misc/FileHelper.h"
#include "Misc/Paths.h"

AODCreditsMenu::AODCreditsMenu()
{
	PrimaryActorTick.bCanEverTick = false;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODCreditsMenu::BeginPlay()
{
	Super::BeginPlay();

	// Same Content/OpenDrive/Masks convention every other sidecar in this
	// project loads from (centerline.bin, labels_*.bin, ambient_zones_*.bin)
	// - works identically in packaged builds since Content/ is cooked the
	// same way regardless of file format inside it.
	const FString Path = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks/credits.txt");
	if (!FFileHelper::LoadFileToStringArray(Lines, *Path))
	{
		Lines.Reset();
		Lines.Add(TEXT("(credits.txt missing - run pipeline/build_credits.py)"));
		UE_LOG(LogTemp, Warning, TEXT("ODCreditsMenu: could not load %s"), *Path);
		return;
	}
	UE_LOG(LogTemp, Display, TEXT("ODCreditsMenu: loaded %d line(s) from %s"), Lines.Num(), *Path);
}

void AODCreditsMenu::Scroll(int32 Dir)
{
	const int32 MaxScroll = FMath::Max(0, Lines.Num() - VisibleLines);
	ScrollLine = FMath::Clamp(ScrollLine + Dir, 0, MaxScroll);
}
