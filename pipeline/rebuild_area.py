"""Single driver for a full, consistent Arc 7 open-world bake of the active
test area (test_areas.AREA_NAME) - runs every stage from raw OSM/DEM fetch
through to a fully populated OpenDriveUE/Content/OpenDrive/Masks/, in the
one dependency order the pipeline actually requires.

Why this exists (AUDIT-RELIABILITY.md SS3.3): the README used to hand-list
only six of the ~twenty scripts that feed Content/OpenDrive/Masks/ (fetch ->
stations -> terrain -> road network -> road bin -> verify), omitting the
barrier, buildings, retaining walls, place labels, biome/settlement
classification, and map texture steps entirely. Following that partial list
after any station-changing edit produces a mixed-generation bake - some
tiles built against the new road_stations.json, some still on disk from a
stale run - with no error anywhere (AUDIT-RELIABILITY.md SS2). This script
is the fix: one command, the real order, fail-fast.

Order derived by reading every build_*/export_*/fetch_*/classify_*/verify_*
script's own docstring + Reads/Writes header (2026-08-21), not guessed from
filenames:

  1. fetch_osm_network.py     -> data/raw/<area>_test/osm.json
  2. fetch_osm_places.py      -> data/raw/<area>_test/osm_places.json
  3. fetch_osm_buildings.py   -> data/raw/<area>_test/osm_buildings.json
  4. build_road_stations.py   -> road_stations.json (the single source of
     truth every later stage reads; needs osm.json)
  5. classify_settlements.py  -> settlement_tiles.json (needs
     osm_buildings.json + road_stations.json; read by build_road_barrier.py
     and build_building_mesh.py below)
  6. build_retaining_walls.py -> retaining_walls.json (needs
     road_stations.json). MUST run before build_terrain_mesh_tiles.py -
     not because it reads the terrain script's output, but because both
     independently call road_mesh_common.classify_wall_stations() against
     the SAME road_stations.json and the terrain carve needs the identical
     wall-conflict exclusion set the wall build implies
     (AUDIT-RELIABILITY.md SS3.1).
  7. build_terrain_mesh_tiles.py -> terrain_*.bin tiles, carved to
     road_stations.json (see #6 for the ordering constraint)
  8. export_retaining_wall_bin.py -> walls_*.bin (needs retaining_walls.json)
  9. build_road_network.py    -> road_mesh.json, road_test_meta.json (needs
     road_stations.json)
 10. export_road_mesh_bin.py  -> road_*.bin (needs road_mesh.json)
 10b. export_road_mesh_bin_v2.py -> MasksV2/roadv3_*.bin (needs road_mesh.json;
     ODR3 extended format - AODRoadManager PREFERS these over the ODR2 tiles
     when present, so skipping this stage ships a stale road generation)
 10c. experimental_junction_pads.py --fresh-baseline -> patches road, ODR3
     and terrain tiles + the station sidecar at junctions (furniture,
     centerline and menu spots read the patched sidecar)
 11. export_road_centerline_bin.py -> centerline_*.bin, centerline_map_*.bin (runs after furniture since 2026-10-06: the routegraph reads its controls)
     (needs road_stations.json)
 12. build_road_barrier.py    -> road_barrier.json (needs road_stations.json;
     reads settlement_tiles.json if present so towns stay open)
 13. export_road_barrier_bin.py -> barrier_*.bin (needs road_barrier.json)
 14. build_building_mesh.py   -> building_mesh.json (needs
     osm_buildings.json, road_stations.json, settlement_tiles.json)
 15. export_building_mesh_bin.py -> buildings_*.bin (needs building_mesh.json)
 16. export_building_centroids_bin.py -> building_centroids_*.bin (needs
     building_mesh.json)
 17. build_place_labels.py    -> road_labels.json (needs road_stations.json,
     osm_places.json)
 18. export_place_labels_bin.py -> labels_*.bin (needs road_labels.json)
 19. classify_biomes.py       -> road_biomes.json (needs road_stations.json;
     fetches its own imagery, no separate fetch stage required)
 20. export_biome_bin.py      -> biome_*.bin (needs road_biomes.json)
 21. build_map_texture.py     -> map_texture.png, map_texture_meta.json
     (needs road_stations.json; reuses classify_biomes.py's imagery/
     classification functions directly)
 22. build_forest_elevation.py -> elev_*.bin (needs road_stations.json;
     samples the shipped terrain_*.bin tiles on disk;
     experimental_junction_pads.py re-runs it after re-carving terrain)
 23. verify_terrain_fit.py    -> offline gate. Rewritten 2026-08-21
     (AUDIT-RELIABILITY.md SS4): now reads the ACTUAL terrain .bin tiles
     this script just wrote to Content/ (not an in-memory rebuild) as its
     primary check, plus a generation-id cross-artifact check, with the old
     in-memory pre-bake consistency check kept as a clearly separate,
     secondary check.
 24. verify_road_fit.py       -> offline gate, run last. Restored 2026-08-21
     (AUDIT-RELIABILITY.md SS4.2, previously deleted and never replaced) -
     reads the ACTUAL road and terrain .bin tiles on disk and checks the
     road surface sits the expected small gap above the terrain surface at
     every in-scope station. A PASS on both gates is necessary but not
     sufficient evidence the shipped bake is good; it does not replace
     eyeballing the level.

Not included (deliberately out of scope - these feed the separate
Tail-of-the-Dragon stage or standalone stage assets, not the Arc 7 area
Masks pipeline): fetch_data.py, fetch_imagery.py,
build_stage.py, make_road_texture.py, make_detail_textures.py,
make_tree_meshes.py, make_car_mesh.py, make_car_gt.py, export_road_obj.py,
make_forest_mask_bin.py (reads data/stages/tail_of_the_dragon/... directly),
export_centerline_bin.py (same, superseded for the open-world area by
export_road_centerline_bin.py), analyze_audio_test.py, analyze_brap.py.

Usage:  python pipeline/rebuild_area.py

None of the underlying scripts take an --area flag (checked 2026-08-21,
`grep argparse` across pipeline/ finds none) - the active area is switched
by editing AREA_NAME in test_areas.py, same as every stage script already
assumes. This driver just reports whichever area is currently configured
there and runs every stage against it.

Fails fast: the first stage to return a non-zero exit code stops the whole
run immediately (no partial bake left half-updated silently) and this
script exits non-zero itself with a clear "stage N/23 failed" message.
"""

