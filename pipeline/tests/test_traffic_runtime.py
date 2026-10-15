"""AODTrafficManager (PLAN-REALISM 3c, 2026-10-14): C++ density + IDM mirror.

Parses the mirrored constants out of ODTrafficManager.cpp, checks them against
build_traffic_demand, then checks the fundamental-diagram target density
(jam at rush on an I-5-like road, free at 3 am, continuous at q = c) and the
IDM step (no overlap behind a stopped car, reaches the limit on an open road,
congested equilibrium flow at the FD density stays under capacity).

Run:  python pipeline/tests/test_traffic_runtime.py"""
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_traffic_demand as td

CPP = Path(__file__).resolve().parents[2] / "OpenDriveUE/Source/OpenDriveUE/ODTrafficManager.cpp"
src = CPP.read_text(encoding="utf-8")
C = {m[0]: float(m[1]) for m in re.findall(r"constexpr float (\w+) = ([\d.]+)f;", src)}
cap = re.search(r"CapacityVphpl\[7\] = \{([^}]*)\}", src)[1]
CAP = tuple(float(x.strip().rstrip("f")) for x in cap.split(","))

assert C["TruckPce"] == td.TRUCK_PCE, C["TruckPce"]
assert CAP == tuple(float(x) for x in td.CAPACITY_VPHPL), CAP


def target_density(q, c, vf, lanes, pce):
    """Mirror of ODTraffic::TargetDensity -> (veh/km all lanes, km/h)."""
    if q <= c:
        k, v = q / vf, vf
    else:
        kjam = lanes * C["KJamPerLaneKm"]
        w = c / max(kjam - c / vf, 1.0)
        qc = c * c / q
        k = kjam - qc / w
        v = qc / k
    return k / pce, v


def idm(v, v0, gap, lv):
    """Mirror of AODTrafficManager::Idm, SI units."""
    a, b = C["IdmAccel"], C["IdmDecel"]
    s_star = C["IdmMinGap"] + max(0.0, v * C["IdmHeadway"] + v * (v - lv) / (2 * np.sqrt(a * b)))
    acc = a * (1 - (v / max(v0, 1.0)) ** 4 - (s_star / max(gap, 0.1)) ** 2)
    return max(acc, -C["MaxDecel"])


def step(v, acc, dt):
    nv = max(0.0, v + acc * dt)
    return nv, 0.5 * (v + nv) * dt


# --- density: I-5-like urban 3-lane freeway, 160k AADT, 60 % peak direction
URBAN, INBOUND = 1, 4
lanes, cls, truck = 3, 0, 10
pce = 1 + truck / 100 * (td.TRUCK_PCE - 1)
c = td.capacity_vph(cls, lanes)
vf = 105.0
q_rush = td.demand_vph(160000, 60, URBAN | INBOUND, False, 17, 1, truck)   # outbound, evening, Tue
q_night = td.demand_vph(160000, 60, URBAN | INBOUND, False, 3, 1, truck)
assert q_rush > c, (q_rush, c)
k, v = target_density(q_rush, c, vf, lanes, pce)
assert v < 40.0 and k / lanes > 40.0, (k, v)          # stuck: a crawl, packed lanes
k3, v3 = target_density(q_night, c, vf, lanes, pce)
assert v3 == vf and k3 / lanes < 10.0, (k3, v3)
ka, va = target_density(c, c, vf, lanes, 1.0)
kb, vb = target_density(c * (1 + 1e-7), c, vf, lanes, 1.0)
assert abs(ka - kb) < 0.05 and abs(va - vb) < 0.05, (ka, kb, va, vb)

# --- IDM: open road reaches the limit
dt = C["StepS"]
vel = 0.0
for _ in range(int(90 / dt)):
    vel, _ = step(vel, idm(vel, 30.0, 1e6, 0.0), dt)
assert vel > 28.0, vel

# --- IDM: 25 m/s toward a stopped car 120 m ahead: stops, never touches
vel, gap = 25.0, 120.0
for _ in range(int(60 / dt)):
    vel, ds = step(vel, idm(vel, 30.0, gap, 0.0), dt)
    gap -= ds
    assert gap > 0.5, gap
assert vel < 0.05 and gap < C["IdmMinGap"] + 1.0, (vel, gap)

# --- IDM equilibrium at the FD jam spacing: flow stays at or under capacity
spacing = 1000.0 / (k / lanes)                         # m per car, one lane
gap_eq = spacing - C["CarLength"]
v0 = v / 3.6
lo, hi = 0.0, v0
for _ in range(60):   # bisection on idm(v, v0, gap_eq, v) == 0
    mid = 0.5 * (lo + hi)
    lo, hi = (mid, hi) if idm(mid, v0, gap_eq, mid) > 0 else (lo, mid)
ve = lo
flow_lane = ve * 3.6 * (k / lanes)
assert 0.0 < ve <= v0 and flow_lane < td.CAPACITY_VPHPL[cls] * 1.2, (ve, flow_lane)

# --- the C++ reads HOUR_SHARE / DOW_FACTOR from the ODT1 file: check a real one matches
bins = sorted(td.MASKS.glob("traffic_*_test.bin"))
for p in bins[:1]:
    raw = p.read_bytes()
    assert raw[:4] == b"ODT1"
    share = np.frombuffer(raw, "<f4", 96, 8).reshape(4, 24)
    dow = np.frombuffer(raw, "<f4", 7, 8 + 384)
    assert np.allclose(share, td.HOUR_SHARE, atol=1e-6) and np.allclose(dow, td.DOW_FACTOR, atol=1e-6), p.name

print(f"ok: rush q={q_rush:.0f} > c={c:.0f} -> {k / lanes:.0f} veh/km/lane at {v:.0f} km/h "
      f"(IDM eq {ve * 3.6:.0f} km/h, {flow_lane:.0f} veh/h/lane); 3am {k3 / lanes:.1f} veh/km/lane; "
      f"{len(bins)} ODT1 bin(s), {'checked ' + bins[0].name if bins else 'none to check'}")
