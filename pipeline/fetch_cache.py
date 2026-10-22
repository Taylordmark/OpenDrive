"""Disk cache for everything the bake downloads (2026-10-01).

Every rebake used to re-download the same Terrarium DEM tiles (once per
stage that samples heights, and once per terrain worker), the USGS imagery
tiles classify_biomes reads, and every Overpass query. Responses are now kept
under data/cache/<kind>/ (gitignored with the rest of data/) and reused.

To pull fresh data from the source, delete the relevant data/cache/<kind>/
dir - e.g. data/cache/overpass/ after OSM edits you want picked up.
"""
import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np
import requests

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
# Waits (s) before each extra pass over every mirror (2026-10-14: both
# original mirrors 504'd for an hour, failing whole rebakes on the spot).
OVERPASS_RETRY_WAITS_S = (30, 120, 300)


def cached_bytes(kind, name, fetch):
    """fetch() -> bytes, or the copy saved under data/cache/<kind>/<name>.
    Safe across the parallel bake workers: written to a per-process temp
    file and renamed into place, so nobody ever reads a half-written file."""
    path = CACHE_DIR / kind / name
    if path.is_file():
        return path.read_bytes()
    data = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    try:
        os.replace(tmp, path)
    except OSError:   # Windows: another worker wrote and is reading it - same bytes
        tmp.unlink(missing_ok=True)
    return data


def overpass_json(query, headers, timeout=200, clip=True):
    """POST an Overpass QL query (trying each mirror), cached by query text."""
    def fetch():
        for wait in (0, *OVERPASS_RETRY_WAITS_S):
            if wait:
                print(f"  all Overpass mirrors failed, retrying in {wait} s")
                time.sleep(wait)
            for url in OVERPASS_URLS:
                try:
                    r = requests.post(url, data={"data": query}, headers=headers, timeout=timeout)
                    r.raise_for_status()
                    # never cache a non-JSON error page, nor a timed-out query's
                    # partial result (HTTP 200 + "remark": "runtime error: ...")
                    if "error" in r.json().get("remark", ""):
                        raise ValueError(r.json()["remark"])
                    return r.content
                except (requests.RequestException, ValueError) as exc:
                    print(f"  {url} failed ({exc}), trying next mirror")
        raise SystemExit("All Overpass mirrors failed.")
    key = hashlib.sha1(query.encode()).hexdigest()
    data = json.loads(cached_bytes("overpass", f"{key}.json", fetch))
    # (2026-10-21) a drawn area keeps only what is inside its outline; the
    # rectangle download above stays cached as-is (scoping round 8)
    from bake_job import outline_ll
    poly = outline_ll()
    return data if poly is None or not clip else clip_osm(data, poly)


CLIP_ID0 = 2 * 10 ** 15   # synthetic ids for split ways / cut nodes (build_track uses 1e15)


