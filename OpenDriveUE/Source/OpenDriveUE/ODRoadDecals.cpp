#include "ODRoadDecals.h"

#include "Components/DecalComponent.h"
#include "GameFramework/Actor.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/MaterialInterface.h"
#include "ODRoadManager.h"

namespace
{
// Mirrored by pipeline/road_decals.py, which parses these lines - keep the
// "constexpr float Name = 1.0f;" / "constexpr float Name[3] = {...};" form.
constexpr float DecalSlotM = 8.0f;          // one candidate decal per 8 m of arc length
constexpr float DecalSlotJitter = 0.25f;    // +- fraction of a slot along the road
// per km by class band: 0 major (motorway..primary), 1 secondary/tertiary, 2 minor
constexpr float CrackPerKm[3] = {8.0f, 12.0f, 16.0f};
constexpr float PatchPerKm[3] = {3.0f, 6.0f, 10.0f};
constexpr float ManholePerKm[3] = {0.0f, 0.5f, 2.0f};   // ponytail: class-only, no urban/rural input
constexpr float TireMarkChance = 0.1f;      // per slot, in a corner
constexpr float CornerRadiusM = 120.0f;     // tighter than this counts as a corner
constexpr float TrackOffsetM = 0.8f;        // wheel path off lane center (M_Road's TrackOffsetM)
constexpr float EdgeMarginM = 0.15f;        // decals stay this far inside the pavement edge
constexpr float MinHalfWidthM = 1.5f;
constexpr float CondMin = 0.4f;             // per-chunk wear multiplier CondMin..CondMin+CondRange
constexpr float CondRange = 1.2f;
constexpr float DecalDepthCm = 30.0f;       // half projection depth
constexpr float SnowHideCm = 1.0f;
constexpr float DecalFadeScreenSize = 0.002f;

uint32 DecalHash(uint32 X)   // lowbias32 (C. Wellons)
{
	X ^= X >> 16;
	X *= 0x7feb352du;
	X ^= X >> 15;
	X *= 0x846ca68bu;
	X ^= X >> 16;
	return X;
}

/** [0,1), 24-bit so it's exact in float and in the Python mirror. */
float DecalRand(uint32 Seed, int32 Slot, uint32 Salt)
{
	return (float)((DecalHash(DecalHash(Seed ^ DecalHash((uint32)Slot)) + Salt)) >> 8) * (1.0f / 16777216.0f);
}
}

UODRoadDecals::UODRoadDecals()
{
	PrimaryComponentTick.bCanEverTick = true;
	PrimaryComponentTick.TickInterval = 0.5f;   // snow-hide poll only
}

