"""Impervious ground mask (realism eval T6, 2026-10-15): which ground is
paved - parking lots, plazas, forecourts, and the ground between buildings in
built-up blocks - for the Realistic terrain material's pave layer
(M_GroundStylized_real PaveTex, unreal/apply_terrain_real_materials.py).
Until now towns rendered as lawn between the buildings (SF, Honolulu,
Valencia's big-box lots, Frisco).

Sources, both open:
  - OpenStreetMap (ODbL): amenity=parking (not multi-storey/underground/
    rooftop/sheds), amenity=fuel, highway=pedestrian / area:highway / place=
    square areas -> fully paved (255);
  - ESA WorldCover 2021 (CC-BY 4.0) built-up, 10 m: local built-up fraction
    (~30 m) * BUILT_PAVE. Built-up also covers yards and roofs (roofs are hidden
    under the buildings anyway), so it is only a partial pave.
  ponytail: no NLCD impervious % (CONUS only - not Oahu); add it where 30 m
  fractions beat WorldCover's yes/no if suburbs read too grey.

Covers the near terrain tiles' extent (same as build_sat_albedo.py), TEXEL_M
texels, capped at MAX_PX per side. Writes Content/OpenDrive/Masks/pave_<area>_test.png (RGB,
grey = paved weight sRGB-encoded like SatTex so the material's Color sampler
reads it linear; row 0 = north edge = UE y_min) + pave_<area>_test.json (extent in UE cm, same
layout as sat_*.json), read by AODTerrainManager::LoadPaveMask.
    OD_AREA=oahu python pipeline/build_pave_mask.py
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bake_job import outline_tiles  # noqa: E402
from build_water_tiles import assemble_outer_rings  # noqa: E402
from classify_biomes import WC_BUILT, fetch_worldcover, sample_nearest_ll  # noqa: E402
from fetch_cache import overpass_json  # noqa: E402
from test_areas import AREA_NAME, TERRAIN_TILE_SIZE_CM, lonlat_bbox, to_local, to_lonlat  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MASKS = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
HEADERS = {"User-Agent": "OpenDrive-stage-pipeline/0.1 (pave mask)"}
TEXEL_M = 5.0
MAX_PX = 8192
BUILT_PAVE = 0.35    # built-up = roofs + yards + drives; sat albedo carries the rest
PARKING_EXCLUDE = {"multi-storey", "underground", "rooftop", "sheds"}


def is_paved_area(tags):
    if tags.get("amenity") == "parking":
        return tags.get("parking") not in PARKING_EXCLUDE
    return (tags.get("amenity") == "fuel" or tags.get("place") == "square" or "area:highway" in tags
            or (tags.get("highway") == "pedestrian" and tags.get("area") == "yes"))


def srgb_lut():
    """uint8 linear weight -> uint8 sRGB code (UE decodes the texture back to linear)."""
    v = np.arange(256) / 255.0
    e = np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(v, 1 / 2.4) - 0.055)
    return (e * 255.0 + 0.5).astype(np.uint8)


def near_tile_rect():
    """Local-metre rect (e0, n0, e1, n1) of the area's near terrain tiles (a
    drawn area's planned ones: pass 1 runs before any is baked)."""
    keys = outline_tiles(TERRAIN_TILE_SIZE_CM) or [
        tuple(map(int, p.stem.rsplit("_", 2)[1:])) for p in MASKS.glob(f"terrain_{AREA_NAME}_test_tile_*.bin")]
    if not keys:
        raise SystemExit(f"no terrain tiles for {AREA_NAME} in {MASKS}")
    tx, ty = np.array(keys).T
    m = TERRAIN_TILE_SIZE_CM / 100.0
    return tx.min() * m, -(ty.max() + 1) * m, (tx.max() + 1) * m, -ty.min() * m


