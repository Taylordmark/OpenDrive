"""Suspension frame-rate stability (2026-10-09, Taylor: "tires/wheels are
unstable and bouncing around like crazy at all times" on a 25-40 fps drive;
the log's ROLLOVER tripwire caught 312 deg/s of roll rate at -2 deg of roll).

UODVehicleMovement::TickSuspension applied spring + damper forces once per
game frame, and Chaos integrates them semi-implicitly: v += dt*F/m,
x += dt*v. The damper's per-corner C is sized for the corner's share of the
MASS, but in roll each corner only moves Ix / (4 t^2) of effective mass -
about half - so at 25-30 fps the roll mode was over-damped for the step size
(negative eigenvalue: frame-to-frame flip-flop). The first fix capped C at
RollCornerMass / Dt - an fps-dependent ride. Now (2026-10-09, Taylor:
"everything in the game should be independent of frame rate") the sim and
Chaos both step at a fixed 1/120 s whatever the frame rate, and the tuned C
runs uncapped.

Mirrors the 1-DOF roll and heave modes with the Muscle car's numbers: the old
per-frame step reproduces the flip-flop, the fixed step is clean.

Usage:  python pipeline/check_suspension_stability.py
"""
import numpy as np

MASS = 1620.0            # kg (log: "bound to ... mass=1620 kg")
K = 61000.0              # ODVehicleDef SpringRate
ZETA = 0.9               # ODVehicleDef DampingRatio
HALF_TRACK = 90.0        # cm, cars_manifest Muscle
BOX_W, BOX_H = 190.0, 130.0                       # cm, collision box (approx)
IX = MASS * (BOX_W ** 2 + BOX_H ** 2) / 12.0      # kg cm^2, box roll inertia
ROLL_CORNER_MASS = IX / (4.0 * HALF_TRACK ** 2)   # what one corner moves in roll
FIXED_DT = 1.0 / 120.0   # DefaultEngine.ini AsyncFixedTimeStepSize - sim + Chaos, any fps
C_GAME = 2.0 * ZETA * np.sqrt(K * MASS * 0.25)


def step_matrix(m_eff, c, dt):
    """[x, v] -> next step, force from this step's x and v (ComprRate)."""
    return np.array([[1 - dt * dt * K / m_eff, dt * (1 - dt * c / m_eff)],
                     [-dt * K / m_eff, 1 - dt * c / m_eff]])


def worst(m_eff, dt):
    """(spectral radius, most negative real eigenvalue) of one step."""
    ev = np.linalg.eigvals(step_matrix(m_eff, C_GAME, dt))
    return np.abs(ev).max(), min([e.real for e in ev if abs(e.imag) < 1e-9], default=0.0)


def main():
    print(f"roll corner mass {ROLL_CORNER_MASS:.0f} kg vs heave corner {MASS / 4:.0f} kg; C {C_GAME:.0f}")
    print(" old per-frame step: fps | roll |eig| min-real")
    for fps in (60, 45, 35, 30, 25, 20):
        r = worst(ROLL_CORNER_MASS, min(1.0 / fps, 1.0 / 30.0))   # Chaos clamped its step at 1/30
        print(f"                    {fps:3d} | {r[0]:6.2f} {r[1]:+6.2f}")
    roll, heave = worst(ROLL_CORNER_MASS, FIXED_DT), worst(MASS / 4, FIXED_DT)
    print(f" fixed 120 Hz, any fps: roll {roll[0]:.3f} {roll[1]:+.3f}  heave {heave[0]:.3f} {heave[1]:+.3f}")
    assert worst(ROLL_CORNER_MASS, 1.0 / 30.0)[1] < -0.4, "the mirror reproduces the old per-frame flip-flop"
    assert roll[0] < 1.0 and roll[1] >= 0.0, "fixed step: roll stable, no step-to-step flip-flop, uncapped C"
    assert heave[0] < 1.0 and heave[1] >= 0.0, "fixed step: heave stable"
    print("PASS: suspension stable with tuned damping at the fixed step, independent of frame rate")


if __name__ == "__main__":
    main()
