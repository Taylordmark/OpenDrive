#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "ODMenuGlobe.generated.h"

class UCameraComponent;
class UStaticMeshComponent;
class UDynamicMeshComponent;
class UMaterialInstanceDynamic;
class UTexture2D;

/** One globe pin (an area cluster), drawn by AODRaceHUD over its projected point. */
struct FODGlobePin
{
	FString Key;    // AreaKeys[i] of the cluster's first area
	FString Name;   // "Area A / Area B"
	FString Sub;    // "12 towns" / "3 tracks"
	double Lat = 0.0;
	double Lng = 0.0;
};

/**
 * Open Drive / Track Select world map (2026-10-14), in-engine (was a
 * globe.gl page in an SWebBrowser). Same look: NASA Blue Marble
 * (WorldMap/globe-dark.jpg, bump globe-bump.jpg, loaded at runtime), coast /
 * border / state lines from Masks/worldmap.json, a procedural star field and
 * at most a faint rim (2026-10-14: the white atmosphere halo is gone). Lives far
 * from every streamed area (like the showroom) and owns its own camera;
 * AODMainMenu makes it the view target on the World screen, feeds the pins and
 * drives FlyTo. Pins are not 3D - the HUD draws them at ProjectPin().
 */
UCLASS()
class OPENDRIVEUE_API AODMenuGlobe : public AActor
{
	GENERATED_BODY()

public:
	AODMenuGlobe();
	virtual void BeginPlay() override;
	virtual void Tick(float DeltaSeconds) override;

	/** Coast/border/state polylines, flat [lat0, lng0, lat1, lng1, ...] per line
	 *  (worldmap.json is lng-first; AODMainMenu::LoadWorldMap swaps). Call once after spawn. */
	void SetLines(const TArray<TArray<float>>& Coast, const TArray<TArray<float>>& Borders,
		const TArray<TArray<float>>& States);
	void SetPins(const TArray<FODGlobePin>& InPins) { Pins = InPins; }
	const TArray<FODGlobePin>& GetPins() const { return Pins; }

	/** Ease the camera to look straight down at (Lat, Lng) from Altitude globe
	 *  radii above the surface (globe.gl's altitude: 1.7 overview, 0.06 fly-in).
	 *  Arcs (2026-10-14): great-circle path, altitude log-eased with a bump
	 *  for long hops, never below the surface; Seconds scales with the hop
	 *  (0.75x..1.5x). Seconds <= 0 snaps. */
	void FlyTo(double Lat, double Lng, double Altitude, float Seconds);
	/** Altitude (radii) at which a top-down view spans SpanKm of ground vertically. */
	static double FitAltitude(double SpanKm);
	bool IsFlying() const;
	/** Camera (lat, lng, altitude in radii) now; AODMainMenu clamps the AreaMap pan with FlyTo(..., 0). */
	FVector GetView() const { return View; }
	/** Mouse drag / wheel (the HUD's World-screen input): spin by screen-pixel delta, zoom by wheel notches. */
	void Orbit(const FVector2D& PixelDelta);
	void Zoom(float Notches);

	/** Screen position of pin I for this player's viewport; false if it is
	 *  behind the globe (or off screen). */
	bool ProjectPin(const APlayerController* PC, int32 I, FVector2D& OutScreen) const;
	/** Any surface point (2026-10-14), e.g. start dots on the drape; same rules as ProjectPin. */
	bool LatLngToScreen(const APlayerController* PC, double Lat, double Lng, FVector2D& OutScreen) const;
	/** Ray from a screen point to the globe surface; false on a miss (space). */
	bool ScreenToLatLng(const APlayerController* PC, FVector2D Screen, double& OutLat, double& OutLng) const;

	/** Area relief draped on the globe (2026-10-14): Tex (sRGB, row 0 = north)
	 *  over the patch bilinear in lat/lng between CornerLatLng = 4 x (lat, lng)
	 *  NW, NE, SE, SW (relief_<area>.json "corners_ll"). Fades in by altitude:
	 *  none above 0.5, full by 0.08. Replaces any previous drape. */
	void SetDrape(UTexture2D* Tex, const TArray<FVector2D>& CornerLatLng);
	void ClearDrape();

	UCameraComponent* GetCamera() const { return Camera; }

private:
	UPROPERTY()
	TObjectPtr<USceneComponent> Root;
	UPROPERTY()
	TObjectPtr<UDynamicMeshComponent> Sphere;
	UPROPERTY()
	TObjectPtr<UDynamicMeshComponent> Atmosphere;
	UPROPERTY()
	TObjectPtr<UDynamicMeshComponent> Lines;
	UPROPERTY()
	TObjectPtr<UDynamicMeshComponent> Backdrop;
	UPROPERTY()
	TObjectPtr<UDynamicMeshComponent> Stars;
	UPROPERTY()
	TObjectPtr<UDynamicMeshComponent> Drape;
	UPROPERTY()
	TObjectPtr<UCameraComponent> Camera;
	UPROPERTY()
	TObjectPtr<UMaterialInstanceDynamic> LinesMID;
	UPROPERTY()
	TObjectPtr<UMaterialInstanceDynamic> DrapeMID;

	void UpdateCamera();

	TArray<FODGlobePin> Pins;
	// camera state: (lat, lng, altitude) now; fly: unit directions + altitudes, bump at mid-flight
	FVector View = FVector(38.0, -105.0, 1.7);
	FVector FromDir = FVector::ForwardVector, ToDir = FVector::ForwardVector;
	double FromAlt = 1.7, ToAlt = 1.7, FlyBump = 0.0;
	float FlyT = 1.0f, FlySec = 0.0f;
	float DrapeAlpha = 0.0f;
};
