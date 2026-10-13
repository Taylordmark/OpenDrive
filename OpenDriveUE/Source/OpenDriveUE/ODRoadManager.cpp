#include "ODRoadManager.h"
#include "ODVisualStyle.h"
#include "ODRoadDecals.h"

#include "ODBinSidecarUtil.h"
#include "ODTileSource.h"
#include "Algo/Reverse.h"
#include "Async/Async.h"
#include "Components/DynamicMeshComponent.h"
#include "Containers/StringConv.h"
#include "EngineUtils.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/DynamicMeshOverlay.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/MaterialInterface.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "ODWeatherController.h"

using namespace UE::Geometry;

namespace
{
// manual little-endian parsing (no struct cast) - same convention as
// ODForestManager::LoadMask / ODRaceManager::LoadCenterline's binary
// sidecars, and avoids any packing/alignment assumption on disk.
// ReadU32LE/ReadF32LE moved to the shared ODBinSidecarUtil.h (2026-07-28) -
// this file's own ReadU16LE/ReadPascalUtf8 below are unique to the ODL1
// place-label format, so they stay local.
uint16 ReadU16LE(const uint8* P)
{
	return (uint16)P[0] | (uint16)P[1] << 8;
}

// Reads a uint16-length-prefixed UTF-8 string, advancing Cur past it.
// Returns false (leaving Out untouched) if the declared length would run
// past End - the same "truncated file" bail-out every other parser in this
// project uses, just for a variable-length field instead of a fixed one.
// Puddles (PLAN-REALISM Phase 6, 2026-10-09): how far each ribbon station's
// pavement sits below the mean of its +-PuddleSagHalfWindow neighbours, in
// cm (0 = crest or straight grade). M_Road reads it from UV3.x. Ribbon verts
// lead the chunk at 7 per station (pave-L/pave-R = columns 2/3, u0 0 or 1);
// junction-pad verts appended after them (u0 strictly inside 0..1) get 0.
// Mirrored in pipeline/check_puddle_sag.py.
constexpr int32 PuddleSagHalfWindow = 4;   // +-20 m at 5 m stations
int32 RibbonVertCount(const TArray<float>& Uvs, int32 VCount)
{
	int32 N = 0;
	while (N < VCount && !(Uvs[N * 2] > 1e-3f && Uvs[N * 2] < 1.0f - 1e-3f))
	{
		++N;
	}
	return N;
}
TArray<float> RoadSagCm(const TArray<float>& Verts, const TArray<float>& Uvs, int32 VCount)
{
	TArray<float> Sag;
	Sag.Init(0.0f, VCount);
	const int32 NRibbon = RibbonVertCount(Uvs, VCount);
	const int32 NSt = NRibbon / 7;
	TArray<float> Z;
	Z.SetNumUninitialized(NSt);
	for (int32 s = 0; s < NSt; ++s)
	{
		Z[s] = 0.5f * (Verts[(s * 7 + 2) * 3 + 2] + Verts[(s * 7 + 3) * 3 + 2]);
	}
	for (int32 s = 0; s < NSt; ++s)
	{
		const int32 A = FMath::Max(0, s - PuddleSagHalfWindow), B = FMath::Min(NSt - 1, s + PuddleSagHalfWindow);
		float Sum = 0.0f;
		for (int32 k = A; k <= B; ++k)
		{
			Sum += Z[k];
		}
		const float S = FMath::Max(0.0f, Sum / (B - A + 1) - Z[s]);
		for (int32 c = 0; c < 7; ++c)
		{
			Sag[s * 7 + c] = S;
		}
	}
	return Sag;
}

// Junction road-edge signal (PLAN-VISUALS join J, Realistic only), UV3.y per
// triangle corner. M_Road*_real's fringe turns the outer decimetres of
// pavement into the terrain's gravel shoulder; ribbons get that from u0/u1,
// but a junction pad sits at |meterPos| <= 2 m and so had a hard edge.
//   pad corner:    1 + metres to the pad's visible edge (uncovered rim), 0 on
//                  that rim and on the apron (always gravel, like the
//                  ribbon's wrap lip); exact across each rim triangle
//   ribbon vertex: min(s, cap) - cap, s = metres along the road from the
//                  nearest pad-rim crossing of its pavement edge, negative
//                  inside a pad, so a ribbon end tucked under a pad drops its
//                  own fringe inside the junction (0 = far from any pad)
// A rim vertex inside another road surface (the ribbon it tucks under, an
// overlapping fill disc) is covered: no fringe there. Pads are found by their
// apron, an edge PAD_APRON_OUT_M out and PAD_APRON_DROP_M down from the rim
// (pipeline/experimental_junction_pads.py). Sees this tile only; a surface
// from a neighbouring tile is missed (a ribbon over a pad sits 1.5 cm above
// it anyway). Mirrored in pipeline/checks/check_pad_edge.py. The cap is
// longer than any station gap so the ribbon code stays linear (exact)
// through a crossing; UV3 is full-precision float (MeshRenderBufferSet).
constexpr float EdgeCodeCapM = 1000.0f;
constexpr float PadApronOutCm = 250.0f;
constexpr float PadApronDropCm = 100.0f;
constexpr float PadApronTolCm = 2.0f;
constexpr float EdgeCoverMarginCm = 5.0f;   // rim vertex this far inside another surface = covered
constexpr float EdgeSameLevelCm = 150.0f;   // surfaces further apart in Z are other levels
constexpr float EdgeGridCm = 1000.0f;

struct FEdgeTri
{
	FVector3f A, B, C;
	FVector2f Lo, Hi;
	bool bPad = false;
};

// P inside T's XY footprint by more than Margin cm (either winding), with
// T's surface at P within EdgeSameLevelCm of P.Z.
bool EdgeInTri(const FEdgeTri& T, const FVector3f& P, float Margin)
{
	if (P.X < T.Lo.X || P.X > T.Hi.X || P.Y < T.Lo.Y || P.Y > T.Hi.Y)
	{
		return false;
	}
	auto Side = [&P](const FVector3f& U, const FVector3f& V)
	{
		const FVector2f N(V.Y - U.Y, U.X - V.X);
		const float L = N.Size();
		return L > 1e-3f ? ((P.X - U.X) * N.X + (P.Y - U.Y) * N.Y) / L : 0.0f;
	};
	const float S0 = Side(T.A, T.B), S1 = Side(T.B, T.C), S2 = Side(T.C, T.A);
	if (!((S0 > Margin && S1 > Margin && S2 > Margin) || (S0 < -Margin && S1 < -Margin && S2 < -Margin)))
	{
		return false;
	}
	const FVector2f E1(T.B.X - T.A.X, T.B.Y - T.A.Y), E2(T.C.X - T.A.X, T.C.Y - T.A.Y), Q(P.X - T.A.X, P.Y - T.A.Y);
	const float D = E1.X * E2.Y - E2.X * E1.Y;
	if (FMath::Abs(D) < 1e-3f)
	{
		return false;
	}
	const float L1 = (Q.X * E2.Y - E2.X * Q.Y) / D, L2 = (E1.X * Q.Y - Q.X * E1.Y) / D;
	return FMath::Abs(T.A.Z + L1 * (T.B.Z - T.A.Z) + L2 * (T.C.Z - T.A.Z) - P.Z) < EdgeSameLevelCm;
}

// bRunA/bRunB: the edge runs on as its line past that end (affine, so exact
// when interpolated over a triangle)
float EdgeSegDistM(const FVector3f& P, const FVector3f& A, const FVector3f& B, bool bRunA = false, bool bRunB = false)
{
	const FVector2f AB(B.X - A.X, B.Y - A.Y), AP(P.X - A.X, P.Y - A.Y);
	const float T0 = (AP | AB) / FMath::Max(AB | AB, 1e-6f);
	const float T = FMath::Clamp(T0, bRunA ? -UE_BIG_NUMBER : 0.0f, bRunB ? UE_BIG_NUMBER : 1.0f);
	return (AP - AB * T).Size() / 100.0f;
}

void ComputeEdgeCodes(TArray<FParsedChunk>& Chunks)
{
	struct FChunkPads
	{
		int32 NRib = 0;
		TArray<bool> bRim, bApron, bCovered;
		TArray<FIntPoint> Segs;   // uncovered rim edges
	};
	TArray<FChunkPads> Info;
	Info.SetNum(Chunks.Num());
	TArray<FEdgeTri> Tris;
	auto Vert = [](const FParsedChunk& C, int32 V)
	{
		return FVector3f(C.Verts[V * 3], C.Verts[V * 3 + 1], C.Verts[V * 3 + 2]);
	};
	auto AddTri = [&](const FParsedChunk& C, uint32 I0, uint32 I1, uint32 I2, bool bPad)
	{
		FEdgeTri T;
		T.A = Vert(C, I0);
		T.B = Vert(C, I1);
		T.C = Vert(C, I2);
		T.Lo = FVector2f(FMath::Min3(T.A.X, T.B.X, T.C.X), FMath::Min3(T.A.Y, T.B.Y, T.C.Y));
		T.Hi = FVector2f(FMath::Max3(T.A.X, T.B.X, T.C.X), FMath::Max3(T.A.Y, T.B.Y, T.C.Y));
		T.bPad = bPad;
		Tris.Add(T);
	};

	// pads (rim/apron) and the road surfaces: pad tops + ribbon pavement
	bool bAnyPad = false;
	for (int32 c = 0; c < Chunks.Num(); ++c)
	{
		const FParsedChunk& C = Chunks[c];
		FChunkPads& I = Info[c];
		const int32 VCount = C.Verts.Num() / 3;
		I.NRib = RibbonVertCount(C.Uvs, VCount);
		I.bRim.Init(false, VCount);
		I.bApron.Init(false, VCount);
		I.bCovered.Init(false, VCount);
		for (int32 t = 0; t * 3 < C.Tris.Num(); ++t)
		{
			for (int32 k = 0; k < 3; ++k)
			{
				const int32 A = C.Tris[t * 3 + k], B = C.Tris[t * 3 + (k + 1) % 3];
				if (A < I.NRib || B < I.NRib || A >= VCount || B >= VCount)
				{
					continue;
				}
				const float Dxy = FVector2f(C.Verts[B * 3] - C.Verts[A * 3], C.Verts[B * 3 + 1] - C.Verts[A * 3 + 1]).Size();
				const float Dz = C.Verts[B * 3 + 2] - C.Verts[A * 3 + 2];
				if (FMath::Abs(Dxy - PadApronOutCm) < PadApronTolCm && FMath::Abs(FMath::Abs(Dz) - PadApronDropCm) < PadApronTolCm)
				{
					I.bRim[Dz < 0.0f ? A : B] = true;
					I.bApron[Dz < 0.0f ? B : A] = true;
					bAnyPad = true;
				}
			}
		}
		for (int32 t = 0; t * 3 < C.Tris.Num(); ++t)
		{
			const uint32 I0 = C.Tris[t * 3], I1 = C.Tris[t * 3 + 1], I2 = C.Tris[t * 3 + 2];
			if ((int32)FMath::Max3(I0, I1, I2) >= VCount)
			{
				continue;
			}
			if ((int32)FMath::Min3(I0, I1, I2) >= I.NRib)
			{
				if (!I.bApron[I0] && !I.bApron[I1] && !I.bApron[I2])
				{
					AddTri(C, I0, I1, I2, true);
				}
			}
			else if ((int32)FMath::Max3(I0, I1, I2) < I.NRib
				&& (I0 % 7 == 2 || I0 % 7 == 3) && (I1 % 7 == 2 || I1 % 7 == 3) && (I2 % 7 == 2 || I2 % 7 == 3))
			{
				AddTri(C, I0, I1, I2, false);
			}
		}
	}
	if (!bAnyPad)
	{
		return;
	}
	TMap<FIntPoint, TArray<int32>> Grid;
	for (int32 i = 0; i < Tris.Num(); ++i)
	{
		for (int32 gx = FMath::FloorToInt(Tris[i].Lo.X / EdgeGridCm); gx <= FMath::FloorToInt(Tris[i].Hi.X / EdgeGridCm); ++gx)
		{
			for (int32 gy = FMath::FloorToInt(Tris[i].Lo.Y / EdgeGridCm); gy <= FMath::FloorToInt(Tris[i].Hi.Y / EdgeGridCm); ++gy)
			{
				Grid.FindOrAdd(FIntPoint(gx, gy)).Add(i);
			}
		}
	}
	auto Inside = [&](const FVector3f& P, float Margin, bool bPadsOnly)
	{
		if (const TArray<int32>* Cell = Grid.Find(FIntPoint(FMath::FloorToInt(P.X / EdgeGridCm), FMath::FloorToInt(P.Y / EdgeGridCm))))
		{
			for (int32 i : *Cell)
			{
				if ((Tris[i].bPad || !bPadsOnly) && EdgeInTri(Tris[i], P, Margin))
				{
					return true;
				}
			}
		}
		return false;
	};

	for (int32 c = 0; c < Chunks.Num(); ++c)
	{
		FParsedChunk& C = Chunks[c];
		FChunkPads& I = Info[c];
		const int32 VCount = C.Verts.Num() / 3;
		const int32 TCount = C.Tris.Num() / 3;
		TArray<float> Code;
		Code.Init(0.0f, VCount);
		bool bAny = false;

		// pad: covered rim, uncovered rim edges, metres to the nearest one
		for (int32 v = I.NRib; v < VCount; ++v)
		{
			I.bCovered[v] = I.bRim[v] && Inside(Vert(C, v), EdgeCoverMarginCm, false);
		}
		auto Open = [&I](int32 V) { return I.bRim[V] && !I.bCovered[V]; };
		for (int32 t = 0; t < TCount; ++t)
		{
			for (int32 k = 0; k < 3; ++k)
			{
				const int32 A = C.Tris[t * 3 + k], B = C.Tris[t * 3 + (k + 1) % 3], X = C.Tris[t * 3 + (k + 2) % 3];
				if (A < VCount && B < VCount && X < VCount && FMath::Min3(A, B, X) >= I.NRib
					&& !I.bApron[X] && Open(A) && Open(B))
				{
					I.Segs.Add(FIntPoint(A, B));
				}
			}
		}
		TArray<int32> Near;   // each pad vertex's nearest uncovered rim edge
		Near.Init(INDEX_NONE, VCount);
		for (int32 v = I.NRib; v < VCount; ++v)
		{
			float E = EdgeCodeCapM;
			for (int32 i = 0; i < I.Segs.Num(); ++i)
			{
				const float D = EdgeSegDistM(Vert(C, v), Vert(C, I.Segs[i].X), Vert(C, I.Segs[i].Y));
				if (D < E)
				{
					E = D;
					Near[v] = i;
				}
			}
			Code[v] = I.bApron[v] ? 1.0f : 1.0f + E;
			bAny = true;
		}

		// ribbon: where each pavement edge crosses into a pad, along the road
		const int32 NSt = I.NRib / 7;
		if (NSt >= 2 && I.NRib % 7 == 0)
		{
			for (int32 Col = 2; Col <= 3; ++Col)
			{
				TArray<bool> In;
				TArray<float> Len, Cross;
				TArray<int32> Gap;
				In.SetNum(NSt);
				Len.SetNum(NSt);
				for (int32 s = 0; s < NSt; ++s)
				{
					In[s] = Inside(Vert(C, s * 7 + Col), 0.0f, true);
					Len[s] = s == 0 ? 0.0f
						: Len[s - 1] + FVector3f::Dist(Vert(C, s * 7 + Col), Vert(C, (s - 1) * 7 + Col)) / 100.0f;
				}
				for (int32 s = 0; s + 1 < NSt; ++s)
				{
					if (In[s] != In[s + 1])
					{
						const FVector3f P0 = Vert(C, s * 7 + Col), P1 = Vert(C, (s + 1) * 7 + Col);
						float Lo = 0.0f, Hi = 1.0f;   // In[s] holds at Lo, In[s + 1] at Hi
						for (int32 It = 0; It < 12; ++It)
						{
							const float Mid = 0.5f * (Lo + Hi);
							(Inside(FMath::Lerp(P0, P1, Mid), 0.0f, true) == In[s] ? Lo : Hi) = Mid;
						}
						Cross.Add(FMath::Lerp(Len[s], Len[s + 1], 0.5f * (Lo + Hi)));
						Gap.Add(s);
					}
				}
				// pave column + the lip/underside columns on its side
				// (under-L, lip-L, pave-L, pave-R, lip-R, under-R, under-L)
				// an inside station between two crossings takes the further one of
				// its own gaps: the ribbon fringe then never reaches into the pad
				// (it stops short outside instead)
				static const int32 SideCols[2][4] = {{0, 1, 2, 6}, {3, 4, 5, 5}};
				for (int32 s = 0; s < NSt; ++s)
				{
					float D = EdgeCodeCapM;
					for (float X : Cross)
					{
						D = FMath::Min(D, FMath::Abs(Len[s] - X));
					}
					for (int32 x = 0; x < Cross.Num(); ++x)
					{
						if (In[s] && (Gap[x] == s || Gap[x] + 1 == s))
						{
							D = FMath::Max(D, FMath::Abs(Len[s] - Cross[x]));
						}
					}
					const float Sc = (In[s] ? -D : D) - EdgeCodeCapM;
					bAny |= Sc != 0.0f;
					for (int32 k : SideCols[Col - 2])
					{
						Code[s * 7 + k] = Sc;
					}
				}
			}
		}
		if (!bAny)
		{
			continue;
		}

		// per corner: apron triangles all gravel. A rim triangle measures to its
		// own edge, run on as the line past an end where the visible rim carries
		// on (exact under interpolation). Any other keeps its corners' nearest-
		// edge distances unless those lie on different rim chains (overlapping
		// pads; min-of-distances interpolates low there), then it measures to the
		// one closest to its centre (convex, so never low: no fringe leak inward)
		TArray<int32> NSeg, Chain;
		NSeg.Init(0, VCount);
		Chain.SetNumUninitialized(VCount);
		for (int32 v = 0; v < VCount; ++v)
		{
			Chain[v] = v;
		}
		auto Root = [&Chain](int32 V)
		{
			while (Chain[V] != V)
			{
				V = Chain[V] = Chain[Chain[V]];
			}
			return V;
		};
		for (const FIntPoint& S : I.Segs)
		{
			++NSeg[S.X];
			++NSeg[S.Y];
			Chain[Root(S.X)] = Root(S.Y);
		}
		C.EdgeCode.SetNumUninitialized(TCount * 3);
		for (int32 t = 0; t < TCount; ++t)
		{
			const int32 V3[3] = {(int32)C.Tris[t * 3], (int32)C.Tris[t * 3 + 1], (int32)C.Tris[t * 3 + 2]};
			bool bPadTri = true;
			for (int32 k = 0; k < 3; ++k)
			{
				C.EdgeCode[t * 3 + k] = V3[k] < VCount ? Code[V3[k]] : 0.0f;
				bPadTri &= V3[k] >= I.NRib && V3[k] < VCount;
			}
			if (!bPadTri)
			{
				continue;
			}
			bool bApronTri = false, bRimOrApron = true;
			for (int32 V : V3)
			{
				bApronTri |= I.bApron[V];
				bRimOrApron &= I.bApron[V] || I.bRim[V];
			}
			if (bApronTri)
			{
				if (bRimOrApron)
				{
					C.EdgeCode[t * 3] = C.EdgeCode[t * 3 + 1] = C.EdgeCode[t * 3 + 2] = 1.0f;
				}
				continue;
			}
			int32 A = INDEX_NONE, B = INDEX_NONE;
			for (int32 k = 0; k < 3 && A == INDEX_NONE; ++k)
			{
				if (Open(V3[k]) && Open(V3[(k + 1) % 3]))
				{
					A = V3[k];
					B = V3[(k + 1) % 3];
				}
			}
			if (A == INDEX_NONE)
			{
				const int32 N0 = Near[V3[0]], N1 = Near[V3[1]], N2 = Near[V3[2]];
				int32 R = INDEX_NONE;
				bool bSplit = false;
				for (int32 Ni : {N0, N1, N2})
				{
					if (Ni != INDEX_NONE)
					{
						const int32 Ri = Root(I.Segs[Ni].X);
						bSplit |= R != INDEX_NONE && Ri != R;
						R = Ri;
					}
				}
				if (!bSplit)
				{
					continue;
				}
				const FVector3f Ctr = (Vert(C, V3[0]) + Vert(C, V3[1]) + Vert(C, V3[2])) / 3.0f;
				float Best = UE_BIG_NUMBER;
				for (int32 Ni : {N0, N1, N2})
				{
					if (Ni != INDEX_NONE)
					{
						const float D = EdgeSegDistM(Ctr, Vert(C, I.Segs[Ni].X), Vert(C, I.Segs[Ni].Y));
						if (D < Best)
						{
							Best = D;
							A = I.Segs[Ni].X;
							B = I.Segs[Ni].Y;
						}
					}
				}
			}
			for (int32 j = 0; j < 3; ++j)
			{
				C.EdgeCode[t * 3 + j] = 1.0f + FMath::Min(EdgeCodeCapM,
					EdgeSegDistM(Vert(C, V3[j]), Vert(C, A), Vert(C, B), NSeg[A] > 1, NSeg[B] > 1));
			}
		}
	}
}

bool ReadPascalUtf8(const uint8*& Cur, const uint8* End, FString& Out)
{
	if (End - Cur < 2)
	{
		return false;
	}
	const uint16 Len = ReadU16LE(Cur);
	Cur += 2;
	if (End - Cur < Len)
	{
		return false;
	}
	FUTF8ToTCHAR Converter(reinterpret_cast<const ANSICHAR*>(Cur), Len);
	Out = FString(Converter.Length(), Converter.Get());
	Cur += Len;
	return true;
}
}

