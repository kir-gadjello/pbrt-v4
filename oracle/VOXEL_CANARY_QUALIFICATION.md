# Voxel reference canary qualification — 2026-09-04

This staging lineage carries the renderer-independent voxel-to-PBRT adapter and its qualification contract. It is intentionally separate from the PBRT renderer-hardening branch until the adapter and eight-view smoke dataset are promoted together.

Validated locally against the exact qualified Linux f32 PBRT artifact from renderer commit `35220cff46c1ccd3e1d994152404c8d12ac4a43f`.

## Dataset summary

Four deterministic/reference-resampled voxel levels were independently surface-extracted at 0.1 m and rendered from two cameras each at 32 spp, 512x288, `volpath`, max depth 10.

- `pcg_island_ravine.js`: 672,370 occupied voxels -> 51,334 greedy quads / 102,668 triangles, 16 materials.
- `pcg_holed_building_among_trees.js`: 3,454,583 occupied -> 1,019,563 quads / 2,039,126 triangles, 140 materials.
- `pcg_building_carcass_among_trees.js`: 3,839,625 occupied -> 948,847 quads / 1,897,694 triangles, 137 materials.
- `pcg_bunkers_under_canopy.js`: 9,336,440 occupied -> 890,959 quads / 1,781,918 triangles, 17 materials.

All four extractors reported zero ambiguous transmissive transitions. All eight rendered linear images contained zero NaN/Inf channels.

The 32 spp images are geometry/material/transport bring-up canaries, not statistical ground truth.

## Performance finding

For paged/deduplicated engine volumes the adapter materializes one immutable dense snapshot using `toDense()` when available. On the 384x160x384 carcass volume, `toDense()` took ~86 ms after generation; total extraction became ~31 s, of which ~27 s was the procedural generator itself. The 512x160x512 bunker world completed generation + extraction in ~8.1 s.

This is both a performance optimization and a provenance improvement: meshing operates on a frozen authoritative world image instead of repeatedly querying a mutable paged data structure.

PBRT render time at 32 spp was only a few seconds per 512x288 camera even for ~2M-triangle surfaces, so procedural generation/reference extraction—not PBRT transport—is the dominant preparation cost.

## Material semantics

The adapter owns canonical normalization:

- engine perceptual roughness `r` -> GGX `alpha = r^2`;
- PBRT scenes emit that alpha with `remaproughness=false`;
- water uses dielectric eta 1.333 and a named homogeneous interior medium;
- closed transmissive boundaries must be outward-wound;
- `MediumInterface` is emitted `(interior, exterior)`.

See `VOXEL_ADAPTER_INVARIANTS.md` for the release contract.