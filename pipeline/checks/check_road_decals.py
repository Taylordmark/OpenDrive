"""Road wear decal check (PLAN-VISUALS Track C). Mirrors
UODRoadDecals::PlaceSpots (OpenDriveUE/Source/OpenDriveUE/ODRoadDecals.cpp;
constants are parsed from it so the mirror can't drift) and asserts:
  - same chunk -> same decals (seed comes from the chunk's own data)
  - at most one decal per slot, slots >= DecalSlotM*(1-2*Jitter) apart
  - every decal sits inside the pavement minus EdgeMarginM
  - no tire marks on a straight road; on a serpentine, some, all near
    corner entries/exits (braking zones) and under 8/km
  - long synthetic road density ~ the per-km constants
then runs it over the area's ODR3 road tiles (default deals_gap_test, the
Tail of the Dragon) and writes a scatter + per-chunk density plot.

Usage:  python pipeline/checks/check_road_decals.py [area] [out.png]
"""
import math
import re
import struct
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CPP = (ROOT / "OpenDriveUE/Source/OpenDriveUE/ODRoadDecals.cpp").read_text(encoding="utf-8")


def _c(name):
    m = re.search(rf"constexpr float {name} = ([\d.]+)f;", CPP)
    return float(m.group(1))


def _c3(name):
    m = re.search(rf"constexpr float {name}\[3\] = \{{([^}}]*)\}};", CPP)
    return [float(x.strip().rstrip("f")) for x in m.group(1).split(",")]


SLOT, JIT = _c("DecalSlotM"), _c("DecalSlotJitter")
CRACK, PATCH, MANHOLE = _c3("CrackPerKm"), _c3("PatchPerKm"), _c3("ManholePerKm")
TIRE, CORNER_R, TRACK = _c("TireMarkChance"), _c("CornerRadiusM"), _c("TrackOffsetM")
BRAKE = _c("BrakeZoneM")
MARGIN, MIN_HALF = _c("EdgeMarginM"), _c("MinHalfWidthM")
COND_MIN, COND_RANGE = _c("CondMin"), _c("CondRange")
M32 = 0xFFFFFFFF


def mix(x):
    x &= M32
    x ^= x >> 16
    x = (x * 0x7feb352d) & M32
    x ^= x >> 15
    x = (x * 0x846ca68b) & M32
    x ^= x >> 16
    return x


def rand(seed, slot, salt):
    return (mix(mix(seed ^ mix(slot & M32)) + salt) >> 8) / 16777216.0


def place(v, uv, uv1, surface, cls, lanes):
    """-> list of dicts (kind, pos, arc, lateral, extent, half_w)."""
    if surface != 0:
        return []
    inner = np.flatnonzero((uv[:, 0] > 1e-3) & (uv[:, 0] < 1 - 1e-3))
    nst = (inner[0] if len(inner) else len(uv)) // 7
    half_w = float(uv1[2, 0]) if nst else 0.0
    if nst < 2 or half_w < MIN_HALF:
        return []
    lanes = max(1, lanes)
    lane_w = 2 * half_w / lanes
    band = 0 if cls <= 2 else 1 if cls <= 4 else 2
    f100 = np.float32(100)
    seed = mix(((math.floor(np.float32(v[0, 0]) / f100) * 73856093) & M32)
               ^ ((math.floor(np.float32(v[0, 1]) / f100) * 19349663) & M32))
    cond = COND_MIN + COND_RANGE * rand(seed, 0, 99)
    km = SLOT / 1000
    p_crack = CRACK[band] * km * cond
    p_patch = p_crack + PATCH[band] * km * cond
    p_man = p_patch + MANHOLE[band] * km
    p_tire = p_man + TIRE
    v = v.astype(np.float64)
    ctr = 0.5 * (v[2:nst * 7:7] + v[3:nst * 7:7])
    arc = uv[2:nst * 7:7, 1].astype(np.float64)
    junc = uv1[2:nst * 7:7, 1]

    def corner(s):
        a, b, c, d = ctr[max(s - 1, 0)], ctr[s], ctr[s + 1], ctr[min(s + 2, nst - 1)]
        i, m, o = (b - a)[:2], (c - b)[:2], (d - c)[:2]
        li, lm, lo = np.linalg.norm(i), np.linalg.norm(m), np.linalg.norm(o)
        if li < 1 or lo < 1:
            return False
        turn = math.acos(max(-1.0, min(1.0, float(i @ o) / (li * lo))))
        return turn * CORNER_R > (0.5 * li + lm + 0.5 * lo) / 100

    in_j = [junc[s] > 0.01 or junc[s + 1] > 0.01 for s in range(nst - 1)]
    is_c = [corner(s) for s in range(nst - 1)]
    edges = [arc[s] for s in range(1, nst - 1) if is_c[s] != is_c[s - 1] or in_j[s] != in_j[s - 1]]
    out = []
    for s in range(nst - 1):
        v0, v1 = arc[s], arc[s + 1]
        if in_j[s] or abs(v1 - v0) < 0.01:
            continue
        lo, hi = min(v0, v1), max(v0, v1)
        k = math.ceil(lo / SLOT)
        while k * SLOT < hi:
            r = rand(seed, k, 1)
            brake = any(abs(k * SLOT - e) < BRAKE for e in edges)
            kind = 0 if r < p_crack else 1 if r < p_patch else 2 if r < p_man else \
                3 if (brake and r < p_tire) else -1
            if kind >= 0:
                a = k * SLOT + (rand(seed, k, 2) - 0.5) * 2 * JIT * SLOT
                t = (a - v0) / (v1 - v0)
                lane = min(lanes - 1, int(rand(seed, k, 3) * lanes))
                lane_c = -half_w + lane_w * (lane + 0.5)
                r4, r5, r6 = rand(seed, k, 4), rand(seed, k, 5), rand(seed, k, 6)
                lateral = lane_c
                if kind == 0:
                    if r4 < 0.3:
                        extent = 0.45 * lane_w
                    else:
                        extent = 0.3
                        lateral = lane_c + (-TRACK if r6 < 0.5 else TRACK)
                elif kind == 1:
                    extent = 0.6 + max(0.0, 0.45 * lane_w - 0.6) * r6
                    lateral = lane_c + (r4 - 0.5) * 0.6
                elif kind == 2:
                    extent = 0.4
                    lateral = lane_c + (r6 - 0.5) * 1.2
                else:
                    extent = 1.05
                room = half_w - MARGIN - extent
                if room >= 0:
                    lat = max(-room, min(room, lateral))
                    right = v[s * 7 + 3] - v[s * 7 + 2]
                    right = right / max(np.linalg.norm(right), 1e-6)
                    pos = ctr[s] + (ctr[s + 1] - ctr[s]) * t + right * lat * 100
                    out.append(dict(kind=kind, pos=pos, arc=a, slot=k, lateral=lat,
                                    extent=extent, half_w=half_w))
            k += 1
    return out


