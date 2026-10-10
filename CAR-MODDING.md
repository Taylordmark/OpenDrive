# Editing the cars visually (Blender round-trip)

The seven car bodies are plain OBJ files — you can reshape them in **Blender**
(free, blender.org) and pull the result back into the game with one command.
This is the most detailed editing route available: full vertex/face-level
control, not just parameters.

## The files

Edit these (each is one car, in centimeters, pivot at axle height):

    data\cars\procedural\obj\SM_Car_Muscle.obj
    data\cars\procedural\obj\SM_Car_JDM.obj
    data\cars\procedural\obj\SM_Car_Super.obj
    data\cars\procedural\obj\SM_Car_Pickup.obj
    data\cars\procedural\obj\SM_Car_Rally.obj
    data\cars\procedural\obj\SM_Car_Concept.obj
    data\cars\procedural\obj\SM_Car_LMP.obj

Wheels are a separate mesh (`SM_CarWheel.obj`) — the game places them at the
axle positions, so don't move where the wheel arches sit vertically (the
bottom of the car at z=0 is axle height).

## Blender workflow

1. **File → Import → Wavefront (.obj)** — leave every import option at its
   default. Pick the car you want.
2. Edit it however you like (Edit Mode: move verts, extrude, sculpt the
   silhouette...). Two rules:
   - **Don't rename or merge the materials** (`body`, `glass`, `dark`, ...).
     The game maps its real materials onto those names — a renamed material
     comes in as the wrong surface. Assigning FACES to a different existing
     material is fine (that's how you make a panel glass vs. paint).
   - Colors come from which material a face uses, not from painting —
     there's a shared palette texture, so leave the UVs alone unless you
     know which palette cell you're aiming at.
3. **File → Export → Wavefront (.obj)** over the SAME file, with:
   - Forward: **-Z**, Up: **Y** (the defaults — matching the import)
   - Scale: **1.0**
   - **Triangulated Mesh: ON**
   - Include UVs + Normals + Materials: ON (defaults)
4. Run **`import_cars.bat`** (repo root — double-click or run from a
   terminal). Takes ~20s.
5. Launch the game — your edit is on the car everywhere (garage, drive).

## Warnings

- **`make_car_meshes.py` / `preview_cars.py` OVERWRITE these OBJs** when the
  generator runs — they regenerate the stock cars. Keep a copy of any edit
  you care about (e.g. a `data\cars\custom\` folder) and don't run the
  previewer while hand-editing, or your work is gone on its next rebuild.
- Collision and physics don't change with the mesh (the game uses the stock
  car's handling), so a wildly different silhouette will still drive like
  the original — visual-only for now.
- If an import comes in looking broken (inside-out faces, wrong materials),
  re-export with the settings above; the usual culprit is a changed
  forward/up axis or untriangulated n-gons.

## Handling fields (grip)

Grip is physical: there is no global multiplier. Each car's `UODVehicleDef`
(set in `unreal/import_car_meshes.py` / `apply_vehicle_defs.py`) sets:

- **`mu_peak` + `tire_load_sensitivity`**: the tire compound. Use
  all-terrain 0.90/0.25, street 1.00/0.25, performance 1.05/0.25,
  sport 1.15/0.22, semi-slick 1.25/0.20 or slick 1.60/0.15.
- **`rear_grip_ratio`**: rear/front grip (wider rears). 1.05-1.10 is
  stable under brake + steer; 1.0 is looser.
- **`downforce_coeff`**: N per (m/s)^2, = 0.6125 x ClA. Road car 0-0.3,
  GT ~1.2, prototype ~3.4.
- **`aero_balance_front`**: front share of downforce. About the front
  weight fraction minus 0.03.
- **`mass_kg`, `front_weight_frac`, `com_local_z`**: normal load and weight
  transfer.

Cornering and braking g come out of these: about 1.0 g for road cars and
1.7 g for the LMP at low speed, rising to about 2.9 g at 60 m/s. Tuning
presets are relative trims on top. After changing a car, run
`python pipeline/checks/esc_sim.py`. It prints every car's lateral and
brake g and asserts the bands and spin checks.
