#include "ODForestManager.h"

#include "Components/CapsuleComponent.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/Texture2D.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"
#include "LandscapeProxy.h"
#include "PhysicalMaterials/PhysicalMaterial.h"
#include "UnrealClient.h"

namespace
{
const TCHAR* TreeMeshPaths[] = {
	TEXT("/Game/OpenDrive/Trees/tree_blob.tree_blob"),
	TEXT("/Game/OpenDrive/Trees/tree_olive.tree_olive"),
	TEXT("/Game/OpenDrive/Trees/tree_conifer.tree_conifer"),
};
}

AODForestManager::AODForestManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODForestManager::BeginPlay()
{
	Super::BeginPlay();

	for (const TCHAR* Path : TreeMeshPaths)
	{
		TreeMeshes.Add(LoadObject<UStaticMesh>(nullptr, Path));
	}
	BushMesh = LoadObject<UStaticMesh>(
		nullptr, TEXT("/Game/OpenDrive/Trees/bush.bush"));

	Pool.Reserve(PoolSize);
	for (int32 i = 0; i < PoolSize; ++i)
	{
		UCapsuleComponent* Cap = NewObject<UCapsuleComponent>(this);
		Cap->SetupAttachment(RootComponent);
		Cap->SetMobility(EComponentMobility::Movable);
		Cap->SetCapsuleSize(TrunkRadius, TrunkHalfHeight);
		Cap->SetCollisionProfileName(TEXT("BlockAll"));
		Cap->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		Cap->SetVisibility(false);
		Cap->RegisterComponent();
		Pool.Add(Cap);
	}

	bReady = LoadMask();
	if (!bReady)
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODForestManager: terrain mask unavailable - forest disabled"));
	}
}

bool AODForestManager::LoadMask()
{
#if WITH_EDITORONLY_DATA
	UTexture2D* Tex = LoadObject<UTexture2D>(
		nullptr, TEXT("/Game/OpenDrive/Textures/T_TerrainMask.T_TerrainMask"));
	if (!Tex || !Tex->Source.IsValid())
	{
		return false;
	}

	// mask footprint == landscape footprint (same square the material uses)
	const ALandscapeProxy* Landscape = nullptr;
	for (TActorIterator<ALandscapeProxy> It(GetWorld()); It; ++It)
	{
		Landscape = *It;
		break;
	}
	if (!Landscape)
	{
		return false;
	}
	const FVector LsLoc = Landscape->GetActorLocation();
	MaskCorner = FVector2D(LsLoc.X, LsLoc.Y);
	FVector Origin, Extent;
	Landscape->GetActorBounds(false, Origin, Extent);
	MaskSideCm = 2.0 * FMath::Max(Extent.X, Extent.Y);

	TArray64<uint8> Raw;
	if (!Tex->Source.GetMipData(Raw, 0))
	{
		return false;
	}
	const int32 SrcRes = Tex->Source.GetSizeX();
	const int32 Step = FMath::Max(1, SrcRes / 1024);   // ~9 m/px is plenty
	MaskRes = SrcRes / Step;
	MaskR.SetNumUninitialized(MaskRes * MaskRes);
	const int64 Bpp = Raw.Num() / ((int64)SrcRes * SrcRes);   // BGRA8 = 4
	for (int32 y = 0; y < MaskRes; ++y)
	{
		for (int32 x = 0; x < MaskRes; ++x)
		{
			const int64 Src = ((int64)y * Step * SrcRes + (int64)x * Step) * Bpp;
			MaskR[y * MaskRes + x] = Raw[Src + 2];     // BGRA -> R
		}
	}
	UE_LOG(LogTemp, Display,
		TEXT("ODForestManager: mask %dx%d (from %d), corner (%.0f, %.0f), side %.0f m"),
		MaskRes, MaskRes, SrcRes, MaskCorner.X, MaskCorner.Y, MaskSideCm / 100.0);
	return true;
#else
	return false;   // packaged builds: mask needs a cooked CPU copy (Arc 7)
#endif
}

float AODForestManager::ForestWeightAt(double WorldX, double WorldY) const
{
	const double U = (WorldX - MaskCorner.X) / MaskSideCm;
	const double V = (WorldY - MaskCorner.Y) / MaskSideCm;
	if (U < 0.0 || U >= 1.0 || V < 0.0 || V >= 1.0)
	{
		return 0.0f;
	}
	const int32 X = FMath::Clamp((int32)(U * MaskRes), 0, MaskRes - 1);
	const int32 Y = FMath::Clamp((int32)(V * MaskRes), 0, MaskRes - 1);
	return MaskR[Y * MaskRes + X] / 255.0f;
}

