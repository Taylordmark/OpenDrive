"""Offline verification for the waterway-ribbon pass (§7.26). Reads the
build's own dumped summary (data/raw/summit_county_test/waterway_build_summary.json)
plus road_stations.json - no OSM re-fetch, no UE launch. Writes 3 files into
the session scratchpad (NOT the repo):
    verify_waterways_map.png
    verify_waterways_profile.png
    verify_waterways_stats.txt
"""
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "summit_county_test"
STATIONS_PATH = ROOT / "data" / "road_tiles" / "summit_county_test" / "road_stations.json"
SCRATCH = Path(__file__).resolve().parent

dump = json.loads((RAW_DIR / "waterway_build_summary.json").read_text())
station_data = json.loads(STATIONS_PATH.read_text())
lat0, lon0 = station_data["origin_latlon"]
M_PER_DEG_LAT = 111132.0
m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))

lake_summaries = dump["lake_summaries"]
chains = dump["chain_summaries"]
ww_stats = dump["ww_stats"]

CLASS_COLOR = {"river": "#1f6fd6", "stream": "#5bb8e8"}

# ---------------------------------------------------------------- MAP PLOT
fig = plt.figure(figsize=(15, 11))
gs = fig.add_gridspec(2, 3, height_ratios=[2.4, 1])
ax_map = fig.add_subplot(gs[0, :])

# lakes (grey-blue fill)
for lk in lake_summaries:
    ring = np.array(lk["ring_latlon"])  # (lat, lon)
    e = (ring[:, 1] - lon0) * m_per_deg_lon
    n = (ring[:, 0] - lat0) * M_PER_DEG_LAT
    ax_map.fill(e, n, color="#7fb3d5", alpha=0.6, zorder=2, linewidth=0)

# roads (thin grey)
for seg in station_data["segments"]:
    ex = np.asarray(seg["east_m"]); nx = np.asarray(seg["north_m"])
    ax_map.plot(ex, nx, color="#999999", linewidth=0.4, alpha=0.6, zorder=1)

named_labeled = set()
TARGET_NAMES = ["Blue River", "Tenmile Creek", "Straight Creek", "Snake River", "Swan River"]
for ch in chains:
    e = np.asarray(ch["east_m"]); n = np.asarray(ch["north_m"])
    le = np.asarray(ch["left_e"]); ln = np.asarray(ch["left_n"])
    re = np.asarray(ch["right_e"]); rn = np.asarray(ch["right_n"])
    poly_e = np.concatenate([le, re[::-1]])
    poly_n = np.concatenate([ln, rn[::-1]])
    color = CLASS_COLOR.get(ch["class"], "#333333")
    ax_map.fill(poly_e, poly_n, color=color, alpha=0.85, zorder=3, linewidth=0)
    if ch["name"] in TARGET_NAMES and ch["name"] not in named_labeled:
        named_labeled.add(ch["name"])
        mid = len(e) // 2
        ax_map.annotate(ch["name"], (e[mid], n[mid]), fontsize=8, color="black",
                         xytext=(6, 6), textcoords="offset points",
                         bbox=dict(boxstyle="round,pad=0.2", fc="white", alpha=0.7, ec="none"))

ax_map.set_title("Summit County: lakes (light blue) + waterway ribbons "
                  "(dark=river, light=stream) + roads (grey)")
ax_map.set_xlabel("east (m)"); ax_map.set_ylabel("north (m)")
ax_map.set_aspect("equal")

# ------------------------------------------------------------ ZOOM INSETS
ax_conf = fig.add_subplot(gs[1, 0])
ax_brk = fig.add_subplot(gs[1, 1])
ax_missing = fig.add_subplot(gs[1, 2])

def draw_chains(ax, xlim, ylim, title):
    for ch in chains:
        e = np.asarray(ch["east_m"]); n = np.asarray(ch["north_m"])
        if e.max() < xlim[0] or e.min() > xlim[1] or n.max() < ylim[0] or n.min() > ylim[1]:
            continue
        le = np.asarray(ch["left_e"]); ln = np.asarray(ch["left_n"])
        re = np.asarray(ch["right_e"]); rn = np.asarray(ch["right_n"])
        poly_e = np.concatenate([le, re[::-1]])
        poly_n = np.concatenate([ln, rn[::-1]])
        color = CLASS_COLOR.get(ch["class"], "#333333")
        ax.fill(poly_e, poly_n, color=color, alpha=0.85, zorder=3, linewidth=0.3, edgecolor="black")
    for seg in station_data["segments"]:
        ex = np.asarray(seg["east_m"]); nx = np.asarray(seg["north_m"])
        m = (ex > xlim[0] - 50) & (ex < xlim[1] + 50) & (nx > ylim[0] - 50) & (nx < ylim[1] + 50)
        if m.any():
            ax.plot(ex[m], nx[m], color="#c0392b", linewidth=1.2, alpha=0.8, zorder=1)
    ax.set_xlim(xlim); ax.set_ylim(ylim); ax.set_aspect("equal")
    ax.set_title(title, fontsize=9)

