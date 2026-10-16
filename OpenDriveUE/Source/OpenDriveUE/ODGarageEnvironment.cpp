#include "ODGarageEnvironment.h"

#include "Components/BoxReflectionCaptureComponent.h"
#include "Components/DirectionalLightComponent.h"
#include "Components/DynamicMeshComponent.h"
#include "Components/PointLightComponent.h"
#include "Components/SkyLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "DynamicMesh/DynamicMesh3.h"
#include "DynamicMesh/DynamicMeshAttributeSet.h"
#include "DynamicMesh/DynamicMeshOverlay.h"
#include "DynamicMesh/MeshNormals.h"
#include "Engine/DirectionalLight.h"
#include "Engine/SkyLight.h"
#include "EngineUtils.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Math/RandomStream.h"
#include "ODWeatherController.h"

using namespace UE::Geometry;

namespace
{
// Same node/path this project already relies on elsewhere for basic-shape
// meshes (see ODForestManager.cpp's rock fallback) - never delete/rename.
const TCHAR* CubeMeshPath = TEXT("/Engine/BasicShapes/Cube.Cube");

const TCHAR* FloorMatPath = TEXT("/Game/OpenDrive/Materials/M_GarageFloorGlossy");
const TCHAR* WallMatPath = TEXT("/Game/OpenDrive/Materials/M_GarageWallDark");
const TCHAR* HexMatPath = TEXT("/Game/OpenDrive/Materials/M_HexPanelEmissive");

UStaticMeshComponent* MakeCubeComp(AActor* Owner, USceneComponent* Parent, const TCHAR* Name)
{
	UStaticMeshComponent* Comp = NewObject<UStaticMeshComponent>(Owner, Name);
	Comp->SetupAttachment(Parent);
	Comp->SetMobility(EComponentMobility::Movable);
	Comp->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	Comp->SetCastShadow(true);
	if (UStaticMesh* Cube = LoadObject<UStaticMesh>(nullptr, CubeMeshPath))
	{
		Comp->SetStaticMesh(Cube);
	}
	Comp->RegisterComponent();
	return Comp;
}
}

AODGarageEnvironment::AODGarageEnvironment()
{
	PrimaryActorTick.bCanEverTick = false;

	Root = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
	Root->SetMobility(EComponentMobility::Movable);
	RootComponent = Root;

	// hidden until the menu first asks for it (AODMainMenu::EnterCarConfig())
	SetActorHiddenInGame(true);
	SetActorEnableCollision(false);
}

void AODGarageEnvironment::BeginPlay()
{
	Super::BeginPlay();
	// NewObject<>()+RegisterComponent() (BuildRoom's whole approach, matching
	// AODTerrainManager::BuildTile's dynamic-mesh-tile pattern) is only valid
	// once this actor is actually in a world - doing it from the constructor
	// runs during CDO construction, before the engine's typed-element
	// registry exists yet ("Typed element was requested... before the
	// registry was available", a real ensure hit here 2026-08-27). BeginPlay
	// is the right place, and this actor is only ever created via
	// SpawnActor() (AODMainMenu::EnsureGarageEnvironment()), never placed in
	// a level, so BeginPlay firing once per spawn is exactly "build it once".
	BuildRoom();
}

