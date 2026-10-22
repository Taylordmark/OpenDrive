#include "ODBoundaryEffect.h"

#include "Camera/PlayerCameraManager.h"
#include "Components/DynamicMeshComponent.h"
#include "Components/PostProcessComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/MeshNormals.h"
#include "Engine/World.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInterface.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "ODBakeLink.h"

using namespace UE::Geometry;

namespace
{
	constexpr double PostSpacingCm = 5000.0;   // a ground trace every 50 m
	constexpr double BelowCm = 5000.0;         // curtain foot under the ground (hides trace misses)
	constexpr double AboveCm = 15000.0;        // 150 m tall
	constexpr double FadeBandCm = 15000.0;     // post-process ramps over the last 150 m inside
}

AODBoundaryEffect::AODBoundaryEffect()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
	Fade = CreateDefaultSubobject<UPostProcessComponent>(TEXT("Fade"));
	Fade->SetupAttachment(RootComponent);
	Fade->bUnbound = true;
	Fade->BlendWeight = 0.0f;
	FPostProcessSettings& S = Fade->Settings;
	S.bOverride_ColorSaturation = true;
	S.ColorSaturation = FVector4(0.3, 0.3, 0.35, 1.0);   // cool, washed out: "not synced"
	S.bOverride_SceneFringeIntensity = true;
	S.SceneFringeIntensity = 3.0f;
	S.bOverride_VignetteIntensity = true;
	S.VignetteIntensity = 0.9f;
}

void AODBoundaryEffect::BeginPlay()
{
	Super::BeginPlay();
	const TSharedPtr<const TArray<FVector2D>, ESPMode::ThreadSafe> Outline = ODBakeLink::ActiveOutline();
	if (!Outline.IsValid())
	{
		return;
	}
	for (int32 i = 0; i < Outline->Num(); ++i)
	{
		const FVector2D A = (*Outline)[i];
		const FVector2D B = (*Outline)[(i + 1) % Outline->Num()];
		const int32 Steps = FMath::Max(1, FMath::CeilToInt(FVector2D::Distance(A, B) / PostSpacingCm));
		for (int32 k = 0; k < Steps; ++k)
		{
			Posts.Add(FMath::Lerp(A, B, (double)k / Steps));
		}
	}
	PostZ.Init(NAN, Posts.Num());
	NumUnresolved = Posts.Num();

	// (2026-10-21) pass 1's DEM heights along the outline: 'ODC1' + uint32 N +
	// N * (float X, Y, Z cm), so far posts stand on the ground before any trace
	// ponytail: nearest point by brute force, once (~2.4k posts x ~4.8k points at 30 km)
	BakedZ.Init(NAN, Posts.Num());
	TArray<uint8> Bin;
	const FString CurtainPath = FPaths::ProjectContentDir() / FString::Printf(
		TEXT("OpenDrive/Masks/curtain_%s_test.bin"), *ODBakeLink::ActiveKey());
	if (FFileHelper::LoadFileToArray(Bin, *CurtainPath, FILEREAD_Silent) && Bin.Num() >= 8
		&& FMemory::Memcmp(Bin.GetData(), "ODC1", 4) == 0)
	{
		uint32 N = 0;
		FMemory::Memcpy(&N, Bin.GetData() + 4, 4);
		if (Bin.Num() >= 8 + (int64)N * 12)
		{
			const float* P = reinterpret_cast<const float*>(Bin.GetData() + 8);
			for (int32 i = 0; i < Posts.Num(); ++i)
			{
				double Best = TNumericLimits<double>::Max();
				for (uint32 k = 0; k < N; ++k)
				{
					const double D2 = FVector2D::DistSquared(Posts[i], FVector2D(P[k * 3], P[k * 3 + 1]));
					if (D2 < Best)
					{
						Best = D2;
						BakedZ[i] = P[k * 3 + 2];
					}
				}
			}
		}
	}

	Curtain = NewObject<UDynamicMeshComponent>(this);
	Curtain->SetupAttachment(RootComponent);
	Curtain->SetCollisionProfileName(TEXT("NoCollision"));
	Curtain->SetCastShadow(false);
	Curtain->RegisterComponent();
	if (UMaterialInterface* M = LoadObject<UMaterialInterface>(nullptr, TEXT("/Game/OpenDrive/Materials/M_ODCurtain.M_ODCurtain")))
	{
		Curtain->SetMaterial(0, M);
	}
	UE_LOG(LogTemp, Display, TEXT("ODBoundaryEffect: %d curtain posts on a %d-point outline"), Posts.Num(), Outline->Num());
	RebuildCurtain();
}

