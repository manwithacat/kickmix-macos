#!/usr/bin/env python3
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Execute a diagnostic DQI KMB prefix on pre-registered inputs.

Requires a compatible kickmix_engine Python package supplied by the caller.
This is a sampled state probe, not a complete-circuit verifier.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path

import numpy as np


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sample(full, prefix, inputs: Path, seeds: list[int], engine_path: Path, out: Path) -> dict:
    if out.exists() or out.with_suffix(".json").exists():
        raise FileExistsError("refusing to overwrite sampled evidence")
    if prefix.qubits != full.qubits or prefix.bits != full.bits or len(prefix.ops) > len(full.ops):
        raise ValueError("prefix header differs from source")
    if not np.array_equal(prefix.ops, full.ops[:len(prefix.ops)]):
        raise ValueError("prefix operations differ from source")
    for name in ("qqq0", "qqq1", "qqq2", "qq0", "qq1", "q0", "b0", "bc", "angles"):
        n = len(prefix.stream(name))
        if not np.array_equal(prefix.stream(name), full.stream(name)[:n]):
            raise ValueError(f"prefix {name} stream differs from source")
    if not seeds or len(seeds) > 4:
        raise ValueError("supply 1..4 fixed engine seeds")
    if not engine_path.is_dir():
        raise ValueError("engine package path is not a directory")
    sys.path.insert(0, str(engine_path))
    ke = importlib.import_module("kickmix_engine")
    pack_rows = importlib.import_module("kickmix_engine.dqi").pack_rows
    with np.load(inputs, allow_pickle=False) as z:
        syn = np.array(z["syndromes"])
        mb = np.array(z["mbuc"])
    if syn.ndim != 2 or syn.shape[1] != 70 or mb.shape != (len(syn), 4095) \
            or len(syn) != 256 or np.any(syn < 0) or np.any(syn > 4095) \
            or np.any(mb < 0) or np.any(mb > 4095):
        raise ValueError("expected 256 DQI shots with 70 syndromes and 4095 masks of 12-bit words")
    regs = full.register_ids
    try:
        syn_ids = [q for i in range(70) for isq, q in regs[f"S{i}"] if isq]
        e_ids = [q for i in range(4095) for isq, q in regs[f"E{i}"] if not isq]
    except KeyError as exc:
        raise ValueError(f"missing DQI register {exc}") from exc
    if len(syn_ids) != 840 or len(e_ids) != 49140:
        raise ValueError("unexpected DQI register widths")
    program = ke.compile_kmb(str(prefix.path))
    comp = np.asarray(program.bit_compact, dtype=np.int64)
    mapped = comp[np.asarray(e_ids)]
    keep = np.flatnonzero(mapped >= 0)
    qin = np.repeat(pack_rows(syn, 12)[None], len(seeds), axis=0)
    bin_ = np.repeat(pack_rows(mb, 12)[None], len(seeds), axis=0)
    result = ke.run_batches(program, seeds, syn_ids, qin, mapped[keep].tolist(),
                            bin_[:, keep, :], threads=1, counting=True, dump=True)
    ns = len(seeds); shots = len(syn)
    qb = np.unpackbits(result["dump_q"].view(np.uint8).reshape(ns, prefix.qubits, shots // 8),
                       axis=2, bitorder="little").astype(bool)
    phase = np.unpackbits(result["phase"].view(np.uint8).reshape(ns, shots // 8),
                          axis=1, bitorder="little").astype(bool)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("xb") as f:
        np.savez_compressed(f, qubits=qb, syndrome=qb[:, syn_ids, :], phase=phase,
                            bits=result["dump_bits"], bit_compact=comp,
                            toffoli_count=result["toffoli_count"])
    meta = {"schema": "kickmix-workbench-sample-v1", "tier": "local prescreen",
            "source_sha256": sha(full.path), "prefix_sha256": sha(prefix.path),
            "inputs_sha256": sha(inputs), "dump_sha256": sha(out),
            "engine_sha256": sha(Path(ke._engine.__file__)), "seeds": seeds, "shots": shots,
            "cut_op": len(prefix.ops), "qubits": prefix.qubits,
            "note": "Diagnostic prefix state only; no completed-circuit correctness claim."}
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta
