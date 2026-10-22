#include "ODTrafficManager.h"
#include "ODBakeLink.h"

#include "Algo/Sort.h"
#include "Algo/BinarySearch.h"
#include "Async/Async.h"
#include "Camera/PlayerCameraManager.h"
#include "Components/BoxComponent.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/Engine.h"
#include "Engine/World.h"
#include "Engine/StaticMesh.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/AutomationTest.h"
#include "Misc/CommandLine.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "ODBinSidecarUtil.h"
#include "ODDayNightController.h"
#include "ODEngineSynth.h"
#include "ODMenuVista.h"
#include "ODRaceManager.h"
#include "ODRoadManager.h"
#include "ODTileSource.h"
#include "ODVehicleDef.h"
#include "ODVisualStyle.h"

namespace ODTraffic
{
// ---- mirrored in pipeline/tests/test_traffic_runtime.py, which parses these lines ----
constexpr float TruckPce = 2.0f;               // build_traffic_demand.TRUCK_PCE
constexpr float CapacityVphpl[7] = {1900.0f, 1500.0f, 900.0f, 800.0f, 700.0f, 500.0f, 300.0f};   // CAPACITY_VPHPL
constexpr float KJamPerLaneKm = 130.0f;        // jam density, vehicles per km per lane
constexpr float IdmAccel = 1.4f;               // m/s^2
constexpr float IdmDecel = 2.0f;               // comfortable braking, m/s^2
constexpr float IdmHeadway = 1.4f;             // s
constexpr float IdmMinGap = 2.0f;              // m, standstill bumper gap
constexpr float CarLength = 4.5f;              // m
constexpr float MaxDecel = 9.0f;               // m/s^2
constexpr float GapAcceptS = 4.0f;             // s, stop / give-way
constexpr float SafeDecel = 4.0f;              // m/s^2, MOBIL safety criterion
constexpr float SignalGreenS = 26.0f;
constexpr float SignalYellowS = 4.0f;
constexpr float SignalAllRedS = 2.0f;
constexpr float StepS = 0.05f;                 // fixed 20 Hz step
// ----------------------------------------------------------------------------------------
constexpr float LaneWidthCm = 350.0f;
constexpr float GridCm = 20000.0f;
constexpr float ClusterCm = 2000.0f;           // export_road_centerline_bin.JUNCTION_CLUSTER_M
constexpr float ClusterDzCm = 500.0f;
constexpr float CarLenCm = CarLength * 100.0f;
constexpr float StopLineCm = 150.0f;           // stop this far before the junction node
constexpr float TrafficWheelMeshRadiusCm = 34.0f;   // ODVehiclePawn.cpp
constexpr int32 NumBoxes = 12;
constexpr int32 NumPassBy = 4;
constexpr float BoxRangeCm = 6000.0f;
constexpr float PassByRangeCm = 6000.0f;
// a player contact wrecks a car only past this closing speed along the contact
// normal; slower is a bump that stops it (2026-10-16 drive: 0-6 km/h touches wrecked)
constexpr float WreckClosingCmS = 25.0f / 0.036f;
constexpr float BumpHoldS = 3.0f;              // a bumped car stands this long
constexpr float ContactGapCm = 50.0f;          // hard floor on the bumper gap to anything ahead
constexpr float NeighbourCm = 1500.0f;         // any-link look-ahead (merges, junctions, lane entry)
constexpr float CarHalfWidthCm = 190.0f;       // centre-to-centre lateral clearance of two cars

/** Closing speed along the contact normal: a sideswipe at speed is a scrape, not a wreck. */
float ClosingCmS(const FVector& PlayerVel, const FVector& CarVel, const FVector& Normal)
{
	const FVector Rel = PlayerVel - CarVel;
	return Normal.IsNearlyZero() ? Rel.Size() : FMath::Abs(FVector::DotProduct(Rel, Normal.GetSafeNormal()));
}

/** P sits in the corridor ahead of a car at Pos / Rot; OutAlong = centre-to-centre distance. */
bool InCorridor(const FVector& Pos, const FQuat& Rot, const FVector& P, double Range, double HalfW, double& OutAlong)
{
	const FVector D = P - Pos;
	OutAlong = FVector::DotProduct(D, Rot.GetForwardVector());
	return FMath::Abs(D.Z) < 400.0 && OutAlong > 0.0 && OutAlong < Range
		&& FMath::Abs(FVector::DotProduct(D, Rot.GetRightVector())) < HalfW;
}

/** B (any link) blocks A: in A's corridor, not oncoming; when each is in the other's corridor
 *  (a merge, two noses at one point) only the higher id yields, so nobody deadlocks. */
bool Blocks(const FVector& PA, const FQuat& RA, uint32 IdA, const FVector& PB, const FQuat& RB, uint32 IdB, double& OutAlong)
{
	double Back;
	return InCorridor(PA, RA, PB, NeighbourCm, CarHalfWidthCm, OutAlong)
		&& FVector::DotProduct(RA.GetForwardVector(), RB.GetForwardVector()) > -0.7
		&& !(IdA < IdB && InCorridor(PB, RB, PA, NeighbourCm, CarHalfWidthCm, Back));
}

/** One fixed step of the IDM integration with a hard no-contact floor: IDM's braking is
 *  capped at MaxDecel, so a cut-in / merge / spawn inside its stopping distance used to
 *  drive straight into the car ahead. CarGap is the bumper gap at the step start. */
void Advance(float& S, float& V, float A, float Dt, float CarGap, float LeaderV)
{
	const float NewV = FMath::Max(0.0f, V + A * Dt);
	const float Adv = 0.5f * (V + NewV) * Dt;
	const float Room = FMath::Max(0.0f, CarGap - ContactGapCm);
	S += FMath::Min(Adv, Room);
	V = Adv > Room ? FMath::Min(NewV, FMath::Max(LeaderV, 0.0f)) : NewV;
}

/** Where a car's centre waits before a junction crossed by a road of Lanes (per direction):
 *  its nose 1.5 m short of that road's edge. */
float JunctionStopCm(int32 Lanes, bool bOneway)
{
	return FMath::Min(Lanes * LaneWidthCm * (bOneway ? 0.5f : 1.0f) + 150.0f + CarLenCm * 0.5f, 1500.0f);
}

/** Two car footprints (bounds half extents, centred on the pose) overlap in plan: separating axis test. */
bool Overlaps(const FVector& PA, const FQuat& RA, const FVector& EA, const FVector& PB, const FQuat& RB, const FVector& EB)
{
	if (FMath::Abs(PA.Z - PB.Z) > 300.0)
	{
		return false;   // bridge over road
	}
	const FVector2D D(PB - PA);
	const FVector2D Ax[4] = {FVector2D(RA.GetForwardVector()).GetSafeNormal(), FVector2D(RA.GetRightVector()).GetSafeNormal(),
		FVector2D(RB.GetForwardVector()).GetSafeNormal(), FVector2D(RB.GetRightVector()).GetSafeNormal()};
	for (const FVector2D& N : Ax)
	{
		const double RadA = EA.X * FMath::Abs(Ax[0] | N) + EA.Y * FMath::Abs(Ax[1] | N);
		const double RadB = EB.X * FMath::Abs(Ax[2] | N) + EB.Y * FMath::Abs(Ax[3] | N);
		if (FMath::Abs(D | N) > RadA + RadB)
		{
			return false;
		}
	}
	return true;
}

/** A physics wreck of a traffic car. The physics body is a box that reaches down to the
 *  tyres: the chassis mesh's own collision is its body bbox, which stops 8-24 cm under the
 *  axle while the tyres reach a wheel radius under it, so a wreck resting on that box sank
 *  its wheels into the road and the arches hid the rest (Taylor 2026-10-16: "wheels
 *  disappear"). Chassis and wheels ride the box with no collision (never welded or
 *  simulated on their own). OutParts[0] = chassis, then the four wheels. */
UBoxComponent* SpawnWreck(AActor* Owner, UStaticMesh* Chassis, UStaticMesh* Wheel, float FrontX, float RearX,
	float HalfTrack, float WheelR, const FVector& Pos, const FQuat& Rot, const FVector& Vel,
	TArray<UStaticMeshComponent*, TInlineAllocator<5>>& OutParts)
{
	const FBox B = Chassis->GetBounds().GetBox();
	const FVector Lo(FMath::Min(B.Min.X, RearX - WheelR), B.Min.Y, FMath::Min(B.Min.Z, -WheelR));
	const FVector Hi(FMath::Max(B.Max.X, FrontX + WheelR), B.Max.Y, B.Max.Z);
	const FVector Center = (Lo + Hi) * 0.5;
	UBoxComponent* Box = NewObject<UBoxComponent>(Owner);
	Box->SetupAttachment(Owner->GetRootComponent());
	Box->SetMobility(EComponentMobility::Movable);
	Box->SetBoxExtent((Hi - Lo) * 0.5, false);
	Box->SetWorldLocationAndRotation(Pos + Rot.RotateVector(Center), Rot);
	Box->SetCollisionProfileName(TEXT("PhysicsActor"));
	Box->RegisterComponent();
	auto Part = [&](UStaticMesh* Mesh, const FVector& Local, float Scale)
	{
		UStaticMeshComponent* C = NewObject<UStaticMeshComponent>(Owner);
		C->SetupAttachment(Box);
		C->SetMobility(EComponentMobility::Movable);
		C->SetStaticMesh(Mesh);
		C->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		C->SetRelativeLocation(Local - Center);
		C->SetRelativeScale3D(FVector(Scale));
		C->RegisterComponent();
		ODVisualStyle::PickSlots(C);
		OutParts.Add(C);
	};
	Part(Chassis, FVector::ZeroVector, 1.0f);
	for (int32 w = 0; Wheel && w < 4; ++w)
	{
		Part(Wheel, FVector(w < 2 ? FrontX : RearX, (w % 2 == 0 ? -1.0f : 1.0f) * HalfTrack, 0.0f), WheelR / TrafficWheelMeshRadiusCm);
	}
	Box->SetMassOverrideInKg(NAME_None, 1400.0f, true);
	Box->SetSimulatePhysics(true);
	Box->SetPhysicsLinearVelocity(Vel);
	return Box;
}

// D3: the five traffic bodies (AODVehiclePawn's own defs; road cars, no prototypes)
const TCHAR* const ModelDefs[] = {
	TEXT("/Game/OpenDrive/Cars/DA_Vehicle_Muscle.DA_Vehicle_Muscle"),
	TEXT("/Game/OpenDrive/Cars/DA_Vehicle_JDM.DA_Vehicle_JDM"),
	TEXT("/Game/OpenDrive/Cars/DA_Vehicle_Pickup.DA_Vehicle_Pickup"),
	TEXT("/Game/OpenDrive/Cars/DA_Vehicle_Rally.DA_Vehicle_Rally"),
	TEXT("/Game/OpenDrive/Cars/DA_Vehicle_Super.DA_Vehicle_Super"),
};

inline uint32 Hash(uint32 X)
{
	X ^= X >> 16; X *= 0x7feb352dU; X ^= X >> 15; X *= 0x846ca68bU; X ^= X >> 16;
	return X;
}
inline float Unit(uint32 X) { return (Hash(X) & 0xFFFFFF) / 16777216.0f; }

const FString TrafficSection = TEXT("OpenDrive.Traffic");
}

using namespace ODTraffic;

/** The merged lane graph + demand, built off the game thread, read-only after. */
struct FODTrafficGraph
{
	struct FLink
	{
		int32 PolyStart = 0, PolyNum = 0;   // Poly / Cum slice, travel order
		float Length = 0.0f;                // cm
		float SpeedCmS = 1100.0f;           // limit
		uint32 Aadt = 0;
		uint8 DirPct = 55, TruckPct = 5, Flags = 0;
		bool bEdgeForward = true;           // travel = the demand edge's a->b
		uint8 Cls = 4, Lanes = 1;
		bool bOneway = false;
		bool bYield = false;                // stop / give way at the end junction
		int32 StartCluster = -1, EndCluster = -1;
		uint8 EndControl = 0;               // 1 signal 2 stop 3 give way 4 priority
		uint8 SignalGroup = 0;
		int32 OutStart = 0, OutNum = 0;
		int32 Reverse = -1;
		bool IsEntry() const { return EndCluster >= 0 && StartCluster != EndCluster; }
	};
	TArray<FVector3f> Node;
	TArray<int32> Poly;
	TArray<float> Cum;
	TArray<FLink> Links;
	TArray<int32> Out;
	TArray<TArray<int32>> ClusterIn;     // entry links per junction cluster
	TArray<float> ClusterStop;           // cm from the end node a car's centre waits at
	TArray<FVector2f> ClusterCenter;
	TArray<float> ClusterOffset;         // signal cycle phase, s
	TMap<FIntPoint, TArray<int32>> Grid; // GridCm cells -> links
	float HourShare[96] = {};
	float DowFactor[7] = {};
	FString Name;
};

