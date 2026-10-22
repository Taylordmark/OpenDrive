"""Per-area ground elevation bands (realism eval T1, 2026-10-15): sea level,
treeline, summer/winter snow line and the vertex-tint elevation ramp, in
the area's own UE Z frame (cm). They used to be baked into the shared ground
materials from summit_county's ORIGIN_HEIGHT_M, so every other area got
Colorado's snow and scree (Green Valley ridges white in summer, Koko Crater
grey) and the beach band sat kilometres underground.

Derived from climate.json (fetch_climate.py, NASA POWER monthly means) with
a standard 6.5 C/km lapse rate, referenced to the elevation of the POWER
grid cell those means are for (same point fetch_climate.py queries; cached
in data/cache/power/; the area's median coarse-terrain elevation offline):
  - treeline: warmest-month mean = TREELINE_T_C (near the Koppen 10 C
    forest/tundra line, fitted to Colorado's ~3550 m), TREELINE_BAND_M wide;
  - summer snow: patchy from where the warmest month is SUMMER_SNOW_T_C
    (fitted: Colorado's late-summer snowfields start ~3900 m), full cover
    SNOW_BAND_M higher;
  - winter snow: patchy from where the coldest month is WINTER_FIRST_T_C,
    full where it is WINTER_FULL_T_C.
Warm areas (Oahu, Green Valley, SF) land far above their highest terrain, so
they get no snow or scree at all - no per-area special case.

The ground material's snow noise pushes a cell up to SNOW_NOISE_STRENGTH/2
of the band width past the ramp's low edge, so "first patch" elevations are
converted to the material's (low, high) edges such that nothing snows below
them (snow_ramp()).

Writes Content/OpenDrive/Masks/groundbands_<area>_test.json, read by
AODTerrainManager::LoadGroundBands (material params + tint) and
AODSeasonController (snow line sweep). Needs terrain tiles + climate.json.
    OD_AREA=oahu python pipeline/build_ground_bands.py      (one area)
    python pipeline/build_ground_bands.py --all             (every area with a climate.json)
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bin_tile_io import parse_terrain_tile_bin  # noqa: E402
from test_areas import AREA_NAME, AREAS  # noqa: E402
from world_frame import WORLD_DATUM_M  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MASKS = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"

LAPSE_C_PER_M = 0.0065
TREELINE_T_C = 11.0
TREELINE_BAND_M = 300.0
SUMMER_SNOW_T_C = 8.8
SNOW_BAND_M = 990.0
WINTER_FIRST_T_C = 1.0
WINTER_FULL_T_C = -3.0
ELEV_TINT_SPAN_M = 765.0        # vertex-tint low->high grey ramp, from treeline low
SNOW_NOISE_STRENGTH = 1.3       # == apply_ground_material.py / apply_terrain_real_materials.py
# ApplyBiomeTint pulls ground below the elevation ramp toward dark "lush" green
# (ElevLowBias, 0.45). Right for summer-wet valleys, wrong for summer-dry ones:
# Green Valley's golden grass and ochre desert rendered olive (T5). Scaled by
# the three warmest months' precipitation: <= 30 mm none, >= 120 mm full.
LOW_GREEN_BIAS = 0.45
LOW_GREEN_DRY_MM, LOW_GREEN_WET_MM = 30.0, 120.0


def isotherm_m(ref_m, ref_t_c, t_c):
    return ref_m + (ref_t_c - t_c) / LAPSE_C_PER_M


def snow_ramp(first_m, full_m):
    """Material (low, high) edges so the noisy ramp starts at first_m and is
    fully white at full_m: low - k*w = first, low + w = full, k = noise/2."""
    w = (full_m - first_m) / (1.0 + SNOW_NOISE_STRENGTH / 2.0)
    return full_m - w, full_m


def low_green_bias(climate):
    t, p = climate["monthly_temp_c"], climate.get("monthly_precip_mm")
    if not p:
        return LOW_GREEN_BIAS
    summer = sum(p[i] for i in sorted(range(12), key=lambda i: -t[i])[:3])
    return round(LOW_GREEN_BIAS * min(1.0, max(0.0, (summer - LOW_GREEN_DRY_MM)
                                                / (LOW_GREEN_WET_MM - LOW_GREEN_DRY_MM))), 3)


def bands(origin_m, climate, ref_elev_m):
    """All values in UE cm relative to origin_m (= area-local world Z), plus
    low_green_bias (0..LOW_GREEN_BIAS)."""
    tw, tc = max(climate["monthly_temp_c"]), min(climate["monthly_temp_c"])
    tree_lo = isotherm_m(ref_elev_m, tw, TREELINE_T_C)
    s_lo, s_hi = snow_ramp(isotherm_m(ref_elev_m, tw, SUMMER_SNOW_T_C),
                           isotherm_m(ref_elev_m, tw, SUMMER_SNOW_T_C) + SNOW_BAND_M)
    w_lo, w_hi = snow_ramp(isotherm_m(ref_elev_m, tc, WINTER_FIRST_T_C),
                           isotherm_m(ref_elev_m, tc, WINTER_FULL_T_C))
    cm = lambda m: round((m - origin_m) * 100.0, 1)  # noqa: E731
    return dict(
        sea_z_cm=cm(0.0),
        treeline_low_cm=cm(tree_lo), treeline_high_cm=cm(tree_lo + TREELINE_BAND_M),
        elev_low_cm=cm(tree_lo), elev_high_cm=cm(tree_lo + ELEV_TINT_SPAN_M),
        snow_summer_low_cm=cm(s_lo), snow_summer_high_cm=cm(s_hi),
        snow_winter_low_cm=cm(w_lo), snow_winter_high_cm=cm(w_hi),
        low_green_bias=low_green_bias(climate),
    )


def origin_of(area):
    a = AREAS[area]
    return WORLD_DATUM_M[a["world"]] if "world" in a else a["origin_height_m"]


def power_cell_elev_m(area):
    """Elevation (m) of the NASA POWER cell climate.json's temperatures are for."""
    import requests
    from fetch_cache import cached_bytes
    meta = json.loads((ROOT / "data" / "road_tiles" / f"{area}_test" / "road_test_meta.json").read_text())
    lat, lon = meta.get("center_latlon") or meta["origin_latlon"]
    url = ("https://power.larc.nasa.gov/api/temporal/climatology/point?parameters=T2M&community=RE"
           f"&longitude={lon:.4f}&latitude={lat:.4f}&format=JSON")
    raw = cached_bytes("power", f"{lat:.4f}_{lon:.4f}", lambda: requests.get(url, timeout=60).content)
    return float(json.loads(raw)["geometry"]["coordinates"][2])


