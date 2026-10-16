#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODTrafficManager.generated.h"

class UBoxComponent;
class UInstancedStaticMeshComponent;
class UMaterialInstanceDynamic;
class UODEngineSynth;
class UStaticMesh;
class UStaticMeshComponent;
class AODRoadManager;
struct FODTrafficGraph;

/**
 * Ambient traffic (PLAN-REALISM 3c-3e, 2026-10-14).
 *
 * Reads the nearest area's lane graph (routegraph_<area>_test.bin ODG1 lane
 * block, PLAN-REALISM 3a) and its demand (traffic_<area>_test.bin ODT1,
 * pipeline/build_traffic_demand.py, 3b). The 5 m station edges are merged
 * into directed links: runs of degree-2 nodes with the same lanes / speed /
 * class / one-way, so a lane drop or a junction ends a link.
 *
 * Density (D4): per link, hourly one-way demand q (DemandVph, a mirror of
 * build_traffic_demand.demand_vph) against capacity c (capacity_vph) on a
 * triangular fundamental diagram. q <= c is the free branch (k = q / v_f).
 * q > c is the congested branch: throughput c^2/q at the wave speed that
 * keeps the diagram continuous, so I-5 at 17:30 fills to a crawl. The
 * congested speed also caps the link's IDM desired speed, which keeps the
 * queue in place until the clock moves past the peak.
 * pipeline/tests/test_traffic_runtime.py mirrors the maths and constants.
 *
 * Agents: IDM car following on link polylines at a fixed 20 Hz step (render
 * interpolates), with the player as a possible leader. At a link end:
 * strict fixed-cycle signals, full stop at stop signs, gap acceptance at
 * stop / give-way (D5); a lane that ends forces a lane change (MOBIL safety
 * test) or a stop at the lane end, which is what turns a lane drop at
 * capacity into a queue. Route choice at junctions is weighted by demand.
 *
 * ponytail: one IDM tier out to SpawnRadiusCm, drawn as instanced meshes,
 * no far cell-transmission tier (plan 3c); add one if a full jam outgrows
 * MaxAgents. Leader search is O(lane neighbours) on a per-step sort by
 * (link, s), not O(N^2).
 *
 * Off in a race (D3: any AODRaceManager with gates), with -odtraffic=0, or
 * with the Settings > Game > Traffic row Off (IsEnabledSetting).
 */
UCLASS()
class OPENDRIVEUE_API AODTrafficManager : public AActor
{
	GENERATED_BODY()

public:
	AODTrafficManager();
	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type Reason) override;
	virtual void Tick(float DeltaSeconds) override;

	/** Agents live inside this radius of the player (cm). */
	UPROPERTY(EditAnywhere, Category = "Traffic")
	float SpawnRadiusCm = 80000.0f;

	/** Cars past this draw as the far tier (see FModel). */
	UPROPERTY(EditAnywhere, Category = "Traffic")
	float NearTierCm = 15000.0f;

	/** Agents past this are removed. */
	UPROPERTY(EditAnywhere, Category = "Traffic")
	float DespawnRadiusCm = 95000.0f;

	/** Top-up spawns never land closer to the player than this (pop-in). */
	UPROPERTY(EditAnywhere, Category = "Traffic")
	float PopInCm = 30000.0f;

	UPROPERTY(EditAnywhere, Category = "Traffic")
	int32 MaxAgents = 1500;

	/** Settings > Game > Traffic (GameUserSettings.ini [OpenDrive.Traffic]). */
	static bool IsEnabledSetting();
	static void SetEnabledSetting(bool bOn);

	int32 GetAgentCount() const { return Agents.Num(); }