namespace ODTraffic
{
/** Mirror of build_traffic_demand.demand_vph (passenger-car equivalents/h). */
float DemandVph(const FODTrafficGraph& G, const FODTrafficGraph::FLink& L, int32 Hour, int32 Dow)
{
	const bool bUrban = (L.Flags & 1) != 0;
	const bool bWeekend = Dow >= 5;
	const float Share = G.HourShare[((bWeekend ? 2 : 0) + (bUrban ? 0 : 1)) * 24 + Hour % 24];
	const bool bInbound = ((L.Flags & 4) != 0) == L.bEdgeForward;
	const bool bPeak = (Hour < 12) == bInbound;
	const float D = L.DirPct / 100.0f;
	const float Pce = 1.0f + L.TruckPct / 100.0f * (TruckPce - 1.0f);
	return L.Aadt * Pce * G.DowFactor[Dow] * Share * (bPeak ? D : 1.0f - D);
}

/** Mirror of build_traffic_demand.capacity_vph. */
float CapacityVph(uint8 Cls, uint8 Lanes)
{
	return CapacityVphpl[FMath::Min(Cls & 7, 6)] * FMath::Max<int32>(Lanes, 1);
}

/** Triangular fundamental diagram -> (vehicles per km over all lanes, km/h).
 *  Mirrored by test_traffic_runtime.target_density. */
void TargetDensity(float Q, float C, float Vf, int32 Lanes, float Pce, float& OutK, float& OutV)
{
	if (Q <= C)
	{
		OutK = Q / Vf;
		OutV = Vf;
	}
	else
	{
		const float KJam = Lanes * KJamPerLaneKm;
		const float W = C / FMath::Max(KJam - C / Vf, 1.0f);   // wave speed keeping the diagram continuous
		const float Qc = C * C / Q;                            // overload cuts the throughput
		OutK = KJam - Qc / W;
		OutV = Qc / OutK;
	}
	OutK /= Pce;
}

TSharedPtr<FODTrafficGraph, ESPMode::ThreadSafe> LoadGraph(const FString& GraphPath, const FString& DemandPath, FString& OutErr)
{
	TArray<uint8> B, D;
	if (!FFileHelper::LoadFileToArray(B, *GraphPath) || B.Num() < 12 || FMemory::Memcmp(B.GetData(), "ODG1", 4) != 0)
	{
		OutErr = TEXT("no routegraph");
		return nullptr;
	}
	const int32 N = (int32)ReadU32LE(B.GetData() + 4);
	const int64 EOff = 8 + (int64)N * 8;
	const int32 E = B.Num() >= EOff + 4 ? (int32)ReadU32LE(B.GetData() + EOff) : -1;
	if (N <= 0 || E <= 0 || B.Num() != EOff + 4 + (int64)E * 17 + (int64)N * 5)
	{
		OutErr = TEXT("routegraph has no lane block (re-run export_road_centerline_bin.py)");
		return nullptr;
	}
	if (!FFileHelper::LoadFileToArray(D, *DemandPath) || D.Num() != 8 + 96 * 4 + 7 * 4 + E * 7
		|| FMemory::Memcmp(D.GetData(), "ODT1", 4) != 0 || ReadU32LE(D.GetData() + 4) != E)
	{
		OutErr = TEXT("traffic demand missing or stale (re-run build_traffic_demand.py)");
		return nullptr;
	}
	TSharedPtr<FODTrafficGraph, ESPMode::ThreadSafe> GP = MakeShared<FODTrafficGraph, ESPMode::ThreadSafe>();
	FODTrafficGraph& G = *GP;
	const uint8* EP = B.GetData() + EOff + 4;
	const int8* Dir = reinterpret_cast<const int8*>(EP + E * 12);
	const uint8* ZP = EP + E * 13;
	const uint8* Ctl = ZP + N * 4;
	const uint8* ClsP = Ctl + N;
	const uint8* LanesP = ClsP + E;
	const uint8* KmhP = LanesP + E;
	const uint8* YldP = KmhP + E;
	for (int32 i = 0; i < 96; ++i)
	{
		G.HourShare[i] = ReadF32LE(D.GetData() + 8 + i * 4);
	}
	for (int32 i = 0; i < 7; ++i)
	{
		G.DowFactor[i] = ReadF32LE(D.GetData() + 8 + 384 + i * 4);
	}
	const uint8* AadtP = D.GetData() + 8 + 412;
	const uint8* DirPctP = AadtP + E * 4;
	const uint8* TruckP = DirPctP + E;
	const uint8* FlagP = TruckP + E;

	G.Node.SetNumUninitialized(N);
	for (int32 i = 0; i < N; ++i)
	{
		G.Node[i] = FVector3f(ReadF32LE(B.GetData() + 8 + i * 8), ReadF32LE(B.GetData() + 12 + i * 8), ReadF32LE(ZP + i * 4));
	}
	TArray<int32> EA, EB;
	EA.SetNumUninitialized(E);
	EB.SetNumUninitialized(E);
	TArray<int32> AdjStart;
	AdjStart.SetNumZeroed(N + 1);
	for (int32 e = 0; e < E; ++e)
	{
		EA[e] = ReadU32LE(EP + e * 12);
		EB[e] = ReadU32LE(EP + e * 12 + 4);
		if (EA[e] >= N || EB[e] >= N)
		{
			OutErr = TEXT("bad edge index");
			return nullptr;
		}
		++AdjStart[EA[e] + 1];
		++AdjStart[EB[e] + 1];
	}
	for (int32 i = 0; i < N; ++i)
	{
		AdjStart[i + 1] += AdjStart[i];
	}
	TArray<int32> AdjEdge;
	AdjEdge.SetNumUninitialized(E * 2);
	{
		TArray<int32> Fill(AdjStart.GetData(), N);
		for (int32 e = 0; e < E; ++e)
		{
			AdjEdge[Fill[EA[e]]++] = e;
			AdjEdge[Fill[EB[e]]++] = e;
		}
	}
	auto Other = [&](int32 e, int32 n) { return EA[e] == n ? EB[e] : EA[e]; };
	auto Legal = [&](int32 e, int32 From) { return EA[e] == From ? Dir[e] >= 0 : Dir[e] <= 0; };

	// a link ends at a junction, a dead end, or where lanes / speed / class / one-way change
	TBitArray<> Break(false, N);
	for (int32 n = 0; n < N; ++n)
	{
		if (AdjStart[n + 1] - AdjStart[n] != 2)
		{
			Break[n] = true;
			continue;
		}
		const int32 E0 = AdjEdge[AdjStart[n]], E1 = AdjEdge[AdjStart[n] + 1];
		const int32 X = Other(E0, n), Y = Other(E1, n);
		Break[n] = E0 == E1 || LanesP[E0] != LanesP[E1] || KmhP[E0] != KmhP[E1] || ClsP[E0] != ClsP[E1]
			|| Legal(E0, X) != Legal(E1, n) || Legal(E0, n) != Legal(E1, Y);
	}

	TBitArray<> Used(false, E);
	TArray<int32> LinkStartNode;
	auto Emit = [&](const TArray<int32>& PN, const TArray<int32>& PE)
	{
		const int32 M = PE.Num();
		TArray<float> C;
		C.SetNumUninitialized(M + 1);
		C[0] = 0.0f;
		for (int32 i = 0; i < M; ++i)
		{
			C[i + 1] = C[i] + FMath::Max(FVector3f::Dist(G.Node[PN[i]], G.Node[PN[i + 1]]), 1.0f);
		}
		const int32 Rep = PE[M / 2];
		const bool bRepAB = EA[Rep] == PN[M / 2];
		const bool bCanF = Legal(PE[0], PN[0]), bCanB = Legal(PE[0], PN[1]);
		int32 First = INDEX_NONE;
		for (int32 Pass = 0; Pass < 2; ++Pass)
		{
			const bool bF = Pass == 0;
			if (bF ? !bCanF : !bCanB)
			{
				continue;
			}
			FODTrafficGraph::FLink L;
			L.PolyStart = G.Poly.Num();
			L.PolyNum = M + 1;
			for (int32 i = 0; i <= M; ++i)
			{
				G.Poly.Add(bF ? PN[i] : PN[M - i]);
				G.Cum.Add(bF ? C[i] : C[M] - C[M - i]);
			}
			L.Length = C[M];
			L.Lanes = FMath::Max<uint8>(LanesP[Rep], 1);
			L.SpeedCmS = FMath::Max<uint8>(KmhP[Rep], 10) / 3.6f * 100.0f;
			L.Cls = ClsP[Rep];
			L.bOneway = !(bCanF && bCanB);
			L.bEdgeForward = bF == bRepAB;
			L.Aadt = ReadU32LE(AadtP + Rep * 4);
			L.DirPct = DirPctP[Rep];
			L.TruckPct = TruckP[Rep];
			L.Flags = FlagP[Rep];
			for (int32 i = 0; i < M; ++i)
			{
				const bool bAB = (EA[PE[i]] == PN[i]) == bF;   // travel along this edge's a->b
				L.bYield |= (YldP[PE[i]] & (bAB ? 1 : 2)) != 0;
			}
			const int32 Id = G.Links.Add(L);
			LinkStartNode.Add(bF ? PN[0] : PN[M]);
			if (First == INDEX_NONE)
			{
				First = Id;
			}
			else
			{
				G.Links[First].Reverse = Id;
				G.Links[Id].Reverse = First;
			}
		}
	};
	auto Walk = [&](int32 U, int32 E0)
	{
		TArray<int32> PN = {U}, PE;
		int32 Cur = U, Ed = E0;
		for (;;)
		{
			Used[Ed] = true;
			PE.Add(Ed);
			const int32 V = Other(Ed, Cur);
			PN.Add(V);
			if (Break[V] || V == U)
			{
				break;
			}
			const int32 S = AdjStart[V];
			const int32 NextE = AdjEdge[S] == Ed ? AdjEdge[S + 1] : AdjEdge[S];
			if (Used[NextE])
			{
				break;
			}
			Cur = V;
			Ed = NextE;
		}
		Emit(PN, PE);
	};
	for (int32 n = 0; n < N; ++n)
	{
		if (!Break[n])
		{
			continue;
		}
		for (int32 k = AdjStart[n]; k < AdjStart[n + 1]; ++k)
		{
			const int32 e = AdjEdge[k];
			if (!Used[e] && EA[e] != EB[e])
			{
				Walk(n, e);
			}
		}
	}
	for (int32 e = 0; e < E; ++e)   // loops with no break node
	{
		if (!Used[e] && EA[e] != EB[e])
		{
			Walk(EA[e], e);
		}
	}

	// successors: links leaving the end node, no U-turn unless it's a dead end
	TMultiMap<int32, int32> ByStart;
	for (int32 l = 0; l < G.Links.Num(); ++l)
	{
		ByStart.Add(LinkStartNode[l], l);
	}
	TArray<int32> Cand;
	for (int32 l = 0; l < G.Links.Num(); ++l)
	{
		FODTrafficGraph::FLink& L = G.Links[l];
		const int32 End = G.Poly[L.PolyStart + L.PolyNum - 1];
		Cand.Reset();
		ByStart.MultiFind(End, Cand);
		L.OutStart = G.Out.Num();
		for (int32 c : Cand)
		{
			if (c != L.Reverse)
			{
				G.Out.Add(c);
			}
		}
		if (G.Out.Num() == L.OutStart && L.Reverse >= 0 && Cand.Contains(L.Reverse))
		{
			G.Out.Add(L.Reverse);
		}
		L.OutNum = G.Out.Num() - L.OutStart;
	}

	// junction clusters: controlled nodes within ClusterCm and level (the pipeline's rule)
	TArray<int32> Parent;
	TArray<int32> JN;
	TMap<FIntPoint, TArray<int32>> Cells;
	for (int32 n = 0; n < N; ++n)
	{
		if (Ctl[n])
		{
			JN.Add(n);
		}
	}
	Parent.SetNumUninitialized(JN.Num());
	for (int32 i = 0; i < JN.Num(); ++i)
	{
		Parent[i] = i;
		const FVector3f& P = G.Node[JN[i]];
		Cells.FindOrAdd(FIntPoint(FMath::FloorToInt(P.X / ClusterCm), FMath::FloorToInt(P.Y / ClusterCm))).Add(i);
	}
	auto Find = [&](int32 i) { while (Parent[i] != i) { Parent[i] = Parent[Parent[i]]; i = Parent[i]; } return i; };
	for (int32 i = 0; i < JN.Num(); ++i)
	{
		const FVector3f& P = G.Node[JN[i]];
		const FIntPoint C(FMath::FloorToInt(P.X / ClusterCm), FMath::FloorToInt(P.Y / ClusterCm));
		for (int32 dy = -1; dy <= 1; ++dy)
		{
			for (int32 dx = -1; dx <= 1; ++dx)
			{
				if (const TArray<int32>* Cell = Cells.Find(C + FIntPoint(dx, dy)))
				{
					for (int32 j : *Cell)
					{
						const FVector3f& Q = G.Node[JN[j]];
						if (j > i && FVector2f::DistSquared(FVector2f(P.X, P.Y), FVector2f(Q.X, Q.Y)) <= ClusterCm * ClusterCm
							&& FMath::Abs(P.Z - Q.Z) <= ClusterDzCm)
						{
							Parent[Find(i)] = Find(j);
						}
					}
				}
			}
		}
	}
	TMap<int32, int32> NodeCluster;
	TMap<int32, int32> RootId;
	TArray<int32> ClusterCount;
	for (int32 i = 0; i < JN.Num(); ++i)
	{
		const int32 R = Find(i);
		int32* Id = RootId.Find(R);
		if (!Id)
		{
			Id = &RootId.Add(R, G.ClusterCenter.Num());
			G.ClusterCenter.Add(FVector2f::ZeroVector);
			ClusterCount.Add(0);
		}
		NodeCluster.Add(JN[i], *Id);
		G.ClusterCenter[*Id] += FVector2f(G.Node[JN[i]].X, G.Node[JN[i]].Y);
		++ClusterCount[*Id];
	}
	for (int32 c = 0; c < G.ClusterCenter.Num(); ++c)
	{
		G.ClusterCenter[c] /= (float)ClusterCount[c];
	}
	G.ClusterIn.SetNum(G.ClusterCenter.Num());
	G.ClusterOffset.SetNum(G.ClusterCenter.Num());
	const float Cycle = 2.0f * (SignalGreenS + SignalYellowS + SignalAllRedS);
	for (int32 c = 0; c < G.ClusterOffset.Num(); ++c)
	{
		G.ClusterOffset[c] = Unit(c * 7919 + 13) * Cycle;
	}
	TArray<float> AxisYaw;
	AxisYaw.Init(1000.0f, G.ClusterCenter.Num());
	for (int32 l = 0; l < G.Links.Num(); ++l)
	{
		FODTrafficGraph::FLink& L = G.Links[l];
		const int32 S = G.Poly[L.PolyStart], En = G.Poly[L.PolyStart + L.PolyNum - 1];
		if (const int32* C = NodeCluster.Find(S)) { L.StartCluster = *C; }
		if (const int32* C = NodeCluster.Find(En)) { L.EndCluster = *C; }
		L.EndControl = Ctl[En];
		if (!L.IsEntry())
		{
			continue;
		}
		G.ClusterIn[L.EndCluster].Add(l);
		// signal phase group by approach axis: the first approach and its opposite go together
		const FVector3f& P0 = G.Node[G.Poly[L.PolyStart + L.PolyNum - 2]];
		const FVector3f& P1 = G.Node[En];
		const float Yaw = FMath::RadiansToDegrees(FMath::Atan2(P1.Y - P0.Y, P1.X - P0.X));
		float& Axis = AxisYaw[L.EndCluster];
		if (Axis > 999.0f)
		{
			Axis = Yaw;
		}
		const float Diff = FMath::Abs(FMath::Fmod(Yaw - Axis + 720.0f, 180.0f));
		L.SignalGroup = (Diff > 45.0f && Diff < 135.0f) ? 1 : 0;
	}
	// wait clear of the widest road crossing the junction: a 1.5 m stop line left the
	// waiting car's nose past the junction centre, in the crossing traffic's lane
	G.ClusterStop.Init(StopLineCm, G.ClusterIn.Num());
	for (int32 c = 0; c < G.ClusterIn.Num(); ++c)
	{
		for (int32 In : G.ClusterIn[c])
		{
			const FODTrafficGraph::FLink& L = G.Links[In];
			G.ClusterStop[c] = FMath::Max(G.ClusterStop[c], JunctionStopCm(L.Lanes, L.bOneway));
		}
	}

	for (int32 l = 0; l < G.Links.Num(); ++l)
	{
		const FODTrafficGraph::FLink& L = G.Links[l];
		for (int32 i = 0; i < L.PolyNum; ++i)
		{
			const FVector3f& P = G.Node[G.Poly[L.PolyStart + i]];
			TArray<int32>& Cell = G.Grid.FindOrAdd(FIntPoint(FMath::FloorToInt(P.X / GridCm), FMath::FloorToInt(P.Y / GridCm)));
			if (Cell.Num() == 0 || Cell.Last() != l)
			{
				Cell.Add(l);
			}
		}
	}
	return GP;
}

void SyncIsm(UInstancedStaticMeshComponent* Ism, const TArray<FTransform>& Xf, const TArray<FTransform>& PrevXf)
{
	if (!Ism)
	{
		return;
	}
	const int32 Have = Ism->GetInstanceCount();
	if (Have > Xf.Num())
	{
		TArray<int32> Rm;
		for (int32 i = Have - 1; i >= Xf.Num(); --i)
		{
			Rm.Add(i);
		}
		Ism->RemoveInstances(Rm, true);
	}
	if (Have > 0 && Xf.Num() > 0)
	{
		// each car's own last-frame transform: instance slots reshuffle as cars
		// come and go or swap to the brake-lamp ISM, and teleporting every frame
		// left moving traffic with no velocity, so TSR smeared it (flicker)
		const int32 N = FMath::Min(Have, Xf.Num());
		// no MarkRenderStateDirty: the instance data manager pushes just the
		// changed transforms; a dirty flag rebuilt every traffic proxy each
		// frame (400+ cars in town ran ~15 fps, 2026-10-16)
		Ism->BatchUpdateInstancesTransforms(0, TArray<FTransform>(Xf.GetData(), N), TArray<FTransform>(PrevXf.GetData(), N), true, false, false);
	}
	if (Have < Xf.Num())
	{
		Ism->AddInstances(TArray<FTransform>(Xf.GetData() + Have, Xf.Num() - Have), false, true, false);
	}
}
}

