# PBRT-v4 GI Oracle — Camera-Conditioned FPS Irradiance Reference

Status: **reference implementation / qualification candidate**

This subsystem turns the hardened CPU PBRT-v4 fork into a practical irradiance reference for realtime GI systems. It is intentionally narrower than a general renderer: the primary measurement is the hemispherical irradiance incident on the geometric side of the first material-bearing surface visible from a gameplay-like camera.

## 1. Measured quantity

For a camera ray whose first material-bearing surface is `x` with geometric-side normal `n` face-forwarded toward the camera, the oracle estimates

`E(x,n) = integral_hemisphere Li(x, wi) max(0, n . wi) dwi`.

The film value is irradiance, not reflected beauty radiance. Surface albedo therefore does not scale the sensor output. PBRT's spectral estimator is projected to the configured output RGB space only at film output; all comparisons use linear EXR/PFM values.

The integrator supports two matched estimators:

- `component="total"`: direct + recursively transported surface irradiance;
- `component="direct"`: source paths before the first material scattering vertex.

With identical Halton seed/sample count, the direct estimator is shared, so `E_indirect = E_total - E_direct` is a strongly correlated residual rather than the subtraction of unrelated noisy images.

## 2. Current qualified scope

In scope for the first project reference:

- triangle-mesh boundaries extracted independently from voxel worlds;
- diffuse, conductor, dielectric and thin-dielectric surface interfaces;
- finite emissive surfaces, infinite environment, and distant sun;
- vacuum surface transport through the camera-conditioned irradiance integrator;
- direct / indirect decomposition;
- deterministic world-space P/N/Ns alignment and dense image-space irradiance fields.

Participating-medium random walks are **not** yet part of the irradiance-reference claim. The integrator accounts for transmittance on explicit direct connections inherited from PBRT's transport utilities, but it is not a `volpath` replacement. The voxel oracle adapter therefore keeps water/glass as dielectric interfaces for this mode and omits homogeneous media. Beauty/context scenes may still use the qualified `volpath` renderer with media.

BDPT remains a vacuum/surface stress backend only. MLT is out of scope.

## 3. Camera suite

`oracle/fps_camera_suite.json` freezes twelve human-height, 70-degree-FOV FPS-like cameras: three per representative voxel level. Camera candidates are generated only from walkable cells with player-sized clearance. View scoring uses ray-fan measurements of:

- near/far geometry mix and depth variance;
- enclosure / overhead coverage;
- visible material diversity;
- open-sky threshold behavior;
- emissive visibility, angular occupancy, and distance;
- water/glass/metal visibility;
- avoidance of trivial near-wall and foliage-only views.

The final suite is frozen after a visual rejection audit. It deliberately includes bright exterior thresholds, deep covered interiors, emissive-rich views, damaged multi-story structures, foliage/structure mixtures and a very dark indirect-dominated bunker view.

Camera transforms are data, not a moving heuristic benchmark. A level source SHA mismatch invalidates a frozen camera-suite comparison until reviewed.

## 4. Voxel -> reference geometry contract

`oracle/voxel_fps_oracle.js` loads the authoritative procedural level, snapshots its paged/deduplicated volume via `toDense()` when available, and greedily extracts only material boundaries to independent binary PLY meshes.

Key invariants:

- PBRT never reuses the DUT's voxel traversal code;
- closed dielectric boundaries retain consistent outward winding;
- `MediumInterface` ordering is interior then exterior when media are enabled elsewhere;
- ambiguous solid/transmissive material-to-material boundaries are reported and must be zero for the release suite;
- canonical PBR roughness is adapter-owned: engine perceptual roughness `r` maps to GGX `alpha=r^2`, and PBRT receives alpha with `remaproughness=false`.

Emissive voxel faces become PBRT diffuse area lights. The first reference environment is deliberately fixed and reproducible: low-intensity cool infinite illumination plus a warm distant sun, augmented by level emissives. A later canonical physical sky can replace this only as a versioned lighting contract.

## 5. Process-isolated execution

