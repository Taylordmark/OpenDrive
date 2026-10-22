"""Far forest (realism eval T3): one canopy card per ESA WorldCover tree cell,
over every owned terrain tile, so forested hills read as forest out to
AODForestManager::FarForestRadius instead of only the 120 m road band within
500 m of the car.

WorldCover 2021 v200 tree class (10) at its 20 m overview - the same cached
COG reads classify_biomes.fetch_worldcover() already makes (CC-BY 4.0, (c) ESA
WorldCover project 2021). Each tree cell gets a deterministic jitter, the
SHIPPED terrain tile's height at that jittered point (same "sample what's
rendered" rule as build_forest_elevation.py), a leaf type from the nearest
road_biomes.json point, and a flag for "inside the near forest's road band"
(the runtime hides those cards while the near scatter covers the band).

Must run after the terrain bake + classify_biomes.py; re-run after any
terrain rebake (heights come from the tiles on disk).

Usage:  OD_AREA=<area> python build_far_forest.py
Writes  OpenDriveUE/Content/OpenDrive/Masks/farforest_<AREA>_test.bin
        'ODF1' + uint32 nTiles, then per tile: int32 tx, int32 ty, uint32 n,
        n * (uint16 cell = iy*CELLS+ix, int8 jx_dm, int8 jy_dm... see REC)
"""

import json
import struct
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from bake_job import in_outline
from test_areas import AREA_NAME, TERRAIN_TILE_SIZE_CM, lonlat_bbox, owns, to_lonlat

CELL_M = 20.0                        # WorldCover overview 1 pixel
TILE_M = TERRAIN_TILE_SIZE_CM / 100.0
CELLS = int(TILE_M / CELL_M)         # 100 per 2 km tile side
WC_TREE, WC_MANGROVE = 10, 95
PAVEMENT_M = 18.0                    # no card this close to a road station
BAND_M = 130.0                       # AODForestManager::ForestBandCm (120 m) + margin
JITTER_M = 0.4 * CELL_M
# leaf types (runtime card meshes): 0 needle (pine_needle cell), 1 broadleaf (aspen_leaf)
NEEDLE_BIOMES = {1, 6, 7, 13}        # EvergreenForest, AlpineBare, ArcticTundra, RockyFoothill
FLAG_BAND = 0x80
FLAG_STREET = 0x40                   # low bits = AODForestManager::TreeMeshes species index
# street trees (town streets: build_sidewalks' town mask + classes)
STREET_SPACING_STATIONS = 3          # 5 m stations -> one candidate per 15 m per side
STREET_KEEP = 0.7
STREET_BEHIND_WALK_M = 1.2           # past the 1.8 m walk, on the verge
STREET_CLEAR_M = 2.5                 # trunk this far off any other road's pavement
STREET_BUILDING_M = 8.0              # off any building centroid
STREET_DRIVEWAY_M = 6.0
SP_BLOB, SP_PALM, SP_BROADLEAF = 0, 7, 9
REC = struct.Struct("<HbbhB")        # cell, jx (dm/5 = 0.5 m units), jy, z (dm), flags

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
AREA_DIR = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test"


def cell_jitter(tx, ty, n_cells=CELLS):
    """Deterministic per-cell jitter (m), in 0.5 m steps so it packs into int8."""
    rng = np.random.default_rng(((tx * 73856093) ^ (ty * 19349663)) & 0xFFFFFFFF)
    j = rng.uniform(-JITTER_M, JITTER_M, size=(2, n_cells * n_cells))
    return np.round(j * 2.0) / 2.0


def pack_tile(tx, ty, cells, jx, jy, z_cm, flags):
    """One tile's record block; z_cm -> int16 decimetres (asserted in range)."""
    z_dm = np.round(np.asarray(z_cm) / 10.0)
    assert np.all(np.abs(z_dm) < 32767), "far-forest Z out of int16 dm range"
    out = [struct.pack("<iiI", tx, ty, len(cells))]
    for c, a, b, z, f in zip(cells, jx, jy, z_dm, flags):
        out.append(REC.pack(int(c), int(round(a * 2)), int(round(b * 2)), int(z), int(f)))
    return b"".join(out)


