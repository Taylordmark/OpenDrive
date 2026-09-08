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

float FParsedAmbientZoneGrid::BilinearSample(const TArray<uint16>& Field, int32 InCols, int32 InRows,
	int32 InOriginXCm, int32 InOriginYCm, float InCellSizeCm, float WorldXCm, float WorldYCm)
{
	// Bilinear across the 4 cells surrounding (WorldXCm,WorldYCm)'s cell-
	// center-relative position - see header comment on why the water
	// layers interpolate while SampleNearest doesn't. Shared by
	// SampleLakeDistM/SampleWaterwayDistM below (identical math, different
	// field - split from Water into Lake/Waterway next steps.md SS7.29).
	const float Fx = (WorldXCm - InOriginXCm) / InCellSizeCm - 0.5f;
	const float Fy = (WorldYCm - InOriginYCm) / InCellSizeCm - 0.5f;
	const int32 X0 = FMath::Clamp(FMath::FloorToInt(Fx), 0, InCols - 1);
	const int32 Y0 = FMath::Clamp(FMath::FloorToInt(Fy), 0, InRows - 1);
	const int32 X1 = FMath::Clamp(X0 + 1, 0, InCols - 1);
	const int32 Y1 = FMath::Clamp(Y0 + 1, 0, InRows - 1);
	const float Tx = FMath::Clamp(Fx - X0, 0.0f, 1.0f);
	const float Ty = FMath::Clamp(Fy - Y0, 0.0f, 1.0f);

	const float D00 = Field[Y0 * InCols + X0];
	const float D10 = Field[Y0 * InCols + X1];
	const float D01 = Field[Y1 * InCols + X0];
	const float D11 = Field[Y1 * InCols + X1];
	const float Top = FMath::Lerp(D00, D10, Tx);
	const float Bottom = FMath::Lerp(D01, D11, Tx);
	return FMath::Lerp(Top, Bottom, Ty);
}

float FParsedAmbientZoneGrid::SampleLakeDistM(float WorldXCm, float WorldYCm) const
{
	if (!bValid || Cols <= 0 || Rows <= 0)
	{
		return TNumericLimits<float>::Max();
	}
	return BilinearSample(LakeDistM, Cols, Rows, OriginXCm, OriginYCm, CellSizeCm, WorldXCm, WorldYCm);
}