The recommended runner is `oracle/run_irradiance_suite.sh`. Each PBRT invocation is a hermetic process. Python is used only to postprocess completed EXRs. This separation avoids carrying renderer state into the harness and makes failed cameras independently resumable.

For each camera the runner creates:

1. a 1-spp, no-pixel-jitter `gbuffer` alignment render;
2. one or more independent-seed total-irradiance renders;
3. the same seed/sample sequence for direct irradiance;
4. postprocessed averaged `total.pfm`, `direct.pfm`, and `indirect.pfm`;
5. `position.pfm`, `normal.pfm`, `shading_normal.pfm`;
6. `valid_mask.pgm` and stricter `comparison_mask.pgm`;
7. deterministic sparse `probes.jsonl` records;
8. `camera-report.json` with hashes and convergence statistics.

The comparison mask removes silhouette/discontinuity pixels where a pixel-integrated irradiance estimate cannot be assigned unambiguously to one center-ray surface. It requires a valid center hit plus locally consistent position and geometric normal in a 4-neighborhood.

## 6. Sampling and convergence policy

No fixed spp is called ground truth by fiat.

Recommended modes:

- bring-up / CI canary: 16–32 spp, one seed;
- ordinary reference: 128–256 spp per seed, at least two independent Halton seeds for difficult scenes;
- dark/emissive stress: increase spp until the reported independent-seed disagreement is below the tolerance required by the DUT qualification decision.

The harness reports pairwise seed disagreement using relative luminance with a floor tied to the camera's p95 irradiance. It also reports a significant-energy subset and absolute disagreement normalized by p95. A low-energy dark pixel is therefore not silently allowed to dominate convergence claims through division by almost zero.

If only one seed is rendered, the artifact is still a valid finite-sample PBRT reference but it does not carry an independent convergence estimate. Such an artifact must not be described as numerically converged solely because it rendered successfully.

## 7. DUT comparison

`oracle/compare_irradiance.py` compares a DUT PFM against any oracle total/direct/indirect buffer using the oracle comparison mask. It reports:

- RGB RMSE;
- luminance MAE;
- p50/p90/p95/p99 relative luminance error;
- p50/p90/p95/p99 log2 luminance error;
- over-lighting / under-lighting fractions;
- a dark-region light-leak false-positive rate.

A production GI qualification should normally report total and indirect separately. A system can have a plausible total image while compensating wrong direct lighting with wrong indirect lighting; the decomposition prevents that cancellation from receiving a passing score.

## 8. Release gates for the irradiance oracle

Promotion from renderer RC to **GI Irradiance Oracle Qualified** requires on the exact renderer lineage:

- the pre-existing f32/f64 dielectric and BSDF analytic/statistical gates;
- the rendered transport canaries;
- the dedicated irradiance analytic canaries (`E=pi L`, camera-side orientation, finite-disk solid angle, maxdepth/direct identity, positive multi-bounce indirect, GBuffer alignment);
- all 12 frozen FPS cameras extracted with zero ambiguous transmissive boundaries;
- no NaN/Inf in measurement buffers;
- direct/indirect decomposition and world-space alignment artifacts present;
- at least one independently reseeded convergence check in each level class;
- complete renderer/adapter/scene/camera hashes.

The beauty PNG/EXR views are contextual diagnostics. They are not the irradiance oracle. The linear aligned measurement buffers are.

## 9. Practical use with VEXEL GI

For a VEXEL GI run, export or read back the solver's screen-conditioned irradiance in the same output-RGB convention and camera resolution. Compare only the oracle's `comparison_mask`. Use `position.pfm` and `normal.pfm` to associate error with world-space regions, edits, material classes, or solver hierarchy nodes.

The most diagnostic initial plots are:

- total irradiance relative-error heatmap;
- indirect-only relative-error heatmap;
- error percentile curve per camera;
- error grouped by camera category (emissive / threshold / occluded / mixed);
- dark-region leak rate;
- indirect under-propagation in covered regions.

This makes PBRT a measurement backend rather than an aesthetic judge and gives incremental/realtime GI work a stable target that can survive solver and representation changes.