void AODGarageEnvironment::BuildRoom()
{
	// ---- floor: glossy dark concrete, one flat slab under the whole room --
	Floor = MakeCubeComp(this, Root, TEXT("Floor"));
	const float FloorThicknessCm = 20.0f;
	Floor->SetRelativeLocation(FVector(0, 0, -FloorThicknessCm * 0.5f));
	Floor->SetRelativeScale3D(FVector(
		RoomHalfExtentCm * 2.0f / 100.0f, RoomHalfExtentCm * 2.0f / 100.0f, FloorThicknessCm / 100.0f));
	if (UMaterialInterface* FloorMat = LoadObject<UMaterialInterface>(nullptr, FloorMatPath))
	{
		Floor->SetMaterial(0, UMaterialInstanceDynamic::Create(FloorMat, this));
	}

	// ---- four walls: very dark, matte enough that corners still read -----
	const float WallThicknessCm = 20.0f;
	const float WallZ = RoomHeightCm * 0.5f;
	UMaterialInterface* WallMatBase = LoadObject<UMaterialInterface>(nullptr, WallMatPath);

	auto SetupWall = [&](UStaticMeshComponent* Wall, const FVector& Loc, const FVector& ScaleCm)
	{
		Wall->SetRelativeLocation(Loc);
		Wall->SetRelativeScale3D(ScaleCm / 100.0f);
		if (WallMatBase)
		{
			Wall->SetMaterial(0, UMaterialInstanceDynamic::Create(WallMatBase, this));
		}
	};

	WallNorth = MakeCubeComp(this, Root, TEXT("WallNorth"));
	SetupWall(WallNorth, FVector(0, RoomHalfExtentCm, WallZ),
		FVector(RoomHalfExtentCm * 2.0f, WallThicknessCm, RoomHeightCm));
	WallSouth = MakeCubeComp(this, Root, TEXT("WallSouth"));
	SetupWall(WallSouth, FVector(0, -RoomHalfExtentCm, WallZ),
		FVector(RoomHalfExtentCm * 2.0f, WallThicknessCm, RoomHeightCm));
	WallEast = MakeCubeComp(this, Root, TEXT("WallEast"));
	SetupWall(WallEast, FVector(RoomHalfExtentCm, 0, WallZ),
		FVector(WallThicknessCm, RoomHalfExtentCm * 2.0f, RoomHeightCm));
	WallWest = MakeCubeComp(this, Root, TEXT("WallWest"));
	SetupWall(WallWest, FVector(-RoomHalfExtentCm, 0, WallZ),
		FVector(WallThicknessCm, RoomHalfExtentCm * 2.0f, RoomHeightCm));

	// ---- ceiling backing: same dark wall material, sits just above the hex
	// outlines so the hex centres read as dark panels, not void ----
	CeilingBacking = MakeCubeComp(this, Root, TEXT("CeilingBacking"));
	const float BackingThicknessCm = 20.0f;
	CeilingBacking->SetRelativeLocation(FVector(0, 0, RoomHeightCm + BackingThicknessCm * 0.5f));
	CeilingBacking->SetRelativeScale3D(FVector(
		RoomHalfExtentCm * 2.0f / 100.0f, RoomHalfExtentCm * 2.0f / 100.0f, BackingThicknessCm / 100.0f));
	if (WallMatBase)
	{
		CeilingBacking->SetMaterial(0, UMaterialInstanceDynamic::Create(WallMatBase, this));
	}

	// ---- hex-tile ceiling (see BuildHexCeiling) ---------------------------
	HexCeiling = NewObject<UDynamicMeshComponent>(this, TEXT("HexCeiling"));
	HexCeiling->SetupAttachment(Root);
	HexCeiling->SetMobility(EComponentMobility::Movable);
	HexCeiling->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	HexCeiling->SetCastShadow(false);   // thin two-sided emissive tiles - shadowing them buys nothing
	HexCeiling->RegisterComponent();
	if (UMaterialInterface* HexMat = LoadObject<UMaterialInterface>(nullptr, HexMatPath))
	{
		// the outlines carry all the light the filled tiles used to (0.88^2 of
		// each hex at the material's default 14 lit the car's top through
		// Lumen; outlines alone at 14 left roofs and bonnets black, 2026-10-16)
		UMaterialInstanceDynamic* HexMID = UMaterialInstanceDynamic::Create(HexMat, this);
		const float LitArea = 1.0f - FMath::Square(FMath::Clamp(HexFillRatio, 0.0f, 0.98f));
		HexMID->SetScalarParameterValue(TEXT("EmissiveStrength"), 14.0f * FMath::Square(0.88f) / LitArea);
		HexCeiling->SetMaterial(0, HexMID);
	}
	BuildHexCeiling();

	// ---- fill lights: the emissive hex tiles alone won't light the car
	// under normal exposure - a couple of hidden point lights do the real
	// illumination, matching the hex panels' cool-white color (standard
	// "fake it with real lights behind the emissive" trick) ----------------
	auto MakeFillLight = [&](const TCHAR* Name, const FVector& Loc)
	{
		UPointLightComponent* Light = NewObject<UPointLightComponent>(this, Name);
		Light->SetupAttachment(Root);
		Light->SetMobility(EComponentMobility::Movable);
		Light->SetRelativeLocation(Loc);
		Light->SetLightColor(FLinearColor(0.75f, 0.85f, 1.0f));   // cool ~6500K white
		Light->SetIntensity(4500.0f);   // Unitless (runtime default; ~7 cd) - the room was tuned by eye at this
		Light->AttenuationRadius = RoomHalfExtentCm * 2.5f;
		Light->SetCastShadows(false);   // cheap - this room only exists while the menu is paused
		Light->RegisterComponent();
		return Light;
	};
	FillLightA = MakeFillLight(TEXT("FillLightA"),
		FVector(RoomHalfExtentCm * 0.35f, RoomHalfExtentCm * 0.35f, RoomHeightCm - 40.0f));
	FillLightB = MakeFillLight(TEXT("FillLightB"),
		FVector(-RoomHalfExtentCm * 0.35f, -RoomHalfExtentCm * 0.35f, RoomHeightCm - 40.0f));

	// ---- reflection capture: lets the glossy floor actually pick up the
	// car/hex-ceiling instead of relying on SSR alone (which can't reflect
	// anything currently off-screen, e.g. the ceiling while looking at the
	// floor near the car). Re-captured in PlaceAt() every time the room
	// moves - see that function. ---------------------------------------
	ReflectionCapture = NewObject<UBoxReflectionCaptureComponent>(this, TEXT("ReflectionCapture"));
	ReflectionCapture->SetupAttachment(Root);
	ReflectionCapture->SetMobility(EComponentMobility::Movable);
	ReflectionCapture->SetRelativeLocation(FVector(0, 0, RoomHeightCm * 0.5f));
	ReflectionCapture->SetRelativeScale3D(FVector(RoomHalfExtentCm / 100.0f));
	ReflectionCapture->RegisterComponent();
}

