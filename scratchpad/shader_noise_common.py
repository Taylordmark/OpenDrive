"""Shared numpy port of build_flora_materials.py's _value_noise() (the
tileable-lattice value-noise hash used for both the fall-color patch-turn
and the leaf-drop dissolve) plus the turn-threshold / fall_amount /
drop_amount formulas from the M_Foliage deciduous-arc wiring section. This
is a literal port of the material-graph math, not an approximation -
every operation here corresponds 1:1 to a MaterialExpression node in
unreal/build_flora_materials.py.
"""
import numpy as np


def _frac(x):
    return x - np.floor(x)


def _hash2(ix, iy):
    d = ix * 12.9898 + iy * 78.233
    s = np.sin(d)
    return _frac(s * 43758.5453)


def value_noise(wx, wy, scale_cm, period=97.0, seed_offset=(0.0, 0.0)):
    """wx, wy: world-position arrays (cm). Returns array in ~[0,1]."""
    ux = wx / scale_cm + seed_offset[0]
    uy = wy / scale_cm + seed_offset[1]
    sx = ux / period
    sy = uy / period
    wrapped01_x = _frac(sx)
    wrapped01_y = _frac(sy)
    wx2 = wrapped01_x * period
    wy2 = wrapped01_y * period

    ix = np.floor(wx2)
    iy = np.floor(wy2)
    fx = _frac(wx2)
    fy = _frac(wy2)

    h00 = _hash2(ix, iy)
    h10 = _hash2(ix + 1, iy)
    h01 = _hash2(ix, iy + 1)
    h11 = _hash2(ix + 1, iy + 1)

    top = h00 * (1 - fx) + h10 * fx
    bot = h01 * (1 - fx) + h11 * fx
    return top * (1 - fy) + bot * fy


TURN_BAND = 0.30
PATCH_NOISE_SCALE_CM = 180.0
PATCH_NOISE_SEED = (41.0, 17.0)


def turn_threshold(wx, wy, per_instance_random):
    """turn_threshold = per_instance_random*0.5 + patch_noise*0.5, exactly
    matching build_flora_materials.py's deciduous-arc section."""
    patch = value_noise(wx, wy, PATCH_NOISE_SCALE_CM, seed_offset=PATCH_NOISE_SEED)
    return per_instance_random * 0.5 + patch * 0.5


def fall_or_drop_amount(param_value, wx, wy, per_instance_random):
    """fall_amount / drop_amount - same formula, just fed FallBlend vs
    LeafDrop as param_value (the material graph builds both from the same
    turn_threshold/thr_lo)."""
    thr = turn_threshold(wx, wy, per_instance_random)
    thr_lo = thr - TURN_BAND / 2.0
    return np.clip((param_value - thr_lo) / TURN_BAND, 0.0, 1.0)