# Confluence inset: find the pair of chains whose endpoints are geographically
# closest (a real shared-node confluence should have near-zero endpoint gap).
best = None
for i, c1 in enumerate(chains):
    for c2 in chains[i + 1:]:
        for e1, n1 in [(c1["east_m"][0], c1["north_m"][0]), (c1["east_m"][-1], c1["north_m"][-1])]:
            for e2, n2 in [(c2["east_m"][0], c2["north_m"][0]), (c2["east_m"][-1], c2["north_m"][-1])]:
                d = math.hypot(e1 - e2, n1 - n2)
                if best is None or d < best[0]:
                    best = (d, e1, n1)
if best and best[0] < 50:
    d, cx, cy = best
    draw_chains(ax_conf, (cx - 150, cx + 150), (cy - 150, cy + 150),
                f"Confluence union check (gap={d:.1f} m)")
else:
    ax_conf.text(0.5, 0.5, "no close confluence found", ha="center")
    ax_conf.set_title("Confluence check")

# Breckenridge Blue River / CO-9 inset: use a Blue River chain point closest
# to a CO-9-tagged road segment (road_name containing "9" and highway_class
# in the primary/trunk family - fall back to nearest road point generally).
blue = next((c for c in chains if c["name"] == "Blue River"), None)
if blue:
    be = np.asarray(blue["east_m"]); bn = np.asarray(blue["north_m"])
    # nearest road point search (brute force - dataset is small enough)
    best_d = None; best_pt = None
    for seg in station_data["segments"]:
        ex = np.asarray(seg["east_m"]); nx = np.asarray(seg["north_m"])
        for bi in range(0, len(be), 3):
            d = np.hypot(ex - be[bi], nx - bn[bi])
            j = np.argmin(d)
            if best_d is None or d[j] < best_d:
                best_d = d[j]; best_pt = (be[bi], bn[bi])
    if best_pt:
        cx, cy = best_pt
        draw_chains(ax_brk, (cx - 120, cx + 120), (cy - 120, cy + 120),
                    f"Blue River / nearest road (d={best_d:.1f} m)")
    else:
        ax_brk.set_title("Blue River: no road found nearby")
else:
    ax_brk.text(0.5, 0.5, "Blue River not selected", ha="center")
    ax_brk.set_title("Blue River / CO-9 corridor")

# Missing/unbridged crossing inset: pick a chain with n_crossings > n_bridged
# implied at the individual-crossing level is not stored per-crossing, so
# instead show whichever named chain has the most crossings overall as a
# representative "what a crossing looks like" inset.
cross_chain = max((c for c in chains if c["n_crossings"] > 0), key=lambda c: c["n_crossings"], default=None)
if cross_chain:
    e = np.asarray(cross_chain["east_m"]); n = np.asarray(cross_chain["north_m"])
    mid = len(e) // 2
    draw_chains(ax_missing, (e[mid] - 100, e[mid] + 100), (n[mid] - 100, n[mid] + 100),
                f"{cross_chain['name']}: a road crossing")
else:
    ax_missing.text(0.5, 0.5, "no crossings found", ha="center")
    ax_missing.set_title("Road crossing example")

fig.tight_layout()
fig.savefig(SCRATCH / "verify_waterways_map.png", dpi=140)
plt.close(fig)
print("wrote verify_waterways_map.png")

# ------------------------------------------------------------ PROFILE PLOT
fig2, axes = plt.subplots(3, 1, figsize=(12, 13))

def plot_profile(ax, chain, title):
    e = np.asarray(chain["east_m"]); n = np.asarray(chain["north_m"])
    s = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(e), np.diff(n)))]) / 1000.0
    ax.plot(s, chain["raw_elev_m"], "o-", color="#999999", markersize=2, linewidth=0.8, label="raw DEM")
    ax.plot(s, chain["smooth_elev_m"], color="#c0392b", linewidth=2, label="smoothed monotonic (bank)")
    ax.plot(s, chain["water_elev_m"], color="#1f6fd6", linewidth=2, label="water surface (bank - 0.4m)")
    ax.set_title(title)
    ax.set_xlabel("distance along chain (km)"); ax.set_ylabel("elevation (m)")
    ax.legend(fontsize=8)
    mono_ok = np.all(np.diff(chain["smooth_elev_m"]) <= 1e-6)
    ax.text(0.02, 0.05, f"monotonic non-increasing: {mono_ok}", transform=ax.transAxes, fontsize=8)