AODRoadManager::AODRoadManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODRoadManager::SetWetness(float Wetness)
{
	for (UMaterialInterface* Mat : { RoadMat.Get(), RoadMatGravel.Get(), RoadMatDirt.Get() })
	{
		if (UMaterialInstanceDynamic* Mid = Cast<UMaterialInstanceDynamic>(Mat))
		{
			Mid->SetScalarParameterValue(TEXT("Wetness"), Wetness);
		}
	}
	if (Decals)
	{
		Decals->SetWetness(Wetness);
	}
}

void AODRoadManager::BeginPlay()
{
	Super::BeginPlay();

	if (ODVisualStyle::IsRealistic())
	{
		Decals = NewObject<UODRoadDecals>(this, TEXT("RoadDecals"));
		Decals->RegisterComponent();
	}

	RoadMat = ODVisualStyle::Pick(LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_Road.M_Road")));
	// Weather W2 (sec 7.40): one shared MID so AODSeasonController can push
	// the "Wetness" scalar to every road chunk at once.
	if (RoadMat)
	{
		RoadMat = UMaterialInstanceDynamic::Create(RoadMat, this);
	}
	RoadMatOneWay = ODVisualStyle::Pick(LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_Road_OneWay.M_Road_OneWay")));
	RoadMatGravel = ODVisualStyle::Pick(LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_Road_Gravel.M_Road_Gravel")));
	RoadMatDirt = ODVisualStyle::Pick(LoadObject<UMaterialInterface>(
		nullptr, TEXT("/Game/OpenDrive/Materials/M_Road_Dirt.M_Road_Dirt")));
	// unpaved roads get their own shared MIDs so SetWetness reaches them too
	if (RoadMatGravel)
	{
		RoadMatGravel = UMaterialInstanceDynamic::Create(RoadMatGravel, this);
	}
	if (RoadMatDirt)
	{
		RoadMatDirt = UMaterialInstanceDynamic::Create(RoadMatDirt, this);
	}
	LoadCenterline();
	LoadRouteGraph();
	LoadMapCenterline();
	LoadPlaceLabels();
	BuildTownNodes();

	// Diagnostic-only override (2026-08-25): lets a headless test bisect
	// how many resident chunks trigger the open Chaos/UE5.8
	// collision-registration bug without a recompile per step - see
	// AODTerrainManager's matching -odcollisionradius= override.
	float OverrideRadius = 0.0f;
	if (FParse::Value(FCommandLine::Get(), TEXT("odspawnradius="), OverrideRadius))
	{
		SpawnRadius = OverrideRadius;
		UE_LOG(LogTemp, Display, TEXT("ODRoadManager: odspawnradius override -> %.0f"), SpawnRadius);
	}

	// Tiles/PendingTiles/MissingTiles start empty - the first Tick()
	// discovers what's needed around the player/spawn point (slice 1 used
	// to eagerly load the whole network here instead).
}

void AODRoadManager::LoadCenterline()
{
	if (CenterlineFilePrefix.IsEmpty())
	{
		return;   // no route data on this actor instance - e.g. Tail of the
		          // Dragon, which draws its map off AODRaceManager's own copy
	}
	TArray<uint8> Bin;
	const FString BinPath = FPaths::ProjectContentDir()
		/ TEXT("OpenDrive/Masks") / (CenterlineFilePrefix + TEXT(".bin"));
	if (!FFileHelper::LoadFileToArray(Bin, *BinPath) || Bin.Num() < 8
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'C' || Bin[3] != '1')
	{
		return;
	}
	const int32 N = (int32)ReadU32LE(Bin.GetData() + 4);
	constexpr int32 RecordBytes = 4 + 4;   // X_cm, Y_cm
	if (N <= 0 || Bin.Num() < 8 + (int64)N * RecordBytes)
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODRoadManager: %s truncated/corrupt (N=%d, %lld bytes)"),
			*BinPath, N, Bin.Num());
		return;
	}
	RoadCenterline.SetNumUninitialized(N);
	const uint8* P = Bin.GetData() + 8;
	for (int32 i = 0; i < N; ++i, P += RecordBytes)
	{
		// float32 pairs on disk, FVector2D components are double (UE5 LWC) -
		// widen per-element via ReadF32LE, not a bulk memcpy (silently
		// corrupts every point - see ODRaceManager::LoadCenterline's comment).
		RoadCenterline[i] = FVector2D(ReadF32LE(P), ReadF32LE(P + 4));
	}
	UE_LOG(LogTemp, Display,
		TEXT("ODRoadManager: loaded %d centerline point(s) from %s (HUD route fallback)"),
		N, *BinPath);
}

namespace
{
// SS7.37 navigation tuning - mirrored in pipeline/checks/verify_route_graph.py
constexpr float TurnLookCm = 2500.0f;    // heading sampled 25 m before/after a junction
constexpr float TurnMinDeg = 35.0f;      // smaller bends at a junction = "continue"
constexpr float TurnMergeCm = 3000.0f;   // junction nodes this close = one turn
constexpr float OffRouteCm = 5000.0f;    // reroute past 50 m off the route line
constexpr double RerouteCooldownS = 2.0;
constexpr float ArriveCm = 3000.0f;
// one-way (2026-10-06): a wrong-way arc costs this much more, so a legal route
// always wins and a graph gap still routes (shortest wrong-way stretch)
constexpr float WrongWayCostMult = 20.0f;
// route start (PLAN-REALISM 3a): the nearest node the car can legally drive
// on from along its heading, if it's at most this much further than the
// nearest node (the opposite carriageway of a divided road is ~10-25 m off)
constexpr double StartSnapSlackCm = 3000.0;
}

void AODRoadManager::LoadRouteGraph()
{
	if (!CenterlineFilePrefix.StartsWith(TEXT("centerline_")))
	{
		return;   // no open-world road data (e.g. Tail of the Dragon) - no nav
	}
	TArray<uint8> Bin;
	const FString BinPath = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ (CenterlineFilePrefix.Replace(TEXT("centerline_"), TEXT("routegraph_")) + TEXT(".bin"));
	if (!FFileHelper::LoadFileToArray(Bin, *BinPath) || Bin.Num() < 8
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'G' || Bin[3] != '1')
	{
		return;
	}
	const int64 N = ReadU32LE(Bin.GetData() + 4);
	const int64 EOff = 8 + N * 8;
	const int64 E = Bin.Num() >= EOff + 4 ? ReadU32LE(Bin.GetData() + EOff) : -1;
	// optional trailing one-way block (2026-10-06): E * int8, +1 = a->b only,
	// -1 = b->a only, 0 = both. Older files end at the edges: all two-way.
	// The lane block after it (PLAN-REALISM 3a: node Z / junction control,
	// edge class / lanes / km/h / yield - export_road_centerline_bin.py
	// docstring) is for traffic; nav only needs the one-way block.
	const bool bHasLanes = Bin.Num() == EOff + 4 + E * 17 + N * 5;
	const bool bHasOneway = bHasLanes || Bin.Num() == EOff + 4 + E * 13;
	if (N <= 0 || E < 0 || (Bin.Num() != EOff + 4 + E * 12 && !bHasOneway))
	{
		UE_LOG(LogTemp, Warning, TEXT("ODRoadManager: %s truncated/corrupt"), *BinPath);
		return;
	}
	RouteNodes.SetNumUninitialized(N);
	for (int64 i = 0; i < N; ++i)
	{
		const uint8* P = Bin.GetData() + 8 + i * 8;
		RouteNodes[i] = FVector2D(ReadF32LE(P), ReadF32LE(P + 4));
	}
	// CSR build: count degrees, prefix-sum, fill (both directions)
	RouteAdjStart.SetNumZeroed(N + 1);
	const uint8* EP = Bin.GetData() + EOff + 4;
	for (int64 e = 0; e < E; ++e)
	{
		const uint32 A = ReadU32LE(EP + e * 12), B = ReadU32LE(EP + e * 12 + 4);
		if (A >= N || B >= N)
		{
			UE_LOG(LogTemp, Warning, TEXT("ODRoadManager: %s bad edge index"), *BinPath);
			RouteNodes.Reset();
			RouteAdjStart.Reset();
			return;
		}
		++RouteAdjStart[A + 1];
		++RouteAdjStart[B + 1];
	}
	for (int64 i = 0; i < N; ++i)
	{
		RouteAdjStart[i + 1] += RouteAdjStart[i];
	}
	RouteAdjNode.SetNumUninitialized(E * 2);
	RouteAdjLen.SetNumUninitialized(E * 2);
	RouteAdjWrongWay.Init(false, E * 2);
	TArray<int32> Fill(RouteAdjStart.GetData(), N);
	const int8* Oneway = bHasOneway ? reinterpret_cast<const int8*>(EP + E * 12) : nullptr;
	for (int64 e = 0; e < E; ++e)
	{
		const int32 A = ReadU32LE(EP + e * 12), B = ReadU32LE(EP + e * 12 + 4);
		const float L = ReadF32LE(EP + e * 12 + 8);
		const int8 Dir = Oneway ? Oneway[e] : 0;
		// both arcs stay (junction degree for turn prompts ignores one-way);
		// the wrong-way one just costs WrongWayCostMult x
		RouteAdjWrongWay[Fill[A]] = Dir < 0;
		RouteAdjNode[Fill[A]] = B; RouteAdjLen[Fill[A]++] = Dir < 0 ? L * WrongWayCostMult : L;
		RouteAdjWrongWay[Fill[B]] = Dir > 0;
		RouteAdjNode[Fill[B]] = A; RouteAdjLen[Fill[B]++] = Dir > 0 ? L * WrongWayCostMult : L;
	}
	UE_LOG(LogTemp, Display, TEXT("ODRoadManager: route graph %lld nodes / %lld edges (one-way %s, lanes %s) from %s"),
		N, E, bHasOneway ? TEXT("yes") : TEXT("no, old file"), bHasLanes ? TEXT("yes") : TEXT("no"), *BinPath);
}

int32 AODRoadManager::NearestRouteNode(const FVector2D& Q, const FVector2D& Heading) const
{
	// ponytail: brute-force O(N) (~290k nodes on summit_county) - only runs on
	// a route pick or a >=2 s-throttled reroute; grid-bucket it if it shows up
	int32 Best = INDEX_NONE, BestFwd = INDEX_NONE;
	double BestD2 = TNumericLimits<double>::Max(), BestFwdD2 = BestD2;
	const bool bHeading = !Heading.IsNearlyZero();
	for (int32 i = 0; i < RouteNodes.Num(); ++i)
	{
		const double D2 = FVector2D::DistSquared(RouteNodes[i], Q);
		if (D2 < BestD2)
		{
			BestD2 = D2;
			Best = i;
		}
		if (bHeading && D2 < BestFwdD2)
		{
			// a legal arc leaving along the heading: not the opposite carriageway
			for (int32 k = RouteAdjStart[i]; k < RouteAdjStart[i + 1]; ++k)
			{
				if (!RouteAdjWrongWay[k] && FVector2D::DotProduct(RouteNodes[RouteAdjNode[k]] - RouteNodes[i], Heading) > 0.0)
				{
					BestFwdD2 = D2;
					BestFwd = i;
					break;
				}
			}
		}
	}
	return BestFwd != INDEX_NONE && FMath::Sqrt(BestFwdD2) <= FMath::Sqrt(BestD2) + StartSnapSlackCm ? BestFwd : Best;
}

bool AODRoadManager::SnapToRouteGraph(const FVector2D& Query, FVector2D& OutPoint) const
{
	const int32 N = NearestRouteNode(Query);
	if (N == INDEX_NONE)
	{
		return false;
	}
	OutPoint = RouteNodes[N];
	return true;
}

bool AODRoadManager::FindRoutePath(int32 From, int32 To, TArray<int32>& OutNodes) const
{
	// A*, Euclidean heuristic (admissible: edge lengths are 3D chords between
	// the nodes, >= their 2D distance). Lazy-deletion binary heap.
	const int32 N = RouteNodes.Num();
	TArray<double> G;
	G.Init(TNumericLimits<double>::Max(), N);
	TArray<int32> Prev;
	Prev.Init(INDEX_NONE, N);
	TBitArray<> Closed(false, N);
	struct FOpen { double F; int32 Node; };
	auto Less = [](const FOpen& A, const FOpen& B) { return A.F < B.F; };
	TArray<FOpen> Heap;
	const FVector2D Goal = RouteNodes[To];
	G[From] = 0.0;
	Heap.HeapPush({FVector2D::Distance(RouteNodes[From], Goal), From}, Less);
	while (Heap.Num() > 0)
	{
		FOpen Top;
		Heap.HeapPop(Top, Less, EAllowShrinking::No);
		const int32 U = Top.Node;
		if (U == To)
		{
			break;
		}
		if (Closed[U])
		{
			continue;
		}
		Closed[U] = true;
		for (int32 k = RouteAdjStart[U]; k < RouteAdjStart[U + 1]; ++k)
		{
			const int32 V = RouteAdjNode[k];
			const double NG = G[U] + RouteAdjLen[k];
			if (NG < G[V])
			{
				G[V] = NG;
				Prev[V] = U;
				Heap.HeapPush({NG + FVector2D::Distance(RouteNodes[V], Goal), V}, Less);
			}
		}
	}
	if (Prev[To] == INDEX_NONE && From != To)
	{
		return false;
	}
	OutNodes.Reset();
	for (int32 V = To; V != INDEX_NONE; V = Prev[V])
	{
		OutNodes.Add(V);
	}
	Algo::Reverse(OutNodes);
	return true;
}

bool AODRoadManager::RecomputeRoute(const FVector2D& From)
{
	// start on a node the car can drive on from along its heading (on a divided
	// road the plain nearest node can be the other carriageway -> a U-turn route)
	const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0);
	const FVector2D Heading = Player ? FVector2D(Player->GetActorForwardVector()).GetSafeNormal() : FVector2D::ZeroVector;
	const int32 S = NearestRouteNode(From, Heading), T = NearestRouteNode(RouteDest);
	TArray<int32> Path;
	if (S == INDEX_NONE || T == INDEX_NONE || !FindRoutePath(S, T, Path) || Path.Num() < 2)
	{
		return false;
	}
	RoutePts.Reset(Path.Num());
	RouteCum.Reset(Path.Num());
	for (int32 i = 0; i < Path.Num(); ++i)
	{
		RoutePts.Add(RouteNodes[Path[i]]);
		RouteCum.Add(i == 0 ? 0.0f : RouteCum[i - 1] + (float)FVector2D::Distance(RoutePts[i - 1], RoutePts[i]));
	}
	// turns = junction nodes (graph degree >= 3) where the heading 25 m
	// before vs 25 m after bends past TurnMinDeg; near-duplicates merged.
	// X=east, Y=south (screen-down), so cross > 0 = clockwise = RIGHT.
	RouteTurns.Reset();
	const int32 Num = RoutePts.Num();
	for (int32 k = 1; k < Num - 1; ++k)
	{
		if (RouteAdjStart[Path[k] + 1] - RouteAdjStart[Path[k]] < 3)
		{
			continue;
		}
		int32 j = k - 1, m = k + 1;
		while (j > 0 && RouteCum[k] - RouteCum[j] < TurnLookCm) { --j; }
		while (m < Num - 1 && RouteCum[m] - RouteCum[k] < TurnLookCm) { ++m; }
		const FVector2D In = (RoutePts[k] - RoutePts[j]).GetSafeNormal();
		const FVector2D Out = (RoutePts[m] - RoutePts[k]).GetSafeNormal();
		const float Ang = (float)FMath::RadiansToDegrees(FMath::Atan2(
			FVector2D::CrossProduct(In, Out), FVector2D::DotProduct(In, Out)));
		if (FMath::Abs(Ang) < TurnMinDeg)
		{
			continue;
		}
		if (RouteTurns.Num() > 0 && RouteCum[k] - RouteTurns.Last().Cum < TurnMergeCm)
		{
			if (FMath::Abs(Ang) > FMath::Abs(RouteTurns.Last().AngleDeg))
			{
				RouteTurns.Last() = {RouteCum[k], Ang};
			}
			continue;
		}
		RouteTurns.Add({RouteCum[k], Ang});
	}
	RouteSeg = 0;
	RouteProgressCm = 0.0f;
	RouteRemainingCm = RouteCum.Last();
	return true;
}

