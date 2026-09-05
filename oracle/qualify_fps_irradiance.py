#!/usr/bin/env python3
"""Fail-closed qualification for camera-conditioned FPS irradiance suites.

This script deliberately separates three states:
  PASS       structural invariants and convergence evidence are sufficient;
  UNRESOLVED structurally valid, but Monte-Carlo convergence is insufficient;
  FAIL       structural/correctness/provenance invariants are violated.

A noisy reference is never promoted to a correctness failure in the DUT, and a
structurally invalid reference is never hidden behind more samples.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

DEFAULT_TOTAL_ABS_P95 = 0.12
DEFAULT_TOTAL_SIGNIFICANT_REL_P95 = 0.60
DEFAULT_DIRECT_ABS_P95 = 0.12
DEFAULT_DIRECT_SIGNIFICANT_REL_P95 = 0.80
DEFAULT_MIN_COMPARISON_FRACTION = 0.15


def finite_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(float(x))


def next_spp(current: int, observed: float, target: float) -> int:
    """Predict next power-of-two spp from sqrt(N) Monte-Carlo scaling."""
    current = max(1, int(current))
    if not finite_number(observed) or observed <= 0 or target <= 0:
        return current * 2
    needed = current * (float(observed) / float(target)) ** 2
    n = 1
    while n < max(current * 2, math.ceil(needed)):
        n <<= 1
    return n


def camera_reports(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(doc.get("reports"), list):
        return list(doc["reports"])
    if isinstance(doc.get("cameras"), list):
        return list(doc["cameras"])
    # A single per-level suite report is also accepted.
    if isinstance(doc.get("camera"), dict) and "comparison_pixels" in doc:
        return [doc]
    return []


def level_key(r: Dict[str, Any]) -> str:
    if isinstance(r.get("level"), str):
        return r["level"]
    cam = r.get("camera")
    if isinstance(cam, dict):
        for k in ("level", "source"):
            if isinstance(cam.get(k), str):
                return cam[k]
    if isinstance(r.get("source"), str):
        return r["source"]
    return "unknown"


def camera_key(r: Dict[str, Any]) -> str:
    level = level_key(r)
    cam = r.get("camera")
    if isinstance(cam, dict):
        name = cam.get("name", "unnamed")
    else:
        name = str(cam or r.get("name") or "unnamed")
    return f"{level}/{name}"


def stat_is_finite(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    if block.get("finite") is False:
        return False
    for k in ("mean_luminance", "min_channel", "max_channel"):
        if k in block and not finite_number(block[k]):
            return False
    return True


def disagreement_metrics(d: Any) -> Tuple[float | None, float | None]:
    if not isinstance(d, dict):
        return None, None
    abs95 = d.get("absdiff_over_p95_p95")
    rel95 = d.get("significant_p95")
    # Accept the compact experimental report vocabulary as well.
    if abs95 is None:
        abs95 = d.get("abs_p95")
    if rel95 is None:
        rel95 = d.get("sig_rel_p95")
    return (float(abs95) if finite_number(abs95) else None,
            float(rel95) if finite_number(rel95) else None)


def qualify(doc: Dict[str, Any], *, expected_cameras: int,
            expected_levels: int, min_comparison_fraction: float,
            total_abs_p95: float, total_sig_rel_p95: float,
            direct_abs_p95: float, direct_sig_rel_p95: float) -> Dict[str, Any]:
    reports = camera_reports(doc)
    failures: List[str] = []
    unresolved: List[Dict[str, Any]] = []
    warnings: List[str] = []

    if len(reports) != expected_cameras:
        failures.append(f"expected {expected_cameras} cameras, found {len(reports)}")

    levels = sorted({level_key(r) for r in reports})
    if len(levels) != expected_levels:
        failures.append(f"expected {expected_levels} level classes, found {len(levels)}: {levels}")

    if doc.get("zero_ambiguous_transmissive") is False:
        failures.append("ambiguous transmissive boundaries are non-zero")
    if finite_number(doc.get("ambiguousTransmissive")) and doc["ambiguousTransmissive"] != 0:
        failures.append(f"ambiguous transmissive boundaries={doc['ambiguousTransmissive']}")

    reseeded_by_level: Dict[str, int] = {k: 0 for k in levels}
    for r in reports:
        key = camera_key(r)
        frac = r.get("comparison_fraction")
        if not finite_number(frac) or float(frac) < min_comparison_fraction:
            failures.append(f"{key}: comparison_fraction={frac!r} below {min_comparison_fraction}")
        if not r.get("comparison_pixels"):
            failures.append(f"{key}: no comparison pixels")
        for component in ("total", "direct", "indirect"):
            if not stat_is_finite(r.get(component)):
                failures.append(f"{key}: {component} statistics are missing/non-finite")

        seeds = r.get("seeds") or []
        if len(seeds) < 2:
            continue
        reseeded_by_level[level_key(r)] = reseeded_by_level.get(level_key(r), 0) + 1
        spp = int(r.get("spp_per_seed") or 0)

        for component, abs_target, rel_target in (
            ("total", total_abs_p95, total_sig_rel_p95),
            ("direct", direct_abs_p95, direct_sig_rel_p95),
        ):
            d = r.get(f"seed_disagreement_{component}")
            abs95, rel95 = disagreement_metrics(d)
            if abs95 is None or rel95 is None:
                failures.append(f"{key}: reseeded {component} lacks convergence metrics")
                continue
            if abs95 > abs_target or rel95 > rel_target:
                rec = next_spp(spp, max(abs95 / abs_target, rel95 / rel_target), 1.0)
                unresolved.append({
                    "camera": key,
                    "component": component,
                    "spp_per_seed": spp,
                    "absdiff_over_p95_p95": abs95,
                    "significant_p95": rel95,
                    "targets": {"absdiff_over_p95_p95": abs_target,
                                "significant_p95": rel_target},
                    "recommended_spp_per_seed": rec,
                })

    for level, count in reseeded_by_level.items():
        if count < 1:
            failures.append(f"{level}: no independently reseeded convergence sentinel")

    status = "FAIL" if failures else ("UNRESOLVED" if unresolved else "PASS")
    return {
        "schema": 1,
        "status": status,
        "camera_count": len(reports),
        "level_count": len(levels),
        "levels": levels,
        "reseeded_by_level": reseeded_by_level,
        "thresholds": {
            "min_comparison_fraction": min_comparison_fraction,
            "total_absdiff_over_p95_p95": total_abs_p95,
            "total_significant_p95": total_sig_rel_p95,
            "direct_absdiff_over_p95_p95": direct_abs_p95,
            "direct_significant_p95": direct_sig_rel_p95,
        },
        "failures": failures,
        "unresolved": unresolved,
        "warnings": warnings,
    }


def self_test() -> None:
    base_cam = {
        "level": "L0", "camera": "fps_test", "comparison_pixels": 100,
        "comparison_fraction": 0.5, "seeds": [11, 29], "spp_per_seed": 512,
        "total": {"finite": True, "mean_luminance": 1.0},
        "direct": {"finite": True, "mean_luminance": 0.6},
        "indirect": {"finite": True, "mean_luminance": 0.4},
        "seed_disagreement_total": {"absdiff_over_p95_p95": 0.10, "significant_p95": 0.50},
        "seed_disagreement_direct": {"absdiff_over_p95_p95": 0.09, "significant_p95": 0.70},
    }
    doc = {"reports": []}
    for li in range(4):
        for ci in range(3):
            c = json.loads(json.dumps(base_cam)); c["level"] = f"L{li}"; c["camera"] = f"fps_{ci}"
            if ci:
                c["seeds"] = [11]
                c["seed_disagreement_total"] = None
                c["seed_disagreement_direct"] = None
            doc["reports"].append(c)
    q = qualify(doc, expected_cameras=12, expected_levels=4,
                min_comparison_fraction=.15, total_abs_p95=.12,
                total_sig_rel_p95=.60, direct_abs_p95=.12,
                direct_sig_rel_p95=.80)
    assert q["status"] == "PASS", q
    noisy = json.loads(json.dumps(doc)); noisy["reports"][0]["seed_disagreement_total"]["absdiff_over_p95_p95"] = .25
    q = qualify(noisy, expected_cameras=12, expected_levels=4,
                min_comparison_fraction=.15, total_abs_p95=.12,
                total_sig_rel_p95=.60, direct_abs_p95=.12,
                direct_sig_rel_p95=.80)
    assert q["status"] == "UNRESOLVED" and q["unresolved"], q
    broken = json.loads(json.dumps(doc)); broken["reports"][0]["comparison_fraction"] = 0
    q = qualify(broken, expected_cameras=12, expected_levels=4,
                min_comparison_fraction=.15, total_abs_p95=.12,
                total_sig_rel_p95=.60, direct_abs_p95=.12,
                direct_sig_rel_p95=.80)
    assert q["status"] == "FAIL" and q["failures"], q
    print("qualify_fps_irradiance self-test: PASS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report", nargs="?", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--expected-cameras", type=int, default=12)
    ap.add_argument("--expected-levels", type=int, default=4)
    ap.add_argument("--min-comparison-fraction", type=float, default=DEFAULT_MIN_COMPARISON_FRACTION)
    ap.add_argument("--total-abs-p95", type=float, default=DEFAULT_TOTAL_ABS_P95)
    ap.add_argument("--total-significant-rel-p95", type=float, default=DEFAULT_TOTAL_SIGNIFICANT_REL_P95)
    ap.add_argument("--direct-abs-p95", type=float, default=DEFAULT_DIRECT_ABS_P95)
    ap.add_argument("--direct-significant-rel-p95", type=float, default=DEFAULT_DIRECT_SIGNIFICANT_REL_P95)
    a = ap.parse_args()
    if a.self_test:
        self_test(); return 0
    if not a.report:
        ap.error("report is required unless --self-test is used")
    doc = json.loads(a.report.read_text())
    result = qualify(doc, expected_cameras=a.expected_cameras,
                     expected_levels=a.expected_levels,
                     min_comparison_fraction=a.min_comparison_fraction,
                     total_abs_p95=a.total_abs_p95,
                     total_sig_rel_p95=a.total_significant_rel_p95,
                     direct_abs_p95=a.direct_abs_p95,
                     direct_sig_rel_p95=a.direct_significant_rel_p95)
    text = json.dumps(result, indent=2, sort_keys=True)
    if a.out:
        a.out.write_text(text + "\n")
    print(text)
    return 0 if result["status"] == "PASS" else (2 if result["status"] == "UNRESOLVED" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
