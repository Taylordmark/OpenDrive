"""Pad fusing (2026-10-14): the indexed conform / nearest-chunk / bridge
paths give byte-identical chunks to the brute-force scans they replaced.

Run:  python pipeline/tests/test_junction_pads_fuse.py"""
import copy
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import experimental_junction_pads as jp
from road_mesh_common import build_road_chunks


def brute_near(pad, rib_pos, rib_tris):
    top = np.asarray(pad["vertices"], dtype=np.float64).reshape(-1, 3)[:pad["ring_start"]]
    lo, hi = top[:, :2].min(0), top[:, :2].max(0)
    tb = rib_pos[rib_tris]
    return np.flatnonzero((tb[:, :, 0].max(1) >= lo[0]) & (tb[:, :, 0].min(1) <= hi[0])
                          & (tb[:, :, 1].max(1) >= lo[1]) & (tb[:, :, 1].min(1) <= hi[1]))


def brute_bridge(host, guest_base, guest_vcount, ring_start):
    hv = host["vertices"]
    ring = range(ring_start, guest_vcount)
    best, best_any = {}, (float("inf"), 0, 0)
    for hi in range(guest_base):
        d = [(hv[hi * 3] - hv[(guest_base + i) * 3]) ** 2 + (hv[hi * 3 + 1] - hv[(guest_base + i) * 3 + 1]) ** 2
             for i in ring]
        j = int(np.argmin(d))
        if d[j] < 300.0 ** 2 and (j not in best or d[j] < best[j][0]):
            best[j] = (d[j], hi)
        if d[j] < best_any[0]:
            best_any = (d[j], hi, j)
    return best or {best_any[2]: best_any[:2]}   # nothing within 3 m: one bridge to the closest


rng = np.random.default_rng(1)
chunks, pads = [], []
for r in range(20):
    s = np.arange(0, 1000, 5.0)
    o, a = rng.uniform(-1500, 1500, 2), rng.uniform(0, 6.3)
    e = o[0] + s * np.cos(a) + 30 * np.sin(s / 90 + r)
    n = o[1] + s * np.sin(a) + 30 * np.cos(s / 70 + r)
    z = 50 + 0.03 * s
    chunks += build_road_chunks(s, e, n, jp.road_ue_z_m(z, jp.ROAD_LIFT, jp.ORIGIN_HEIGHT_M), name_prefix=f"r{r}")[0]
    for k in rng.choice(len(s), 6, replace=False):
        pads.append(((e[k], n[k]), z[k], rng.uniform(6, 25), rng.uniform(4, 25), rng.uniform(0, 3)))

rib = jp.ribbon_top_triangles(chunks)
cents = jp.chunk_centroids(chunks)
ref = copy.deepcopy(chunks)
n_conf = 0
for k, (xy, z, ra, rb, th) in enumerate(pads):
    pad = jp.build_pad_chunk_ellipse(f"p{k}", (*xy, ra, rb, th), z, jp.PAD_OVERLAP_MARGIN_M)
    n_conf += jp.conform_pad_to_ribbons(pad, *rib)
    # the KD prefilter finds exactly the bbox-overlap triangles a full scan does
    probe = jp.build_pad_chunk_ellipse("q", (*xy, ra, rb, th), z, jp.PAD_OVERLAP_MARGIN_M)
    tlo, thi, tree, tr = rib[2]
    top = np.asarray(probe["vertices"]).reshape(-1, 3)[:probe["ring_start"], :2]
    lo, hi = top.min(0), top.max(0)
    got = np.sort(tree.query_ball_point((lo + hi) / 2, np.hypot(*(hi - lo)) / 2 + tr + 1.0))
    got = got[(thi[got, 0] >= lo[0]) & (tlo[got, 0] <= hi[0]) & (thi[got, 1] >= lo[1]) & (tlo[got, 1] <= hi[1])]
    assert np.array_equal(got, brute_near(probe, rib[0], rib[1]))
    # nearest by a full centroid recompute over the live (growing) chunks
    want = min(range(len(ref)), key=lambda i: (lambda c: (c[0] - xy[0] * 100) ** 2 + (c[1] + xy[1] * 100) ** 2)(
        jp.chunk_centroid(ref[i])))
    host, guest = ref[want], copy.deepcopy(pad)   # merge_chunk_into, brute-force bridge
    base, gv = len(host["vertices"]) // 3, len(guest["vertices"]) // 3
    for key in ("vertices", "uvs", "uv1"):
        host[key].extend(guest[key])
    host["triangles"].extend(i + base for i in guest["triangles"])
    rs = guest.get("ring_start", 1)
    for j, (_d2, hi) in brute_bridge(host, base, gv, rs).items():
        host["triangles"].extend([hi, base + rs + j, base + rs + (j + 1) % (gv - rs)])
    cents, merged = jp.fuse_into_nearest(chunks, cents, pad, xy)
    assert merged

assert chunks == ref, "fused chunks differ from the brute-force reference"
assert n_conf > 0, "conform path never exercised"
print(f"ok ({len(pads)} pads, {n_conf} conformed verts)")