blue_chain = max((c for c in chains if c["name"] == "Blue River"),
                  key=lambda c: c["n_segments"], default=None)
creek_chain = max((c for c in chains if c["class"] == "stream"),
                    key=lambda c: c["n_segments"], default=None)

if blue_chain:
    plot_profile(axes[0], blue_chain, f"Blue River longitudinal profile ({blue_chain['n_segments']} segments)")
else:
    axes[0].set_title("Blue River not found")
if creek_chain:
    plot_profile(axes[1], creek_chain, f"{creek_chain['name']} longitudinal profile ({creek_chain['n_segments']} segments)")
else:
    axes[1].set_title("no named stream found")

# Cross-section at a bridge span: pick a crossing chain, show deck (from
# bridge station elevation, approximated as raw road elev_m + typical deck
# offset) above, water below, banks either side.
ax3 = axes[2]
if cross_chain:
    e = np.asarray(cross_chain["east_m"]); n = np.asarray(cross_chain["north_m"])
    mid = len(e) // 2
    hw = cross_chain["half_width_m"]
    water_z = cross_chain["water_elev_m"][mid]
    bank_z = cross_chain["smooth_elev_m"][mid]
    xs = np.linspace(-hw - 15, hw + 15, 200)
    bed = np.where(np.abs(xs) <= hw, water_z - 0.3, bank_z + 0.02 * (np.abs(xs) - hw))
    ax3.plot(xs, bed, color="#7f5539", linewidth=2, label="terrain/bank")
    ax3.fill_between(xs, water_z - 1.0, water_z, where=(np.abs(xs) <= hw),
                      color="#1f6fd6", alpha=0.7, label="water surface")
    # find nearest bridge station elevation near this crossing for deck height
    best_deck = None
    for seg in station_data["segments"]:
        ex = np.asarray(seg["east_m"]); nx = np.asarray(seg["north_m"])
        ib = np.asarray(seg.get("is_bridge", [False] * len(ex)))
        if not ib.any():
            continue
        d = np.hypot(ex - e[mid], nx - n[mid])
        j = np.argmin(d)
        if d[j] < 60 and ib[j]:
            elev = seg["elev_m"][j]
            if best_deck is None or d[j] < best_deck[0]:
                best_deck = (d[j], elev)
    if best_deck:
        deck_z = best_deck[1]
        ax3.plot([-hw - 5, hw + 5], [deck_z, deck_z], color="black", linewidth=4, label="bridge deck")
        ax3.text(0, deck_z + 0.5, f"deck ~{deck_z:.1f}m (nearest bridge station {best_deck[0]:.0f}m away)",
                  ha="center", fontsize=8)
    else:
        ax3.text(0.5, 0.9, "no bridge station found within 60m of this crossing",
                  transform=ax3.transAxes, ha="center", fontsize=9, color="red")
    ax3.set_title(f"Cross-section at a {cross_chain['name']} road crossing")
    ax3.set_xlabel("distance from centerline (m)"); ax3.set_ylabel("elevation (m)")
    ax3.legend(fontsize=8)
else:
    ax3.set_title("no crossing available for cross-section")

fig2.tight_layout()
fig2.savefig(SCRATCH / "verify_waterways_profile.png", dpi=140)
plt.close(fig2)
print("wrote verify_waterways_profile.png")

# ------------------------------------------------------------------ STATS
lines = []
lines.append("=== Waterway ribbon bake - verification stats ===\n")
n_river = sum(1 for c in chains if c["class"] == "river")
n_stream = sum(1 for c in chains if c["class"] == "stream")
lines.append(f"Waterway centerlines selected: {len(chains)} ({n_river} river, {n_stream} named stream)")
lines.append(f"  Selection rule: waterway=river always selected; waterway=stream only if tagged with a name")
lines.append(f"  (Overpass query itself excludes unnamed streams server-side - see fetch_waterways())")
lines.append("")
lines.append(f"Total ribbon length: {ww_stats['total_ribbon_km']:.2f} km across {sum(c['n_segments'] for c in chains)} segments")
lines.append(f"Width sourcing: {ww_stats['n_width_tagged']} chain(s) used an OSM width= tag, "
             f"{ww_stats['n_width_default']} used the class default "
             f"(river=10m, named stream=3.5m)")
