"""§7.34 verification: monkeypatch reconcile_junction_elevations() to capture
the REAL pre-reconciliation station arrays build_road_stations.py passes it
for the current ACTIVE_AREA (summit_county), then save them to a .npz so
sweep_same_seg_radius.py can independently re-derive the same-segment pair
distance distribution without re-running the (slow) full station-build.

Runs the real pipeline entry point (build_road_stations.main()) against the
already-cached data/raw/summit_county_test/*.json - no network calls.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import numpy as np
import road_mesh_common as rmc

_captured = {}
_orig = rmc.reconcile_junction_elevations


def _capture_and_call(east_m, north_m, elev_m, seg_idx, station_idx, road_name, *args, **kwargs):
    if not _captured:
        _captured["east_m"] = np.array(east_m, dtype=np.float64)
        _captured["north_m"] = np.array(north_m, dtype=np.float64)
        _captured["elev_m"] = np.array(elev_m, dtype=np.float64)
        _captured["seg_idx"] = np.array(seg_idx, dtype=np.int64)
        _captured["station_idx"] = np.array(station_idx, dtype=np.int64)
        _captured["road_name"] = np.array(road_name, dtype=object)
        is_bridge = kwargs.get("is_bridge")
        _captured["is_bridge"] = (np.array(is_bridge, dtype=bool) if is_bridge is not None
                                   else np.zeros(len(east_m), dtype=bool))
        print(f"[capture] intercepted reconcile_junction_elevations() call: "
              f"{len(east_m)} stations, {len(np.unique(seg_idx))} segments, "
              f"is_bridge sum={int(_captured['is_bridge'].sum())}")
    return _orig(east_m, north_m, elev_m, seg_idx, station_idx, road_name, *args, **kwargs)


rmc.reconcile_junction_elevations = _capture_and_call
# build_road_stations imported reconcile_junction_elevations by name at module
# load time, so patch its local binding too, not just the module attribute.
import build_road_stations as brs
brs.reconcile_junction_elevations = _capture_and_call

brs.main()

out_path = Path(__file__).resolve().parent / "reconcile_pre_arrays.npz"
np.savez(out_path,
         east_m=_captured["east_m"], north_m=_captured["north_m"],
         elev_m=_captured["elev_m"], seg_idx=_captured["seg_idx"],
         station_idx=_captured["station_idx"],
         road_name=_captured["road_name"], is_bridge=_captured["is_bridge"])
print(f"saved {len(_captured['east_m'])} pre-reconciliation station(s) -> {out_path}")
