#include "ODMenuGlobe.h"
#include "Camera/CameraComponent.h"
#include "Components/DynamicMeshComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/DynamicMeshOverlay.h"
#include "Engine/Texture2D.h"
#include "GameFramework/PlayerController.h"
#include "ImageUtils.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/MaterialInterface.h"
#include "Math/RandomStream.h"
#include "Misc/Paths.h"

using namespace UE::Geometry;

namespace
{
// ---- tunables (2026-10-14): the globe.gl page's look, in engine units ----
// 1 km globe (2026-10-14, was 100 m): the drape fly-in frames ~1 km areas from
// ~2e-4 radii, which must stay past the 10 cm near plane
constexpr double GlobeRadiusCm = 100000.0;    // altitudes below are in radii of this
constexpr double EarthRadiusKm = 6371.0;
constexpr int32 GlobeLngSegs = 192, GlobeLatSegs = 96;
constexpr double GlobeAtmoAltitude = 0.02;    // thin rim shell (2026-10-14: the 0.15 globe.gl halo looked bad)
constexpr float GlobeRimStrength = 0.12f;     // rim opacity gain; <= 0 hides the shell
constexpr double GlobeBackdropR = 5.0;        // black shell + stars (radii): past the farthest camera (1 + max alt), well short of the showroom
constexpr int32 GlobeStarCount = 3000, GlobeStarSeed = 1014;
constexpr float GlobeStarPx = 2.0f;           // star diamond diagonal at 1080p
constexpr double GlobeLineLift = 0.001;       // lines ride this far above the surface (radii) - above the facets' sag
constexpr double GlobeLinesAltOff = 0.003, GlobeLinesAltOn = 0.02;   // lines fade out below (their lift reads as parallax)
constexpr int32 GlobeDrapeSegs = 32;
constexpr double GlobeDrapeLift = 2e-6;       // drape just above the true sphere (its facets sag below it)
constexpr double GlobeDrapeAltOff = 0.5, GlobeDrapeAltOn = 0.08;    // drape invisible above / full below
constexpr float GlobeDrapeFadeSpeed = 4.0f;   // FInterpTo speed (1/s)
constexpr double GlobeFlyBumpPerRad = 1.2;    // fly arc peak altitude (radii) per radian travelled
constexpr float GlobeVFovDeg = 50.0f;         // three.js camera (vertical) fov
constexpr float GlobeHFovDeg = 79.3f;         // the same at 16:9 - UCameraComponent's FieldOfView is horizontal
constexpr float GlobeLinePx = 1.2f;           // ribbon width at 1080p (globe.gl draws 1 px GL lines)
constexpr double GlobeMinAlt = 0.00015, GlobeMaxAlt = 3.0;   // min: 15 cm clearance at 1 km radius
constexpr double GlobeMaxLat = 85.0;          // camera stays off the poles (look-at basis)
constexpr double GlobeOrbitDegPerPx = 0.12;   // x altitude: slower spin when close, like OrbitControls
constexpr double GlobeZoomPerNotch = 0.85;
constexpr float GlobeBrightness = 1.0f;       // M_Globe's emissive gain
const FColor GlobeAtmoColor(0x88, 0x94, 0xa8);
const FColor GlobeCoastColor(255, 216, 137), GlobeBorderColor(190, 199, 209);
constexpr float GlobeCoastAlpha = 0.35f, GlobeBorderAlpha = 0.55f, GlobeStateAlpha = 0.22f;

/** Unit direction of (Lat, Lng), actor-relative: +X = (0, 0), +Z = north,
 *  east toward -Y so it is on the right seen from outside (UE is left-handed). */
FVector GlobeDir(double Lat, double Lng)
{
	const double La = FMath::DegreesToRadians(Lat), Ln = FMath::DegreesToRadians(Lng);
	return FVector(FMath::Cos(La) * FMath::Cos(Ln), -FMath::Cos(La) * FMath::Sin(Ln), FMath::Sin(La));
}

/** Grid on a sphere of Radius: (u, v) in [0, 1] -> LatLngAt(u, v) = (lat, lng),
 *  UV = (u, v), normals radial. bInward faces it inside out (the atmosphere
 *  shell: only its far half draws, around the globe). */
void BuildGrid(FDynamicMesh3& Mesh, double Radius, bool bInward, int32 USegs, int32 VSegs,
	TFunctionRef<FVector2D(double, double)> LatLngAt)
{
	Mesh.Clear();
	Mesh.EnableAttributes();
	FDynamicMeshUVOverlay* UV = Mesh.Attributes()->PrimaryUV();
	FDynamicMeshNormalOverlay* Nrm = Mesh.Attributes()->PrimaryNormals();
	const int32 Cols = USegs + 1;   // the sphere's seam column is duplicated for the UV wrap
	for (int32 r = 0; r <= VSegs; ++r)
	{
		for (int32 c = 0; c < Cols; ++c)
		{
			const double U = double(c) / USegs, V = double(r) / VSegs;
			const FVector2D LL = LatLngAt(U, V);
			const FVector D = GlobeDir(LL.X, LL.Y);
			Mesh.AppendVertex(FVector3d(D * Radius));   // vertex id == UV id == normal id
			UV->AppendElement(FVector2f(float(U), float(V)));
			Nrm->AppendElement(FVector3f(bInward ? -D : D));
		}
	}
	auto Tri = [&](int32 A, int32 B, int32 C)
	{
		// front face = VectorUtil::Normal(A, B, C) = (C-A) x (B-A); flip to point out (or in)
		const FVector3d PA = Mesh.GetVertex(A), PB = Mesh.GetVertex(B), PC = Mesh.GetVertex(C);
		if (PA.Equals(PB, 1e-3) || PB.Equals(PC, 1e-3) || PA.Equals(PC, 1e-3))
		{
			return;   // zero-area (the sphere's pole rows)
		}
		if (((PC - PA).Cross(PB - PA)).Dot(PA + PB + PC) * (bInward ? -1.0 : 1.0) < 0.0)
		{
			Swap(B, C);
		}
		const int32 T = Mesh.AppendTriangle(A, B, C);
		if (T >= 0)
		{
			UV->SetTriangle(T, FIndex3i(A, B, C));
			Nrm->SetTriangle(T, FIndex3i(A, B, C));
		}
	};
	for (int32 r = 0; r < VSegs; ++r)
	{
		for (int32 c = 0; c < USegs; ++c)
		{
			const int32 A = r * Cols + c, B = A + 1, C = A + Cols, D = C + 1;
			Tri(A, C, D);
			Tri(A, D, B);
		}
	}
}

/** Equirectangular UV sphere (u = lng, v = down from the north pole, so globe-dark.jpg maps as-is). */
void BuildSphere(FDynamicMesh3& Mesh, double Radius, bool bInward)
{
	BuildGrid(Mesh, Radius, bInward, GlobeLngSegs, GlobeLatSegs,
		[](double U, double V) { return FVector2D(90.0 - 180.0 * V, -180.0 + 360.0 * U); });
}

/** M_GlobeLine's WPO pushes each vertex along its normal by Width x camera
 *  distance; this Width is Px pixels at 1080p. */
float GlobeLineWidth(float Px)
{
	return Px / 1080.0f * 2.0f * FMath::Tan(FMath::DegreesToRadians(GlobeVFovDeg * 0.5f));
}

UMaterialInstanceDynamic* GlobeMID(UObject* Outer, const TCHAR* Path)
{
	UMaterialInterface* M = LoadObject<UMaterialInterface>(nullptr, Path);
	if (!M)
	{
		UE_LOG(LogTemp, Warning, TEXT("ODMenuGlobe: %s missing - run unreal/apply_globe_material.py"), Path);
		return nullptr;
	}
	return UMaterialInstanceDynamic::Create(M, Outer);
}

UTexture2D* GlobeTexture(const TCHAR* File, bool bSRGB)
{
	// WorldMap is staged UFS (DefaultGame.ini): a plain file path, read through the pak in packaged builds
	UTexture2D* T = FImageUtils::ImportFileAsTexture2D(FPaths::ProjectContentDir() / TEXT("OpenDrive/WorldMap") / File);
	if (!T)
	{
		UE_LOG(LogTemp, Warning, TEXT("ODMenuGlobe: WorldMap/%s missing or unreadable"), File);
		return nullptr;
	}
	T->SRGB = bSRGB;
	T->UpdateResource();
	return T;
}
}