void AODBoundaryEffect::RebuildCurtain()
{
	if (!Curtain || Posts.Num() < 3)
	{
		return;
	}
	// unresolved posts stand on their baked DEM height, else take the nearest
	// resolved neighbour's Z (else the car's)
	TArray<double> Z;
	Z.SetNumUninitialized(Posts.Num());
	const APawn* Pawn = UGameplayStatics::GetPlayerPawn(this, 0);
	const double Fallback = Pawn ? Pawn->GetActorLocation().Z : 0.0;
	for (int32 i = 0; i < Posts.Num(); ++i)
	{
		Z[i] = Fallback;
		if (FMath::IsNaN(PostZ[i]) && !FMath::IsNaN(BakedZ[i]))
		{
			Z[i] = BakedZ[i];
			continue;
		}
		for (int32 d = 0; d < Posts.Num() / 2 + 1; ++d)
		{
			const float L = PostZ[(i - d + Posts.Num()) % Posts.Num()], R = PostZ[(i + d) % Posts.Num()];
			if (!FMath::IsNaN(L) || !FMath::IsNaN(R))
			{
				Z[i] = FMath::IsNaN(L) ? R : L;
				break;
			}
		}
	}
	Curtain->EditMesh([&](FDynamicMesh3& Mesh)
	{
		Mesh.Clear();
		Mesh.EnableAttributes();
		FDynamicMeshUVOverlay* UV = Mesh.Attributes()->PrimaryUV();
		double U = 0.0;
		TArray<int32> V, T;
		for (int32 i = 0; i <= Posts.Num(); ++i)
		{
			const int32 p = i % Posts.Num();
			if (i > 0)
			{
				U += FVector2D::Distance(Posts[i - 1], Posts[p]) / 10000.0;   // 1 UV per 100 m
			}
			V.Add(Mesh.AppendVertex(FVector3d(Posts[p].X, Posts[p].Y, Z[p] - BelowCm)));
			V.Add(Mesh.AppendVertex(FVector3d(Posts[p].X, Posts[p].Y, Z[p] + AboveCm)));
			T.Add(UV->AppendElement(FVector2f((float)U, 0.0f)));
			T.Add(UV->AppendElement(FVector2f((float)U, 1.0f)));
		}
		for (int32 i = 0; i < Posts.Num(); ++i)
		{
			const int32 a = 2 * i, b = a + 1, c = a + 2, d = a + 3;
			for (const FIndex3i& Tri : { FIndex3i(a, c, b), FIndex3i(b, c, d) })
			{
				const int32 Tid = Mesh.AppendTriangle(V[Tri.A], V[Tri.B], V[Tri.C]);
				if (Tid >= 0)
				{
					UV->SetTriangle(Tid, FIndex3i(T[Tri.A], T[Tri.B], T[Tri.C]));
				}
			}
		}
		FMeshNormals::InitializeOverlayToPerVertexNormals(Mesh.Attributes()->PrimaryNormals(), false);
	});
}

void AODBoundaryEffect::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	const TSharedPtr<const TArray<FVector2D>, ESPMode::ThreadSafe> Outline = ODBakeLink::ActiveOutline();
	if (!Outline.IsValid())
	{
		return;
	}

	// post-process weight from the camera's distance to the outline (visual,
	// so per-frame is fine - the physics side is on the fixed step)
	if (const APlayerCameraManager* Cam = UGameplayStatics::GetPlayerCameraManager(this, 0))
	{
		const double D = ODBakeLink::SignedDistanceCm(*Outline, FVector2D(Cam->GetCameraLocation()));
		Fade->BlendWeight = (float)FMath::Clamp((FadeBandCm - D) / FadeBandCm, 0.0, 1.0);
	}

	// ground-trace unresolved posts as collision streams in (only near tiles
	// carry collision, so posts resolve as the car approaches them)
	if (NumUnresolved == 0 || GetWorld()->GetTimeSeconds() < NextRetrace)
	{
		return;
	}
	NextRetrace = GetWorld()->GetTimeSeconds() + 2.0;
	int32 NewlyResolved = 0;
	FCollisionQueryParams Params;
	if (const APawn* Pawn = UGameplayStatics::GetPlayerPawn(this, 0))
	{
		Params.AddIgnoredActor(Pawn);
	}
	for (int32 i = 0; i < Posts.Num(); ++i)
	{
		FHitResult Hit;
		if (FMath::IsNaN(PostZ[i]) && GetWorld()->LineTraceSingleByChannel(Hit,
			FVector(Posts[i].X, Posts[i].Y, 1000000.0), FVector(Posts[i].X, Posts[i].Y, -1000000.0), ECC_Visibility, Params))
		{
			PostZ[i] = (float)Hit.ImpactPoint.Z;
			--NumUnresolved;
			++NewlyResolved;
		}
	}
	if (NewlyResolved > 0)
	{
		RebuildCurtain();
	}
}
