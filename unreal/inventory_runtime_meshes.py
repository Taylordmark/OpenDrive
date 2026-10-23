"""Headless, read-only: what the runtime managers draw, per asset class
(2026-10-22, 4 GB VRAM pass). For every static mesh under the folders the
managers load from: triangles and vertices per LOD, LOD count, Nanite,
ray tracing support, and a rough GPU memory estimate (vertex + index buffers,
BLAS). Then typical instance counts from the livetest logs' manager lines.

Cast shadow and cull distances are set on the components in C++, not on the
assets; RUNTIME below quotes them (re-check the cited lines if they drift).
Far-forest cards (ODFarForest.cpp MakeCardMesh) and buildings
(ODBuildingManager) are built at runtime, so they appear only in RUNTIME.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
Logs default to the project's Saved/Logs/livetest6.log + livetest7.log;
set OD_INVENTORY_LOGS=a.log;b.log to read others.
"""

import os
import re
from pathlib import Path

import unreal

FOLDERS = {
    "cars": "/Game/OpenDrive/Cars",
    "furniture": "/Game/OpenDrive/Furniture",
    "ground cover": "/Game/OpenDrive/Realistic/GroundCover",
    "rocks": "/Game/OpenDrive/Rocks",
    "trees+shrubs": "/Game/OpenDrive/Trees",
}
# component settings from the C++ (file:line at the time of writing)
RUNTIME = {
    "cars": "near <150 m: _real mesh, shadow, RT; far 150-950 m: stylized mesh, no shadow/RT (ODTrafficManager.cpp:792-797)",
    "furniture": "shadow only on poles; cull per type 0.8x..1x (ODFurnitureManager.cpp:201-216)",
    "ground cover": "no shadow; cull ~72 m (pebbles 0.4x) (ODGroundCover.cpp:256-259)",
    "rocks": "forest rocks: shadow, cull 450/600 m (ODForestManager.cpp:2183-2185)",
    "trees+shrubs": "trees: shadow, cull 450/600 m; low veg no shadow 200/450 m; scrub 150/250 m (ODForestManager.cpp:2183-2185)",
    "far cards": "runtime mesh 8 tris LOD0 / 6 tris LOD1 (<0.02 screen, ~1.5 km, 1.7x wide); no shadow, no RT; primary cull 4.5/5 km, 2 extras/cell 1.35/1.5 km (ODFarForest.cpp)",
    "buildings": "runtime UDynamicMesh per tile, ~45 tris/building (claude/distance-lod note)",
}
BYTES_PER_VERT = 32      # position 12 + tangent basis 8 + 2 UVs 8 + colour 4
BYTES_PER_BLAS_TRI = 64  # compacted BLAS, rough NVIDIA figure


def log(msg):
    unreal.log(f"ODInventory: {msg}")


def is_live(name):
    """Realistic style + Standard asset detail: the variant the game loads."""
    return not name.endswith(("_high", "_ultra")) and "_real_" not in name


def mesh_rows(folder):
    rows = []
    for path in unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False):
        if unreal.EditorAssetLibrary.find_asset_data(path).asset_class_path.asset_name != "StaticMesh":
            continue
        mesh = unreal.load_asset(path)
        name = mesh.get_name()
        lods = mesh.get_num_lods()
        tris = [mesh.get_num_triangles(i) for i in range(lods)]
        verts = [mesh.get_num_vertices(i) for i in range(lods)]
        nanite = mesh.get_editor_property("nanite_settings").get_editor_property("enabled")
        rt = mesh.get_editor_property("support_ray_tracing")
        geo_mb = sum(v * BYTES_PER_VERT + t * 12 for v, t in zip(verts, tris)) / 1e6
        blas_mb = (tris[0] * BYTES_PER_BLAS_TRI / 1e6) if rt and not nanite else 0.0
        rows.append((name, lods, tris, nanite, rt, geo_mb, blas_mb, is_live(name)))
    return rows


def log_counts(paths):
    pats = {
        "far cards": r"far forest chunks=\d+ cards=(\d+)",
        "near trees+shrubs": r"ODForestManager: tiles=\d+ instances=(\d+)",
        "furniture props": r"ODFurnitureManager: tiles=\d+ .*props=(\d+)",
        "traffic cars": r"ODTrafficManager: '\w+' (\d+) car\(s\)",
        "ground cover / 5 s": r"ODGroundCover: \d+ cell\(s\) in 5s, grass (\d+) weed (\d+) bush (\d+) sage (\d+) pebble (\d+)",
        "buildings": r"ODBuildingManager: tiles=\d+ .*buildings=(\d+)",
    }
    out = {}
    for key, pat in pats.items():
        vals = []
        for p in paths:
            if p.exists():
                for m in re.finditer(pat, p.read_text(errors="ignore")):
                    vals.append(sum(int(g) for g in m.groups()))
        if vals:
            vals.sort()
            out[key] = (vals[len(vals) // 2], vals[-1], len(vals))
    return out


total_geo = total_blas = 0.0
for cls, folder in FOLDERS.items():
    log(f"== {cls} ({folder}) - {RUNTIME[cls]}")
    for name, lods, tris, nanite, rt, geo_mb, blas_mb, live in sorted(mesh_rows(folder)):
        if live:
            total_geo += geo_mb
            total_blas += blas_mb
        log(f"  {'*' if live else ' '} {name:<34} LODs {lods}  tris {'/'.join(map(str, tris)):<22} "
            f"nanite {int(nanite)} rt {int(rt)}  geo {geo_mb:5.2f} MB  blas~{blas_mb:5.2f} MB")
for cls in ("far cards", "buildings"):
    log(f"== {cls} - {RUNTIME[cls]}")
log(f"live (*) meshes: geometry ~{total_geo:.0f} MB, non-Nanite BLAS ~{total_blas:.0f} MB "
    f"(estimates: {BYTES_PER_VERT} B/vertex, {BYTES_PER_BLAS_TRI} B/BLAS triangle, LOD0 only)")

logs_dir = Path(unreal.Paths.project_saved_dir()) / "Logs"
env = os.environ.get("OD_INVENTORY_LOGS")
paths = [Path(p) for p in env.split(";")] if env else [logs_dir / "livetest6.log", logs_dir / "livetest7.log"]
for key, (median, peak, n) in log_counts(paths).items():
    log(f"instances {key:<20} median {median:>7}  max {peak:>7}  ({n} log lines)")
log("DONE")