def chunks(path):
    """ODR3 tile -> (verts, uv0, uv1, surface, class, lanes) per chunk."""
    buf = path.read_bytes()
    off = 20
    (n,) = struct.unpack_from("<I", buf, off); off += 4
    for _ in range(n):
        vc, tc, flags, cls, lanes = struct.unpack_from("<IIBBB", buf, off); off += 11
        v = np.frombuffer(buf, "<f4", vc * 3, off).reshape(-1, 3); off += vc * 12
        uv = np.frombuffer(buf, "<f4", vc * 2, off).reshape(-1, 2); off += vc * 8
        uv1 = np.frombuffer(buf, "<f4", vc * 2, off).reshape(-1, 2); off += vc * 8
        off += tc * 12
        yield v, uv, uv1, (flags >> 1) & 3, cls, lanes


def synthetic(centers, half_w=3.5, junction=None):
    """Ribbon chunk along centers (n, 3) cm: 7 verts/station, pave cols 2/3."""
    n = len(centers)
    d = np.gradient(centers, axis=0)
    right = np.stack([d[:, 1], -d[:, 0], np.zeros(n)], -1)
    right /= np.linalg.norm(right, axis=1, keepdims=True)
    arc = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(centers, axis=0), axis=1))]) / 100
    lat = np.array([-1.3, -1.1, -1, 1, 1.1, 1.3, 1.5])
    us = np.array([0, 0, 0, 1, 1, 1, 1], np.float32)
    v = (centers[:, None, :] + right[:, None, :] * (lat * half_w * 100)[None, :, None]).reshape(-1, 3)
    uv = np.stack([np.tile(us, n), np.repeat(arc, 7)], -1).astype(np.float32)
    j = np.zeros(n) if junction is None else junction
    uv1 = np.stack([np.full(n * 7, half_w), np.repeat(j, 7)], -1).astype(np.float32)
    return v.astype(np.float32), uv, uv1


def check(spots):
    for d in spots:
        assert abs(d["lateral"]) + d["extent"] <= d["half_w"] - MARGIN + 1e-6, d
    slots = [d["slot"] for d in spots]
    assert len(slots) == len(set(slots)), "two decals in one slot"
    arcs = sorted(d["arc"] for d in spots)
    assert all(b - a >= SLOT * (1 - 2 * JIT) - 1e-6 for a, b in zip(arcs, arcs[1:])), "decals too close"


