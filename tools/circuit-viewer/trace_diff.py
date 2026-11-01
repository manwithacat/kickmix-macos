#!/usr/bin/env python3
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Compare pinned sampled cut dumps without executing a circuit or verifier.

Input manifest schema: kickmix-trace-manifest-v1. Paths are relative to the
manifest. Dumps must contain qubits[seed,qubit,shot], syndrome[seed,bit,shot],
phase[seed,shot], and bits/bit_compact. The output is a bounded local prescreen.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def checked(base: Path, spec: dict, key: str) -> Path:
    path = (base / spec[key]).resolve()
    want = spec.get(f"{key}_sha256")
    if not isinstance(want, str) or len(want) != 64 or any(c not in "0123456789abcdef" for c in want):
        raise ValueError(f"missing or malformed SHA-256 for {key}")
    if sha256(path) != want:
        raise ValueError(f"SHA-256 mismatch for {key}: {path}")
    return path


def word(bits: np.ndarray) -> int:
    return sum(int(x) << i for i, x in enumerate(bits))


def gf2_rank(columns: np.ndarray) -> int:
    """Rank of bit columns over GF(2); descriptive on the fixed sample only."""
    basis = {}
    for j in range(columns.shape[1]):
        v = int.from_bytes(np.packbits(columns[:, j], bitorder="little").tobytes(), "little")
        while v:
            p = v.bit_length() - 1
            if p in basis:
                v ^= basis[p]
            else:
                basis[p] = v
                break
    return len(basis)


