#!/usr/bin/env python3
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Screen a pinned DQI cut for a one-bit affine coordinate map.

This deliberately tests only donor_bit = host_bit XOR constant. A failure says
nothing about multi-bit linear maps or nonlinear representation changes.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from trace_diff import checked, sha256


def screen(manifest_path: Path, cut_name: str) -> dict:
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != "kickmix-trace-manifest-v1":
        raise ValueError("unsupported trace manifest schema")
    base = manifest_path.parent
    meta = json.loads(checked(base, manifest, "input_metadata").read_text())["metadata"]
    inputs = checked(base, manifest, "inputs")
    with np.load(inputs, allow_pickle=False) as z:
        weights = z["weights"].astype(int)
    if len(meta) != len(weights) or [m["slot"] for m in meta] != list(range(len(meta))):
        raise ValueError("input metadata is not aligned with shots")
    cohorts = np.array([m["cohort"] for m in meta])
    fit = (cohorts == "fixed_weight") & (weights % 2 == 0)
    holdouts = {
        "odd_fixed_weight": (cohorts == "fixed_weight") & (weights % 2 == 1),
        "natural": cohorts == "natural",
        "known_failure": cohorts == "known_failure",
    }
    if not np.any(fit) or any(not np.any(mask) for mask in holdouts.values()):
        raise ValueError("required fit or holdout cohort is empty")
    matches = [c for c in manifest["cuts"] if c["name"] == cut_name]
    if len(matches) != 1:
        raise ValueError("cut must appear exactly once")
    cut = matches[0]
    hp = checked(base, cut, "host_dump")
    dp = checked(base, cut, "donor_dump")
    with np.load(hp, allow_pickle=False) as hz, np.load(dp, allow_pickle=False) as dz:
        hs, ds = hz["syndrome"], dz["syndrome"]
        if hs.shape != ds.shape or hs.ndim != 3 or hs.shape[0] != len(manifest["seeds"]) \
                or hs.shape[2] != len(meta):
            raise ValueError("incompatible syndrome dump shapes")
        if not np.array_equal(hs, np.broadcast_to(hs[:1], hs.shape)) \
                or not np.array_equal(ds, np.broadcast_to(ds[:1], ds.shape)):
            raise ValueError("syndrome basis state differs between seeds")
        host, donor = hs[0], ds[0]
    nbits = host.shape[0]
    # Pack the fit patterns so each exact or complemented candidate is found
    # without a quadratic scan over all bit pairs.
    fit_count = int(np.count_nonzero(fit))
    host_patterns = np.packbits(host[:, fit], axis=1, bitorder="little")
    donor_patterns = np.packbits(donor[:, fit], axis=1, bitorder="little")
    mask = (1 << fit_count) - 1
    lookup: dict[int, list[int]] = {}
    for i, row in enumerate(host_patterns):
        key = int.from_bytes(row.tobytes(), "little")
        lookup.setdefault(key, []).append(i)
    mappings = []
    fit_candidates = 0
    full_pass = 0
    bit_errors = {name: 0 for name in holdouts}
    evaluated = {name: 0 for name in holdouts}
    ambiguous = 0
    for j, row in enumerate(donor_patterns):
        pattern = int.from_bytes(row.tobytes(), "little")
        if pattern in (0, mask):
            continue  # A constant fit pattern cannot identify a coordinate.
        candidates = [(i, 0) for i in lookup.get(pattern, [])]
        candidates += [(i, 1) for i in lookup.get(pattern ^ mask, [])]
        if not candidates:
            mappings.append({"donor_bit": j, "status": "no_fit_candidate"})
            continue
        fit_candidates += 1
        ambiguous += len(candidates) > 1
        candidates.sort(key=lambda x: (x[0] != j, x[0], x[1]))
        i, complement = candidates[0]
        errors = {}
        for name, hold in holdouts.items():
            count = int(np.count_nonzero((host[i, hold] ^ complement) != donor[j, hold]))
            errors[name] = count
            bit_errors[name] += count
            evaluated[name] += int(np.count_nonzero(hold))
        full_pass += all(n == 0 for n in errors.values())
        mappings.append({"donor_bit": j, "status": "fit_candidate", "host_bit": i,
                         "complement": complement, "candidate_count": len(candidates),
                         "holdout_errors": errors})
    variable = len(mappings)
    return {"schema": "kickmix-state-map-v1", "tier": "local prescreen",
            "model": "donor syndrome bit = one host syndrome bit XOR constant",
            "manifest_sha256": sha256(manifest_path), "cut": cut_name,
            "host_dump_sha256": sha256(hp), "donor_dump_sha256": sha256(dp),
            "seeds": manifest["seeds"], "fit_shots": fit_count,
            "holdout_shots": {name: int(np.count_nonzero(hold)) for name, hold in holdouts.items()},
            "syndrome_bits": nbits, "variable_donor_bits": variable,
            "fit_candidate_bits": fit_candidates, "full_holdout_pass_bits": full_pass,
            "ambiguous_fit_bits": ambiguous, "holdout_bit_errors": bit_errors,
            "holdout_evaluated_bits": evaluated, "mappings": mappings,
            "limits": ["Constant fit bits are excluded because a coordinate cannot be identified.",
                       "A failed one-bit map does not rule out multi-bit linear or nonlinear maps.",
                       "This is a fixed-sample local prescreen, not a verifier result."]}


