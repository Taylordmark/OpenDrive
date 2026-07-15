#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODForestManager.generated.h"

class UCapsuleComponent;
class UHierarchicalInstancedStaticMeshComponent;
class UStaticMesh;

/**
 * Runtime forest (replaces landscape-grass trees, 2026-07-15): deterministic
 * seeded scatter of trees + understory bushes on 96 m tiles around the player,
 * driven by the terrain mask's forest weight. Owning the instances fixes what
 * landscape grass could not give us:
 *  - trunk collision (we know every transform; a capsule pool parks on the
 *    nearest trunks),
 *  - foliage in standalone/packaged builds (grass maps are editor-only),
 *  - the Arc 7.3 hook for regional flora recipes (per-tile species mix).
 * Zero per-instance storage: layout derives from tile-seeded RNG + the mask.
 * Each tile owns its HISM components, so retiring a tile is just component
 * teardown - no instance-id bookkeeping.
 */
UCLASS()
class OPENDRIVEUE_API AODForestManager : public AActor
{
	GENERATED_BODY()

public:
	AODForestManager();
	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

	/** Tiles fully populated inside this radius of the player (cm). */
	UPROPERTY(EditAnywhere, Category = "Forest")
	float SpawnRadius = 48000.0f;

	/** Trees per m^2 at forest weight 1 ("wall of trees" ~ every 3.7 m). */
	UPROPERTY(EditAnywhere, Category = "Forest")
	float TreesPerSqM = 0.075f;

	/** Understory bushes per m^2 at forest weight 1. */
	UPROPERTY(EditAnywhere, Category = "Forest")
	float BushesPerSqM = 0.03f;

	UPROPERTY(EditAnywhere, Category = "Forest")
	int32 RandomSeed = 20260715;

	// --- trunk collision (moved from the retired ODForestCollisionManager) ---
	UPROPERTY(EditAnywhere, Category = "Collision")
	int32 PoolSize = 48;

	UPROPERTY(EditAnywhere, Category = "Collision")
	float CollisionRadius = 6000.0f;

	UPROPERTY(EditAnywhere, Category = "Collision")
	float TrunkRadius = 26.0f;

	UPROPERTY(EditAnywhere, Category = "Collision")
	float TrunkHalfHeight = 380.0f;

private:
	struct FTile
	{
		TArray<TObjectPtr<UHierarchicalInstancedStaticMeshComponent>> Hisms;
		TArray<FVector> Trunks;        // world tree positions (collision)
		TArray<float> TrunkScales;
	};

	bool LoadMask();
	float ForestWeightAt(double WorldX, double WorldY) const;
	bool GroundAt(double WorldX, double WorldY, const AActor* Ignore,
	              FVector& OutHit) const;
	void BuildTile(const FIntPoint& Key, const AActor* Player);
	void ReleaseTile(const FIntPoint& Key);
	void UpdateCollision(const FVector& PlayerLoc);

	UPROPERTY()
	TArray<TObjectPtr<UStaticMesh>> TreeMeshes;

	UPROPERTY()
	TObjectPtr<UStaticMesh> BushMesh;

	UPROPERTY()
	TArray<TObjectPtr<UCapsuleComponent>> Pool;

	TMap<FIntPoint, FTile> Tiles;

	// terrain mask CPU copy (downsampled R channel = forest weight)
	TArray<uint8> MaskR;
	int32 MaskRes = 0;
	FVector2D MaskCorner = FVector2D::ZeroVector;   // world cm
	double MaskSideCm = 0.0;

	double LastDiagTime = 0.0;
	bool bReady = false;

	static constexpr float TileSizeCm = 9600.0f;    // 96 m
	static constexpr int32 TilesPerTick = 1;
};