def street_trees():
    """(x_m, y_m UE, species) for town-street trees: both sides of every
    sidewalk-class town street, STREET_SPACING_STATIONS apart, a random
    STREET_KEEP of them, just past the sidewalk. Palms/broadleaf in the
    tropics, deciduous (aspen slot) elsewhere."""
    import build_retaining_walls as brw
    import build_sidewalks as bsw
    import build_terrain_mesh_tiles as btmt
    from build_road_furniture import RAW, load_town_mask
    segs = json.loads(btmt.STATIONS_PATH.read_text())["segments"]
    cat = lambda k, d=0: np.concatenate([np.asarray(sg.get(k, [d] * len(sg["east_m"]))) for sg in segs])
    east, north = cat("east_m").astype(float), cat("north_m").astype(float)
    hw = brw.load_station_half_widths()
    hwy = np.array([h for sg in segs for h in sg["highway_class"]])
    stride = np.concatenate([np.arange(len(sg["east_m"])) % STREET_SPACING_STATIONS == 1 for sg in segs])
    base = (stride & np.isin(hwy, list(bsw.SIDEWALK_CLASSES)) & load_town_mask(east, north)
            & ~cat("is_struct", False).astype(bool) & ~cat("is_tunnel", False).astype(bool)
            & ~cat("in_junction_box", False).astype(bool)
            & (cat("junction_suppress", 0.0).astype(float) < 0.05))
    rxy = btmt.station_bank(east, north)[:, 1:]          # UE-frame unit right normal
    tree = cKDTree(np.c_[east, north])
    idx = np.arange(len(east))
    blds = json.loads((RAW / "osm_buildings.json").read_text()) if (RAW / "osm_buildings.json").exists() else []
    ll = np.array([np.mean(b["nodes"], axis=0) for b in blds if b["nodes"]]).reshape(-1, 2)
    bld = cKDTree(np.c_[to_local_ll(ll)]) if len(ll) else None
    drv = [p for p, _ in bsw.fetch_service_ways()]
    drv = cKDTree(np.concatenate(drv)) if drv else None
    lat0 = float(np.mean(cat("lat", 0.0)))
    tropical = abs(lat0) < 23.5
    rng = np.random.default_rng(4242)
    out = []
    for sgn in (-1.0, 1.0):
        o = hw + bsw.WALK_W_M + STREET_BEHIND_WALK_M
        px, py = east + rxy[:, 0] * sgn * o, north - rxy[:, 1] * sgn * o
        ok = base & (rng.random(len(east)) < STREET_KEEP)
        ok &= bsw.clear_of_roads(px, py, tree, hw + STREET_CLEAR_M - bsw.ROAD_CLEAR_M, idx)
        if bld is not None:
            ok &= bld.query(np.c_[px, py])[0] > STREET_BUILDING_M
        if drv is not None:
            ok &= drv.query(np.c_[px, py])[0] > STREET_DRIVEWAY_M
        r = rng.random(len(east))
        sp = np.where(tropical, np.where(r < 0.6, SP_PALM, SP_BROADLEAF), SP_BLOB)
        out.append(np.c_[px[ok], -py[ok], sp[ok]])
    return np.concatenate(out) if out else np.zeros((0, 3))


def to_local_ll(latlon):
    from test_areas import to_local
    return to_local(latlon[:, 1], latlon[:, 0])


def read_bin(path):
    """Inverse of the writer - {(tx,ty): [(cell, jx_m, jy_m, z_cm, flags)]}."""
    b = Path(path).read_bytes()
    assert b[:4] == b"ODF1"
    n_tiles, = struct.unpack_from("<I", b, 4)
    off, out = 8, {}
    for _ in range(n_tiles):
        tx, ty, n = struct.unpack_from("<iiI", b, off)
        off += 12
        recs = []
        for _ in range(n):
            c, a, bb, z, f = REC.unpack_from(b, off)
            off += REC.size
            recs.append((c, a / 2.0, bb / 2.0, z * 10.0, f))
        out[(tx, ty)] = recs
    return out


