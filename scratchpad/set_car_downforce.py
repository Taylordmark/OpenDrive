import unreal
# mirrors downforce_coeff in unreal/import_car_meshes.py (no mesh re-import needed)
for name, coeff in {"DA_Vehicle_Concept": 2.51, "DA_Vehicle_LMP": 3.37, "DA_Vehicle_Super": 1.56}.items():
    path = f"/Game/OpenDrive/Cars/{name}"
    a = unreal.load_asset(path)
    a.set_editor_property("downforce_coeff", coeff)
    unreal.EditorAssetLibrary.save_asset(path, only_if_is_dirty=False)
    unreal.log(f"DOWNFORCE {name} = {unreal.load_asset(path).get_editor_property('downforce_coeff')}")