bool AODForestManager::GroundAt(double WorldX, double WorldY,
                                const AActor* Ignore, FVector& OutHit) const
{
	FHitResult Hit;
	const FVector Start(WorldX, WorldY, 100000.0);
	const FVector End(WorldX, WorldY, -100000.0);
	FCollisionQueryParams Params;
	Params.bReturnPhysicalMaterial = true;
	Params.AddIgnoredActor(this);          // our own trunk capsules
	if (Ignore)
	{
		Params.AddIgnoredActor(Ignore);    // the player's car
	}
	if (!GetWorld()->LineTraceSingleByChannel(Hit, Start, End,
			ECC_WorldStatic, Params))
	{
		return false;
	}
	// never plant a tree on pavement (road meshes carry PM_Asphalt)
	if (const UPhysicalMaterial* Pm = Hit.PhysMaterial.Get();
		Pm && Pm->GetName().Contains(TEXT("Asphalt")))
	{
		return false;
	}
	OutHit = Hit.ImpactPoint;
	return true;
}

void AODForestManager::BuildTile(const FIntPoint& Key, const AActor* Player)
{
	FTile& Tile = Tiles.Add(Key);
	FRandomStream Rng(HashCombine(GetTypeHash(Key), (uint32)RandomSeed));

	// per-tile dominant species = regional clumping (and the 7.3 hook)
	const float DomPick = Rng.FRand();
	const int32 Dominant = DomPick < 0.5f ? 0 : (DomPick < 0.8f ? 1 : 2);

	const double X0 = Key.X * (double)TileSizeCm;
	const double Y0 = Key.Y * (double)TileSizeCm;
	const float TileSqM = FMath::Square(TileSizeCm / 100.0f);
	const int32 NTrees = FMath::RoundToInt(TileSqM * TreesPerSqM);
	const int32 NBushes = FMath::RoundToInt(TileSqM * BushesPerSqM);

	// one transform batch per mesh (3 trees + bush)
	TArray<FTransform> Batches[4];

	for (int32 i = 0; i < NTrees + NBushes; ++i)
	{
		const bool bTree = i < NTrees;
		const double Px = X0 + Rng.FRand() * TileSizeCm;
		const double Py = Y0 + Rng.FRand() * TileSizeCm;
		const float Accept = Rng.FRand();
		const float Yaw = Rng.FRand() * 360.0f;
		const float Scale = bTree ? 0.9f + Rng.FRand() * 0.9f
		                          : 1.0f + Rng.FRand() * 1.0f;
		const float SpeciesPick = Rng.FRand();
		// RNG fully consumed above -> layout independent of accept/trace results
		if (Accept >= ForestWeightAt(Px, Py))
		{
			continue;
		}
		FVector Ground;
		if (!GroundAt(Px, Py, Player, Ground))
		{
			continue;
		}
		int32 MeshIdx = 3;                             // bush batch
		if (bTree)
		{
			// 70% dominant species, remainder split over the other two
			MeshIdx = SpeciesPick < 0.7f
				? Dominant
				: (SpeciesPick < 0.85f ? (Dominant + 1) % 3 : (Dominant + 2) % 3);
			Tile.Trunks.Add(Ground);
			Tile.TrunkScales.Add(Scale);
		}
		Batches[MeshIdx].Emplace(FRotator(0, Yaw, 0), Ground, FVector(Scale));
	}

	for (int32 m = 0; m < 4; ++m)
	{
		if (Batches[m].Num() == 0)
		{
			continue;
		}
		UStaticMesh* Mesh = m == 3 ? BushMesh.Get() : TreeMeshes[m].Get();
		if (!Mesh)
		{
			continue;
		}
		auto* Hism = NewObject<UHierarchicalInstancedStaticMeshComponent>(this);
		Hism->SetupAttachment(RootComponent);
		Hism->SetStaticMesh(Mesh);
		Hism->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		Hism->SetCullDistances(m == 3 ? 20000 : 45000, m == 3 ? 45000 : 60000);
		Hism->SetCastShadow(m != 3);
		Hism->RegisterComponent();
		Hism->AddInstances(Batches[m], false, true);
		Tile.Hisms.Add(Hism);
	}
}

void AODForestManager::ReleaseTile(const FIntPoint& Key)
{
	if (FTile* Tile = Tiles.Find(Key))
	{
		for (const auto& Hism : Tile->Hisms)
		{
			if (Hism)
			{
				Hism->DestroyComponent();
			}
		}
		Tiles.Remove(Key);
	}
}