def self_test():
    x = np.arange(0, 10_000_00 + 1, 500.0)                       # 10 km straight, 5 m stations
    straight = np.stack([x + 123456, np.full_like(x, -98765), np.zeros_like(x)], -1)
    v, uv, uv1 = synthetic(straight)
    a, b = place(v, uv, uv1, 0, 6, 2), place(v, uv, uv1, 0, 6, 2)
    assert [(d["kind"], d["arc"]) for d in a] == [(d["kind"], d["arc"]) for d in b], "not deterministic"
    check(a)
    kinds = np.bincount([d["kind"] for d in a], minlength=4)
    assert kinds[3] == 0, "tire marks on a straight road"
    seed = mix(((math.floor(np.float32(v[0, 0]) / np.float32(100)) * 73856093) & M32)
               ^ ((math.floor(np.float32(v[0, 1]) / np.float32(100)) * 19349663) & M32))
    cond = COND_MIN + COND_RANGE * rand(seed, 0, 99)
    for kind, per_km, c in ((0, CRACK[2], cond), (1, PATCH[2], cond), (2, MANHOLE[2], 1.0)):
        want = per_km * c * 10
        assert abs(kinds[kind] - want) < 4 * math.sqrt(want) + 2, (kind, kinds[kind], want)
    assert place(v, uv, uv1, 1, 6, 2) == [], "decals on gravel"
    assert place(*synthetic(straight, junction=np.ones(len(x))), 0, 6, 2) == [], "decals in a junction"
    # serpentine: 100 m straights between 25 m-radius hairpins (5 m stations)
    head, pts, ends, a = 0.0, [np.array([0.0, 0.0])], [], 0.0
    for i in range(60):
        for length, k in ((100, 0.0), (25 * math.pi, (1 if i % 2 else -1) / 25)):
            for _ in range(int(length / 5)):
                head += k * 5
                pts.append(pts[-1] + 500 * np.array([math.cos(head), math.sin(head)]))
            a += int(length / 5) * 5
            ends.append(a)
    serp = np.concatenate([np.array(pts), np.zeros((len(pts), 1))], 1) + [5e6, 3e6, 0]
    c = place(*synthetic(serp), 0, 6, 2)
    check(c)
    tire = [d["arc"] for d in c if d["kind"] == 3]
    assert tire, "no tire marks at serpentine corner entries"
    far = [t for t in tire if min(abs(t - e) for e in ends) > BRAKE + 3 * SLOT]   # corner flag ~1 station off
    assert not far, f"tire marks away from corner entries: {far}"
    per_km = len(tire) / (a / 1000)
    assert per_km < 8, f"{per_km:.1f} tire marks/km on a serpentine"
    print(f"self-test ok: straight 10 km -> {kinds.tolist()} (cond {cond:.2f}), "
          f"serpentine {a / 1000:.1f} km -> {len(tire)} tire marks ({per_km:.1f}/km)")


def main():
    self_test()
    area = sys.argv[1] if len(sys.argv) > 1 else "deals_gap_test"
    out_png = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.gettempdir()) / "road_decals.png"
    tiles = sorted((ROOT / "OpenDriveUE/Content/OpenDrive/MasksV2").glob(f"roadv3_{area}_tile_*.bin"))
    assert tiles, f"no ODR3 tiles for {area}"
    roads, spots, dens, dens_len = [], [], [], []
    for p in tiles:
        for v, uv, uv1, surf, cls, lanes in chunks(p):
            s = place(v, uv, uv1, surf, cls, lanes)
            again = place(v, uv, uv1, surf, cls, lanes)
            assert [(d["kind"], d["arc"]) for d in s] == [(d["kind"], d["arc"]) for d in again]
            check(s)
            spots += s
            roads.append(v[2::7, :2] / 100)
            length = np.ptp(uv[:, 1]) / 1000 if len(uv) else 0
            dens_len.append(length if surf == 0 else 0)
            if length > 0.2 and surf == 0:
                dens.append(len(s) / length)
    kinds = np.bincount([d["kind"] for d in spots], minlength=4)
    paved_km = sum(dens_len)
    print(f"{area}: tire marks {kinds[3] / paved_km:.2f}/km over {paved_km:.0f} paved km")
    assert kinds[3] / paved_km < 2, "too many tire marks"   # real roads: a few per km on twisty ones only
    print(f"{area}: {len(tiles)} tiles, {len(spots)} decals (crack/patch/manhole/tire {kinds.tolist()}), "
          f"median {np.median(dens):.1f}/km over {len(dens)} paved chunks > 200 m")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(16, 8), gridspec_kw={"width_ratios": [2, 1]})
    for r in roads:
        ax.plot(r[:, 0], r[:, 1], color="0.75", lw=0.5)
    for k, (name, col) in enumerate((("crack", "k"), ("patch", "tab:blue"),
                                     ("manhole", "tab:green"), ("tire marks", "tab:red"))):
        p = np.array([d["pos"][:2] / 100 for d in spots if d["kind"] == k]).reshape(-1, 2)
        ax.scatter(p[:, 0], p[:, 1], s=4, c=col, label=f"{name} ({len(p)})")
    ax.set_aspect("equal"); ax.legend(); ax.set_title(f"{area} road decals (m)")
    bx.hist(dens, bins=40); bx.set_xlabel("decals / km per paved chunk"); bx.set_title("density")
    fig.tight_layout(); fig.savefig(out_png, dpi=110)
    print(f"wrote {out_png}")


if __name__ == "__main__":
    main()
