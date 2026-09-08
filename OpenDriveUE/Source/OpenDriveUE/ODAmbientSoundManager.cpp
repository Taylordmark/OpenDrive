#include "ODAmbientSoundManager.h"

#include "Components/AudioComponent.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "ODBinSidecarUtil.h"
#include "Sound/SoundBase.h"

bool FParsedAmbientZoneGrid::SampleNearest(float WorldXCm, float WorldYCm, EODAmbientZone& OutZone, uint8& OutDensity) const
{
	if (!bValid || Cols <= 0 || Rows <= 0)
	{
		OutZone = EODAmbientZone::Open;
		OutDensity = 0;
		return false;
	}
	const int32 Cx = FMath::Clamp(FMath::FloorToInt((WorldXCm - OriginXCm) / CellSizeCm), 0, Cols - 1);
	const int32 Cy = FMath::Clamp(FMath::FloorToInt((WorldYCm - OriginYCm) / CellSizeCm), 0, Rows - 1);
	const int32 Idx = Cy * Cols + Cx;
	OutZone = static_cast<EODAmbientZone>(Zone[Idx]);
	OutDensity = Density255[Idx];
	return true;
}

float FParsedAmbientZoneGrid::SampleWaterDistM(float WorldXCm, float WorldYCm) const
{
	if (!bValid || Cols <= 0 || Rows <= 0)
	{
		return TNumericLimits<float>::Max();
	}
	// Bilinear across the 4 cells surrounding (WorldXCm,WorldYCm)'s cell-
	// center-relative position - see header comment on why this layer
	// interpolates while SampleNearest above doesn't.
	const float Fx = (WorldXCm - OriginXCm) / CellSizeCm - 0.5f;
	const float Fy = (WorldYCm - OriginYCm) / CellSizeCm - 0.5f;
	const int32 X0 = FMath::Clamp(FMath::FloorToInt(Fx), 0, Cols - 1);
	const int32 Y0 = FMath::Clamp(FMath::FloorToInt(Fy), 0, Rows - 1);
	const int32 X1 = FMath::Clamp(X0 + 1, 0, Cols - 1);
	const int32 Y1 = FMath::Clamp(Y0 + 1, 0, Rows - 1);
	const float Tx = FMath::Clamp(Fx - X0, 0.0f, 1.0f);
	const float Ty = FMath::Clamp(Fy - Y0, 0.0f, 1.0f);

	const float D00 = WaterDistM[Y0 * Cols + X0];
	const float D10 = WaterDistM[Y0 * Cols + X1];
	const float D01 = WaterDistM[Y1 * Cols + X0];
	const float D11 = WaterDistM[Y1 * Cols + X1];
	const float Top = FMath::Lerp(D00, D10, Tx);
	const float Bottom = FMath::Lerp(D01, D11, Tx);
	return FMath::Lerp(Top, Bottom, Ty);
}

AODAmbientSoundManager::AODAmbientSoundManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

