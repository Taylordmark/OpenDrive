"""Diagnostic (not committed to pipeline/): quantify whether FAR_GRID_N's
MIN-filter downsample (build_terrain_mesh_tiles.downsample_min_grid) injects
apparent slope noise on background hillsides that a smoother (mean-filter /
raw point-sample) downsample would not show, and how that translates through
apply_ground_material.py's flat_w = dot(normal,up)^SLOPE_SOFTNESS gate.

Samples a handful of ~2km FAR-tile-sized patches of real DEM around
Silverthorne and Vail (summit_county), away from any road (no carve
applied - matches the vast majority of real background/off-corridor FAR
tiles), reproduces build_tile's exact FAR sampling path (supersample 10x,
then MIN-downsample to a 21x21 grid), and compares vertex-to-vertex slope
against a MEAN-downsample of the identical fine data.
"""
import sys
from pathlib import Path
import math
import numpy as np
import requests

sys.path.insert(0, str(Path(r"C:\Users\taylo\Coding\OpenDrive\pipeline")))
from terrain_common import fetch_terrarium_tile, terrarium_to_elevation
import build_terrain_mesh_tiles as bt

SLOPE_SOFTNESS = 4.0
FAR_GRID_N = bt.FAR_GRID_N
FAR_SUPERSAMPLE = bt.FAR_SUPERSAMPLE
TILE_CM = 2000.0 * 100.0  # 2000 m tile

session = requests.Session()
cache = {}

# (name, lat, lon) - patches picked a few km from town center, away from
# the road corridor itself, on visibly rising ground per the bbox comment
# ("background rolling hills" / "mid-distance mountainside").
PATCHES = [
    ("Silverthorne_bg_hill", 39.6295 + 0.035, -106.0714 + 0.01),
    ("Vail_bg_mountainside", 39.6403 + 0.03, -106.3742 - 0.02),
]

zoom = 15


def sample_grid(lat0, lon0, half_size_m=1000.0, n=FAR_GRID_N, supersample=FAR_SUPERSAMPLE):
    m_per_deg_lat = 111132.0
    m_per_deg_lon = m_per_deg_lat * math.cos(math.radians(lat0))
    sample_n = (n - 1) * supersample + 1
    xs_m = np.linspace(-half_size_m, half_size_m, sample_n)
    ys_m = np.linspace(-half_size_m, half_size_m, sample_n)
    gx_m, gy_m = np.meshgrid(xs_m, ys_m)
    lat = lat0 + gy_m / m_per_deg_lat
    lon = lon0 + gx_m / m_per_deg_lon
    elev = bt.sample_dem(lat.reshape(-1), lon.reshape(-1), zoom, session, cache).reshape(sample_n, sample_n)
    return gx_m, gy_m, elev, sample_n


def mean_downsample(fine, n, factor):
    sample_n = fine.shape[0]
    half = factor // 2
    bounds = []
    for i in range(n):
        c = i * factor
        lo = max(0, c - half)
        hi = min(sample_n, c + half + 1)
        bounds.append((lo, hi))
    out = np.empty((n, n), dtype=fine.dtype)
    for oi, (ylo, yhi) in enumerate(bounds):
        row_block = fine[ylo:yhi, :]
        for oj, (xlo, xhi) in enumerate(bounds):
            out[oi, oj] = row_block[:, xlo:xhi].mean()
    return out


def slope_stats(elev_grid_m, spacing_m):
    """Per-quad slope_dot = dot(normal,up), matching a simple finite-difference
    vertex normal (close enough to FMeshNormals' averaged triangle normals
    for this diagnostic's purpose - same order of magnitude)."""
    dzdx = (elev_grid_m[:, 1:] - elev_grid_m[:, :-1]) / spacing_m
    dzdx = 0.5 * (dzdx[1:, :] + dzdx[:-1, :])
    dzdy = (elev_grid_m[1:, :] - elev_grid_m[:-1, :]) / spacing_m
    dzdy = 0.5 * (dzdy[:, 1:] + dzdy[:, :-1])
    # normal = normalize((-dzdx, -dzdy, 1))
    norm = np.sqrt(dzdx ** 2 + dzdy ** 2 + 1.0)
    slope_dot = 1.0 / norm
    return slope_dot


def flat_w(slope_dot):
    return slope_dot ** SLOPE_SOFTNESS


for name, lat0, lon0 in PATCHES:
    gx_m, gy_m, elev_fine, sample_n = sample_grid(lat0, lon0)
    spacing_out_m = (2 * 1000.0) / (FAR_GRID_N - 1)  # ~100 m
    min_grid = bt.downsample_min_grid(elev_fine, FAR_GRID_N, FAR_SUPERSAMPLE)
    mean_grid = mean_downsample(elev_fine, FAR_GRID_N, FAR_SUPERSAMPLE)

    sd_min = slope_stats(min_grid, spacing_out_m)
    sd_mean = slope_stats(mean_grid, spacing_out_m)
    fw_min = flat_w(sd_min)
    fw_mean = flat_w(sd_mean)

    elev_range = elev_fine.max() - elev_fine.min()
    print(f"=== {name} @ ({lat0:.5f},{lon0:.5f}) elev_range={elev_range:.1f}m ===")
    print(f"  MIN-downsample : slope_dot mean={sd_min.mean():.3f} p10={np.percentile(sd_min,10):.3f} "
          f"min={sd_min.min():.3f}  -> flat_w mean={fw_min.mean():.3f} frac(flat_w<0.5)={ (fw_min<0.5).mean():.2%}")
    print(f"  MEAN-downsample: slope_dot mean={sd_mean.mean():.3f} p10={np.percentile(sd_mean,10):.3f} "
          f"min={sd_mean.min():.3f}  -> flat_w mean={fw_mean.mean():.3f} frac(flat_w<0.5)={ (fw_mean<0.5).mean():.2%}")
    # angle equivalents
    ang_min = np.degrees(np.arccos(np.clip(sd_min, -1, 1)))
    ang_mean = np.degrees(np.arccos(np.clip(sd_mean, -1, 1)))
    print(f"  slope angle (deg): MIN mean={ang_min.mean():.1f} max={ang_min.max():.1f} | "
          f"MEAN mean={ang_mean.mean():.1f} max={ang_mean.max():.1f}")
    print()