void AODGarageEnvironment::BuildHexCeiling()
{
	if (!HexCeiling)
	{
		return;
	}

	const float R = HexRadiusCm;               // nominal circumradius - drives SPACING
	const float InR = R * HexFillRatio;         // inner edge of the lit outline band
	const float HorizSpacing = FMath::Sqrt(3.0f) * R;
	const float VertSpacing = 1.5f * R;
	const float Margin = R;                     // keep whole hexes clear of the walls
	const float ExtentXY = RoomHalfExtentCm - Margin;
	const float CeilingZ = RoomHeightCm - 5.0f;   // just under the backing slab, avoids z-fighting

	// deterministic pseudo-random "a few dimmer for realism" pick, same
	// fixed seed every rebuild so this stays reproducible.
	FRandomStream Rng(1337);

	const int32 MaxRow = FMath::CeilToInt(ExtentXY / VertSpacing) + 1;
	const int32 MaxCol = FMath::CeilToInt(ExtentXY / HorizSpacing) + 1;

	int32 HexCount = 0;

	HexCeiling->EditMesh([&](FDynamicMesh3& Mesh)
	{
		Mesh.EnableAttributes();
		Mesh.Attributes()->EnablePrimaryColors();
		FDynamicMeshColorOverlay* Color = Mesh.Attributes()->PrimaryColors();

		for (int32 Row = -MaxRow; Row <= MaxRow; ++Row)
		{
			const float Y = Row * VertSpacing;
			if (FMath::Abs(Y) > ExtentXY)
			{
				continue;
			}
			const float RowOffset = (Row & 1) ? HorizSpacing * 0.5f : 0.0f;
			for (int32 Col = -MaxCol; Col <= MaxCol; ++Col)
			{
				const float X = Col * HorizSpacing + RowOffset;
				if (FMath::Abs(X) > ExtentXY)
				{
					continue;
				}

				// most outlines read as full-brightness LED strips; a small
				// pseudo-random fraction read dimmer, as if one strip is going
				// - variation, not a broken look.
				const bool bDim = Rng.FRand() < 0.12f;
				const FVector4f VColor = bDim
					? FVector4f(0.35f, 0.35f, 0.35f, 1.0f)
					: FVector4f(1.0f, 1.0f, 1.0f, 1.0f);

				// the outline: a ring between InR and R, 12 triangles
				int32 OutIds[6], InIds[6], ColIds[12];
				for (int32 i = 0; i < 6; ++i)
				{
					// pointy-top hex: vertices at 30,90,...,330 degrees
					const float Theta = FMath::DegreesToRadians(30.0f + 60.0f * i);
					const FVector2d Dir(FMath::Cos(Theta), FMath::Sin(Theta));
					OutIds[i] = Mesh.AppendVertex(FVector3d(X + R * Dir.X, Y + R * Dir.Y, CeilingZ));
					InIds[i] = Mesh.AppendVertex(FVector3d(X + InR * Dir.X, Y + InR * Dir.Y, CeilingZ));
					ColIds[i] = Color->AppendElement(VColor);
					ColIds[6 + i] = Color->AppendElement(VColor);
				}

				for (int32 i = 0; i < 6; ++i)
				{
					const int32 Next = (i + 1) % 6;
					// winding direction doesn't matter for visibility - the
					// hex material is set two-sided in apply_garage_material.py
					// specifically so a wrong-way normal here never shows up
					// as an invisible/black ceiling (this mesh is never
					// checked against a real render, only -nullrhi headless).
					const int32 T0 = Mesh.AppendTriangle(OutIds[i], OutIds[Next], InIds[Next]);
					if (T0 >= 0)
					{
						Color->SetTriangle(T0, FIndex3i(ColIds[i], ColIds[Next], ColIds[6 + Next]));
					}
					const int32 T1 = Mesh.AppendTriangle(OutIds[i], InIds[Next], InIds[i]);
					if (T1 >= 0)
					{
						Color->SetTriangle(T1, FIndex3i(ColIds[i], ColIds[6 + Next], ColIds[6 + i]));
					}
				}
				++HexCount;
			}
		}

		FMeshNormals::InitializeOverlayToPerVertexNormals(Mesh.Attributes()->PrimaryNormals(), false);
	});

	HexCeiling->SetVertexColorSpaceTransformMode(EDynamicMeshVertexColorTransformMode::LinearToSRGB);

	UE_LOG(LogTemp, Display, TEXT("ODGarageEnvironment: built hex ceiling with %d tiles"), HexCount);
}