def query(report: dict, word: int) -> dict:
    """Return a bounded 12-bit view of one donor syndrome word."""
    if report.get("schema") != "kickmix-state-map-v1":
        raise ValueError("expected kickmix-state-map-v1 report")
    if word < 0 or word * 12 >= report["syndrome_bits"]:
        raise ValueError("donor word is out of range")
    rows = [r for r in report["mappings"] if word * 12 <= r["donor_bit"] < (word + 1) * 12]
    mapped = [r for r in rows if r["status"] == "fit_candidate"]
    aligned = [r for r in mapped if r["host_bit"] % 12 == r["donor_bit"] % 12 and not r["complement"]]
    host_words = sorted({r["host_bit"] // 12 for r in aligned})
    return {"schema": "kickmix-state-map-query-v1", "tier": report["tier"],
            "cut": report["cut"], "donor_word": word,
            "aligned_host_word": host_words[0] if len(rows) == 12 and len(aligned) == 12 and len(host_words) == 1 else None,
            "fit_candidate_bits": len(mapped),
            "holdout_pass_bits": {c: sum(r["holdout_errors"][c] == 0 for r in mapped)
                                  for c in report["holdout_shots"]},
            "rows": rows, "limits": report["limits"]}


def complete_words(report: dict, holdouts: tuple[str, ...] = ("odd_fixed_weight", "natural")) -> dict[int, int]:
    """Whole-word sampled matches with aligned bit order and chosen holdouts."""
    if report.get("schema") != "kickmix-state-map-v1":
        raise ValueError("expected kickmix-state-map-v1 report")
    if any(h not in report["holdout_shots"] for h in holdouts):
        raise ValueError("requested holdout is absent")
    out = {}
    for word in range(report["syndrome_bits"] // 12):
        rows = [r for r in report["mappings"] if word * 12 <= r["donor_bit"] < (word + 1) * 12]
        aligned = [r for r in rows if r["status"] == "fit_candidate" and not r["complement"]
                   and r["host_bit"] % 12 == r["donor_bit"] % 12
                   and all(r["holdout_errors"][h] == 0 for h in holdouts)]
        sources = {r["host_bit"] // 12 for r in aligned}
        if len(rows) == len(aligned) == 12 and len(sources) == 1:
            out[word] = next(iter(sources))
    return out


def compare_words(before: dict, after: dict, before_manifest: Path, after_manifest: Path) -> dict:
    if before.get("manifest_sha256") != sha256(before_manifest) \
            or after.get("manifest_sha256") != sha256(after_manifest):
        raise ValueError("state map does not match its pinned manifest")
    bm, am = json.loads(before_manifest.read_text()), json.loads(after_manifest.read_text())
    if bm.get("inputs_sha256") != am.get("inputs_sha256") \
            or bm.get("input_metadata_sha256") != am.get("input_metadata_sha256"):
        raise ValueError("state maps use different fixed inputs or metadata")
    for path, manifest in ((before_manifest, bm), (after_manifest, am)):
        checked(path.parent, manifest, "inputs")
        checked(path.parent, manifest, "input_metadata")
    if before.get("seeds") != after.get("seeds") or before.get("fit_shots") != after.get("fit_shots") \
            or before.get("holdout_shots") != after.get("holdout_shots"):
        raise ValueError("state-map split or seeds differ")
    a, b = complete_words(before), complete_words(after)
    common = sorted(a.keys() & b.keys())
    rows = [{"donor_word": w, "before_host_word": a[w], "after_host_word": b[w],
             "same": a[w] == b[w]} for w in common]
    return {"schema": "kickmix-state-map-compare-v1", "tier": "local prescreen",
            "before_cut": before["cut"], "after_cut": after["cut"],
            "before_manifest_sha256": before["manifest_sha256"],
            "after_manifest_sha256": after["manifest_sha256"],
            "inputs_sha256": bm["inputs_sha256"],
            "before_full_words": len(a), "after_full_words": len(b),
            "common_full_words": len(common), "same_full_words": sum(r["same"] for r in rows),
            "added_words": sorted(b.keys() - a.keys()), "removed_words": sorted(a.keys() - b.keys()),
            "rows": rows,
            "limits": ["These are exact matches on the fixed fit and regular-input holdouts only.",
                       "Known-failure fixtures and phase debt remain separate checks."]}
