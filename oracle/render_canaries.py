#!/usr/bin/env python3
"""End-to-end CPU transport canaries for the GI-oracle PBRT fork.

These tests intentionally avoid golden images.  They exercise the complete renderer
(parser -> materials/media -> transport -> film) and check analytic relationships,
finite/bounded output, and differential agreement between integrators.

The generated EXR files remain linear measurement artifacts.  PFM conversion is used
only so this script can inspect pixels with the Python standard library.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
from typing import Dict, Iterable, List, Sequence, Tuple


class CanaryFailure(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: Sequence[str], cwd: Path, log: Path) -> None:
    start = time.monotonic()
    with log.open("w", encoding="utf-8") as out:
        out.write("command=" + " ".join(cmd) + "\n")
        out.flush()
        proc = subprocess.run(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT)
        elapsed = time.monotonic() - start
        out.write(f"\nexit={proc.returncode}\nelapsed_s={elapsed:.6f}\n")
    if proc.returncode != 0:
        raise CanaryFailure(f"command failed ({proc.returncode}); see {log}")


def read_pfm(path: Path) -> Tuple[int, int, List[Tuple[float, float, float]]]:
    with path.open("rb") as f:
        magic = f.readline().strip()
        if magic not in (b"PF", b"Pf"):
            raise CanaryFailure(f"{path}: invalid PFM magic {magic!r}")
        dims = f.readline().split()
        if len(dims) != 2:
            raise CanaryFailure(f"{path}: invalid PFM dimensions")
        width, height = map(int, dims)
        scale = float(f.readline().strip())
        if scale == 0:
            raise CanaryFailure(f"{path}: zero PFM scale")
        little = scale < 0
        channels = 3 if magic == b"PF" else 1
        raw = f.read()
        expected = width * height * channels * 4
        if len(raw) != expected:
            raise CanaryFailure(
                f"{path}: expected {expected} PFM data bytes, got {len(raw)}"
            )
        vals = struct.unpack(("<" if little else ">") + f"{width * height * channels}f", raw)

    pixels: List[Tuple[float, float, float]] = []
    if channels == 3:
        pixels = [(vals[i], vals[i + 1], vals[i + 2]) for i in range(0, len(vals), 3)]
    else:
        pixels = [(v, v, v) for v in vals]
    # PFM scanline orientation is irrelevant for the symmetric ROIs below.  Keep the
    # file order intact so this parser remains a minimal measurement dependency.
    return width, height, pixels


def luminance(rgb: Tuple[float, float, float]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def image_stats(image: Tuple[int, int, List[Tuple[float, float, float]]]) -> Dict[str, float]:
    w, h, pixels = image
    flat = [v for rgb in pixels for v in rgb]
    if not all(math.isfinite(v) for v in flat):
        bad = sum(not math.isfinite(v) for v in flat)
        raise CanaryFailure(f"image contains {bad} non-finite channel values")
    ys = [luminance(p) for p in pixels]
    return {
        "width": w,
        "height": h,
        "min_channel": min(flat),
        "max_channel": max(flat),
        "mean_luminance": sum(ys) / len(ys),
        "min_luminance": min(ys),
        "max_luminance": max(ys),
    }


def roi_mean_luminance(
    image: Tuple[int, int, List[Tuple[float, float, float]]],
    x0: int,
    x1: int,
    y0: int,
    y1: int,
) -> float:
    w, h, pixels = image
    if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
        raise CanaryFailure(f"invalid ROI {(x0, x1, y0, y1)} for {w}x{h}")
    s = 0.0
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            s += luminance(pixels[y * w + x])
            n += 1
    return s / n


def rel_error(got: float, expected: float) -> float:
    return abs(got - expected) / max(abs(expected), 1e-30)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CanaryFailure(message)


def require_rel(name: str, got: float, expected: float, tol: float) -> None:
    e = rel_error(got, expected)
    if e > tol:
        raise CanaryFailure(
            f"{name}: got {got:.9g}, expected {expected:.9g}, relative error {e:.6g} > {tol}"
        )


def header(name: str, integrator: str, spp: int, resolution: Tuple[int, int], fov: float,
           eye: Tuple[float, float, float], target: Tuple[float, float, float], maxdepth: int) -> str:
    w, h = resolution
    return f'''LookAt {eye[0]} {eye[1]} {eye[2]}
       {target[0]} {target[1]} {target[2]}
       0 1 0
Camera "perspective" "float fov" [{fov}]
Film "rgb"
    "integer xresolution" [{w}]
    "integer yresolution" [{h}]
    "string filename" ["{name}.exr"]
Sampler "halton"
    "integer pixelsamples" [{spp}]
    "integer seed" [17]
Integrator "{integrator}"
    "integer maxdepth" [{maxdepth}]
WorldBegin
'''


def uniform_diffuse_scene(name: str, rho: float, integrator: str = "path") -> str:
    return header(name, integrator, 256, (32, 32), 30, (0, 0, 4), (0, 0, 0), 4) + f'''
LightSource "infinite" "rgb L" [1 1 1]
Material "diffuse" "rgb reflectance" [{rho} {rho} {rho}]
Shape "sphere" "float radius" [1]
WorldEnd
'''


def passive_material_scene(name: str) -> str:
    return header(name, "volpath", 128, (48, 32), 38, (0, 0.15, 6.2), (0, 0, 0), 8) + '''
LightSource "infinite" "rgb L" [1 1 1]
AttributeBegin
    Translate -1.15 0 0
    Material "dielectric"
        "float eta" [1.5]
        "float roughness" [0.42]
    Shape "sphere" "float radius" [1]
AttributeEnd
AttributeBegin
    Translate 1.15 0 0
    Material "conductor"
        "spectrum eta" ["metal-Ag-eta"]
        "spectrum k" ["metal-Ag-k"]
        "float roughness" [0.32]
    Shape "sphere" "float radius" [1]
AttributeEnd
WorldEnd
'''


def medium_scene(name: str, sigma_a: float) -> str:
    # A one-degree field of view keeps every ray very near the sphere diameter, so
    # the image-average Beer-Lambert expectation is effectively exp(-2*sigma_a).
    s = header(name, "volpath", 16, (16, 16), 1.0, (0, 0, 4), (0, 0, 0), 8)
    s += 'LightSource "infinite" "rgb L" [1 1 1]\n'
    if sigma_a > 0:
        s += f'''MakeNamedMedium "absorb" "string type" "homogeneous"
    "spectrum sigma_a" [300 {sigma_a} 830 {sigma_a}]
    "spectrum sigma_s" [300 0 830 0]
AttributeBegin
    Material "interface"
    MediumInterface "" "absorb"
    Shape "sphere" "float radius" [1]
AttributeEnd
'''
    s += 'WorldEnd\n'
    return s


def caustic_scene(name: str, integrator: str) -> str:
    # z is up for the scene below, so use an explicit camera up-vector rather than
    # the common y-up helper above.
    return f'''LookAt 5 -7 4
       0 0 0.8
       0 0 1
Camera "perspective" "float fov" [42]
Film "rgb"
    "integer xresolution" [48]
    "integer yresolution" [32]
    "string filename" ["{name}.exr"]
Sampler "halton" "integer pixelsamples" [128] "integer seed" [23]
Integrator "{integrator}" "integer maxdepth" [8]
WorldBegin
AttributeBegin
    Material "diffuse" "rgb reflectance" [0.65 0.65 0.65]
    Shape "disk" "float radius" [6]
AttributeEnd
AttributeBegin
    Translate 0 0 1
    Material "dielectric" "float eta" [1.5] "float roughness" [0.08]
    Shape "sphere" "float radius" [1]
AttributeEnd
AttributeBegin
    Translate -2 -1 4
    AreaLightSource "diffuse" "rgb L" [18 18 18]
    Shape "sphere" "float radius" [0.35]
AttributeEnd
WorldEnd
'''


def render(pbrt: Path, imgtool: Path, out: Path, name: str, scene_text: str) -> Dict[str, object]:
    scene = out / f"{name}.pbrt"
    scene.write_text(scene_text, encoding="utf-8")
    run([str(pbrt), scene.name], cwd=out, log=out / f"{name}.render.log")
    exr = out / f"{name}.exr"
    require(exr.exists() and exr.stat().st_size > 0, f"{name}: renderer did not produce EXR")
    pfm = out / f"{name}.pfm"
    run(
        [str(imgtool), "convert", exr.name, "--outfile", pfm.name],
        cwd=out,
        log=out / f"{name}.convert.log",
    )
    image = read_pfm(pfm)
    stats = image_stats(image)
    return {
        "scene": scene,
        "scene_sha256": sha256_file(scene),
        "exr": exr,
        "exr_sha256": sha256_file(exr),
        "pfm": pfm,
        "pfm_sha256": sha256_file(pfm),
        "image": image,
        "stats": stats,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbrt", required=True, type=Path)
    ap.add_argument("--imgtool", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--commit", default=os.environ.get("GITHUB_SHA", "unknown"))
    args = ap.parse_args()

    pbrt = args.pbrt.resolve()
    imgtool = args.imgtool.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    require(pbrt.is_file(), f"missing renderer: {pbrt}")
    require(imgtool.is_file(), f"missing imgtool: {imgtool}")

    report: Dict[str, object] = {
        "schema": 1,
        "commit": args.commit,
        "pbrt_sha256": sha256_file(pbrt),
        "imgtool_sha256": sha256_file(imgtool),
        "canaries": {},
    }
    failures: List[str] = []

    try:
        # A. Uniform-environment Lambertian identity and proportionality.
        d20 = render(pbrt, imgtool, out, "diffuse_r020_path", uniform_diffuse_scene("diffuse_r020_path", 0.2))
        d60 = render(pbrt, imgtool, out, "diffuse_r060_path", uniform_diffuse_scene("diffuse_r060_path", 0.6))
        i20 = d20["image"]
        i60 = d60["image"]
        center20 = roi_mean_luminance(i20, 12, 20, 12, 20)
        center60 = roi_mean_luminance(i60, 12, 20, 12, 20)
        # Corner pixels see the uniform environment directly.
        env20 = roi_mean_luminance(i20, 0, 4, 0, 4)
        env60 = roi_mean_luminance(i60, 0, 4, 0, 4)
        require_rel("diffuse rho=.2 / environment", center20 / env20, 0.2, 0.025)
        require_rel("diffuse rho=.6 / environment", center60 / env60, 0.6, 0.025)
        require_rel("diffuse material proportionality", center60 / center20, 3.0, 0.012)
        report["canaries"]["diffuse_uniform_environment"] = {
            "rho_020_center": center20,
            "rho_020_environment": env20,
            "rho_060_center": center60,
            "rho_060_environment": env60,
            "ratio_060_over_020": center60 / center20,
            "status": "pass",
        }

        # B. In vacuum, path and volpath must solve the same transport equation.
        vpath = render(pbrt, imgtool, out, "diffuse_r050_path", uniform_diffuse_scene("diffuse_r050_path", 0.5, "path"))
        vvol = render(pbrt, imgtool, out, "diffuse_r050_volpath", uniform_diffuse_scene("diffuse_r050_volpath", 0.5, "volpath"))
        lp = roi_mean_luminance(vpath["image"], 12, 20, 12, 20)
        lv = roi_mean_luminance(vvol["image"], 12, 20, 12, 20)
        require_rel("path vs volpath vacuum", lv, lp, 0.015)
        report["canaries"]["path_volpath_vacuum"] = {
            "path_center": lp,
            "volpath_center": lv,
            "relative_error": rel_error(lv, lp),
            "status": "pass",
        }

        # C. Rough dielectric + rough conductor must stay finite under a uniform furnace.
        passive = render(pbrt, imgtool, out, "passive_materials", passive_material_scene("passive_materials"))
        ps = passive["stats"]
        require(ps["mean_luminance"] > 0, "passive material canary is black")
        require(ps["min_channel"] > -1e-5, f"passive material canary has materially negative channel {ps['min_channel']}")
        require(ps["max_channel"] < 100.0, f"passive material canary has implausible furnace amplification {ps['max_channel']}")
        report["canaries"]["passive_material_finiteness"] = {**ps, "status": "pass"}

        # D. Homogeneous absorption is checked against Beer-Lambert over an almost
        # exactly two-unit chord through an invisible unit-radius medium boundary.
        m0 = render(pbrt, imgtool, out, "medium_vacuum", medium_scene("medium_vacuum", 0.0))
        m5 = render(pbrt, imgtool, out, "medium_sigma050", medium_scene("medium_sigma050", 0.5))
        l0 = m0["stats"]["mean_luminance"]
        l5 = m5["stats"]["mean_luminance"]
        measured_t = l5 / l0
        expected_t = math.exp(-1.0)
        require_rel("Beer-Lambert sphere chord", measured_t, expected_t, 0.012)
        report["canaries"]["beer_lambert"] = {
            "vacuum_mean": l0,
            "absorbing_mean": l5,
            "measured_transmittance": measured_t,
            "expected_transmittance": expected_t,
            "relative_error": rel_error(measured_t, expected_t),
            "status": "pass",
        }

        # E. Exercise a specular-caustic configuration through both the primary path
        # family and the independent bidirectional transport implementation.  This is
        # deliberately a smoke/invariant gate, not a claim that 128 spp is ground truth.
        cp = render(pbrt, imgtool, out, "caustic_path", caustic_scene("caustic_path", "path"))
        cb = render(pbrt, imgtool, out, "caustic_bdpt", caustic_scene("caustic_bdpt", "bdpt"))
        for label, result in (("path", cp), ("bdpt", cb)):
            st = result["stats"]
            require(st["mean_luminance"] > 1e-8, f"caustic {label} image is black")
            require(st["min_channel"] > -1e-4, f"caustic {label} has materially negative output")
            require(st["max_channel"] < 1e5, f"caustic {label} has explosive output {st['max_channel']}")
        report["canaries"]["caustic_integrator_smoke"] = {
            "path": cp["stats"],
            "bdpt": cb["stats"],
            "status": "pass",
        }

    except CanaryFailure as e:
        failures.append(str(e))

    report["status"] = "pass" if not failures else "fail"
    report["failures"] = failures
    # Remove in-memory pixel arrays from the report; individual artifact hashes and
    # scalar measurements are the durable interface.
    (out / "canary-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if failures:
        for f in failures:
            print("CANARY FAILURE:", f, file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
