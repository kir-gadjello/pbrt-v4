#!/usr/bin/env python3
"""Matched real-Mitsuba scenes for VPT RTC3 runtime qualification.

This intentionally uses Mitsuba's real `path` integrator in an RGB LLVM variant.
All outputs are linear radiance. No exposure fitting or tone mapping is permitted
for numeric qualification.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import struct

import numpy as np
import mitsuba as mi

OUT = Path(os.environ.get("MITSUBA_ORACLE_OUT", "mitsuba-oracle-output"))
SPP = int(os.environ.get("MITSUBA_ORACLE_SPP", "1024"))
SEED = int(os.environ.get("MITSUBA_ORACLE_SEED", "1369903142"))
W, H = 128, 96

# Exact effective Lambertian reflectance of the VPT DIFFUSE_ONLY fixture.
# VPT base=[.60,.45,.30], eta=1.5 -> F0=.04, Favg=F0+(1-F0)/21.
RHO = [0.5485714285714286, 0.4114285714285714, 0.2742857142857143]
ENV = [0.80, 1.00, 1.20]
GLASS_TINT = [0.84, 0.93, 0.98]
PANEL_LE = [7.0, 5.0, 3.0]


def write_pfm(path: Path, rgb: np.ndarray) -> None:
    a = np.asarray(rgb, dtype=np.float32)
    assert a.ndim == 3 and a.shape[2] >= 3
    a = a[:, :, :3]
    # PFM stores scanlines bottom-to-top for the conventional negative scale.
    with path.open("wb") as f:
        f.write(b"PF\n")
        f.write(f"{a.shape[1]} {a.shape[0]}\n".encode())
        f.write(b"-1.0\n")
        np.flipud(a).astype("<f4", copy=False).tofile(f)


def add_box(verts, faces, lo, hi):
    x0,y0,z0 = lo; x1,y1,z1 = hi
    base = len(verts)
    verts += [
        (x0,y0,z0),(x1,y0,z0),(x1,y1,z0),(x0,y1,z0),
        (x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1),
    ]
    q = [
        (0,3,2),(0,2,1),       # -z
        (4,5,6),(4,6,7),       # +z
        (0,4,7),(0,7,3),       # -x
        (1,2,6),(1,6,5),       # +x
        (0,1,5),(0,5,4),       # -y
        (3,7,6),(3,6,2),       # +y
    ]
    faces += [(base+a, base+b, base+c) for a,b,c in q]


def write_obj(path: Path, boxes) -> None:
    verts=[]; faces=[]
    for lo,hi in boxes:
        add_box(verts, faces, lo, hi)
    with path.open("w") as f:
        for x,y,z in verts:
            f.write(f"v {x:.9g} {y:.9g} {z:.9g}\n")
        for a,b,c in faces:
            f.write(f"f {a+1} {b+1} {c+1}\n")


def write_quad_obj(path: Path, x0,x1,y0,y1,z, normal_plus_z=True) -> None:
    # Two triangles, exact planar sheet/panel.
    vs=[(x0,y0,z),(x1,y0,z),(x1,y1,z),(x0,y1,z)]
    fs=[(0,1,2),(0,2,3)] if normal_plus_z else [(0,2,1),(0,3,2)]
    with path.open("w") as f:
        for v in vs: f.write("v %.9g %.9g %.9g\n" % v)
        for a,b,c in fs: f.write(f"f {a+1} {b+1} {c+1}\n")


def sensor(origin, target, fov):
    return {
        "type":"perspective",
        "fov": float(fov),
        "fov_axis":"y",
        "to_world": mi.ScalarTransform4f.look_at(origin=origin, target=target, up=[0,1,0]),
        "sampler":{"type":"independent", "sample_count":SPP},
        "film":{
            "type":"hdrfilm", "width":W, "height":H,
            "pixel_format":"rgb", "component_format":"float32",
            "rfilter":{"type":"box"},
        },
    }


def render_scene(name: str, scene_dict: dict, extra: dict) -> np.ndarray:
    scene=mi.load_dict(scene_dict)
    img=mi.render(scene, spp=SPP, seed=SEED)
    arr=np.asarray(img, dtype=np.float32)
    if arr.ndim == 2:
        arr=arr.reshape(H,W,-1)
    arr=arr[..., :3]
    assert arr.shape == (H,W,3), arr.shape
    assert np.all(np.isfinite(arr)), f"non-finite Mitsuba output in {name}"
    write_pfm(OUT/f"{name}.pfm", arr)
    np.save(OUT/f"{name}.npy", arr)
    mi.Bitmap(arr).write(str(OUT/f"{name}.exr"))
    y = arr[...,0]*0.2126 + arr[...,1]*0.7152 + arr[...,2]*0.0722
    stats={
        "name":name,"spp":SPP,"seed":SEED,"shape":list(arr.shape),
        "mean_rgb":[float(x) for x in arr.mean(axis=(0,1))],
        "mean_luminance":float(y.mean()),"max_luminance":float(y.max()),
        "center_rgb":[float(x) for x in arr[H//2,W//2]],
        **extra,
    }
    (OUT/f"{name}.json").write_text(json.dumps(stats,indent=2,sort_keys=True)+"\n")
    return arr


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    variants=mi.variants()
    preferred="llvm_ad_rgb" if "llvm_ad_rgb" in variants else ("scalar_rgb" if "scalar_rgb" in variants else None)
    if preferred is None:
        raise RuntimeError(f"No RGB Mitsuba variant available: {variants}")
    mi.set_variant(preferred)

    # Recreate transforms after variant selection.
    global mi

    diffuse_obj=OUT/"diffuse_geometry.obj"
    write_obj(diffuse_obj,[
        ((0.0,0.0,0.0),(6.4,0.1,6.4)),
        ((0.0,0.1,0.0),(6.4,2.4,0.1)),
        ((2.3,0.1,2.3),(4.1,1.8,4.1)),
    ])
    pitch=-0.10
    eye=[3.2,1.75,6.10]
    fwd=[0.0,math.sin(pitch),-math.cos(pitch)]
    target=[eye[i]+fwd[i] for i in range(3)]
    diffuse_scene={
        "type":"scene",
        "integrator":{"type":"path","max_depth":64,"rr_depth":5},
        "sensor":sensor(eye,target,55.0),
        "env":{"type":"constant","radiance":{"type":"rgb","value":ENV}},
        "geom":{
            "type":"obj","filename":str(diffuse_obj),
            "bsdf":{"type":"diffuse","reflectance":{"type":"rgb","value":RHO}},
        },
    }
    render_scene("diffuse_env",diffuse_scene,{
        "variant":preferred,"semantic_contract":"Lambertian rho + constant environment radiance",
        "rho":RHO,"environment_radiance":ENV,
    })

    glass_obj=OUT/"thin_glass.obj"; panel_obj=OUT/"emissive_panel.obj"
    # VPT representational slab indices z=30..34 has front face z=3.5 when viewed from +z.
    write_quad_obj(glass_obj,1.2,5.2,0.6,3.4,3.5,True)
    # VPT emissive panel voxel layer z=19 has camera-facing surface z=2.0.
    write_quad_obj(panel_obj,1.0,5.4,0.4,3.6,2.0,True)
    eye=[3.2,2.0,5.7]; target=[3.2,2.0,4.7]
    thin_scene={
        "type":"scene",
        "integrator":{"type":"path","max_depth":64,"rr_depth":5},
        "sensor":sensor(eye,target,45.0),
        "glass":{
            "type":"obj","filename":str(glass_obj),
            "bsdf":{
                "type":"thindielectric","int_ior":1.5,"ext_ior":1.0,
                "specular_reflectance":{"type":"rgb","value":[1,1,1]},
                "specular_transmittance":{"type":"rgb","value":GLASS_TINT},
            },
        },
        "panel":{
            "type":"obj","filename":str(panel_obj),
            "emitter":{"type":"area","radiance":{"type":"rgb","value":PANEL_LE}},
        },
    }
    # Mitsuba thindielectric exact normal-incidence sheet reflectance.
    r=((1.5-1.0)/(1.5+1.0))**2
    rs=2*r/(1+r); ts=1-rs
    expected=[PANEL_LE[i]*GLASS_TINT[i]*ts for i in range(3)]
    render_scene("thin_emitter",thin_scene,{
        "variant":preferred,"semantic_contract":"zero-thickness paired-interface dielectric sheet",
        "ior":1.5,"transmission_tint":GLASS_TINT,"panel_radiance":PANEL_LE,
        "normal_incidence_R_interface":r,"normal_incidence_R_sheet":rs,
        "normal_incidence_T_sheet":ts,"analytic_transmitted_rgb":expected,
    })

    manifest={
        "mitsuba_version":getattr(mi,"__version__","unknown"),"variant":preferred,
        "available_variants":variants,"spp":SPP,"seed":SEED,"width":W,"height":H,
        "linear_hdr":True,"tone_mapping":False,"exposure_fit":False,
    }
    (OUT/"runtime_manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
    print(json.dumps(manifest,sort_keys=True))


if __name__ == "__main__":
    main()
