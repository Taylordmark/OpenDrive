"""Headless: GT_Ground keeps only grass tufts (bushes now come from
AODForestManager, which respects the road corridor + collision)."""
import unreal

path = "/Game/OpenDrive/Materials/GT_Ground"
gt = unreal.load_asset(path)
keep = []
for v in gt.get_editor_property("grass_varieties"):
    mesh = v.get_editor_property("grass_mesh")
    if mesh and mesh.get_name() != "bush":
        keep.append(v)
gt.set_editor_property("grass_varieties", keep)
unreal.EditorAssetLibrary.save_asset(path)
unreal.log(f"GROUND kept {len(keep)} varieties (bushes stripped)")
