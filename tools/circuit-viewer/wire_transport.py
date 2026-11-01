#!/usr/bin/env python3
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Track only unconditional SWAP transport through selected KMB stages.

This is a wiring projection, not a value or phase proof. All other gates are
ignored, including conditional CSWAP and operations that change bit values.
"""
from __future__ import annotations

import numpy as np

from workbench import Circuit, digest


def analyze(c: Circuit, static: dict, stages: tuple[str, ...] = ("synthesis#1", "convert#1")) -> dict:
    circuit_sha = digest(c.path)
    if static.get("circuit", {}).get("sha256") != circuit_sha:
        raise ValueError("static report circuit hash differs")
    regs = []
    for i in range(70):
        row = c.register_ids.get(f"S{i}")
        if row is None or len(row) != 12 or not all(isq for isq, _ in row):
            raise ValueError(f"missing 12-qubit S{i} register")
        regs.append([q for _, q in row])
    flat = np.asarray(regs, dtype=np.int64)
    if len(np.unique(flat)) != 840:
        raise ValueError("S register qubit ids overlap")
    regions = {r["id"]: r for r in static["tag_regions"] if r["depth"] == 0}
    t = c.ops
    two = (t & 0x30) == 0x20
    three = (t & 0x30) == 0x30
    if int(np.count_nonzero(two)) != c.count["qq"] or int(np.count_nonzero(three)) != c.count["qqq"]:
        raise ValueError("opcode and operand stream counts differ")
    q0, q1 = c.stream("qq0"), c.stream("qq1")
    perm = np.arange(c.qubits, dtype=np.int64)
    stage_rows = []
    last_end = -1
    for name in stages:
        if name not in regions:
            raise ValueError(f"missing top-level region {name}")
        region = regions[name]
        lo, hi = region["start"], region["end"]
        if lo < last_end:
            raise ValueError("stages are overlapping or out of order")
        last_end = hi
        chunk = t[lo:hi]
        two_before = int(np.count_nonzero(two[:lo]))
        two_local = np.cumsum(two[lo:hi], dtype=np.int64) - 1
        swap_at = np.flatnonzero(chunk == 34)
        for at in swap_at:
            k = two_before + int(two_local[at])
            a, b = int(q0[k]), int(q1[k])
            perm[a], perm[b] = perm[b], perm[a]
        stage_rows.append({"stage": name, "range": [lo, hi],
                           "unconditional_swaps": int(len(swap_at)),
                           "cswaps_ignored": int(np.count_nonzero((chunk & 0x7f) == 50))})
    source = perm[flat]
    source_lookup = {int(q): (i, bit) for i, row in enumerate(regs) for bit, q in enumerate(row)}
    words = []
    for i, row in enumerate(source):
        origin = [source_lookup.get(int(q)) for q in row]
        aligned = origin[0][0] if origin[0] is not None and all(
            o == (origin[0][0], bit) for bit, o in enumerate(origin)) else None
        words.append({"word": i, "source_word": aligned,
                      "source_qubits": [int(q) for q in row]})
    return {"schema": "kickmix-wire-transport-v1", "tier": "static model / projection",
            "circuit_sha256": circuit_sha, "stages": stage_rows,
            "s_register_ids": flat.tolist(), "aligned_words": sum(w["source_word"] is not None for w in words),
            "words": words,
            "limits": ["Only unconditional SWAP opcodes are tracked; other gates can change values.",
                       "A matching physical origin is not a state or phase equivalence proof."]}


def compare(host: dict, donor: dict, sampled: dict) -> dict:
    if host.get("schema") != "kickmix-wire-transport-v1" or donor.get("schema") != "kickmix-wire-transport-v1" \
            or sampled.get("schema") != "kickmix-state-map-v1":
        raise ValueError("expected transport and state-map reports")
    if sampled["manifest_sha256"] is None:
        raise ValueError("sample map is not pinned")
    if host["s_register_ids"] != donor["s_register_ids"]:
        raise ValueError("S register physical ids differ")
    hosts_for_source = {}
    for w in host["words"]:
        if w["source_word"] is not None:
            hosts_for_source.setdefault(w["source_word"], []).append(w["word"])
    host_by_source = {s: words[0] for s, words in hosts_for_source.items() if len(words) == 1}
    sampled_words = {}
    for j in range(70):
        bits = [r for r in sampled["mappings"] if j * 12 <= r["donor_bit"] < (j + 1) * 12]
        aligned = [r for r in bits if r["status"] == "fit_candidate" and not r["complement"]
                   and r["host_bit"] % 12 == r["donor_bit"] % 12
                   and r["holdout_errors"]["odd_fixed_weight"] == 0
                   and r["holdout_errors"]["natural"] == 0]
        src = {r["host_bit"] // 12 for r in aligned}
        if len(bits) == 12 and len(aligned) == 12 and len(src) == 1:
            sampled_words[j] = next(iter(src))
    rows = []
    for d in donor["words"]:
        predicted = host_by_source.get(d["source_word"])
        observed = sampled_words.get(d["word"])
        rows.append({"donor_word": d["word"], "predicted_host_word": predicted,
                     "sampled_regular_host_word": observed,
                     "agrees": predicted == observed if predicted is not None and observed is not None else None})
    testable = [r for r in rows if r["agrees"] is not None]
    return {"schema": "kickmix-wire-transport-compare-v1", "tier": "static model / projection",
            "host_sha256": host["circuit_sha256"], "donor_sha256": donor["circuit_sha256"],
            "sample_manifest_sha256": sampled["manifest_sha256"],
            "status": "compared" if testable else "no_testable_words",
            "testable_words": len(testable),
            "agree_words": sum(r["agrees"] for r in testable) if testable else None,
            "rows": rows,
            "limits": ["The prediction tracks unconditional SWAPs only and ignores value-changing gates.",
                       "The sampled map is a local prescreen, not a universal word identity."]}