void AODGarageEnvironment::PlaceAt(const FVector& Center, float YawDeg, float GroundContactOffsetCm)
{
	// see header comment: Center is the pawn's actor origin (body pivot at
	// axle height), not ground level - drop the whole room by the wheels'
	// actual clearance below that pivot so the floor top surface lands under
	// the tires instead of at axle height (2026-08-27, "cars sink into the
	// garage floor" fix). Shifting the whole room (not just the Floor mesh)
	// keeps walls/ceiling self-consistent with the new floor with no other
	// change needed - RoomHeightCm (500) dwarfs this offset (tens of cm).
	const FVector GroundedCenter = Center - FVector(0.0f, 0.0f, GroundContactOffsetCm);
	SetActorLocation(GroundedCenter, false, nullptr, ETeleportType::TeleportPhysics);
	// cosmetic only - the square footprint is symmetric, so any yaw clears
	// the showroom camera identically; matching the car's display yaw just
	// keeps the room's "front" wall roughly behind the car in frame.
	SetActorRotation(FRotator(0.0f, YawDeg, 0.0f));

	if (ReflectionCapture)
	{
		// re-bake the box capture at the new spot - MarkDirtyForRecapture()
		// queues it, UpdateReflectionCaptureContents() actually processes the
		// queue (this is the standard runtime-reflection-capture pairing;
		// safe to call even with nothing dirty).
		ReflectionCapture->MarkDirtyForRecapture();
		if (UWorld* World = GetWorld())
		{
			UReflectionCaptureComponent::UpdateReflectionCaptureContents(
				World, TEXT("ODGarageEnvironment::PlaceAt"));
		}
	}
}

void AODGarageEnvironment::SetActive(bool bOn)
{
	if (bOn == bIsActive)
	{
		return;
	}
	bIsActive = bOn;
	SetActorHiddenInGame(!bOn);
	SetActorEnableCollision(bOn);
	OverrideSceneLighting(bOn);
	for (TActorIterator<AODWeatherController> It(GetWorld()); It; ++It)
	{
		It->SetIndoors(bOn);
	}
	UE_LOG(LogTemp, Display, TEXT("ODGarageEnvironment: garage %s"), bOn ? TEXT("shown, lights on") : TEXT("hidden"));
}

void AODGarageEnvironment::OverrideSceneLighting(bool bOn)
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	if (bOn)
	{
		CachedDirLights.Reset();
		for (TActorIterator<ADirectionalLight> It(World); It; ++It)
		{
			if (UDirectionalLightComponent* Comp = It->FindComponentByClass<UDirectionalLightComponent>())
			{
				CachedDirLights.Add({Comp, Comp->Intensity});
				Comp->SetIntensity(0.0f);
			}
		}
		CachedSkyLights.Reset();
		for (TActorIterator<ASkyLight> It(World); It; ++It)
		{
			if (USkyLightComponent* Comp = It->FindComponentByClass<USkyLightComponent>())
			{
				CachedSkyLights.Add({Comp, Comp->Intensity});
				Comp->SetIntensity(0.0f);
			}
		}
	}
	else
	{
		for (const FCachedDirLight& L : CachedDirLights)
		{
			if (L.Component.IsValid())
			{
				L.Component->SetIntensity(L.OriginalIntensity);
			}
		}
		CachedDirLights.Reset();
		for (const FCachedSkyLight& L : CachedSkyLights)
		{
			if (L.Component.IsValid())
			{
				L.Component->SetIntensity(L.OriginalIntensity);
			}
		}
		CachedSkyLights.Reset();
	}
}