AODMenuGlobe::AODMenuGlobe()
{
	PrimaryActorTick.bCanEverTick = true;
	PrimaryActorTick.bTickEvenWhenPaused = true;   // the menu pauses the game

	Root = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
	RootComponent = Root;
	auto Mesh = [this](const TCHAR* Name)
	{
		UDynamicMeshComponent* C = CreateDefaultSubobject<UDynamicMeshComponent>(Name);
		C->SetupAttachment(Root);
		C->SetCollisionProfileName(TEXT("NoCollision"));
		C->SetCastShadow(false);
		return C;
	};
	Sphere = Mesh(TEXT("Sphere"));
	Atmosphere = Mesh(TEXT("Atmosphere"));
	Lines = Mesh(TEXT("Lines"));
	Backdrop = Mesh(TEXT("Backdrop"));   // black inward shell (2026-10-14): hides the level's sky / fog behind the globe
	Stars = Mesh(TEXT("Stars"));
	Drape = Mesh(TEXT("Drape"));

	Camera = CreateDefaultSubobject<UCameraComponent>(TEXT("Camera"));
	Camera->SetupAttachment(Root);
	Camera->SetFieldOfView(GlobeHFovDeg);
	// fixed exposure: the globe is all emissive, eye adaptation would pump as it turns
	FPostProcessSettings& PP = Camera->PostProcessSettings;
	PP.bOverride_AutoExposureMethod = true;
	PP.AutoExposureMethod = AEM_Manual;
	PP.bOverride_AutoExposureApplyPhysicalCameraExposure = true;
	PP.AutoExposureApplyPhysicalCameraExposure = false;
	PP.bOverride_AutoExposureBias = true;
	PP.AutoExposureBias = 0.0f;
}

