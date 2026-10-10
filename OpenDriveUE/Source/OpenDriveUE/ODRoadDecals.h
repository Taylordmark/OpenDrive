#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "ODRoadDecals.generated.h"

class UDecalComponent;
class UMaterialInstanceDynamic;
class UPrimitiveComponent;
struct FParsedChunk;

/** One road decal, in road-manager actor space (= the chunk vertex frame). */
struct FODRoadDecalSpot
{
	FVector Pos = FVector::ZeroVector;   // cm, on the pavement
	FVector Down = -FVector::UpVector;   // projection direction (unit)
	FVector Along = FVector::ForwardVector;   // decal U / atlas x (unit)
	float HalfAcrossCm = 0.0f;
	float HalfAlongCm = 0.0f;
	int32 Kind = 0;                      // atlas cell: 0 crack, 1 patch, 2 manhole, 3 tire marks
	float Intensity = 1.0f;              // opacity scale
};

/**
 * Realistic-only road wear decals (PLAN-VISUALS Track C): cracks, patches,
 * manholes and tire marks in corners, all from one atlas material
 * (/Game/OpenDrive/Realistic/Road/M_RoadDecal_real, built by
 * unreal/apply_road_materials_real.py). AODRoadManager creates this only
 * when ODVisualStyle::IsRealistic() and feeds it each chunk as it's built.
 *
 * Placement is a pure function of the chunk's own data (PlaceSpots: slots
 * every DecalSlotM of arc length, hashed with a seed from the chunk's first
 * vertex), so a tile that streams out and back gets the same decals.
 * Spawned once per chunk build, never per frame. Mirrored offline by
 * pipeline/road_decals.py (determinism, spacing, density check).
 *
 * The decal material reads "Wetness" (pushed with the road's), and a
 * chunk's decals hide while its snow depth (Custom Primitive Data 0) is
 * over SnowHideCm - DBuffer decals would otherwise draw over the snow.
 */
UCLASS()
class OPENDRIVEUE_API UODRoadDecals : public UActorComponent
{
	GENERATED_BODY()

public:
	UODRoadDecals();

	/** Deterministic decals for one road chunk (empty for unpaved, non-ODR3
	 *  or too-short chunks). */
	static void PlaceSpots(const FParsedChunk& Chunk, TArray<FODRoadDecalSpot>& Out);

	void AddChunk(const FIntPoint& TileKey, const FParsedChunk& Chunk, UPrimitiveComponent* ChunkComp);
	void ReleaseTile(const FIntPoint& TileKey);
	void SetWetness(float Wetness);

	virtual void TickComponent(float DeltaTime, ELevelTick TickType,
		FActorComponentTickFunction* ThisTickFunction) override;

private:
	struct FChunkDecals
	{
		TWeakObjectPtr<UPrimitiveComponent> Chunk;
		TArray<TWeakObjectPtr<UDecalComponent>> Decals;
		bool bHidden = false;
	};

	void UpdateSnowHidden(FChunkDecals& Entry);

	TMap<FIntPoint, TArray<FChunkDecals>> Tiles;

	UPROPERTY()
	TObjectPtr<UMaterialInstanceDynamic> Mat;

	bool bTriedLoad = false;
};
