#include "ODRaceHUD.h"

#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/Font.h"
#include "EngineUtils.h"
#include "GameFramework/Pawn.h"
#include "ODRaceManager.h"
#include "ODTuningManager.h"

static FString FormatTime(float T)
{
	const int32 Mins = FMath::FloorToInt(T / 60.0f);
	return FString::Printf(TEXT("%d:%05.2f"), Mins, T - Mins * 60.0f);
}

void AODRaceHUD::DrawHUD()
{
	Super::DrawHUD();

	AODRaceManager* Race = nullptr;
	for (TActorIterator<AODRaceManager> It(GetWorld()); It; ++It)
	{
		Race = *It;
		break;
	}
	if (!Race || !Canvas)
	{
		return;
	}

	UFont* Font = GEngine->GetLargeFont();
	const float X = Canvas->SizeX * 0.5f - 90.0f;
	float Y = 30.0f;

	auto Line = [&](const FString& Text, const FLinearColor& Color, float Scale)
	{
		FCanvasTextItem Item(FVector2D(X, Y), FText::FromString(Text), Font, Color);
		Item.Scale = FVector2D(Scale, Scale);
		Item.EnableShadow(FLinearColor::Black);
		Canvas->DrawItem(Item);
		Y += 26.0f * Scale;
	};

	const float Now = GetWorld()->GetTimeSeconds();
	if (Race->bRunning)
	{
		Line(FormatTime(Race->ElapsedTime()), FLinearColor::White, 2.0f);
		Line(FString::Printf(TEXT("gate %d / %d"), Race->NextGate,
			Race->Checkpoints.Num()), FLinearColor(1, 1, 0.3f), 1.2f);
		if (Race->ElapsedTime() < 1.5f)
		{
			Line(TEXT("GO!"), FLinearColor(0.3f, 1, 0.3f), 2.4f);
		}
		else if (Now - Race->SplitShownAt < 3.0f)
		{
			// sector split vs best run: green = ahead, red = behind
			const bool bAhead = Race->SplitDelta <= 0.0f;
			Line(FString::Printf(TEXT("%+.2f"), Race->SplitDelta),
				bAhead ? FLinearColor(0.3f, 1, 0.3f) : FLinearColor(1, 0.35f, 0.3f),
				1.6f);
		}
	}
	else
	{
		Line(TEXT("cross the green gate to start"), FLinearColor(0.4f, 1, 0.4f), 1.2f);
		if (Race->LastTime > 0.0f)
		{
			Line(FString::Printf(TEXT("last  %s"), *FormatTime(Race->LastTime)),
				FLinearColor::White, 1.2f);
		}
	}
	if (Race->BestTime > 0.0f)
	{
		Line(FString::Printf(TEXT("best  %s"), *FormatTime(Race->BestTime)),
			FLinearColor(0.5f, 0.8f, 1), 1.2f);
	}

	// tuning preset toast (T to cycle)
	for (TActorIterator<AODTuningManager> It(GetWorld()); It; ++It)
	{
		if (Now - It->SwapShownAt < 3.0f && !It->CurrentPresetName.IsEmpty())
		{
			FCanvasTextItem Toast(
				FVector2D(Canvas->SizeX * 0.5f - 60.0f, Canvas->SizeY - 130.0f),
				FText::FromString(It->CurrentPresetName), Font,
				FLinearColor(1.0f, 0.8f, 0.3f));
			Toast.Scale = FVector2D(1.6f, 1.6f);
			Toast.EnableShadow(FLinearColor::Black);
			Canvas->DrawItem(Toast);
		}
		break;
	}

	if (const APawn* Pawn = GetOwningPawn())
	{
		const float Mph = Pawn->GetVelocity().Size() * 0.02237f;
		FCanvasTextItem Speed(FVector2D(Canvas->SizeX - 200.0f, Canvas->SizeY - 80.0f),
			FText::FromString(FString::Printf(TEXT("%.0f mph"), Mph)),
			Font, FLinearColor::White);
		Speed.Scale = FVector2D(1.8f, 1.8f);
		Speed.EnableShadow(FLinearColor::Black);
		Canvas->DrawItem(Speed);
	}
}
