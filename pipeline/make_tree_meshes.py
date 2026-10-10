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

Detail tiers (Graphics menu "Asset Detail"): every mesh is written three
times - <name> (Standard, the shipped look), <name>_high, <name>_ultra - the
higher tiers with more/smaller foliage cards, an extra crossed plane per
cluster, rounder/smoother trunks and crown-rounded foliage normals. The
game falls back to <name> when a tier variant isn't imported.

Outputs: obj/<name>{,_high,_ultra}.obj + .mtl for each of the above. Requires
textures/T_BarkConifer.png, T_BarkAspen.png, T_BarkWillow.png,
T_FoliageAtlas.png to already exist (run build_flora_textures.py first).
"""

import math
import sys
from pathlib import Path

import numpy as np

from flora_common import cell_uv_rect

STAGE_DIR = Path(__file__).resolve().parent.parent / "data" / "stages" / \
    "tail_of_the_dragon"
OBJ_OUT = STAGE_DIR / "obj"
TEX = STAGE_DIR / "textures"
OBJ_OUT.mkdir(parents=True, exist_ok=True)

rng = np.random.default_rng(11)

# suffix -> (cluster-count mult, card-size mult, extra crossed planes,
# extra trunk sides, trunk ring subdivisions). Standard must stay exactly
# 1/1/0/0/1 so its meshes are byte-identical to the pre-tier outputs.
TIERS = {"": (1, 1.0, 0, 0, 1), "_high": (2.2, 0.8, 1, 4, 2), "_ultra": (4.0, 0.68, 1, 8, 3)}
DENS, CARD, XCROSS, SIDES, RINGS = TIERS[""]


def nc(n):
    """Cluster/branch count scaled by the current detail tier."""
    return n if DENS == 1 else int(round(n * DENS))

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
        sides += SIDES
        if RINGS > 1:
            profile = [profile[0]] + [
                (h0 + (h1 - h0) * k / RINGS, r0 + (r1 - r0) * k / RINGS)
                for (h0, r0), (h1, r1) in zip(profile, profile[1:])
                for k in range(1, RINGS + 1)]
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
        n_cross += XCROSS
        size_w, size_h = size_w * CARD, size_h * CARD
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

    def branch(self, start, end, r0, r1, mat, sides=4, cap=True, v0=0.0, v1=1.0):
        """A simple tapered tube from arbitrary 3D point `start` to `end`
        (unlike trunk(), which only stacks rings along world-Z) - used for
        the deciduous "bare twig" branch structure (2026-09 seasons
        follow-up, see build_aspen): a handful of these fanning out from the
        upper trunk gives real geometry for leaves to dissolve away from,
        without the poly cost of a proper branching system. Capped at the
        far end only (near end is buried in the trunk/a parent branch, never
        seen); low `sides` (default 4) keeps each branch cheap - this is
        rendered as thousands of HISM instances."""
        sides += SIDES // 2
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
            self._tri(a, b, c, (u0, v0), (u1, v0), (u1, v1), mat)
            self._tri(a, c, d, (u0, v0), (u1, v1), (u0, v1), mat)
        for s in range(sides if cap else 0):
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
        tris = self.tris
        if SIDES:
            # Higher tiers: bend foliage normals toward "out of the crown"
            # (from the foliage centroid) - cards then shade like a leafy
            # volume instead of a stack of flat planes. Standard keeps flat
            # face normals.
            fol = [t for t in tris if t[0] == "foliage"]
            if fol:
                ctr = np.mean([p for t in fol for p in t[1]], axis=0)
                tris = []
                for mat, pts, uvs, ns in self.tris:
                    if mat == "foliage":
                        fn = ns[0]
                        if np.dot(fn, np.mean(pts, axis=0) - ctr) < 0:
                            fn = -fn
                        ns = []
                        for p in pts:
                            d = p - ctr
                            d = d / (np.linalg.norm(d) or 1.0)
                            v = fn * 0.35 + d * 0.65
                            ns.append(v / (np.linalg.norm(v) or 1.0))
                    tris.append((mat, pts, uvs, ns))
        lines = [f"# OpenDrive card-based flora: {name}", f"mtllib {name}.mtl"]
        f_lines = []
        vi = 1
        cur_mat = None
        for mat, (a, b, c), (uva, uvb, uvc), (na, nb, nc) in tris:
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
    n_clusters = nc(n_clusters)
    if n_bands:
        n_bands = nc(n_bands)
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
    for _ in range(nc(n_clusters)):
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
    """Stunted, wind-sculpted treeline conifer ("flag + mat" form): a dense,
    low needle cushion hugging the ground (the part winter snow protects),
    a few gnarled stems bent downwind poking out of it, their needles only
    on the lee side (windward side stripped - a couple of dead bark stubs
    there instead). The earlier single leaning stick with sparse wisps
    floating off one side read as a dead sapling in grass."""
    # Rewound + replayed at the end: the old build drew exactly 24*6 values
    # from the shared rng, and every species built after this one depends on
    # that stream (their Standard meshes stay byte-identical).
    state = rng.bit_generator.state
    lee = math.pi * 0.1                                  # downwind azimuth
    ldir = np.array([math.cos(lee), math.sin(lee), 0.0])
    perp = np.array([-ldir[1], ldir[0], 0.0])
    zup = np.array([0.0, 0.0, 1.0])
    m = Mesh()

    stems = []
    for i in range(3):
        h = 300.0 if i == 0 else rng.uniform(170, 250)
        bx, by = rng.uniform(-30, 30), rng.uniform(-30, 30)
        bend = rng.uniform(0.35, 0.55) * h
        ang = lee + rng.uniform(-0.35, 0.35)

        def lean(z, h=h, bend=bend, ang=ang, bx=bx, by=by):
            t = min(z / h, 1.0)
            g = math.sin(z * 0.045) * 6.0                # gnarl
            return (bx + math.cos(ang) * bend * t * t - math.sin(ang) * g,
                    by + math.sin(ang) * bend * t * t + math.cos(ang) * g)

        r0 = rng.uniform(12, 17) * h / 300
        profile = [(0, r0), (h * 0.3, r0 * 0.8), (h * 0.65, r0 * 0.55), (h, r0 * 0.18)]
        m.trunk(profile, "bark_conifer", sides=6, u_repeat=1.0, v_repeat=1.5, lean=lean)
        stems.append((h, lean, ang, r0))
    for h, lean, ang, r0 in stems:                        # dead windward stubs
        for _ in range(2):
            z = h * rng.uniform(0.4, 0.85)
            dx, dy = lean(z)
            a = ang + math.pi + rng.uniform(-0.6, 0.6)
            ln = rng.uniform(25, 50)
            m.branch((dx, dy, z), (dx + math.cos(a) * ln, dy + math.sin(a) * ln,
                                   z + rng.uniform(0, 15)), r0 * 0.3, r0 * 0.08,
                     "bark_conifer", sides=3)

    for _ in range(nc(28)):                               # ground mat
        a = rng.uniform(0, 2 * math.pi)
        rad = math.sqrt(rng.uniform(0.05, 1.0))
        p = ldir * (70 + math.cos(a) * rad * 230) + perp * (math.sin(a) * rad * 150)
        z = rng.uniform(15, 55) * (1.15 - rad * 0.6)      # domed cushion
        out = p / (np.linalg.norm(p) or 1.0)
        m.card_cluster((p[0], p[1], z), rng.uniform(110, 170), rng.uniform(70, 110),
                       "krummholz_needle", up_dir=out * 0.7 + zup * 0.5)
    for h, lean, ang, r0 in stems:                        # lee-side flags
        n = nc(9)
        for k in range(n):
            t = 0.3 + 0.7 * (k + rng.uniform(0, 1)) / n
            z = t * h
            dx, dy = lean(z)
            a = ang + rng.uniform(-0.6, 0.6)
            reach = rng.uniform(25, 70) * (1.1 - 0.5 * t)
            out = np.array([math.cos(a), math.sin(a), 0.0])
            m.card_cluster((dx + out[0] * reach, dy + out[1] * reach, z),
                           rng.uniform(80, 130) * (1.1 - 0.4 * t), rng.uniform(70, 110),
                           "krummholz_needle", up_dir=out * 0.8 + zup * 0.45)
    m.save(name, {"bark_conifer": BARK_TEX["bark_conifer"], "foliage": FOLIAGE_TEX})
    rng.bit_generator.state = state
    rng.random(24 * 6)


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
    n_branches = nc(n_branches)
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
        for _ in range(nc(n_clusters)):
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
    for _ in range(nc(n_clusters)):
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
    for _ in range(nc(5)):
        cx, cy = rng.uniform(-14, 14), rng.uniform(-14, 14)
        w = rng.uniform(26, 40)
        hh = rng.uniform(34, 52)
        m.card_cluster((cx, cy, 0), w, hh, "grass_blade", n_cross=2)
    m.save(name, {"foliage": FOLIAGE_TEX})


# ---------------------------------------------------------------------------
# Realistic mode (PLAN-VISUALS.md Track F): `python make_tree_meshes.py --real`
# writes <slot>_real{,_high,_ultra}.obj beside the stylized meshes, picked up
# in game by ODVisualStyle::Pick (+ ODAssetDetail::Upgrade for the tiers).
# Same slot names, slot order (bark 0, foliage 1), pivot (trunk base at the
# origin) and cm scale as the stylized twin. Instead of crossed clusters
# scattered over a crown envelope, a real skeleton is grown - conifers as
# whorls of drooping/rising branches, aspens as a recursive 2-level branch
# system - and leaf/needle spray cards are laid ALONG the branches, front
# face outward, with crown-spherical normals so the canopy shades as a
# volume. The cards sample the same T_FoliageAtlas cells (the _real atlas
# keeps the layout), bark UVs keep a constant texel density (BARK_TILE_CM).
# The stylized output above is untouched: this path uses its own rngs.

REAL_TIERS = {"": (1.0, 1.0, 0, 1), "_high": (1.7, 0.8, 2, 1), "_ultra": (2.6, 0.68, 4, 2)}
# (card count mult, card size mult, extra trunk sides, branch tube segments)
REAL_TRI_BUDGET = {"": 1800, "_high": 3200, "_ultra": 6400}
BARK_TILE_CM = 90.0    # world size of one bark texture repeat
REAL_TEX = {"bark_conifer": "T_BarkConifer_real.png", "bark_aspen": "T_BarkAspen_real.png",
            "bark_willow": "T_BarkWillow_real.png", "foliage": "T_FoliageAtlas_real.png"}


def _unit(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else np.array([0.0, 0.0, 1.0])


def _dir(az, pitch):
    return np.array([math.cos(az) * math.cos(pitch), math.sin(az) * math.cos(pitch), math.sin(pitch)])


def real_card(m, base, up, roll, w, h, cell, outward):
    """One spray card: bottom edge at `base` (the attach point on the
    branch), growing along `up`; `roll` rotates its plane about `up` (0 =
    lying flat). Wound so its front face points along `outward` - the same
    winding relation as Mesh.trunk() (whose _tri normal points inward)."""
    up = _unit(up)
    flat = _unit(np.cross(up, [0.0, 0.0, 1.0])) if abs(up[2]) < 0.97 else np.array([1.0, 0.0, 0.0])
    side = np.cross(up, flat)
    right = flat * math.cos(roll) + side * math.sin(roll)
    base = np.asarray(base, dtype=float) - up * h * 0.12
    if np.dot(np.cross(up, right), outward) > 0:      # _tri normal must point inward
        right = -right
    bl, br = base - right * w / 2, base + right * w / 2
    m.quad(bl, br, br + up * h, bl + up * h, cell_uv_rect(cell), "foliage")


def real_tube(m, pts, r0, r1, mat, sides, tile=BARK_TILE_CM):
    """Tapered tube along a polyline (one Mesh.branch per segment, no tip
    cap), v running in world cm / tile so bark texel density is constant."""
    v, n = 0.0, len(pts) - 1
    for i in range(n):
        ln = float(np.linalg.norm(np.subtract(pts[i + 1], pts[i])))
        ra, rb = r0 + (r1 - r0) * i / n, r0 + (r1 - r0) * (i + 1) / n
        m.branch(pts[i], pts[i + 1], ra, rb, mat, sides=sides, cap=False, v0=v, v1=v + ln / tile)
        v += ln / tile


def real_normals(m, crown_dir):
    """Smooth (welded) outward normals on bark; crown-spherical normals on
    foliage: crown_dir(p) -> outward direction of the crown at p."""
    acc = {}
    for mat, pts, _, ns in m.tris:
        if mat != "foliage":
            for p in pts:
                k = tuple(np.round(p, 1))
                acc[k] = acc.get(k, 0) - ns[0]       # _tri normal points inward
    out = []
    for mat, pts, uvs, ns in m.tris:
        if mat == "foliage":
            front = -ns[0]
            ns = [_unit(front * 0.3 + _unit(crown_dir(p)) * 0.7) for p in pts]
        else:
            ns = [_unit(acc[tuple(np.round(p, 1))]) for p in pts]
        out.append((mat, pts, uvs, ns))
    m.tris = sorted(out, key=lambda t: t[0] == "foliage")   # bark first: slot 0


def _poly_at(pts, f):
    """Point and tangent at fraction f (0..1, by segment) of a polyline."""
    n = len(pts) - 1
    i = min(int(f * n), n - 1)
    t = f * n - i
    a, b = np.asarray(pts[i], float), np.asarray(pts[i + 1], float)
    return a + (b - a) * t, _unit(b - a)


def real_conifer(name, sfx, seed, H, r0, crown0, R, taper, whorl_dz, per_whorl,
                 pitch, tip_bend, cell, card, card_from=0.15, card_step=70.0, stubs=0):
    """Whorled conifer. pitch=(low, top) branch elevation in degrees, tip_bend
    = extra degrees on the outer half (negative droops, positive upturns),
    card=(w, h) cm, card_from = fraction along each branch where needles
    start, card_step = cm of branch per card at Standard."""
    dens, csize, xsides, segs = REAL_TIERS[sfx]
    sk = np.random.default_rng(seed)           # skeleton: identical on every tier
    cr = np.random.default_rng(seed + 7)       # cards
    ph = sk.uniform(0, 6.3, 2)
    amp = H * 0.012

    def lean(h):
        return (amp * math.sin(h / H * 2.6 + ph[0]) * h / H, amp * math.sin(h / H * 1.9 + ph[1]) * h / H)

    def rad(h):
        t = h / H
        return r0 * ((1 - t) ** 0.85 * 0.92 + 0.08 * (1 - t) + 0.25 * max(0.0, 1 - t * 12))

    m = Mesh()
    hs = [0, H * 0.03, H * 0.1] + [H * k / 8 for k in range(2, 8)] + [H * 0.94, H]
    m.trunk([(h, rad(h) if h < H else r0 * 0.04) for h in hs], "bark_conifer",
            sides=6 + xsides, u_repeat=max(1, round(2 * math.pi * r0 / BARK_TILE_CM)),
            v_repeat=H / BARK_TILE_CM, lean=lean)
    zc = H * crown0

    def axis(h):
        return np.array([*lean(h), h])

    for _ in range(stubs):                      # self-pruned dead lower limbs
        h = sk.uniform(H * 0.04, max(zc, H * 0.05))
        a = sk.uniform(0, 2 * math.pi)
        d = _dir(a, math.radians(sk.uniform(-25, 5)))
        s = axis(h) + d * rad(h) * 0.6
        real_tube(m, [s, s + d * sk.uniform(20, 55)], rad(h) * 0.22, 0.4, "bark_conifer", 3)

    h = zc
    while h < H * 0.95:
        t = (h - zc) / (H - zc)
        n = per_whorl + int(sk.integers(-1, 2))
        a0 = sk.uniform(0, 2 * math.pi)
        for k in range(n):
            a = a0 + 2 * math.pi * k / n + sk.uniform(-0.3, 0.3)
            L = R * (1 - t) ** taper * sk.uniform(0.8, 1.15) + 25
            p = math.radians(pitch[0] + (pitch[1] - pitch[0]) * t + sk.uniform(-7, 7))
            s = axis(h) + _dir(a, 0) * rad(h) * 0.5
            mid = s + _dir(a, p) * L * 0.55
            tip = mid + _dir(a, p + math.radians(tip_bend)) * L * 0.45
            pts = [s, mid, tip] if segs > 1 else [s, tip]
            real_tube(m, pts, max(1.2, rad(h) * 0.42 * (L / R) ** 0.5), 0.35, "bark_conifer", 3)
            nc_ = max(1, int(round(L * (1 - card_from) / card_step * dens)))
            for i in range(nc_):
                f = card_from + (1 - card_from) * min(1.0, (i + cr.uniform(0.2, 0.9)) / nc_)
                q, tan = _poly_at([s, mid, tip], f)
                up = _unit(tan + cr.normal(0, 0.18, 3))
                roll = cr.uniform(-0.6, 0.6) + (math.pi / 2 if cr.random() < 0.25 else 0.0)
                sc = csize * (0.65 + 0.35 * L / R) * cr.uniform(0.85, 1.15)
                real_card(m, q, up, roll, card[0] * sc, card[1] * sc, cell, q - axis(q[2]) + [0, 0, 20])
        h += whorl_dz * H * sk.uniform(0.8, 1.2) * (1 - 0.35 * t)
    for i in range(max(2, int(round(3 * dens)))):   # leader tuft
        a = 2 * math.pi * i / 3 + cr.uniform(-0.4, 0.4)
        q = axis(H * cr.uniform(0.88, 0.95))
        real_card(m, q, _dir(a, math.radians(72)), cr.uniform(-1.5, 1.5),
                  card[0] * 0.6 * csize, card[1] * 0.75 * csize, cell, _dir(a, 0))
    real_normals(m, lambda p: p - axis(p[2]) + [0, 0, 0.35 * R])
    m.save(name + "_real" + sfx, {"bark_conifer": REAL_TEX["bark_conifer"], "foliage": REAL_TEX["foliage"]})


def real_aspen(name, sfx, seed, hs, clear_frac):
    """Quaking aspen: straight pale trunk with a thin leader, main limbs
    rising at 35-65 deg from the upper trunk, each splitting into 2-3
    twigs; leaf cards cluster on the outer twigs with random roll (aspen
    leaves flutter in every orientation). The twigs are real bark geometry,
    so LeafDrop leaves a believable bare crown."""
    dens, csize, xsides, segs = REAL_TIERS[sfx]
    sk = np.random.default_rng(seed)
    cr = np.random.default_rng(seed + 7)
    H, r0 = 1150 * hs, 19 * hs
    hc = H * clear_frac
    ph = sk.uniform(0, 6.3, 2)

    def lean(h):
        return (H * 0.015 * math.sin(h / H * 2.2 + ph[0]) * h / H, H * 0.01 * math.sin(h / H * 3.1 + ph[1]) * h / H)

    def axis(h):
        return np.array([*lean(h), h])

    def rad(h):
        return r0 * (1.0 - 0.8 * h / H + 0.2 * max(0.0, 1 - h / H * 14))

    m = Mesh()
    top = H * 0.9
    m.trunk([(h, rad(h)) for h in (0, H * 0.04, H * 0.2, hc * 0.7, hc, (hc + top) / 2, top)],
            "bark_aspen", sides=7 + xsides, u_repeat=max(1, round(2 * math.pi * r0 / BARK_TILE_CM)),
            v_repeat=top / BARK_TILE_CM, lean=lean)
    ctr = axis(hc + (H - hc) * 0.55)
    crown_r = 300 * hs
    tips = []
    n_limbs = 11
    for k in range(n_limbs):
        h = hc + (top - hc) * (k + sk.uniform(0.1, 0.9)) / n_limbs
        t = (h - hc) / (top - hc)
        a = k * 2.4 + sk.uniform(-0.3, 0.3)                 # golden-angle spiral
        p = math.radians(sk.uniform(35, 55) + 15 * t)
        L = crown_r * (1.05 - 0.55 * t) * sk.uniform(0.8, 1.1)
        s = axis(h)
        mid = s + _dir(a, p) * L * 0.5
        end = mid + _dir(a + sk.uniform(-0.3, 0.3), p + math.radians(sk.uniform(-10, 15))) * L * 0.5
        rl = max(2.0, rad(h) * 0.55)
        real_tube(m, [s, mid, end] if segs > 1 else [s, end], rl, rl * 0.25, "bark_aspen", 4)
        tips.append((end, _unit(end - mid), L * 0.35))
        for j in range(int(sk.integers(2, 4))):              # twigs off the outer half
            q, tan = _poly_at([s, mid, end], sk.uniform(0.45, 0.85))
            d = _unit(tan + _dir(a + sk.choice([-1, 1]) * sk.uniform(0.6, 1.2), sk.uniform(0.0, 0.8)) * 0.9)
            tl = L * sk.uniform(0.35, 0.55)
            real_tube(m, [q, q + d * tl], rl * 0.3, 0.3, "bark_aspen", 3)
            tips.append((q + d * tl, d, tl))
    tips.append((axis(top), np.array([0.0, 0.0, 1.0]), H - top))
    for end, d, ln in tips:                                  # leaf clusters on the outer twigs
        n = max(2, int(round(5.0 * dens * min(1.6, ln / 90))))
        for i in range(n):
            f = cr.uniform(0.0, 1.0)
            q = end - d * ln * f * 0.8 + cr.normal(0, 30 * hs, 3)
            up = _unit(d + cr.normal(0, 0.5, 3))
            sc = csize * hs * cr.uniform(0.85, 1.2)
            real_card(m, q, up, cr.uniform(-math.pi, math.pi), 145 * sc, 145 * sc, "aspen_leaf", q - ctr)
    real_normals(m, lambda p: (p - ctr) * [1, 1, 0.8] + [0, 0, 0.2 * crown_r])
    m.save(name + "_real" + sfx, {"bark_aspen": REAL_TEX["bark_aspen"], "foliage": REAL_TEX["foliage"]})


def _real_bent_stem(sk, base, ang, h, bend, n, gnarl=0.0, power=2.0):
    """Polyline stem from `base` rising `h` cm, bending `bend` cm toward
    azimuth `ang` (bend grows as height**power), with optional sideways
    gnarl wobble."""
    side = _dir(ang + math.pi / 2, 0)
    ph = sk.uniform(0, 6.3)
    return [np.asarray(base, float) + _dir(ang, 0) * bend * (k / n) ** power
            + side * gnarl * math.sin(k * 1.7 + ph) * (k / n) + [0, 0, h * k / n] for k in range(n + 1)]


def real_krummholz(name, sfx, seed=707):
    """Treeline krummholz ("flag + mat"): a dense needle cushion lying over
    the ground, three gnarled stems bent downwind, needles only on short
    lee-side limbs (dead bark stubs to windward). Same footprint, height
    and lee azimuth as the stylized build_krummholz."""
    dens, csize, xsides, segs = REAL_TIERS[sfx]
    sk = np.random.default_rng(seed)
    cr = np.random.default_rng(seed + 7)
    lee = math.pi * 0.1
    ldir, perp = _dir(lee, 0), _dir(lee + math.pi / 2, 0)
    m = Mesh()
    limbs = []
    for i in range(3):
        h = 300.0 if i == 0 else sk.uniform(170, 250)
        ang = lee + sk.uniform(-0.35, 0.35)
        pts = _real_bent_stem(sk, (sk.uniform(-30, 30), sk.uniform(-30, 30), 0.0), ang, h,
                              sk.uniform(0.35, 0.55) * h, 3 + 2 * segs, gnarl=7.0)
        r0 = sk.uniform(12, 17) * h / 300
        real_tube(m, pts, r0, r0 * 0.18, "bark_conifer", 5 + xsides // 2)
        for _ in range(2):                                   # dead windward stubs
            q, _ = _poly_at(pts, sk.uniform(0.4, 0.85))
            d = _dir(ang + math.pi + sk.uniform(-0.6, 0.6), sk.uniform(-0.1, 0.3))
            real_tube(m, [q, q + d * sk.uniform(25, 50)], r0 * 0.3, 0.4, "bark_conifer", 3)
        for k in range(6):                                   # lee-side flag limbs
            f = 0.3 + 0.7 * (k + sk.uniform(0.1, 0.9)) / 6
            q, _ = _poly_at(pts, f)
            d = _dir(ang + sk.uniform(-0.7, 0.7), math.radians(sk.uniform(-15, 20)))
            ln = sk.uniform(35, 80) * (1.1 - 0.5 * f)
            real_tube(m, [q, q + d * ln], max(1.0, r0 * 0.25 * (1 - f)), 0.4, "bark_conifer", 3)
            limbs.append((q, d, ln, f))
    for q, d, ln, f in limbs:
        for i in range(max(1, int(round(2.2 * dens)))):
            p = q + d * ln * cr.uniform(0.1, 0.8)
            up = _unit(d + cr.normal(0, 0.25, 3) + [0, 0, 0.25])
            sc = csize * (1.15 - 0.4 * f) * cr.uniform(0.85, 1.15)
            real_card(m, p, up, cr.uniform(-0.6, 0.6), 95 * sc, 85 * sc, "krummholz_needle", d)
    for _ in range(int(round(26 * dens))):                  # ground cushion
        a = cr.uniform(0, 2 * math.pi)
        rad = math.sqrt(cr.uniform(0.05, 1.0))
        p = ldir * (70 + math.cos(a) * rad * 230) + perp * (math.sin(a) * rad * 150)
        out = _unit(p)
        p = p + [0, 0, cr.uniform(18, 50) * (1.15 - rad * 0.6)]
        up = _unit(out * 0.85 + [0, 0, 0.4] + cr.normal(0, 0.15, 3))
        sc = csize * cr.uniform(0.85, 1.15)
        real_card(m, p, up, cr.uniform(-0.4, 0.4), 150 * sc, 95 * sc, "krummholz_needle", out + [0, 0, 1])
    ctr = ldir * 70
    real_normals(m, lambda p: (p - ctr) * [1, 1, 0.6] + [0, 0, 120])
    m.save(name + "_real" + sfx, {"bark_conifer": REAL_TEX["bark_conifer"], "foliage": REAL_TEX["foliage"]})


def real_willow(name, sfx, seed=808):
    """Streamside willow scrub: six thin stems splaying from one root
    crown, a couple of side twigs each, narrow-leaf sprays along the outer
    half. Stylized `bush` envelope (~2.2 m tall, ~1.5 m wide)."""
    dens, csize, xsides, segs = REAL_TIERS[sfx]
    sk = np.random.default_rng(seed)
    cr = np.random.default_rng(seed + 7)
    m = Mesh()
    shoots = []
    for i in range(6):
        ang = 2 * math.pi * i / 6 + sk.uniform(-0.3, 0.3)
        h = 220 * sk.uniform(0.75, 1.05)
        pts = _real_bent_stem(sk, (sk.uniform(-8, 8), sk.uniform(-8, 8), 0.0), ang, h,
                              sk.uniform(40, 90), 2 + segs, gnarl=3.0, power=1.4)
        r0 = sk.uniform(4.5, 7.0)
        real_tube(m, pts, r0, 0.6, "bark_willow", 4 + xsides // 2)
        shoots.append((pts, 1.0))
        for _ in range(int(sk.integers(1, 3))):
            q, tan = _poly_at(pts, sk.uniform(0.4, 0.8))
            d = _unit(tan + _dir(ang + sk.choice([-1, 1]) * sk.uniform(0.5, 1.0), 0.3) * 0.8)
            tl = sk.uniform(40, 70)
            real_tube(m, [q, q + d * tl], r0 * 0.35, 0.3, "bark_willow", 3)
            shoots.append(([q, q + d * tl], 0.5))
    ctr = np.array([0.0, 0.0, 110.0])
    for pts, wgt in shoots:
        for _ in range(max(1, int(round(6 * wgt * dens)))):
            q, tan = _poly_at(pts, cr.uniform(0.45, 1.0))
            up = _unit(tan + cr.normal(0, 0.45, 3))
            sc = csize * cr.uniform(0.85, 1.15)
            real_card(m, q, up, cr.uniform(-math.pi, math.pi), 85 * sc, 95 * sc, "willow_leaf", q - ctr)
    real_normals(m, lambda p: (p - ctr) * [1, 1, 0.8] + [0, 0, 40])
    m.save(name + "_real" + sfx, {"bark_willow": REAL_TEX["bark_willow"], "foliage": REAL_TEX["foliage"]})


def real_ground_cards(name, sfx, seed, n, r, pitch, h, w, cell, crown_z, roll=0.4):
    """Foliage-only mound (sagebrush, grass tuft): `n` spray cards rising
    from a disc of radius `r`, leaning outward at `pitch` degrees, with
    their bottom edge on the ground (ScrubMeshBaseOffsetCm reads min Z).
    Single material slot, like the stylized twin."""
    dens = REAL_TIERS[sfx][0]
    cr = np.random.default_rng(seed)
    m = Mesh()
    k = max(n, int(round(n * dens)))
    for i in range(k):
        a = 2 * math.pi * (i + cr.uniform(-0.4, 0.4)) / k
        rr_ = r * math.sqrt(cr.uniform(0, 1))
        hh, ww = cr.uniform(*h), cr.uniform(*w)
        up = _dir(a + cr.uniform(-0.3, 0.3), math.radians(cr.uniform(*pitch)))
        base = _dir(a, 0) * rr_ + up * hh * 0.12            # undo real_card's sink
        real_card(m, base, up, cr.uniform(-roll, roll), ww, hh, cell, _dir(a, 0) + [0, 0, 0.5])
    lift = np.array([0.0, 0.0, max(0.0, -min(p[2] for t in m.tris for p in t[1]))])   # rolled corners
    m.tris = [(mt, tuple(p + lift for p in pts), uv, ns) for mt, pts, uv, ns in m.tris]
    real_normals(m, lambda p: p * [1, 1, 0] + [0, 0, crown_z])
    m.save(name + "_real" + sfx, {"foliage": REAL_TEX["foliage"]})


# slot -> builder; slots not listed keep their stylized mesh in Realistic
# (they still render the _real atlas through M_Foliage_real's season MID).
REAL_SPECIES = {
    "tree_conifer": lambda s: real_conifer(        # Lodgepole A - dominant evergreen
        "tree_conifer", s, 101, H=1650, r0=24, crown0=0.45, R=290, taper=1.1, whorl_dz=0.042,
        per_whorl=5, pitch=(-4, 28), tip_bend=8, cell="pine_needle", card=(125, 150),
        card_from=0.3, card_step=26, stubs=9),
    "tree_canyon_oak": lambda s: real_conifer(     # Lodgepole B - mature backdrop
        "tree_canyon_oak", s, 202, H=2200, r0=32, crown0=0.55, R=360, taper=1.0, whorl_dz=0.038,
        per_whorl=5, pitch=(-8, 25), tip_bend=10, cell="pine_needle", card=(140, 165),
        card_from=0.3, card_step=30, stubs=12),
    "tree_olive": lambda s: real_conifer(          # Engelmann spruce - full, drooping
        "tree_olive", s, 303, H=1500, r0=28, crown0=0.12, R=330, taper=0.9, whorl_dz=0.05,
        per_whorl=5, pitch=(-22, 10), tip_bend=14, cell="spruce_needle", card=(120, 130),
        card_from=0.12, card_step=42, stubs=3),
    "tree_yucca": lambda s: real_conifer(          # Subalpine fir - narrow spire
        "tree_yucca", s, 404, H=1400, r0=22, crown0=0.08, R=200, taper=1.45, whorl_dz=0.05,
        per_whorl=5, pitch=(-12, 18), tip_bend=8, cell="fir_needle", card=(95, 110),
        card_from=0.1, card_step=38, stubs=2),
    "tree_blob": lambda s: real_aspen("tree_blob", s, 505, hs=0.92, clear_frac=0.48),
    "tree_scrub_oak": lambda s: real_aspen("tree_scrub_oak", s, 606, hs=1.22, clear_frac=0.6),
    "tree_manzanita": lambda s: real_krummholz("tree_manzanita", s),
    "bush": lambda s: real_willow("bush", s),
    "sagebrush": lambda s: real_ground_cards(      # ~70 cm silver-grey mound
        "sagebrush", s, 909, n=22, r=30, pitch=(48, 78), h=(45, 70), w=(40, 60),
        cell="sagebrush_leaf", crown_z=30, roll=1.2),
    "grass_tuft": lambda s: real_ground_cards(     # same 35-55 cm tuft as stylized
        "grass_tuft", s, 1010, n=6, r=10, pitch=(66, 84), h=(36, 54), w=(26, 38),
        cell="grass_blade", crown_z=60),
}
# Per-slot triangle budgets where REAL_TRI_BUDGET (trees) doesn't fit:
# ground cover is drawn in the hundreds of thousands, so it stays close to
# the stylized count (sagebrush 64 / grass 20 tris at Standard).
REAL_TRI_BUDGET_SLOT = {"sagebrush": {"": 96, "_high": 160, "_ultra": 240},
                        "grass_tuft": {"": 24, "_high": 40, "_ultra": 64}}


def main_real():
    global DENS, CARD, XCROSS, SIDES, RINGS
    DENS, CARD, XCROSS, SIDES, RINGS = TIERS[""]     # Mesh helpers at their plain settings
    for sfx in REAL_TIERS:
        for build in REAL_SPECIES.values():
            build(sfx)


def main():
    global rng, DENS, CARD, XCROSS, SIDES, RINGS
    if "--real" in sys.argv:
        main_real()
        return
    for suffix, tier in TIERS.items():
        rng = np.random.default_rng(11)
        DENS, CARD, XCROSS, SIDES, RINGS = tier
        build_all(suffix)


def build_all(sfx):
    build_aspen("tree_blob" + sfx, height_scale=0.92, trunk_clear_frac=0.48, with_branches=True)
    build_engelmann_spruce("tree_olive" + sfx)
    build_lodgepole_pine("tree_conifer" + sfx, height_scale=1.0)
    build_aspen("tree_scrub_oak" + sfx, height_scale=1.22, trunk_clear_frac=0.62)
    build_krummholz("tree_manzanita" + sfx)
    build_subalpine_fir("tree_yucca" + sfx)
    build_lodgepole_pine("tree_canyon_oak" + sfx, height_scale=1.35, crown_start_frac=0.5)
    build_willow_scrub("bush" + sfx, tree_form=False)
    build_sagebrush("sagebrush" + sfx)
    build_grass_tuft("grass_tuft" + sfx)


if __name__ == "__main__":
    main()