void AODMenuGlobe::BeginPlay()
{
	Super::BeginPlay();

	for (UDynamicMeshComponent* C : {Sphere.Get(), Atmosphere.Get(), Lines.Get(), Backdrop.Get(), Stars.Get(), Drape.Get()})
	{
		C->SetTangentsType(EDynamicMeshComponentTangentsMode::NoTangents);
	}
	Drape->SetVisibility(false);
	Sphere->EditMesh([](FDynamicMesh3& M) { BuildSphere(M, GlobeRadiusCm, false); });
	Atmosphere->EditMesh([](FDynamicMesh3& M) { BuildSphere(M, GlobeRadiusCm * (1.0 + GlobeAtmoAltitude), true); });

	if (UMaterialInstanceDynamic* M = GlobeMID(this, TEXT("/Game/OpenDrive/Materials/M_Globe.M_Globe")))
	{
		if (UTexture2D* T = GlobeTexture(TEXT("globe-dark.jpg"), true))
		{
			M->SetTextureParameterValue(TEXT("Albedo"), T);
		}
		// sRGB like M_Globe's Color samplers expect (as RoadDistTex): the emboss only wants relative slope
		if (UTexture2D* T = GlobeTexture(TEXT("globe-bump.jpg"), true))
		{
			M->SetTextureParameterValue(TEXT("Bump"), T);
			M->SetScalarParameterValue(TEXT("BumpTexel"), 1.0f / T->GetSizeX());
		}
		M->SetScalarParameterValue(TEXT("Brightness"), GlobeBrightness);
		Sphere->SetMaterial(0, M);
	}
	Backdrop->EditMesh([](FDynamicMesh3& M) { BuildSphere(M, GlobeRadiusCm * GlobeBackdropR, true); });
	if (UMaterialInstanceDynamic* M = GlobeMID(this, TEXT("/Game/OpenDrive/Materials/M_Globe.M_Globe")))
	{
		M->SetScalarParameterValue(TEXT("Brightness"), 0.0f);   // unlit black
		Backdrop->SetMaterial(0, M);
	}
	UMaterialInstanceDynamic* Atmo = GlobeRimStrength > 0.0f
		? GlobeMID(this, TEXT("/Game/OpenDrive/Materials/M_GlobeAtmo.M_GlobeAtmo")) : nullptr;
	if (UMaterialInstanceDynamic* M = Atmo)
	{
		M->SetVectorParameterValue(TEXT("Color"), FLinearColor::FromSRGBColor(GlobeAtmoColor));
		M->SetScalarParameterValue(TEXT("Strength"), GlobeRimStrength);
		// view dot at the globe's limb, seen through the shell: glow is full there, 0 at the shell's edge
		M->SetScalarParameterValue(TEXT("Edge"), float(FMath::Sqrt(1.0 - FMath::Square(1.0 / (1.0 + GlobeAtmoAltitude)))));
		Atmosphere->SetMaterial(0, M);
	}
	else
	{
		Atmosphere->SetVisibility(false);   // off, or the default material would be a grey ring
	}
	LinesMID = GlobeMID(this, TEXT("/Game/OpenDrive/Materials/M_GlobeLine.M_GlobeLine"));
	if (LinesMID)
	{
		LinesMID->SetScalarParameterValue(TEXT("Width"), 0.5f * GlobeLineWidth(GlobeLinePx));   // each edge goes half the line
		Lines->SetMaterial(0, LinesMID);
	}
	Lines->SetVertexColorSpaceTransformMode(EDynamicMeshVertexColorTransformMode::NoTransform);   // linear in, see ODTerrainManager

	// Star field (2026-10-14): one zero-size diamond per star just inside the
	// backdrop, its 4 corners' normals the tangent axes, opened by M_GlobeLine's
	// WPO to GlobeStarPx. Mostly faint white, a few blue / warm. Fixed seed.
	Stars->EditMesh([](FDynamicMesh3& Mesh)
	{
		Mesh.Clear();
		Mesh.EnableAttributes();
		Mesh.Attributes()->EnablePrimaryColors();
		FDynamicMeshColorOverlay* Col = Mesh.Attributes()->PrimaryColors();
		FDynamicMeshNormalOverlay* Nrm = Mesh.Attributes()->PrimaryNormals();
		FRandomStream Rng(GlobeStarSeed);
		for (int32 i = 0; i < GlobeStarCount; ++i)
		{
			const FVector D = Rng.GetUnitVector();
			FVector T1, T2;
			D.FindBestAxisVectors(T1, T2);
			const float Tint = Rng.GetFraction();
			const FLinearColor C = Tint < 0.08f ? FLinearColor(0.7f, 0.8f, 1.0f)
				: Tint < 0.16f ? FLinearColor(1.0f, 0.8f, 0.6f) : FLinearColor::White;
			const FVector4f Rgba(C.R, C.G, C.B, 0.15f + 0.85f * FMath::Pow(Rng.GetFraction(), 4.0f));   // mostly faint
			int32 V[4];
			for (int32 k = 0; k < 4; ++k)
			{
				V[k] = Mesh.AppendVertex(FVector3d(D * GlobeRadiusCm * GlobeBackdropR * 0.98));   // id == color id == normal id
				Col->AppendElement(Rgba);
				Nrm->AppendElement(FVector3f(k == 0 ? T1 : k == 1 ? T2 : k == 2 ? -T1 : -T2));
			}
			for (const FIndex3i& T : {FIndex3i(V[0], V[1], V[2]), FIndex3i(V[0], V[2], V[3])})
			{
				const int32 Tid = Mesh.AppendTriangle(T);   // M_GlobeLine is two-sided
				if (Tid >= 0)
				{
					Col->SetTriangle(Tid, T);
					Nrm->SetTriangle(Tid, T);
				}
			}
		}
	});
	if (UMaterialInstanceDynamic* M = GlobeMID(this, TEXT("/Game/OpenDrive/Materials/M_GlobeLine.M_GlobeLine")))
	{
		M->SetScalarParameterValue(TEXT("Width"), 0.5f * GlobeLineWidth(GlobeStarPx));   // corner = half the diagonal
		Stars->SetMaterial(0, M);
	}
	Stars->SetVertexColorSpaceTransformMode(EDynamicMeshVertexColorTransformMode::NoTransform);
	DrapeMID = GlobeMID(this, TEXT("/Game/OpenDrive/Materials/M_GlobeDrape.M_GlobeDrape"));
	if (DrapeMID)
	{
		Drape->SetMaterial(0, DrapeMID);
	}
	UpdateCamera();
}

