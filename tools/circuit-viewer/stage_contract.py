#!/usr/bin/env python3
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Check sampled basis-state preservation between two cuts of one circuit."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from trace_diff import checked, sha256


def run(manifest_path: Path, entry_meta_path: Path, exit_meta_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != "kickmix-trace-manifest-v1":
        raise ValueError("unsupported trace manifest schema")
    base = manifest_path.parent
    meta = json.loads(checked(base, manifest, "input_metadata").read_text())["metadata"]
    inputs = checked(base, manifest, "inputs")
    cohorts = np.array([m["cohort"] for m in meta])
    if [m["slot"] for m in meta] != list(range(len(meta))):
        raise ValueError("input metadata is not slot-aligned")
    endpoints = []
    for path in (entry_meta_path, exit_meta_path):
        info = json.loads(path.read_text())
        if info.get("schema") != "kickmix-workbench-sample-v1":
            raise ValueError("unsupported sample metadata schema")
        dump = path.with_suffix(".npz")
        if sha256(dump) != info["dump_sha256"]:
            raise ValueError(f"dump SHA-256 mismatch: {dump}")
        if info["inputs_sha256"] != sha256(inputs) or info["shots"] != len(meta) \
                or info["seeds"] != manifest["seeds"]:
            raise ValueError("sample metadata disagrees with pinned inputs or seeds")
        endpoints.append((info, dump))
    before, after = endpoints[0][0], endpoints[1][0]
    for key in ("source_sha256", "engine_sha256", "qubits"):
        if before[key] != after[key]:
            raise ValueError(f"entry/exit {key} differs")
    if before["cut_op"] >= after["cut_op"]:
        raise ValueError("cuts are not in increasing order")
    pinned_exit = any(after["source_sha256"] == manifest[f"{side}_sha256"]
                      and after["cut_op"] == cut[f"{side}_op"]
                      and after["dump_sha256"] == cut[f"{side}_dump_sha256"]
                      for cut in manifest["cuts"] for side in ("host", "donor"))
    if not pinned_exit:
        raise ValueError("exit cut is not pinned by the trace manifest")
    with np.load(endpoints[0][1], allow_pickle=False) as a, np.load(endpoints[1][1], allow_pickle=False) as b:
        aq, bq, as_, bs = a["qubits"], b["qubits"], a["syndrome"], b["syndrome"]
        ap, bp = a["phase"], b["phase"]
        expected = (len(manifest["seeds"]), before["qubits"], len(meta))
        if aq.shape != bq.shape or aq.shape != expected or as_.shape != bs.shape \
                or as_.shape[0] != expected[0] or as_.shape[2] != expected[2] \
                or ap.shape != bp.shape or ap.shape != (expected[0], expected[2]):
            raise ValueError("incompatible sampled dump shapes")
        if not np.array_equal(aq, np.broadcast_to(aq[:1], aq.shape)) \
                or not np.array_equal(bq, np.broadcast_to(bq[:1], bq.shape)):
            raise ValueError("basis state differs between seeds")
        rows = []
        for i, seed in enumerate(manifest["seeds"]):
            qubit_changed = np.any(aq[i] != bq[i], axis=0)
            syndrome_changed = np.any(as_[i] != bs[i], axis=0)
            rows.append({"seed": seed, "qubit_mismatch_slots": int(np.count_nonzero(qubit_changed)),
                         "syndrome_mismatch_slots": int(np.count_nonzero(syndrome_changed)),
                         "qubit_mismatch_by_cohort": {str(c): int(np.count_nonzero(qubit_changed & (cohorts == c)))
                                                       for c in np.unique(cohorts)},
                         "syndrome_mismatch_by_cohort": {str(c): int(np.count_nonzero(syndrome_changed & (cohorts == c)))
                                                         for c in np.unique(cohorts)},
                         "raw_intermediate_phase_mismatch_slots": int(np.count_nonzero(ap[i] ^ bp[i]))})
    return {"schema": "kickmix-stage-contract-v1", "tier": "local prescreen",
            "manifest_sha256": sha256(manifest_path), "source_sha256": before["source_sha256"],
            "engine_sha256": before["engine_sha256"], "entry_op": before["cut_op"],
            "exit_op": after["cut_op"], "entry_dump_sha256": before["dump_sha256"],
            "exit_dump_sha256": after["dump_sha256"], "rows": rows,
            "limits": ["A sampled basis-state match is not an algebraic proof over all inputs.",
                       "Raw phase differences are intermediate and measurement dependent.",
                       "This tool does not compare the classical measurement record or certify phase debt."]}
