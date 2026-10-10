#include "ODGroundCover.h"

#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "HAL/PlatformTime.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/MaterialInterface.h"

namespace ODGroundCover
{
	// Per EODBiome, in enum order: grass, weed, shrub, pebble density scale,
	// then dryness (0 lush green .. 1 straw; also the sagebrush share of shrubs).
	static const float BiomeTable[14][5] = {
		/* DeciduousForest */ {0.50f, 0.80f, 0.60f, 0.30f, 0.05f},
		/* EvergreenForest */ {0.35f, 0.50f, 0.60f, 0.50f, 0.10f},
		/* Chaparral       */ {0.40f, 0.30f, 0.90f, 0.80f, 0.65f},
		/* DesertScrub     */ {0.30f, 0.15f, 0.70f, 1.00f, 0.90f},
		/* PlainsGrassland */ {1.00f, 0.70f, 0.30f, 0.20f, 0.30f},
		/* Wetland         */ {1.00f, 0.90f, 0.50f, 0.10f, 0.00f},
		/* AlpineBare      */ {0.15f, 0.15f, 0.10f, 1.00f, 0.40f},
		/* ArcticTundra    */ {0.20f, 0.20f, 0.20f, 0.60f, 0.50f},
		/* Farmland        */ {0.80f, 0.80f, 0.30f, 0.20f, 0.25f},
		/* Savanna         */ {1.00f, 0.40f, 0.40f, 0.40f, 0.70f},
		/* Rainforest      */ {0.60f, 1.00f, 0.80f, 0.10f, 0.00f},
		/* CoastalDune     */ {0.50f, 0.10f, 0.30f, 0.20f, 0.60f},
		/* CanyonBadlands  */ {0.15f, 0.10f, 0.40f, 1.00f, 0.80f},
		/* RockyFoothill   */ {0.40f, 0.30f, 0.40f, 0.90f, 0.35f},
	};
	static constexpr float PerSqM[4] = {0.30f, 0.12f, 0.015f, 0.10f};   // grass, weed, shrub, pebble at Model Detail High
	static constexpr float DetailDensity[3] = {0.5f, 1.0f, 1.6f};       // Standard, High, Ultra
	static constexpr int32 BaseCap[NumSlots] = {96, 48, 6, 6, 40};       // per 16 m cell at High
	static constexpr float BandFullCm = 3000.0f;   // full density this far past the pavement edge
	static constexpr float BandEndCm = 6000.0f;    // ...fading to FillFrac here and beyond
	static constexpr float FillFrac = 0.2f;
	static constexpr float ShrubClearCm = 150.0f;  // shrubs stay this much further off the shoulder

	int32 CapFor(int32 Slot, int32 Detail)
	{
		return FMath::CeilToInt(BaseCap[Slot] * DetailDensity[FMath::Clamp(Detail, 0, 2)]);
	}

	static uint32 CellSeed(const FIntPoint& K, uint32 Seed)
	{
		return ((uint32)K.X * 73856093u) ^ ((uint32)K.Y * 19349663u) ^ (Seed * 83492791u) ^ 0x6C0FE4u;
	}