AODTrafficManager::AODTrafficManager()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

bool AODTrafficManager::IsEnabledSetting()
{
	bool bOn = true;
	GConfig->GetBool(*TrafficSection, TEXT("Enabled"), bOn, GGameUserSettingsIni);
	return bOn;
}

void AODTrafficManager::SetEnabledSetting(bool bOn)
{
	GConfig->SetBool(*TrafficSection, TEXT("Enabled"), bOn, GGameUserSettingsIni);
	GConfig->Flush(false, GGameUserSettingsIni);
}

void AODTrafficManager::BeginPlay()
{
	Super::BeginPlay();
	int32 Flag = 1;
	if (FParse::Value(FCommandLine::Get(), TEXT("odtraffic="), Flag) && Flag == 0)
	{
		bCmdLineOff = true;
		UE_LOG(LogTemp, Display, TEXT("ODTrafficManager: -odtraffic=0 - traffic off"));
	}
	BuildModels();

	for (int32 i = 0; i < NumBoxes; ++i)
	{
		UBoxComponent* Box = NewObject<UBoxComponent>(this);
		Box->SetupAttachment(RootComponent);
		Box->SetMobility(EComponentMobility::Movable);
		Box->SetCollisionProfileName(TEXT("BlockAll"));
		// kinematic boxes teleport along with the cars: blocking wrecks drove them
		// through the wreck and flung it into the next car and the furniture
		Box->SetCollisionResponseToChannel(ECC_PhysicsBody, ECR_Ignore);
		Box->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		Box->SetCanEverAffectNavigation(false);
		Box->RegisterComponent();
		Boxes.Add(Box);
		BoxAgent.Add(-1);
	}
	for (int32 i = 0; i < NumPassBy + 1; ++i)
	{
		UODEngineSynth* S = NewObject<UODEngineSynth>(this);
		S->SetupAttachment(RootComponent);
		if (i < NumPassBy)
		{
			S->bOverrideAttenuation = true;
			S->AttenuationOverrides.bAttenuate = true;
			S->AttenuationOverrides.bSpatialize = true;
			S->AttenuationOverrides.AttenuationShapeExtents = FVector(500.0f, 0.0f, 0.0f);
			S->AttenuationOverrides.FalloffDistance = PassByRangeCm;
			S->ConfigureEngine(4, 2.0f);
			S->Loudness = 0.35f;
		}
		else
		{
			S->bAllowSpatialization = false;   // the jam bed sits around the listener
			S->ConfigureEngine(12, 6.0f);
			S->TimingJitter = 1.0f;
			S->MufflerCutoff = 600.0f;
			S->Loudness = 0.4f;
		}
		S->SetVolumeScale(0.0f);
		S->RegisterComponent();
		S->Start();
		if (i < NumPassBy)
		{
			PassBy.Add(S);
		}
		else
		{
			IdleBed = S;
		}
	}
}

void AODTrafficManager::EndPlay(const EEndPlayReason::Type Reason)
{
	if (UPrimitiveComponent* Body = HookedPlayerBody.Get())
	{
		Body->OnComponentHit.RemoveDynamic(this, &AODTrafficManager::OnPlayerHit);
	}
	Super::EndPlay(Reason);
}