float FParsedAmbientZoneGrid::SampleWaterwayDistM(float WorldXCm, float WorldYCm) const
{
	if (!bValid || Cols <= 0 || Rows <= 0)
	{
		return TNumericLimits<float>::Max();
	}
	return BilinearSample(WaterwayDistM, Cols, Rows, OriginXCm, OriginYCm, CellSizeCm, WorldXCm, WorldYCm);
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
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'A')
	{
		UE_LOG(LogTemp, Warning, TEXT("ODAmbientSoundManager: could not load/parse '%s' - "
			"ambient audio disabled for this session"), *Path);
		return false;
	}
	if (Bin[3] != '2')
	{
		// ODA1 (pre next steps.md SS7.29) has a different per-cell record
		// layout - one combined uint16 WaterDistM field instead of the two
		// (LakeDistM, WaterwayDistM) ODA2 needs, so every byte after the
		// header would misalign if read as ODA2. Reject cleanly rather than
		// misread: same "log once, ambient audio just stays off" failure
		// mode as any other parse failure here, not a crash/corrupt-read.
		UE_LOG(LogTemp, Warning, TEXT("ODAmbientSoundManager: '%s' is format 'ODA%c', this build only "
			"reads ODA2 (re-run pipeline/build_ambient_zones.py) - ambient audio disabled for this session"),
			*Path, (TCHAR)Bin[3]);
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
	// uint8 zone + uint8 density + uint16 lakedist + uint16 waterwaydist + uint8 reserved = 7 bytes/cell (ODA2)
	const int64 NeedBytes = NumCells * 7;
	if (Grid.Cols <= 0 || Grid.Rows <= 0 || Remaining() < NeedBytes)
	{
		UE_LOG(LogTemp, Warning, TEXT("ODAmbientSoundManager: '%s' truncated/invalid grid header"), *Path);
		return false;
	}

	Grid.Zone.SetNumUninitialized(NumCells);
	Grid.Density255.SetNumUninitialized(NumCells);
	Grid.LakeDistM.SetNumUninitialized(NumCells);
	Grid.WaterwayDistM.SetNumUninitialized(NumCells);
	for (int64 i = 0; i < NumCells; ++i, Cur += 7)
	{
		Grid.Zone[i] = Cur[0];
		Grid.Density255[i] = Cur[1];
		Grid.LakeDistM[i] = (uint16)(Cur[2] | (Cur[3] << 8));
		Grid.WaterwayDistM[i] = (uint16)(Cur[4] | (Cur[5] << 8));
		// Cur[6] reserved, intentionally unread.
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
	LayerCreek.Sound = CreekSound;
	LayerCreek.Comp = SpawnAmbientLoop(this, CreekSound);
	LayerLake.Sound = LakeSound;
	LayerLake.Comp = SpawnAmbientLoop(this, LakeSound);

	int32 NumReady = 0;
	for (const FODAmbientLayer* L : { &LayerForest, &LayerAlpine, &LayerSettlement, &LayerCreek, &LayerLake })
	{
		if (L->Comp) { ++NumReady; }
	}
	UE_LOG(LogTemp, Display, TEXT("ODAmbientSoundManager: %d/5 ambient layer(s) have a sound assigned "
		"and playing (unset ones stay silent - see AmbientZoneFile's ForestSound/AlpineWindSound/"
		"SettlementHumSound/CreekSound/LakeSound properties)"), NumReady);
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
	LoadDefaultSound(CreekSound, TEXT("/Game/OpenDrive/Audio/Ambient/SW_CreekBabble.SW_CreekBabble"));
	LoadDefaultSound(LakeSound, TEXT("/Game/OpenDrive/Audio/Ambient/SW_LakeShore.SW_LakeShore"));

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
	const float LakeDistM = Grid.SampleLakeDistM(Loc.X, Loc.Y);
	const float WaterwayDistM = Grid.SampleWaterwayDistM(Loc.X, Loc.Y);

	// Alpine layer doubles as the base "open" wind bed (see class comment -
	// ZONE_OPEN plays a plain wind bed too), so it's targeted at full volume
	// for both Open and Alpine zones, and left up (not zeroed) under Forest/
	// Settlement too - a forest or town patch still has wind, the other
	// layer just adds on top of it.
	LayerAlpine.TargetVolume = 1.0f;
	LayerForest.TargetVolume = (Zone == EODAmbientZone::Forest) ? 1.0f : 0.0f;
	LayerSettlement.TargetVolume = (Zone == EODAmbientZone::Settlement)
		? FMath::Clamp(Density / 255.0f, 0.0f, 1.0f) : 0.0f;

	// Independent linear fade-in over the last WaterFadeInStartM meters of
	// approach for EACH water kind - 0 at/beyond WaterFadeInStartM, 1 at
	// that feature's own edge (0 m). Not mutually exclusive: at a creek-
	// into-reservoir confluence both can be near zero distance at once, so
	// both layers can be audible together - no special-casing, their
	// independent crossfades already produce the right blend (next steps.md
	// SS7.29).
	LayerCreek.TargetVolume = FMath::Clamp(1.0f - WaterwayDistM / WaterFadeInStartM, 0.0f, 1.0f);
	LayerLake.TargetVolume = FMath::Clamp(1.0f - LakeDistM / WaterFadeInStartM, 0.0f, 1.0f);
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
	for (FODAmbientLayer* L : { &LayerForest, &LayerAlpine, &LayerSettlement, &LayerCreek, &LayerLake })
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
			TEXT("ODAmbientSoundManager: vol forest=%.2f alpine=%.2f settlement=%.2f creek=%.2f lake=%.2f"),
			LayerForest.CurrentVolume, LayerAlpine.CurrentVolume,
			LayerSettlement.CurrentVolume, LayerCreek.CurrentVolume, LayerLake.CurrentVolume);
	}
}