import subprocess
import sys
import time
from pathlib import Path

from test_areas import AREA_NAME

PIPELINE_DIR = Path(__file__).resolve().parent

# (script filename, human-readable description) in the exact required order.
STAGES = [
    ("fetch_osm_network.py", "fetch OSM road network -> osm.json"),
    ("fetch_osm_places.py", "fetch OSM place nodes -> osm_places.json"),
    ("fetch_osm_buildings.py", "fetch OSM building footprints -> osm_buildings.json"),
    ("build_road_stations.py", "build road_stations.json (single source of truth)"),
    ("classify_settlements.py", "classify settlement archetypes -> settlement_tiles.json"),
    ("build_retaining_walls.py", "build retaining_walls.json (must precede terrain carve)"),
    # WATER (2026-09-01): must precede terrain - build_terrain_mesh_tiles
    # reads the baked water_*.bin back as lake beds and depresses terrain to
    # them (load_lake_beds). It used to sit after terrain ("needs terrain's
    # sample_dem"), but it only imports that function, never reads terrain
    # tiles - so terrain always baked against the PREVIOUS run's lakes
    # (2026-09-25: 126 stations up to 10m off near Dillon after a rebake).
    ("build_water_tiles.py", "bake lake/reservoir water tiles -> water_*.bin (terrain reads these as lake beds)"),
    ("build_terrain_mesh_tiles.py", "bake terrain tiles -> Content/OpenDrive/Masks/terrain_*.bin"),
    ("export_retaining_wall_bin.py", "export wall tiles -> Content/OpenDrive/Masks/walls_*.bin"),
    ("build_road_network.py", "build road_mesh.json + road_test_meta.json"),
    ("export_road_mesh_bin.py", "export road tiles -> Content/OpenDrive/Masks/road_*.bin"),
    # 2026-09-06: was MISSING since ODR3 landed (2026-09-03) - ODRoadManager
    # prefers MasksV2/roadv3_* when present, so a rebake without this stage
    # left 1161 stale-generation ODR3 tiles overriding every fresh ODR2 tile.
    ("export_road_mesh_bin_v2.py", "export extended ODR3 tiles -> Content/OpenDrive/MasksV2/roadv3_*.bin"),
    # JUNCTION PADS (2026-10-08, was a manual step, §7.51): patches the road,
    # ODR3 and terrain tiles and the station sidecar just written above, so it
    # needs them all; furniture and menu spots read its patched sidecar, so it
    # precedes them. --fresh-baseline drops the previous bake's backups.
    ("experimental_junction_pads.py --fresh-baseline", "junction pads: regrade + pad road tiles, re-carve terrain"),
    ("export_pad_points_bin.py", "junction-pad pavement points -> padpts_*.bin (forest road exclusion)"),
    ("build_road_barrier.py", "build road_barrier.json"),
    ("export_road_barrier_bin.py", "export barrier tiles -> Content/OpenDrive/Masks/barrier_*.bin"),
    # GUARDRAILS (2026-09-04): road-above-terrain drop detection (dam
    # causeways, mountain switchback outer edges) - needs road_stations.json
    # + settlement_tiles.json (town exclusion, like build_road_barrier.py)
    # + the terrain tiles' own DEM sampling helper (build_terrain_mesh_tiles.
    # sample_dem), so it sits after terrain but has no other ordering
    # constraint against barrier/buildings.
    ("build_road_guardrails.py", "build road_guardrails.json (road-vs-terrain drop detection)"),
    ("export_road_guardrails_bin.py", "export guardrail tiles -> Content/OpenDrive/Masks/guardrail_*.bin"),
    # FURNITURE (2026-10-06, PLAN-REALISM Phase 1): needs stations, osm.json's
    # furniture nodes, osm_buildings.json (town lamps), road_guardrails.json
    # (no delineators along a rail) and the terrain tiles (prop bases).
    ("build_road_furniture.py", "build road_furniture.json (signals, signs, chevrons, lights, delineators)"),
    ("export_road_furniture_bin.py", "export furniture tiles -> Content/OpenDrive/Masks/furniture_*.bin"),
    # after furniture (2026-10-06): the ODG1 lane block reads road_furniture.json
    # `controls`; run earlier it shipped 0 OSM signals/stops
    ("export_road_centerline_bin.py", "export centerline_*.bin / centerline_map_*.bin / routegraph_*.bin"),
    # EMBANKMENTS (2026-09-07): reuses road_stations.json's own in_junction_box
    # field (Arc 7.18) plus the same DEM-probe drop detection guardrails use -
    # sits right after guardrails for the same "needs terrain's sample_dem
    # helper, no other ordering constraint" reason.
    ("build_road_embankments.py", "build road_embankments.json (3-way drop classification: none/embankment/bridge) "
                                   "+ §7.21 bridge sub-classification (demotes sidehill-cut runs back to embankment)"),
    ("export_road_embankment_bin.py", "export embankment tiles -> Content/OpenDrive/Masks/embankment_*.bin"),
    # BRIDGES (2026-09-07, §7.21): consumes the now-genuine-only "bridge"
    # class build_road_embankments.py just wrote (its own sub-classification
    # pass demotes every sidehill-cut run), adding pier placement - sits
    # right after the embankment export for the same "no other ordering
    # constraint, needs road_embankments.json first" reason.
    ("build_road_bridges.py", "build road_bridges.json (genuine-span deck points + pier placement)"),
    ("export_road_bridge_bin.py", "export bridge tiles -> Content/OpenDrive/Masks/bridge_*.bin"),
    ("build_building_mesh.py", "build building_mesh.json"),
    ("export_building_mesh_bin.py", "export building tiles -> Content/OpenDrive/Masks/buildings_*.bin"),
    ("export_building_centroids_bin.py", "export building_centroids_*.bin"),
    ("build_place_labels.py", "build road_labels.json"),
    ("export_place_labels_bin.py", "export labels_*.bin"),
    ("classify_biomes.py", "classify road-corridor biomes -> road_biomes.json"),
    ("export_biome_bin.py", "export biome_*.bin"),
    # (2026-10-05) was missing: every area without a hand-run climate file got
    # ODWeatherController's mild default climate. Needs road_test_meta.json +
    # road_biomes.json (storm weights); this area only, not its no-arg "all".
    (f"fetch_climate.py {AREA_NAME}_test", "fetch climate normals + storm weights -> climate_*.json"),
    ("build_map_texture.py", "build map_texture.png + map_texture_meta.json"),
    ("build_forest_elevation.py", "bake tree ground-truth elevation -> elev_*.bin"),
    # (2026-10-05, step 3) after the last tile writer: the gates check owned tiles only
    ("prune_unowned_tiles.py", "delete tiles outside the area's owned rect (its halo)"),
    ("build_menu_spots.py", "pick the garage-menu backdrop spot -> menu_spots.json"),
    ("verify_terrain_fit.py", "offline gate: shipped-artifact terrain fit + generation-id + pre-bake consistency"),
    ("verify_road_fit.py", "offline gate: shipped-artifact road-vs-terrain fit"),
    # (2026-10-05) after the gates: lists every baked world member, this one included
    ("build_world_manifest.py", "write Content/OpenDrive/Masks/worlds.json (streamed world members)"),
]


