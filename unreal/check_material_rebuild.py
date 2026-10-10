"""Re-run check for the in-place material build scripts.

Builds each script's material(s) into /Game/_Scratch twice and requires the
same expression count both times (the 2nd run takes the clear-in-place path).
UE 5.8's MaterialEditingLibrary.delete_all_material_expressions() only
removes about half the nodes per call, so a single call left stale nodes
behind on every rebuild. Content/_Scratch is deleted afterwards; nothing under
/Game/OpenDrive is saved.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import shutil
from pathlib import Path

import unreal

HERE = Path(__file__).resolve().parent
SCRATCH = "/Game/_Scratch"
SCRIPTS = ["apply_road_material.py", "apply_ground_material.py", "apply_embankment_material.py",
           "apply_bridge_material.py", "apply_building_material.py", "apply_parking_material.py",
           "apply_water_material.py", "apply_rock_material.py", "apply_garage_material.py",
           "apply_headlight_beam_material.py", "apply_lens_drop_material.py",
           "apply_lightning_material.py", "apply_night_sky_material.py",
           "apply_snow_particle_material.py", "apply_tornado_material.py"]
EAL = unreal.EditorAssetLibrary
MEL = unreal.MaterialEditingLibrary


def run_all():
    for name in SCRIPTS:
        path = HERE / name
        src = path.read_text(encoding="utf-8").replace('"/Game/OpenDrive/Materials"', f'"{SCRATCH}/Materials"')
        exec(compile(src, str(path), "exec"), {"__file__": str(path), "__name__": "__main__"})
    out = {}
    for p in EAL.list_assets(SCRATCH, recursive=True):
        a = unreal.load_asset(p)
        if isinstance(a, unreal.Material):
            out[a.get_name()] = MEL.get_num_material_expressions(a)
    return out


try:
    first, second = run_all(), run_all()
finally:
    # on disk, not delete_directory(): force-deleting just-built packages trips an ensure
    shutil.rmtree(Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_content_dir())) / SCRATCH.split("/")[-1],
                  ignore_errors=True)
bad = sorted(n for n in first if first[n] != second.get(n))
for n in sorted(first):
    unreal.log_warning(f"[check_material_rebuild] {'FAIL' if n in bad else 'PASS'} {n}: {first[n]} -> {second.get(n)}")
unreal.log_warning(f"[check_material_rebuild] {len(first)} materials, " + ("ALL PASS" if first and not bad else f"FAILED: {bad}"))
