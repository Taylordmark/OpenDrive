#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Pawn.h"
#include "Kismet/GameplayStatics.h"

/** Actor tag on every menu-vista manager copy (AODGameMode::FreezeStreaming skips them). */
inline const FName ODVistaTag(TEXT("ODVista"));

/**
 * Garage-menu vista region (2026-10-05). A manager copy spawned for the menu
 * backdrop (AODGameMode::SpawnVistaSet) streams around a fixed Focus (the
 * car's menu spot, area-local cm) instead of the player pawn, and only loads
 * tiles that touch either a PatchCm disc around the car or a +-HalfAngle wedge
 * whose apex sits BackCm behind the car (the camera) along -Dir, reaching
 * FarCm past it. Inactive (default) = every tile wanted, stream around the
 * player as always. Plain struct, no UObject refs.
 * PanoFarCm (panorama trial, 2026-10-05): terrain alone also loads its
 * coarse/background tiles in the same wedge out to this reach (Far override
 * below), so distant ridges/shores show instead of void; 0 = off.
 */
struct FODVistaRegion
{
	bool bActive = false;
	FVector Focus = FVector::ZeroVector;   // area-local cm
	FVector2D Dir = FVector2D(1.0, 0.0);   // unit view direction
	double BackCm = 700.0;
	double FarCm = 100000.0;
	double PatchCm = 3000.0;
	double PanoFarCm = 0.0;
	double HalfAngleRad = FMath::DegreesToRadians(40.0);

	/** Conservative: true if the AABB might touch the patch or the wedge
	 *  (reaching Far past the car; < 0 = FarCm). */
	bool WantsRect(const FVector2D& Min, const FVector2D& Max, double Far = -1.0) const
	{
		if (!bActive)
		{
			return true;
		}
		const FVector2D F(Focus.X, Focus.Y);
		const FVector2D Closest(FMath::Clamp(F.X, Min.X, Max.X), FMath::Clamp(F.Y, Min.Y, Max.Y));
		if (FVector2D::DistSquared(Closest, F) <= PatchCm * PatchCm)
		{
			return true;
		}
		// ponytail: bounding-circle test against the wedge, over-includes a
		// sliver of corner tiles; exact polygon clip if tile counts matter.
		const FVector2D Apex = F - Dir * BackCm;
		const FVector2D C = (Min + Max) * 0.5;
		const double H = (Max - Min).Size() * 0.5;
		const FVector2D D = C - Apex;
		const double L = D.Size();
		if (L <= H)
		{
			return true;
		}
		if (L - H > BackCm + (Far < 0.0 ? FarCm : Far))
		{
			return false;
		}
		const double Ang = FMath::Acos(FVector2D::DotProduct(D / L, Dir));
		return Ang <= HalfAngleRad + FMath::Asin(FMath::Min(1.0, H / L));
	}

	bool WantsTile(const FIntPoint& Key, double TileSizeCm, double PadCm = 0.0, double Far = -1.0) const
	{
		const FVector2D Min(Key.X * TileSizeCm - PadCm, Key.Y * TileSizeCm - PadCm);
		const FVector2D Max((Key.X + 1) * TileSizeCm + PadCm, (Key.Y + 1) * TileSizeCm + PadCm);
		return WantsRect(Min, Max, Far);
	}

	bool WantsPoint(const FVector2D& P, double PadCm) const
	{
		return WantsRect(P - FVector2D(PadCm, PadCm), P + FVector2D(PadCm, PadCm));
	}

	/** Where a manager streams around: Focus in vista mode, else the player
	 *  pawn (false = no pawn yet, skip the tick like before). */
	bool StreamCenter(const UObject* WorldContext, FVector& Out) const
	{
		if (bActive)
		{
			Out = Focus;
			return true;
		}
		const APawn* Player = UGameplayStatics::GetPlayerPawn(WorldContext, 0);
		if (!Player)
		{
			return false;
		}
		Out = Player->GetActorLocation();
		return true;
	}
};