def main():
    print(f"=== rebuild_area.py: AREA_NAME = {AREA_NAME!r} ({len(STAGES)} stages) ===")
    t_start = time.time()

    # --from <script>: resume at that stage (e.g. a terrain-only change)
    start = sys.argv[sys.argv.index("--from") + 1] if "--from" in sys.argv else None
    for i, (cmd, desc) in enumerate(STAGES, start=1):
        script, *args = cmd.split()
        if start and script != start:
            continue
        start = None
        script_path = PIPELINE_DIR / script
        if not script_path.is_file():
            print(f"\n[{i}/{len(STAGES)}] FAILED: {script} not found at {script_path}")
            sys.exit(1)

        print(f"\n[{i}/{len(STAGES)}] {script} - {desc}")
        t0 = time.time()
        result = subprocess.run([sys.executable, str(script_path), *args])
        dt = time.time() - t0

        if result.returncode != 0:
            print(
                f"\n=== rebuild_area.py: stage {i}/{len(STAGES)} ({script}) "
                f"FAILED with exit code {result.returncode} after {dt:.1f}s. "
                "Stopping - the bake is incomplete/partial, nothing further "
                "will run. Fix the failure above and re-run rebuild_area.py "
                "from the start (or manually resume from this stage) before "
                "trusting anything in Content/OpenDrive/Masks/. ==="
            )
            sys.exit(result.returncode)

        print(f"[{i}/{len(STAGES)}] {script} done in {dt:.1f}s")

    total = time.time() - t_start
    print(
        f"\n=== rebuild_area.py: all {len(STAGES)} stages completed for "
        f"AREA_NAME={AREA_NAME!r} in {total / 60:.1f} min. "
        "verify_terrain_fit.py and verify_road_fit.py above both check the "
        "actual .bin files on disk - a PASS is still not a substitute for "
        "loading the level. ==="
    )


if __name__ == "__main__":
    main()
