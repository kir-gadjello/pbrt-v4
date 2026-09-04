# Voxel-to-PBRT Oracle Adapter Invariants

Draft staging note. Do not treat this document alone as a qualification claim.

## Physical boundary orientation

For every closed material or medium component that has side-dependent transport semantics, the generated surface must have coherent **outward** winding.

This is not cosmetic. PBRT's dielectric and medium interface equations use the sign of the local direction relative to the geometric/shading normal to decide which side of an interface the ray is on. Reversed closed geometry can therefore turn a physically external air->glass ray into an apparent glass->air ray and legitimately trigger total internal reflection.

The adapter must therefore:

1. generate every exposed voxel face with a canonical outward normal;
2. preserve that orientation during coplanar face merging;
3. validate triangle orientation against the source occupancy transition;
4. for closed components, optionally compute signed volume as a secondary orientation diagnostic;
5. reject non-manifold or ambiguously open boundaries from volumetric reference status unless sidedness is explicitly authored;
6. distinguish closed solid dielectric from deliberately two-sided thin dielectric;
7. record every automatic orientation repair in scene provenance.

## Independence from the DUT

Reference geometry extraction must not call or reuse the real-time renderer's DDA, visibility cache, GI page graph, surface cache, or interpolation implementation. It may consume the same authoritative material-byte voxel field, but it must derive visible boundaries independently.

## Face ownership

At a voxel/material transition the adapter creates a boundary only when required by the canonical material model:

- opaque -> air: opaque surface;
- opaque A -> opaque B: normally no internal visibility boundary unless the canonical material specification explicitly requires one;
- closed dielectric -> non-dielectric: dielectric boundary, oriented from dielectric interior outward;
- homogeneous participating medium -> non-identical medium: medium boundary, with explicit inside/outside names;
- emissive material -> visible neighbor: emissive surface and its canonical scattering material, if any.

Transitions between transmissive materials require explicit IOR/medium policy; they must not be guessed from artist-facing IDs.

## Roughness

The adapter owns artist-parameter normalization. Canonical GGX alpha is stored in the reference scene. For the current engine-facing perceptual roughness convention:

`alpha = roughness^2`

Do not apply a second renderer-side perceptual remap after conversion.

## Geometry validation manifest

Each converted level/camera package must record:

- source generator + SHA-256;
- source world fingerprint/hash if provided by the generator;
- dimensions and voxel size;
- material table hash;
- non-air voxel count;
- generated quad/triangle count by material;
- boundary face count by orientation;
- count of dielectric/medium boundary faces;
- count of rejected ambiguous transitions;
- mesh signed-volume diagnostics for closed transmissive components when computed;
- PBRT scene/include hashes;
- adapter source hash.

A 32 spp render may be used as a geometry/material bring-up canary only after these structural checks pass.