def fetch_paved_rings(rect):
    """[[(lon, lat), ...], ...] outer rings of paved OSM areas over rect."""
    s, w, n, e = lonlat_bbox(*rect)
    sel = "".join(f'{t}["{k}"{"=" + chr(34) + v + chr(34) if v else ""}]({s},{w},{n},{e});'
                  for t in ("way", "relation")
                  for k, v in (("amenity", "parking"), ("amenity", "fuel"), ("place", "square"),
                               ("area:highway", None), ("highway", "pedestrian")))
    data = overpass_json(f"[out:json][timeout:300];({sel});(._;>>;);out body;", HEADERS)
    nodes = {el["id"]: (el["lon"], el["lat"]) for el in data["elements"] if el["type"] == "node"}
    ways = {el["id"]: el for el in data["elements"] if el["type"] == "way"}
    rels = [el for el in data["elements"] if el["type"] == "relation" and is_paved_area(el.get("tags", {}))]
    rings, used = [], set()
    for rel in rels:
        outer = [m["ref"] for m in rel.get("members", []) if m.get("type") == "way" and m.get("role") == "outer"]
        used.update(outer)
        for ids in assemble_outer_rings(outer, {i: w_.get("nodes", []) for i, w_ in ways.items()}):
            rings.append([nodes[i] for i in ids if i in nodes])
    for wid, w_ in ways.items():
        nd = w_.get("nodes", [])
        if wid not in used and is_paved_area(w_.get("tags", {})) and len(nd) >= 4 and nd[0] == nd[-1]:
            rings.append([nodes[i] for i in nd[:-1] if i in nodes])
    return [r for r in rings if len(r) >= 3]


def grid_for(rect, texel_m=TEXEL_M, max_px=MAX_PX):
    """(texel_m, width, height) covering local rect (e0, n0, e1, n1)."""
    e0, n0, e1, n1 = rect
    texel_m = max(texel_m, (e1 - e0) / max_px, (n1 - n0) / max_px)
    return texel_m, int(np.ceil((e1 - e0) / texel_m)), int(np.ceil((n1 - n0) / texel_m))


def rasterize(rings_en, rect, texel_m, w, h):
    """Rings in local metres (east, north) -> uint8 mask, row 0 = north edge."""
    e0, _, _, n1 = rect
    img = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(img)
    for ring in rings_en:
        d.polygon([((e - e0) / texel_m, (n1 - n) / texel_m) for e, n in ring], fill=255)
    return np.asarray(img)


def build():
    rect = near_tile_rect()
    texel_m, w, h = grid_for(rect)
    e0, n0, e1, n1 = rect
    # WorldCover built-up at each texel centre (row chunks: Oahu is ~8k x 6k)
    s, west, n, east = lonlat_bbox(*rect)
    wc, wcb = fetch_worldcover(west, s, east, n, ovr=0)
    built = np.zeros((h, w), np.float32)
    ex = e0 + (np.arange(w) + 0.5) * texel_m
    for r0 in range(0, h, 256):
        rows = n1 - (np.arange(r0, min(h, r0 + 256)) + 0.5) * texel_m
        ee, nn = np.meshgrid(ex, rows)
        lon, lat = to_lonlat(ee.ravel(), nn.ravel())
        built[r0:r0 + len(rows)] = (sample_nearest_ll(wc, wcb, lon, lat) == WC_BUILT).reshape(len(rows), w)
    del wc
    pave = ndimage.uniform_filter(built, max(1, round(30.0 / texel_m))) * (BUILT_PAVE * 255.0)

    rings = fetch_paved_rings(rect)
    rings_en = [list(zip(*to_local(np.array([p[0] for p in r]), np.array([p[1] for p in r])))) for r in rings]
    osm = rasterize(rings_en, rect, texel_m, w, h)
    out = np.maximum(pave, osm).clip(0, 255).astype(np.uint8)
    out[[0, -1], :] = 0     # clamp addressing repeats the border texels past the extent
    out[:, [0, -1]] = 0

    MASKS.mkdir(parents=True, exist_ok=True)
    Image.fromarray(srgb_lut()[out], "L").convert("RGB").save(MASKS / f"pave_{AREA_NAME}_test.png", optimize=True)
    meta = {"x_min_cm": e0 * 100.0, "y_min_cm": -n1 * 100.0,
            "size_x_cm": w * texel_m * 100.0, "size_y_cm": h * texel_m * 100.0,
            "texel_m": texel_m, "width": w, "height": h, "osm_areas": len(rings),
            "attribution": "OpenStreetMap contributors (ODbL); ESA WorldCover 2021 (CC-BY 4.0)"}
    (MASKS / f"pave_{AREA_NAME}_test.json").write_text(json.dumps(meta, indent=1))
    print(f"build_pave_mask: {AREA_NAME} {w}x{h} @ {texel_m:.1f} m, {len(rings)} OSM paved areas, "
          f"paved>0.5 {(out > 127).mean() * 100:.2f}%, built-up {built.mean() * 100:.1f}%")


if __name__ == "__main__":
    build()