bool AODAmbientSoundManager::LoadZoneGrid()
{
	const FString Path = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ (AmbientZoneFile + TEXT(".bin"));

	TArray<uint8> Bin;
	if (!FFileHelper::LoadFileToArray(Bin, *Path) || Bin.Num() < 4 + GenerationIdBytes + 20
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'A' || Bin[3] != '1')
	{
		UE_LOG(LogTemp, Warning, TEXT("ODAmbientSoundManager: could not load/parse '%s' - "
			"ambient audio disabled for this session"), *Path);
		return false;
	}

	const uint8* Cur = Bin.GetData() + 4;
	const uint8* End = Bin.GetData() + Bin.Num();
	auto Remaining = [&]() -> int64 { return (int64)(End - Cur); };

	Grid.GenerationId = ReadGenerationId(Cur);
	Cur += GenerationIdBytes;

	Grid.OriginXCm = (int32)ReadU32LE(Cur); Cur += 4;
	Grid.OriginYCm = (int32)ReadU32LE(Cur); Cur += 4;
	Grid.Cols = (int32)ReadU32LE(Cur); Cur += 4;
	Grid.Rows = (int32)ReadU32LE(Cur); Cur += 4;
	Grid.CellSizeCm = ReadF32LE(Cur); Cur += 4;

	const int64 NumCells = (int64)Grid.Cols * Grid.Rows;
	const int64 NeedBytes = NumCells * 5; // uint8 zone + uint8 density + uint16 waterdist + uint8 reserved
	if (Grid.Cols <= 0 || Grid.Rows <= 0 || Remaining() < NeedBytes)
	{
		UE_LOG(LogTemp, Warning, TEXT("ODAmbientSoundManager: '%s' truncated/invalid grid header"), *Path);
		return false;
	}

	Grid.Zone.SetNumUninitialized(NumCells);
	Grid.Density255.SetNumUninitialized(NumCells);
	Grid.WaterDistM.SetNumUninitialized(NumCells);
	for (int64 i = 0; i < NumCells; ++i, Cur += 5)
	{
		Grid.Zone[i] = Cur[0];
		Grid.Density255[i] = Cur[1];
		Grid.WaterDistM[i] = (uint16)(Cur[2] | (Cur[3] << 8));
		// Cur[4] reserved, intentionally unread.
	}
	Grid.bValid = true;

	UE_LOG(LogTemp, Display, TEXT("ODAmbientSoundManager: loaded '%s' (%dx%d cells @ %.0f cm, gen=%s)"),
		*Path, Grid.Cols, Grid.Rows, Grid.CellSizeCm, *Grid.GenerationId);
	return true;
}

static UAudioComponent* SpawnAmbientLoop(AActor* Owner, USoundBase* Sound)
{
	if (!Sound)
	{
		return nullptr;
	}
	// Non-spatialized (2D) - these are ambience BEDS meant to surround the
	// player uniformly (wind/town-hum/water-babble), not a localized point
	// source, so plain SpawnSound2D is the right playback shape here (the
	// only other engine-audio actor in this project, ODVehicleAudio, uses
	// procedural synth components instead of SoundBase playback at all -
	// there's no existing SpawnSoundAttached convention to follow).
	UAudioComponent* Comp = UGameplayStatics::SpawnSound2D(Owner, Sound,
		/*VolumeMultiplier=*/0.0f, /*PitchMultiplier=*/1.0f, /*StartTime=*/0.0f,
		/*ConcurrencySettings=*/nullptr, /*bPersistAcrossLevelTransition=*/false,
		/*bAutoDestroy=*/false);
	if (Comp)
	{
		Comp->bIsUISound = false;
	}
	return Comp;
}

void AODAmbientSoundManager::EnsureLayers()
{
	LayerForest.Sound = ForestSound;
	LayerForest.Comp = SpawnAmbientLoop(this, ForestSound);
	LayerAlpine.Sound = AlpineWindSound;
	LayerAlpine.Comp = SpawnAmbientLoop(this, AlpineWindSound);
	LayerSettlement.Sound = SettlementHumSound;
	LayerSettlement.Comp = SpawnAmbientLoop(this, SettlementHumSound);
	LayerWater.Sound = WaterSound;
	LayerWater.Comp = SpawnAmbientLoop(this, WaterSound);

	int32 NumReady = 0;
	for (const FODAmbientLayer* L : { &LayerForest, &LayerAlpine, &LayerSettlement, &LayerWater })
	{
		if (L->Comp) { ++NumReady; }
	}
	UE_LOG(LogTemp, Display, TEXT("ODAmbientSoundManager: %d/4 ambient layer(s) have a sound assigned "
		"and playing (unset ones stay silent - see AmbientZoneFile's ForestSound/AlpineWindSound/"
		"SettlementHumSound/WaterSound properties)"), NumReady);
}