def ref_elevation_m(area, origin_m):
    """POWER cell elevation; offline, the median coarse-terrain elevation (ASL)."""
    try:
        return power_cell_elev_m(area)
    except Exception as exc:  # noqa: BLE001 - offline / no meta: terrain fallback
        print(f"build_ground_bands: {area}: POWER cell elevation unavailable ({exc}) - terrain median")
    zs = []
    for p in MASKS.glob(f"terrain_coarse_{area}_test_tile_*.bin"):
        t = parse_terrain_tile_bin(p)   # ODT1 or ODT2 (2026-10-21)
        if t is None:
            continue
        zs.append(np.asarray(t["verts"], np.float32)[2::3])
    if zs:
        return float(np.median(np.concatenate(zs))) / 100.0 + origin_m
    st = json.loads((ROOT / "data" / "road_tiles" / f"{area}_test" / "road_stations.json").read_text())
    return float(np.median(np.concatenate([s["elev_m"] for s in st["segments"]])))


def build(area):
    clim_path = ROOT / "data" / "road_tiles" / f"{area}_test" / "climate.json"
    if not clim_path.exists():
        print(f"build_ground_bands: {area}: no climate.json (run fetch_climate.py) - skipped")
        return None
    origin = origin_of(area)
    ref = ref_elevation_m(area, origin)
    out = bands(origin, json.loads(clim_path.read_text()), ref)
    out.update(area=area, origin_m=origin, ref_elev_m=round(ref, 1))
    (MASKS / f"groundbands_{area}_test.json").write_text(json.dumps(out, indent=1))
    asl = {k: round(v / 100.0 + origin) for k, v in out.items() if k.endswith("_cm")}
    print(f"build_ground_bands: {area} ref {ref:.0f} m -> ASL m {asl}, low green bias {out['low_green_bias']}")
    return out


if __name__ == "__main__":
    for a in (sorted(AREAS) if "--all" in sys.argv else [AREA_NAME]):
        build(a)
