"""Suspension frame-rate stability (2026-10-09, Taylor: "tires/wheels are
unstable and bouncing around like crazy at all times" on a 25-40 fps drive;
the log's ROLLOVER tripwire caught 312 deg/s of roll rate at -2 deg of roll).

UODVehicleMovement::TickSuspension applies spring + damper forces once per
game frame (no physics substepping), and Chaos integrates them
semi-implicitly: v += dt*F/m, x += dt*v. The damper's per-corner C is sized
for the corner's share of the MASS, but in roll each corner only moves
Ix / (4 t^2) of effective mass - about half - so at 25-30 fps the roll mode
is over-damped for the step size: the step matrix gets a negative eigenvalue
(frame-to-frame flip-flop, worst at 30 fps where Chaos's 1/30 s step clamp
kicks in). TickSuspension now
caps C at RollCornerMass / Dt, which keeps det = 1 - C*dt/m >= 0.

Mirrors the 1-DOF roll and heave modes with the Muscle car's numbers and
checks the cap removes the flip-flop and the divergence at every frame rate.

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
MAX_PHYS_DT = 1.0 / 30.0  # UPhysicsSettings::MaxPhysicsDeltaTime default (not set in DefaultEngine.ini)
C_GAME = 2.0 * ZETA * np.sqrt(K * MASS * 0.25)


def step_matrix(m_eff, c, dt):
    """[x, v] -> next frame, force from this frame's x and v (ComprRate)."""
    return np.array([[1 - dt * dt * K / m_eff, dt * (1 - dt * c / m_eff)],
                     [-dt * K / m_eff, 1 - dt * c / m_eff]])


def worst(m_eff, capped, fps):
    dt = min(1.0 / fps, 0.05)        # TickComponent's Dt (ComprRate divides by it)
    dp = min(1.0 / fps, MAX_PHYS_DT)  # the step Chaos actually integrates
    c = min(C_GAME, ROLL_CORNER_MASS / dt) if capped else C_GAME
    ev = np.linalg.eigvals(step_matrix(m_eff, c * dp / dt, dp))
    return np.abs(ev).max(), min(e.real for e in ev if abs(e.imag) < 1e-9) if np.isreal(ev).any() else 0.0


def main():
    print(f"roll corner mass {ROLL_CORNER_MASS:.0f} kg vs heave corner {MASS / 4:.0f} kg; C {C_GAME:.0f}")
    print(" fps | roll old: |eig| min-real | roll capped: |eig| min-real | heave capped |eig|")
    for fps in (60, 45, 35, 30, 25, 22, 20):
        ro, rc, hc = worst(ROLL_CORNER_MASS, False, fps), worst(ROLL_CORNER_MASS, True, fps), worst(MASS / 4, True, fps)
        print(f" {fps:3d} | {ro[0]:6.2f} {ro[1]:+6.2f}        | {rc[0]:6.2f} {rc[1]:+6.2f}           | {hc[0]:6.2f}")
        assert rc[0] < 1.0 and rc[1] >= -1e-6, f"capped roll must be stable with no flip-flop at {fps} fps"
        assert hc[0] < 1.0, f"capped heave must be stable at {fps} fps"
    assert worst(ROLL_CORNER_MASS, False, 30)[1] < -0.4, "the mirror reproduces the old flip-flop"
    assert min(C_GAME, ROLL_CORNER_MASS * 60) == C_GAME, "60 fps keeps the tuned damping"
    print("PASS: suspension stable with no frame-to-frame flip-flop down to 20 fps")


if __name__ == "__main__":
    main()