void AODMenuGlobe::SetLines(const TArray<TArray<float>>& Coast, const TArray<TArray<float>>& Borders,
	const TArray<TArray<float>>& States)
{
	// One mesh for every polyline (~25k points): per segment a zero-width quad on
	// the surface whose two edges carry opposite side normals; M_GlobeLine's WPO
	// opens it to a constant screen width. Colors are linear (NoTransform).
	Lines->EditMesh([&](FDynamicMesh3& Mesh)
	{
		Mesh.Clear();
		Mesh.EnableAttributes();
		Mesh.Attributes()->EnablePrimaryColors();
		FDynamicMeshColorOverlay* Col = Mesh.Attributes()->PrimaryColors();
		FDynamicMeshNormalOverlay* Nrm = Mesh.Attributes()->PrimaryNormals();
		const double R = GlobeRadiusCm * (1.0 + GlobeLineLift);
		auto Add = [&](const TArray<TArray<float>>& Set, const FColor& C, float Alpha)
		{
			const FLinearColor L = FLinearColor::FromSRGBColor(C);
			const FVector4f Rgba(L.R, L.G, L.B, Alpha);
			for (const TArray<float>& Line : Set)
			{
				for (int32 i = 0; i + 3 < Line.Num(); i += 2)
				{
					const FVector P0 = GlobeDir(Line[i], Line[i + 1]) * R, P1 = GlobeDir(Line[i + 2], Line[i + 3]) * R;
					const FVector Side = (P1 - P0).Cross(P0).GetSafeNormal();
					if (Side.IsZero())
					{
						continue;   // repeated point
					}
					int32 V[4];
					for (int32 k = 0; k < 4; ++k)
					{
						V[k] = Mesh.AppendVertex(FVector3d(k < 2 ? P0 : P1));   // id == color id == normal id
						Col->AppendElement(Rgba);
						Nrm->AppendElement(FVector3f(k & 1 ? -Side : Side));
					}
					for (const FIndex3i& T : {FIndex3i(V[0], V[1], V[2]), FIndex3i(V[1], V[3], V[2])})
					{
						const int32 Tid = Mesh.AppendTriangle(T);   // M_GlobeLine is two-sided: winding is moot
						if (Tid >= 0)
						{
							Col->SetTriangle(Tid, T);
							Nrm->SetTriangle(Tid, T);
						}
					}
				}
			}
		};
		Add(States, GlobeBorderColor, GlobeStateAlpha);   // the page's draw order: states, borders, coast
		Add(Borders, GlobeBorderColor, GlobeBorderAlpha);
		Add(Coast, GlobeCoastColor, GlobeCoastAlpha);
	});
}