void AODForestManager::UpdateCollision(const FVector& PlayerLoc)
{
	struct FNear { FVector Pos; float Scale; float DistSq; };
	TArray<FNear> Near;
	const float R2 = CollisionRadius * CollisionRadius;
	const FIntPoint Center(FMath::FloorToInt(PlayerLoc.X / TileSizeCm),
	                       FMath::FloorToInt(PlayerLoc.Y / TileSizeCm));
	const int32 Reach = FMath::CeilToInt(CollisionRadius / TileSizeCm);
	for (int32 dy = -Reach; dy <= Reach; ++dy)
	{
		for (int32 dx = -Reach; dx <= Reach; ++dx)
		{
			if (const FTile* Tile = Tiles.Find(Center + FIntPoint(dx, dy)))
			{
				for (int32 i = 0; i < Tile->Trunks.Num(); ++i)
				{
					const float D2 = FVector::DistSquared2D(Tile->Trunks[i], PlayerLoc);
					if (D2 < R2)
					{
						Near.Add({Tile->Trunks[i], Tile->TrunkScales[i], D2});
					}
				}
			}
		}
	}
	Near.Sort([](const FNear& A, const FNear& B) { return A.DistSq < B.DistSq; });

	for (int32 i = 0; i < Pool.Num(); ++i)
	{
		UCapsuleComponent* Cap = Pool[i];
		if (i < Near.Num())
		{
			Cap->SetWorldLocation(Near[i].Pos
				+ FVector(0, 0, TrunkHalfHeight * Near[i].Scale));
			Cap->SetWorldScale3D(FVector(Near[i].Scale));
			Cap->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
		}
		else
		{
			Cap->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		}
	}

	if (const double Now = GetWorld()->GetTimeSeconds(); Now - LastDiagTime > 5.0)
	{
		LastDiagTime = Now;
		int32 NumInst = 0;
		for (const auto& Pair : Tiles)
		{
			for (const auto& H : Pair.Value.Hisms)
			{
				NumInst += H ? H->GetInstanceCount() : 0;
			}
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODForestManager: tiles=%d instances=%d trunksNear=%d capsulesOn=%d"),
			Tiles.Num(), NumInst, Near.Num(), FMath::Min(Near.Num(), Pool.Num()));
	}
}

void AODForestManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	if (!bReady)
	{
		return;
	}
	const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Player)
	{
		return;
	}
	const FVector Loc = Player->GetActorLocation();

	// spawn missing tiles nearest-first, a few per tick
	const FIntPoint Center(FMath::FloorToInt(Loc.X / TileSizeCm),
	                       FMath::FloorToInt(Loc.Y / TileSizeCm));
	const int32 Reach = FMath::CeilToInt(SpawnRadius / TileSizeCm);
	int32 Built = 0;
	for (int32 Ring = 0; Ring <= Reach && Built < TilesPerTick; ++Ring)
	{
		for (int32 dy = -Ring; dy <= Ring && Built < TilesPerTick; ++dy)
		{
			for (int32 dx = -Ring; dx <= Ring && Built < TilesPerTick; ++dx)
			{
				if (FMath::Max(FMath::Abs(dx), FMath::Abs(dy)) != Ring)
				{
					continue;
				}
				const FIntPoint Key = Center + FIntPoint(dx, dy);
				if (!Tiles.Contains(Key))
				{
					BuildTile(Key, Player);
					++Built;
				}
			}
		}
	}

	// retire tiles far outside the radius (hysteresis: +2 tiles)
	TArray<FIntPoint> Gone;
	for (const auto& Pair : Tiles)
	{
		if (FMath::Max(FMath::Abs(Pair.Key.X - Center.X),
		               FMath::Abs(Pair.Key.Y - Center.Y)) > Reach + 2)
		{
			Gone.Add(Pair.Key);
		}
	}
	for (const FIntPoint& Key : Gone)
	{
		ReleaseTile(Key);
	}

	UpdateCollision(Loc);

	// headless visual QA: `-odshot=25` captures a screenshot 25 s after boot
	static float ShotAt = -1.0f;
	static bool bShotParsed = false, bShotDone = false;
	if (!bShotParsed)
	{
		bShotParsed = true;
		FParse::Value(FCommandLine::Get(), TEXT("odshot="), ShotAt);
	}
	if (ShotAt > 0.0f && !bShotDone && GetWorld()->GetTimeSeconds() > ShotAt)
	{
		bShotDone = true;
		FScreenshotRequest::RequestScreenshot(false);
		UE_LOG(LogTemp, Display, TEXT("ODForestManager: screenshot requested"));
	}
}
