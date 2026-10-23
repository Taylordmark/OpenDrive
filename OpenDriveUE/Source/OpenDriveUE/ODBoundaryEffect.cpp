#include "ODBoundaryEffect.h"

#include "Algo/BinarySearch.h"
#include "Components/DynamicMeshComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/MeshNormals.h"
#include "Engine/World.h"
#include "GameFramework/Pawn.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/MaterialInterface.h"
#include "ODBakeLink.h"

using namespace UE::Geometry;

namespace
{
	constexpr double TileCm = 90.0;            // triangle edge (before jitter)
	constexpr double RowCm = TileCm * 0.866;
	constexpr int32 Rows = 10;                 // ~7.8 m tall
	constexpr double FootCm = 60.0;            // bottom row starts in the ground
	constexpr double RadiusCm = 2600.0;        // built this far from the car (M_ODCurtain fades it out by 22 m)
	constexpr double RebuildMoveCm = 400.0;
	constexpr double Overlap = 1.12;           // tiles scaled past their lattice cell: never a gap

	// deterministic 0..1 per lattice vertex/tile, so a rebuild reproduces the same wall
	double Hash(int64 I, int64 J, int32 K)
	{
		return FMath::Frac(FMath::Abs(FMath::Sin(I * 12.9898 + J * 78.233 + K * 37.719) * 43758.5453));
	}
}

AODBoundaryEffect::AODBoundaryEffect()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void AODBoundaryEffect::BeginPlay()
{
	Super::BeginPlay();
	const TSharedPtr<const TArray<FVector2D>, ESPMode::ThreadSafe> Outline = ODBakeLink::ActiveOutline();
	if (!Outline.IsValid() || Outline->Num() < 3)
	{
		return;
	}
	Pts = *Outline;
	double Area2 = 0.0;
	Cum.Add(0.0);
	for (int32 i = 0; i < Pts.Num(); ++i)
	{
		const FVector2D A = Pts[i], B = Pts[(i + 1) % Pts.Num()];
		Cum.Add(Cum.Last() + FVector2D::Distance(A, B));
		Area2 += FVector2D::CrossProduct(A, B);
	}
	// positive shoelace area: the inside is left of travel
	for (int32 i = 0; i < Pts.Num(); ++i)
	{
		const FVector2D D = (Pts[(i + 1) % Pts.Num()] - Pts[i]).GetSafeNormal();
		Inward.Add(Area2 > 0.0 ? FVector2D(-D.Y, D.X) : FVector2D(D.Y, -D.X));
	}

	Wall = NewObject<UDynamicMeshComponent>(this);
	Wall->SetupAttachment(RootComponent);
	Wall->SetCollisionProfileName(TEXT("NoCollision"));
	Wall->SetCastShadow(false);
	Wall->SetAbsolute(true, true, true);
	Wall->RegisterComponent();
	if (UMaterialInterface* M = LoadObject<UMaterialInterface>(nullptr, TEXT("/Game/OpenDrive/Materials/M_ODCurtain.M_ODCurtain")))
	{
		Mat = UMaterialInstanceDynamic::Create(M, this);
		Wall->SetMaterial(0, Mat);
	}
	UE_LOG(LogTemp, Display, TEXT("ODBoundaryEffect: glass wall on a %d-point, %.1f km outline"), Pts.Num(), Cum.Last() / 100000.0);
}

FVector2D AODBoundaryEffect::PosAt(double S, FVector2D* InwardOut) const
{
	const double Perim = Cum.Last();
	S = FMath::Fmod(S, Perim);
	if (S < 0.0)
	{
		S += Perim;
	}
	const int32 i = FMath::Clamp(Algo::UpperBound(Cum, S) - 1, 0, Pts.Num() - 1);
	const double L = FMath::Max(Cum[i + 1] - Cum[i], 1.0);
	if (InwardOut)
	{
		*InwardOut = Inward[i];
	}
	return FMath::Lerp(Pts[i], Pts[(i + 1) % Pts.Num()], (S - Cum[i]) / L);
}

