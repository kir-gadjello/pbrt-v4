#!/usr/bin/env python3
"""Rendered analytic canaries for PBRT's camera-conditioned irradiance integrator.

These tests exercise parser -> camera hit -> irradiance sensor MIS -> path transport ->
film.  They intentionally use closed-form irradiance identities instead of image
goldens.  EXR RGB values are linear spectral-to-output-RGB projections of irradiance;
display conversion is never used for acceptance.
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
import time
from typing import Dict, List, Sequence, Tuple


class CanaryFailure(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: Sequence[str], cwd: Path, log: Path) -> None:
    t0 = time.monotonic()
    with log.open("w", encoding="utf-8") as out:
        out.write("command=" + " ".join(cmd) + "\n")
        out.flush()
        p = subprocess.run(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT)
        out.write(f"\nexit={p.returncode}\nelapsed_s={time.monotonic()-t0:.6f}\n")
    if p.returncode:
        raise CanaryFailure(f"command failed ({p.returncode}); see {log}")


def capture(cmd: Sequence[str], cwd: Path) -> str:
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True)
    if p.returncode:
        raise CanaryFailure(f"command failed ({p.returncode}): {' '.join(cmd)}\n{p.stdout}")
    return p.stdout


def read_pfm(path: Path) -> Tuple[int, int, List[Tuple[float, float, float]]]:
    with path.open("rb") as f:
        magic = f.readline().strip()
        if magic not in (b"PF", b"Pf"):
            raise CanaryFailure(f"{path}: bad PFM magic {magic!r}")
        w, h = map(int, f.readline().split())
        scale = float(f.readline().strip())
        if scale == 0:
            raise CanaryFailure(f"{path}: zero PFM scale")
        channels = 3 if magic == b"PF" else 1
        raw = f.read()
        expected = w * h * channels * 4
        if len(raw) != expected:
            raise CanaryFailure(f"{path}: expected {expected} bytes, got {len(raw)}")
        vals = struct.unpack(("<" if scale < 0 else ">") + f"{w*h*channels}f", raw)
    if channels == 3:
        px = [(vals[i], vals[i+1], vals[i+2]) for i in range(0, len(vals), 3)]
    else:
        px = [(v, v, v) for v in vals]
    return w, h, px


def luminance(rgb: Tuple[float, float, float]) -> float:
    return .2126 * rgb[0] + .7152 * rgb[1] + .0722 * rgb[2]


def mean_luminance(image, inset: int = 0) -> float:
    w, h, px = image
    vals = []
    for y in range(inset, h - inset):
        for x in range(inset, w - inset):
            vals.append(luminance(px[y*w+x]))
    if not vals:
        raise CanaryFailure("empty measurement ROI")
    if not all(math.isfinite(v) for v in vals):
        raise CanaryFailure("non-finite irradiance sample")
    return sum(vals) / len(vals)


def relerr(a: float, b: float) -> float:
    return abs(a - b) / max(abs(b), 1e-30)


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise CanaryFailure(msg)


def require_rel(name: str, got: float, expected: float, tol: float) -> None:
    e = relerr(got, expected)
    if e > tol:
        raise CanaryFailure(
            f"{name}: got {got:.9g}, expected {expected:.9g}, relerr {e:.6g} > {tol}")


def header(name: str, component: str, spp: int, res=(16, 16), maxdepth=6,
           eye=(0, 0, 4), target=(0, 0, 0), fov=24, film="rgb") -> str:
    w, h = res
    film_line = f'''Film "{film}" "integer xresolution" [{w}] "integer yresolution" [{h}]
    "string filename" ["{name}.exr"] "bool savefp16" [false]'''
    if film == "gbuffer":
        film_line += '\n    "string coordinatesystem" ["world"]'
    return f'''LookAt {eye[0]} {eye[1]} {eye[2]}
       {target[0]} {target[1]} {target[2]}
       0 1 0
Camera "perspective" "float fov" [{fov}]
{film_line}
Sampler "halton" "integer pixelsamples" [{spp}] "integer seed" [47]
Integrator "irradiance" "integer maxdepth" [{maxdepth}] "string component" ["{component}"]
WorldBegin
'''


def constant_env(name: str, component: str, spp=256, underside=False,
                 maxdepth=6, film="rgb") -> str:
    if underside:
        h = header(name, component, spp, res=(1, 1), maxdepth=maxdepth,
                   eye=(4, 0, -1), target=(0, 0, 0), fov=.08, film=film)
        shape = 'Material "diffuse" "rgb reflectance" [.37 .37 .37]\nShape "disk" "float radius" [3]\n'
    else:
        h = header(name, component, spp, res=(16, 16), maxdepth=maxdepth,
                   eye=(0, 0, 4), target=(0, 0, 0), fov=24, film=film)
        shape = 'Material "diffuse" "rgb reflectance" [.37 .37 .37]\nShape "sphere" "float radius" [1]\n'
    return h + 'LightSource "infinite" "rgb L" [1 1 1]\n' + shape


def disk_light(name: str, component: str, spp=1024) -> str:
    # Sensor center at origin with normal +Z.  A radius-1 uniform radiance disk is
    # centered h=2 along the normal.  Exact center irradiance is
    # E = pi L R^2/(R^2+h^2) = pi/5 for L=R=1,h=2.
    return header(name, component, spp, res=(1, 1), maxdepth=6,
                  eye=(4, 0, 1), target=(0, 0, 0), fov=.06) + '''
Material "diffuse" "rgb reflectance" [.4 .4 .4]
Shape "disk" "float radius" [3]
AttributeBegin
    Translate 0 0 2
    AreaLightSource "diffuse" "rgb L" [1 1 1] "bool twosided" [true]
    Shape "disk" "float radius" [1]
AttributeEnd
'''


def bounce_scene(name: str, component: str, spp=1024) -> str:
    # A diffuse sphere hides a significant solid angle of a uniform environment
    # from the sensor but returns indirect light.  Total must therefore exceed
    # direct by a comfortable margin while remaining below the empty-hemisphere pi.
    return header(name, component, spp, res=(1, 1), maxdepth=6,
                  eye=(4, 0, 1), target=(0, 0, 0), fov=.06) + '''
LightSource "infinite" "rgb L" [1 1 1]
Material "diffuse" "rgb reflectance" [.35 .35 .35]
Shape "disk" "float radius" [4]
AttributeBegin
    Translate 0 0 2.2
    Material "diffuse" "rgb reflectance" [.8 .8 .8]
    Shape "sphere" "float radius" [1.1]
AttributeEnd
'''


def render(pbrt: Path, imgtool: Path, out: Path, name: str, scene: str):
    sp = out / f"{name}.pbrt"
    sp.write_text(scene, encoding="utf-8")
    run([str(pbrt), "--quiet", sp.name], out, out / f"{name}.render.log")
    exr = out / f"{name}.exr"
    require(exr.is_file() and exr.stat().st_size > 0, f"{name}: missing EXR")
    pfm = out / f"{name}.pfm"
    run([str(imgtool), "convert", exr.name, "--outfile", pfm.name], out,
        out / f"{name}.convert.log")
    img = read_pfm(pfm)
    vals = [v for rgb in img[2] for v in rgb]
    require(all(math.isfinite(v) for v in vals), f"{name}: non-finite RGB")
    require(min(vals) > -1e-4, f"{name}: materially negative channel {min(vals)}")
    return {
        "scene_sha256": sha256_file(sp), "exr_sha256": sha256_file(exr),
        "pfm_sha256": sha256_file(pfm), "image": img,
        "mean_luminance": mean_luminance(img),
        "min_channel": min(vals), "max_channel": max(vals),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbrt", required=True, type=Path)
    ap.add_argument("--imgtool", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--commit", default=os.environ.get("GITHUB_SHA", "unknown"))
    args = ap.parse_args()
    pbrt, imgtool, out = args.pbrt.resolve(), args.imgtool.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    require(pbrt.is_file(), f"missing pbrt: {pbrt}")
    require(imgtool.is_file(), f"missing imgtool: {imgtool}")

    report: Dict[str, object] = {
        "schema": 1, "commit": args.commit,
        "pbrt_sha256": sha256_file(pbrt), "imgtool_sha256": sha256_file(imgtool),
        "definition": "camera-visible geometric-side hemispherical irradiance",
        "units": "linear spectral-to-output-RGB projection of irradiance",
        "canaries": {},
    }

    # 1. Constant radiance environment: E = pi L on every surface orientation.
    env_t = render(pbrt, imgtool, out, "env_total", constant_env("env_total", "total"))
    env_d = render(pbrt, imgtool, out, "env_direct", constant_env("env_direct", "direct"))
    Et = mean_luminance(env_t["image"], inset=5)
    Ed = mean_luminance(env_d["image"], inset=5)
    require_rel("constant environment total", Et, math.pi, .018)
    require_rel("constant environment direct", Ed, math.pi, .018)
    require_rel("constant environment total/direct", Et, Ed, .008)
    report["canaries"]["constant_environment"] = {
        "total": Et, "direct": Ed, "expected": math.pi,
        "total_relerr": relerr(Et, math.pi), "direct_relerr": relerr(Ed, math.pi),
        "status": "pass",
    }

    # 2. Same identity from the geometric underside validates camera-facing side choice.
    under = render(pbrt, imgtool, out, "env_underside",
                   constant_env("env_underside", "total", spp=512, underside=True))
    Eu = under["mean_luminance"]
    require_rel("underside face-forward environment", Eu, math.pi, .022)
    report["canaries"]["camera_facing_side"] = {
        "measured": Eu, "expected": math.pi,
        "relative_error": relerr(Eu, math.pi), "status": "pass",
    }

    # 3. Closed-form finite disk irradiance.
    disk = render(pbrt, imgtool, out, "disk_direct", disk_light("disk_direct", "direct"))
    E_disk = disk["mean_luminance"]
    exact_disk = math.pi / 5.0
    require_rel("finite disk irradiance", E_disk, exact_disk, .025)
    report["canaries"]["finite_disk"] = {
        "measured": E_disk, "expected": exact_disk,
        "relative_error": relerr(E_disk, exact_disk), "status": "pass",
    }

    # 4. maxdepth=0 total must reduce to direct transport in an unoccluded env case.
    env0 = render(pbrt, imgtool, out, "env_depth0",
                  constant_env("env_depth0", "total", spp=256, maxdepth=0))
    E0 = mean_luminance(env0["image"], inset=5)
    require_rel("depth-zero/direct equivalence", E0, Ed, .008)
    report["canaries"]["depth_zero_direct_equivalence"] = {
        "depth_zero": E0, "direct": Ed, "relative_error": relerr(E0, Ed),
        "status": "pass",
    }

    # 5. Indirect transport must add energy behind a nonemissive diffuse blocker.
    bounce_d = render(pbrt, imgtool, out, "bounce_direct",
                      bounce_scene("bounce_direct", "direct", spp=1024))
    bounce_t = render(pbrt, imgtool, out, "bounce_total",
                      bounce_scene("bounce_total", "total", spp=1024))
    Bd, Bt = bounce_d["mean_luminance"], bounce_t["mean_luminance"]
    require(Bd > 0, "bounce direct unexpectedly black")
    require(Bt > Bd * 1.08,
            f"indirect continuation too small: total={Bt:.6g}, direct={Bd:.6g}")
    require(Bt < math.pi * 1.08,
            f"passive bounce scene created implausible energy: {Bt:.6g}")
    report["canaries"]["indirect_positive"] = {
        "direct": Bd, "total": Bt, "indirect": Bt - Bd,
        "total_over_direct": Bt / Bd, "status": "pass",
    }

    # 6. GBuffer is the alignment surface used by DUT comparison: require world-space
    # position and geometric/shading normals to coexist with irradiance RGB.
    gb = render(pbrt, imgtool, out, "gbuffer_world",
                constant_env("gbuffer_world", "total", spp=32, film="gbuffer"))
    info = capture([str(imgtool), "info", "gbuffer_world.exr"], out)
    (out / "gbuffer_world.info.txt").write_text(info, encoding="utf-8")
    for channel in ("P.X", "P.Y", "P.Z", "N.X", "N.Y", "N.Z", "Ns.X", "R", "G", "B"):
        require(channel in info, f"GBuffer info missing channel {channel}")
    report["canaries"]["gbuffer_alignment"] = {
        "exr_sha256": gb["exr_sha256"], "required_channels":
            ["R", "G", "B", "P.X", "P.Y", "P.Z", "N.X", "N.Y", "N.Z", "Ns.X"],
        "status": "pass",
    }

    report_path = out / "irradiance-canaries.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CanaryFailure as e:
        print(f"IRRADIANCE CANARY FAILURE: {e}", file=os.sys.stderr)
        raise SystemExit(2)
