"""Phase 3b flora meshes (pipeline half of the art-direction shift toward The
Crew 2's grounded semi-photoreal look - see next steps.md). Replaces the
previous solid faceted low-poly blobs/cones with the standard game-vegetation
technique: a tapered segmented trunk (real bark-photo UVs, from
build_flora_textures.py) plus alpha-masked branch/leaf CARDS (crossed quads)
scattered over the crown, UV'd into T_FoliageAtlas.png. Two material slots
per tree mesh - slot 0 "bark" (opaque, its own bark texture per species),
slot 1 "foliage" (alpha-cutout, ALWAYS T_FoliageAtlas.png so every species
can share one UE material for that slot). Shrubs/ground-cover are
foliage-only (slot 0 = foliage, no bark slot at all).

Species set (Summit County, CO Rockies - see next steps.md for the
art-direction brief): lodgepole pine, engelmann spruce, subalpine fir,
aspen, willow scrub bush, sagebrush, krummholz. The existing 7 tree-species
mesh SLOTS + 2 shrub-mesh slots in ODForestManager.cpp / ODBinSidecarUtil.h
are a fixed C++ contract (paths + array sizes) this pass does NOT touch -
every slot keeps its old filename/path but is re-authored as a specific
Colorado species. The two dominant species (lodgepole pine, aspen) each get
TWO slots with different seeds/proportions (a young/typical form and an
older/mature form) - since ODForestManager already scatters slots by
per-biome weighted random pick, this gives real in-world shape variety for
the dominant species without needing any per-instance runtime hook (HISM
here carries no per-instance custom-data shape variation - see next steps.md
for why that's a C++-side follow-up, out of scope for this pass):

    tree_blob        -> Aspen A       (young grove clump, rounder crown)
    tree_olive       -> Engelmann Spruce
    tree_conifer     -> Lodgepole Pine A (typical - dominant evergreen slot)
    tree_scrub_oak   -> Aspen B       (older/broader, taller clear trunk)
    tree_manzanita   -> Krummholz     (stunted, wind-bent, treeline)
    tree_yucca       -> Subalpine Fir (narrow "cathedral spire" form)
    tree_canyon_oak  -> Lodgepole Pine B (mature backdrop variant)
    bush             -> Willow/streamside scrub bush (multi-stem, low)
    sagebrush        -> Sagebrush (foliage-only, no bark slot)
    grass_tuft       -> alpine grass tuft (foliage-only, unchanged role)

Outputs: obj/<name>.obj + obj/<name>.mtl for each of the above. Requires
textures/T_BarkConifer.png, T_BarkAspen.png, T_BarkWillow.png,
T_FoliageAtlas.png to already exist (run build_flora_textures.py first).
"""

import math
from pathlib import Path

import numpy as np

from flora_common import cell_uv_rect

STAGE_DIR = Path(__file__).resolve().parent.parent / "data" / "stages" / \
    "tail_of_the_dragon"
OBJ_OUT = STAGE_DIR / "obj"
TEX = STAGE_DIR / "textures"
OBJ_OUT.mkdir(parents=True, exist_ok=True)

rng = np.random.default_rng(11)

BARK_TEX = {
    "bark_conifer": "T_BarkConifer.png",
    "bark_aspen": "T_BarkAspen.png",
    "bark_willow": "T_BarkWillow.png",
}
FOLIAGE_TEX = "T_FoliageAtlas.png"