double AODBoundaryEffect::GroundAt(double S, double FallbackZ)
{
	// ground traced once per lattice column, lerped between; a miss (no
	// collision streamed there yet) falls back without caching
	// ponytail: Visibility channel also hits trees/buildings on the line; a terrain-only channel if that shows
	auto Sample = [&](int64 K) -> double
	{
		if (const float* Z = GroundZ.Find(K))
		{
			return *Z;
		}
		const FVector2D P = PosAt(K * TileCm);
		FCollisionQueryParams Params;
		if (const APawn* Pawn = UGameplayStatics::GetPlayerPawn(this, 0))
		{
			Params.AddIgnoredActor(Pawn);
		}
		FHitResult Hit;
		if (GetWorld()->LineTraceSingleByChannel(Hit, FVector(P, 1000000.0), FVector(P, -1000000.0), ECC_Visibility, Params))
		{
			return GroundZ.Add(K, (float)Hit.ImpactPoint.Z);
		}
		return FallbackZ;
	};
	const double F = S / TileCm;
	const int64 K = FMath::FloorToInt64(F);
	return FMath::Lerp(Sample(K), Sample(K + 1), F - K);
}

void AODBoundaryEffect::Rebuild(const FVector& Car)
{
	BuiltAt = FVector2D(Car);
	const FVector2D C2(Car);

	// lattice columns near the car: every outline segment's stretch within RadiusCm
	TSet<int64> Cols;
	for (int32 i = 0; i < Pts.Num(); ++i)
	{
		const FVector2D A = Pts[i], AB = Pts[(i + 1) % Pts.Num()] - A;
		const double L = Cum[i + 1] - Cum[i];
		const double T = FMath::Clamp(FVector2D::DotProduct(C2 - A, AB) / FMath::Max(AB.SizeSquared(), 1.0), 0.0, 1.0);
		const double D2 = FVector2D::DistSquared(C2, A + AB * T);
		if (D2 > RadiusCm * RadiusCm)
		{
			continue;
		}
		const double Half = FMath::Sqrt(RadiusCm * RadiusCm - D2);
		const double S0 = Cum[i] + FMath::Clamp(T * L - Half, 0.0, L), S1 = Cum[i] + FMath::Clamp(T * L + Half, 0.0, L);
		// rows lean by half a tile each (skewed lattice), so start Rows/2 columns early
		for (int64 c = FMath::FloorToInt64(S0 / TileCm) - Rows / 2 - 2; c <= FMath::CeilToInt64(S1 / TileCm) + 2; ++c)
		{
			Cols.Add(c);
		}
	}

	// ponytail: lattice index runs on past the perimeter, so the wall has one jittered seam at the outline's start
	struct FV { FVector P; FVector2D N; double H; double S; };
	auto Vert = [&](int64 I, int64 J) -> FV
	{
		const double Jz = (Hash(I, J, 1) - 0.5) * TileCm * 0.8, Jy = (Hash(I, J, 2) - 0.5) * RowCm * 0.8;
		FV V;
		V.H = J * RowCm - FootCm + (J > 0 ? Jy : -FMath::Abs(Jy));
		V.S = (I + J * 0.5) * TileCm + Jz;
		const FVector2D XY = PosAt(V.S, &V.N);
		V.P = FVector(XY, GroundAt(V.S, Car.Z - 60.0) + V.H);
		return V;
	};

	Wall->SetWorldLocation(Car);
	Wall->EditMesh([&](FDynamicMesh3& Mesh)
	{
		Mesh.Clear();
		Mesh.EnableAttributes();
		Mesh.Attributes()->SetNumUVLayers(2);
		Mesh.Attributes()->EnablePrimaryColors();
		FDynamicMeshUVOverlay* Bary = Mesh.Attributes()->GetUVLayer(0);
		FDynamicMeshUVOverlay* Info = Mesh.Attributes()->GetUVLayer(1);
		FDynamicMeshColorOverlay* Col = Mesh.Attributes()->PrimaryColors();
		for (const int64 I : Cols)
		{
			for (int64 J = 0; J < Rows; ++J)
			{
				for (int32 Up = 0; Up < 2; ++Up)
				{
					const FV V[3] = {Up == 0 ? Vert(I, J) : Vert(I + 1, J), Up == 0 ? Vert(I + 1, J) : Vert(I + 1, J + 1), Vert(I, J + 1)};
					const FVector Cen = (V[0].P + V[1].P + V[2].P) / 3.0;
					if (FVector2D::DistSquared(FVector2D(Cen), C2) > RadiusCm * RadiusCm)
					{
						continue;
					}
					const FVector N = FVector((V[0].N + V[1].N + V[2].N).GetSafeNormal(), 0.0);
					const double Rise = FMath::Clamp(((V[0].H + V[1].H + V[2].H) / 3.0 + 30.0) / 250.0, 0.0, 1.0);
					// tip: off-centre, a random reach; the shader breathes it +-Amp/2 about this
					const double Amp = (0.5 + 0.9 * Hash(I, J, Up + 9)) * TileCm * 0.5 * Rise;
					const FVector Apex = Cen + (V[0].P - Cen) * (Hash(I, J, Up + 5) - 0.5) * 0.6 + N * (0.1 * TileCm * Rise + 0.5 * Amp);
					const double Phase = FMath::Frac(-(V[0].S + V[1].S + V[2].S) / 9000.0 + Hash(I, J, Up) * 0.5);
					const FVector4f Base(N.X * 0.5f + 0.5f, N.Y * 0.5f + 0.5f, 0.5f, 0.0f);
					const FVector4f Tip(Base.X, Base.Y, Base.Z, (float)(Amp / 100.0));
					for (int32 k = 0; k < 3; ++k)
					{
						const FVector A = Cen + (V[k].P - Cen) * Overlap - Car;
						const FVector B = Cen + (V[(k + 1) % 3].P - Cen) * Overlap - Car;
						const int32 Ia = Mesh.AppendVertex(A), Ib = Mesh.AppendVertex(B), Ic = Mesh.AppendVertex(Apex - Car);
						const int32 Tid = Mesh.AppendTriangle(Ia, Ib, Ic);
						if (Tid < 0)
						{
							continue;
						}
						// UV0: barycentric (edges glow), UV1: (rise out of the ground, breathing phase)
						Bary->SetTriangle(Tid, FIndex3i(Bary->AppendElement(FVector2f(1, 0)), Bary->AppendElement(FVector2f(0, 1)), Bary->AppendElement(FVector2f(0, 0))));
						const int32 E = Info->AppendElement(FVector2f((float)Rise, (float)Phase));
						Info->SetTriangle(Tid, FIndex3i(E, E, E));
						const int32 Cb = Col->AppendElement(Base);
						Col->SetTriangle(Tid, FIndex3i(Cb, Cb, Col->AppendElement(Tip)));
					}
				}
			}
		}
		FMeshNormals::InitializeOverlayToPerTriangleNormals(Mesh.Attributes()->PrimaryNormals());
	});
}

