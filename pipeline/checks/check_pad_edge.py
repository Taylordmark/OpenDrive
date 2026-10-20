"""Junction road-edge check (PLAN-VISUALS join J, sibling of
check_terrain_real.check_road_edge). Mirrors ODRoadManager.cpp's
ComputeEdgeCodes (the UV3.y edge code, Realistic only) and the junction part
of M_Road*_real's fringe (unreal/apply_road_material.py RealEdge*), runs them
over shipped ODR3 road tiles and asserts:

1. edge fully gravel: every visible (uncovered) pad rim and every apron
   triangle decodes to d_in 0, so the fringe there is 1.
2. interior untouched: no fringe on a pad more than the fringe's reach from
   a visible rim; a ribbon's pavement edge inside a pad keeps no fringe, and
   one outside every pad keeps the plain road fringe; ribbon triangles never
   read as pad (lanes and paint unchanged).
3. same width as road edges: the decoded d_in matches the true distance to
   the rim, and the fringe width along pad rims (world-XY noise) has the
   road's mean and spread.

Constants are parsed from the C++ and the material script.
Usage:  python pipeline/checks/check_pad_edge.py [area ...]
        (default: deals_gap far_gasquet sf_russian_hill)
"""
import re
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_terrain_real import R, ROOT, road_value_noise2

CPP = (ROOT / "OpenDriveUE/Source/OpenDriveUE/ODRoadManager.cpp").read_text(encoding="utf-8")
K = {n: float(re.search(rf"constexpr float {n} = ([\d.]+)f;", CPP).group(1))
     for n in ("EdgeCodeCapM", "PadApronOutCm", "PadApronDropCm", "PadApronTolCm",
               "EdgeCoverMarginCm", "EdgeSameLevelCm", "EdgeGridCm")}
CAP = K["EdgeCodeCapM"]
assert R["RealEdgeCodeCapM"] == CAP, "material RealEdgeCodeCapM != ODRoadManager EdgeCodeCapM"
TILES = ROOT / "OpenDriveUE/Content/OpenDrive/MasksV2"
REACH = R["RealEdgeMaxM"] + R["RealEdgeCrumbleM"]          # widest fringe, before softness


def read_tile(path):
    b = path.read_bytes()
    assert b[:4] == b"ODR3"
    off, out = 24, []
    for _ in range(struct.unpack_from("<I", b, 20)[0]):
        vc, tc = struct.unpack_from("<II", b, off)
        off += 11
        V = np.frombuffer(b, "<f4", vc * 3, off).reshape(-1, 3).astype(np.float64)
        off += vc * 12
        U0 = np.frombuffer(b, "<f4", vc * 2, off).reshape(-1, 2)
        off += vc * 16                                     # uv0 + uv1
        T = np.frombuffer(b, "<u4", tc * 3, off).reshape(-1, 3).astype(np.int64)
        off += tc * 12
        out.append((V, U0, T))
    return out


# ------------------------------------------------------------ C++ mirror
def ribbon_vert_count(U0):
    inner = np.flatnonzero((U0[:, 0] > 1e-3) & (U0[:, 0] < 1 - 1e-3))
    return int(inner[0]) if len(inner) else len(U0)


def seg_dist_m(P, A, B, open_a=False, open_b=False):
    """EdgeSegDistM, vectorised over P (n, 2+); open_a/open_b: the segment runs on
    past that end (to the line there - affine, so exact under interpolation)."""
    ab, ap = B[:2] - A[:2], P[:, :2] - A[:2]
    t = ap @ ab / max(ab @ ab, 1e-6)
    t = np.clip(t, -np.inf if open_a else 0.0, np.inf if open_b else 1.0)
    return np.hypot(*(ap - t[:, None] * ab).T) / 100.0