void AODMenuGlobe::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	if (FlyT < 1.0f)
	{
		// arc (2026-10-14): great circle, altitude log-eased (steady zoom feel)
		// plus the bump; UpdateCamera keeps it above the surface
		FlyT = FMath::Min(1.0f, FlyT + DeltaSeconds / FlySec);
		const double E = FMath::SmoothStep(0.0, 1.0, double(FlyT));
		const FVector D = FQuat::Slerp(FQuat::Identity, FQuat::FindBetweenNormals(FromDir, ToDir), E).RotateVector(FromDir);
		View = FVector(FMath::Clamp(FMath::RadiansToDegrees(FMath::Asin(FMath::Clamp(D.Z, -1.0, 1.0))), -GlobeMaxLat, GlobeMaxLat),
			FMath::RadiansToDegrees(FMath::Atan2(-D.Y, D.X)),
			FMath::Exp(FMath::Lerp(FMath::Loge(FromAlt), FMath::Loge(ToAlt), E)) + FlyBump * FMath::Sin(UE_DOUBLE_PI * E));
		UpdateCamera();
	}
	if (LinesMID)
	{
		LinesMID->SetScalarParameterValue(TEXT("Opacity"), float(FMath::SmoothStep(GlobeLinesAltOff, GlobeLinesAltOn, View.Z)));
	}
	if (DrapeMID && Drape->IsVisible())
	{
		const double Target = FMath::GetMappedRangeValueClamped(FVector2D(GlobeDrapeAltOff, GlobeDrapeAltOn), FVector2D(0.0, 1.0), View.Z);
		DrapeAlpha = FMath::FInterpTo(DrapeAlpha, float(Target), DeltaSeconds, GlobeDrapeFadeSpeed);
		DrapeMID->SetScalarParameterValue(TEXT("Opacity"), DrapeAlpha);
	}
}

