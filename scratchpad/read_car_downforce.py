import unreal
for n in ["Concept","LMP","Super","Muscle","JDM","Pickup","Rally"]:
    a = unreal.load_asset(f"/Game/OpenDrive/Cars/DA_Vehicle_{n}")
    unreal.log_warning(f"DFREAD {n} {a.get_editor_property('downforce_coeff')}")
