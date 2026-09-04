# PBRT-v4 Reference Fork — GI Oracle Release Contract

Status: **qualification branch / RC work**

This fork does not attempt to certify every PBRT-v4 feature. Its purpose is narrower: provide a pinned, reproducible, physically defensible **CPU reference renderer** for qualification of sparse real-time GI systems in teardown-like voxel worlds.

A commit may be called **GI Oracle Qualified** only when all mandatory gates below pass on that exact commit. The claim applies only to the explicitly qualified surface.

## 1. Qualified surface

### Geometry

- triangle meshes / independently surface-extracted voxel boundaries;
- simple analytic fixtures used by qualification scenes;
- rigid transforms and instancing only insofar as exercised by canaries.

The oracle geometry path must not reuse the DUT's DDA, visibility cache, GI page graph, surface cache, or interpolation code.

**Orientation is part of the physical contract.** Closed dielectric and medium boundaries must have coherent outward winding. PBRT defines the dielectric exterior as the side toward which the surface normal points, and `MediumInterface` likewise associates distinct interior/exterior media. Inverted geometry can therefore change the physical interface, not merely its shading orientation.

The adapter must reject or explicitly repair inverted/ambiguous closed transmissive components, and any repair must be recorded in provenance. Non-manifold/open volumetric boundaries are outside reference status unless sidedness is explicitly authored.

### Materials

Mandatory:

- diffuse/Lambertian;
- dielectric, including rough dielectric;
- thin dielectric;
- conductor, including rough conductor;
- physically bounded emissive surfaces.

Optional only after dedicated qualification:

- coated diffuse / coated conductor;
- diffuse transmission.

**Material normalization belongs to the oracle adapter.** The canonical microfacet parameter is GGX alpha. For our engine-facing perceptual roughness convention `r`, the adapter uses:

`alpha = r^2`

and emits PBRT materials with `remaproughness=false`. PBRT's historical default `roughness` remapping is deliberately left source-compatible and therefore cannot silently redefine oracle semantics.

### Media

Qualified:

- vacuum;
- homogeneous absorption/scattering media used for water/glass/fog qualification through `volpath`.

Heterogeneous volumes are outside the first release claim.

### Lighting / environment

- finite area lights;
- point/distant lights for analytic fixtures;
- uniform and image infinite lights;
- externally generated canonical sky/environment maps.

Sky semantics should normally be defined outside PBRT and supplied as a radiance environment so the same environment can drive PBRT, the DUT, and independent solvers.

### Integrators

Primary reference backends:

- `path` for surface-only transport;
- `volpath` for the qualified general surface + participating-media path.

Differential/stress backend:

- `bdpt` **only for vacuum/surface specular and caustic stress cases**.

PBRT has unresolved upstream reports of BDPT disagreement in participating media; therefore BDPT media results do not receive reference status in this release. MLT is explicitly out of scope and must never be the sole oracle.

### Platform

- CPU renderer only;
- f32 is the production reference build;
- f64 (`PBRT_FLOAT_AS_DOUBLE=ON`) is a numerical/meta-oracle build.

GPU/CUDA/OptiX behavior is outside the release claim.

## 2. Explicit exclusions

The first GI Oracle Qualified release does **not** claim qualification of:

- GPU rendering paths;
- hair/curve transport;
- subsurface scattering;
- measured BSDFs;
- every spectral material preset;
- exotic cameras/lenses;
- heterogeneous media;
- BDPT in participating media;
- MLT;
- every light sampler or accelerator combination;
- PBRT utilities unrelated to the qualification pipeline.

An excluded subsystem may be used experimentally, but its output must not silently acquire reference status.

## 3. Source-hardening policy

The fork should keep renderer-source divergence from upstream small. A source correction is justified only when it removes undefined/non-finite behavior, preserves the configured `Float` precision, or fixes a transport/numerical defect demonstrated by an invariant or analytic test.

Scene-language or artist-parameter conventions should normally be canonicalized in the `OracleScene` adapter instead of globally redefining PBRT defaults.

Every source correction requires an analytic/invariant regression and a dedicated auditable commit.

## 4. Mandatory qualification gates

### A. Analytic / algebraic

- dielectric Fresnel vs closed form over both interface orientations;
- normal-incidence reflectance;
- critical angle and TIR;
- smooth dielectric branch probabilities;
- smooth dielectric importance white-furnace throughput;
- radiance-transport eta Jacobian;
- rough dielectric `Sample_f` / evaluator / PDF consistency with precision-aware bounds;
- rough dielectric reflection reciprocity;
- passive-material energy upper bounds;
- diffuse cosine-sampling estimator identity;
- directed floating-point boundary tests used by PBRT ray robustness.

