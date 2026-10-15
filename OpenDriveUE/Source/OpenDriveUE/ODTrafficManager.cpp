#include "ODTrafficManager.h"

#include "Algo/Sort.h"
#include "Algo/BinarySearch.h"
#include "Async/Async.h"
#include "Components/BoxComponent.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInstanceDynamic.h"
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
constexpr float ReferenceWheelMeshRadiusCm = 34.0f;   // ODVehiclePawn.cpp
constexpr int32 NumBoxes = 12;
constexpr int32 NumPassBy = 4;
constexpr float BoxRangeCm = 6000.0f;
constexpr float PassByRangeCm = 6000.0f;

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

void SyncIsm(UInstancedStaticMeshComponent* Ism, const TArray<FTransform>& Xf)
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
		Ism->BatchUpdateInstancesTransforms(0, TArrayView<const FTransform>(Xf.GetData(), FMath::Min(Have, Xf.Num())), true, true, true);
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
	TMap<UStaticMesh*, int32> WheelIdx;
	for (const TCHAR* Path : ModelDefs)
	{
		const UODVehicleDef* Def = LoadObject<UODVehicleDef>(nullptr, Path, nullptr, LOAD_NoWarn | LOAD_Quiet);
		UStaticMesh* Chassis = Def ? ODVisualStyle::Pick(Def->ChassisMesh.LoadSynchronous()) : nullptr;
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
		auto MakeIsm = [&](UStaticMesh* Mesh, float CullCm, bool bShadow)
		{
			UInstancedStaticMeshComponent* Ism = NewObject<UInstancedStaticMeshComponent>(this);
			Ism->SetupAttachment(RootComponent);
			Ism->SetMobility(EComponentMobility::Movable);
			Ism->SetStaticMesh(Mesh);
			Ism->SetCollisionEnabled(ECollisionEnabled::NoCollision);
			Ism->SetCanEverAffectNavigation(false);
			Ism->SetCastShadow(bShadow);
			Ism->SetCullDistances(0, (int32)CullCm);
			Ism->RegisterComponent();
			ODVisualStyle::PickSlots(Ism);
			return Ism;
		};
		M.Body = MakeIsm(Chassis, DespawnRadiusCm, true);
		M.Braking = MakeIsm(Chassis, DespawnRadiusCm, true);
		for (UInstancedStaticMeshComponent* Ism : {M.Body, M.Braking})
		{
			const int32 Slot = Ism->GetMaterialIndex(TEXT("lights"));
			(Ism == M.Body ? M.LampMID : M.BrakeMID) = Slot != INDEX_NONE ? Ism->CreateDynamicMaterialInstance(Slot) : nullptr;
		}
		if (UStaticMesh* Wheel = ODVisualStyle::Pick(Def->WheelMesh.LoadSynchronous()))
		{
			if (const int32* W = WheelIdx.Find(Wheel))
			{
				M.Wheel = *W;
			}
			else
			{
				M.Wheel = WheelIsms.Add(MakeIsm(Wheel, 25000.0f, false));
				WheelIdx.Add(Wheel, M.Wheel);
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
		M.Body->ClearInstances();
		M.Braking->ClearInstances();
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

FVector AODTrafficManager::PosAt(int32 Link, float S, int32 Next) const
{
	const FODTrafficGraph& G = *Graph;
	if (S > G.Links[Link].Length && Next >= 0)
	{
		S -= G.Links[Link].Length;
		Link = Next;
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
	FVector F = PosAt(Ag.Link, Ag.S + 300.0f, Ag.Next) - PosAt(Ag.Link, Ag.S - 300.0f, Ag.Next);
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
		if (B.Link == In && Dist < 8000.0f && Dist / FMath::Max(B.V, 100.0f) < GapAcceptS)
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
	if (L.OutNum == 0)
	{
		return -1;
	}
	if (L.OutNum == 1)
	{
		return Graph->Out[L.OutStart];
	}
	// split by demand: each branch weighted by its target flow
	float Total = 0.0f;
	float W[16];
	const int32 N = FMath::Min(L.OutNum, 16);
	for (int32 i = 0; i < N; ++i)
	{
		const int32 C = Graph->Out[L.OutStart + i];
		float V;
		const float K = TargetPerCm(C, &V);
		W[i] = K * V + 0.002f * Graph->Links[C].Lanes;
		Total += W[i];
	}
	Ag.Seed = Hash(Ag.Seed + 0x9e3779b9U);
	float R = Unit(Ag.Seed) * Total;
	for (int32 i = 0; i < N; ++i)
	{
		R -= W[i];
		if (R <= 0.0f)
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
	return true;
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
				if (FVector2D::DistSquared(FVector2D(P), FVector2D(PlayerPos)) < (double)SpawnRadiusCm * SpawnRadiusCm
					&& FVector::DistSquared(P, PlayerPos) > 1500.0 * 1500.0)
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
	for (const FRangeLink& RL : RangeLinks)
	{
		float V;
		const float K = TargetPerCm(RL.Link, &V);
		const float Target = K * RL.InRangeCm;
		const int32 Have = SliceStamp[RL.Link] == StepId ? SliceNum[RL.Link] : 0;
		const FODTrafficGraph::FLink& L = G.Links[RL.Link];
		if (Have > Target * 1.5f + 2.0f)
		{
			// the clock moved off a peak: thin the link out where nobody sees it
			for (int32 o = SliceStart[RL.Link] + Have - 1; o >= SliceStart[RL.Link]; --o)
			{
				if (FVector::DistSquared(Agents[Order[o]].CurPos, PlayerPos) > (double)PopInCm * PopInCm)
				{
					Remove.Add(Order[o]);
					break;
				}
			}
			continue;
		}
		++TopUpSalt;
		if (Budget <= 0 || Have + Unit(TopUpSalt * 7 + RL.Link) > Target)
		{
			continue;
		}
		// alternate the two ends of the in-range stretch, so the road ahead of
		// the player fills as well as the road behind
		const float S = (TopUpSalt & 1) ? RL.EntryS : FMath::Max(RL.EntryS, RL.ExitS - 2000.0f);
		if (FVector::DistSquared(PosAt(RL.Link, S, -1), PlayerPos) < (double)PopInCm * PopInCm)
		{
			continue;
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

		// the player as a leader: in this lane's corridor, ahead
		const FVector D = PlayerPos - Ag.CurPos;
		if (D.SizeSquared() < 15000.0 * 15000.0 && FMath::Abs(D.Z) < 400.0)
		{
			const FVector F = Ag.CurRot.GetForwardVector(), R = Ag.CurRot.GetRightVector();
			const double Along = FVector::DotProduct(D, F);
			if (Along > 0.0 && FMath::Abs(FVector::DotProduct(D, R)) < 220.0 && Along - CarLenCm < Gap)
			{
				Gap = Along - CarLenCm;
				LV = FMath::Max(0.0, FVector::DotProduct(PlayerVel, F));
			}
		}

		const float DistEnd = L.Length - Ag.S;
		auto StopAt = [&](float Dist)
		{
			if (Dist < Gap)
			{
				Gap = Dist;
				LV = 0.0f;
			}
		};
		const FODTrafficGraph::FLink* NL = Ag.Next >= 0 ? &G.Links[Ag.Next] : nullptr;
		if (!NL)
		{
			StopAt(DistEnd - StopLineCm);   // dead end with no way round: despawned there
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
			const bool bStopSign = L.EndControl == 2 && L.bYield;
			const bool bGiveWay = (L.EndControl == 3 || L.EndControl == 4) && L.bYield;
			if (L.EndControl == 1)
			{
				const int32 St = SignalState(Ag.Link);
				const float StopDist = DistEnd - StopLineCm;
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
				if (DistEnd < StopLineCm + 400.0f && Ag.V < 50.0f)
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
					StopAt(DistEnd - StopLineCm);
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
					StopAt(DistEnd - StopLineCm);
				}
			}
			else
			{
				Ag.bCleared = true;   // priority approach / uncontrolled
			}
		}

		Ag.A = Idm(Ag.V, V0, Gap, LV);
		const float NewV = FMath::Max(0.0f, Ag.V + Ag.A * Dt);
		Ag.S += 0.5f * (Ag.V + NewV) * Dt;
		Ag.V = NewV;

		while (Ag.S >= G.Links[Ag.Link].Length)
		{
			if (Ag.Next < 0)
			{
				Ag.S = G.Links[Ag.Link].Length;
				Remove.Add(i);
				break;
			}
			const FODTrafficGraph::FLink& Old = G.Links[Ag.Link];
			const bool bJunction = Old.EndCluster >= 0;
			Ag.S -= Old.Length;
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
		const float LatTarget = LaneLat(Ag.Link, Ag.Lane);
		Ag.Lat += FMath::Clamp(LatTarget - Ag.Lat, -150.0f * Dt, 150.0f * Dt);
		Ag.PrevPos = Ag.CurPos;
		Ag.PrevRot = Ag.CurRot;
		Pose(Ag, Ag.CurPos, Ag.CurRot);
		if (FVector2D::DistSquared(FVector2D(Ag.CurPos), FVector2D(PlayerPos)) > Despawn2)
		{
			Remove.AddUnique(i);
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
	for (TArray<FTransform>& X : BodyXf) { X.Reset(); }
	for (TArray<FTransform>& X : BrakeXf) { X.Reset(); }
	for (TArray<FTransform>& X : WheelXf) { X.Reset(); }
	BodyXf.SetNum(Models.Num());
	BrakeXf.SetNum(Models.Num());
	for (const FAgent& Ag : Agents)
	{
		const FModel& M = Models[Ag.Model];
		const FTransform Xf(FQuat::Slerp(Ag.PrevRot, Ag.CurRot, Alpha), FMath::Lerp(Ag.PrevPos, Ag.CurPos, (double)Alpha));
		// brake lamps on under braking and while stood in a queue
		(Ag.A < -150.0f || Ag.V < 50.0f ? BrakeXf : BodyXf)[Ag.Model].Add(Xf);
		if (M.Wheel >= 0)
		{
			const FVector Sc(M.WheelR / ReferenceWheelMeshRadiusCm);
			for (int32 w = 0; w < 4; ++w)
			{
				const FVector Local(w < 2 ? M.FrontX : M.RearX, (w % 2 == 0 ? -1.0f : 1.0f) * M.HalfTrack, 0.0f);
				WheelXf[M.Wheel].Add(FTransform(Xf.GetRotation(), Xf.TransformPosition(Local), Sc));
			}
		}
	}
	for (int32 m = 0; m < Models.Num(); ++m)
	{
		SyncIsm(Models[m].Body, BodyXf[m]);
		SyncIsm(Models[m].Braking, BrakeXf[m]);
	}
	for (int32 w = 0; w < WheelIsms.Num(); ++w)
	{
		SyncIsm(WheelIsms[w], WheelXf[w]);
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
		for (UMaterialInstanceDynamic* MID : {M.LampMID, M.BrakeMID})
		{
			if (MID)
			{
				MID->SetScalarParameterValue(TEXT("HeadGlow"), bNight ? HeadGlow : 0.0f);
				MID->SetScalarParameterValue(TEXT("TailGlow"), MID == M.BrakeMID ? BrakeGlow : (bNight ? TailGlow : 0.0f));
				MID->SetScalarParameterValue(TEXT("ReverseGlow"), 0.0f);
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
	if (b != INDEX_NONE && BoxAgent[b] >= 0)
	{
		HitIds.AddUnique((uint32)BoxAgent[b]);
	}
}

void AODTrafficManager::ProcessHits()
{
	for (uint32 Id : HitIds)
	{
		const int32 i = Agents.IndexOfByPredicate([Id](const FAgent& A) { return A.Id == Id; });
		if (i == INDEX_NONE)
		{
			continue;
		}
		const FAgent& Ag = Agents[i];
		const FModel& M = Models[Ag.Model];
		UStaticMeshComponent* W = NewObject<UStaticMeshComponent>(this);
		W->SetupAttachment(RootComponent);
		W->SetMobility(EComponentMobility::Movable);
		W->SetStaticMesh(M.Chassis);
		W->SetWorldLocationAndRotation(Ag.CurPos, Ag.CurRot);
		W->SetCollisionProfileName(TEXT("PhysicsActor"));
		W->RegisterComponent();
		ODVisualStyle::PickSlots(W);
		W->SetMassOverrideInKg(NAME_None, 1400.0f, true);
		W->SetSimulatePhysics(true);
		W->SetPhysicsLinearVelocity(Ag.CurRot.GetForwardVector() * Ag.V);
		Wrecks.Add({W, SimTime});
		UE_LOG(LogTemp, Display, TEXT("ODTrafficManager: player hit car %u at %.0f km/h - now a physics wreck"), Id, Ag.V * 0.036f);
		Agents.RemoveAtSwap(i);
	}
	HitIds.Reset();
	// wrecks go once settled (Phase 0 handled the player side)
	for (int32 w = Wrecks.Num() - 1; w >= 0; --w)
	{
		UStaticMeshComponent* C = Wrecks[w].Comp;
		const double Age = SimTime - Wrecks[w].Born;
		if (!C || Age > 20.0 || (Age > 4.0 && C->GetPhysicsLinearVelocity().SizeSquared() < 30.0 * 30.0))
		{
			if (C)
			{
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
	PlayerPos = Pawn->GetActorLocation();
	PlayerVel = Pawn->GetVelocity();

	int32 Hour, Dow;
	ClockHourDow(Hour, Dow);
	HourBucket = Hour + 24 * Dow;
	ProcessHits();

	// a jump (spawn, teleport, area swap) refills the range; a drive tops it up
	const double Moved = FVector2D::Distance(FVector2D(PlayerPos), FVector2D(RangeCenter));
	if (Moved > SpawnRadiusCm)
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
		UE_LOG(LogTemp, Display, TEXT("ODTrafficManager: '%s' %d car(s) (%d under 11 km/h) on %d link(s) in range, hour %d day %d, peak %.2f ms/frame, wrecks %d"),
			*Graph->Name, Agents.Num(), Slow, RangeLinks.Num(), Hour, Dow, PeakMs, Wrecks.Num());
		PeakMs = 0.0f;
	}
}
