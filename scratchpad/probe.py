import unreal
fns = [f for f in dir(unreal.MaterialEditingLibrary) if not f.startswith('_')]
with open(r"C:\Users\taylo\Coding\OpenDrive\scratchpad\probe_out.txt", "w") as f:
    f.write("\n".join(fns))
