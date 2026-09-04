#!/usr/bin/env python3
"""End-to-end CPU transport canaries for the GI-oracle PBRT fork.

No golden image is treated as truth. The scenes exercise parser -> material/media
construction -> visibility -> transport -> film and are checked against analytic
relationships or conservative invariants. All rough microfacet parameters below
are canonical GGX alpha values and therefore set remaproughness=false.
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


class CanaryFailure(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd, cwd: Path, log: Path) -> None:
    start = time.monotonic()
    with log.open("w", encoding="utf-8") as out:
        out.write("command=" + " ".join(cmd) + "\n")
        out.flush()
        proc = subprocess.run(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT)
        out.write(f"\nexit={proc.returncode}\nelapsed_s={time.monotonic()-start:.6f}\n")
    if proc.returncode:
        raise CanaryFailure(f"command failed ({proc.returncode}); see {log}")


def read_pfm(path: Path):
    with path.open("rb") as f:
        magic = f.readline().strip()
        if magic not in (b"PF", b"Pf"):
            raise CanaryFailure(f"{path}: invalid PFM magic {magic!r}")
        width, height = map(int, f.readline().split())
        scale = float(f.readline().strip())
        if scale == 0:
            raise CanaryFailure(f"{path}: zero PFM scale")
        channels = 3 if magic == b"PF" else 1
        raw = f.read()
    expected = width * height * channels * 4
    if len(raw) != expected:
        raise CanaryFailure(f"{path}: expected {expected} data bytes, got {len(raw)}")
    values = struct.unpack(("<" if scale < 0 else ">") + f"{width*height*channels}f", raw)
    if channels == 3:
        pixels = [(values[i], values[i+1], values[i+2]) for i in range(0, len(values), 3)]
    else:
        pixels = [(v, v, v) for v in values]
    return width, height, pixels


def luminance(rgb) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def image_stats(image):
    w, h, pixels = image
    channels = [v for rgb in pixels for v in rgb]
    if not all(math.isfinite(v) for v in channels):
        raise CanaryFailure("image contains non-finite channel values")
    ys = [luminance(p) for p in pixels]
    return {
        "width": w, "height": h,
        "min_channel": min(channels), "max_channel": max(channels),
        "mean_luminance": sum(ys) / len(ys),
        "min_luminance": min(ys), "max_luminance": max(ys),
    }


def roi_mean(image, x0: int, x1: int, y0: int, y1: int) -> float:
    w, h, pixels = image
    if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
        raise CanaryFailure(f"invalid ROI {(x0,x1,y0,y1)} for {w}x{h}")
    vals = [luminance(pixels[y*w+x]) for y in range(y0, y1) for x in range(x0, x1)]
    return sum(vals) / len(vals)


def rel_error(got: float, expected: float) -> float:
    return abs(got - expected) / max(abs(expected), 1e-30)


def require(cond: bool, message: str) -> None:
    if not cond:
        raise CanaryFailure(message)


def require_rel(name: str, got: float, expected: float, tol: float) -> None:
    e = rel_error(got, expected)
    if e > tol:
        raise CanaryFailure(f"{name}: got={got:.9g} expected={expected:.9g} relerr={e:.6g} > {tol}")


def header(name: str, integrator: str, spp: int, resolution, fov: float,
           eye, target, maxdepth: int) -> str:
    w, h = resolution
    return f'''LookAt {eye[0]} {eye[1]} {eye[2]}
       {target[0]} {target[1]} {target[2]}
       0 1 0
Camera "perspective" "float fov" [{fov}]
Film "rgb"
    "integer xresolution" [{w}]
    "integer yresolution" [{h}]
    "string filename" ["{name}.exr"]
Sampler "halton" "integer pixelsamples" [{spp}]
Integrator "{integrator}" "integer maxdepth" [{maxdepth}]
WorldBegin
'''


def diffuse_scene(name: str, rho: float, integrator: str = "path") -> str:
    return header(name, integrator, 256, (32, 32), 30, (0, 0, 4), (0, 0, 0), 4) + f'''
LightSource "infinite" "rgb L" [1 1 1]
Material "diffuse" "rgb reflectance" [{rho} {rho} {rho}]
Shape "sphere" "float radius" [1]
'''


def passive_scene(name: str) -> str:
    glass_alpha = 0.42 * 0.42
    silver_alpha = 0.32 * 0.32
    return header(name, "volpath", 128, (48, 32), 38, (0, .15, 6.2), (0, 0, 0), 8) + f'''
LightSource "infinite" "rgb L" [1 1 1]
AttributeBegin
    Translate -1.15 0 0
    Material "dielectric" "float eta" [1.5]
        "float roughness" [{glass_alpha}] "bool remaproughness" [false]
    Shape "sphere" "float radius" [1]
AttributeEnd
AttributeBegin
    Translate 1.15 0 0
    Material "conductor"
        "spectrum eta" ["metal-Ag-eta"] "spectrum k" ["metal-Ag-k"]
        "float roughness" [{silver_alpha}] "bool remaproughness" [false]
    Shape "sphere" "float radius" [1]
AttributeEnd
'''


def medium_scene(name: str, sigma_a: float) -> str:
    s = header(name, "volpath", 16, (16, 16), 1.0, (0, 0, 4), (0, 0, 0), 8)
    s += 'LightSource "infinite" "rgb L" [1 1 1]\n'
    if sigma_a:
        s += f'''MakeNamedMedium "absorb" "string type" "homogeneous"
    "spectrum sigma_a" [300 {sigma_a} 830 {sigma_a}]
    "spectrum sigma_s" [300 0 830 0]
AttributeBegin
    Material "interface"
    MediumInterface "absorb" ""
    Shape "sphere" "float radius" [1]
AttributeEnd
'''
    return s


def caustic_scene(name: str, integrator: str) -> str:
    alpha = 0.08 * 0.08
    return f'''LookAt 5 -7 4
       0 0 0.8
       0 0 1
Camera "perspective" "float fov" [42]
Film "rgb" "integer xresolution" [48] "integer yresolution" [32]
    "string filename" ["{name}.exr"]
Sampler "halton" "integer pixelsamples" [128]
Integrator "{integrator}" "integer maxdepth" [8]
WorldBegin
Material "diffuse" "rgb reflectance" [.65 .65 .65]
Shape "disk" "float radius" [6]
AttributeBegin
    Translate 0 0 1
    Material "dielectric" "float eta" [1.5]
        "float roughness" [{alpha}] "bool remaproughness" [false]
    Shape "sphere" "float radius" [1]
AttributeEnd
AttributeBegin
    Translate -2 -1 4
    AreaLightSource "diffuse" "rgb L" [18 18 18]
    Shape "sphere" "float radius" [.35]
AttributeEnd
'''


def render(pbrt: Path, imgtool: Path, out: Path, name: str, text: str):
    scene = out / f"{name}.pbrt"
    scene.write_text(text, encoding="utf-8")
    run([str(pbrt), scene.name], out, out / f"{name}.render.log")
    exr = out / f"{name}.exr"
    require(exr.exists() and exr.stat().st_size > 0, f"{name}: no EXR")
    pfm = out / f"{name}.pfm"
    run([str(imgtool), "convert", exr.name, "--outfile", pfm.name], out, out / f"{name}.convert.log")
    image = read_pfm(pfm)
    return {
        "scene_sha256": sha256_file(scene), "exr_sha256": sha256_file(exr),
        "pfm_sha256": sha256_file(pfm), "image": image, "stats": image_stats(image),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbrt", type=Path, required=True)
    ap.add_argument("--imgtool", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--commit", default=os.environ.get("GITHUB_SHA", "unknown"))
    a = ap.parse_args()
    pbrt, imgtool, out = a.pbrt.resolve(), a.imgtool.resolve(), a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    require(pbrt.is_file(), f"missing {pbrt}")
    require(imgtool.is_file(), f"missing {imgtool}")
    report = {
        "schema": 2, "commit": a.commit,
        "pbrt_sha256": sha256_file(pbrt), "imgtool_sha256": sha256_file(imgtool),
        "roughness_contract": "engine perceptual r -> GGX alpha=r^2; PBRT remaproughness=false",
        "canaries": {}, "failures": [],
    }
    try:
        d20 = render(pbrt, imgtool, out, "diffuse_r020_path", diffuse_scene("diffuse_r020_path", .2))
        d60 = render(pbrt, imgtool, out, "diffuse_r060_path", diffuse_scene("diffuse_r060_path", .6))
        c20, c60 = roi_mean(d20["image"], 12, 20, 12, 20), roi_mean(d60["image"], 12, 20, 12, 20)
        e20, e60 = roi_mean(d20["image"], 0, 4, 0, 4), roi_mean(d60["image"], 0, 4, 0, 4)
        require_rel("Lambert rho=.2", c20/e20, .2, .025)
        require_rel("Lambert rho=.6", c60/e60, .6, .025)
        require_rel("Lambert proportionality", c60/c20, 3.0, .012)
        report["canaries"]["diffuse_uniform_environment"] = {
            "rho020_over_env": c20/e20, "rho060_over_env": c60/e60,
            "rho060_over_rho020": c60/c20, "status": "pass"}

        vp = render(pbrt, imgtool, out, "diffuse_r050_path", diffuse_scene("diffuse_r050_path", .5, "path"))
        vv = render(pbrt, imgtool, out, "diffuse_r050_volpath", diffuse_scene("diffuse_r050_volpath", .5, "volpath"))
        lp, lv = roi_mean(vp["image"], 12, 20, 12, 20), roi_mean(vv["image"], 12, 20, 12, 20)
        require_rel("path vs volpath vacuum", lv, lp, .015)
        report["canaries"]["path_volpath_vacuum"] = {
            "path": lp, "volpath": lv, "relative_error": rel_error(lv, lp), "status": "pass"}

        passive = render(pbrt, imgtool, out, "passive_materials", passive_scene("passive_materials"))
        ps = passive["stats"]
        require(ps["mean_luminance"] > 0, "passive materials black")
        require(ps["min_channel"] > -1e-5, f"passive material negative channel {ps['min_channel']}")
        require(ps["max_channel"] < 100, f"passive material explosive output {ps['max_channel']}")
        report["canaries"]["passive_material_finiteness"] = {**ps, "status": "pass"}

        m0 = render(pbrt, imgtool, out, "medium_vacuum", medium_scene("medium_vacuum", 0))
        m5 = render(pbrt, imgtool, out, "medium_sigma050", medium_scene("medium_sigma050", .5))
        measured = m5["stats"]["mean_luminance"] / m0["stats"]["mean_luminance"]
        expected = math.exp(-1.0)
        require_rel("Beer-Lambert two-unit chord", measured, expected, .012)
        report["canaries"]["beer_lambert"] = {
            "measured_transmittance": measured, "expected_transmittance": expected,
            "relative_error": rel_error(measured, expected), "status": "pass"}

        cp = render(pbrt, imgtool, out, "caustic_path", caustic_scene("caustic_path", "path"))
        cb = render(pbrt, imgtool, out, "caustic_bdpt", caustic_scene("caustic_bdpt", "bdpt"))
        for label, result in (("path", cp), ("bdpt", cb)):
            st = result["stats"]
            require(st["mean_luminance"] > 1e-8, f"caustic {label} black")
            require(st["min_channel"] > -1e-4, f"caustic {label} negative")
            require(st["max_channel"] < 1e5, f"caustic {label} explosive")
        report["canaries"]["caustic_vacuum_smoke"] = {
            "path": cp["stats"], "bdpt": cb["stats"], "status": "pass"}
    except CanaryFailure as e:
        report["failures"].append(str(e))

    report["status"] = "pass" if not report["failures"] else "fail"
    (out / "canary-report.json").write_text(json.dumps(report, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    if report["failures"]:
        for f in report["failures"]:
            print("CANARY FAILURE:", f, file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