void AODMenuGlobe::FlyTo(double Lat, double Lng, double Altitude, float Seconds)
{
	Lat = FMath::Clamp(Lat, -GlobeMaxLat, GlobeMaxLat);
	FromDir = GlobeDir(View.X, View.Y);
	ToDir = GlobeDir(Lat, Lng);
	FromAlt = View.Z;
	ToAlt = FMath::Clamp(Altitude, GlobeMinAlt, GlobeMaxAlt);
	// van Wijk-style: a long hop climbs to ~GlobeFlyBumpPerRad x the angle at
	// mid-flight (sqrt(a0 a1) is the log-eased altitude there); a pure zoom has no bump
	const double Theta = FMath::Acos(FMath::Clamp(FromDir.Dot(ToDir), -1.0, 1.0));
	FlyBump = FMath::Max(0.0, FMath::Min(GlobeFlyBumpPerRad * Theta, GlobeMaxAlt) - FMath::Sqrt(FromAlt * ToAlt));
	FlySec = Seconds * float(FMath::Clamp(0.75 + Theta, 0.75, 1.5));
	FlyT = 0.0f;
	if (Seconds <= 0.0f)
	{
		View = FVector(Lat, FMath::UnwindDegrees(Lng), ToAlt);
		FlyT = 1.0f;
		UpdateCamera();
	}
}

double AODMenuGlobe::FitAltitude(double SpanKm)
{
	return FMath::Clamp(SpanKm / EarthRadiusKm / (2.0 * FMath::Tan(FMath::DegreesToRadians(GlobeVFovDeg * 0.5))) + GlobeDrapeLift,
		GlobeMinAlt, GlobeMaxAlt);
}

bool AODMenuGlobe::IsFlying() const
{
	return FlyT < 1.0f;
}

void AODMenuGlobe::Orbit(const FVector2D& PixelDelta)
{
	FlyT = 1.0f;   // a drag cancels a fly
	const double K = GlobeOrbitDegPerPx * View.Z;
	View.X = FMath::Clamp(View.X + PixelDelta.Y * K, -GlobeMaxLat, GlobeMaxLat);   // drag down = see further north
	View.Y = FMath::UnwindDegrees(View.Y - PixelDelta.X * K);                      // drag right = spin east-to-west
	UpdateCamera();
}