def analyze(manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != "kickmix-trace-manifest-v1":
        raise ValueError("unsupported trace manifest schema")
    base = manifest_path.parent
    meta = json.loads(checked(base, manifest, "input_metadata").read_text())["metadata"]
    inputs = checked(base, manifest, "inputs")
    with np.load(inputs, allow_pickle=False) as z:
        shots = len(z["weights"])
        weights = z["weights"].astype(int)
    if len(meta) != shots or [m["slot"] for m in meta] != list(range(shots)):
        raise ValueError("input metadata is not aligned with shots")
    cohorts = np.array([m["cohort"] for m in meta])
    seeds = list(manifest["seeds"])
    first = {seed: {str(i): None for i in range(shots)} for seed in seeds}
    mismatch_history = {seed: [] for seed in seeds}
    outcuts = []
    for cut in manifest["cuts"]:
        hp = checked(base, cut, "host_dump")
        dp = checked(base, cut, "donor_dump")
        with np.load(hp, allow_pickle=False) as hz, np.load(dp, allow_pickle=False) as dz:
            hq, dq = hz["qubits"], dz["qubits"]
            hs, ds = hz["syndrome"], dz["syndrome"]
            ph, pd = hz["phase"], dz["phase"]
            if hq.ndim != 3 or dq.ndim != 3 or hs.shape != ds.shape or hs.shape[0] != len(seeds) \
                    or hs.shape[2] != shots or ph.shape != pd.shape or ph.shape != (len(seeds), shots) \
                    or hs.shape[1] % 12 or hq.shape[0] != len(seeds) or dq.shape[0] != len(seeds) \
                    or hq.shape[2] != shots or dq.shape[2] != shots:
                raise ValueError(f"incompatible dump shape at {cut['name']}")
            if not all(k in hz and k in dz for k in ("bits", "bit_compact")):
                raise ValueError("missing classical-bit trace")
            nwords = hs.shape[1] // 12
            rows = []
            for ix, seed in enumerate(seeds):
                xor = hs[ix] ^ ds[ix]
                mismatch = xor.any(axis=0)
                mismatch_history[seed].append(mismatch.copy())
                mw = xor.reshape(nwords, 12, shots).any(axis=1)
                slots = np.flatnonzero(mismatch)
                for slot in slots:
                    if first[seed][str(int(slot))] is None:
                        first[seed][str(int(slot))] = cut["name"]
                changed = np.flatnonzero(mw.any(axis=1)).astype(int).tolist()
                examples = []
                for slot in slots[:4]:
                    wi = int(np.flatnonzero(mw[:, slot])[0])
                    examples.append({"slot": int(slot), "cohort": str(cohorts[slot]),
                                     "weight": int(weights[slot]), "metadata": meta[int(slot)],
                                     "word": wi,
                                     "host": word(hs[ix, wi * 12:(wi + 1) * 12, slot]),
                                     "donor": word(ds[ix, wi * 12:(wi + 1) * 12, slot])})
                rows.append({"seed": seed, "syndrome_mismatch_slots": int(len(slots)),
                             "syndrome_mismatch_by_cohort": {
                                 str(c): int(np.count_nonzero(mismatch & (cohorts == c)))
                                 for c in np.unique(cohorts)},
                             "syndrome_mismatch_by_weight": {
                                 str(int(w)): int(np.count_nonzero(mismatch & (weights == w)))
                                 for w in np.unique(weights) if np.any(mismatch & (weights == w))},
                             "changed_words": changed,
                             "changed_words_by_cohort": {
                                 str(c): np.flatnonzero(mw[:, cohorts == c].any(axis=1)).astype(int).tolist()
                                 for c in np.unique(cohorts)},
                             "xor_rank_by_cohort": {
                                 str(c): gf2_rank(xor[:, cohorts == c]) for c in np.unique(cohorts)},
                             "examples": examples,
                             "host_dirty_scratch_slots": int(np.count_nonzero(hq[ix, hs.shape[1]:].any(axis=0))),
                             "donor_dirty_scratch_slots": int(np.count_nonzero(dq[ix, hs.shape[1]:].any(axis=0))),
                             "raw_intermediate_phase_mismatch_slots": int(np.count_nonzero(ph[ix] ^ pd[ix]))})
            outcuts.append({"name": cut["name"], "host_op": cut["host_op"], "donor_op": cut["donor_op"],
                            "host_dump_sha256": sha256(hp), "donor_dump_sha256": sha256(dp),
                            "host_basis_seed_independent": bool(np.array_equal(hq[0], hq[1])) if len(seeds) == 2 else None,
                            "donor_basis_seed_independent": bool(np.array_equal(dq[0], dq[1])) if len(seeds) == 2 else None,
                            "rows": rows})
    transitions = {}
    nonmonotonic = False
    for seed in seeds:
        transitions[str(seed)] = []
        for i in range(1, len(outcuts)):
            before, after = mismatch_history[seed][i - 1], mismatch_history[seed][i]
            row = {"from": outcuts[i - 1]["name"], "to": outcuts[i]["name"], "by_cohort": {}}
            for c in np.unique(cohorts):
                mask = cohorts == c
                resolved = int(np.count_nonzero(before & ~after & mask))
                row["by_cohort"][str(c)] = {
                    "new": int(np.count_nonzero(~before & after & mask)),
                    "persisting": int(np.count_nonzero(before & after & mask)),
                    "resolved": resolved}
                nonmonotonic |= resolved > 0
            transitions[str(seed)].append(row)
    first_summary = {}
    for seed in seeds:
        first_summary[str(seed)] = {cut["name"]: {
            str(c): sum(first[seed][str(i)] == cut["name"] and cohorts[i] == c for i in range(shots))
            for c in np.unique(cohorts)} for cut in manifest["cuts"]}
    return {"schema": "kickmix-trace-diff-v1", "tier": "local prescreen",
            "manifest_sha256": sha256(manifest_path), "inputs_sha256": sha256(inputs),
            "host_sha256": manifest["host_sha256"], "donor_sha256": manifest["donor_sha256"],
            "shots": shots, "seeds": seeds, "cuts": outcuts,
            "first_syndrome_divergence_by_cohort": first_summary,
            "raw_difference_nonmonotonic": bool(nonmonotonic), "transitions": transitions,
            "limits": ["Raw intermediate phase bits may differ with measurement records and do not prove a circuit failure.",
                       "Matching sampled basis states do not prove algebraic equivalence.",
                       "Raw state differences can arise from temporary representations and later resolve; first difference is not a fault location.",
                       "The caller must separately attest that each dump came from its named circuit and cut."]}


def query(report: dict, cut: str | None = None, seed: int | None = None) -> dict:
    if report.get("schema") != "kickmix-trace-diff-v1":
        raise ValueError("expected kickmix-trace-diff-v1 report")
    rows = []
    for c in report["cuts"]:
        if cut and c["name"] != cut:
            continue
        for r in c["rows"]:
            if seed is not None and r["seed"] != seed:
                continue
            rows.append({"cut": c["name"], "host_op": c["host_op"], "donor_op": c["donor_op"],
                         "seed": r["seed"], "syndrome_mismatch_slots": r["syndrome_mismatch_slots"],
                         "syndrome_mismatch_by_cohort": r["syndrome_mismatch_by_cohort"],
                         "syndrome_mismatch_by_weight": r["syndrome_mismatch_by_weight"],
                         "changed_words": r["changed_words"],
                         "changed_words_by_cohort": r["changed_words_by_cohort"],
                         "xor_rank_by_cohort": r["xor_rank_by_cohort"],
                         "examples": r["examples"][:2],
                         "host_dirty_scratch_slots": r["host_dirty_scratch_slots"],
                         "donor_dirty_scratch_slots": r["donor_dirty_scratch_slots"],
                         "raw_intermediate_phase_mismatch_slots": r["raw_intermediate_phase_mismatch_slots"]})
    return {"schema": "kickmix-trace-query-v1", "tier": "local prescreen", "matched": len(rows),
            "rows": rows, "first_syndrome_divergence_by_cohort": report["first_syndrome_divergence_by_cohort"],
            "raw_difference_nonmonotonic": report.get("raw_difference_nonmonotonic", False),
            "transitions": report.get("transitions", {}).get(str(seed), []) if seed is not None else report.get("transitions", {}),
            "phase_note": report["limits"][0],
            "state_note": "Raw differences may be temporary representation changes, not faults."}


def plan(report: dict, host_static: dict, donor_static: dict, cohort: str, seed: int) -> dict:
    """Suggest an aligned top-level tag cut between last cohort match and first mismatch.

    This is a static diagnostic plan, not a state or phase claim.
    """
    if report.get("schema") != "kickmix-trace-diff-v1":
        raise ValueError("expected trace report")
    if report.get("raw_difference_nonmonotonic"):
        return {"schema": "kickmix-trace-plan-v1", "tier": "static model / projection",
                "status": "raw_state_nonmonotonic", "cohort": cohort, "seed": seed,
                "note": "A sampled difference resolves at a later cut. Derive an invariant or canonical state map before bisection."}
    if host_static["circuit"]["sha256"] != report["host_sha256"] \
            or donor_static["circuit"]["sha256"] != report["donor_sha256"]:
        raise ValueError("static report circuit hashes do not match trace report")
    seq = []
    for cut in report["cuts"]:
        rows = [r for r in cut["rows"] if r["seed"] == seed]
        if len(rows) != 1:
            raise ValueError("seed not found exactly once at each cut")
        seq.append((cut, rows[0]["syndrome_mismatch_by_cohort"].get(cohort, 0)))
    first = next((i for i, (_, n) in enumerate(seq) if n), None)
    if first is None:
        return {"schema": "kickmix-trace-plan-v1", "status": "no_sampled_divergence",
                "cohort": cohort, "seed": seed}
    if first == 0:
        return {"schema": "kickmix-trace-plan-v1", "status": "no_prior_matching_cut",
                "cohort": cohort, "seed": seed, "first_mismatch": seq[0][0]["name"]}
    before, after = seq[first - 1][0], seq[first][0]
    hregions = {r["id"]: r for r in host_static["tag_regions"] if r["depth"] == 0}
    dregions = {r["id"]: r for r in donor_static["tag_regions"] if r["depth"] == 0}
    candidates = []
    for key in hregions.keys() & dregions.keys():
        h, d = hregions[key], dregions[key]
        for side in ("start", "end"):
            ho, do = h[side], d[side]
            if before["host_op"] < ho < after["host_op"] and before["donor_op"] < do < after["donor_op"]:
                candidates.append({"region": key, "side": side, "host_op": ho, "donor_op": do})
    middle = (before["host_op"] + after["host_op"]) / 2
    candidates.sort(key=lambda x: abs(x["host_op"] - middle))
    return {"schema": "kickmix-trace-plan-v1", "tier": "static model / projection",
            "status": "suggested_cut" if candidates else "no_aligned_top_level_cut",
            "cohort": cohort, "seed": seed, "last_match": before["name"],
            "first_mismatch": after["name"], "host_interval": [before["host_op"], after["host_op"]],
            "donor_interval": [before["donor_op"], after["donor_op"]],
            "suggestions": candidates[:3],
            "note": "Re-run a separately registered sampler at a suggested paired cut; this plan does not run one."}