	void Place(const FCell& C, TArray<FPlaced> (&Out)[NumSlots])
	{
		const float* B = BiomeTable[FMath::Min<int32>(C.Biome, 13)];
		const int32 D = FMath::Clamp(C.Detail, 0, 2);
		const float Area = FMath::Square(C.Step / 100.0);
		const float HalfDiag = C.Step * 0.7072;
		const int32 W = C.N + 1;
		FRandomStream Rng((int32)CellSeed(C.Key, C.Seed));
		for (int32 sy = 0; sy < C.N; ++sy)
		{
			for (int32 sx = 0; sx < C.N; ++sx)
			{
				const int32 i00 = sy * W + sx, i10 = i00 + 1, i01 = i00 + W, i11 = i01 + 1;
				if (!(C.Good[i00] && C.Good[i10] && C.Good[i01] && C.Good[i11]))
				{
					continue;
				}
				const float Z00 = C.Z[i00], Z10 = C.Z[i10], Z01 = C.Z[i01], Z11 = C.Z[i11];
				const float Slope = FVector2f((Z10 + Z11 - Z00 - Z01) * 0.5f, (Z01 + Z11 - Z00 - Z10) * 0.5f).Size() / C.Step;
				const float Road = C.RoadEdgeCm(C.X0 + (sx + 0.5) * C.Step, C.Y0 + (sy + 0.5) * C.Step);
				const float Band = FMath::Lerp(1.0f, FillFrac, FMath::Clamp((Road - BandFullCm) / (BandEndCm - BandFullCm), 0.0f, 1.0f));
				for (int32 k = 0; k < 4; ++k)
				{
					// cards and shrubs thin past ~6 deg and stop at ~31; pebbles hold to ~56
					const float Gate = k == 3 ? FMath::Clamp(1.5f - Slope, 0.0f, 1.0f) : FMath::Clamp(1.2f - 2.0f * Slope, 0.0f, 1.0f);
					const float Expected = PerSqM[k] * DetailDensity[D] * B[k] * Band * Gate * Area;
					const int32 Count = FMath::FloorToInt(Expected) + (Rng.FRand() < FMath::Frac(Expected) ? 1 : 0);
					for (int32 c = 0; c < Count; ++c)
					{
						const float U = Rng.FRand(), V = Rng.FRand(), Yaw = Rng.FRand() * 360.0f;
						const float S = Rng.FRand(), R1 = Rng.FRand(), R2 = Rng.FRand();
						const double X = C.X0 + (sx + U) * C.Step, Y = C.Y0 + (sy + V) * C.Step;
						const float Clear = C.ShoulderClearCm + (k == 2 ? ShrubClearCm : 0.0f);
						if (Road - HalfDiag < Clear && C.RoadEdgeCm(X, Y) < Clear)
						{
							continue;   // shoulder gravel strip
						}
						FPlaced P;
						P.Pos = FVector(X, Y, FMath::Lerp(FMath::Lerp(Z00, Z10, U), FMath::Lerp(Z01, Z11, U), V));
						P.Rot = FRotator(0.0f, Yaw, 0.0f);
						P.Dry = FMath::Clamp(B[4] + (R1 - 0.5f) * 0.6f, 0.0f, 1.0f);
						int32 Slot = Grass;
						if (k == 0)
						{
							P.Scale = FVector(0.7f + S * 0.6f);
						}
						else if (k == 1)
						{
							Slot = Weed;
							P.Scale = FVector(0.6f + S * 0.6f);
						}
						else if (k == 2)
						{
							Slot = R2 < B[4] ? Sage : Bush;
							P.Scale = FVector(0.3f + S * 0.3f);
						}
						else
						{
							Slot = Pebble;
							P.Rot = FRotator(R1 * 20.0f - 10.0f, Yaw, R2 * 20.0f - 10.0f);
							P.Scale = FVector(1.0f, 0.8f + R1 * 0.4f, 0.55f + R2 * 0.4f) * (0.06f + S * 0.22f);
						}
						Out[Slot].Add(P);
					}
				}
			}
		}
		// per-slot cap: an even stride subset, so a full cell thins all over
		// rather than losing its last rows
		for (int32 s = 0; s < NumSlots; ++s)
		{
			const int32 Cap = CapFor(s, D), Num = Out[s].Num();
			if (Num > Cap)
			{
				TArray<FPlaced> Kept;
				Kept.Reserve(Cap);
				for (int32 j = 0; j < Cap; ++j)
				{
					Kept.Add(Out[s][(int64)j * Num / Cap]);
				}
				Out[s] = MoveTemp(Kept);
			}
		}
	}
}