void AODMenuGlobe::Zoom(float Notches)
{
	FlyT = 1.0f;
	View.Z *= FMath::Pow(GlobeZoomPerNotch, double(Notches));   // UpdateCamera clamps
	UpdateCamera();
}

void AODMenuGlobe::UpdateCamera()
{
	View.Z = FMath::Clamp(View.Z, GlobeMinAlt, GlobeMaxAlt);   // never below the surface (2026-10-14)
	const FVector D = GlobeDir(View.X, View.Y);
	Camera->SetRelativeLocationAndRotation(D * GlobeRadiusCm * (1.0 + View.Z),
		FRotationMatrix::MakeFromXZ(-D, FVector::UpVector).Rotator());
}

bool AODMenuGlobe::ProjectPin(const APlayerController* PC, int32 I, FVector2D& OutScreen) const
{
	return Pins.IsValidIndex(I) && LatLngToScreen(PC, Pins[I].Lat, Pins[I].Lng, OutScreen);
}

bool AODMenuGlobe::LatLngToScreen(const APlayerController* PC, double Lat, double Lng, FVector2D& OutScreen) const
{
	if (!PC)
	{
		return false;
	}
	const FVector D = GlobeDir(Lat, Lng);
	const FVector P = GetActorLocation() + D * GlobeRadiusCm;
	if (D.Dot(Camera->GetComponentLocation() - P) < 0.0 || !PC->ProjectWorldLocationToScreen(P, OutScreen))
	{
		return false;   // behind the globe
	}
	int32 W = 0, H = 0;
	PC->GetViewportSize(W, H);
	return OutScreen.X >= 0.0 && OutScreen.Y >= 0.0 && OutScreen.X < W && OutScreen.Y < H;
}

bool AODMenuGlobe::ScreenToLatLng(const APlayerController* PC, FVector2D Screen, double& OutLat, double& OutLng) const
{
	FVector O, Ray;
	if (!PC || !PC->DeprojectScreenPositionToWorld(Screen.X, Screen.Y, O, Ray))
	{
		return false;
	}
	// nearest hit of O + t Ray on the sphere (the camera is always outside it)
	const FVector C = O - GetActorLocation();
	const double B = C.Dot(Ray), Disc = B * B - (C.SizeSquared() - FMath::Square(GlobeRadiusCm));
	if (Disc < 0.0)
	{
		return false;   // space
	}
	const FVector D = (C + Ray * (-B - FMath::Sqrt(Disc))).GetSafeNormal();
	OutLat = FMath::RadiansToDegrees(FMath::Asin(FMath::Clamp(D.Z, -1.0, 1.0)));
	OutLng = FMath::RadiansToDegrees(FMath::Atan2(-D.Y, D.X));
	return true;
}

void AODMenuGlobe::SetDrape(UTexture2D* Tex, const TArray<FVector2D>& CornerLatLng)
{
	if (!Tex || CornerLatLng.Num() != 4 || !DrapeMID)
	{
		ClearDrape();
		return;
	}
	// ponytail: plain lng lerp - an area straddling the antimeridian would wrap the long way
	const FVector2D NW = CornerLatLng[0], NE = CornerLatLng[1], SE = CornerLatLng[2], SW = CornerLatLng[3];
	Drape->EditMesh([&](FDynamicMesh3& M)
	{
		BuildGrid(M, GlobeRadiusCm * (1.0 + GlobeDrapeLift), false, GlobeDrapeSegs, GlobeDrapeSegs,
			[&](double U, double V) { return FMath::Lerp(FMath::Lerp(NW, NE, U), FMath::Lerp(SW, SE, U), V); });
	});
	DrapeMID->SetTextureParameterValue(TEXT("Albedo"), Tex);
	DrapeAlpha = 0.0f;
	DrapeMID->SetScalarParameterValue(TEXT("Opacity"), 0.0f);
	Drape->SetVisibility(true);
}

void AODMenuGlobe::ClearDrape()
{
	Drape->SetVisibility(false);
	DrapeAlpha = 0.0f;
}
