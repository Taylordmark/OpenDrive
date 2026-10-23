#pragma once

#include "CoreMinimal.h"

/** Turn indicators / hazards (fun-features #8b), shared by the player pawn
 *  and the traffic AI. The lamps are the AMBER lens cells of the car meshes:
 *  M_CarLights "IndicatorL/R" (player MID) or per-instance custom data 0/1
 *  (traffic ISMs). Pure state, so the automation test drives it directly. */
struct FODIndicator
{
	enum EState : uint8 { Off, Left, Right, Hazard };

	static constexpr float BlinkHz = 1.5f;   // 90 flashes a minute (UN R48: 60-120)
	static constexpr float ArmSteer = 0.3f;  // steer toward the side past this arms the self-cancel...
	static constexpr float CancelSteer = 0.1f;   // ...and back under this cancels

	EState State = Off;
	bool bArmed = false;

	/** Lit half of the blink cycle, on world time (frame-rate independent). */
	static bool BlinkOn(double TimeS) { return FMath::Frac(TimeS * BlinkHz) < 0.5; }

	/** A second press of the same switch turns it off. */
	void Toggle(EState S)
	{
		State = State == S ? Off : S;
		bArmed = false;
	}

	/** Self-cancel like a real column switch: steer (-1 left .. +1 right)
	 *  past ArmSteer toward the indicated side, then back under CancelSteer. */
	void UpdateCancel(float Steer)
	{
		if (State != Left && State != Right)
		{
			return;
		}
		const float Toward = State == Left ? -Steer : Steer;
		if (Toward > ArmSteer)
		{
			bArmed = true;
		}
		else if (bArmed && Toward < CancelSteer)
		{
			State = Off;
			bArmed = false;
		}
	}

	bool LitL(double TimeS) const { return (State == Left || State == Hazard) && BlinkOn(TimeS); }
	bool LitR(double TimeS) const { return (State == Right || State == Hazard) && BlinkOn(TimeS); }
};