bool AODRoadManager::SetRouteDestination(const FVector2D& Dest, const FString& Name)
{
	const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Player || !HasRouteGraph())
	{
		return false;
	}
	const FVector2D OldDest = RouteDest;
	RouteDest = Dest;
	if (!RecomputeRoute(FVector2D(Player->GetActorLocation())))
	{
		RouteDest = OldDest;
		return false;
	}
	RouteName = Name;
	LastRerouteTime = GetWorld()->GetTimeSeconds();
	return true;
}

void AODRoadManager::ClearRoute()
{
	RoutePts.Reset();
	RouteCum.Reset();
	RouteTurns.Reset();
	RouteName.Reset();
	RouteRemainingCm = 0.0f;
}

bool AODRoadManager::GetNextTurn(float& OutDistCm, float& OutAngleDeg) const
{
	for (const FRouteTurn& T : RouteTurns)
	{
		if (T.Cum > RouteProgressCm)
		{
			OutDistCm = T.Cum - RouteProgressCm;
			OutAngleDeg = T.AngleDeg;
			return true;
		}
	}
	return false;
}

void AODRoadManager::UpdateRouteProgress(const FVector2D& P)
{
	// windowed nearest-segment search around the last known segment (the
	// player can't cover 240 x ~5 m nodes between frames); a teleport/fast
	// travel just reads as "off route" and reroutes below
	const int32 Lo = FMath::Max(0, RouteSeg - 12);
	const int32 Hi = FMath::Min(RoutePts.Num() - 2, RouteSeg + 240);
	double BestD2 = TNumericLimits<double>::Max();
	for (int32 i = Lo; i <= Hi; ++i)
	{
		const FVector2D A = RoutePts[i], D = RoutePts[i + 1] - A;
		const double DD = D.SizeSquared();
		const double T = DD > 1.0 ? FMath::Clamp(FVector2D::DotProduct(P - A, D) / DD, 0.0, 1.0) : 0.0;
		const double D2 = FVector2D::DistSquared(P, A + D * T);
		if (D2 < BestD2)
		{
			BestD2 = D2;
			RouteSeg = i;
			RouteProgressCm = RouteCum[i] + (float)(T * FMath::Sqrt(DD));
		}
	}
	RouteRemainingCm = FMath::Max(0.0f, RouteCum.Last() - RouteProgressCm);
	const double Now = GetWorld()->GetTimeSeconds();
	if (RouteRemainingCm < ArriveCm && BestD2 < FMath::Square(OffRouteCm))
	{
		ArrivedTime = Now;
		ArrivedName = RouteName;
		ClearRoute();
		return;
	}
	if (BestD2 > FMath::Square(OffRouteCm) && Now - LastRerouteTime > RerouteCooldownS)
	{
		LastRerouteTime = Now;
		RecomputeRoute(P);   // failure (e.g. off on a disconnected piece) keeps the old line
	}
}