private:
	struct FAgent
	{
		uint32 Id = 0;
		int32 Link = -1;
		int32 Next = -1;          // chosen successor link, -1 = none
		int32 Prev = -1;          // link it came from (pose looks back across the junction)
		float S = 0.0f;           // cm from the link start
		float V = 0.0f;           // cm/s
		float A = 0.0f;           // cm/s^2, last step
		float Lat = 0.0f;         // lateral offset (cm, + = right), eased
		float V0Factor = 1.0f;    // 0.9..1.1 of the limit
		float StopTime = 0.0f;    // seconds stood at a stop line
		uint32 Seed = 0;
		uint8 Lane = 0;           // 0 = kerb lane
		uint8 Model = 0;
		bool bCleared = false;    // may enter the junction at the link end
		FVector PrevPos = FVector::ZeroVector, CurPos = FVector::ZeroVector;
		FQuat PrevRot = FQuat::Identity, CurRot = FQuat::Identity;
		FVector2f Approach = FVector2f::ZeroVector;   // heading into the current junction cluster
		FTransform Shown;         // last rendered transform (ISM motion vectors)
		bool bShown = false;
	};

	// components below are owned (outer = this, registered), so plain pointers are GC-safe.
	// [0] near tier (Visual Style mesh, shadows) / [1] far tier past NearTierCm
	// (stylized low-poly mesh, no shadow / distance field / ray tracing): 450
	// shadow-casting 18k-tri non-Nanite _real cars out to 950 m cost ~70 ms
	// a frame on summit_county (2026-10-16 logs, fps fell with car count).
	struct FModel
	{
		UInstancedStaticMeshComponent* Body[2] = {};
		UInstancedStaticMeshComponent* Braking[2] = {};   // same body, brake lamps lit
		UMaterialInstanceDynamic* LampMID[2] = {};
		UMaterialInstanceDynamic* BrakeMID[2] = {};
		UStaticMesh* Chassis = nullptr;
		int32 Wheel[2] = { -1, -1 };   // index into WheelIsms
		float FrontX = 132.0f, RearX = -128.0f, HalfTrack = 84.0f, WheelR = 34.0f;
		FVector BoundsOrigin = FVector::ZeroVector;
		FVector HalfExtent = FVector(230.0f, 95.0f, 70.0f);
	};

	struct FWreck
	{
		UStaticMeshComponent* Comp = nullptr;
		double Born = 0.0;
	};

	struct FRangeLink
	{
		int32 Link;
		float EntryS;     // first / last in-range polyline station (cm)
		float ExitS;
		float InRangeCm;
	};

	void StartLoad(AODRoadManager* Road);
	void ClearAgents();
	void Step(float Dt);
	void RebuildOrder();
	void UpdateRange(bool bFill);
	void TopUp();
	void Render(float Alpha);
	void UpdateCollision(float Alpha);
	void UpdateAudio(float Alpha);
	void UpdateLights();
	void ProcessHits();
	bool SpawnAgent(int32 Link, float S, uint8 Lane, float V, uint32 Seed);
	int32 PickNext(FAgent& Ag);
	float TargetPerCm(int32 Link, float* OutSpeedCmS = nullptr) const;
	FVector PosAt(int32 Link, float S, int32 Next, int32 Prev = -1) const;
	void Pose(const FAgent& Ag, FVector& OutPos, FQuat& OutRot) const;
	float LaneLat(int32 Link, uint8 Lane) const;
	float Idm(float V, float V0, float Gap, float LeaderV) const;
	bool JunctionClear(int32 Cluster, int32 SelfLink) const;
	int32 SignalState(int32 Link) const;   // 0 green, 1 yellow, 2 red
	bool FindLeader(int32 Self, uint8 Lane, float& OutGap, float& OutV) const;
	void ClockHourDow(int32& OutHour, int32& OutDow) const;
	bool TrafficWanted();
	void BuildModels();

	UFUNCTION()
	void OnPlayerHit(UPrimitiveComponent* HitComp, AActor* OtherActor, UPrimitiveComponent* OtherComp,
		FVector NormalImpulse, const FHitResult& Hit);

	TSharedPtr<FODTrafficGraph, ESPMode::ThreadSafe> Graph;
	TWeakObjectPtr<AODRoadManager> GraphRoad;
	TWeakObjectPtr<class AODDayNightController> DayNight;
	bool bLoading = false;

	TArray<FAgent> Agents;
	// per step: agent indices sorted by (link, s); each link's slice of Order
	TArray<int32> Order;
	TArray<int32> OrderPos;
	TArray<int32> SliceStart, SliceNum, SliceStamp;   // per link, valid when SliceStamp == StepId
	int32 StepId = 0;

	TArray<FRangeLink> RangeLinks;
	FVector RangeCenter = FVector(1.0e12);
	FVector PlayerPos = FVector::ZeroVector;
	FVector PlayerVel = FVector::ZeroVector;
	uint32 TopUpSalt = 0;

	TArray<double> ClusterBusyUntil;   // junction cluster -> sim time a yielder's crossing ends

	double SimTime = 0.0;
	float Accum = 0.0f;
	double LastTopUp = 0.0;
	int32 HourBucket = 0;              // hour + 24 * day of week
	mutable TArray<float> TargetK, TargetV;   // per link: veh/cm, cm/s at TargetStamp's hour
	mutable TArray<int32> TargetStamp;
	uint32 SpawnCounter = 0;

	UPROPERTY()
	TArray<TObjectPtr<UInstancedStaticMeshComponent>> WheelIsms;
	TArray<FModel> Models;
	TArray<TArray<FTransform>> BodyXf, BrakeXf, WheelXf;
	TArray<TArray<FTransform>> BodyPrevXf, BrakePrevXf, WheelPrevXf;
	bool bNightLights = false;
	bool bLightsSet = false;

	UPROPERTY()
	TArray<TObjectPtr<UBoxComponent>> Boxes;
	TArray<int32> BoxAgent;        // agent Id per box, -1 = parked
	TArray<uint32> HitIds;
	TArray<FWreck> Wrecks;
	TWeakObjectPtr<UPrimitiveComponent> HookedPlayerBody;

	UPROPERTY()
	TArray<TObjectPtr<UODEngineSynth>> PassBy;
	UPROPERTY()
	TObjectPtr<UODEngineSynth> IdleBed;
	float AudioVolume = 1.0f;
	double LastVolumeRead = -100.0;

	bool bEnabled = true;          // -odtraffic=0 / settings / race
	bool bCmdLineOff = false;
	double LastWantCheck = -100.0;
	double LastDiag = 0.0;
	double LastRoadCheck = -100.0;
	float PeakMs = 0.0f;
};