lines.append("")
lines.append("Per-waterway detail:")
for c in sorted(chains, key=lambda c: -c["n_segments"]):
    lines.append(f"  \"{c['name']}\" [{c['class']}] segments={c['n_segments']} "
                 f"half_width={c['half_width_m']:.1f}m elev {c['elev_hi_m']:.1f}->{c['elev_lo_m']:.1f}m "
                 f"reversed={c['reversed']} crossings={c['n_crossings']} parallel_clipped={c['n_parallel_clipped']}")
lines.append("")
lines.append(f"Road crossings found: {ww_stats['n_crossings']}")
lines.append(f"  of those, {ww_stats['n_crossings_bridged']} coincide with an existing bridge station "
             f"(within the per-segment road-index nearest-point match)")
lines.append(f"  {ww_stats['n_crossings'] - ww_stats['n_crossings_bridged']} do NOT coincide with a bridge station - "
             f"water will render crossing the road at grade there (caveat, see below)")
lines.append(f"Road parallel-run clips applied (pavement-overlap avoidance): {ww_stats['n_parallel_clipped']}")
lines.append("")
lines.append(f"Lakes (existing, unchanged): {len(lake_summaries)}")
lines.append(f"Tile counts: {dump['n_lake_tiles']} tile(s) touched by lakes alone; "
             f"{dump['n_total_tiles']} tile(s) total after adding waterway ribbons "
             f"({dump['n_total_tiles'] - dump['n_lake_tiles']} new tile(s))")
lines.append(f"Total verts/tris across all water tiles: {dump['total_verts']} verts, {dump['total_tris']} tris")
lines.append("")
lines.append("=== Honest caveats ===")
lines.append("- Terrain depression reuses build_terrain_mesh_tiles.py's existing lake-bed/shore-blend")
lines.append("  code UNCHANGED (WATER_BED_FALLOFF_M=40m interior, WATER_SHORE_BLEND_M=35m exterior),")
lines.append("  since it already treats any polygon in a water tile generically. That falloff/blend was")
lines.append("  tuned for lake shorelines; for a ~4-10m-wide ribbon the EXTERIOR 35m shore blend pulls")
lines.append("  terrain down toward the water's edge height across a ~70m total swath around every")
lines.append("  waterway segment - wider than the visual creek/river banks. This was NOT re-tuned")
lines.append("  (no per-feature falloff parameter exists in the ODL1 format / depression code today;")
lines.append("  adding one would touch code shared with lakes and risk a lake regression outside this")
lines.append("  task's scope). In-engine look (does the 70m-wide bank blend read as \"too flattened\"")
lines.append("  next to a narrow mountain creek) is Taylor's to confirm on a drive-test.")
lines.append("- No full terrain-tile rebake was run as part of this pass (55+ min per the pipeline's own")
lines.append("  estimate, and no terrain-tile CODE changed - only more polygons feed the same,")
lines.append("  unmodified apply_water_depression() path). Perf impact was instead measured directly")
lines.append("  against that real function - see the perf note below/console output.")
lines.append("- Ribbon offset/miter is a simple average-of-adjacent-tangents normal, not a proper")
lines.append("  miter-limited buffer - no visible spike was found in this county's data but a very")
lines.append("  sharp digitized bend elsewhere could produce one; not exhaustively checked.")
lines.append("- Confluences are NOT boolean-unioned; adjoining ribbons meet because chained waterway")
lines.append("  segments of the SAME name share an OSM node (and therefore an exact centerline vertex)")
lines.append("  at a confluence with each other - two DIFFERENT named waterways (e.g. a creek meeting")
lines.append("  the Blue River) still just touch at that shared point without an explicit polygon merge;")
lines.append("  any visible seam there is a per-quad flat-shading artifact, not a gap.")
lines.append("- Road-overlap clipping is a one-shot approximation (shrinks a ribbon corner toward its")
lines.append("  centerline by the corner's pre-clip nearest-road shortfall, not re-queried after moving)")
lines.append("  - correct to within the clearance margin for this data, not geometrically exact.")
lines.append("- DEM resolution (~z15 Terrarium, ~9m ground sample near this latitude) cannot resolve a")
lines.append("  true creek channel separately from its banks at these widths - the 0.4m water depression")
lines.append("  below the smoothed centerline DEM reading is a judgment call, not a surveyed bank height.")

(SCRATCH / "verify_waterways_stats.txt").write_text("\n".join(lines))
print("wrote verify_waterways_stats.txt")
print("\n".join(lines[:20]))