void AODRoadManager::LoadMapCenterline()
{
	if (MapCenterlineFilePrefix.IsEmpty())
	{
		return;   // unset - DrawStageMap falls back to RoadCenterline
	}
	TArray<uint8> Bin;
	const FString BinPath = FPaths::ProjectContentDir()
		/ TEXT("OpenDrive/Masks") / (MapCenterlineFilePrefix + TEXT(".bin"));
	if (!FFileHelper::LoadFileToArray(Bin, *BinPath) || Bin.Num() < 8
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'M' || Bin[3] != '1')
	{
		return;
	}
	const int32 N = (int32)ReadU32LE(Bin.GetData() + 4);
	constexpr int32 RecordBytes = 4 + 4;   // X_cm, Y_cm
	if (N <= 0 || Bin.Num() < 8 + (int64)N * RecordBytes)
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODRoadManager: %s truncated/corrupt (N=%d, %lld bytes)"),
			*BinPath, N, Bin.Num());
		return;
	}
	MapCenterline.SetNumUninitialized(N);
	const uint8* P = Bin.GetData() + 8;
	for (int32 i = 0; i < N; ++i, P += RecordBytes)
	{
		// NAN sentinel pairs round-trip fine through per-element ReadF32LE -
		// same double-widen-per-component convention as LoadCenterline above,
		// just that some "points" here are deliberately NAN (segment breaks).
		MapCenterline[i] = FVector2D(ReadF32LE(P), ReadF32LE(P + 4));
	}
	UE_LOG(LogTemp, Display,
		TEXT("ODRoadManager: loaded %d map-centerline point(s) (incl. breaks) from %s"),
		N, *BinPath);
}

void AODRoadManager::LoadPlaceLabels()
{
	if (PlaceLabelFilePrefix.IsEmpty())
	{
		return;   // feature off for this level actor instance
	}
	TArray<uint8> Bin;
	const FString BinPath = FPaths::ProjectContentDir()
		/ TEXT("OpenDrive/Masks") / (PlaceLabelFilePrefix + TEXT(".bin"));
	if (!FFileHelper::LoadFileToArray(Bin, *BinPath) || Bin.Num() < 4 + GenerationIdBytes + 4
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'L' || Bin[3] != '1')
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODRoadManager: place-label sidecar %s missing/unreadable - "
			     "labels will stay blank"), *BinPath);
		return;
	}
	// ODL1 header: magic(4) + generation id (pipeline/generation_id.py,
	// 2026-08-21 - see ODBinSidecarUtil.h) + point_count(4). Single fixed-
	// name file, not tile-bucketed, so there's no sibling tile to compare
	// this id against - read past it and move on.
	const FString LabelGenerationId = ReadGenerationId(Bin.GetData() + 4);
	const int32 N = (int32)ReadU32LE(Bin.GetData() + 4 + GenerationIdBytes);
	const uint8* Cur = Bin.GetData() + 4 + GenerationIdBytes + 4;
	const uint8* End = Bin.GetData() + Bin.Num();
	PlaceLabels.Reserve(N);
	for (int32 i = 0; i < N; ++i)
	{
		if (End - Cur < 8)
		{
			UE_LOG(LogTemp, Warning,
				TEXT("ODRoadManager: %s truncated at record %d/%d"), *BinPath, i, N);
			break;
		}
		FPlaceLabel Label;
		Label.Pos = FVector2D(ReadF32LE(Cur), ReadF32LE(Cur + 4));
		Cur += 8;
		if (!ReadPascalUtf8(Cur, End, Label.RoadLabel)
			|| !ReadPascalUtf8(Cur, End, Label.CityLabel))
		{
			UE_LOG(LogTemp, Warning,
				TEXT("ODRoadManager: %s truncated mid-string at record %d/%d"),
				*BinPath, i, N);
			break;
		}
		PlaceLabels.Add(MoveTemp(Label));
	}
	UE_LOG(LogTemp, Display,
		TEXT("ODRoadManager: loaded %d place label(s) from %s (generation id %s)"),
		PlaceLabels.Num(), *BinPath, *LabelGenerationId);
}

void AODRoadManager::UpdateCurrentLabels(const FVector& PlayerLoc)
{
	if (PlaceLabels.Num() == 0)
	{
		return;   // leave CurrentRoadLabel/CurrentCityLabel blank - HUD
		          // already treats that as "draw nothing"
	}
	const FVector2D Loc2D(PlayerLoc);
	int32 BestIdx = 0;
	double BestDistSq = FVector2D::DistSquared(Loc2D, PlaceLabels[0].Pos);
	for (int32 i = 1; i < PlaceLabels.Num(); ++i)
	{
		const double DistSq = FVector2D::DistSquared(Loc2D, PlaceLabels[i].Pos);
		if (DistSq < BestDistSq)
		{
			BestDistSq = DistSq;
			BestIdx = i;
		}
	}
	CurrentRoadLabel = PlaceLabels[BestIdx].RoadLabel;
	CurrentCityLabel = PlaceLabels[BestIdx].CityLabel;
}

bool AODRoadManager::NearestRoadPoint(const FVector2D& Query, FVector2D& OutPoint,
	float& OutYawDeg) const
{
	const int32 N = RoadCenterline.Num();
	if (N == 0)
	{
		return false;
	}
	int32 Best = 0;
	double BestSq = FVector2D::DistSquared(Query, RoadCenterline[0]);
	for (int32 i = 1; i < N; ++i)
	{
		const double D = FVector2D::DistSquared(Query, RoadCenterline[i]);
		if (D < BestSq)
		{
			BestSq = D;
			Best = i;
		}
	}
	// Pull the snap point in from segment ends (2026-09-01, Frisco spawn bug):
	// town labels can snap to the terminal station of a dead-end segment, which
	// parks half the spawned car past the pavement's end edge - the settle drop
	// then bounced it backwards off the road entirely. RoadCenterline is all
	// segments concatenated with no explicit break markers, so detect segment
	// boundaries by the spacing jump (in-segment stations are ~3-5m apart) and
	// walk Best far enough inward that a full car length sits on pavement.
	const double BreakDistSq = 2500.0 * 2500.0;   // >25m gap = different segment
	auto IsBreakBefore = [&](int32 i)   // break between i-1 and i
	{
		return i <= 0 || i >= N
			|| FVector2D::DistSquared(RoadCenterline[i - 1], RoadCenterline[i]) > BreakDistSq;
	};
	int32 Lo = Best, Hi = Best;
	while (!IsBreakBefore(Lo) && Best - Lo < 8) { --Lo; }
	while (!IsBreakBefore(Hi + 1) && Hi - Best < 8) { ++Hi; }
	const double EndMarginCm = 1200.0;   // ~1.5 car lengths of pavement each way
	auto DistFrom = [&](int32 A, int32 B)   // along-segment cm between stations
	{
		double D = 0.0;
		for (int32 i = A; i < B; ++i)
		{
			D += FVector2D::Distance(RoadCenterline[i], RoadCenterline[i + 1]);
		}
		return D;
	};
	while (Best < Hi && DistFrom(Lo, Best) < EndMarginCm
		&& DistFrom(Best, Hi) > EndMarginCm) { ++Best; }
	while (Best > Lo && DistFrom(Best, Hi) < EndMarginCm
		&& DistFrom(Lo, Best) > EndMarginCm) { --Best; }

	OutPoint = RoadCenterline[Best];
	// road heading from the local tangent (forward neighbor, or backward at the
	// segment's end). World frame X=east, Y=south, so yaw = atan2(dy,dx) maps
	// straight through - same convention the HUD player arrow uses.
	const FVector2D Tan = (Best < Hi)
		? RoadCenterline[Best + 1] - RoadCenterline[Best]
		: RoadCenterline[Best] - RoadCenterline[Best - 1];
	OutYawDeg = FMath::RadiansToDegrees(FMath::Atan2(Tan.Y, Tan.X));
	return true;
}

void AODRoadManager::BuildTownNodes()
{
	TownNodes.Reset();
	if (PlaceLabels.Num() == 0)
	{
		return;   // no place data (e.g. Tail of the Dragon) - map-select falls
		          // back to PlayerStart
	}
	// Dedup the ~50 m-spaced label points into one node per unique town: the
	// representative position is the centroid of that town's label points
	// (they already sit on/near the road), then snapped to the exact nearest
	// centerline point so a spawn there lands on pavement.
	struct FAccum { FVector2D Sum = FVector2D::ZeroVector; int32 Count = 0; };
	TMap<FString, FAccum> ByName;
	for (const FPlaceLabel& L : PlaceLabels)
	{
		if (L.CityLabel.IsEmpty())
		{
			continue;
		}
		FAccum& A = ByName.FindOrAdd(L.CityLabel);
		A.Sum += L.Pos;
		++A.Count;
	}
	for (const TPair<FString, FAccum>& Pair : ByName)
	{
		const FVector2D Centroid = Pair.Value.Sum / FMath::Max(1, Pair.Value.Count);
		FTownNode Node;
		Node.Name = Pair.Key;
		FVector2D Snapped;
		float Yaw;
		Node.Pos = NearestRoadPoint(Centroid, Snapped, Yaw) ? Snapped : Centroid;
		TownNodes.Add(MoveTemp(Node));
	}
	// stable, human-friendly order for the map labels
	TownNodes.Sort([](const FTownNode& A, const FTownNode& B)
	{
		return A.Name < B.Name;
	});
	UE_LOG(LogTemp, Display, TEXT("AODRoadManager: %d town node(s) for the menu map"),
		TownNodes.Num());
}

void AODRoadManager::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	if (PendingTiles.Num() > 0)
	{
		// New to this module: nothing else here has had a lifetime-crossing
		// async op to clean up before. Thread-pool work in flight can't be
		// cancelled - OnTileParsed's WeakThis/GetWorld() guard makes a
		// post-teardown completion a safe no-op, so this is expected, not a
		// bug, if it fires (e.g. PIE stopped mid-drive with loads in flight).
		UE_LOG(LogTemp, Display,
			TEXT("ODRoadManager: EndPlay with %d tile load(s) still in flight - "
			     "their completions will no-op harmlessly when they land"),
			PendingTiles.Num());
	}
	Super::EndPlay(EndPlayReason);
}

