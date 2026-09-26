"""Diagnose: corridor-classification drift vs the 2026-09-06 bake, and where
the grid's Wetland points sit (grade/northness/elev)."""
import json, numpy as np
from pathlib import Path
R = Path(__file__).resolve().parents[2]
old = json.load(open(R/'scratchpad/terrain_variety/before/road_biomes.json'))
new = json.load(open(R/'data/road_tiles/summit_county_test/road_biomes.json'))
nc = new['n_corridor']
ob, nb = np.array(old['biome']), np.array(new['biome'][:nc])
assert np.allclose(old['x_cm'], new['x_cm'][:nc])
diff = ob != nb
print(f"corridor samples changed biome: {diff.sum()} / {nc}")
fo, fn = np.array(old['forest_weight']), np.array(new['forest_weight'][:nc])
print(f"corridor forest_weight mean abs diff {np.abs(fo-fn).mean():.4f}, frac changed>0.01: {(np.abs(fo-fn)>0.01).mean():.3f}")
b = np.array(new['biome']); g = slice(nc, None)
north = np.array(new['northness']); moist = np.array(new['moisture']); elev = np.array(new['elev_m'])
for name, idx in (('Wetland', 5), ('Evergreen', 1), ('Alpine', 6)):
    m = b[g] == idx
    print(f"grid {name}: n={m.sum()} northness mean {north[g][m].mean():+.2f} "
          f"moist mean {moist[g][m].mean():.2f} elev p10/50/90 {np.percentile(elev[g][m],[10,50,90]).round()}")