def clip_osm(data, poly):
    """Overpass JSON clipped to poly (shapely, lon/lat) - scoping round 8:
    - an element with a "center" (out center): kept iff the centre is inside
    - a building (way or relation): whole footprint, kept iff its centroid is
    - other relations: whole, with their member ways, iff any vertex is inside
    - closed non-road ways (lakes, lots, plazas): intersected with the outline
    - other ways (roads): runs of inside nodes, split where they leave
    - nodes: tagged ones inside, plus every node a kept way uses."""
    import shapely
    from shapely.prepared import prep
    pin = prep(poly).contains
    els = data["elements"]
    ll = {e["id"]: (e["lon"], e["lat"]) for e in els if e["type"] == "node" and "lon" in e}
    inside = {i: pin(shapely.Point(p)) for i, p in ll.items()}
    ways = {e["id"]: e for e in els if e["type"] == "way"}
    new_id = iter(range(CLIP_ID0, CLIP_ID0 + 10 ** 14))

    def coords(w):
        if "geometry" in w:
            return [(g["lon"], g["lat"]) if g else None for g in w["geometry"]]
        return [ll.get(n) for n in w.get("nodes", [])]

    def centroid_in(pts):   # vertex mean, like fetch_osm_buildings' MS footprints
        pts = [p for p in pts if p]
        return bool(pts) and pin(shapely.Point(np.mean(pts, axis=0)))

    out, used, extra = [], set(), []
    keep_rel, whole = set(), set()
    for e in els:
        if e["type"] != "relation":
            continue
        mem = [ways[m["ref"]] for m in e.get("members", []) if m["type"] == "way" and m["ref"] in ways]
        if "center" in e:
            ok = pin(shapely.Point(e["center"]["lon"], e["center"]["lat"]))
        elif "building" in e.get("tags", {}):
            ok = centroid_in([p for w in mem for p in coords(w)])
        else:
            ok = any(inside.get(m["ref"]) for m in e.get("members", []) if m["type"] == "node") or                 any(p and pin(shapely.Point(p)) for w in mem for p in coords(w))
        if ok:
            keep_rel.add(e["id"])
            whole |= {w["id"] for w in mem}
    for e in els:
        if e["type"] == "node":
            continue
        if e["type"] == "relation":
            if e["id"] in keep_rel:
                out.append(e)
            continue
        tags, nds, pts = e.get("tags", {}), e.get("nodes", []), coords(e)
        closed = len(nds) >= 4 and nds[0] == nds[-1]
        if "center" in e:
            pieces = [e] if pin(shapely.Point(e["center"]["lon"], e["center"]["lat"])) else []
        elif e["id"] in whole or (closed and ("building" in tags or "building:part" in tags)):
            pieces = [e] if e["id"] in whole or centroid_in(pts) else []
        elif closed and ("highway" not in tags or tags.get("area") == "yes"):
            pieces = []
            if all(pts) and len(pts) >= 4:
                ring = shapely.Polygon(pts)
                ring = ring if ring.is_valid else ring.buffer(0)
                if pin(ring):
                    pieces = [e]
                elif poly.intersects(ring):
                    # ponytail: holes and slivers of the cut are dropped
                    cut = ring.intersection(poly)
                    for g in getattr(cut, "geoms", [cut]):
                        if g.geom_type != "Polygon" or g.is_empty:
                            continue
                        ids = [next(new_id) for _ in g.exterior.coords[:-1]]
                        extra += [{"type": "node", "id": i, "lon": x, "lat": y}
                                  for i, (x, y) in zip(ids, g.exterior.coords[:-1])]
                        w = {**e, "id": e["id"] if not pieces else next(new_id), "nodes": ids + ids[:1]}
                        if "geometry" in e:
                            w["geometry"] = [{"lat": y, "lon": x} for x, y in g.exterior.coords]
                        pieces.append(w)
        else:
            pieces, run = [], []
            for k, p in enumerate(pts + [None]):
                if p and pin(shapely.Point(p)):
                    run.append(k)
                    continue
                if len(run) >= 2:
                    w = {**e, "id": e["id"] if not pieces else next(new_id),
                         "nodes": nds[run[0]:run[-1] + 1]}
                    if "geometry" in e:
                        w["geometry"] = e["geometry"][run[0]:run[-1] + 1]
                    pieces.append(w)
                run = []
        for w in pieces:
            used.update(w.get("nodes", []))
        out += pieces
    nodes = [e for e in els if e["type"] == "node"
             and (e["id"] in used or (e.get("tags") and inside.get(e["id"])))]
    return {**data, "elements": nodes + extra + out}


def clip_geojson_lines(features, poly):
    """GeoJSON line features (HPMS sections) intersected with poly (lon/lat)."""
    import shapely
    from shapely.geometry import mapping, shape
    out = []
    for f in features:
        if not f.get("geometry"):
            continue
        cut = shape(f["geometry"]).intersection(poly)
        lines = [g for g in getattr(cut, "geoms", [cut]) if g.geom_type == "LineString" and not g.is_empty]
        if lines:
            g = lines[0] if len(lines) == 1 else shapely.MultiLineString(lines)
            out.append({**f, "geometry": mapping(g)})
    return out
