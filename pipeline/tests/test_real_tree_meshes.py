"""Track F (PLAN-VISUALS.md) guard for make_tree_meshes.py:
  1. the stylized output is byte-identical to the pre-Track-F generator
     (sha256 prefixes below, recorded from commit 2b65727's script), and
  2. every --real mesh stays inside its tier's triangle budget, has the
     stylized twin's exact usemtl slot list (bark first, foliage second, or
     foliage only), sits on the ground, only references _real textures,
     and its wind sway weights (flora_common.sway_uv) are 0 at the base
     and inside the materials' WPO bound.

Run:  python pipeline/tests/test_real_tree_meshes.py"""
import hashlib
import math
import subprocess
import sys
from pathlib import Path

PIPE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPE))
OBJ = PIPE.parent / "data" / "stages" / "tail_of_the_dragon" / "obj"

STYLIZED_SHA = {
    "bush.mtl": "ac7996da3a0d1149",
    "bush.obj": "73d6eaf3d6f93e34",
    "bush_high.mtl": "ac7996da3a0d1149",
    "bush_high.obj": "d3959cba8cad2a6c",
    "bush_ultra.mtl": "ac7996da3a0d1149",
    "bush_ultra.obj": "36a7f8959cd3cda3",
    "grass_tuft.mtl": "3058f854c7a4d69b",
    "grass_tuft.obj": "5e1b4bb92d7b8b3e",
    "grass_tuft_high.mtl": "3058f854c7a4d69b",
    "grass_tuft_high.obj": "290e16c2254db010",
    "grass_tuft_ultra.mtl": "3058f854c7a4d69b",
    "grass_tuft_ultra.obj": "e708c8adcbbe7a29",
    "sagebrush.mtl": "3058f854c7a4d69b",
    "sagebrush.obj": "e3169b6efb152837",
    "sagebrush_high.mtl": "3058f854c7a4d69b",
    "sagebrush_high.obj": "43c94582f2025737",
    "sagebrush_ultra.mtl": "3058f854c7a4d69b",
    "sagebrush_ultra.obj": "54a3ddd3f2bade13",
    "tree_blob.mtl": "455ccdc684afa86c",
    "tree_blob.obj": "ca476c308da2254d",
    "tree_blob_high.mtl": "455ccdc684afa86c",
    "tree_blob_high.obj": "dac275fcb90dcc1e",
    "tree_blob_ultra.mtl": "455ccdc684afa86c",
    "tree_blob_ultra.obj": "a1e2eb719491a32e",
    "tree_canyon_oak.mtl": "09463960d6a85ad3",
    "tree_canyon_oak.obj": "85f158ed6da4eebd",
    "tree_canyon_oak_high.mtl": "09463960d6a85ad3",
    "tree_canyon_oak_high.obj": "a02b12d41b4640af",
    "tree_canyon_oak_ultra.mtl": "09463960d6a85ad3",
    "tree_canyon_oak_ultra.obj": "4d9df373a77218a0",
    "tree_conifer.mtl": "09463960d6a85ad3",
    "tree_conifer.obj": "6e50488fa3db86f9",
    "tree_conifer_high.mtl": "09463960d6a85ad3",
    "tree_conifer_high.obj": "3f5c56b020c2c6ab",
    "tree_conifer_ultra.mtl": "09463960d6a85ad3",
    "tree_conifer_ultra.obj": "b35c790430419605",
    "tree_manzanita.mtl": "09463960d6a85ad3",
    "tree_manzanita.obj": "8ffbd02dc9cac1ae",
    "tree_manzanita_high.mtl": "09463960d6a85ad3",
    "tree_manzanita_high.obj": "b027c412417d4297",
    "tree_manzanita_ultra.mtl": "09463960d6a85ad3",
    "tree_manzanita_ultra.obj": "4a973386b3c76313",
    "tree_olive.mtl": "09463960d6a85ad3",
    "tree_olive.obj": "2c594c69aae97ed4",
    "tree_olive_high.mtl": "09463960d6a85ad3",
    "tree_olive_high.obj": "42f0de60f3fda4c9",
    "tree_olive_ultra.mtl": "09463960d6a85ad3",
    "tree_olive_ultra.obj": "54df120f02ebfaa6",
    "tree_scrub_oak.mtl": "455ccdc684afa86c",
    "tree_scrub_oak.obj": "036a3ce4d4b902df",
    "tree_scrub_oak_high.mtl": "455ccdc684afa86c",
    "tree_scrub_oak_high.obj": "f1ac403115defa7c",
    "tree_scrub_oak_ultra.mtl": "455ccdc684afa86c",
    "tree_scrub_oak_ultra.obj": "f0e1e6d81e9d2e50",
    "tree_yucca.mtl": "09463960d6a85ad3",
    "tree_yucca.obj": "4df84e8d43ed55f3",
    "tree_yucca_high.mtl": "09463960d6a85ad3",
    "tree_yucca_high.obj": "a517878a7e63028f",
    "tree_yucca_ultra.mtl": "09463960d6a85ad3",
    "tree_yucca_ultra.obj": "1fa9ee936f8e90a9",
}

subprocess.run([sys.executable, str(PIPE / "make_tree_meshes.py")], check=True, capture_output=True)
for name, want in STYLIZED_SHA.items():
    got = hashlib.sha256((OBJ / name).read_bytes()).hexdigest()[:16]
    assert got == want, f"stylized {name} changed ({got} != {want})"

subprocess.run([sys.executable, str(PIPE / "make_tree_meshes.py"), "--real"], check=True, capture_output=True)
import flora_common as fc
import make_tree_meshes as mt
for slot in mt.REAL_SPECIES:
    twin_mats = list(dict.fromkeys(l.split()[1] for l in (OBJ / f"{slot}.obj").read_text().splitlines()
                                   if l.startswith("usemtl")))   # UE merges repeats into one slot
    for tier, budget in mt.REAL_TRI_BUDGET_SLOT.get(slot, mt.REAL_TRI_BUDGET).items():
        name = f"{slot}_real{tier}"
        text = (OBJ / f"{name}.obj").read_text()
        tris = sum(1 for l in text.splitlines() if l.startswith("f "))
        mats = [l.split()[1] for l in text.splitlines() if l.startswith("usemtl")]
        assert 0 < tris <= budget, f"{name}: {tris} tris > budget {budget}"
        assert mats == twin_mats, f"{name}: slots {mats} != stylized {twin_mats}"
        min_z = min(float(l.split()[3]) for l in text.splitlines() if l.startswith("v "))
        assert slot not in mt.REAL_TRI_BUDGET_SLOT or min_z > -6, f"{name}: min z {min_z} (ground cover is planted by its min Z)"
        pts = [tuple(map(float, l.split()[1:4])) for l in text.splitlines() if l.startswith("v ")]
        H, R = max(z for _, _, z in pts), max(math.hypot(x, y) for x, y, _ in pts)
        u, v = zip(*(fc.sway_uv(x, y, z, H, R) for x, y, z in pts))   # import_biome_flora's UV1 bake
        assert fc.sway_uv(0, 0, 0, H, R) == (0, 0), name
        assert max(u) <= fc.SWAY_U_MAX and max(v) <= fc.SWAY_V_MAX, f"{name}: sway {max(u):.2f}/{max(v):.2f} over the WPO bound"
        mtl = (OBJ / f"{name}.mtl").read_text()
        assert all("_real" in l for l in mtl.splitlines() if l.startswith("map_")), name
        print(f"{name:28s} {tris:5d} tris (budget {budget})")
print("ok")