bool AODRoadManager::ParseRoadTileFileV2(const FString& Path, FParsedRoadTile& Out)
{
	// ODR2 magic+LE-header+payload sidecar convention (2026-07-25: was ODR1,
	// bumped when the per-chunk oneway byte was added - reader/writer are
	// always regenerated together in this pipeline, no back-compat needed,
	// the bump just makes a stale/mismatched pair fail loudly via this magic
	// check instead of silently misreading the new byte as vertex data).
	// Pure data parse, no UObject/actor state touched, safe off the game
	// thread.
	TArray<uint8> Bin;
	if (!FFileHelper::LoadFileToArray(Bin, *Path) || Bin.Num() < 4 + GenerationIdBytes + 4
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'R' || Bin[3] != '2')
	{
		return false;
	}

	const uint8* Cur = Bin.GetData() + 4;
	const uint8* End = Bin.GetData() + Bin.Num();
	auto Remaining = [&]() -> int64 { return (int64)(End - Cur); };

	if (Remaining() < GenerationIdBytes)
	{
		return false;
	}
	Out.GenerationId = ReadGenerationId(Cur);
	Cur += GenerationIdBytes;

	if (Remaining() < 4)
	{
		return false;
	}
	const uint32 ChunkCount = ReadU32LE(Cur);
	Cur += 4;

	Out.Chunks.Reserve(ChunkCount);
	for (uint32 c = 0; c < ChunkCount; ++c)
	{
		if (Remaining() < 9)
		{
			return false;
		}
		const uint32 VCount = ReadU32LE(Cur); Cur += 4;
		const uint32 TCount = ReadU32LE(Cur); Cur += 4;
		const bool bOneway = *Cur != 0; Cur += 1;
		const int64 NeedBytes = (int64)VCount * 12 + (int64)VCount * 8 + (int64)TCount * 12;
		if (Remaining() < NeedBytes)
		{
			return false;
		}

		const uint8* VertsBase = Cur;
		const uint8* UvBase = Cur + (int64)VCount * 12;
		const uint8* TrisBase = Cur + (int64)VCount * 12 + (int64)VCount * 8;
		Cur += NeedBytes;

		FParsedChunk& Chunk = Out.Chunks.AddDefaulted_GetRef();
		Chunk.bOneway = bOneway;
		Chunk.Verts.SetNumUninitialized(VCount * 3);
		for (uint32 v = 0; v < VCount; ++v)
		{
			Chunk.Verts[v * 3 + 0] = ReadF32LE(VertsBase + v * 12 + 0);
			Chunk.Verts[v * 3 + 1] = ReadF32LE(VertsBase + v * 12 + 4);
			Chunk.Verts[v * 3 + 2] = ReadF32LE(VertsBase + v * 12 + 8);
		}
		Chunk.Uvs.SetNumUninitialized(VCount * 2);
		for (uint32 v = 0; v < VCount; ++v)
		{
			Chunk.Uvs[v * 2 + 0] = ReadF32LE(UvBase + v * 8 + 0);
			Chunk.Uvs[v * 2 + 1] = ReadF32LE(UvBase + v * 8 + 4);
		}
		Chunk.Tris.SetNumUninitialized(TCount * 3);
		for (uint32 t = 0; t < TCount; ++t)
		{
			Chunk.Tris[t * 3 + 0] = ReadU32LE(TrisBase + t * 12 + 0);
			Chunk.Tris[t * 3 + 1] = ReadU32LE(TrisBase + t * 12 + 4);
			Chunk.Tris[t * 3 + 2] = ReadU32LE(TrisBase + t * 12 + 8);
		}
	}
	return true;
}

bool AODRoadManager::ParseRoadTileFileV3(const FString& Path, FParsedRoadTile& Out)
{
	// 'O','D','R','3' + generation id + chunk_count, then per chunk:
	// vertex_count, tri_count, oneway, road_class_id, lane_count, verts
	// (xyz), uv0 (u0,v0), uv1 (u1,v1), tris - see
	// pipeline/export_road_mesh_bin_v2.py's module docstring for the
	// authoritative byte-for-byte layout this mirrors. Same "bail out on any
	// truncation/short read, never trust the declared counts blindly"
	// convention as ParseRoadTileFileV2 above - pure data parse, safe off
	// the game thread.
	TArray<uint8> Bin;
	if (!FFileHelper::LoadFileToArray(Bin, *Path) || Bin.Num() < 4 + GenerationIdBytes + 4
		|| Bin[0] != 'O' || Bin[1] != 'D' || Bin[2] != 'R' || Bin[3] != '3')
	{
		return false;
	}

	const uint8* Cur = Bin.GetData() + 4;
	const uint8* End = Bin.GetData() + Bin.Num();
	auto Remaining = [&]() -> int64 { return (int64)(End - Cur); };

	if (Remaining() < GenerationIdBytes)
	{
		return false;
	}
	Out.GenerationId = ReadGenerationId(Cur);
	Cur += GenerationIdBytes;

	if (Remaining() < 4)
	{
		return false;
	}
	const uint32 ChunkCount = ReadU32LE(Cur);
	Cur += 4;

	Out.Chunks.Reserve(ChunkCount);
	for (uint32 c = 0; c < ChunkCount; ++c)
	{
		if (Remaining() < 11)   // vertex_count(4) + tri_count(4) + oneway(1) + road_class_id(1) + lane_count(1)
		{
			return false;
		}
		const uint32 VCount = ReadU32LE(Cur); Cur += 4;
		const uint32 TCount = ReadU32LE(Cur); Cur += 4;
		const uint8 Flags = *Cur; Cur += 1;   // bit0 oneway, bits1-2 surface class
		const uint8 RoadClassId = *Cur; Cur += 1;
		const uint8 LaneCount = *Cur; Cur += 1;
		// xyz(12) + uv0(8) + uv1(8) per vertex, i0/i1/i2(12) per tri
		const int64 NeedBytes = (int64)VCount * 12 + (int64)VCount * 8 + (int64)VCount * 8 + (int64)TCount * 12;
		if (Remaining() < NeedBytes)
		{
			return false;
		}

		const uint8* VertsBase = Cur;
		const uint8* Uv0Base = Cur + (int64)VCount * 12;
		const uint8* Uv1Base = Uv0Base + (int64)VCount * 8;
		const uint8* TrisBase = Uv1Base + (int64)VCount * 8;
		Cur += NeedBytes;

		FParsedChunk& Chunk = Out.Chunks.AddDefaulted_GetRef();
		Chunk.bOneway = (Flags & 1) != 0;
		Chunk.SurfaceClass = (Flags >> 1) & 3;
		Chunk.RoadClassId = RoadClassId;
		Chunk.LaneCount = LaneCount;
		Chunk.bHasV3Data = true;
		Chunk.Verts.SetNumUninitialized(VCount * 3);
		for (uint32 v = 0; v < VCount; ++v)
		{
			Chunk.Verts[v * 3 + 0] = ReadF32LE(VertsBase + v * 12 + 0);
			Chunk.Verts[v * 3 + 1] = ReadF32LE(VertsBase + v * 12 + 4);
			Chunk.Verts[v * 3 + 2] = ReadF32LE(VertsBase + v * 12 + 8);
		}
		Chunk.Uvs.SetNumUninitialized(VCount * 2);
		for (uint32 v = 0; v < VCount; ++v)
		{
			Chunk.Uvs[v * 2 + 0] = ReadF32LE(Uv0Base + v * 8 + 0);
			Chunk.Uvs[v * 2 + 1] = ReadF32LE(Uv0Base + v * 8 + 4);
		}
		Chunk.Uv1.SetNumUninitialized(VCount * 2);
		for (uint32 v = 0; v < VCount; ++v)
		{
			Chunk.Uv1[v * 2 + 0] = ReadF32LE(Uv1Base + v * 8 + 0);
			Chunk.Uv1[v * 2 + 1] = ReadF32LE(Uv1Base + v * 8 + 4);
		}
		Chunk.Tris.SetNumUninitialized(TCount * 3);
		for (uint32 t = 0; t < TCount; ++t)
		{
			Chunk.Tris[t * 3 + 0] = ReadU32LE(TrisBase + t * 12 + 0);
			Chunk.Tris[t * 3 + 1] = ReadU32LE(TrisBase + t * 12 + 4);
			Chunk.Tris[t * 3 + 2] = ReadU32LE(TrisBase + t * 12 + 8);
		}
	}
	return true;
}

void AODRoadManager::RequestTileLoad(const FIntPoint& Key)
{
	PendingTiles.Add(Key);
	const FString PathV2 = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks")
		/ FString::Printf(TEXT("%s_tile_%d_%d.bin"), *RoadTilePrefix, Key.X, Key.Y);

	// ODR3 lives in a parallel MasksV2/ dir under a "roadv3_" + (RoadTilePrefix
	// with any leading "road_" stripped) name - see RoadTilePrefix's header
	// comment for why. Computed here (not cached) since it's cheap and this
	// only runs per tile-load dispatch, not per frame.
	FString V3Suffix = RoadTilePrefix;
	V3Suffix.RemoveFromStart(TEXT("road_"));
	const FString PathV3 = FPaths::ProjectContentDir() / TEXT("OpenDrive/MasksV2")
		/ FString::Printf(TEXT("roadv3_%s_tile_%d_%d.bin"), *V3Suffix, Key.X, Key.Y);

	// worker-thread lambda captures ONLY plain copied-by-value inputs (paths,
	// key) and a weak ptr for the eventual game-thread hop - it must never
	// touch Tiles/PendingTiles/MissingTiles or any other actor state, which
	// isn't synchronized for cross-thread access.
	TWeakObjectPtr<AODRoadManager> WeakThis(this);
	const bool bEdgeCodes = ODVisualStyle::IsRealistic();   // read here: GConfig is game-thread
	Async(EAsyncExecution::ThreadPool, [WeakThis, Key, PathV2, PathV3, bEdgeCodes]()
	{
		FParsedRoadTile Parsed;
		// ODR3 preferred when present (worn-asphalt/lane-marking data) -
		// falls back to the plain ODR2 tile otherwise so nothing regresses
		// on areas that haven't been re-baked through
		// export_road_mesh_bin_v2.py yet (see RoadTilePrefix's comment).
		FString ReadPath;
		EODTileSource Src = ODTileSource::Resolve(PathV3, ReadPath);
		bool bOk = Src == EODTileSource::Ready && ParseRoadTileFileV3(ReadPath, Parsed);
		if (!bOk && Src != EODTileSource::Pending)
		{
			Src = ODTileSource::Resolve(PathV2, ReadPath);
			bOk = Src == EODTileSource::Ready && ParseRoadTileFileV2(ReadPath, Parsed);
		}
		const bool bPending = Src == EODTileSource::Pending;
		if (bOk && bEdgeCodes)
		{
			ComputeEdgeCodes(Parsed.Chunks);
		}
		AsyncTask(ENamedThreads::GameThread,
			[WeakThis, Key, bOk, bPending, Parsed = MoveTemp(Parsed)]() mutable
			{
				// a queued GameThread task can in principle run a frame or
				// two after world teardown starts even if the actor object
				// itself hasn't been GC'd yet - check both.
				if (!WeakThis.IsValid() || WeakThis->GetWorld() == nullptr)
				{
					return;
				}
				if (bPending)
				{
					ODTileSource::RetryLater(WeakThis.Get(), WeakThis->PendingTiles, Key);
					return;
				}
				WeakThis->OnTileParsed(Key, bOk, MoveTemp(Parsed));
			});
	});
}

void AODRoadManager::OnTileParsed(const FIntPoint& Key, bool bOk, FParsedRoadTile&& Parsed)
{
	if (!bOk)
	{
		PendingTiles.Remove(Key);
		MissingTiles.Add(Key);
		return;
	}

	if (!bHasFirstGenerationId)
	{
		FirstGenerationId = Parsed.GenerationId;
		bHasFirstGenerationId = true;
	}
	else if (!bGenerationMismatchWarned && Parsed.GenerationId != FirstGenerationId)
	{
		bGenerationMismatchWarned = true;
		UE_LOG(LogTemp, Warning,
			TEXT("ODRoadManager: tile (%d,%d) generation id %s does not match "
			     "%s's first-loaded generation id %s - tiles from different pipeline "
			     "runs may be mixed (AUDIT-RELIABILITY.md SS3.1/SS5.1)"),
			Key.X, Key.Y, *Parsed.GenerationId, *RoadTilePrefix, *FirstGenerationId);
	}

	// No runtime ground-snap/wait-for-terrain step needed (Arc 7.2 restructure,
	// 2026-07-20, superseded by the Arc 7.0 baked-terrain pivot on 2026-07-23):
	// the pipeline now carves the offline-baked terrain tiles to the road's
	// elevation profile offline (pipeline/build_terrain_mesh_tiles.py +
	// road_mesh_common.carve_heightmap), verified by pipeline/verify_terrain_fit.py
	// against the ACTUAL simplified mesh before it ever reaches the engine, so
	// station Z values arriving here are already correct.
	//
	// Chunk *components* are NOT all built this frame though (2026-08-25,
	// Taylor: full-map junction-pad collision investigation) - a tile with
	// dozens of chunks used to build every UDynamicMeshComponent and enable
	// its complex-as-simple collision in one synchronous burst here. Isolated
	// via headless multi-trace diagnostics: with ~260 chunks registering their
	// physics state in the same frame (the full map's near-tile count), some
	// components' own state (BodyInstance.IsValidBodyInstance(),
	// GetCollisionEnabled()) reported perfectly valid, correct position/bounds,
	// yet the world's physics scene query never found them - even 1000+ frames
	// later. The identical geometry in --solo (5 chunks, no burst) worked
	// instantly. Root cause looks like a Chaos/UE5.8 physics-scene broadphase
	// registration bug under bulk same-frame creation, not anything wrong with
	// this class's own bookkeeping. Spreading chunk creation across frames
	// (ChunksPerFrameBudget, see ProcessPendingChunkBuilds) avoids ever hitting
	// that many same-frame registrations at once.
	//
	// Key stays in PendingTiles (not removed here) until the queued build
	// actually finishes in ProcessPendingChunkBuilds - Tick()'s dispatch guard
	// (Tiles/PendingTiles/MissingTiles) must keep treating this tile as
	// "already being handled" for however many frames that now takes.
	FPendingTileBuild& Build = PendingChunkBuilds.AddDefaulted_GetRef();
	Build.Key = Key;
	Build.Parsed = MoveTemp(Parsed);
	Build.InProgress.Chunks.Reserve(Build.Parsed.Chunks.Num());
}