void AODTrafficManager::BuildModels()
{
	TMap<TPair<UStaticMesh*, int32>, int32> WheelIdx;   // (mesh, tier) -> WheelIsms
	for (const TCHAR* Path : ModelDefs)
	{
		const UODVehicleDef* Def = LoadObject<UODVehicleDef>(nullptr, Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
		UStaticMesh* FarChassis = Def ? Def->ChassisMesh.LoadSynchronous() : nullptr;
		UStaticMesh* Chassis = ODVisualStyle::Pick(FarChassis);
		if (!Chassis)
		{
			UE_LOG(LogTemp, Warning, TEXT("ODTrafficManager: no car def/mesh at %s - skipped"), Path);
			continue;
		}
		FModel M;
		M.Chassis = Chassis;
		M.FrontX = Def->FrontAxleXCm;
		M.RearX = Def->RearAxleXCm;
		M.HalfTrack = Def->HalfTrackYCm;
		M.WheelR = Def->WheelRadiusCm;
		const FBoxSphereBounds Bounds = Chassis->GetBounds();
		M.BoundsOrigin = Bounds.Origin;
		M.HalfExtent = Bounds.BoxExtent;
		auto MakeIsm = [&](UStaticMesh* Mesh, float CullCm, bool bShadow, bool bFar)
		{
			UInstancedStaticMeshComponent* Ism = NewObject<UInstancedStaticMeshComponent>(this);
			Ism->SetupAttachment(RootComponent);
			Ism->SetMobility(EComponentMobility::Movable);
			Ism->SetStaticMesh(Mesh);
			Ism->SetCollisionEnabled(ECollisionEnabled::NoCollision);
			Ism->SetCanEverAffectNavigation(false);
			Ism->SetCastShadow(bShadow && !bFar);
			Ism->SetCullDistances(0, (int32)CullCm);
			Ism->SetHasPerInstancePrevTransforms(true);
			// moving instances re-voxelise into the global distance field every frame
			Ism->bAffectDistanceFieldLighting = false;
			Ism->bVisibleInRayTracing = !bFar;
			Ism->RegisterComponent();
			if (!bFar)
			{
				ODVisualStyle::PickSlots(Ism);
			}
			return Ism;
		};
		for (int32 t = 0; t < 2; ++t)
		{
			UStaticMesh* Mesh = t == 0 ? Chassis : FarChassis;
			M.Body[t] = MakeIsm(Mesh, DespawnRadiusCm, true, t == 1);
			M.Braking[t] = MakeIsm(Mesh, DespawnRadiusCm, true, t == 1);
			for (UInstancedStaticMeshComponent* Ism : {M.Body[t], M.Braking[t]})
			{
				const int32 Slot = Ism->GetMaterialIndex(TEXT("lights"));
				(Ism == M.Body[t] ? M.LampMID[t] : M.BrakeMID[t]) = Slot != INDEX_NONE ? Ism->CreateDynamicMaterialInstance(Slot) : nullptr;
			}
			UStaticMesh* FarWheel = Def->WheelMesh.LoadSynchronous();
			if (UStaticMesh* Wheel = t == 0 ? ODVisualStyle::Pick(FarWheel) : FarWheel)
			{
				if (const int32* W = WheelIdx.Find({Wheel, t}))
				{
					M.Wheel[t] = *W;
				}
				else
				{
					M.Wheel[t] = WheelIsms.Add(MakeIsm(Wheel, t == 0 ? 25000.0f : 40000.0f, false, t == 1));
					WheelIdx.Add({Wheel, t}, M.Wheel[t]);
				}
			}
		}
		Models.Add(M);
	}
	WheelXf.SetNum(WheelIsms.Num());
	UE_LOG(LogTemp, Display, TEXT("ODTrafficManager: %d traffic car model(s), %d wheel mesh(es)"), Models.Num(), WheelIsms.Num());
}

void AODTrafficManager::StartLoad(AODRoadManager* Road)
{
	bLoading = true;
	GraphRoad = Road;
	const FString Dir = FPaths::ProjectContentDir() / TEXT("OpenDrive/Masks");
	const FString GraphPath = Dir / (Road->CenterlineFilePrefix.Replace(TEXT("centerline_"), TEXT("routegraph_")) + TEXT(".bin"));
	const FString DemandPath = Dir / (Road->CenterlineFilePrefix.Replace(TEXT("centerline_"), TEXT("traffic_")) + TEXT(".bin"));
	const FString Name = Road->GetAreaKey();
	TWeakObjectPtr<AODTrafficManager> WeakThis(this);
	Async(EAsyncExecution::ThreadPool, [WeakThis, GraphPath, DemandPath, Name]()
	{
		const double T0 = FPlatformTime::Seconds();
		FString Err;
		TSharedPtr<FODTrafficGraph, ESPMode::ThreadSafe> G = LoadGraph(ODTileSource::Fetch(GraphPath), ODTileSource::Fetch(DemandPath), Err);
		if (G)
		{
			G->Name = Name;
		}
		const double Ms = (FPlatformTime::Seconds() - T0) * 1000.0;
		AsyncTask(ENamedThreads::GameThread, [WeakThis, G, Err, Name, Ms]()
		{
			AODTrafficManager* Self = WeakThis.Get();
			if (!Self)
			{
				return;
			}
			Self->bLoading = false;
			Self->ClearAgents();
			Self->Graph = G;
			if (!G)
			{
				UE_LOG(LogTemp, Warning, TEXT("ODTrafficManager: '%s' has no traffic: %s"), *Name, *Err);
				return;
			}
			const int32 L = G->Links.Num();
			Self->SliceStart.Init(0, L);
			Self->SliceNum.Init(0, L);
			Self->SliceStamp.Init(-1, L);
			Self->TargetK.Init(0.0f, L);
			Self->TargetV.Init(0.0f, L);
			Self->TargetStamp.Init(-1, L);
			Self->ClusterBusyUntil.Init(0.0, G->ClusterCenter.Num());
			Self->RangeCenter = FVector(1.0e12);
			UE_LOG(LogTemp, Display, TEXT("ODTrafficManager: '%s' %d nodes -> %d directed links, %d junctions (%.0f ms)"),
				*Name, G->Node.Num(), L, G->ClusterCenter.Num(), Ms);
		});
	});
}

void AODTrafficManager::ClearAgents()
{
	Agents.Reset();
	RangeLinks.Reset();
	Order.Reset();
	OrderPos.Reset();
	RangeCenter = FVector(1.0e12);
	for (const FModel& M : Models)
	{
		for (int32 t = 0; t < 2; ++t)
		{
			M.Body[t]->ClearInstances();
			M.Braking[t]->ClearInstances();
		}
	}
	for (UInstancedStaticMeshComponent* W : WheelIsms)
	{
		W->ClearInstances();
	}
	for (int32 b = 0; b < Boxes.Num(); ++b)
	{
		Boxes[b]->SetCollisionEnabled(ECollisionEnabled::NoCollision);
		BoxAgent[b] = -1;
	}
	for (UODEngineSynth* S : PassBy)
	{
		S->SetVolumeScale(0.0f);
	}
	if (IdleBed)
	{
		IdleBed->SetVolumeScale(0.0f);
	}
}

bool AODTrafficManager::TrafficWanted()
{
	if (bCmdLineOff || !IsEnabledSetting())
	{
		return false;
	}
	for (TActorIterator<AODRaceManager> It(GetWorld()); It; ++It)
	{
		if (It->Checkpoints.Num() > 0)
		{
			return false;   // D3: a stage is a race
		}
	}
	return true;
}

void AODTrafficManager::ClockHourDow(int32& OutHour, int32& OutDow) const
{
	OutHour = 12;
	OutDow = 0;
	if (const AODDayNightController* DN = DayNight.Get())
	{
		OutHour = FMath::Clamp(FMath::FloorToInt(DN->TimeOfDay), 0, 23);
		OutDow = DN->GetDayOfWeek();
	}
}

float AODTrafficManager::TargetPerCm(int32 Link, float* OutSpeedCmS) const
{
	if (TargetStamp[Link] != HourBucket)
	{
		const FODTrafficGraph::FLink& L = Graph->Links[Link];
		const int32 Hour = HourBucket % 24, Dow = HourBucket / 24;
		const float Pce = 1.0f + L.TruckPct / 100.0f * (TruckPce - 1.0f);
		float K, V;
		TargetDensity(DemandVph(*Graph, L, Hour, Dow), CapacityVph(L.Cls, L.Lanes), L.SpeedCmS * 0.036f, L.Lanes, Pce, K, V);
		TargetK[Link] = K / 100000.0f;
		TargetV[Link] = FMath::Max(V / 0.036f, 100.0f);
		TargetStamp[Link] = HourBucket;
	}
	if (OutSpeedCmS)
	{
		*OutSpeedCmS = TargetV[Link];
	}
	return TargetK[Link];
}

FVector AODTrafficManager::PosAt(int32 Link, float S, int32 Next, int32 Prev) const
{
	const FODTrafficGraph& G = *Graph;
	if (S > G.Links[Link].Length && Next >= 0)
	{
		S -= G.Links[Link].Length;
		Link = Next;
	}
	else if (S < 0.0f && Prev >= 0)
	{
		S += G.Links[Prev].Length;
		Link = Prev;
	}
	const FODTrafficGraph::FLink& L = G.Links[Link];
	S = FMath::Clamp(S, 0.0f, L.Length);
	const float* C = G.Cum.GetData() + L.PolyStart;
	const int32 K = FMath::Clamp((int32)Algo::UpperBound(TArrayView<const float>(C, L.PolyNum), S) - 1, 0, L.PolyNum - 2);
	const float T = (S - C[K]) / FMath::Max(C[K + 1] - C[K], 1.0f);
	const FVector3f P = FMath::Lerp(G.Node[G.Poly[L.PolyStart + K]], G.Node[G.Poly[L.PolyStart + K + 1]], T);
	return FVector(P);
}

void AODTrafficManager::Pose(const FAgent& Ag, FVector& OutPos, FQuat& OutRot) const
{
	const FVector P = PosAt(Ag.Link, Ag.S, Ag.Next);
	// heading over +-3 m spanning the junction both ways, so a turn eases in
	// instead of snapping the car (and its lane offset) round at the node
	FVector F = PosAt(Ag.Link, Ag.S + 300.0f, Ag.Next) - PosAt(Ag.Link, Ag.S - 300.0f, Ag.Next, Ag.Prev);
	if (F.SizeSquared() < 1.0)
	{
		F = FVector(1, 0, 0);
	}
	const double Yaw = FMath::Atan2(F.Y, F.X);
	const double Pitch = FMath::Atan2(F.Z, FVector2D(F.X, F.Y).Size());
	const FVector Right(-FMath::Sin(Yaw), FMath::Cos(Yaw), 0.0);
	OutPos = P + Right * Ag.Lat + FVector(0, 0, Models.IsValidIndex(Ag.Model) ? Models[Ag.Model].WheelR + 2.0f : 36.0f);
	OutRot = FRotator(FMath::RadiansToDegrees(Pitch), FMath::RadiansToDegrees(Yaw), 0.0).Quaternion();
}

float AODTrafficManager::LaneLat(int32 Link, uint8 Lane) const
{
	const FODTrafficGraph::FLink& L = Graph->Links[Link];
	return L.bOneway ? (Lane + 0.5f - L.Lanes * 0.5f) * LaneWidthCm : (Lane + 0.5f) * LaneWidthCm;
}

float AODTrafficManager::Idm(float V, float V0, float Gap, float LeaderV) const
{
	// cm units throughout
	const float A = IdmAccel * 100.0f, Bc = IdmDecel * 100.0f;
	const float SStar = IdmMinGap * 100.0f + FMath::Max(0.0f, V * IdmHeadway + V * (V - LeaderV) / (2.0f * FMath::Sqrt(A * Bc)));
	const float R = V / FMath::Max(V0, 100.0f);
	const float Acc = A * (1.0f - R * R * R * R - FMath::Square(SStar / FMath::Max(Gap, 10.0f)));
	return FMath::Max(Acc, -MaxDecel * 100.0f);
}

bool AODTrafficManager::FindLeader(int32 Self, uint8 Lane, float& OutGap, float& OutV) const
{
	const FAgent& Ag = Agents[Self];
	OutGap = 1.0e9f;
	OutV = 0.0f;
	if (SliceStamp[Ag.Link] == StepId)
	{
		const int32 End = SliceStart[Ag.Link] + SliceNum[Ag.Link];
		for (int32 o = OrderPos[Self] + 1; o < End; ++o)
		{
			const FAgent& B = Agents[Order[o]];
			if (B.Link == Ag.Link && B.Lane == Lane && B.S >= Ag.S)
			{
				OutGap = B.S - Ag.S - CarLenCm;
				OutV = B.V;
				return true;
			}
		}
	}
	if (Ag.Next >= 0 && SliceStamp[Ag.Next] == StepId)
	{
		const uint8 NL = FMath::Min<uint8>(Lane, Graph->Links[Ag.Next].Lanes - 1);
		const float Dist = Graph->Links[Ag.Link].Length - Ag.S;
		const int32 End = SliceStart[Ag.Next] + SliceNum[Ag.Next];
		for (int32 o = SliceStart[Ag.Next]; o < End; ++o)
		{
			const FAgent& B = Agents[Order[o]];
			if (B.Link == Ag.Next && B.Lane == NL)
			{
				OutGap = Dist + B.S - CarLenCm;
				OutV = B.V;
				return true;
			}
		}
	}
	return false;
}

int32 AODTrafficManager::SignalState(int32 Link) const
{
	const FODTrafficGraph::FLink& L = Graph->Links[Link];
	const float Half = SignalGreenS + SignalYellowS + SignalAllRedS;
	const float T = FMath::Fmod((float)FMath::Fmod(SimTime, 2.0 * Half) + Graph->ClusterOffset[L.EndCluster] + L.SignalGroup * Half, 2.0f * Half);
	return T < SignalGreenS ? 0 : T < SignalGreenS + SignalYellowS ? 1 : 2;
}

bool AODTrafficManager::JunctionClear(int32 Cluster, int32 SelfLink) const
{
	if (SimTime < ClusterBusyUntil[Cluster])
	{
		return false;
	}
	for (int32 In : Graph->ClusterIn[Cluster])
	{
		const FODTrafficGraph::FLink& L = Graph->Links[In];
		if (In == SelfLink || L.bYield || SliceStamp[In] != StepId || SliceNum[In] == 0)
		{
			continue;
		}
		const FAgent& B = Agents[Order[SliceStart[In] + SliceNum[In] - 1]];   // nearest the junction
		const float Dist = L.Length - B.S;
		// only a car that is arriving: one stood near the node (often stopped by the
		// yielder's own nose on a skewed junction) blocked the side road for good;
		// once the yielder goes, the any-link corridor test keeps the two apart
		if (B.Link == In && Dist < 8000.0f && B.V > 100.0f && Dist / B.V < GapAcceptS)
		{
			return false;
		}
	}
	// the player counts as priority traffic
	const FVector2D C(Graph->ClusterCenter[Cluster]);
	const FVector2D ToC = C - FVector2D(PlayerPos);
	const double D = ToC.Size();
	if (D < 1500.0)
	{
		return false;
	}
	if (D < 8000.0)
	{
		const double Closing = FVector2D::DotProduct(FVector2D(PlayerVel), ToC / D);
		if (Closing > 100.0 && D / Closing < GapAcceptS)
		{
			return false;
		}
	}
	return true;
}

int32 AODTrafficManager::PickNext(FAgent& Ag)
{
	const FODTrafficGraph::FLink& L = Graph->Links[Ag.Link];
	// heading into the junction, held across its internal links (both ends in
	// one cluster) so a median crossover + turn still reads as a U-turn
	const bool bInternal = L.EndCluster >= 0 && L.StartCluster == L.EndCluster;
	if (!bInternal || Ag.Approach.IsNearlyZero())
	{
		Ag.Approach = FVector2f(FVector2D(PosAt(Ag.Link, L.Length, -1) - PosAt(Ag.Link, L.Length - 1500.0f, -1))).GetSafeNormal();
	}
	if (L.OutNum == 0)
	{
		return -1;
	}
	if (L.OutNum == 1)
	{
		return Graph->Out[L.OutStart];
	}
	// split by demand: each branch weighted by its target flow; U-turns and
	// another lap of a loop (cul-de-sac turning circles) only when nothing else
	// leaves (2026-10-16, Taylor: cars "switching roads and spinning in circles
	// at junctions")
	float Total = 0.0f;
	float W[16];
	const int32 N = FMath::Min(L.OutNum, 16);
	for (int32 i = 0; i < N; ++i)
	{
		const int32 C = Graph->Out[L.OutStart + i];
		float V;
		const float K = TargetPerCm(C, &V);
		W[i] = K * V + 0.002f * Graph->Links[C].Lanes;
		const FVector2f Dir = FVector2f(FVector2D(PosAt(C, FMath::Min(1500.0f, Graph->Links[C].Length), -1) - PosAt(C, 0.0f, -1))).GetSafeNormal();
		if (C == Ag.Link || (Dir | Ag.Approach) < -0.5f)
		{
			W[i] = 0.0f;
		}
		Total += W[i];
	}
	if (Total <= 0.0f)
	{
		return Graph->Out[L.OutStart + Ag.Seed % N];
	}
	Ag.Seed = Hash(Ag.Seed + 0x9e3779b9U);
	float R = Unit(Ag.Seed) * Total;
	for (int32 i = 0; i < N; ++i)
	{
		R -= W[i];
		if (R <= 0.0f && W[i] > 0.0f)
		{
			return Graph->Out[L.OutStart + i];
		}
	}
	return Graph->Out[L.OutStart + N - 1];
}

bool AODTrafficManager::SpawnAgent(int32 Link, float S, uint8 Lane, float V, uint32 Seed)
{
	if (Agents.Num() >= MaxAgents || Models.Num() == 0)
	{
		return false;
	}
	FAgent& Ag = Agents.AddDefaulted_GetRef();
	Ag.Id = ++SpawnCounter;
	Ag.Seed = Hash(Seed ^ (Ag.Id * 2654435761U));
	Ag.Link = Link;
	Ag.S = S;
	Ag.Lane = FMath::Min<uint8>(Lane, Graph->Links[Link].Lanes - 1);
	Ag.V0Factor = 0.9f + 0.2f * Unit(Ag.Seed + 1);
	Ag.V = V * Ag.V0Factor;
	Ag.Model = (uint8)(Hash(Ag.Seed + 2) % (uint32)Models.Num());
	Ag.Lat = LaneLat(Link, Ag.Lane);
	Ag.Next = PickNext(Ag);
	Pose(Ag, Ag.CurPos, Ag.CurRot);
	Ag.PrevPos = Ag.CurPos;
	Ag.PrevRot = Ag.CurRot;
	++WindowSpawns;
	MinSpawnCm = FMath::Min(MinSpawnCm, FVector::Dist2D(Ag.CurPos, PlayerPos));
	return true;
}

// A drawn area's roads are clipped at its outline (2026-10-21, area picker):
// a one-way dead end there sits in the boundary curtain, so its car may go
// even while seen. Two-way dead ends already U-turn via their Reverse link.
static bool AtOutline(const FVector& P)
{
	const TSharedPtr<const TArray<FVector2D>, ESPMode::ThreadSafe> Outline = ODBakeLink::ActiveOutline();
	return Outline.IsValid() && ODBakeLink::SignedDistanceCm(*Outline, FVector2D(P)) < 5000.0;
}

bool AODTrafficManager::Noticed(const FVector& P) const
{
	// 2D: TeleportToSpawn parks the pawn 6 km up until its ground snap, and the
	// old 3D 15 m test let a refill drop cars right under it (Deals Gap 17:30)
	const FVector2D D(P - PlayerPos);
	const double Dist = D.Size();
	return Dist < ClearCm || (Dist < PopInCm && FVector2D::DotProduct(D, CamFwd) > 0.25 * Dist);
}

void AODTrafficManager::UpdateRange(bool bFill)
{
	RangeCenter = PlayerPos;
	RangeLinks.Reset();
	const FODTrafficGraph& G = *Graph;
	const double R2 = (double)SpawnRadiusCm * SpawnRadiusCm;
	const int32 Reach = FMath::CeilToInt(SpawnRadiusCm / GridCm);
	const FIntPoint C(FMath::FloorToInt(PlayerPos.X / GridCm), FMath::FloorToInt(PlayerPos.Y / GridCm));
	TSet<int32> Seen;
	for (int32 dy = -Reach; dy <= Reach; ++dy)
	{
		for (int32 dx = -Reach; dx <= Reach; ++dx)
		{
			const TArray<int32>* Cell = G.Grid.Find(C + FIntPoint(dx, dy));
			if (!Cell)
			{
				continue;
			}
			for (int32 l : *Cell)
			{
				bool bDup = false;
				Seen.Add(l, &bDup);
				if (bDup)
				{
					continue;
				}
				const FODTrafficGraph::FLink& L = G.Links[l];
				FRangeLink RL{l, -1.0f, 0.0f, 0.0f};
				bool bPrevIn = false;
				for (int32 i = 0; i < L.PolyNum; ++i)
				{
					const FVector3f& P = G.Node[G.Poly[L.PolyStart + i]];
					const bool bIn = FVector2D::DistSquared(FVector2D(P.X, P.Y), FVector2D(PlayerPos)) <= R2;
					const float S = G.Cum[L.PolyStart + i];
					if (bIn)
					{
						if (RL.EntryS < 0.0f)
						{
							RL.EntryS = S;
						}
						RL.ExitS = S;
						if (bPrevIn)
						{
							RL.InRangeCm += S - G.Cum[L.PolyStart + i - 1];
						}
					}
					bPrevIn = bIn;
				}
				if (RL.InRangeCm > 100.0f)
				{
					RangeLinks.Add(RL);
				}
			}
		}
	}
	if (!bFill)
	{
		return;
	}
	// start in the middle of whatever the clock says: every lane at its target spacing
	int32 Made = 0;
	for (const FRangeLink& RL : RangeLinks)
	{
		float V;
		const float K = TargetPerCm(RL.Link, &V);
		const FODTrafficGraph::FLink& L = G.Links[RL.Link];
		if (K <= 0.0f)
		{
			continue;
		}
		const float Spacing = FMath::Max(L.Lanes / K, CarLenCm + IdmMinGap * 100.0f);
		for (uint8 Lane = 0; Lane < L.Lanes; ++Lane)
		{
			uint32 Seed = Hash(RL.Link * 977 + HourBucket * 31 + Lane);
			float S = RL.EntryS + Unit(Seed) * Spacing;
			while (S < RL.ExitS)
			{
				const FVector P = PosAt(RL.Link, S, -1);
				if (FVector2D::DistSquared(FVector2D(P), FVector2D(PlayerPos)) < (double)SpawnRadiusCm * SpawnRadiusCm && !Noticed(P))
				{
					Made += SpawnAgent(RL.Link, S, Lane, V, Seed) ? 1 : 0;
				}
				Seed = Hash(Seed);
				S += Spacing * (0.75f + 0.5f * Unit(Seed));
			}
		}
	}
	UE_LOG(LogTemp, Display, TEXT("ODTrafficManager: filled %d link(s) in range with %d car(s) (hour %d, day %d)"),
		RangeLinks.Num(), Made, HourBucket % 24, HourBucket / 24);
}

void AODTrafficManager::RebuildOrder()
{
	++StepId;
	Order.SetNumUninitialized(Agents.Num());
	for (int32 i = 0; i < Agents.Num(); ++i)
	{
		Order[i] = i;
	}
	Algo::Sort(Order, [this](int32 X, int32 Y)
	{
		const FAgent& A = Agents[X];
		const FAgent& B = Agents[Y];
		return A.Link != B.Link ? A.Link < B.Link : A.S < B.S;
	});
	OrderPos.SetNumUninitialized(Agents.Num());
	for (int32 o = 0; o < Order.Num(); ++o)
	{
		const int32 i = Order[o];
		OrderPos[i] = o;
		const int32 L = Agents[i].Link;
		if (SliceStamp[L] != StepId)
		{
			SliceStamp[L] = StepId;
			SliceStart[L] = o;
			SliceNum[L] = 0;
		}
		++SliceNum[L];
	}
}

void AODTrafficManager::TopUp()
{
	const FODTrafficGraph& G = *Graph;
	int32 Budget = 40;
	TArray<int32> Remove;
	// one quota for the whole range: the old per-link roll (Have + rand > Target)
	// kept a car on every short link whatever its share (Target 0.1), so SF's
	// 517 block links filled ~6x over demand and locked its junctions solid
	float TotalTarget = 0.0f;
	int32 TotalHave = 0;
	TArray<TPair<float, int32>> Short;   // (deficit, range link)
	for (int32 r = 0; r < RangeLinks.Num(); ++r)
	{
		const FRangeLink& RL = RangeLinks[r];
		const float Target = TargetPerCm(RL.Link) * RL.InRangeCm;
		const int32 Have = SliceStamp[RL.Link] == StepId ? SliceNum[RL.Link] : 0;
		TotalTarget += Target;
		TotalHave += Have;
		if (Have > Target * 1.5f + 2.0f)
		{
			// the clock moved off a peak: thin the link out where nobody sees it
			for (int32 o = SliceStart[RL.Link] + Have - 1; o >= SliceStart[RL.Link]; --o)
			{
				if (FVector::DistSquared2D(Agents[Order[o]].CurPos, PlayerPos) > (double)PopInCm * PopInCm)
				{
					Remove.Add(Order[o]);
					break;
				}
			}
		}
		else if (Have < Target)
		{
			Short.Emplace(Target - Have, r);
		}
	}
	Short.Sort([](const TPair<float, int32>& A, const TPair<float, int32>& B) { return A.Key > B.Key; });
	for (const TPair<float, int32>& Sh : Short)
	{
		if (Budget <= 0 || TotalHave + 1 > TotalTarget + 0.5f)
		{
			break;
		}
		const FRangeLink& RL = RangeLinks[Sh.Value];
		const FODTrafficGraph::FLink& L = G.Links[RL.Link];
		const int32 Have = SliceStamp[RL.Link] == StepId ? SliceNum[RL.Link] : 0;
		float V;
		TargetPerCm(RL.Link, &V);
		++TopUpSalt;
		// alternate the two ends of the in-range stretch, so the road ahead of
		// the player fills as well as the road behind
		const float S = (TopUpSalt & 1) ? RL.EntryS : FMath::Max(RL.EntryS, RL.ExitS - 2000.0f);
		const FVector P = PosAt(RL.Link, S, -1);
		if (FVector::DistSquared2D(P, PlayerPos) < (double)PopInCm * PopInCm
			|| Agents.ContainsByPredicate([&P](const FAgent& B) { return FVector::DistSquared2D(B.CurPos, P) < 1000.0 * 1000.0; }))
		{
			continue;   // a link start is a junction exit: a car crossing it is on another link
		}
		const float Need = CarLenCm + IdmMinGap * 100.0f + V * IdmHeadway * 0.5f;
		int32 BestLane = -1;
		float BestGap = Need;
		for (uint8 Lane = 0; Lane < L.Lanes; ++Lane)
		{
			float Ahead = 1.0e9f, Behind = 1.0e9f;
			if (Have > 0)
			{
				for (int32 o = SliceStart[RL.Link]; o < SliceStart[RL.Link] + Have; ++o)
				{
					const FAgent& B = Agents[Order[o]];
					if (B.Lane == Lane && B.Link == RL.Link)
					{
						(B.S >= S ? Ahead : Behind) = FMath::Min(B.S >= S ? Ahead : Behind, FMath::Abs(B.S - S));
					}
				}
			}
			const float Gap = FMath::Min(Ahead, Behind);
			if (Gap > BestGap)
			{
				BestGap = Gap;
				BestLane = Lane;
			}
		}
		if (BestLane >= 0 && SpawnAgent(RL.Link, S, (uint8)BestLane, V, TopUpSalt))
		{
			--Budget;
			++TotalHave;
		}
	}
	Remove.Sort([](int32 A, int32 B) { return A > B; });
	for (int32 i : Remove)
	{
		Agents.RemoveAtSwap(i);
	}
	if (Remove.Num())
	{
		RebuildOrder();
	}
}

void AODTrafficManager::Step(float Dt)
{
	SimTime += Dt;
	const FODTrafficGraph& G = *Graph;
	RebuildOrder();
	TArray<int32> Remove;
	const double Despawn2 = (double)DespawnRadiusCm * DespawnRadiusCm;
	// any-link neighbours: the lane graph only sees this link and the next, so a
	// merge, a junction crossing or a short link let cars drive into each other
	TMap<FIntPoint, TArray<int32, TInlineAllocator<8>>> Cells;
	auto CellOf = [](const FVector& P) { return FIntPoint(FMath::FloorToInt(P.X / NeighbourCm), FMath::FloorToInt(P.Y / NeighbourCm)); };
	for (int32 i = 0; i < Agents.Num(); ++i)
	{
		Cells.FindOrAdd(CellOf(Agents[i].CurPos)).Add(i);
	}
	TArray<FVector, TInlineAllocator<16>> WreckPos;
	for (const FWreck& Wr : Wrecks)
	{
		if (Wr.Comp)
		{
			WreckPos.Add(Wr.Comp->GetComponentLocation());
		}
	}
	for (int32 i = 0; i < Agents.Num(); ++i)
	{
		FAgent& Ag = Agents[i];
		const FODTrafficGraph::FLink& L = G.Links[Ag.Link];
		if (Ag.Next < 0)
		{
			Ag.Next = PickNext(Ag);
		}
		float VFd;
		TargetPerCm(Ag.Link, &VFd);
		const float V0 = Ag.V0Factor * FMath::Min(L.SpeedCmS, VFd);

		float Gap, LV;
		FindLeader(i, Ag.Lane, Gap, LV);

		// the player, wrecks and any-link neighbours as leaders: in this lane's corridor, ahead
		const FVector F = Ag.CurRot.GetForwardVector();
		uint8 Why = 0;   // what binds: 0 lane leader / free, 1 any-link car, 2 player / wreck / bump, 3 stop line
		auto Lead = [&](double Along, const FVector& Vel, uint8 W)
		{
			if (Along - CarLenCm < Gap)
			{
				Gap = Along - CarLenCm;
				LV = FMath::Max(0.0, FVector::DotProduct(Vel, F));
				Why = W;
			}
		};
		double Along;
		if (InCorridor(Ag.CurPos, Ag.CurRot, PlayerPos, 15000.0, 220.0, Along))
		{
			// a parked player: after a short wait, steer round it (the corridor test
			// lets go once the player is 2.2 m to the side) instead of queuing forever
			// (Summit, a capture with the car parked on the road). ponytail: no check
			// for oncoming traffic in the lane it swings into.
			if (Ag.Dodge == 0.0f && Ag.StillS > 2.0f && Along < 2000.0 && PlayerVel.SizeSquared2D() < 100.0 * 100.0)
			{
				const float Side = FVector::DotProduct(PlayerPos - Ag.CurPos, Ag.CurRot.GetRightVector());
				Ag.Dodge = Side >= 0.0f ? Side - 260.0f : Side + 260.0f;
			}
			Lead(Along, PlayerVel, 2);
		}
		if (Ag.Dodge != 0.0f && FVector::DistSquared2D(Ag.CurPos, PlayerPos) > 3000.0 * 3000.0)
		{
			Ag.Dodge = 0.0f;   // past it: back into the lane
		}
		for (const FVector& P : WreckPos)
		{
			if (InCorridor(Ag.CurPos, Ag.CurRot, P, 15000.0, 220.0, Along))
			{
				Lead(Along, FVector::ZeroVector, 2);
			}
		}
		const FIntPoint C0 = CellOf(Ag.CurPos);
		for (int32 dy = -1; dy <= 1; ++dy)
		{
			for (int32 dx = -1; dx <= 1; ++dx)
			{
				if (const auto* Cell = Cells.Find(C0 + FIntPoint(dx, dy)))
				{
					for (int32 j : *Cell)
					{
						const FAgent& B = Agents[j];
						// same link: FindLeader and the lane rules own it (and an adjacent
						// lane on a bend would read as in the way)
						if (B.Link != Ag.Link && SimTime >= Ag.GhostUntil && Blocks(Ag.CurPos, Ag.CurRot, Ag.Id, B.CurPos, B.CurRot, B.Id, Along))
						{
							Lead(Along, B.CurRot.GetForwardVector() * B.V, 1);
						}
					}
				}
			}
		}
		if (SimTime < Ag.HoldUntil)
		{
			Lead(CarLenCm, FVector::ZeroVector, 2);   // bumped: stand
		}
		const float CarGap = Gap, CarV = LV;   // stop lines below are targets, not contacts
		if (Why == 1 && Ag.StillS > 10.0f)
		{
			// a ring of cars each waiting on the next across a junction never clears
			// (SF gridlock): the one stood longest drives through for a moment.
			// ponytail: brief visible overlap; a junction reservation would avoid it.
			Ag.GhostUntil = SimTime + 3.0;
		}

		const float DistEnd = L.Length - Ag.S;
		auto StopAt = [&](float Dist)
		{
			if (Dist < Gap)
			{
				Gap = Dist;
				LV = 0.0f;
				Why = 3;
			}
		};
		const FODTrafficGraph::FLink* NL = Ag.Next >= 0 ? &G.Links[Ag.Next] : nullptr;
		if (!NL)
		{
			StopAt(DistEnd - StopLineCm);
			// dead end with no way round: it stops short of the end, so the S >= Length
			// removal below never fired and a queue stood behind it for good
			if (DistEnd < StopLineCm + 500.0f && Ag.V < 50.0f && (!Noticed(Ag.CurPos) || AtOutline(Ag.CurPos)))
			{
				Remove.AddUnique(i);
			}
		}
		// a lane that ends: MOBIL safety test for the lane inside, else wait at its end
		if (NL && Ag.Lane >= NL->Lanes && DistEnd < 30000.0f)
		{
			const uint8 TL = Ag.Lane - 1;
			float LeadGap = 1.0e9f, FolGap = 1.0e9f, FolV = 0.0f, FolV0 = L.SpeedCmS;
			const int32 End = SliceStart[Ag.Link] + SliceNum[Ag.Link];
			for (int32 o = SliceStart[Ag.Link]; o < End; ++o)
			{
				const FAgent& B = Agents[Order[o]];
				if (B.Link != Ag.Link || B.Lane != TL || &B == &Ag)
				{
					continue;
				}
				if (B.S >= Ag.S)
				{
					LeadGap = FMath::Min(LeadGap, B.S - Ag.S - CarLenCm);
				}
				else if (Ag.S - B.S - CarLenCm < FolGap)
				{
					FolGap = Ag.S - B.S - CarLenCm;
					FolV = B.V;
					FolV0 = B.V0Factor * L.SpeedCmS;
				}
			}
			if (LeadGap > IdmMinGap * 100.0f && FolGap > IdmMinGap * 100.0f
				&& Idm(FolV, FolV0, FolGap, Ag.V) > -SafeDecel * 100.0f)
			{
				Ag.Lane = TL;
			}
			else
			{
				StopAt(DistEnd - StopLineCm - 200.0f);
			}
		}
		// junction control at the link end (D5: strict)
		if (L.IsEntry() && !Ag.bCleared && NL)
		{
			const float StopCm = G.ClusterStop[L.EndCluster];
			const bool bStopSign = L.EndControl == 2 && L.bYield;
			const bool bGiveWay = (L.EndControl == 3 || L.EndControl == 4) && L.bYield;
			if (L.EndControl == 1)
			{
				const int32 St = SignalState(Ag.Link);
				const float StopDist = DistEnd - StopCm;
				// yellow: stop only if it can at a comfortable 3 m/s^2
				const bool bStop = St == 2 || (St == 1 && Ag.V * Ag.V / 600.0f < StopDist);
				if (bStop && StopDist > -50.0f)
				{
					StopAt(StopDist);
				}
				else if (StopDist < Ag.V * Dt * 2.0f + 50.0f)
				{
					Ag.bCleared = true;   // past the line: committed
				}
			}
			else if (bStopSign)
			{
				if (DistEnd < StopCm + 400.0f && Ag.V < 50.0f)
				{
					Ag.StopTime += Dt;
				}
				if (Ag.StopTime > 1.0f && JunctionClear(L.EndCluster, Ag.Link))
				{
					Ag.bCleared = true;
					ClusterBusyUntil[L.EndCluster] = SimTime + 3.0;
				}
				else
				{
					StopAt(DistEnd - StopCm);
				}
			}
			else if (bGiveWay)
			{
				if (DistEnd < 3000.0f && JunctionClear(L.EndCluster, Ag.Link))
				{
					Ag.bCleared = true;
					ClusterBusyUntil[L.EndCluster] = SimTime + 3.0;
				}
				else
				{
					StopAt(DistEnd - StopCm);
				}
			}
			else
			{
				Ag.bCleared = true;   // priority approach / uncontrolled
			}
		}

		Ag.A = Idm(Ag.V, V0, Gap, LV);
		Advance(Ag.S, Ag.V, Ag.A, Dt, CarGap, CarV);
		SlowWhy[Why] += Ag.V < 300.0f ? 1 : 0;
		Ag.StillS = Ag.V < 50.0f ? Ag.StillS + Dt : 0.0f;

		while (Ag.S >= G.Links[Ag.Link].Length)
		{
			if (Ag.Next < 0)
			{
				Ag.S = G.Links[Ag.Link].Length;
				Remove.AddUnique(i);
				break;
			}
			const FODTrafficGraph::FLink& Old = G.Links[Ag.Link];
			const bool bJunction = Old.EndCluster >= 0;
			Ag.S -= Old.Length;
			Ag.Prev = Ag.Link;
			Ag.Link = Ag.Next;
			const FODTrafficGraph::FLink& New = G.Links[Ag.Link];
			if (bJunction || New.Lanes != Old.Lanes)
			{
				// take the emptiest lane at the link start
				float Best = -1.0f;
				uint8 Pick = FMath::Min<uint8>(Ag.Lane, New.Lanes - 1);
				for (uint8 Lane = 0; Lane < New.Lanes; ++Lane)
				{
					float First = New.Length;
					if (SliceStamp[Ag.Link] == StepId)
					{
						for (int32 o = SliceStart[Ag.Link]; o < SliceStart[Ag.Link] + SliceNum[Ag.Link]; ++o)
						{
							const FAgent& B = Agents[Order[o]];
							if (B.Link == Ag.Link && B.Lane == Lane)
							{
								First = B.S;
								break;
							}
						}
					}
					if (First > Best + 100.0f)
					{
						Best = First;
						Pick = Lane;
					}
				}
				Ag.Lane = Pick;
			}
			Ag.Lane = FMath::Min<uint8>(Ag.Lane, New.Lanes - 1);
			Ag.Next = -1;
			Ag.Next = PickNext(Ag);
			Ag.bCleared = false;
			Ag.StopTime = 0.0f;
		}
		// ease toward the lane centre (lane changes and lane-count changes)
		const float LatTarget = LaneLat(Ag.Link, Ag.Lane) + Ag.Dodge;
		Ag.Lat += FMath::Clamp(LatTarget - Ag.Lat, -150.0f * Dt, 150.0f * Dt);
		Ag.PrevPos = Ag.CurPos;
		Ag.PrevRot = Ag.CurRot;
		Pose(Ag, Ag.CurPos, Ag.CurRot);
		if (FVector2D::DistSquared(FVector2D(Ag.CurPos), FVector2D(PlayerPos)) > Despawn2)
		{
			Remove.AddUnique(i);
		}
	}
	// AI-AI contact census (diag line): footprints that overlap after this step
	++DiagSteps;
	for (int32 i = 0; i < Agents.Num(); ++i)
	{
		const FAgent& A = Agents[i];
		const FIntPoint C0 = CellOf(A.CurPos);
		for (int32 dy = -1; dy <= 1; ++dy)
		{
			for (int32 dx = -1; dx <= 1; ++dx)
			{
				const auto* Cell = Cells.Find(C0 + FIntPoint(dx, dy));
				for (int32 j : Cell ? TArrayView<const int32>(*Cell) : TArrayView<const int32>())
				{
					const FAgent& B = Agents[j];
					if (A.Id < B.Id && Overlaps(A.CurPos, A.CurRot, Models[A.Model].HalfExtent, B.CurPos, B.CurRot, Models[B.Model].HalfExtent))
					{
						++OverlapSteps;
						bool bSeen = false;
						OverlapPairs.Add(((uint64)A.Id << 32) | B.Id, &bSeen);
						const FODTrafficGraph::FLink& LA = G.Links[A.Link];
						const bool bJunction = A.Link != B.Link || (LA.EndCluster >= 0 && LA.Length - A.S < 3000.0f);
						OverlapJunction += !bSeen && bJunction ? 1 : 0;
					}
				}
			}
		}
	}
	Remove.Sort([](int32 A, int32 B) { return A > B; });
	for (int32 i : Remove)
	{
		Agents.RemoveAtSwap(i);
	}
}

void AODTrafficManager::Render(float Alpha)
{
	// body / brake transforms per (model, tier): slot Model * 2 + tier
	const int32 NumSlots = Models.Num() * 2;
	for (TArray<FTransform>& X : BodyXf) { X.Reset(); }
	for (TArray<FTransform>& X : BrakeXf) { X.Reset(); }
	for (TArray<FTransform>& X : WheelXf) { X.Reset(); }
	for (TArray<FTransform>& X : BodyPrevXf) { X.Reset(); }
	for (TArray<FTransform>& X : BrakePrevXf) { X.Reset(); }
	for (TArray<FTransform>& X : WheelPrevXf) { X.Reset(); }
	BodyXf.SetNum(NumSlots);
	BrakeXf.SetNum(NumSlots);
	BodyPrevXf.SetNum(NumSlots);
	BrakePrevXf.SetNum(NumSlots);
	WheelPrevXf.SetNum(WheelIsms.Num());
	const double NearSq = FMath::Square((double)NearTierCm);
	for (FAgent& Ag : Agents)
	{
		const FModel& M = Models[Ag.Model];
		const FTransform Xf(FQuat::Slerp(Ag.PrevRot, Ag.CurRot, Alpha), FMath::Lerp(Ag.PrevPos, Ag.CurPos, (double)Alpha));
		const FTransform Was = Ag.bShown ? Ag.Shown : Xf;
		Ag.Shown = Xf;
		Ag.bShown = true;
		const int32 Tier = FVector::DistSquared(Xf.GetLocation(), PlayerPos) > NearSq ? 1 : 0;
		const int32 Slot = Ag.Model * 2 + Tier;
		// brake lamps on under braking and while stood in a queue
		const bool bBrake = Ag.A < -150.0f || Ag.V < 50.0f;
		(bBrake ? BrakeXf : BodyXf)[Slot].Add(Xf);
		(bBrake ? BrakePrevXf : BodyPrevXf)[Slot].Add(Was);
		if (const int32 W = M.Wheel[Tier]; W >= 0)
		{
			const FVector Sc(M.WheelR / TrafficWheelMeshRadiusCm);
			for (int32 w = 0; w < 4; ++w)
			{
				const FVector Local(w < 2 ? M.FrontX : M.RearX, (w % 2 == 0 ? -1.0f : 1.0f) * M.HalfTrack, 0.0f);
				WheelXf[W].Add(FTransform(Xf.GetRotation(), Xf.TransformPosition(Local), Sc));
				WheelPrevXf[W].Add(FTransform(Was.GetRotation(), Was.TransformPosition(Local), Sc));
			}
		}
	}
	for (int32 m = 0; m < Models.Num(); ++m)
	{
		for (int32 t = 0; t < 2; ++t)
		{
			SyncIsm(Models[m].Body[t], BodyXf[m * 2 + t], BodyPrevXf[m * 2 + t]);
			SyncIsm(Models[m].Braking[t], BrakeXf[m * 2 + t], BrakePrevXf[m * 2 + t]);
		}
	}
	for (int32 w = 0; w < WheelIsms.Num(); ++w)
	{
		SyncIsm(WheelIsms[w], WheelXf[w], WheelPrevXf[w]);
	}
}

void AODTrafficManager::UpdateLights()
{
	const AODDayNightController* DN = DayNight.Get();
	const bool bNight = DN && DN->bHeadlightsOn;
	if (bNight == bNightLights && bLightsSet)
	{
		return;
	}
	bNightLights = bNight;
	bLightsSet = true;
	const float HeadGlow = DN ? DN->HeadlampGlow : 300.0f;
	const float TailGlow = DN ? DN->TaillampGlow : 25.0f;
	const float BrakeGlow = bNight ? TailGlow * 5.0f : (DN ? DN->BrakelampGlow : 400.0f);   // AODDayNightController's rule
	for (const FModel& M : Models)
	{
		for (int32 t = 0; t < 2; ++t)
		{
			for (UMaterialInstanceDynamic* MID : {M.LampMID[t], M.BrakeMID[t]})
			{
				if (MID)
				{
					MID->SetScalarParameterValue(TEXT("HeadGlow"), bNight ? HeadGlow : 0.0f);
					MID->SetScalarParameterValue(TEXT("TailGlow"), MID == M.BrakeMID[t] ? BrakeGlow : (bNight ? TailGlow : 0.0f));
					MID->SetScalarParameterValue(TEXT("ReverseGlow"), 0.0f);
				}
			}
		}
	}
}

void AODTrafficManager::UpdateCollision(float Alpha)
{
	// kinematic boxes on the nearest cars; a hit turns that car into a physics wreck
	TArray<TPair<double, int32>> Near;
	for (int32 i = 0; i < Agents.Num(); ++i)
	{
		const double D2 = FVector::DistSquared(Agents[i].CurPos, PlayerPos);
		if (D2 < BoxRangeCm * BoxRangeCm)
		{
			Near.Emplace(D2, i);
		}
	}
	Near.Sort([](const TPair<double, int32>& A, const TPair<double, int32>& B) { return A.Key < B.Key; });
	for (int32 b = 0; b < Boxes.Num(); ++b)
	{
		UBoxComponent* Box = Boxes[b];
		if (b < Near.Num())
		{
			const FAgent& Ag = Agents[Near[b].Value];
			const FModel& M = Models[Ag.Model];
			const FQuat Q = FQuat::Slerp(Ag.PrevRot, Ag.CurRot, Alpha);
			const FVector P = FMath::Lerp(Ag.PrevPos, Ag.CurPos, (double)Alpha) + Q.RotateVector(M.BoundsOrigin);
			Box->SetBoxExtent(M.HalfExtent, false);
			Box->SetWorldLocationAndRotation(P, Q, false, nullptr, ETeleportType::TeleportPhysics);
			if (BoxAgent[b] < 0)
			{
				Box->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
			}
			BoxAgent[b] = (int32)Ag.Id;
		}
		else if (BoxAgent[b] >= 0)
		{
			Box->SetCollisionEnabled(ECollisionEnabled::NoCollision);
			BoxAgent[b] = -1;
		}
	}
}

void AODTrafficManager::OnPlayerHit(UPrimitiveComponent* HitComp, AActor* OtherActor, UPrimitiveComponent* OtherComp,
	FVector NormalImpulse, const FHitResult& Hit)
{
	if (OtherActor != this)
	{
		return;
	}
	const int32 b = Boxes.IndexOfByKey(Cast<UBoxComponent>(OtherComp));
	if (b == INDEX_NONE || BoxAgent[b] < 0)
	{
		return;
	}
	const uint32 Id = (uint32)BoxAgent[b];
	const FAgent* Ag = Agents.FindByPredicate([Id](const FAgent& A) { return A.Id == Id; });
	if (!Ag)
	{
		return;
	}
	// PlayerVel was read in this (pre-physics) tick, so it is the speed going into the contact
	float& Worst = HitClosing.FindOrAdd(Id, 0.0f);
	Worst = FMath::Max(Worst, ClosingCmS(PlayerVel, Ag->CurRot.GetForwardVector() * Ag->V, Hit.ImpactNormal));
}

void AODTrafficManager::ProcessHits()
{
	for (const TPair<uint32, float>& H : HitClosing)
	{
		const uint32 Id = H.Key;
		const int32 i = Agents.IndexOfByPredicate([Id](const FAgent& A) { return A.Id == Id; });
		if (i == INDEX_NONE)
		{
			continue;
		}
		FAgent& Ag = Agents[i];
		if (H.Value < WreckClosingCmS)
		{
			Ag.HoldUntil = SimTime + BumpHoldS;   // a bump: the car stops, the player bounces off
			continue;
		}
		const FModel& M = Models[Ag.Model];
		FWreck& Wr = Wrecks.AddDefaulted_GetRef();
		Wr.Born = SimTime;
		Wr.Comp = SpawnWreck(this, M.Chassis, M.Wheel[0] >= 0 ? WheelIsms[M.Wheel[0]]->GetStaticMesh() : nullptr,
			M.FrontX, M.RearX, M.HalfTrack, M.WheelR, Ag.CurPos, Ag.CurRot, Ag.CurRot.GetForwardVector() * Ag.V, Wr.Parts);
		UE_LOG(LogTemp, Display, TEXT("ODTrafficManager: player hit car %u at %.0f km/h closing (car %.0f km/h) - now a physics wreck"),
			Id, H.Value * 0.036f, Ag.V * 0.036f);
		Agents.RemoveAtSwap(i);
	}
	HitClosing.Reset();
	// wrecks go once settled (Phase 0 handled the player side)
	for (int32 w = Wrecks.Num() - 1; w >= 0; --w)
	{
		UBoxComponent* C = Wrecks[w].Comp;
		const double Age = SimTime - Wrecks[w].Born;
		if (!C || Age > 20.0 || (Age > 4.0 && C->GetPhysicsLinearVelocity().SizeSquared() < 30.0 * 30.0))
		{
			int32 Attached = 0;
			for (UStaticMeshComponent* Part : Wrecks[w].Parts)
			{
				Attached += C && Part->GetAttachParent() == C ? 1 : 0;
				Part->DestroyComponent();
			}
			if (C)
			{
				UE_LOG(LogTemp, Display, TEXT("ODTrafficManager: wreck cleared after %.1f s, chassis + wheels attached %d/%d"), Age, Attached, Wrecks[w].Parts.Num());
				C->DestroyComponent();
			}
			Wrecks.RemoveAtSwap(w);
		}
	}
}

void AODTrafficManager::UpdateAudio(float Alpha)
{
	if (SimTime - LastVolumeRead > 2.0)
	{
		LastVolumeRead = SimTime;
		float Master = 1.0f, Ambient = 1.0f;
		GConfig->GetFloat(TEXT("OpenDrive.Audio"), TEXT("MasterVolume"), Master, GGameUserSettingsIni);
		GConfig->GetFloat(TEXT("OpenDrive.Audio"), TEXT("AmbientVolume"), Ambient, GGameUserSettingsIni);
		AudioVolume = FMath::Clamp(Master * Ambient, 0.0f, 1.0f);
	}
	// pass-by: the closest NumPassBy cars get an engine voice
	TArray<TPair<double, int32>, TInlineAllocator<64>> Near;
	int32 Idle = 0;
	for (int32 i = 0; i < Agents.Num(); ++i)
	{
		const double D2 = FVector::DistSquared(Agents[i].CurPos, PlayerPos);
		if (D2 < PassByRangeCm * PassByRangeCm)
		{
			Near.Emplace(D2, i);
		}
		if (D2 < 10000.0 * 10000.0 && Agents[i].V < 300.0f)
		{
			++Idle;
		}
	}
	Near.Sort([](const TPair<double, int32>& A, const TPair<double, int32>& B) { return A.Key < B.Key; });
	for (int32 k = 0; k < PassBy.Num(); ++k)
	{
		UODEngineSynth* S = PassBy[k];
		if (k >= Near.Num())
		{
			S->SetVolumeScale(0.0f);
			continue;
		}
		const FAgent& Ag = Agents[Near[k].Value];
		S->SetWorldLocation(FMath::Lerp(Ag.PrevPos, Ag.CurPos, (double)Alpha));
		// a 5-speed road car: each gear spans 1500 -> 4500 rpm
		const float Kmh = Ag.V * 0.036f;
		static const float GearTop[] = {25.0f, 45.0f, 70.0f, 100.0f, 1000.0f};
		float Lo = 0.0f, Rpm = 800.0f;
		for (float Top : GearTop)
		{
			if (Kmh < Top || Top > 999.0f)
			{
				const float Span = FMath::Min(Top, 140.0f) - Lo;
				Rpm = Kmh < 3.0f ? 800.0f : 1500.0f + 3000.0f * FMath::Clamp((Kmh - Lo) / FMath::Max(Span, 1.0f), 0.0f, 1.0f);
				break;
			}
			Lo = Top * 0.75f;
		}
		const float Throttle = FMath::Clamp(Ag.A / 150.0f, 0.0f, 1.0f);
		S->SetEngineState(Rpm, Throttle, Throttle);
		S->SetVolumeScale(AudioVolume);
	}
	if (IdleBed)
	{
		IdleBed->SetEngineState(650.0f, 0.05f, 0.05f);
		IdleBed->SetVolumeScale(AudioVolume * 0.6f * FMath::Clamp((Idle - 3) / 25.0f, 0.0f, 1.0f));
	}
}

void AODTrafficManager::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	const double Now = GetWorld()->GetTimeSeconds();
	const APawn* Pawn = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Pawn)
	{
		return;
	}
	if (!DayNight.IsValid())
	{
		for (TActorIterator<AODDayNightController> It(GetWorld()); It; ++It)
		{
			DayNight = *It;
			break;
		}
	}
	if (Now - LastWantCheck > 1.0)
	{
		LastWantCheck = Now;
		const bool bWant = TrafficWanted();
		if (bWant != bEnabled)
		{
			bEnabled = bWant;
			ClearAgents();
			UE_LOG(LogTemp, Display, TEXT("ODTrafficManager: traffic %s"), bEnabled ? TEXT("on") : TEXT("off"));
		}
	}
	if (!bEnabled)
	{
		return;
	}
	// the nearest open-world area's graph (like the HUD's nav)
	if (Now - LastRoadCheck > 2.0 && !bLoading)
	{
		LastRoadCheck = Now;
		AODRoadManager* Best = nullptr;
		double BestD2 = TNumericLimits<double>::Max();
		for (TActorIterator<AODRoadManager> It(GetWorld()); It; ++It)
		{
			if (It->Tags.Contains(ODVistaTag) || !It->CenterlineFilePrefix.StartsWith(TEXT("centerline_")) || !It->HasRouteGraph())
			{
				continue;
			}
			FVector2D Snap;
			const double D2 = It->SnapToRouteGraph(FVector2D(Pawn->GetActorLocation()), Snap)
				? FVector2D::DistSquared(Snap, FVector2D(Pawn->GetActorLocation())) : TNumericLimits<double>::Max() * 0.5;
			if (D2 < BestD2)
			{
				BestD2 = D2;
				Best = *It;
			}
		}
		if (Best && Best != GraphRoad.Get())
		{
			StartLoad(Best);
		}
	}
	if (!Graph.IsValid())
	{
		return;
	}
	if (UPrimitiveComponent* Body = Cast<UPrimitiveComponent>(Pawn->GetRootComponent()); Body && Body != HookedPlayerBody.Get())
	{
		Body->OnComponentHit.AddUniqueDynamic(this, &AODTrafficManager::OnPlayerHit);
		HookedPlayerBody = Body;
	}
	const FVector WasPos = PlayerPos;
	PlayerPos = Pawn->GetActorLocation();
	PlayerVel = Pawn->GetVelocity();
	const APlayerCameraManager* Cam = UGameplayStatics::GetPlayerCameraManager(this, 0);
	CamFwd = FVector2D(Cam ? Cam->GetCameraRotation().Vector() : Pawn->GetActorForwardVector()).GetSafeNormal();

	int32 Hour, Dow;
	ClockHourDow(Hour, Dow);
	HourBucket = Hour + 24 * Dow;
	ProcessHits();

	// a jump (spawn, teleport, area swap) refills the range; a drive tops it up
	const double Moved = FVector2D::Distance(FVector2D(PlayerPos), FVector2D(RangeCenter));
	// a teleport shorter than the radius (respawn on a nearby road) also refills,
	// or it could land the player on a car that was already there
	if (Moved > SpawnRadiusCm || FVector::Dist2D(WasPos, PlayerPos) > 10000.0)
	{
		ClearAgents();
		UpdateRange(true);
	}
	else if (Moved > 5000.0)
	{
		UpdateRange(false);
	}

	const double T0 = FPlatformTime::Seconds();
	Accum = FMath::Min(Accum + DeltaSeconds, StepS * 8.0f);
	while (Accum >= StepS)
	{
		Accum -= StepS;
		Step(StepS);
		if (SimTime - LastTopUp >= 0.5)
		{
			LastTopUp = SimTime;
			RebuildOrder();
			TopUp();
		}
	}
	const float Alpha = Accum / StepS;
	Render(Alpha);
	UpdateCollision(Alpha);
	UpdateAudio(Alpha);
	UpdateLights();
	const float Ms = (FPlatformTime::Seconds() - T0) * 1000.0;
	PeakMs = FMath::Max(PeakMs, Ms);

	if (Now - LastDiag > 5.0)
	{
		LastDiag = Now;
		int32 Slow = 0;
		for (const FAgent& Ag : Agents)
		{
			Slow += Ag.V < 300.0f ? 1 : 0;
		}
		UE_LOG(LogTemp, Display, TEXT("ODTrafficManager: '%s' %d car(s) (%d under 11 km/h) on %d link(s) in range, hour %d day %d, peak %.2f ms/frame, wrecks %d, wheel instances %d (%s)"),
			*Graph->Name, Agents.Num(), Slow, RangeLinks.Num(), Hour, Dow, PeakMs, Wrecks.Num(),
			WheelIsms.Num() ? WheelIsms[0]->GetInstanceCount() : -1,
			WheelIsms.Num() && WheelIsms[0]->GetInstanceCount() ? *FString::Printf(TEXT("%s visible %d"), *WheelIsms[0]->GetStaticMesh()->GetName(), WheelIsms[0]->IsVisible()) : TEXT("-"));
		const float N = FMath::Max(DiagSteps, 1);
		UE_LOG(LogTemp, Display, TEXT("ODTrafficManager: AI-AI contacts %d pair(s) (%d at junctions), %.1f overlapping on average; slow cars held by lane leader %.0f, other-link car %.0f, player/wreck/bump %.0f, stop line %.0f"),
			OverlapPairs.Num(), OverlapJunction, OverlapSteps / N, SlowWhy[0] / N, SlowWhy[1] / N, SlowWhy[2] / N, SlowWhy[3] / N);
		// the health line: what "traffic flows" means, checkable from a log alone
		TArray<float> Kmh;
		int32 Hist[5] = {}, Still20 = 0, Still60 = 0;
		for (const FAgent& Ag : Agents)
		{
			const float K = Ag.V * 0.036f;
			Kmh.Add(K);
			++Hist[K < 11.0f ? 0 : K < 30.0f ? 1 : K < 50.0f ? 2 : K < 80.0f ? 3 : 4];
			Still20 += Ag.StillS > 20.0f ? 1 : 0;
			Still60 += Ag.StillS > 60.0f ? 1 : 0;
		}
		Kmh.Sort();
		UE_LOG(LogTemp, Display, TEXT("ODPerfTraffic: '%s' cars %d | slow<11kmh %.0f%% median %.0f km/h | kmh 0-11/11-30/30-50/50-80/80+ %d/%d/%d/%d/%d | still>20s %d still>60s %d | spawns %d min spawn dist %.0f m"),
			*Graph->Name, Agents.Num(), Agents.Num() ? 100.0f * Hist[0] / Agents.Num() : 0.0f, Kmh.Num() ? Kmh[Kmh.Num() / 2] : 0.0f,
			Hist[0], Hist[1], Hist[2], Hist[3], Hist[4], Still20, Still60, WindowSpawns, WindowSpawns ? MinSpawnCm / 100.0 : -1.0);
		WindowSpawns = 0;
		MinSpawnCm = 1.0e12;
		OverlapPairs.Reset();
		OverlapJunction = OverlapSteps = DiagSteps = 0;
		FMemory::Memzero(SlowWhy);
		PeakMs = 0.0f;
	}
}

