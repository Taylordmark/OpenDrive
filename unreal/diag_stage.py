"""Read-only diagnostics - prints landscape/PlayerStart state to Output Log.

Tools > Execute Python Script... > this file, then paste the LogPython
lines that start with DIAG.
"""

import json
from pathlib import Path

import unreal

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "stages" / "tail_of_the_dragon"
meta = json.loads((DATA_DIR / "stage_meta.json").read_text())

actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
actors = actor_sub.get_all_level_actors()


def v3(v):
    return f"({v.x:.0f}, {v.y:.0f}, {v.z:.0f})"


unreal.log(f"DIAG expected landscape loc {meta['landscape']['location_cm']} "
           f"scale {meta['landscape']['scale']}")
unreal.log(f"DIAG expected playerstart  {meta['player_start']['location_cm']} "
           f"yaw {meta['player_start']['yaw_deg']}")

n_road = 0
for a in actors:
    cls = a.get_class().get_name()
    if "Landscape" in cls and "Streaming" not in cls and "Gizmo" not in cls:
        t = a.get_actor_transform()
        unreal.log(f"DIAG landscape [{cls}] '{a.get_actor_label()}' "
                   f"loc {v3(t.translation)} scale ({t.scale3d.x:.4f}, "
                   f"{t.scale3d.y:.4f}, {t.scale3d.z:.4f})")
    elif cls == "PlayerStart":
        unreal.log(f"DIAG playerstart '{a.get_actor_label()}' "
                   f"loc {v3(a.get_actor_location())} "
                   f"rot {a.get_actor_rotation()}")
    elif cls == "DynamicMeshActor":
        n_road += 1
unreal.log(f"DIAG road chunks present: {n_road}")

world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
x, y, z = meta["player_start"]["location_cm"]
try:
    hit = unreal.SystemLibrary.line_trace_single(
        world, unreal.Vector(x, y, z + 200000), unreal.Vector(x, y, z - 200000),
        unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, False, [],
        unreal.DrawDebugTrace.NONE, True, unreal.LinearColor.RED,
        unreal.LinearColor.GREEN, 0.0)
    if hit:
        unreal.log(f"DIAG ground under spawn: hit '{hit.get_editor_property('hit_object_handle')}' "
                   f"at z={hit.get_editor_property('location').z:.0f} "
                   f"(spawn z={z:.0f})")
    else:
        unreal.log("DIAG ground under spawn: NO HIT - nothing solid within +/-2km!")
except Exception as exc:
    unreal.log_warning(f"DIAG trace failed: {exc}")
