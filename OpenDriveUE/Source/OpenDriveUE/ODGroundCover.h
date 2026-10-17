#pragma once

#include "CoreMinimal.h"
#include "ODGroundCover.generated.h"

class UHierarchicalInstancedStaticMeshComponent;
class UMaterialInstanceDynamic;
class UMaterialInterface;
class UMaterialParameterCollection;
class UStaticMesh;

/**
 * Realistic-only ground cover (PLAN-VISUALS.md Track K): tall grass, weeds,
 * low shrubs and pebbles scattered on AODForestManager's grass cells (the
 * 16 m cells of the near-car grass carpet). It reuses that cell's traced
 * 2 m lattice, so pavement, pads, bridges, buildings, parking, cut walls
 * (embankment meshes) and lakes are already bare; this adds the shoulder
 * strip, a road band (full density to BandFullCm from the pavement edge,
 * down to FillFrac at BandEndCm and beyond), biome and slope.
 *
 * Place() is pure and seeded only from the cell key, so a cell always gets
 * the same scatter (no frame or time input). pipeline/checks/
 * check_ground_cover.py mirrors it line for line - change both together.
 * Never called in Stylized (AODForestManager only loads the assets when
 * ODVisualStyle::IsRealistic()).
 */
namespace ODGroundCover
{
	enum ESlot : int32 { Grass, Weed, Bush, Sage, Pebble, NumSlots };

	struct FCell
	{
		FIntPoint Key;
		double X0, Y0, Step;
		int32 N;
		const float* Z;      // (N+1)^2 lattice corner heights, [iy * (N + 1) + ix]
		const bool* Good;    // corner is on terrain (see the grass lattice)
		uint8 Biome;         // EODBiome (ODForestManager.h includes this header)
		int32 Detail;        // ODAssetDetail::Level()
		uint32 Seed;
		float ShoulderClearCm;
		TFunctionRef<float(double, double)> RoadEdgeCm;   // to the nearest pavement edge, huge if none
	};

	struct FPlaced
	{
		FVector Pos;         // ground point under the pivot
		FRotator Rot;
		FVector Scale;
		float Dry;           // per-instance custom data 0 (cards only): 0 lush .. 1 straw
	};

	/** Deterministic scatter for one cell, capped per slot (CapFor). */
	void Place(const FCell& C, TArray<FPlaced> (&Out)[NumSlots]);

	/** Density multiplier at a world XY for a placement class (0 grass,
	 *  1 weed, 2 shrub, 3 pebble): low-frequency clump noise, ~40% of the
	 *  ground empty, mean ~0.6, peaks ClumpGain. Pure function of position.
	 *  AODForestManager's grass carpet can multiply its per-square density by
	 *  Clump(X, Y, 0) so the tufts gather into the same clumps. */
	float Clump(double X, double Y, int32 Class);

	int32 CapFor(int32 Slot, int32 Detail);
}

/** Loaded once in AODForestManager::BeginPlay (Realistic only); UPROPERTY
 *  so the meshes and the card MID stay alive. */
USTRUCT()
struct FODGroundCoverAssets
{
	GENERATED_BODY()

	UPROPERTY()
	TArray<TObjectPtr<UStaticMesh>> Meshes;   // per ODGroundCover::ESlot, null = slot off

	UPROPERTY()
	TObjectPtr<UMaterialInterface> PebbleMat;

	/** M_GroundCover_real MID: AODForestManager copies SnowCover/Wetness
	 *  into it from the foliage season MID. */
	UPROPERTY()
	TObjectPtr<UMaterialInstanceDynamic> CardMID;

	/** MPC_Season (unreal/apply_terrain_real_materials.py): SyncSeason copies
	 *  SnowCover into it so M_Rock030_real (boulders, pebbles) gets snow on
	 *  its tops. Null = asset not built, rocks stay bare. */
	UPROPERTY()
	TObjectPtr<UMaterialParameterCollection> SeasonMPC;

	TWeakObjectPtr<UWorld> World;
	TArray<float> BaseZ;   // mesh bounds Min.Z per slot (pivot -> base)
	bool bOn = false;

	void Load(UObject* Outer);

	/** Place + spawn one cell's HISMs (no collision, no shadow, fade band
	 *  CullEndCm/2..CullEndCm, pebbles at a fraction of it). FoliageMat
	 *  gives shrubs the season foliage MID. Logs counts every 5 s. */
	void BuildCell(const ODGroundCover::FCell& C, USceneComponent* Root, float CullEndCm, float WpoDisableCm,
		TFunctionRef<void(UHierarchicalInstancedStaticMeshComponent*)> FoliageMat,
		TArray<UHierarchicalInstancedStaticMeshComponent*>& OutComps) const;

	/** Copy SnowCover / Wetness from the forest's season MID (and SnowCover
	 *  into SeasonMPC). */
	void SyncSeason(const UMaterialInstanceDynamic* From) const;
};