void AODRoadManager::ProcessPendingChunkBuilds()
{
	// Millisecond budget, not a chunk count (2026-08-27, fast-drive hitch
	// investigation) - see ChunkBuildMsBudget's comment. Always finishes at
	// least one chunk per tick (the ElapsedMs check only stops the inner loop
	// AFTER a chunk completes) so the queue can't stall even if a single
	// chunk alone busts the budget.
	const double StartTime = FPlatformTime::Seconds();
	double ElapsedMs = 0.0;
	int32 NumChunksBuiltThisTick = 0;
	const FTransform CompXf = GetActorTransform();   // identity today (actor
	                                                  // always spawned at world
	                                                  // origin) - kept explicit
	                                                  // rather than assumed.
	while (PendingChunkBuilds.Num() > 0 && (NumChunksBuiltThisTick == 0 || ElapsedMs < ChunkBuildMsBudget))
	{
		FPendingTileBuild& Build = PendingChunkBuilds[0];
		while (Build.NextChunkIndex < Build.Parsed.Chunks.Num()
			&& (NumChunksBuiltThisTick == 0 || ElapsedMs < ChunkBuildMsBudget))
		{
			BuildOneChunk(Build.Key, Build.InProgress, Build.Parsed.Chunks[Build.NextChunkIndex], CompXf);
			++Build.NextChunkIndex;
			++NumChunksBuiltThisTick;
			ElapsedMs = (FPlatformTime::Seconds() - StartTime) * 1000.0;
		}

		if (Build.NextChunkIndex < Build.Parsed.Chunks.Num())
		{
			break;   // budget spent mid-tile - finish it next tick
		}

		const FIntPoint Key = Build.Key;
		const int32 NumChunks = Build.InProgress.Chunks.Num();
		Tiles.Add(Key, MoveTemp(Build.InProgress));
		PendingTiles.Remove(Key);
		PendingChunkBuilds.RemoveAt(0);

		UE_LOG(LogTemp, Display,
			TEXT("ODRoadManager: tile (%d,%d) built %d chunk(s)"),
			Key.X, Key.Y, NumChunks);
	}
}

// Instrumentation (2026-08-27, fast-drive hitch investigation): per-stage
// game-thread timing for one road chunk build - see CreateChunkMeshComponent
// below for the stage boundaries. Logged unconditionally per chunk (not
// throttled - per-build events), plus a Warning when a single chunk's total
// exceeds WarnThresholdMs.
static constexpr double RoadChunkBuildWarnThresholdMs = 8.0;

UDynamicMeshComponent* AODRoadManager::CreateChunkMeshComponent(
	const FParsedChunk& Chunk, const FTransform& CompXf, bool bRecordSkirtSamples)
{
	const double StageStart = FPlatformTime::Seconds();
	double TCreate = 0.0, TCollisionSetup = 0.0, TMeshBuild = 0.0, TMaterial = 0.0, TRegister = 0.0;
	auto Lap = [](double& Since) -> double
	{
		const double Now = FPlatformTime::Seconds();
		const double DeltaMs = (Now - Since) * 1000.0;
		Since = Now;
		return DeltaMs;
	};
	double Clock = StageStart;

	const uint32 VCount = Chunk.Verts.Num() / 3;
	const uint32 TCount = Chunk.Tris.Num() / 3;

	auto* Comp = NewObject<UDynamicMeshComponent>(this);
	Comp->SetupAttachment(RootComponent);
	// Large-world precision (2026-10-02): vertices are stored relative to the
	// chunk's first vertex so a chunk hundreds of km from the world origin
	// doesn't render/collide with float-ulp (4 cm at 650 km) error. Z stays
	// absolute. ponytail: the .bin verts are still float, so far-out chunks keep
	// a static +-2 cm quantization; store per-chunk origins in the format if it shows.
	const FVector3d Origin = VCount ? FVector3d(Chunk.Verts[0], Chunk.Verts[1], 0.0) : FVector3d::ZeroVector;
	Comp->SetRelativeLocation(Origin);
	// Async collision cook (2026-08-27, fast-drive hitch fix): set BEFORE
	// EnableComplexAsSimpleCollision below so whichever call actually
	// triggers RebuildPhysicsData (immediately here, or later when
	// RegisterComponent's own collision refresh picks up EditMesh's mesh
	// change) honors the async path - UDynamicMeshComponent::bUseAsyncCooking
	// dispatches the triangle-mesh cook to the thread pool
	// (BodySetup->CreatePhysicsMeshesAsync) and only does the cheap
	// RecreatePhysicsState() on the game thread once the cook completes,
	// instead of cooking synchronously inline. This does NOT change how many
	// chunks call RegisterComponent() in a given frame (still exactly the
	// ChunkBuildMsBudget-gated set below) - only the expensive cook itself
	// moves off the game thread, so it doesn't add to the same-frame
	// collision-REGISTRATION count the bulk-registration Chaos bug (see
	// ChunkBuildMsBudget's comment) is about.
	Comp->bUseAsyncCooking = true;
	TCreate = Lap(Clock);

	// 2026-08-26: RegisterComponent() used to run before EditMesh() ever
	// populated geometry, and EnableComplexAsSimpleCollision()/
	// SetCollisionProfileName() used to run AFTER EditMesh(). Neither
	// reorder alone fixed the open bug (a merged host+pad chunk's disc
	// portion silently missing from collision scene queries despite every
	// component-level health check - registered/visible/valid body/correct
	// triangle count/now-correct single-connected topology via
	// bridge_pad_to_host - reporting fine) but both are still correct
	// lifecycle fixes to keep: set the FINAL collision type/profile before
	// the mesh's first EditMesh populates it, so whatever rebuild EditMesh
	// triggers already sees the right collision settings, and only
	// register once mesh + collision are both fully final.
	Comp->EnableComplexAsSimpleCollision();
	Comp->SetCollisionProfileName(TEXT("BlockAll"));
	TCollisionSetup = Lap(Clock);

	// ODR3 data available for this chunk (Chunk.bHasV3Data) - road_class_id/
	// lane_count/oneway/a per-chunk wear-variety seed get baked into vertex
	// COLOR (not a per-chunk MaterialInstanceDynamic - one road-mesh tile can
	// be dozens of chunks, and a MID per chunk means a real material
	// instance object + per-draw param binding for every one of them; a
	// vertex-color channel is free bandwidth already flowing through the
	// existing render path, same trick AODTerrainManager::ApplyBiomeTint
	// already uses for its own per-vertex biome signal - see that class for
	// precedent). The UV1 overlay carries the one value that's genuinely
	// continuous/per-vertex today (pavement half-width, meters) so the
	// material can convert u0's 0..1 pavement-normalized coordinate into a
	// real world-space meters offset for constant-width lane markings
	// regardless of road class.
	const bool bHasV3 = Chunk.bHasV3Data && Chunk.Uv1.Num() == (int32)(VCount * 2);
	// Stable per-chunk pseudo-random 0..1 (hash of the chunk's first vertex
	// position) - purely a texture-variety seed for M_Road's wear-noise
	// offset, not meant to be reproducible or cryptographic.
	float ChunkSeed = 0.0f;
	if (bHasV3 && VCount > 0)
	{
		const uint32 H = GetTypeHash(FVector(Chunk.Verts[0], Chunk.Verts[1], Chunk.Verts[2]));
		ChunkSeed = (float)(H % 10000) / 10000.0f;
	}

	Comp->EditMesh([&](FDynamicMesh3& Mesh)
	{
		Mesh.EnableAttributes();
		FDynamicMeshUVOverlay* UV = Mesh.Attributes()->PrimaryUV();
		// Always 3 layers (2026-10-06): M_Road reads TexCoord[1]/[2] and must
		// see zeros on a non-V3 chunk, not a clamped-to-UV0 read. UV2 = snow
		// berm direction, see BermDir below. UV3.x = puddle sag (RoadSagCm),
		// UV3.y = Realistic edge code (Chunk.EdgeCode, else 0).
		Mesh.Attributes()->SetNumUVLayers(4);
		FDynamicMeshUVOverlay* UV1 = Mesh.Attributes()->GetUVLayer(1);
		FDynamicMeshUVOverlay* UV2 = Mesh.Attributes()->GetUVLayer(2);
		FDynamicMeshUVOverlay* UV3 = Mesh.Attributes()->GetUVLayer(3);
		const TArray<float> SagCm = RoadSagCm(Chunk.Verts, Chunk.Uvs, (int32)VCount);
		TArray<int32> Uv3Ids; Uv3Ids.SetNumUninitialized(VCount);
		TMap<TPair<uint32, float>, int32> EdgeIds;   // (vertex, EdgeCode) -> UV3 element
		for (uint32 v = 0; v < VCount; ++v)
		{
			Uv3Ids[v] = UV3->AppendElement(FVector2f(SagCm[v], 0.0f));
		}
		FDynamicMeshColorOverlay* Colors = nullptr;
		if (bHasV3)
		{
			Mesh.Attributes()->EnablePrimaryColors();
			Colors = Mesh.Attributes()->PrimaryColors();
		}

		TArray<int32> VertIds; VertIds.SetNumUninitialized(VCount);
		TArray<int32> UvIds; UvIds.SetNumUninitialized(VCount);
		TArray<int32> Uv1Ids; Uv1Ids.SetNumUninitialized(VCount);
		TArray<int32> Uv2Ids; Uv2Ids.SetNumUninitialized(VCount);
		TArray<int32> ColorIds; if (Colors) { ColorIds.SetNumUninitialized(VCount); }

		for (uint32 v = 0; v < VCount; ++v)
		{
			const float X = Chunk.Verts[v * 3 + 0];
			const float Y = Chunk.Verts[v * 3 + 1];
			const float Z = Chunk.Verts[v * 3 + 2];
			VertIds[v] = Mesh.AppendVertex(FVector3d(X - Origin.X, Y - Origin.Y, Z));

			// lip-bottom sample for the -odseam terrain check: every
			// 10th station's outer two verts (index 1/4 of the
			// under-L,lip-L,pave-L,pave-R,lip-R,under-R,under-L layout
			// build_road_chunks emits - road_mesh_common.ROAD_PROFILE_COLS
			// = 7 verts per station) - same convention as slice 1, just
			// filled in incrementally as tiles load now.
			// Skipped on a physics-rebuild pass (bRecordSkirtSamples=false)
			// since those points were already recorded on this chunk's
			// original build - recording again would duplicate them.
			constexpr uint32 ProfileCols = 7;
			const uint32 StationIdx = v / ProfileCols;
			const uint32 InStation = v % ProfileCols;
			if (bRecordSkirtSamples && (StationIdx % 10) == 0 && (InStation == 1 || InStation == 4))
			{
				SkirtSamplePoints.Add(CompXf.TransformPosition(FVector(X, Y, Z)));
			}
		}
		for (uint32 v = 0; v < VCount; ++v)
		{
			UvIds[v] = UV->AppendElement(
				FVector2f(Chunk.Uvs[v * 2 + 0], Chunk.Uvs[v * 2 + 1]));
		}
		if (bHasV3)
		{
			for (uint32 v = 0; v < VCount; ++v)
			{
				Uv1Ids[v] = UV1->AppendElement(
					FVector2f(Chunk.Uv1[v * 2 + 0], Chunk.Uv1[v * 2 + 1]));
			}
		}
		else
		{
			Uv1Ids.Init(UV1->AppendElement(FVector2f::ZeroVector), VCount);
		}
		// Snow berms (2026-10-06): M_Road's WPO lifts each wrap-lip vert
		// (columns 1/4, 0.5 m out / 0.6 m below the pavement edge) up and
		// out along this outward XY unit vector by the roadside snow depth -
		// a bank rides the existing ribbon, no extra geometry or collision.
		// Zero elsewhere; faded by the pave vert's junction suppress so
		// banks stop at junctions. The drop/reach check skips junction-pad
		// verts merged after the ribbon, which don't follow the 7 columns.
		// Component-local XY = world XY (no actor/chunk rotation).
		for (uint32 v = 0; v < VCount; ++v)
		{
			FVector2f BermDir = FVector2f::ZeroVector;
			const uint32 Col = v % 7;
			const uint32 P = Col == 1 ? v + 1 : v - 1;   // its pave-L / pave-R
			if (bHasV3 && (Col == 1 || Col == 4) && P < VCount)
			{
				const FVector2f Out(Chunk.Verts[v * 3] - Chunk.Verts[P * 3],
				                    Chunk.Verts[v * 3 + 1] - Chunk.Verts[P * 3 + 1]);
				const float Drop = Chunk.Verts[P * 3 + 2] - Chunk.Verts[v * 3 + 2];
				const float Reach = Out.Size();
				if (Drop > 30.0f && Drop < 90.0f && Reach > 25.0f && Reach < 80.0f)
				{
					BermDir = Out / Reach * (1.0f - FMath::Clamp(Chunk.Uv1[P * 2 + 1], 0.0f, 1.0f));
				}
			}
			Uv2Ids[v] = UV2->AppendElement(BermDir);
		}
		if (Colors)
		{
			// R = road_class_id/6, G = lane_count/8, B = oneway (0/1),
			// A = per-chunk wear-variety seed - M_Road unpacks these back
			// into integers via round(R*6)/round(G*8) (see
			// unreal/apply_road_material.py's G.classify_from_vcolor()).
			const float R = (float)Chunk.RoadClassId / 6.0f;
			const float Gc = (float)Chunk.LaneCount / 8.0f;
			const float B = Chunk.bOneway ? 1.0f : 0.0f;
			const FVector4f VColor(R, Gc, B, ChunkSeed);
			for (uint32 v = 0; v < VCount; ++v)
			{
				ColorIds[v] = Colors->AppendElement(VColor);
			}
		}
		for (uint32 t = 0; t < TCount; ++t)
		{
			const uint32 I0 = Chunk.Tris[t * 3 + 0];
			const uint32 I1 = Chunk.Tris[t * 3 + 1];
			const uint32 I2 = Chunk.Tris[t * 3 + 2];
			const int32 Tid = Mesh.AppendTriangle(VertIds[I0], VertIds[I1], VertIds[I2]);
			if (Tid >= 0)
			{
				UV->SetTriangle(Tid, FIndex3i(UvIds[I0], UvIds[I1], UvIds[I2]));
				UV1->SetTriangle(Tid, FIndex3i(Uv1Ids[I0], Uv1Ids[I1], Uv1Ids[I2]));
				UV2->SetTriangle(Tid, FIndex3i(Uv2Ids[I0], Uv2Ids[I1], Uv2Ids[I2]));
				FIndex3i E3(Uv3Ids[I0], Uv3Ids[I1], Uv3Ids[I2]);
				if (Chunk.EdgeCode.Num() == (int32)TCount * 3)
				{
					for (int32 k = 0; k < 3; ++k)
					{
						const float Code = Chunk.EdgeCode[t * 3 + k];
						if (Code != 0.0f)
						{
							const uint32 V = Chunk.Tris[t * 3 + k];
							const TPair<uint32, float> Id(V, Code);
							const int32* Found = EdgeIds.Find(Id);
							E3[k] = Found ? *Found : EdgeIds.Add(Id, UV3->AppendElement(FVector2f(SagCm[V], Code)));
						}
					}
				}
				UV3->SetTriangle(Tid, E3);
				if (Colors)
				{
					Colors->SetTriangle(Tid, FIndex3i(ColorIds[I0], ColorIds[I1], ColorIds[I2]));
				}
			}
		}
	});
	TMeshBuild = Lap(Clock);

	// 2026-09-03 (Phase 2a road-visual overhaul): a single M_Road material now
	// covers both carriageway directions - the double-yellow-centerline-vs-
	// not distinction that used to require a whole separate M_Road_OneWay
	// asset is driven in-shader off the vertex-color oneway flag baked above
	// instead (see unreal/apply_road_material.py). RoadMatOneWay stays loaded
	// (unreal/apply_road_physmat.py still assigns it a physical material) but
	// is no longer selected here; falls back to RoadMatOneWay only if RoadMat
	// itself failed to load, rather than rendering nothing.
	// Gravel/dirt chunks (2026-10-06) get RoadMatGravel/RoadMatDirt when it
	// exists; a missing asset just falls back to asphalt.
	UMaterialInterface* ChunkMat = RoadMat ? RoadMat : RoadMatOneWay;
	if (UMaterialInterface* Unpaved = Chunk.SurfaceClass == 1 ? RoadMatGravel.Get()
		: Chunk.SurfaceClass == 2 ? RoadMatDirt.Get() : nullptr)
	{
		ChunkMat = Unpaved;
	}
	if (ChunkMat)
	{
		Comp->SetMaterial(0, ChunkMat);
	}
	TMaterial = Lap(Clock);

	// per-chunk snow depth (Custom Primitive Data 0 road / 1 roadside cm,
	// M_Road's SnowDepth/SideSnowDepth) starts at the ambient depth - see TickSnow
	if (const AODWeatherController* Wx = SnowWeather.Get())
	{
		Comp->SetCustomPrimitiveDataVector2(0, FVector2D(Wx->RoadSnowCm, Wx->SideSnowCm));
	}

	Comp->RegisterComponent();
	TRegister = Lap(Clock);

	const double TotalMs = (FPlatformTime::Seconds() - StageStart) * 1000.0;
	// TCollisionSetup is just the flag/profile-name assignment;
	// bUseAsyncCooking moves the actual physics cook off this synchronous
	// path (see Comp->bUseAsyncCooking's comment above) - the cook cost
	// would otherwise show up folded into TRegister (RegisterComponent is
	// what actually triggers RebuildPhysicsData once EditMesh has dirtied
	// the mesh), so a big TRegister here despite bUseAsyncCooking=true would
	// mean the async path isn't engaging as expected.
	UE_LOG(LogTemp, Verbose,
		TEXT("ODRoadManager: BuildOneChunk verts=%d tris=%d totalMs=%.2f ")
		TEXT("[create=%.2f collisionSetup=%.2f meshBuild=%.2f material=%.2f register=%.2f]"),
		VCount, TCount, TotalMs, TCreate, TCollisionSetup, TMeshBuild, TMaterial, TRegister);
	if (TotalMs > RoadChunkBuildWarnThresholdMs)
	{
		UE_LOG(LogTemp, Warning,
			TEXT("ODRoadManager: BuildOneChunk exceeded %.1fms threshold - totalMs=%.2f ")
			TEXT("[create=%.2f collisionSetup=%.2f meshBuild=%.2f material=%.2f register=%.2f]"),
			RoadChunkBuildWarnThresholdMs, TotalMs, TCreate, TCollisionSetup, TMeshBuild, TMaterial, TRegister);
	}

	return Comp;
}

