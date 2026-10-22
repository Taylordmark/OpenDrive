# Forest distribution: the roadside tree wall (2026-10-22)

## Root cause

Two tree systems with very different densities met at the near-scatter edge:

1. **Near 3D trees** (`AODForestManager::BuildTile`) were scattered at
   `TreesPerSqM` 0.05/m² × the biome's `TreeDensityScale`, inside a band
   only `ForestBandCm` 120 m off the centerline. Each tree was gated only by
   the biome blend's canopy weight. That weight is smooth at 300 m, so it
   said "forest" for meadows and sage too. Result: 300–500 trees per ha of
   forest in the first 120 m.
2. **Far cards** (`ODFarForest.cpp`, `farforest_<area>_test.bin`) are one
   card per 20 m WorldCover tree cell, which is **25 per ha**.

So the band was **13.4× denser** than everything past it in Summit County,
and **8.6×** in Deals Gap. From the car that reads as a solid wall on each
side, with sparse forest behind. Two smaller seams made it worse:

- Band cards (far cards within 130 m of a road) were hidden for a whole
  1 km chunk whenever the chunk touched the near radius. That left a bare
  corridor ahead between about 500 m and the next chunk.
- There was a gap between the 120 m near band and the 130 m card band.

## Fix (runtime only, no rebake)

- **Landcover gate for near trees.** A near tree now plants only on a
  WorldCover tree cell, using the cells already in the far bin
  (`IsTreeCellAt`). Inside the road fade, a neighbouring tree cell also
  counts, because the bake drops cells within 18 m of stations. Off
  tree cells, trees appear only at the biome's `AmbientVegFloor`.
- **Matched density.** Near trees use `ForestTreesPerSqM` 0.0075/m²
  (75/ha) when the gate is active. Far cards go to 3 per cell (75/ha),
  slope-fitted from neighbouring cells with a small downhill sink
  (`FarExtraSink` 0.25).
- **Verge clearance.** Near trees fade in with a smoothstep from 11.5 m
  (pavement exclusion) to 40 m (`RoadFadeCm`) off the centerline.
- **Band seams.** `ForestBandCm` is now 130 m, matching the bake. Band
  cards are hidden per 200 m square instead of per 1 km chunk.
- **Fallback.** If an area has no far bin, the old canopy-weight path runs
  unchanged.

Code: `ODForestManager.h/.cpp`, `ODFarForest.cpp`. Analysis:
`pipeline/analyze_forest_distribution.py`, which replays both systems
offline from baked data.

## Numbers (trees per ha of WorldCover forest, 6 km window)

| Area / mode | 12–40 m | 40–120 m | 130–300 m | 300–1000 m | band / beyond |
|---|---|---|---|---|---|
| Summit County before | 511 | 332 | 24.8 | 25.0 | 13.4× |
| Summit County after | 60.7 | 75.1 | 73.7 | 75.1 | 1.0× |
| Deals Gap before | 289 | 215 | 25.2 | 25.0 | 8.6× |
| Deals Gap after | 49.1 | 75.8 | 74.8 | 74.9 | 1.0× |

The 12–40 m bin is lower on purpose: that is the verge fade. Maps and
histograms are saved as `<area>_<mode>_{map,hist}.png`, and the numbers as
`<area>_<mode>.json`.

**Card heights** (`<area>_zcheck.json`, against the shipped terrain tiles):

- Record cards are within 0.05 m (p95).
- The new extra cards have a p95 error of 2.48 m in Summit County and
  3.17 m in Deals Gap. With flat placement (no slope fit) it would be
  5.6 m and 7.0 m.
- Cards floating more than 1.5 m: 0.6% in Summit County, 1.2% in Deals Gap.

**`verify_forest_elevation_fit.py`: PASS for both areas.** The maximum
error is 0.01 m. No data changed.

## Cost

- Far card instances roughly triple: about 117k → 350k in Summit County,
  and about 0.94M → 2.8M LOD0-equivalent triangles. Earlier distance-LOD
  notes say the GPU is bound by pixels, not triangles, but check fps.
- Far chunk build is about 3× slower. It stays inside the 2 ms per tick
  budget, with at least one chunk per tick.
- Near trees drop about 6× in dense forest. That saves a lot of instances
  and shadow casters.
- There are more band HISM components: up to 25 squares × 2 meshes per
  chunk, only along roads.

## What to look at in the next live drive

1. **No wall.** Forest past the road should look as dense as the forest
   at the roadside. The 130 m edge where 3D trees turn into cards should
   not show as a step in density.
2. **Verge.** Expect about 12–40 m of thinning clearance, with no trees on
   the shoulder. Meadows and sage next to the road should stay open.
3. **Ahead of the car.** There should be no bare corridor between about
   500 m and 1 km ahead.
4. **Ridges.** Look for cards floating on steep ridges or cross-slopes.
   About 1% are more than 1.5 m off in the sim. If they are visible, raise
   `FarExtraSink`.
5. **Shadows.** Cards still cast no shadows, so the 130 m edge may show as
   a change in shading even though density matches. That is a follow-up.
6. **Log.** Check the far forest "cards=…" line and the chunk build ms,
   and check fps against the last drive.