void FODGroundCoverAssets::Load(UObject* Outer)
{
	static const TCHAR* Paths[ODGroundCover::NumSlots] = {
		TEXT("/Game/OpenDrive/Realistic/GroundCover/SM_GCTallGrass_real.SM_GCTallGrass_real"),
		TEXT("/Game/OpenDrive/Realistic/GroundCover/SM_GCWeed_real.SM_GCWeed_real"),
		TEXT("/Game/OpenDrive/Trees/bush_real.bush_real"),           // Track F, small scale
		TEXT("/Game/OpenDrive/Trees/sagebrush_real.sagebrush_real"),
		TEXT("/Game/OpenDrive/Rocks/SM_RockBoulderA_real.SM_RockBoulderA_real"),   // non-Nanite Standard twin
	};
	Meshes.Reset();
	BaseZ.Reset();
	for (const TCHAR* Path : Paths)
	{
		UStaticMesh* Mesh = LoadObject<UStaticMesh>(nullptr, Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
		Meshes.Add(Mesh);
		BaseZ.Add(Mesh ? Mesh->GetBoundingBox().Min.Z : 0.0f);
	}
	PebbleMat = LoadObject<UMaterialInterface>(nullptr,
		TEXT("/Game/OpenDrive/Materials/M_Rock030_real.M_Rock030_real"), nullptr, LOAD_NoWarn | LOAD_Quiet);
	if (UMaterialInterface* Card = LoadObject<UMaterialInterface>(nullptr,
			TEXT("/Game/OpenDrive/Realistic/GroundCover/M_GroundCover_real.M_GroundCover_real"), nullptr, LOAD_NoWarn | LOAD_Quiet))
	{
		CardMID = UMaterialInstanceDynamic::Create(Card, Outer);
	}
	// unbuilt assets (unreal/build_ground_cover_real.py not run): stay off
	bOn = Meshes[ODGroundCover::Grass] && Meshes[ODGroundCover::Weed] && CardMID;
	UE_LOG(LogTemp, Display, TEXT("ODGroundCover: Realistic ground cover %s (grass %d weed %d bush %d sage %d pebble %d mat %d)"),
		bOn ? TEXT("on") : TEXT("OFF - run unreal/build_ground_cover_real.py"),
		Meshes[0] != nullptr, Meshes[1] != nullptr, Meshes[2] != nullptr, Meshes[3] != nullptr, Meshes[4] != nullptr,
		CardMID != nullptr);
}

void FODGroundCoverAssets::BuildCell(const ODGroundCover::FCell& C, USceneComponent* Root, float CullEndCm, float WpoDisableCm,
	TFunctionRef<void(UHierarchicalInstancedStaticMeshComponent*)> FoliageMat,
	TArray<UHierarchicalInstancedStaticMeshComponent*>& OutComps) const
{
	using namespace ODGroundCover;
	TArray<FPlaced> Out[NumSlots];
	Place(C, Out);

	int32 Total = 0;
	for (int32 s = 0; s < NumSlots; ++s)
	{
		if (Out[s].Num() == 0 || !Meshes[s])
		{
			continue;
		}
		const bool bCard = s == Grass || s == Weed;
		TArray<FTransform> Xf;
		Xf.Reserve(Out[s].Num());
		for (const FPlaced& P : Out[s])
		{
			// base on the ground, sunk a little so card roots and pebble edges don't float
			Xf.Emplace(P.Rot, P.Pos - FVector(0.0, 0.0, BaseZ[s] * P.Scale.Z + (s == Pebble ? 1.5 : 3.0)), P.Scale);
		}
		UHierarchicalInstancedStaticMeshComponent* Hism = NewObject<UHierarchicalInstancedStaticMeshComponent>(Root->GetOwner());
		Hism->SetupAttachment(Root);
		Hism->SetStaticMesh(Meshes[s]);
		if (bCard)
		{
			Hism->SetMaterial(0, CardMID);
			Hism->NumCustomDataFloats = 1;
		}
		else if (s == Pebble)
		{
			if (PebbleMat)
			{
				Hism->SetMaterial(0, PebbleMat);
			}
		}
		else
		{
			FoliageMat(Hism);
		}
		const float End = s == Pebble ? CullEndCm * 0.4f : CullEndCm;
		Hism->SetCullDistances(End * 0.5f, End);   // M_GroundCover_real fades cards over this band
		Hism->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		Hism->WorldPositionOffsetDisableDistance = WpoDisableCm;
		Hism->SetCastShadow(false);
		Hism->SetAffectDynamicIndirectLighting(false);
		Hism->RegisterComponent();
		Hism->AddInstances(Xf, false, /*bWorldSpace*/false);
		if (bCard)
		{
			for (int32 i = 0; i < Out[s].Num(); ++i)
			{
				Hism->SetCustomDataValue(i, 0, Out[s][i].Dry, false);
			}
			Hism->MarkRenderStateDirty();
		}
		OutComps.Add(Hism);
		Total += Out[s].Num();
	}

	// counts for the last 5 s (log only - placement never reads time)
	static double LastLog = 0.0;
	static int32 Cells = 0, MaxCell = 0, Slots[NumSlots] = {};
	++Cells;
	MaxCell = FMath::Max(MaxCell, Total);
	for (int32 s = 0; s < NumSlots; ++s)
	{
		Slots[s] += Out[s].Num();
	}
	if (const double Now = FPlatformTime::Seconds(); Now - LastLog > 5.0)
	{
		LastLog = Now;
		UE_LOG(LogTemp, Display,
			TEXT("ODGroundCover: %d cell(s) in 5s, grass %d weed %d bush %d sage %d pebble %d, max %d/cell "
			     "(caps %d/%d/%d/%d/%d at detail %d), cull %.0f m"),
			Cells, Slots[Grass], Slots[Weed], Slots[Bush], Slots[Sage], Slots[Pebble], MaxCell,
			CapFor(Grass, C.Detail), CapFor(Weed, C.Detail), CapFor(Bush, C.Detail), CapFor(Sage, C.Detail),
			CapFor(Pebble, C.Detail), C.Detail, CullEndCm / 100.0f);
		Cells = MaxCell = 0;
		FMemory::Memzero(Slots);
	}
}

void FODGroundCoverAssets::SyncSeason(const UMaterialInstanceDynamic* From) const
{
	if (!From || !CardMID)
	{
		return;
	}
	for (const TCHAR* Name : {TEXT("SnowCover"), TEXT("Wetness")})
	{
		float V;
		if (From->GetScalarParameterValue(FMaterialParameterInfo(Name), V))
		{
			CardMID->SetScalarParameterValue(Name, V);
		}
	}
}