void UODRoadDecals::PlaceSpots(const FParsedChunk& Chunk, TArray<FODRoadDecalSpot>& Out)
{
	const int32 VCount = Chunk.Verts.Num() / 3;
	if (!Chunk.bHasV3Data || Chunk.SurfaceClass != 0
		|| Chunk.Uvs.Num() != VCount * 2 || Chunk.Uv1.Num() != VCount * 2)
	{
		return;
	}
	// ribbon verts lead the chunk at 7 per station (pave-L/R = columns 2/3);
	// junction-pad verts after them have u0 strictly inside 0..1 (RoadSagCm)
	int32 NRibbon = 0;
	while (NRibbon < VCount && !(Chunk.Uvs[NRibbon * 2] > 1e-3f && Chunk.Uvs[NRibbon * 2] < 1.0f - 1e-3f))
	{
		++NRibbon;
	}
	const int32 NSt = NRibbon / 7;
	const float HalfW = NSt > 0 ? Chunk.Uv1[2 * 2] : 0.0f;
	if (NSt < 2 || HalfW < MinHalfWidthM)
	{
		return;
	}
	const int32 Lanes = FMath::Max(1, (int32)Chunk.LaneCount);
	const float LaneW = 2.0f * HalfW / Lanes;
	const int32 Band = Chunk.RoadClassId <= 2 ? 0 : Chunk.RoadClassId <= 4 ? 1 : 2;
	const uint32 Seed = DecalHash((uint32)FMath::FloorToInt(Chunk.Verts[0] / 100.0f) * 73856093u
		^ (uint32)FMath::FloorToInt(Chunk.Verts[1] / 100.0f) * 19349663u);
	const float Cond = CondMin + CondRange * DecalRand(Seed, 0, 99);
	const float SlotKm = DecalSlotM / 1000.0f;
	const float PCrack = CrackPerKm[Band] * SlotKm * Cond;
	const float PPatch = PCrack + PatchPerKm[Band] * SlotKm * Cond;
	const float PManhole = PPatch + ManholePerKm[Band] * SlotKm;
	const float PTire = PManhole + TireMarkChance;

	auto P = [&](int32 V) { return FVector(Chunk.Verts[V * 3], Chunk.Verts[V * 3 + 1], Chunk.Verts[V * 3 + 2]); };
	auto Ctr = [&](int32 S) { return 0.5 * (P(S * 7 + 2) + P(S * 7 + 3)); };
	auto Arc = [&](int32 S) { return Chunk.Uvs[(S * 7 + 2) * 2 + 1]; };
	auto Junction = [&](int32 S) { return Chunk.Uv1[(S * 7 + 2) * 2 + 1]; };
	auto Flat = [](const FVector& V) { return FVector2D(V.X, V.Y); };
	// radius ~ distance between the mid-points of the segments either side
	// of segment S, over the heading change between them
	auto IsCorner = [&](int32 S)
	{
		const FVector A = Ctr(FMath::Max(S - 1, 0)), B = Ctr(S), C = Ctr(S + 1), D = Ctr(FMath::Min(S + 2, NSt - 1));
		const FVector2D In = Flat(B - A), Mid = Flat(C - B), Outd = Flat(D - C);
		if (In.Size() < 1.0 || Outd.Size() < 1.0)
		{
			return false;
		}
		const double Turn = FMath::Acos(FMath::Clamp(
			FVector2D::DotProduct(In.GetSafeNormal(), Outd.GetSafeNormal()), -1.0, 1.0));
		const double SpanM = (0.5 * In.Size() + Mid.Size() + 0.5 * Outd.Size()) / 100.0;
		return Turn * CornerRadiusM > SpanM;
	};

	for (int32 S = 0; S + 1 < NSt; ++S)
	{
		const float V0 = Arc(S), V1 = Arc(S + 1);
		if (Junction(S) > 0.01f || Junction(S + 1) > 0.01f || FMath::Abs(V1 - V0) < 0.01f)
		{
			continue;
		}
		const float Lo = FMath::Min(V0, V1), Hi = FMath::Max(V0, V1);
		const bool bCorner = IsCorner(S);
		const FVector C0 = Ctr(S), C1 = Ctr(S + 1);
		const FVector Fwd = (C1 - C0).GetSafeNormal();
		for (int32 K = FMath::CeilToInt(Lo / DecalSlotM); K * DecalSlotM < Hi; ++K)
		{
			const float R = DecalRand(Seed, K, 1);
			const int32 Kind = R < PCrack ? 0 : R < PPatch ? 1 : R < PManhole ? 2 : (bCorner && R < PTire) ? 3 : -1;
			if (Kind < 0)
			{
				continue;
			}
			const float A = K * DecalSlotM + (DecalRand(Seed, K, 2) - 0.5f) * 2.0f * DecalSlotJitter * DecalSlotM;
			const double T = (A - V0) / (V1 - V0);
			const FVector Right = FMath::Lerp(P(S * 7 + 3) - P(S * 7 + 2), P(S * 7 + 10) - P(S * 7 + 9), T).GetSafeNormal();
			FVector Up = FVector::CrossProduct(Fwd, Right).GetSafeNormal();
			if (Up.Z < 0.0)
			{
				Up = -Up;
			}
			if (Fwd.IsNearlyZero() || Up.IsNearlyZero())
			{
				continue;
			}
			const int32 Lane = FMath::Min(Lanes - 1, (int32)(DecalRand(Seed, K, 3) * Lanes));
			const float LaneC = -HalfW + LaneW * (Lane + 0.5f);
			const float R4 = DecalRand(Seed, K, 4), R5 = DecalRand(Seed, K, 5), R6 = DecalRand(Seed, K, 6);

			FVector Along = Fwd;
			float HalfAlong = 0.0f, HalfAcross = 0.0f, Lateral = LaneC, Extent = 0.0f;   // Extent: half-size across the road
			switch (Kind)
			{
			case 0:   // crack: transverse across the lane, or along a wheel path
				if (R4 < 0.3f)
				{
					Along = Right;
					HalfAlong = 0.45f * LaneW;
					HalfAcross = 0.3f;
					Extent = HalfAlong;
				}
				else
				{
					HalfAlong = 1.0f + 1.5f * R5;
					HalfAcross = 0.3f;
					Lateral = LaneC + (R6 < 0.5f ? -TrackOffsetM : TrackOffsetM);
					Extent = HalfAcross;
				}
				break;
			case 1:   // patch
				HalfAlong = 0.8f + 1.7f * R5;
				HalfAcross = 0.6f + FMath::Max(0.0f, 0.45f * LaneW - 0.6f) * R6;
				Lateral = LaneC + (R4 - 0.5f) * 0.6f;
				Extent = HalfAcross;
				break;
			case 2:   // manhole, any rotation
			{
				const double Ang = 2.0 * UE_DOUBLE_PI * R5;
				Along = Fwd * FMath::Cos(Ang) + FVector::CrossProduct(Up, Fwd) * FMath::Sin(Ang);
				HalfAlong = HalfAcross = Extent = 0.4f;
				Lateral = LaneC + (R6 - 0.5f) * 1.2f;
				break;
			}
			default:  // tire marks (both wheels), following the road
				HalfAlong = 3.0f;
				HalfAcross = 1.05f;
				Extent = HalfAcross;
				break;
			}
			const float Room = HalfW - EdgeMarginM - Extent;
			if (Room < 0.0f)
			{
				continue;
			}
			if (DecalRand(Seed, K, 7) < 0.5f)
			{
				Along = -Along;
			}
			FODRoadDecalSpot& Spot = Out.AddDefaulted_GetRef();
			Spot.Pos = FMath::Lerp(C0, C1, T) + Right * (FMath::Clamp(Lateral, -Room, Room) * 100.0);
			Spot.Down = -Up;
			Spot.Along = Along;
			Spot.HalfAcrossCm = HalfAcross * 100.0f;
			Spot.HalfAlongCm = HalfAlong * 100.0f;
			Spot.Kind = Kind;
			Spot.Intensity = 0.55f + 0.45f * DecalRand(Seed, K, 8);
		}
	}
}