void AODBoundaryEffect::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	const APawn* Pawn = UGameplayStatics::GetPlayerPawn(this, 0);
	if (!Wall || !Mat || !Pawn)
	{
		return;
	}
	const FVector Car = Pawn->GetActorLocation();
	// breathing only when slow (the trees' 10..12 mph band), eased over ~0.4 s
	// independent of frame rate; parked/creeping = 1, at speed = a still wall
	const float Mph = Pawn->GetVelocity().Size() * 0.0223694f;
	const float Target = FMath::Clamp((12.0f - Mph) / 2.0f, 0.0f, 1.0f);
	Breath += (Target - Breath) * (1.0f - FMath::Exp(-DeltaSeconds / 0.4f));
	if (FVector2D::DistSquared(FVector2D(Car), BuiltAt) > RebuildMoveCm * RebuildMoveCm)
	{
		Rebuild(Car);
	}
	// the car in the wall's local space (the mesh sits at the last rebuild's
	// car position, unrotated): small numbers, no LWC in the material
	const FVector Local = Car - Wall->GetComponentLocation();
	Mat->SetScalarParameterValue(TEXT("Breath"), Breath);
	Mat->SetVectorParameterValue(TEXT("Center"), FLinearColor(Local.X, Local.Y, Local.Z));
}