void AODAmbientSoundManager::BeginPlay()
{
	Super::BeginPlay();
	bGridLoaded = LoadZoneGrid();

	// Default sound assets - same LoadObject-in-BeginPlay convention as
	// AODEmbankmentManager::EmbankMat. This manager is find-or-spawned by
	// ODGameMode::EnsureAmbientSoundManager with no Blueprint defaults to
	// carry per-instance overrides, so a hardcoded content path is what
	// actually populates these four properties at runtime; a hand-placed
	// EditAnywhere instance in a level can still override any of them,
	// since a value already set here is left alone (see LoadDefaultSound
	// below). Assets are imported by unreal/import_ambient_audio.py from
	// content_src/audio/ambient/normalized/*.wav - see that script and
	// content_src/audio/ambient/manifest.json for sourcing/licensing.
	auto LoadDefaultSound = [](TObjectPtr<USoundBase>& Target, const TCHAR* Path)
	{
		if (!Target)
		{
			Target = LoadObject<USoundBase>(nullptr, Path);
		}
	};
	LoadDefaultSound(ForestSound, TEXT("/Game/OpenDrive/Audio/Ambient/SW_ForestWind.SW_ForestWind"));
	LoadDefaultSound(AlpineWindSound, TEXT("/Game/OpenDrive/Audio/Ambient/SW_AlpineWind.SW_AlpineWind"));
	LoadDefaultSound(SettlementHumSound, TEXT("/Game/OpenDrive/Audio/Ambient/SW_TownHum.SW_TownHum"));
	LoadDefaultSound(WaterSound, TEXT("/Game/OpenDrive/Audio/Ambient/SW_CreekBabble.SW_CreekBabble"));

	EnsureLayers();
}

void AODAmbientSoundManager::SampleAndRetarget()
{
	const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Player || !bGridLoaded)
	{
		return;
	}
	const FVector Loc = Player->GetActorLocation();

	EODAmbientZone Zone = EODAmbientZone::Open;
	uint8 Density = 0;
	Grid.SampleNearest(Loc.X, Loc.Y, Zone, Density);
	const float WaterDistM = Grid.SampleWaterDistM(Loc.X, Loc.Y);

	// Alpine layer doubles as the base "open" wind bed (see class comment -
	// ZONE_OPEN plays a plain wind bed too), so it's targeted at full volume
	// for both Open and Alpine zones, and left up (not zeroed) under Forest/
	// Settlement too - a forest or town patch still has wind, the other
	// layer just adds on top of it.
	LayerAlpine.TargetVolume = 1.0f;
	LayerForest.TargetVolume = (Zone == EODAmbientZone::Forest) ? 1.0f : 0.0f;
	LayerSettlement.TargetVolume = (Zone == EODAmbientZone::Settlement)
		? FMath::Clamp(Density / 255.0f, 0.0f, 1.0f) : 0.0f;

	// Linear fade-in over the last WaterFadeInStartM meters of approach -
	// 0 at/beyond WaterFadeInStartM, 1 at the water's edge (0 m).
	LayerWater.TargetVolume = FMath::Clamp(1.0f - WaterDistM / WaterFadeInStartM, 0.0f, 1.0f);
}

void AODAmbientSoundManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	TimeSinceSample += DeltaSeconds;
	const float SamplePeriod = SampleHz > 0.0f ? 1.0f / SampleHz : 0.5f;
	if (TimeSinceSample >= SamplePeriod)
	{
		TimeSinceSample = 0.0f;
		SampleAndRetarget();
	}

	// Smooth every layer's CurrentVolume toward TargetVolume every frame
	// (not just on a sample tick) so the crossfade itself reads as
	// continuous audio, not a stepped volume change every SamplePeriod.
	for (FODAmbientLayer* L : { &LayerForest, &LayerAlpine, &LayerSettlement, &LayerWater })
	{
		if (!L->Comp)
		{
			continue;
		}
		L->CurrentVolume = FMath::FInterpTo(L->CurrentVolume, L->TargetVolume, DeltaSeconds,
			1.0f / FMath::Max(CrossfadeSeconds, 0.01f));
		L->Comp->SetVolumeMultiplier(L->CurrentVolume);
		if (!L->Comp->IsPlaying() && L->Sound)
		{
			L->Comp->Play();
		}
	}

	if (const double Now = GetWorld()->GetTimeSeconds(); Now - LastDiagTime > 5.0)
	{
		LastDiagTime = Now;
		UE_LOG(LogTemp, Display,
			TEXT("ODAmbientSoundManager: vol forest=%.2f alpine=%.2f settlement=%.2f water=%.2f"),
			LayerForest.CurrentVolume, LayerAlpine.CurrentVolume,
			LayerSettlement.CurrentVolume, LayerWater.CurrentVolume);
	}
}
