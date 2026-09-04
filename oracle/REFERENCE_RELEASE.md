# PBRT-v4 Reference Fork — GI Oracle Release Contract

Status: **qualification branch / release candidate work**

This fork is not intended to certify every PBRT-v4 feature. Its purpose is narrower and more useful: provide a pinned, reproducible, physically defensible CPU reference renderer for qualification of sparse real-time GI systems used with teardown-like voxel worlds.

## 1. Release claim

A commit may be called **GI Oracle Qualified** only when all mandatory gates below pass on that exact commit. The claim applies only to the qualified surface in §2. Features in §3 are explicitly outside the claim even when they happen to build or work.

The reference fork is expected to prefer explicit physical/material semantics and reproducibility over preserving historical image appearance of upstream scenes.

## 2. Qualified surface

### Geometry

- triangle meshes / surface-extracted voxel boundaries
- analytic planes, disks, quads and spheres when used by qualification scenes
- instancing and rigid transforms only insofar as exercised by canary scenes

The production GI oracle should convert voxel worlds to an independent surface representation rather than reuse the DUT's voxel traversal code. This avoids correlated visibility bugs.

### Materials

Mandatory:

- diffuse/Lambertian
- dielectric, including rough dielectric
- thin dielectric
- conductor, including rough conductor
- physically bounded emissive surfaces

Optional for the first qualified release and only part of the claim after explicit tests exist:

- coated diffuse / coated conductor
- diffuse transmission

Material normalization is owned by the oracle adapter, not by PBRT scene defaults. In particular, the canonical microfacet parameter is **GGX alpha**. If an engine-facing perceptual roughness `r` is accepted, this fork defines the mapping as `alpha = r^2`.

### Media

- vacuum
- homogeneous absorption/scattering media used for water/glass/fog qualification

Heterogeneous volumes are not required for the first release claim.

### Lighting / environment

- finite area lights
- point/distant lights when useful for analytic fixtures
- uniform and image infinite lights
- externally generated canonical sky/environment maps

Sky semantics should normally be defined outside PBRT and supplied as a radiance environment so the same environment can drive PBRT, the DUT, and independent solvers.

### Integrators

Primary reference backends:

- `path` for surface-only transport
- `volpath` for the general surface + participating-media path

Stress / differential backend:

- `bdpt` for difficult specular transport and caustic-oriented cases where applicable

MLT is not required to qualify the first release and should not be used as the sole reference for any scene.

### Platform

- CPU renderer only
- f32 is the production reference build
- f64 (`PBRT_FLOAT_AS_DOUBLE=ON`) is a diagnostic/meta-oracle build used to expose numerical precision regressions

GPU/CUDA/OptiX behavior is outside the release claim.

## 3. Explicit exclusions

The first GI Oracle Qualified release does **not** claim qualification of:

- GPU rendering paths
- hair/curve transport
- subsurface scattering
- measured BSDFs
- every spectral material preset
- exotic cameras/lenses
- all heterogeneous media models
- every light sampler or accelerator combination
- PBRT utility programs unrelated to the qualification pipeline

An excluded subsystem may be used experimentally, but its output must not silently acquire reference status.

## 4. Intentional semantic deviations from upstream

### Perceptual roughness

The fork uses `alpha = roughness^2` for the engine-facing perceptual roughness mapping. Upstream historically retained `sqrt(roughness)` for compatibility with existing scene appearance despite documenting that it was not the desired perceptual mapping. A reference renderer must choose defined semantics over compatibility with scenes authored around that behavior.

### Numerical hardening

Reference-only corrections should be accepted when they remove undefined/NaN behavior, preserve the configured `Float` precision, or make failed numerical operations deterministic without changing the intended transport equation.

Every such correction requires an analytic or invariant test and a dedicated commit.

## 5. Mandatory qualification gates

A release commit must satisfy all of the following on the exact SHA.

### A. Analytic / algebraic gates

- dielectric Fresnel vs closed form over both interface orientations
- normal-incidence reflectance
- critical-angle and total-internal-reflection behavior
- smooth dielectric branch probabilities
- smooth dielectric sample throughput in an importance white furnace
- radiance-transport eta Jacobian
- rough dielectric `Sample_f` / evaluator / PDF consistency with precision-appropriate tolerances
- rough dielectric reflection reciprocity
- passive-material energy upper bounds
- diffuse cosine-sampling estimator identity
- directed floating-point boundary tests used by PBRT ray robustness

### B. Existing PBRT statistical gates

- the relevant `BSDFSampling.*` tests
- the relevant `BSDFEnergyConservation.*` tests

These are necessary but are not accepted as a substitute for the independent analytic gates above.

### C. End-to-end rendered transport canaries

The executable renderer must render tiny deterministic scenes that exercise parser → material construction → BSDF → visibility → lighting → integrator → film. The first release must include at least:

1. diffuse + uniform environment proportionality canary;
2. dielectric/conductor finite-value and bounded-energy canary;
3. `path` vs `volpath` vacuum differential canary;
4. homogeneous-medium attenuation canary;
5. a small specular/caustic stress scene rendered by the primary and differential integrators.

Rendered canaries are measured numerically from linear EXR/PFM output. Denoising, display tonemapping and sample clamping must not participate in oracle measurements.

### D. Voxel-world canaries

Before promotion from release candidate to project reference release:

- at least four representative voxel levels are independently surface-extracted;
- at least two cameras per level are rendered;
- a minimum 32 spp smoke set completes without NaN/Inf or broken geometry;
- representative views receive higher-spp convergence renders or multiple independent seeds;
- the scene/material/environment adapter hashes are recorded.

The 32 spp set is a bring-up/canary dataset, not itself statistical ground truth.

## 6. Precision policy

The f64 lane is a meta-oracle for numerical conditioning, not a replacement for the f32 production build.

For identities that should be algebraically exact, f64 should converge to a substantially tighter residual than f32. For sample/evaluator comparisons involving direction reconstruction in f32, tolerances may be conditioning-aware, but they must be justified by an f64 comparison and must not mask PDF disagreement, non-finite values, reciprocity errors or energy creation.

Any test tolerance relaxation requires a comment explaining the numerical mechanism and a bound based on observed f32/f64 behavior.

## 7. Reproducibility / provenance contract

Every reference render or released binary must record at least:

- repository and exact commit SHA
- submodule SHAs
- compiler identity/version
- build type and CMake options
- f32/f64 mode
- `pbrt --version`
- SHA-256 of `pbrt` and `imgtool`
- OS/architecture
- scene and include-file SHA-256
- environment map SHA-256
- integrator and all non-default integrator parameters
- sampler, seed and spp
- maximum depth / Russian-roulette-relevant settings
- film resolution and output format
- oracle adapter version/hash

Reference artifacts should be immutable and addressed by these hashes rather than by a mutable branch name.

## 8. Release states

### Qualification branch

Development state. Individual tests may fail while a defect is being isolated.

### Release candidate (RC)

Requires A + B + C green on the exact SHA, a sealed f32 binary manifest, and no known correctness blocker in the qualified surface. Voxel-world canaries may still be incomplete, but that limitation must be stated.

### GI Oracle Qualified release

Requires RC gates plus D, reviewed provenance manifests, and no unresolved high-severity issue affecting the qualified surface.

A known issue outside §2 does not block release. A known issue inside §2 does.

## 9. Operational recommendation

Consumers should invoke the renderer as a hermetic process behind a canonical `OracleScene` adapter instead of linking application transport code deeply into PBRT. That architecture keeps geometry/material/environment normalization explicit, allows a second renderer to be added for differential checks, and reduces correlated bugs with the real-time GI implementation.