### B. Existing PBRT statistical regressions

- relevant `BSDFSampling.*` tests;
- relevant `BSDFEnergyConservation.*` tests.

These are necessary but are not substitutes for independent analytic gates.

### C. End-to-end rendered transport canaries

The exact executable must exercise parser -> material/media construction -> visibility -> lighting -> integrator -> film and pass at least:

1. Lambertian sphere under uniform environment: `L_o/L_env = rho` within sampling/film tolerance;
2. rough dielectric + conductor passive furnace finiteness/boundedness;
3. `path` vs `volpath` vacuum differential;
4. homogeneous absorption against Beer-Lambert attenuation;
5. vacuum specular/caustic smoke through `path` and `bdpt`.

Canaries use linear EXR/PFM measurements. Denoising, display tonemapping and sample clamping do not participate.

### D. Voxel-world canaries

Before promotion from RC to project reference release:

- at least four representative voxel levels are independently surface-extracted;
- at least two cameras per level are rendered;
- a minimum 32 spp smoke set completes without NaN/Inf, broken geometry, or material-boundary errors;
- representative views receive higher-spp and/or multi-seed convergence renders;
- source world, adapter, material, scene, environment, and binary hashes are recorded;
- transmissive/medium boundary winding and manifold diagnostics pass.

**32 spp is a bring-up/canary dataset, not statistical ground truth.**

## 5. Precision policy

The f64 lane is a meta-oracle for numerical conditioning, not a replacement for the f32 production build.

For identities that should be algebraically exact, f64 should converge substantially tighter than f32. Sample/evaluator comparisons that reconstruct a microfacet normal from a rounded f32 output direction may use a conditioning-aware bound only when:

- the corresponding f64 check is substantially tighter;
- PDF consistency remains independently strict;
- reciprocity, finiteness and passive-energy gates remain strict;
- the numerical mechanism and observed bound are documented.

## 6. Reproducibility / provenance

Every reference render or released binary records at least:

- repository and exact commit SHA;
- recursive submodule SHAs;
- compiler identity/version;
- build type and CMake options;
- f32/f64 mode;
- binary SHA-256 hashes;
- OS/architecture;
- scene/include SHA-256;
- environment map SHA-256;
- integrator and non-default parameters;
- sampler, seed and spp;
- maximum depth and other termination controls;
- film resolution/output format;
- oracle adapter version/hash;
- source voxel-world fingerprint/hash where available;
- geometry validation/orientation diagnostics.

Reference artifacts are immutable and addressed by hashes/SHA, not by a mutable branch name.

## 7. Release states

### Qualification branch

Development state. Individual gates may fail while a defect is isolated.

### Release candidate (RC)

Requires A + B + C green on the **same exact SHA**, a sealed f32 binary manifest, and no known correctness blocker in the qualified surface. Voxel-world gate D may remain incomplete, but that limitation must be stated.

### GI Oracle Qualified

Requires RC gates plus D, reviewed provenance manifests, and no unresolved high-severity issue affecting the qualified surface.

A known issue outside the qualified surface does not block release. A known issue inside it does.

## 8. Upstream issue classification relevant to this fork

### mmp/pbrt-v4#479 — roughness remapping

Upstream acknowledges that `alpha = roughness^2` is the intended perceptually linear convention but retains the old default mapping to avoid changing existing scene appearance. For this oracle that is an **adapter semantic**, not a transport-source blocker: generated reference scenes pass canonical alpha directly with `remaproughness=false`.

### mmp/pbrt-v4#547 — apparently over-reflective dielectric cube

The posted minimal cube has inward-facing triangle winding. With an external camera this reverses the apparent side of the dielectric interface, so sufficiently oblique rays are evaluated as glass->air and correctly undergo TIR above the critical angle. The release consequence is a strict outward-winding/interface-orientation invariant in the adapter, not a compensating Fresnel hack in PBRT.

### BDPT/MLT participating-media reports

Open upstream disagreement/convergence reports are the reason BDPT participating-media and MLT results are excluded from reference status. `volpath` remains the qualified participating-media backend.

## 9. Operational recommendation

Invoke PBRT as a hermetic process behind a renderer-independent canonical `OracleScene` adapter rather than deeply linking application transport code into PBRT. This keeps geometry/material/environment normalization explicit, reduces correlated DUT/oracle bugs, and permits independent differential renderers later.