class Surfaces:
    """Pad tops + ribbon pavement of one tile, gridded (EdgeInTri/Inside)."""

    def __init__(self, tris, pad):
        self.t, self.pad = np.asarray(tris).reshape(-1, 3, 3), np.asarray(pad, bool)
        self.lo, self.hi = self.t[:, :, :2].min(1), self.t[:, :, :2].max(1)
        g, self.grid = K["EdgeGridCm"], {}
        for i in range(len(self.t)):
            for gx in range(int(self.lo[i, 0] // g), int(self.hi[i, 0] // g) + 1):
                for gy in range(int(self.lo[i, 1] // g), int(self.hi[i, 1] // g) + 1):
                    self.grid.setdefault((gx, gy), []).append(i)

    def inside(self, p, margin, pads_only):
        g = K["EdgeGridCm"]
        idx = np.array(self.grid.get((int(p[0] // g), int(p[1] // g)), []), dtype=int)
        if pads_only and len(idx):
            idx = idx[self.pad[idx]]
        if not len(idx):
            return False
        lo, hi = self.lo[idx], self.hi[idx]
        idx = idx[(p[0] >= lo[:, 0]) & (p[0] <= hi[:, 0]) & (p[1] >= lo[:, 1]) & (p[1] <= hi[:, 1])]
        if not len(idx):
            return False
        t = self.t[idx]
        s = []
        for a, b in ((0, 1), (1, 2), (2, 0)):
            u, v = t[:, a, :2], t[:, b, :2]
            n = np.stack([v[:, 1] - u[:, 1], u[:, 0] - v[:, 0]], 1)
            ln = np.hypot(*n.T)
            s.append(np.where(ln > 1e-3, ((p[:2] - u) * n).sum(1) / np.maximum(ln, 1e-9), 0.0))
        s = np.array(s)
        ok = (s > margin).all(0) | (s < -margin).all(0)
        e1, e2, q = t[:, 1, :2] - t[:, 0, :2], t[:, 2, :2] - t[:, 0, :2], p[:2] - t[:, 0, :2]
        d = e1[:, 0] * e2[:, 1] - e2[:, 0] * e1[:, 1]
        ok &= np.abs(d) >= 1e-3
        d = np.where(ok, d, 1.0)
        l1 = (q[:, 0] * e2[:, 1] - e2[:, 0] * q[:, 1]) / d
        l2 = (e1[:, 0] * q[:, 1] - q[:, 0] * e1[:, 1]) / d
        z = t[:, 0, 2] + l1 * (t[:, 1, 2] - t[:, 0, 2]) + l2 * (t[:, 2, 2] - t[:, 0, 2])
        return bool((ok & (np.abs(z - p[2]) < K["EdgeSameLevelCm"])).any())


def edge_codes(chunks):
    """ComputeEdgeCodes: per chunk (corner codes (m, 3) or None, info)."""
    infos, tris, pad = [], [], []
    for V, U0, T in chunks:
        n, nr = len(V), ribbon_vert_count(U0)
        T = T[(T < n).all(1)]
        rim, apron = np.zeros(n, bool), np.zeros(n, bool)
        e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
        e = e[(e >= nr).all(1)]
        d = V[e[:, 1]] - V[e[:, 0]]
        m = ((np.abs(np.hypot(d[:, 0], d[:, 1]) - K["PadApronOutCm"]) < K["PadApronTolCm"])
             & (np.abs(np.abs(d[:, 2]) - K["PadApronDropCm"]) < K["PadApronTolCm"]))
        dn = d[m, 2] < 0
        rim[np.where(dn, e[m, 0], e[m, 1])] = True
        apron[np.where(dn, e[m, 1], e[m, 0])] = True
        top = T[(T >= nr).all(1) & ~apron[T].any(1)]
        col = T % 7
        rib = T[(T < nr).all(1) & ((col == 2) | (col == 3)).all(1)]
        tris += [V[top], V[rib]]
        pad += [np.ones(len(top), bool), np.zeros(len(rib), bool)]
        infos.append(dict(V=V, T=T, nr=nr, rim=rim, apron=apron, top=top))
    if not any(i["rim"].any() for i in infos):
        return [(None, i) for i in infos], None
    surf = Surfaces(np.concatenate(tris), np.concatenate(pad))
    out = []
    for I in infos:
        V, T, nr, rim, apron = I["V"], I["T"], I["nr"], I["rim"], I["apron"]
        n = len(V)
        code = np.zeros(n)
        covered = np.zeros(n, bool)
        for v in np.flatnonzero(rim):
            covered[v] = surf.inside(V[v], K["EdgeCoverMarginCm"], False)
        open_ = rim & ~covered
        segs = []
        for k in range(3):
            a, b, x = T[:, k], T[:, (k + 1) % 3], T[:, (k + 2) % 3]
            ok = (np.minimum(np.minimum(a, b), x) >= nr) & ~apron[x] & open_[a] & open_[b]
            segs += list(zip(a[ok], b[ok]))
        any_ = nr < n
        near = np.full(n, -1)   # each pad vertex's nearest uncovered rim edge
        if nr < n:
            E = np.full(n - nr, CAP)
            for i, (a, b) in enumerate(segs):
                d = seg_dist_m(V[nr:], V[a], V[b])
                near[nr:][d < E] = i
                E = np.minimum(E, d)
            code[nr:] = np.where(apron[nr:], 1.0, 1.0 + E)
        nst = nr // 7
        crossings = {}
        if nst >= 2 and nr % 7 == 0:
            for c in (2, 3):
                P = V[np.arange(nst) * 7 + c]
                inn = np.array([surf.inside(p, 0.0, True) for p in P])
                ln = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1) / 100.0)])
                cross, gap = [], []
                for s in np.flatnonzero(inn[:-1] != inn[1:]):
                    lo, hi = 0.0, 1.0
                    for _ in range(12):
                        mid = 0.5 * (lo + hi)
                        if surf.inside(P[s] + (P[s + 1] - P[s]) * mid, 0.0, True) == inn[s]:
                            lo = mid
                        else:
                            hi = mid
                    cross.append(ln[s] + (ln[s + 1] - ln[s]) * 0.5 * (lo + hi))
                    gap.append(s)
                # an inside station between two crossings takes the further one of its
                # own gaps: the ribbon's fringe then never reaches into the pad (it
                # stops short outside instead)
                D = np.full(nst, CAP)
                for x in cross:
                    D = np.minimum(D, np.abs(ln - x))
                for x, g in zip(cross, gap):
                    for st in (g, g + 1):
                        if inn[st]:
                            D[st] = max(D[st], abs(ln[st] - x))
                sc = np.where(inn, -D, D) - CAP
                any_ |= bool((sc != 0).any())
                for k in ((0, 1, 2, 6) if c == 2 else (3, 4, 5)):
                    code[np.arange(nst) * 7 + k] = sc
                crossings[c] = (P, ln, cross)
        I.update(covered=covered, open=open_, segs=segs, crossings=crossings, code=code)
        if not any_:
            out.append((None, I))
            continue
        cc = code[T].copy()
        padtri = (T >= nr).all(1)
        apron_tri = padtri & apron[T].any(1)
        cc[apron_tri & (apron[T] | rim[T]).all(1)] = 1.0
        # a rim triangle measures to its own edge, running on as the line past an end
        # where the visible rim carries on (affine, so exact under interpolation);
        # any other keeps its corners' nearest-edge distances unless those edges lie on
        # different rim chains (overlapping pads), where min-of-distances interpolates
        # low: it then measures to the one closest to its centre (convex, never low)
        nseg = np.zeros(n, int)
        chain = list(range(n))

        def root(v):
            while chain[v] != v:
                chain[v] = chain[chain[v]]
                v = chain[v]
            return v
        for a, b in segs:
            nseg[a] += 1
            nseg[b] += 1
            chain[root(a)] = root(b)
        for t in np.flatnonzero(padtri & ~apron_tri):
            own = [(T[t, k], T[t, (k + 1) % 3]) for k in range(3) if open_[T[t, k]] and open_[T[t, (k + 1) % 3]]]
            if own:
                a, b = own[0]
            else:
                ids = {i for i in near[T[t]] if i >= 0}
                if len({root(segs[i][0]) for i in ids}) < 2:
                    continue
                ctr = V[T[t]].mean(0, keepdims=True)
                a, b = segs[min(ids, key=lambda i: seg_dist_m(ctr, V[segs[i][0]], V[segs[i][1]])[0])]
            cc[t] = 1.0 + np.minimum(CAP, seg_dist_m(V[T[t]], V[a], V[b], nseg[a] > 1, nseg[b] > 1))
        out.append((cc, I))
    return out, surf


# ------------------------------------------------------------ material mirror
def decode(code, d_rib):
    """apply_road_material.py: on_pad / rib_keep / d_in from the edge code."""
    on_pad = np.clip((code - 0.5) * 1000.0, 0, 1)
    rib_keep = np.clip((code + CAP) / R["RealPadRibFadeM"] + 0.5, 0, 1)
    d_rib = d_rib + (1 - rib_keep) * R["RealEdgeOffM"]
    return np.minimum(d_rib, R["RealEdgeOffM"] + (code - 1.0 - R["RealEdgeOffM"]) * on_pad), rib_keep


def pad_width(xy_cm):
    """edge_w on a pad: value noise in world XY wrapped like world_noise."""
    def wn(cell_m):
        w = (xy_cm / (cell_m * 100.0 * 97.0)) % 1.0 * 97.0
        return road_value_noise2(w[:, 0], w[:, 1])
    return (R["RealEdgeMinM"] + (R["RealEdgeMaxM"] - R["RealEdgeMinM"]) * wn(R["RealEdgeCellM"])
            + (wn(R["RealEdgeCrumbleCellM"]) - 0.5) * 2 * R["RealEdgeCrumbleM"])


def fringe(w, d_in, soft):
    return np.maximum(np.clip((w - d_in) / soft, 0, 1), np.clip(1 - d_in / R["RealEdgeLipM"], 0, 1))


# ------------------------------------------------------------ checks
def check_tile(chunks, rng, acc):
    res, surf = edge_codes(chunks)
    if surf is None:
        return
    soft_near, soft_far = R["RealEdgeSoftM"], R["RealEdgeFarSoftM"]
    open_segs = [(I["V"][a], I["V"][b]) for _, I in res for a, b in I["segs"]]
    for cc, I in res:
        if cc is None:
            continue
        V, T, nr = I["V"], I["T"], I["nr"]
        padtri = (T >= nr).all(1)
        ribtri = (T < nr).all(1)
        acc["pads"] += int(I["rim"].sum())
        acc["covered"] += int(I["covered"].sum())
        # ribbon triangles never decode as pad: lanes/paint keep the ribbon path
        assert (cc[ribtri] <= 0.0).all(), "ribbon triangle reads as junction pad"
        # 1. visible rim and apron fully gravel
        apron_tri = padtri & I["apron"][T].any(1) & (I["apron"][T] | I["rim"][T]).all(1)
        assert (cc[apron_tri] == 1.0).all(), "apron triangle not d_in 0"
        for a, b in I["segs"]:
            hit = [t for t in np.flatnonzero(padtri & ~I["apron"][T].any(1))
                   if {a, b} <= set(T[t])]
            for t in hit:
                k = [list(T[t]).index(a), list(T[t]).index(b)]
                assert (cc[t, k] == 1.0).all(), "visible rim not d_in 0"
            p = V[a] + (V[b] - V[a]) * rng.random((4, 1))
            for soft in (soft_near, soft_far):
                assert (fringe(pad_width(p[:, :2]), 0.0, soft) == 1.0).all(), "pad rim not fully gravel"
            acc["rim_len_m"] += np.linalg.norm(V[b, :2] - V[a, :2]) / 100.0
            acc["w"].append(pad_width(p[:, :2]))
        # 2./3. interior and width: random points on every pad top triangle
        top = np.flatnonzero(padtri & ~I["apron"][T].any(1))
        if len(top):
            r = rng.random((len(top), 2))
            r = np.where(r.sum(1, keepdims=True) > 1, 1 - r, r)
            lam = np.c_[1 - r.sum(1), r]
            p = (V[T[top]] * lam[:, :, None]).sum(1)
            code = (cc[top] * lam).sum(1)
            d_in, _ = decode(code, np.full(len(top), 2.5))
            true = np.full(len(top), CAP)
            for a, b in open_segs:
                true = np.minimum(true, seg_dist_m(p, a, b))
            f = fringe(pad_width(p[:, :2]), d_in, soft_far)
            # per-vertex distances interpolate a little low across a long fan triangle
            # off a concave stretch of rim: a few dm of extra gravel there, bounded here
            bad = (f > 0) & (true > REACH + soft_far + 0.05)
            assert not (bad & (true > 1.0)).any(), f"pad interior fringed at {p[bad][:3]} (true rim distance {true[bad][:3]})"
            acc["leak"] += int(bad.sum())
            # triangles on a visible rim edge carry the exact (affine) distance; the rest
            # interpolate per-corner minima, which only ever overestimate -> narrower, never wider
            o = I["open"][T[top]]
            on_rim = (o & np.roll(o, -1, axis=1)).any(1)
            band = true < 0.6
            e = np.minimum(d_in, CAP) - true
            acc["err"].append(np.abs(e)[band & on_rim])
            acc["err_signed"].append(e[band])
            acc["pad_pts"] += len(top)
        # ribbon pavement edges: dense walk vs the station-interpolated code. Exact
        # through a lone crossing; with two crossings in neighbouring station gaps (a
        # ribbon clipping a pad corner) the fringe stops short outside by design, and a
        # pad corner poking in and out between two stations can't be represented per
        # vertex at all: both skipped (counted)
        for c, (P, ln, cross) in I["crossings"].items():
            if not cross:
                continue
            st_code = I["code"][np.arange(len(P)) * 7 + c]
            flips = {}
            for x in cross:
                for dl in np.arange(-3.0, 3.0, 0.02):
                    l = x + dl
                    s = np.searchsorted(ln, l) - 1
                    if s < 0 or s + 1 >= len(P) or l < 0.5 or l > ln[-1] - 0.5:
                        continue
                    f_ = (l - ln[s]) / max(ln[s + 1] - ln[s], 1e-9)
                    q = P[s] + (P[s + 1] - P[s]) * f_
                    code = st_code[s] + (st_code[s + 1] - st_code[s]) * f_
                    _, keep = decode(np.array([code]), np.zeros(1))
                    near = [surf.inside(P[s] + (P[s + 1] - P[s]) * ((l + o - ln[s]) / max(ln[s + 1] - ln[s], 1e-9)),
                                        0.0, True) for o in (-0.5, 0.0, 0.5)]
                    st_in = st_code[s:s + 2] < -CAP
                    lo_, hi_ = ln[max(s - 1, 0)], ln[min(s + 2, len(ln) - 1)]
                    pair = sum(lo_ <= y <= hi_ for y in cross) > 1
                    if s not in flips:
                        smp = [surf.inside(P[s] + (P[s + 1] - P[s]) * u, 0.0, True) for u in np.linspace(0, 1, 101)]
                        flips[s] = int(np.sum(np.diff(smp) != 0))
                    pair |= flips[s] > 1
                    if (all(near) and (not st_in.any() or flips[s] > 1)) or (not any(near) and (st_in.all() or pair)):
                        acc["rib_skip"] += 1
                    elif all(near):
                        assert keep[0] == 0.0, f"ribbon edge fringed inside a pad at {q}: code {code} l {l} cross {cross} ln {ln[max(s-2,0):s+4]} st {st_code[max(s-2,0):s+4]}"
                        acc["rib_in"] += 1
                    elif not any(near):
                        assert keep[0] == 1.0, f"ribbon edge lost its fringe outside a pad at {q}: code {code} l {l} cross {cross} ln {ln[max(s-2,0):s+4]} st {st_code[max(s-2,0):s+4]}"
                        acc["rib_out"] += 1


def main():
    areas = sys.argv[1:] or ["deals_gap", "far_gasquet", "sf_russian_hill"]
    rng = np.random.default_rng(7)
    acc = dict(pads=0, covered=0, rim_len_m=0.0, w=[], err=[], err_signed=[], pad_pts=0, leak=0, rib_in=0, rib_out=0, rib_skip=0)
    for area in areas:
        files = sorted(TILES.glob(f"roadv3_{area}_test_tile_*.bin"))
        assert files, f"no ODR3 tiles for {area}"
        for f in files:
            check_tile(read_tile(f), rng, acc)
    w = np.concatenate(acc["w"])
    err = np.concatenate(acc["err"])
    es = np.concatenate(acc["err_signed"])
    # road edge reference: check_terrain_real.check_road_edge's width model
    v0 = np.arange(0.0, 5000.0, 0.02) + 12345.0
    rw = (R["RealEdgeMinM"] + (R["RealEdgeMaxM"] - R["RealEdgeMinM"])
          * road_value_noise2(np.full_like(v0, 4.5 / R["RealEdgeCellM"]), v0 / R["RealEdgeCellM"] + 0.3 * 83.0)
          + (road_value_noise2(np.full_like(v0, 4.5 / R["RealEdgeCrumbleCellM"]), v0 / R["RealEdgeCrumbleCellM"]) - 0.5)
          * 2 * R["RealEdgeCrumbleM"])
    assert acc["rim_len_m"] > 100, "no visible pad rim found - wrong tiles?"
    assert acc["rib_in"] > 100 and acc["rib_out"] > 100, "too few ribbon/pad crossings sampled"
    # rim triangles: exact but where a skewed triangle's far corner sits past a
    # rim corner; elsewhere only ever a few cm wide (and the interior assert above holds)
    assert np.percentile(err, 95) < 0.02 and np.percentile(err, 99) < 0.15,         f"pad d_in off the true rim distance: p95/p99 {np.percentile(err, 95):.3f}/{np.percentile(err, 99):.3f} m"
    assert acc["leak"] < 1e-3 * acc["pad_pts"], f"pad interior fringed at {acc['leak']} of {acc['pad_pts']} samples"
    assert np.percentile(es, 0.1) > -0.15, f"pad fringe wider than the rim distance: p0.1 {np.percentile(es, 0.1):.3f} m"
    assert abs(w.mean() - rw.mean()) < 0.02 and abs(w.std() - rw.std()) < 0.02, \
        f"pad fringe width {w.mean():.3f}+-{w.std():.3f} vs road {rw.mean():.3f}+-{rw.std():.3f}"
    print(f"pad edge: {acc['pads']} rim verts ({acc['covered']} covered), {acc['rim_len_m']:.0f} m visible rim fully "
          f"gravel; {acc['pad_pts']} interior samples, {acc['leak']} fringed past reach (<1 m); rim d_in err p95/p99 {np.percentile(err, 95) * 100:.1f}/{np.percentile(err, 99) * 100:.1f} cm (signed p0.1 {np.percentile(es, 0.1) * 100:.1f} cm); "
          f"width {w.mean():.2f}+-{w.std():.2f} m (road {rw.mean():.2f}+-{rw.std():.2f}); ribbon edge "
          f"{acc['rib_in']} pts in pads no fringe, {acc['rib_out']} outside fringed, "
          f"{acc['rib_skip']} skipped (pad corner between stations)")
    print("OK")


if __name__ == "__main__":
    main()