def main():
    from bin_tile_io import parse_terrain_tile_bin
    from classify_biomes import fetch_worldcover, sample_nearest_ll
    from road_mesh_common import sample_mesh_heights

    tiles = []
    for p in OUT_DIR.glob(f"terrain_{AREA_NAME}_test_tile_*.bin"):
        tx, ty = (int(v) for v in p.stem.rsplit("_", 2)[1:])
        if owns(tx * TERRAIN_TILE_SIZE_CM, ty * TERRAIN_TILE_SIZE_CM):
            tiles.append((tx, ty))
    tiles.sort()
    if not tiles:
        raise SystemExit(f"no terrain tiles for {AREA_NAME} - run build_terrain_mesh_tiles.py first")
    txs, tys = np.array(tiles).T
    # UE y = -north: tile ty spans north in (-(ty+1)*TILE_M, -ty*TILE_M]
    s, w, n, e = lonlat_bbox(txs.min() * TILE_M, -(tys.max() + 1) * TILE_M,
                             (txs.max() + 1) * TILE_M, -tys.min() * TILE_M)
    wc, wc_bounds = fetch_worldcover(w - 0.01, s - 0.01, e + 0.01, n + 0.01, ovr=1)

    stations = json.loads((AREA_DIR / "road_stations.json").read_text())
    st_xy = np.concatenate([np.c_[seg["east_m"], seg["north_m"]] for seg in stations["segments"]])
    st_tree = cKDTree(st_xy)
    bio = json.loads((AREA_DIR / "road_biomes.json").read_text())
    bio_tree = cKDTree(np.c_[np.array(bio["x_cm"]) / 100.0, -np.array(bio["y_cm"]) / 100.0])
    bio_needle = np.isin(np.array(bio["biome"]), list(NEEDLE_BIOMES))

    street = street_trees()
    st_tile = np.floor(street[:, :2] / TILE_M).astype(int)
    print(f"{len(street)} street tree(s)")

    ii = np.arange(CELLS * CELLS)
    blocks, total, n_band, n_forest, n_street = [], 0, 0, 0, 0
    for tx, ty in tiles:
        jx, jy = cell_jitter(tx, ty)
        x_m = tx * TILE_M + ((ii % CELLS) + 0.5) * CELL_M + jx      # UE x = east
        y_m = ty * TILE_M + ((ii // CELLS) + 0.5) * CELL_M + jy     # UE y = -north
        lon, lat = to_lonlat(x_m, -y_m)
        cls = sample_nearest_ll(wc, wc_bounds, lon, lat)
        keep = np.isin(cls, (WC_TREE, WC_MANGROVE))
        n_forest += int(keep.sum())
        d, _ = st_tree.query(np.c_[x_m, -y_m], distance_upper_bound=BAND_M)
        keep &= d > PAVEMENT_M
        idx = np.where(keep)[0]
        _, bi = bio_tree.query(np.c_[x_m[idx], -y_m[idx]]) if len(idx) else (None, np.zeros(0, int))
        flags = np.where(bio_needle[bi], 0, 1) | np.where(np.isfinite(d[idx]), FLAG_BAND, 0)
        cx, cy = x_m[idx], y_m[idx]
        sjx, sjy = jx[idx], jy[idx]
        mine = street[(st_tile[:, 0] == tx) & (st_tile[:, 1] == ty)]
        if len(mine):
            lx, ly = mine[:, 0] - tx * TILE_M, mine[:, 1] - ty * TILE_M
            ci = np.clip((lx // CELL_M).astype(int), 0, CELLS - 1)
            cj = np.clip((ly // CELL_M).astype(int), 0, CELLS - 1)
            idx = np.r_[idx, cj * CELLS + ci]
            sjx = np.r_[sjx, np.round((lx - (ci + 0.5) * CELL_M) * 2) / 2]
            sjy = np.r_[sjy, np.round((ly - (cj + 0.5) * CELL_M) * 2) / 2]
            cx, cy = np.r_[cx, mine[:, 0]], np.r_[cy, mine[:, 1]]
            flags = np.r_[flags, FLAG_STREET | mine[:, 2].astype(int)]
        if not len(idx):
            continue
        parsed = parse_terrain_tile_bin(OUT_DIR / f"terrain_{AREA_NAME}_test_tile_{tx}_{ty}.bin")
        pos = np.array(parsed["verts"], dtype=np.float64).reshape(-1, 3)
        tri = np.array(parsed["tris"], dtype=np.int64).reshape(-1, 3)
        z = sample_mesh_heights(pos, tri, cx * 100.0, cy * 100.0)
        ok = ~np.isnan(z) & in_outline(cx, -cy)   # (2026-10-21) a drawn area's cards stay inside its outline
        n_band += int(((flags[ok] & FLAG_BAND) > 0).sum())
        n_street += int(((flags[ok] & FLAG_STREET) > 0).sum())
        blocks.append(pack_tile(tx, ty, idx[ok], sjx[ok], sjy[ok], z[ok], flags[ok]))
        total += int(ok.sum())

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"farforest_{AREA_NAME}_test.bin"
    data = b"ODF1" + struct.pack("<I", len(blocks)) + b"".join(blocks)
    out.write_bytes(data)
    print(f"{len(tiles)} owned tile(s), {n_forest} WorldCover tree cell(s), {total} record(s) "
          f"({n_band} cards in the road band, {n_street} street trees) -> {out} ({len(data) / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