void AODRoadManager::BuildOneChunk(const FIntPoint& Key, FRoadTile& Tile, const FParsedChunk& Chunk, const FTransform& CompXf)
{
	const uint32 VCount = Chunk.Verts.Num() / 3;
	const uint32 TCount = Chunk.Tris.Num() / 3;

	UDynamicMeshComponent* Comp = CreateChunkMeshComponent(Chunk, CompXf, /*bRecordSkirtSamples=*/true);
	Tile.Chunks.Add(Comp);
	if (Decals)
	{
		Decals->AddChunk(Key, Chunk, Comp);
	}

	// 2026-08-26 diagnostic: the earlier bulk-same-frame-registration theory
	// (see OnTileParsed's comment above) assumed the affected component's own
	// state is fully valid - registered, correct bounds, valid BodyInstance -
	// just invisible to Chaos scene queries. Taylor spotted live that the
	// failing junction pad doesn't RENDER either, which that theory can't
	// explain (render and physics registration are independent paths) - so
	// log the actual component state for chunks built near the known-failing
	// test junction to check whether this is really the same bug or a
	// different, more basic failure (e.g. the chunk never finishing
	// EditMesh/registration at all).
	// 2026-08-26: was checking only Chunk.Verts[0]/[1] (the chunk's FIRST
	// vertex) against the test point - missed the merged host+pad chunk
	// whenever the host ribbon's first vertex happened to be tens of meters
	// away from the junction it was fused near (the ribbon can span up to
	// MaxChunkSpanCm=75000cm). Check every vertex instead so a fused chunk is
	// never invisible to this diagnostic just because of vertex ordering.
	bool bNearTestPoint = false;
	for (int32 vi = 0; vi + 1 < Chunk.Verts.Num() && !bNearTestPoint; vi += 3)
	{
		if (FMath::Abs(Chunk.Verts[vi] - 356900.0f) < 2000.0f
			&& FMath::Abs(Chunk.Verts[vi + 1] - (-540300.0f)) < 2000.0f)
		{
			bNearTestPoint = true;
		}
	}
	if (bNearTestPoint)
	{
		const FBoxSphereBounds B = Comp->Bounds;
		UE_LOG(LogTemp, Display,
			TEXT("ODRoadManager: DIAG chunk near test point - comp=%s registered=%d visible=%d ")
			TEXT("collisionEnabled=%d bodyValid=%d numTris=%d bounds=(%.0f,%.0f,%.0f)+%.0f vcount=%d tcount=%d"),
			*Comp->GetName(), Comp->IsRegistered(), Comp->IsVisible(),
			(int32)Comp->GetCollisionEnabled(), Comp->BodyInstance.IsValidBodyInstance(),
			Comp->GetMesh() ? Comp->GetMesh()->TriangleCount() : -1,
			B.Origin.X, B.Origin.Y, B.Origin.Z, B.SphereRadius, VCount, TCount);

		// 2026-08-26: was restricted to TCount<=25 (the pad's own standalone
		// chunk, pre-merge) - now that the pad is fused into a much bigger
		// host ribbon chunk, that size no longer identifies it. Track
		// whichever near-test-point chunk was built most recently instead.
		DiagTargetComp = Comp;
		bDiagTargetEverSet = true;
	}

	// 2026-08-26: this used to unconditionally enqueue EVERY chunk (not just
	// the one known-broken junction pad) for a forced destroy-and-rebuild one
	// tick later, as a mitigation attempt for the open Chaos/UE5.8 phantom-
	// registration bug. That mitigation was already proven not to fix the
	// bug it targeted (live-tracked retest still failed identically), so
	// applying it to literally every chunk in the game bought nothing while
	// adding real risk: forcing a fresh async physics cook and component
	// swap for every chunk the instant after it's built, which is exactly
	// the ~20s post-spawn window every headless ground-snap self-test
	// measures in. Disabled rather than deleted so the machinery (struct,
	// ProcessPendingPhysicsRefresh, DiagTargetComp tracking) stays available
	// if a future bug needs the same forced-rebuild technique deliberately,
	// scoped to just the affected chunk.
}

void AODRoadManager::ProcessPendingPhysicsRefresh()
{
	for (FPendingPhysicsRebuild& Pending : PendingPhysicsRefresh)
	{
		UDynamicMeshComponent* OldComp = Pending.OldComp.Get();
		UDynamicMeshComponent* NewComp =
			CreateChunkMeshComponent(Pending.Chunk, Pending.CompXf, /*bRecordSkirtSamples=*/false);

		// swap the slot that references OldComp - the owning tile may
		// already have migrated from PendingChunkBuilds into Tiles by now
		// (or vice versa be mid-build still), so check both rather than
		// assuming which one currently holds it.
		bool bReplaced = false;
		if (FRoadTile* Tile = Tiles.Find(Pending.TileKey))
		{
			for (TObjectPtr<UDynamicMeshComponent>& Slot : Tile->Chunks)
			{
				if (Slot.Get() == OldComp)
				{
					Slot = NewComp;
					bReplaced = true;
					break;
				}
			}
		}
		if (!bReplaced)
		{
			for (FPendingTileBuild& Build : PendingChunkBuilds)
			{
				if (Build.Key != Pending.TileKey)
				{
					continue;
				}
				for (TObjectPtr<UDynamicMeshComponent>& Slot : Build.InProgress.Chunks)
				{
					if (Slot.Get() == OldComp)
					{
						Slot = NewComp;
						bReplaced = true;
						break;
					}
				}
				break;
			}
		}

		if (Pending.OldComp == DiagTargetComp)
		{
			UE_LOG(LogTemp, Display,
				TEXT("ODRoadManager: DIAG full-rebuild tracked target old=%s new=%s replacedInTile=%d"),
				OldComp ? *OldComp->GetName() : TEXT("null"), *NewComp->GetName(), bReplaced);
			DiagTargetComp = NewComp;
		}

		if (OldComp)
		{
			OldComp->DestroyComponent();
		}
	}
	PendingPhysicsRefresh.Reset();
}