// ---- headless self-check ---------------------------------------------------
// UnrealEditor-Cmd OpenDriveUE.uproject -ExecCmds="Automation RunTests
// OpenDrive.Traffic;Quit" -unattended -nullrhi -testexit="Automation Test Queue Empty"
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FODTrafficCollisionCheck, "OpenDrive.Traffic.Collisions",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext | EAutomationTestFlags::ProductFilter)

bool FODTrafficCollisionCheck::RunTest(const FString& Parameters)
{
	// ---- wreck threshold: closing speed along the contact normal ----
	const FVector X(1, 0, 0), Y(0, 1, 0);
	auto Kmh = [](float K) { return K / 0.036f; };
	TestFalse(TEXT("0 km/h touch"), ClosingCmS(FVector::ZeroVector, FVector::ZeroVector, X) >= WreckClosingCmS);
	TestFalse(TEXT("6 km/h nudge into a parked car"), ClosingCmS(X * Kmh(6), FVector::ZeroVector, -X) >= WreckClosingCmS);
	TestFalse(TEXT("rear-end 15 km/h faster"), ClosingCmS(X * Kmh(65), X * Kmh(50), -X) >= WreckClosingCmS);
	TestFalse(TEXT("80 km/h sideswipe of a 40 km/h car"), ClosingCmS(X * Kmh(80), X * Kmh(40), Y) >= WreckClosingCmS);
	TestTrue(TEXT("30 km/h into a parked car"), ClosingCmS(X * Kmh(30), FVector::ZeroVector, -X) >= WreckClosingCmS);
	TestTrue(TEXT("head-on 30 + 30"), ClosingCmS(X * Kmh(30), -X * Kmh(30), -X) >= WreckClosingCmS);
	TestTrue(TEXT("no normal: full relative speed"), ClosingCmS(X * Kmh(56), FVector::ZeroVector, FVector::ZeroVector) >= WreckClosingCmS);

	// ---- spacing: 90 km/h, a stopped car cuts in 3 m ahead, IDM braking saturated ----
	float S = 0.0f, V = Kmh(90), MinGap = 1.0e9f;
	const float LeaderS = CarLenCm + 300.0f;
	for (int32 k = 0; k < 100; ++k)
	{
		Advance(S, V, -MaxDecel * 100.0f, StepS, LeaderS - S - CarLenCm, 0.0f);
		MinGap = FMath::Min(MinGap, LeaderS - S - CarLenCm);
	}
	AddInfo(FString::Printf(TEXT("cut-in at 90 km/h: min bumper gap %.0f cm, end speed %.1f cm/s"), MinGap, V));
	TestTrue(TEXT("cut-in never closes past the contact floor"), MinGap >= ContactGapCm - 0.01f);
	TestEqual(TEXT("cut-in ends stopped"), V, 0.0f);
	float S2 = 0.0f, V2 = 1000.0f;
	Advance(S2, V2, 100.0f, StepS, 1.0e9f, 0.0f);
	TestEqual(TEXT("free road: plain trapezoid step"), S2, 0.5f * (1000.0f + 1005.0f) * StepS, 1e-3f);

	// ---- any-link neighbours ----
	const FQuat East = FQuat::Identity, North = FRotator(0, 90, 0).Quaternion(), West = FRotator(0, 180, 0).Quaternion();
	double Along;
	TestTrue(TEXT("car stopped 8 m ahead on another link"), Blocks(FVector::ZeroVector, East, 1, FVector(800, 0, 0), East, 2, Along));
	TestFalse(TEXT("adjacent lane (3.5 m) is not in the way"), Blocks(FVector::ZeroVector, East, 1, FVector(600, 350, 0), East, 2, Along));
	TestFalse(TEXT("oncoming lane is not in the way"), Blocks(FVector::ZeroVector, East, 1, FVector(800, 100, 0), West, 2, Along));
	TestTrue(TEXT("car crossing 6 m ahead"), Blocks(FVector::ZeroVector, East, 1, FVector(600, 50, 0), North, 2, Along));
	// a 60 degree merge, noses level: each is in the other's corridor, exactly one yields
	const FQuat In = FRotator(0, 60, 0).Quaternion();
	const FVector PB(100, -150, 0);
	const bool bAB = Blocks(FVector::ZeroVector, East, 7, PB, In, 9, Along);
	const bool bBA = Blocks(PB, In, 9, FVector::ZeroVector, East, 7, Along);
	TestTrue(TEXT("merge: exactly one car yields"), bAB != bBA);

	// a car waiting at a junction must not stand in the crossing lane: north-bound car 10 m
	// short of the node, east-bound car waiting at the stop line of a 1+1 lane crossing road
	const FVector Crosser(-0.5f * LaneWidthCm, -1000.0f, 0.0f);
	const FVector Waiting(-JunctionStopCm(1, false), 0.5f * LaneWidthCm, 0.0f);
	TestFalse(TEXT("waiting car clear of the crossing lane"), Blocks(Crosser, North, 1, Waiting, East, 2, Along));
	TestTrue(TEXT("(the old 1.5 m stop line was in it)"), Blocks(Crosser, North, 1, FVector(-StopLineCm, 0.5f * LaneWidthCm, 0.0f), East, 2, Along));

	// ---- contact census ----
	const FVector E(230, 95, 70);
	TestFalse(TEXT("adjacent lanes don't overlap"), Overlaps(FVector::ZeroVector, East, E, FVector(0, LaneWidthCm, 0), East, E));
	TestFalse(TEXT("2 m bumper gap doesn't overlap"), Overlaps(FVector::ZeroVector, East, E, FVector(660, 0, 0), East, E));
	TestTrue(TEXT("nose into a door overlaps"), Overlaps(FVector::ZeroVector, East, E, FVector(300, 0, 0), North, E));
	TestFalse(TEXT("bridge over road"), Overlaps(FVector::ZeroVector, East, E, FVector(0, 0, 600), North, E));

	// ---- wheels ride a wreck ----
	// (a test world's physics scene doesn't step headless, so the body is moved the way the
	// physics sync moves a simulating component: a teleporting transform update)
	UWorld* World = UWorld::CreateWorld(EWorldType::Game, false);
	FWorldContext& Ctx = GEngine->CreateNewWorldContext(EWorldType::Game);
	Ctx.SetCurrentWorld(World);
	World->InitializeActorsForPlay(FURL());
	World->BeginPlay();
	AActor* Owner = World->SpawnActor<AActor>();
	USceneComponent* Root = NewObject<USceneComponent>(Owner);
	Owner->SetRootComponent(Root);
	Root->RegisterComponent();
	UStaticMesh* Cube = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube"));
	UStaticMesh* Cyl = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cylinder.Cylinder"));
	// a 60 cm wheel under a 100 cm cube: the tyres reach 10 cm below the chassis bbox, as the
	// real cars' do (floor 8-24 cm under the axle, tyre a wheel radius under it)
	const float WheelR = 60.0f, FrontX = 132.0f, RearX = -128.0f, HalfTrack = 84.0f;
	const FVector Pos(0, 0, 100);
	const FQuat Rot = FRotator(0, 30, 0).Quaternion();
	TArray<UStaticMeshComponent*, TInlineAllocator<5>> Parts;
	UBoxComponent* Body = SpawnWreck(Owner, Cube, Cyl, FrontX, RearX, HalfTrack, WheelR, Pos, Rot, FVector(1500, 0, 0), Parts);
	TestTrue(TEXT("wreck body simulates"), Body->IsSimulatingPhysics());
	TestTrue(TEXT("wreck body detached from the manager (free body)"), Body->GetAttachParent() == nullptr);
	TestEqual(TEXT("chassis + four wheels"), Parts.Num(), 5);
	if (Parts.Num() != 5)
	{
		return false;
	}
	TestTrue(TEXT("chassis where the car was"), Parts[0]->GetComponentLocation().Equals(Pos, 1.0));
	for (int32 w = 1; w < 5; ++w)
	{
		// the collision box must hold the tyres up, or the wreck rests on its floor with the
		// wheels sunk into the road
		const double TyreBottom = Parts[w]->GetRelativeLocation().Z - WheelR;
		TestTrue(*FString::Printf(TEXT("collision reaches the tyre bottom (%.1f vs %.1f)"), TyreBottom, -Body->GetUnscaledBoxExtent().Z),
			TyreBottom >= -Body->GetUnscaledBoxExtent().Z - 0.5);
		// same hub as the live ISM wheel: car transform x (axle x, +-half track, 0)
		const FVector Hub(w < 3 ? FrontX : RearX, (w % 2 == 1 ? -1.0f : 1.0f) * HalfTrack, 0.0f);
		TestTrue(TEXT("wheel on the live car's hub"), Parts[w]->GetComponentLocation().Equals(Pos + Rot.RotateVector(Hub), 1.0));
	}
	// tumble it: upside down, 40 m away
	Body->SetWorldLocationAndRotation(FVector(4000, -500, 300), FRotator(170, 75, -60).Quaternion(), false, nullptr, ETeleportType::TeleportPhysics);
	const FTransform BodyXf = Body->GetComponentTransform();
	for (UStaticMeshComponent* P : Parts)
	{
		const double Drift = FVector::Dist(P->GetComponentLocation(), BodyXf.TransformPosition(P->GetRelativeLocation()));
		TestTrue(TEXT("part attached to the body"), P->GetAttachParent() == Body);
		TestFalse(TEXT("part not simulating"), P->IsSimulatingPhysics());
		TestEqual(TEXT("part has no collision"), P->GetCollisionEnabled(), ECollisionEnabled::NoCollision);
		TestTrue(*FString::Printf(TEXT("part stays on the body (drift %.2f cm)"), Drift), Drift < 1.0);
		TestTrue(TEXT("part next to the body"), FVector::Dist(P->GetComponentLocation(), BodyXf.GetLocation()) < 400.0);
		// drawable: registered, visible, a mesh and materials, real scale, no cull distance
		TestTrue(TEXT("part registered"), P->IsRegistered());
		TestTrue(TEXT("part visible"), P->IsVisible() && !P->bHiddenInGame);
		TestTrue(TEXT("part has a mesh"), P->GetStaticMesh() != nullptr);
		bool bMats = P->GetNumMaterials() > 0;
		for (int32 i = 0; i < P->GetNumMaterials(); ++i)
		{
			bMats &= P->GetMaterial(i) != nullptr;
		}
		TestTrue(TEXT("part has materials"), bMats);
		TestTrue(TEXT("part scale > 0"), P->GetComponentScale().GetMin() > 0.1);
		TestTrue(TEXT("part never distance-culled"), P->LDMaxDrawDistance == 0.0f && P->CachedMaxDrawDistance == 0.0f);
	}
	GEngine->DestroyWorldContext(World);
	World->DestroyWorld(false);
	return true;
}
