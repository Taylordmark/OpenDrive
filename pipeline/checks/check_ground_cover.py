"""Offline check of the Realistic ground cover placement (PLAN-VISUALS.md
Track K). A line-for-line Python mirror of ODGroundCover::Place
(OpenDriveUE/Source/OpenDriveUE/ODGroundCover.cpp), incl. UE's FRandomStream,
run on synthetic 16 m grass cells beside a straight road. Asserts:
determinism, shoulder clearance, nothing on non-terrain lattice squares,
per-cell caps, the road band, biome dryness and the slope cutoff; prints
instance counts per cell and per 120 m grass ring at each Model Detail level.
Change both files together.

    python pipeline/checks/check_ground_cover.py
"""

import math
import struct

# --- mirror of ODGroundCover.cpp --------------------------------------------
BIOMES = ["DeciduousForest", "EvergreenForest", "Chaparral", "DesertScrub", "PlainsGrassland", "Wetland",
          "AlpineBare", "ArcticTundra", "Farmland", "Savanna", "Rainforest", "CoastalDune",
          "CanyonBadlands", "RockyFoothill"]
BIOME_TABLE = [
    [0.50, 0.80, 0.60, 0.30, 0.05], [0.35, 0.50, 0.60, 0.50, 0.10], [0.40, 0.30, 0.90, 0.80, 0.65],
    [0.30, 0.15, 0.70, 1.00, 0.90], [1.00, 0.70, 0.30, 0.20, 0.30], [1.00, 0.90, 0.50, 0.10, 0.00],
    [0.15, 0.15, 0.10, 1.00, 0.40], [0.20, 0.20, 0.20, 0.60, 0.50], [0.80, 0.80, 0.30, 0.20, 0.25],
    [1.00, 0.40, 0.40, 0.40, 0.70], [0.60, 1.00, 0.80, 0.10, 0.00], [0.50, 0.10, 0.30, 0.20, 0.60],
    [0.15, 0.10, 0.40, 1.00, 0.80], [0.40, 0.30, 0.40, 0.90, 0.35],
]
PER_SQM = [0.30, 0.12, 0.015, 0.10]
DETAIL_DENSITY = [0.5, 1.0, 1.6]
SLOTS = ["grass", "weed", "bush", "sage", "pebble"]
BASE_CAP = [96, 48, 6, 6, 40]
BAND_FULL, BAND_END, FILL = 3000.0, 6000.0, 0.2
SHRUB_CLEAR = 150.0
M32 = 0xFFFFFFFF


def cap_for(slot, detail):
    return math.ceil(BASE_CAP[slot] * DETAIL_DENSITY[detail])


def cell_seed(kx, ky, seed):
    return ((kx * 73856093) & M32) ^ ((ky * 19349663) & M32) ^ ((seed * 83492791) & M32) ^ 0x6C0FE4