class Mesh:
    """Card-based flora mesh: arbitrary-UV triangles tagged with a named
    material ("bark_conifer"/"bark_aspen"/"bark_willow"/"foliage" - the
    *slot index* a name ends up at is just its first-appearance order in
    self.tris, which save() preserves into the OBJ's usemtl order)."""

    def __init__(self):
        self.tris = []  # (mat, (a,b,c), (uva,uvb,uvc), (na,nb,nc))

    def _tri(self, a, b, c, uva, uvb, uvc, mat, flip_center=None):
        a, b, c = (np.asarray(p, dtype=float) for p in (a, b, c))
        n = np.cross(c - a, b - a)
        if flip_center is not None:
            fc = (a + b + c) / 3
            if np.dot(n, fc - flip_center) < 0:
                b, c = c, b
                uvb, uvc = uvc, uvb
                n = -n
        ln = np.linalg.norm(n)
        n = n / ln if ln > 0 else np.array([0.0, 0.0, 1.0])
        self.tris.append((mat, (a, b, c), (uva, uvb, uvc), (n, n, n)))

    def quad(self, bl, br, tr, tl, uv_rect, mat):
        u0, u1, v0, v1 = uv_rect
        self._tri(bl, br, tr, (u0, v0), (u1, v0), (u1, v1), mat)
        self._tri(bl, tr, tl, (u0, v0), (u1, v1), (u0, v1), mat)

    def trunk(self, profile, mat, sides=8, u_repeat=2.0, v_repeat=3.0, lean=None):
        """profile: [(height, radius), ...] bottom to top, height 0 = ground.
        lean(height)->(dx,dy) optional bend/sway/gnarl offset function.
        Returns the top ring's centroid + radius (for attaching a crown)."""
        rings = []
        h0 = profile[0][0]
        total_h = max(profile[-1][0] - h0, 1.0)
        for h, r in profile:
            dx, dy = lean(h) if lean else (0.0, 0.0)
            rings.append([np.array([dx + math.cos(2 * math.pi * s / sides) * r,
                                     dy + math.sin(2 * math.pi * s / sides) * r,
                                     h]) for s in range(sides)])
        for ri in range(len(rings) - 1):
            v0 = (profile[ri][0] - h0) / total_h * v_repeat
            v1 = (profile[ri + 1][0] - h0) / total_h * v_repeat
            for s in range(sides):
                s2 = (s + 1) % sides
                u0 = s / sides * u_repeat
                u1 = (s + 1) / sides * u_repeat
                a, b = rings[ri][s], rings[ri][s2]
                c, d = rings[ri + 1][s2], rings[ri + 1][s]
                self._tri(a, b, c, (u0, v0), (u1, v0), (u1, v1), mat)
                self._tri(a, c, d, (u0, v0), (u1, v1), (u0, v1), mat)
        top = rings[-1]
        centroid = np.mean(top, axis=0)
        radius = float(np.mean([np.linalg.norm(p[:2] - centroid[:2]) for p in top]))
        return centroid, radius

    def card_cluster(self, center, size_w, size_h, cell, n_cross=2, base_az=None,
                      up_dir=None):
        """A small crossed-quad clump of foliage cards centered on 'center'
        (spans center +/- size_h/2 vertically, not base-up-only - a card
        that only grows up from its attach point reads as a thin flag; a
        card centered on it fills crown volume with far fewer clusters) -
        the standard billboard-card-tree technique. Planes are spread evenly
        over pi radians around world-Z (a plane at theta and theta+pi are
        the same double-sided plane, so pi is the full unique range).
        up_dir: optional (x,y,z) direction the card grows toward (default
        world-Z). Conifer boughs droop down-and-out rather than standing
        as vertical curtains - see scatter_cone_crown's droop arg, which
        computes this per-cluster."""
        if base_az is None:
            base_az = rng.uniform(0, math.pi)
        uv = cell_uv_rect(cell)
        c = np.asarray(center, dtype=float)
        if up_dir is None:
            up_ref = np.array([0.0, 0.0, 1.0])
        else:
            up_ref = np.asarray(up_dir, dtype=float)
            n = np.linalg.norm(up_ref)
            up_ref = up_ref / n if n > 1e-6 else np.array([0.0, 0.0, 1.0])
        for k in range(n_cross):
            az = base_az + math.pi * k / n_cross
            right = np.array([math.cos(az), math.sin(az), 0.0])
            # orthogonalize up against this plane's right axis so the quad
            # stays non-degenerate even when up_ref isn't purely vertical
            # (droopy conifer boughs - see scatter_cone_crown's droop arg)
            up = up_ref - right * np.dot(up_ref, right)
            un = np.linalg.norm(up)
            up = up / un if un > 1e-6 else np.array([0.0, 0.0, 1.0])
            bl = c - right * size_w / 2 - up * size_h * 0.3
            br = c + right * size_w / 2 - up * size_h * 0.3
            tl = bl + up * size_h
            tr = br + up * size_h
            self.quad(bl, br, tr, tl, uv, "foliage")

    def branch(self, start, end, r0, r1, mat, sides=4):
        """A simple tapered tube from arbitrary 3D point `start` to `end`
        (unlike trunk(), which only stacks rings along world-Z) - used for
        the deciduous "bare twig" branch structure (2026-09 seasons
        follow-up, see build_aspen): a handful of these fanning out from the
        upper trunk gives real geometry for leaves to dissolve away from,
        without the poly cost of a proper branching system. Capped at the
        far end only (near end is buried in the trunk/a parent branch, never
        seen); low `sides` (default 4) keeps each branch cheap - this is
        rendered as thousands of HISM instances."""
        start = np.asarray(start, dtype=float)
        end = np.asarray(end, dtype=float)
        axis = end - start
        length = np.linalg.norm(axis)
        if length < 1e-6:
            return end
        axis_n = axis / length
        ref = np.array([0.0, 0.0, 1.0]) if abs(axis_n[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
        u = np.cross(axis_n, ref)
        u /= np.linalg.norm(u)
        v = np.cross(axis_n, u)
        ring0 = [start + (math.cos(2 * math.pi * s / sides) * u
                           + math.sin(2 * math.pi * s / sides) * v) * r0 for s in range(sides)]
        ring1 = [end + (math.cos(2 * math.pi * s / sides) * u
                         + math.sin(2 * math.pi * s / sides) * v) * r1 for s in range(sides)]
        for s in range(sides):
            s2 = (s + 1) % sides
            u0, u1 = s / sides, (s + 1) / sides
            a, b = ring0[s], ring0[s2]
            c, d = ring1[s2], ring1[s]
            self._tri(a, b, c, (u0, 0.0), (u1, 0.0), (u1, 1.0), mat)
            self._tri(a, c, d, (u0, 0.0), (u1, 1.0), (u0, 1.0), mat)
        for s in range(sides):
            s2 = (s + 1) % sides
            self._tri(ring1[s], ring1[s2], end, (0.0, 0.0), (1.0, 0.0), (0.5, 1.0), mat)
        return end

    def save(self, name, mat_textures):
        """mat_textures: ordered dict-like of matname -> texture filename
        (or None). Order = OBJ material declaration order; usemtl statements
        follow first-appearance order in self.tris, which callers control by
        emitting trunk (bark) triangles before crown (foliage) triangles -
        that puts bark at material slot 0, foliage at slot 1, matching what
        import_biome_flora.py expects. VERIFY this slot order on first UE
        import (see next steps.md) - OBJ/Interchange material-slot ordering
        isn't something this offline pass can confirm without a UE launch."""
        lines = [f"# OpenDrive card-based flora: {name}", f"mtllib {name}.mtl"]
        f_lines = []
        vi = 1
        cur_mat = None
        for mat, (a, b, c), (uva, uvb, uvc), (na, nb, nc) in self.tris:
            if mat != cur_mat:
                f_lines.append(f"usemtl {mat}")
                cur_mat = mat
            for p, uv, n in ((a, uva, na), (b, uvb, nb), (c, uvc, nc)):
                lines.append(f"v {p[0]:.2f} {-p[1]:.2f} {p[2]:.2f}")
                lines.append(f"vt {uv[0]:.4f} {uv[1]:.4f}")
                lines.append(f"vn {n[0]:.3f} {-n[1]:.3f} {n[2]:.3f}")
            f_lines.append(f"f {vi}/{vi}/{vi} {vi + 1}/{vi + 1}/{vi + 1} "
                           f"{vi + 2}/{vi + 2}/{vi + 2}")
            vi += 3
        (OBJ_OUT / f"{name}.obj").write_text("\n".join(lines + f_lines) + "\n")

        mtl_lines = []
        for mat_name, tex in mat_textures.items():
            mtl_lines.append(f"newmtl {mat_name}")
            mtl_lines.append("Kd 1.0 1.0 1.0")
            if tex:
                mtl_lines.append(f"map_Kd ../textures/{tex}")
                if mat_name == "foliage":
                    mtl_lines.append(f"map_d ../textures/{tex}")
                    mtl_lines.append("d 1.0")
            mtl_lines.append("")
        (OBJ_OUT / f"{name}.mtl").write_text("\n".join(mtl_lines))
        print(f"wrote {name}.obj ({len(self.tris)} tris, "
              f"mats={list(mat_textures)})")


# ---------------------------------------------------------------------------
# crown-scatter helpers

def scatter_cone_crown(mesh, base_h, apex_h, base_r, top_r_frac, n_clusters,
                        cell, size_w, size_h, taper_pow=1.0, lean=None,
                        jitter_r=0.18, n_cross=2, az_range=None, az_center=0.0,
                        tier_bands=None, droop=False, n_bands=None):
    """Scatter card clusters over a cone-shaped crown envelope. az_range
    (radians, None = full 2pi) + az_center let krummholz bias foliage to one
    side (wind-shear look). tier_bands: optional list of (t_lo, t_hi) to
    sample from (layered/shelfy look, e.g. spruce) instead of continuous t.

    n_bands: when set (and tier_bands isn't), height is stratified into
    n_bands equal bands and clusters are assigned round-robin across them
    (instead of pure uniform-random t) so no band can come up empty by luck
    - this is what keeps a dense conifer crown from getting random vertical
    sky-gaps. droop=True tilts each cluster's card plane down-and-out
    (blend of outward radial + downward) instead of straight up - reads as
    a drooping bough instead of a vertical foliage curtain."""
    total_h = apex_h - base_h
    for i in range(n_clusters):
        if tier_bands:
            lo, hi = tier_bands[rng.integers(0, len(tier_bands))]
            t = rng.uniform(lo, hi)
        elif n_bands:
            band = i % n_bands
            lo, hi = band / n_bands, (band + 1) / n_bands
            t = rng.uniform(lo, hi)
        else:
            t = rng.uniform(0.0, 1.0)
        h = base_h + t * total_h
        r_at_t = base_r * ((1 - t) ** taper_pow) * (1 - top_r_frac) + base_r * top_r_frac
        r = max(r_at_t * rng.uniform(1 - jitter_r, 1 + jitter_r), r_at_t * 0.4)
        if az_range is None:
            ang = rng.uniform(0, 2 * math.pi)
        else:
            ang = az_center + rng.uniform(-az_range / 2, az_range / 2)
        dx, dy = lean(h) if lean else (0.0, 0.0)
        cx = dx + math.cos(ang) * r
        cy = dy + math.sin(ang) * r
        w = rng.uniform(*size_w) * (0.6 + 0.4 * (1 - t))  # slightly bigger low, smaller near tip
        hh = rng.uniform(*size_h)
        up_dir = None
        if droop:
            # outward radial (away from trunk axis) blended with a downward
            # tilt - real conifer boughs angle out and slightly down from
            # the trunk, not straight up like a flagpole.
            out = np.array([math.cos(ang), math.sin(ang), 0.0])
            up_dir = out * 0.55 + np.array([0.0, 0.0, 0.6])
        mesh.card_cluster((cx, cy, h), w, hh, cell, n_cross=n_cross, up_dir=up_dir)


def scatter_ellipsoid_crown(mesh, center, rx, ry, rz, n_clusters, cell,
                             size_w, size_h, n_cross=2):
    cx0, cy0, cz0 = center
    for _ in range(n_clusters):
        theta = rng.uniform(0, 2 * math.pi)
        phi = math.acos(rng.uniform(-1, 1))
        rad = rng.uniform(0.6, 1.02)
        x = cx0 + rx * rad * math.sin(phi) * math.cos(theta)
        y = cy0 + ry * rad * math.sin(phi) * math.sin(theta)
        z = cz0 + rz * rad * math.cos(phi)
        w = rng.uniform(*size_w)
        hh = rng.uniform(*size_h)
        mesh.card_cluster((x, y, z), w, hh, cell, n_cross=n_cross)


def conifer_lean(sway_amp=18.0, sway_freq=1.0):
    def f(h):
        return (math.sin(h * 0.002 * sway_freq) * sway_amp * (h / 1000.0), 0.0)
    return f


def krummholz_lean(total_h, bend_amp):
    """Strong one-directional bend increasing with height - the classic
    wind-sculpted krummholz trunk."""
    def f(h):
        t = min(h / total_h, 1.0)
        return (bend_amp * t * t, bend_amp * 0.25 * t * t)
    return f


# ---------------------------------------------------------------------------
# species

def build_lodgepole_pine(name, height_scale=1.0, crown_start_frac=0.42):
    """Tall, narrow, conic - the dominant Rockies conifer. High clear
    trunk, foliage concentrated in the upper crown, narrow taper."""
    total_h = 1650 * height_scale
    trunk_r0 = 26 * height_scale
    m = Mesh()
    crown_h = total_h * crown_start_frac
    profile = [(0, trunk_r0), (crown_h * 0.5, trunk_r0 * 0.8),
               (crown_h, trunk_r0 * 0.6), (total_h, trunk_r0 * 0.12)]
    lean = conifer_lean(sway_amp=14 * height_scale)
    _, _ = m.trunk(profile, "bark_conifer", lean=lean)
    scatter_cone_crown(
        m, base_h=crown_h, apex_h=total_h, base_r=280 * height_scale,
        top_r_frac=0.06, n_clusters=104, cell="pine_needle",
        size_w=(110 * height_scale, 190 * height_scale),
        size_h=(170 * height_scale, 260 * height_scale),
        taper_pow=1.4, lean=lean, n_cross=2, droop=True, n_bands=10)
    m.save(name, {"bark_conifer": BARK_TEX["bark_conifer"], "foliage": FOLIAGE_TEX})


def build_engelmann_spruce(name):
    """Fuller conic, crown starts much lower than pine, layered/shelfy
    branch tiers, blue-green needles."""
    total_h = 1500
    trunk_r0 = 30
    m = Mesh()
    crown_h = total_h * 0.34  # continuous cone from ~35-40% height to tip
    profile = [(0, trunk_r0), (total_h * 0.4, trunk_r0 * 0.75),
               (total_h * 0.8, trunk_r0 * 0.45), (total_h, trunk_r0 * 0.1)]
    lean = conifer_lean(sway_amp=10)
    m.trunk(profile, "bark_conifer", lean=lean)
    tiers = [(0.0, 0.18), (0.18, 0.38), (0.38, 0.58), (0.58, 0.76), (0.76, 0.92), (0.92, 1.0)]
    scatter_cone_crown(
        m, base_h=crown_h, apex_h=total_h, base_r=340, top_r_frac=0.05,
        n_clusters=124, cell="spruce_needle", size_w=(120, 200), size_h=(170, 270),
        taper_pow=0.85, lean=lean, n_cross=3, tier_bands=tiers, droop=True)
    m.save(name, {"bark_conifer": BARK_TEX["bark_conifer"], "foliage": FOLIAGE_TEX})


def build_subalpine_fir(name):
    """Narrow "cathedral spire" - tighter, denser, darker than spruce, very
    symmetric silhouette with almost no clear trunk."""
    total_h = 1400
    trunk_r0 = 24
    m = Mesh()
    crown_h = total_h * 0.36  # continuous cone from ~35-40% height to tip
    profile = [(0, trunk_r0), (total_h * 0.5, trunk_r0 * 0.6), (total_h, trunk_r0 * 0.08)]
    lean = conifer_lean(sway_amp=8)
    m.trunk(profile, "bark_conifer", lean=lean)
    scatter_cone_crown(
        m, base_h=crown_h, apex_h=total_h, base_r=250, top_r_frac=0.03,
        n_clusters=112, cell="fir_needle", size_w=(100, 160), size_h=(140, 200),
        taper_pow=1.7, lean=lean, n_cross=2, jitter_r=0.12, droop=True, n_bands=10)
    m.save(name, {"bark_conifer": BARK_TEX["bark_conifer"], "foliage": FOLIAGE_TEX})


def build_krummholz(name):
    """Stunted, wind-bent treeline conifer - short, strongly leaning trunk,
    lopsided foliage biased to the lee side (wind-shear look), sparse. Crown
    radius here tracks the SAME t^2 growth as the trunk lean (small near the
    still-vertical base, widening only as the trunk bends toward the tip) -
    scatter_cone_crown's normal wide-base/narrow-top taper is for upright
    conic trees and left krummholz's crown floating off to the side of its
    still-straight lower trunk, so this species gets its own scatter."""
    total_h = 380
    trunk_r0 = 20
    m = Mesh()
    lean = krummholz_lean(total_h, bend_amp=140)
    profile = [(0, trunk_r0), (total_h * 0.5, trunk_r0 * 0.7), (total_h, trunk_r0 * 0.35)]
    m.trunk(profile, "bark_conifer", sides=7, lean=lean)
    az_center, az_range = math.pi * 0.15, math.radians(150)
    n_clusters = 24
    for _ in range(n_clusters):
        t = rng.uniform(0.25, 1.0)
        h = t * total_h
        dx, dy = lean(h)
        r = (40 + 170 * t * t) * rng.uniform(0.75, 1.15)
        ang = az_center + rng.uniform(-az_range / 2, az_range / 2)
        cx = dx + math.cos(ang) * r
        cy = dy + math.sin(ang) * r
        w = rng.uniform(90, 160)
        hh = rng.uniform(110, 180)
        m.card_cluster((cx, cy, h), w, hh, "krummholz_needle", n_cross=2)
    m.save(name, {"bark_conifer": BARK_TEX["bark_conifer"], "foliage": FOLIAGE_TEX})


def add_deciduous_branches(m, trunk_top_h, crown_center, crown_rx, crown_rz,
                            trunk_top_r, mat="bark_aspen", n_branches=7):
    """Fan a handful of tapering twig branches from the trunk top up into the
    crown volume (2026-09 seasons follow-up: "bare twigs" needs branch
    geometry that survives when the leaf cards dissolve away - see next
    steps.md sec 7.17). Cheap: `n_branches` calls to Mesh.branch() at
    sides=4 (~14 tris each including the tip cap), well under the <100 extra
    tris/tree budget this is scoped to, and it renders as thousands of HISM
    instances so per-tree cost matters. Shares the trunk's own bark material
    so it's always present (just hidden inside the summer canopy) - no new
    material slot."""
    start = np.array([0.0, 0.0, trunk_top_h])
    cx, cy, cz = crown_center
    for i in range(n_branches):
        az = 2 * math.pi * i / n_branches + rng.uniform(-0.3, 0.3)
        reach = rng.uniform(0.7, 1.0)
        end = np.array([
            cx + math.cos(az) * crown_rx * reach,
            cy + math.sin(az) * crown_rx * reach,
            trunk_top_h + (cz + crown_rz * rng.uniform(0.15, 0.55) - trunk_top_h) * reach,
        ])
        r0 = trunk_top_r * rng.uniform(0.55, 0.8)
        r1 = r0 * rng.uniform(0.12, 0.22)
        m.branch(start, end, r0, r1, mat, sides=4)


def build_aspen(name, height_scale=1.0, trunk_clear_frac=0.55, with_branches=False):
    """Pale-trunked deciduous - long bare trunk, oval/round crown of
    fluttering pale-green leaf cards. with_branches=True (tree_blob only,
    the species the seasons deciduous arc targets - see next steps.md sec
    7.17) adds a bare-twig branch structure under the canopy so leaves have
    something to dissolve away FROM in the fall->winter transition."""
    total_h = 1150 * height_scale
    trunk_r0 = 20 * height_scale
    m = Mesh()
    trunk_top_h = total_h * trunk_clear_frac
    crown_h = total_h - trunk_top_h
    profile = [(0, trunk_r0), (trunk_top_h * 0.5, trunk_r0 * 0.85),
               (trunk_top_h, trunk_r0 * 0.55)]
    m.trunk(profile, "bark_aspen", u_repeat=1.5, v_repeat=2.0)
    crown_center = (0.0, 0.0, trunk_top_h + crown_h * 0.55)
    crown_rx = 230 * height_scale
    if with_branches:
        add_deciduous_branches(m, trunk_top_h, crown_center, crown_rx,
                                crown_h * 0.65, trunk_r0 * 0.55)
    scatter_ellipsoid_crown(
        m, crown_center, rx=crown_rx, ry=crown_rx,
        rz=crown_h * 0.65, n_clusters=46, cell="aspen_leaf",
        size_w=(140 * height_scale, 230 * height_scale),
        size_h=(140 * height_scale, 220 * height_scale), n_cross=3)
    m.save(name, {"bark_aspen": BARK_TEX["bark_aspen"], "foliage": FOLIAGE_TEX})


def build_willow_scrub(name, tree_form=False):
    """Multi-stem streamside scrub - a few thin stems splaying from a common
    base, each capped with a small leaf-card clump. tree_form=True makes a
    taller single-clump variant (still multi-stem) for the tree-class slot;
    False is the low shrub-class 'bush' (no trunk collision)."""
    m = Mesh()
    n_stems = 5 if tree_form else 4
    base_h = 550 if tree_form else 220
    for i in range(n_stems):
        ang = 2 * math.pi * i / n_stems + rng.uniform(-0.3, 0.3)
        lean_amt = rng.uniform(40, 90) * (1.6 if tree_form else 1.0)
        h = base_h * rng.uniform(0.75, 1.05)
        r0 = (14 if tree_form else 9) * rng.uniform(0.8, 1.15)

        def lean(hh, ang=ang, lean_amt=lean_amt, h=h):
            t = min(hh / h, 1.0)
            return (math.cos(ang) * lean_amt * t, math.sin(ang) * lean_amt * t)

        profile = [(0, r0), (h * 0.6, r0 * 0.6), (h, r0 * 0.25)]
        top, _ = m.trunk(profile, "bark_willow", sides=5, u_repeat=1.0,
                          v_repeat=1.2, lean=lean)
        centroid = np.mean(top, axis=0)
        n_clusters = 5 if tree_form else 4
        for _ in range(n_clusters):
            off = np.array([rng.uniform(-40, 40), rng.uniform(-40, 40),
                             rng.uniform(-20, 60)])
            w = rng.uniform(70, 130) * (1.3 if tree_form else 1.0)
            hh = rng.uniform(70, 120) * (1.3 if tree_form else 1.0)
            m.card_cluster(centroid + off, w, hh, "willow_leaf", n_cross=2)
    m.save(name, {"bark_willow": BARK_TEX["bark_willow"], "foliage": FOLIAGE_TEX})


def build_sagebrush(name):
    """Low, dense, silvery-grey mound - foliage-only (no bark slot); real
    sagebrush reads almost entirely as leaf mass at this texture scale."""
    m = Mesh()
    n_clusters = 16
    for _ in range(n_clusters):
        ang = rng.uniform(0, 2 * math.pi)
        r = rng.uniform(0, 32)
        cx, cy = math.cos(ang) * r, math.sin(ang) * r
        h = rng.uniform(8, 40)
        w = rng.uniform(30, 55)
        hh = rng.uniform(30, 55)
        m.card_cluster((cx, cy, h), w, hh, "sagebrush_leaf", n_cross=2)
    m.save(name, {"foliage": FOLIAGE_TEX})


def build_grass_tuft(name):
    """Tiny ground-cover tuft - foliage-only, a handful of crossed blade
    cards. Left at the same minimal scale/role as the old blob version."""
    m = Mesh()
    for _ in range(5):
        cx, cy = rng.uniform(-14, 14), rng.uniform(-14, 14)
        w = rng.uniform(26, 40)
        hh = rng.uniform(34, 52)
        m.card_cluster((cx, cy, 0), w, hh, "grass_blade", n_cross=2)
    m.save(name, {"foliage": FOLIAGE_TEX})


def main():
    build_aspen("tree_blob", height_scale=0.92, trunk_clear_frac=0.48, with_branches=True)
    build_engelmann_spruce("tree_olive")
    build_lodgepole_pine("tree_conifer", height_scale=1.0)
    build_aspen("tree_scrub_oak", height_scale=1.22, trunk_clear_frac=0.62)
    build_krummholz("tree_manzanita")
    build_subalpine_fir("tree_yucca")
    build_lodgepole_pine("tree_canyon_oak", height_scale=1.35, crown_start_frac=0.5)
    build_willow_scrub("bush", tree_form=False)
    build_sagebrush("sagebrush")
    build_grass_tuft("grass_tuft")


if __name__ == "__main__":
    main()