void UODRoadDecals::AddChunk(const FIntPoint& TileKey, const FParsedChunk& Chunk, UPrimitiveComponent* ChunkComp)
{
	if (!bTriedLoad)
	{
		bTriedLoad = true;
		if (UMaterialInterface* Base = LoadObject<UMaterialInterface>(
			nullptr, TEXT("/Game/OpenDrive/Realistic/Road/M_RoadDecal_real.M_RoadDecal_real")))
		{
			Mat = UMaterialInstanceDynamic::Create(Base, this);
		}
		else
		{
			UE_LOG(LogTemp, Warning,
				TEXT("ODRoadDecals: M_RoadDecal_real missing (unreal/apply_road_materials_real.py) - no road decals"));
		}
	}
	AActor* Owner = GetOwner();
	if (!Mat || !Owner)
	{
		return;
	}
	TArray<FODRoadDecalSpot> Spots;
	PlaceSpots(Chunk, Spots);
	if (Spots.IsEmpty())
	{
		return;
	}
	FChunkDecals& Entry = Tiles.FindOrAdd(TileKey).AddDefaulted_GetRef();
	Entry.Chunk = ChunkComp;
	for (const FODRoadDecalSpot& S : Spots)
	{
		UDecalComponent* D = NewObject<UDecalComponent>(Owner);
		D->SetupAttachment(Owner->GetRootComponent());
		D->SetDecalMaterial(Mat);
		D->DecalSize = FVector(DecalDepthCm, S.HalfAcrossCm, S.HalfAlongCm);
		// decal X projects (down); its U runs along local Z, V along local Y
		D->SetRelativeLocationAndRotation(S.Pos, FRotationMatrix::MakeFromXZ(S.Down, S.Along).Rotator());
		D->SetDecalColor(FLinearColor((S.Kind + 0.5f) / 4.0f, S.Intensity, 0.0f, 1.0f));
		D->SetFadeScreenSize(DecalFadeScreenSize);
		D->RegisterComponent();
		Entry.Decals.Add(D);
	}
	UpdateSnowHidden(Entry);
}

void UODRoadDecals::ReleaseTile(const FIntPoint& TileKey)
{
	if (TArray<FChunkDecals>* Entries = Tiles.Find(TileKey))
	{
		for (FChunkDecals& Entry : *Entries)
		{
			for (const TWeakObjectPtr<UDecalComponent>& D : Entry.Decals)
			{
				if (UDecalComponent* Comp = D.Get())
				{
					Comp->DestroyComponent();
				}
			}
		}
		Tiles.Remove(TileKey);
	}
}

void UODRoadDecals::SetWetness(float Wetness)
{
	if (Mat)
	{
		Mat->SetScalarParameterValue(TEXT("Wetness"), Wetness);
	}
}

void UODRoadDecals::TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction)
{
	Super::TickComponent(DeltaTime, TickType, ThisTickFunction);
	for (TPair<FIntPoint, TArray<FChunkDecals>>& Pair : Tiles)
	{
		for (FChunkDecals& Entry : Pair.Value)
		{
			UpdateSnowHidden(Entry);
		}
	}
}

void UODRoadDecals::UpdateSnowHidden(FChunkDecals& Entry)
{
	const bool bHide = AODRoadManager::ChunkSnowCm(Entry.Chunk.Get(), 0.0f) > SnowHideCm;
	if (bHide == Entry.bHidden)
	{
		return;
	}
	Entry.bHidden = bHide;
	for (const TWeakObjectPtr<UDecalComponent>& D : Entry.Decals)
	{
		if (UDecalComponent* Comp = D.Get())
		{
			Comp->SetVisibility(!bHide);
		}
	}
}