void AODRoadManager::ReleaseTile(const FIntPoint& Key)
{
	if (FRoadTile* Tile = Tiles.Find(Key))
	{
		for (const auto& Comp : Tile->Chunks)
		{
			if (Comp)
			{
				Comp->DestroyComponent();
			}
		}
		Tiles.Remove(Key);
	}
	if (Decals)
	{
		Decals->ReleaseTile(Key);
	}
}

void AODRoadManager::RunSeamCheck()
{
	// The old live-streaming plugin only streamed terrain tiles near the
	// camera/pawn (proven in Arc 7.1 - it's view-driven LOD, not "load the
	// whole georeferenced bbox").
	// SkirtSamplePoints spans only whatever ROAD tiles have streamed in so
	// far (now incremental, slice 2), so a trace far from the anchor
	// legitimately hits nothing - that's not a seam problem, it's untested
	// terrain. Anchor on the captured spawn location, not the live pawn -
	// by the time a scripted drive has run long enough for terrain to have
	// streamed in, the pawn itself may have driven off this small test area
	// entirely, which would wrongly skip everything near the actual area
	// under test. (Was SkirtSamplePoints[0] - the first-ARRIVED skirt
	// sample, not the first-DISPATCHED one; thread-pool completion order
	// isn't ring order, so that landed 3.4 km from spawn on this network's
	// 18-tile load and reported a false n=0 - see the header comment.)
	const FVector Anchor = bSpawnAnchorCaptured ? SpawnAnchor
		: (SkirtSamplePoints.Num() > 0 ? SkirtSamplePoints[0] : FVector::ZeroVector);
	constexpr double MaxCheckDistCm = 200000.0;   // 2 km
	UE_LOG(LogTemp, Display,
		TEXT("ODRoadManager: seam check anchor=(%.0f,%.0f,%.0f) samples=%d"),
		Anchor.X, Anchor.Y, Anchor.Z, SkirtSamplePoints.Num());

	int32 N = 0, Over = 0, Under = 0, Skipped = 0;
	double SumAbs = 0.0, MaxAbs = 0.0;
	for (const FVector& P : SkirtSamplePoints)
	{
		if (FVector::DistSquared2D(P, Anchor) > MaxCheckDistCm * MaxCheckDistCm)
		{
			++Skipped;
			continue;
		}
		FHitResult Hit;
		const FVector Start = P + FVector(0, 0, 10000.0);
		const FVector End = P - FVector(0, 0, 10000.0);
		FCollisionQueryParams Params;
		Params.AddIgnoredActor(this);
		if (const APawn* Player = UGameplayStatics::GetPlayerPawn(this, 0))
		{
			Params.AddIgnoredActor(Player);
		}
		if (!GetWorld()->LineTraceSingleByChannel(Hit, Start, End, ECC_Visibility, Params))
		{
			continue;
		}
		const double Dz = (Hit.ImpactPoint.Z - P.Z) / 100.0;   // m; terrain - skirt
		++N;
		SumAbs += FMath::Abs(Dz);
		MaxAbs = FMath::Max(MaxAbs, FMath::Abs(Dz));
		if (Dz > 0.0) { ++Over; } else { ++Under; }
	}
	const double Mean = N > 0 ? SumAbs / N : 0.0;
	UE_LOG(LogTemp, Display,
		TEXT("ODRoadManager: seam n=%d mean|dz|=%.2f m max|dz|=%.2f m over=%d under=%d (skipped=%d far from player)"),
		N, Mean, MaxAbs, Over, Under, Skipped);
}

void AODRoadManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);

	// Runs before this tick's new builds so every component gets at least
	// one full tick registered before its forced destroy-and-rebuild pass -
	// see FPendingPhysicsRebuild's declaration comment.
	ProcessPendingPhysicsRefresh();
	ProcessPendingChunkBuilds();

	FVector Loc;
	if (!Vista.StreamCenter(this, Loc))
	{
		return;
	}
	if (!bSpawnAnchorCaptured)
	{
		SpawnAnchor = Loc;
		bSpawnAnchorCaptured = true;
	}

	// throttled (~2 Hz, not every frame) nearest-point scan for the HUD's
	// road-name/city label - a brute-force O(N) scan over a few thousand
	// points is trivial at this rate, no need for a spatial index
	if (const double Now = GetWorld()->GetTimeSeconds(); Now - LastLabelUpdateTime > 0.5)
	{
		LastLabelUpdateTime = Now;
		UpdateCurrentLabels(Loc);
	}

	if (HasRoute())
	{
		UpdateRouteProgress(FVector2D(Loc));
	}
	TickSnow();

	// 2026-08-26 diagnostic: log the tracked junction-pad component's live
	// state every ~2s, plus a trace fired right here (independent of
	// ODVehiclePawn's own ground-snap trace) straight down through its
	// bounds origin - to see whether it's torn down/replaced later, or
	// whether even a trace run from this actor still misses it despite
	// otherwise-valid state persisting the whole time.
	if (const double Now = GetWorld()->GetTimeSeconds(); Now - LastDiagTargetLogTime > 2.0)
	{
		LastDiagTargetLogTime = Now;
		if (UDynamicMeshComponent* Comp = DiagTargetComp.Get())
		{
			const FBoxSphereBounds B = Comp->Bounds;
			FHitResult Hit;
			const FVector Start = B.Origin + FVector(0, 0, 5000.0);
			const FVector End = B.Origin - FVector(0, 0, 5000.0);
			FCollisionQueryParams Params;
			Params.AddIgnoredActor(this);
			const bool bHit = GetWorld()->LineTraceSingleByChannel(Hit, Start, End, ECC_Visibility, Params);
			UE_LOG(LogTemp, Display,
				TEXT("ODRoadManager: DIAG live target t=%.1f valid=1 registered=%d visible=%d ")
				TEXT("collisionEnabled=%d bodyValid=%d renderStateCreated=%d bounds=(%.0f,%.0f,%.0f)+%.0f ")
				TEXT("selfTraceHit=%d selfTraceComp=%s"),
				Now, Comp->IsRegistered(), Comp->IsVisible(),
				(int32)Comp->GetCollisionEnabled(), Comp->BodyInstance.IsValidBodyInstance(),
				Comp->IsRenderStateCreated(),
				B.Origin.X, B.Origin.Y, B.Origin.Z, B.SphereRadius,
				bHit, bHit && Hit.Component.IsValid() ? *Hit.Component->GetName() : TEXT("none"));
		}
		else if (bDiagTargetEverSet)
		{
			UE_LOG(LogTemp, Display,
				TEXT("ODRoadManager: DIAG live target t=%.1f valid=0 (destroyed since last check)"), Now);
		}
	}

	const FIntPoint Center(FMath::FloorToInt(Loc.X / TileSizeCm),
	                       FMath::FloorToInt(Loc.Y / TileSizeCm));

	// world-space-correct reach, not a bare tile-count constant: a chunk's
	// centroid can bucket into a tile up to MaxChunkSpanCm/2 farther out
	// than where its geometry actually starts, so the load radius is padded
	// by that before converting to a tile count. Despawn adds a further
	// fixed world-space margin so tiles don't flicker in/out right at the
	// load boundary.
	const int32 LoadReach =
		FMath::CeilToInt((SpawnRadius + MaxChunkSpanCm * 0.5f) / TileSizeCm);
	const int32 DespawnReach = FMath::CeilToInt(
		(SpawnRadius + MaxChunkSpanCm * 0.5f + DespawnMarginCm) / TileSizeCm);

	// spawn missing tiles nearest-first, capped dispatches per tick (the
	// load itself is async, but this caps how many simultaneous file
	// opens/thread-pool jobs a big ring can trigger at once)
	int32 Dispatched = 0;
	for (int32 Ring = 0; Ring <= LoadReach && Dispatched < MaxNewLoadsPerTick; ++Ring)
	{
		for (int32 dy = -Ring; dy <= Ring && Dispatched < MaxNewLoadsPerTick; ++dy)
		{
			for (int32 dx = -Ring; dx <= Ring && Dispatched < MaxNewLoadsPerTick; ++dx)
			{
				if (FMath::Max(FMath::Abs(dx), FMath::Abs(dy)) != Ring)
				{
					continue;
				}
				const FIntPoint Key = Center + FIntPoint(dx, dy);
				if (Tiles.Contains(Key) || PendingTiles.Contains(Key) || MissingTiles.Contains(Key)
					|| !Vista.WantsTile(Key, TileSizeCm, MaxChunkSpanCm * 0.5))
				{
					continue;
				}
				RequestTileLoad(Key);
				++Dispatched;
			}
		}
	}

	// retire tiles beyond the despawn radius (hysteresis vs. the load radius)
	TArray<FIntPoint> Gone;
	for (const auto& Pair : Tiles)
	{
		if (FMath::Max(FMath::Abs(Pair.Key.X - Center.X),
		               FMath::Abs(Pair.Key.Y - Center.Y)) > DespawnReach)
		{
			Gone.Add(Pair.Key);
		}
	}
	for (const FIntPoint& Key : Gone)
	{
		ReleaseTile(Key);
	}

	if (const double Now = GetWorld()->GetTimeSeconds(); Now - LastDiagTime > 5.0)
	{
		LastDiagTime = Now;
		int32 NumChunks = 0;
		for (const auto& Pair : Tiles)
		{
			NumChunks += Pair.Value.Chunks.Num();
		}
		UE_LOG(LogTemp, Display,
			TEXT("ODRoadManager: tiles=%d pending=%d missing=%d chunks=%d"),
			Tiles.Num(), PendingTiles.Num(), MissingTiles.Num(), NumChunks);
	}

	// headless verification: `-odseam=N` runs the terrain-seam check N
	// seconds after boot, once (same delayed-trigger pattern as -odshot).
	// SeamAt/bSeamParsed/bSeamDone are instance members (see ODRoadManager.h)
	// rather than function-local `static` - a new AODRoadManager instance is
	// spawned fresh every PIE session, so this resets naturally per run
	// instead of persisting for the life of the editor process.
	if (!bSeamParsed)
	{
		bSeamParsed = true;
		FParse::Value(FCommandLine::Get(), TEXT("odseam="), SeamAt);
	}
	if (SeamAt > 0.0f && !bSeamDone && GetWorld()->GetTimeSeconds() > SeamAt)
	{
		bSeamDone = true;
		RunSeamCheck();
	}
}

void AODRoadManager::TickSnow()
{
	if (!SnowWeather.IsValid())
	{
		for (TActorIterator<AODWeatherController> It(GetWorld()); It; ++It) { SnowWeather = *It; break; }
	}
	const AODWeatherController* Wx = SnowWeather.Get();
	const double Now = GetWorld()->GetTimeSeconds();
	if (!Wx || Now - LastSnowTime < 0.5)
	{
		return;
	}
	const float Dt = LastSnowTime > 0.0 ? (float)(Now - LastSnowTime) : 0.0f;
	LastSnowTime = Now;
	const bool bReset = Wx->SnowDepthSerial != SnowSerialSeen;
	SnowSerialSeen = Wx->SnowDepthSerial;
	if (!bReset && Wx->RoadSnowRateCmS == 0.0f && Wx->SideSnowRateCmS == 0.0f)
	{
		return;
	}
	// ponytail: depth lives on the component, so a chunk that streams out and
	// back in restarts at the ambient depth - plowed state needs a per-chunk
	// map keyed by tile+index once plows exist (PLAN-REALISM.md sec 3e).
	for (auto& Pair : Tiles)
	{
		for (UDynamicMeshComponent* Comp : Pair.Value.Chunks)
		{
			if (!Comp)
			{
				continue;
			}
			const TArray<float>& D = Comp->GetCustomPrimitiveData().Data;
			const float Road = bReset ? Wx->RoadSnowCm : FMath::Clamp(
				(D.Num() > 0 ? D[0] : 0.0f) + Wx->RoadSnowRateCmS * Dt, 0.0f, Wx->RoadSnowCapCm);
			const float Side = bReset ? Wx->SideSnowCm : FMath::Clamp(
				(D.Num() > 1 ? D[1] : 0.0f) + Wx->SideSnowRateCmS * Dt, 0.0f, Wx->SideSnowCapCm);
			if (D.Num() < 2 || Road != D[0] || Side != D[1])
			{
				Comp->SetCustomPrimitiveDataVector2(0, FVector2D(Road, Side));
			}
		}
	}
}

float AODRoadManager::ChunkSnowCm(const UPrimitiveComponent* Comp, float Fallback)
{
	if (Comp && Comp->GetOwner() && Comp->GetOwner()->IsA<AODRoadManager>())
	{
		const TArray<float>& D = Comp->GetCustomPrimitiveData().Data;
		return D.Num() > 0 ? D[0] : 0.0f;
	}
	return Fallback;
}
