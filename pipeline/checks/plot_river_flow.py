"""Plots the baked flowing-river data (build_water_tiles.py's ODF1 trailer)
for a look without launching the game: whitewater map with falls, river
profiles through falls, and Aiken Branch Falls (deals_gap) in 3D.

Run:  OD_AREA=deals_gap python pipeline/checks/plot_river_flow.py [out_dir]
      (out_dir defaults to data/raw/<area>_test/)
"""
import json, struct, sys
from pathlib import Path
import numpy as np
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from build_terrain_mesh_tiles import GENERATION_ID_BYTES, OUT_DIR, WATER_TILE_GLOB
from build_water_tiles import RAW_DIR
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else RAW_DIR

def parse(raw):
    cur = 4 + GENERATION_ID_BYTES
    (n,) = struct.unpack_from("<I", raw, cur); cur += 4
    fs = []
    for _ in range(n):
        vc, tc = struct.unpack_from("<II", raw, cur); cur += 8
        v = np.frombuffer(raw, "<f4", vc*3, cur).reshape(-1,3)/100 * [1, -1, 1]; cur += vc*12  # UE Y = -north
        uv = np.frombuffer(raw, "<f4", vc*2, cur).reshape(-1,2); cur += vc*8
        t = np.frombuffer(raw, "<i4", tc*3, cur).reshape(-1,3); cur += tc*12
        fs.append([v, uv, t])
    cur += 4
    out = []
    for v, uv, t in fs:
        k = raw[cur]; cur += 1
        fl = np.frombuffer(raw, "<f4", len(v)*2, cur).reshape(-1,2); cur += len(v)*8
        out.append((k, v, uv, t, fl))
    return out

feats = [f for p in sorted(OUT_DIR.glob(WATER_TILE_GLOB)) for f in parse(p.read_bytes())]
falls = json.loads((RAW_DIR/"waterway_build_summary.json").read_text())["fall_summaries"]
rib = [f for f in feats if f[0] == 1]
V = np.concatenate([f[1] for f in rib]); FL = np.concatenate([f[4] for f in rib])
UVv = np.concatenate([f[2] for f in rib])
cm = LinearSegmentedColormap.from_list("ww", ["#0b3d5c", "#2a7fa8", "#8fd0e8", "#ffffff"])

# 1 map
fig, ax = plt.subplots(figsize=(11, 11), facecolor="#3a3a32"); ax.set_facecolor("#3a3a32")
o = np.argsort(FL[:,1])
sc = ax.scatter(V[o,0], V[o,1], c=FL[o,1], cmap=cm, s=1.2, vmin=0, vmax=1)
for f in falls:
    osm = f["source"] == "osm"
    ax.scatter(f["east_m"], f["north_m"], marker="*" if osm else "^", s=260 if osm else 90,
               c="#ffcc33" if osm else "#ff6a3d", edgecolors="k", zorder=5)
    ax.annotate(f"{f['name']} ({f['drop_m']:.0f} m)", (f["east_m"], f["north_m"]), xytext=(6, 6),
                textcoords="offset points", color="w", fontsize=8)
ax.set_aspect("equal"); ax.tick_params(colors="w"); ax.set_xlabel("east (m)", color="w"); ax.set_ylabel("north (m)", color="w")
cb = fig.colorbar(sc, shrink=0.6); cb.set_label("whitewater (0 calm -> 1 white)")
ax.set_title("Deals Gap rivers: whitewater per baked vertex\n* = OSM waterfall (sheet + mist)   ^ = steep drop found in elevation data (mist)", color="w")
fig.savefig(OUT/"1_map_whitewater.png", dpi=110, bbox_inches="tight"); plt.close(fig)

# 2 profiles near falls
picks = [f for f in falls if f["name"] in ("Aiken Branch Falls", "Slickrock Creek", "Little Tennessee River", "Ike Branch")][:5]
fig, axs = plt.subplots(len(picks), 1, figsize=(10, 3.2*len(picks)))
for ax, f in zip(axs, picks):
    d = np.hypot(V[:,0]-f["east_m"], V[:,1]-f["north_m"])
    i0 = d.argmin(); v0 = UVv[i0,1]
    sel = (d < 500) & (np.abs(UVv[:,1]-v0)*20 < 500)
    s = (UVv[sel,1]-v0)*20
    a = ax.scatter(s, V[sel,2], c=FL[sel,1], cmap=cm, vmin=0, vmax=1, s=10, edgecolors="#555", linewidths=0.2)
    ax.axvline(0, color="#ff6a3d", ls="--"); ax.set_facecolor("#5a5a50")
    ax.set_title(f"{f['name']} - {f['source'].upper()}, drop {f['drop_m']:.0f} m (dashed = fall)", fontsize=10)
    ax.set_ylabel("water z (m)")
axs[-1].set_xlabel("distance downstream from the fall (m)  -> water flows left to right")
fig.colorbar(a, ax=axs, shrink=0.5, label="whitewater")
fig.savefig(OUT/"2_profiles.png", dpi=110, bbox_inches="tight"); plt.close(fig)

# 3 3D Aiken Branch Falls
f = falls[0]; c = np.array([f["east_m"], f["north_m"]])
fig = plt.figure(figsize=(11, 9)); ax = fig.add_subplot(projection="3d")
for k, v, uv, t, fl in feats:
    if np.hypot(*(v[:,:2].mean(0)-c)) > 90 or not len(t): continue
    col = {1: None, 3: "#e8f6ff", 4: "#ffffff"}.get(k)
    if col is None:
        w = fl[t].mean(1)[:,1]; fc = cm(w)
    else:
        fc = col
    ax.plot_trisurf(v[:,0]-c[0], v[:,1]-c[1], v[:,2], triangles=t, color=None if col is None else col,
                    alpha=0.35 if k == 4 else 0.95, shade=True, edgecolor="none")
    if col is None:
        ax.collections[-1].set_facecolor(fc)
ax.set_title("Aiken Branch Falls as baked: river ribbon (coloured by whitewater),\nfalling sheet (pale blue) and mist cards (translucent)")
ax.set_xlabel("east (m)"); ax.set_ylabel("north (m)"); ax.set_zlabel("z (m)")
ax.view_init(elev=22, azim=-60)
fig.savefig(OUT/"3_aiken_falls_3d.png", dpi=110, bbox_inches="tight"); plt.close(fig)
print("ok")
