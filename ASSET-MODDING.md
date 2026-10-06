# Swapping in free 3D models (cars, trees, rocks)

Drop a model into `data\mods\<folder>\`, **named after the stock asset it
replaces**, then run **`import_mods.bat`**. It replaces that asset everywhere
in the game. Formats: `.glb` / `.gltf` (best, keeps PBR textures), `.fbx`,
`.obj`.

| Folder  | Name the file one of |
|---------|----------------------|
| `Cars`  | `SM_Car_Muscle`, `SM_Car_JDM`, `SM_Car_Super`, `SM_Car_Pickup`, `SM_Car_Rally`, `SM_Car_Concept`, `SM_Car_LMP`, `SM_CarWheel` |
| `Trees` | `tree_blob`, `tree_olive`, `tree_conifer`, `tree_scrub_oak`, `tree_manzanita`, `tree_yucca`, `tree_canyon_oak`, `bush`, `sagebrush`, `grass_tuft` |
| `Rocks` | `SM_RockBoulderA`, `SM_RockBoulderB`, `SM_RockBoulderC`, `SM_RockBoulderD` |

Example: `data\mods\Rocks\SM_RockBoulderA.glb`. A file with an unknown name
is skipped, and the window lists the valid names.

## Before exporting from Blender

1. **Join everything into one object** (select all → Ctrl+J). Apply
   transforms (Ctrl+A → All Transforms).
2. **Match the stock asset's size and placement.** Import the stock OBJ as a
   reference (`data\cars\procedural\obj\`, `data\flora\obj\`, or
   `data\stages\tail_of_the_dragon\obj\` for trees), line your model up over it, then delete
   the reference. After the import, the window prints the stock vs. mod size
   and warns if it's more than 2x off, which usually means the scale or
   rotation is wrong.
3. **Cars:**
   - Delete the wheels. The game draws its own wheels (`SM_CarWheel`) at the
     stock car's axle positions, so your body's wheel arches have to sit over
     them.
   - Point the car the same way as the stock car, with the pivot at axle
     height (the bottom of the body at z=0).
   - Handling doesn't change: it comes from the stock car's settings, not
     the mesh. A box collision is rebuilt to fit the new body.
4. **Trees:** opaque bark goes in material slot 0, alpha-cutout leaves in
   slot 1. `tree_conifer` and `tree_blob` have their materials replaced by
   the seasonal snow/fall materials, so pick another species if your model
   has its own leaf textures you want to keep.
5. **Rocks** always render with the game's rock material (M_Rock030), so
   only the shape comes from your model.
6. Keep models reasonably low-poly (Nanite is turned off; trees and rocks
   are drawn thousands of times). Under ~20k triangles for a car and a few
   thousand for a tree or rock is a good target.

## Order matters

`import_cars.bat`, `unreal\import_rock_meshes.py` and
`unreal\import_biome_flora.py` re-import the stock meshes **over your
mods**. Re-run `import_mods.bat` after running any of them. To remove a mod,
delete its file and re-run the matching stock import.

## Where to get free models

CC0 (no attribution needed, can be used for anything):
- **Kenney** (kenney.nl/assets): Car Kit, City Kit, Nature Kit. Low-poly,
  matches this game's style.
- **Quaternius** (quaternius.com): stylized cars, nature, ultimate packs.
- **Poly Haven** (polyhaven.com/models): photoscanned rocks and plants.
- **ambientCG** (ambientcg.com): textures only.

Also: **Sketchfab** (filter "Downloadable" + license) and **OpenGameArt**.
Check each model's license. CC-BY means you must credit the author, and
"NC" (non-commercial) or "Editorial" licenses can't be used in a game you'd
ever sell. Keep a note of where each mod came from in
`data\mods\SOURCES.txt`.