class RandomStream:   # FRandomStream
    def __init__(self, seed):
        self.s = seed & M32

    def frand(self):
        self.s = (self.s * 196314165 + 907633515) & M32
        return struct.unpack("<f", struct.pack("<I", 0x3F800000 | (self.s >> 9)))[0] - 1.0


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def place(key, x0, y0, step, n, z, good, biome, detail, seed, shoulder, road_edge):
    b = BIOME_TABLE[min(biome, 13)]
    area = (step / 100.0) ** 2
    half_diag = step * 0.7072
    w = n + 1
    rng = RandomStream(cell_seed(key[0], key[1], seed))
    out = [[] for _ in SLOTS]
    for sy in range(n):
        for sx in range(n):
            i00 = sy * w + sx
            i10, i01, i11 = i00 + 1, i00 + w, i00 + w + 1
            if not (good[i00] and good[i10] and good[i01] and good[i11]):
                continue
            z00, z10, z01, z11 = z[i00], z[i10], z[i01], z[i11]
            slope = math.hypot((z10 + z11 - z00 - z01) * 0.5, (z01 + z11 - z00 - z10) * 0.5) / step
            road = road_edge(x0 + (sx + 0.5) * step, y0 + (sy + 0.5) * step)
            band = 1.0 + (FILL - 1.0) * clamp((road - BAND_FULL) / (BAND_END - BAND_FULL), 0.0, 1.0)
            for k in range(4):
                gate = clamp(1.5 - slope, 0, 1) if k == 3 else clamp(1.2 - 2.0 * slope, 0, 1)
                expected = PER_SQM[k] * DETAIL_DENSITY[detail] * b[k] * band * gate * area
                count = math.floor(expected) + (1 if rng.frand() < expected - math.floor(expected) else 0)
                for _ in range(count):
                    u, v, yaw = rng.frand(), rng.frand(), rng.frand() * 360.0
                    s, r1, r2 = rng.frand(), rng.frand(), rng.frand()
                    x, y = x0 + (sx + u) * step, y0 + (sy + v) * step
                    clear = shoulder + (SHRUB_CLEAR if k == 2 else 0.0)
                    if road - half_diag < clear and road_edge(x, y) < clear:
                        continue
                    zb = (z00 + (z10 - z00) * u) * (1 - v) + (z01 + (z11 - z01) * u) * v
                    dry = clamp(b[4] + (r1 - 0.5) * 0.6, 0.0, 1.0)
                    slot = [0, 1, 3 if r2 < b[4] else 2, 4][k]
                    out[slot].append((x, y, zb, yaw, s, dry))
    for s in range(len(SLOTS)):
        c, num = cap_for(s, detail), len(out[s])
        if num > c:
            out[s] = [out[s][j * num // c] for j in range(c)]
    return out


# --- synthetic world: a straight road along X, 8 m wide ----------------------
CELL, N = 1600.0, 8
STEP = CELL / N
HALF_W, SHOULDER, SEED = 400.0, 350.0, 20260715


def road_edge(x, y):
    return max(0.0, abs(y) - HALF_W)


def run_cell(kx, ky, biome, detail, slope=0.0):
    x0, y0 = kx * CELL, ky * CELL
    pts = [(x0 + ix * STEP, y0 + iy * STEP) for iy in range(N + 1) for ix in range(N + 1)]
    z = [slope * y for _, y in pts]
    good = [abs(y) >= HALF_W for _, y in pts]   # the lattice trace hits the road mesh, not terrain
    return place((kx, ky), x0, y0, STEP, N, z, good, biome, detail, SEED, SHOULDER, road_edge), good


def main():
    B = {n: i for i, n in enumerate(BIOMES)}

    # determinism: same key, same scatter; a different key differs
    a, _ = run_cell(3, 1, B["PlainsGrassland"], 1)
    b, _ = run_cell(3, 1, B["PlainsGrassland"], 1)
    c, _ = run_cell(4, 1, B["PlainsGrassland"], 1)
    assert a == b, "not deterministic"
    assert a != c, "neighbour cells identical"

    # clearance, bare squares, caps over a strip of cells across the road
    for biome in range(len(BIOMES)):
        for detail in range(3):
            for ky in range(-6, 6):
                out, good = run_cell(2, ky, biome, detail)
                for s, inst in enumerate(out):
                    assert len(inst) <= cap_for(s, detail), (BIOMES[biome], detail, s, len(inst))
                    clear = SHOULDER + (SHRUB_CLEAR if s in (2, 3) else 0.0)
                    for x, y, *_ in inst:
                        assert road_edge(x, y) >= clear, ("on shoulder", SLOTS[s], y)
                        sx, sy = int((x - 2 * CELL) // STEP), int((y - ky * CELL) // STEP)
                        i = sy * (N + 1) + sx
                        assert good[i] and good[i + 1] and good[i + N + 1] and good[i + N + 2], "on a bare square"

    def mean_count(biome, detail, kys, slope=0.0, kx_range=range(0, 20)):
        tot = [0] * len(SLOTS)
        for kx in kx_range:
            for ky in kys:
                out, _ = run_cell(kx, ky, biome, detail, slope)
                for s in range(len(SLOTS)):
                    tot[s] += len(out[s])
        return [t / (len(kx_range) * len(kys)) for t in tot]

    # road band: 10-25 m from the edge vs 70-90 m
    near = sum(mean_count(B["PlainsGrassland"], 1, [1]))
    far = sum(mean_count(B["PlainsGrassland"], 1, [5]))
    assert near > 3.5 * far > 0, (near, far)

    # biome: desert dry and shrubby-sage, wetland lush; steep ground bare of cards
    def mean_dry(biome):
        out, _ = run_cell(1, 1, biome, 2)
        d = [p[5] for p in out[0] + out[1]]
        return sum(d) / len(d)
    assert mean_dry(B["DesertScrub"]) > 0.7 and mean_dry(B["Wetland"]) < 0.2
    steep = mean_count(B["PlainsGrassland"], 2, [1, 2], slope=0.7)
    assert steep[0] == steep[1] == steep[2] == steep[3] == 0 and steep[4] > 0, steep

    # report: per 16 m cell in the full band (ky=1 -> 12-28 m from the road
    # edge) and the worst cell, then the whole grass ring (120 m radius,
    # car on the road) - ODForestManager builds 2 cells per tick
    print(f"{'detail':8}{'biome':17}" + "".join(f"{s:>8}" for s in SLOTS) + f"{'cell':>8}{'ring':>9}")
    for detail, dname in enumerate(["Standard", "High", "Ultra"]):
        for name in ("PlainsGrassland", "DeciduousForest", "DesertScrub", "AlpineBare"):
            m = mean_count(B[name], detail, [1])
            ring = 0.0
            for kx in range(-8, 8):
                for ky in range(-8, 8):
                    if math.hypot((kx + 0.5) * CELL, (ky + 0.5) * CELL) <= 12000 + CELL * 0.71:
                        ring += sum(len(o) for o in run_cell(kx, ky, B[name], detail)[0])
            print(f"{dname:8}{name:17}" + "".join(f"{v:8.1f}" for v in m) + f"{sum(m):8.1f}{ring:9.0f}")
        caps = [cap_for(s, detail) for s in range(len(SLOTS))]
        print(f"{dname:8}{'cap':17}" + "".join(f"{v:8d}" for v in caps) + f"{sum(caps):8d}")
    print("check_ground_cover: OK")


if __name__ == "__main__":
    main()
