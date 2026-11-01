#!/usr/bin/env python3
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Write an unchanged, depth-zero KMB prefix for sampled cut diagnostics."""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import numpy as np

MAGIC = bytes.fromhex("d750c7d5c329d326e3cc9f6834f2b8bf")
STREAMS = ("qqq0", "qqq1", "qqq2", "qq0", "qq1", "q0", "b0", "bc")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_prefix(c, op: int, out: Path) -> dict:
    t = c.ops
    if op < 0 or op > len(t):
        raise ValueError("cut outside circuit")
    sub = t[:op]
    if int(np.count_nonzero(sub == 129)) != int(np.count_nonzero(sub == 1)):
        raise ValueError("cut is inside a condition stack")
    cls = sub & 0x30
    n3 = int(np.count_nonzero(cls == 0x30))
    n2 = int(np.count_nonzero(cls == 0x20))
    n1 = int(np.count_nonzero(cls == 0x10))
    nb = int(np.count_nonzero((sub & 0x40) != 0))
    nc = int(np.count_nonzero((sub & 0x80) != 0))
    na = int(np.count_nonzero((sub & 0x7f) == 19))
    nt = int(np.count_nonzero(sub == 6))
    tags = [s.encode("utf8") for s in c.tags[:nt]]
    ends = np.cumsum([len(x) for x in tags], dtype="<u8")
    chars = b"".join(tags)
    lens = dict(zip(STREAMS, (n3, n3, n3, n2, n2, n1, nb, nc)))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("xb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<4I", 2, c.qubits, c.bits, 0))
        f.write(struct.pack("<9Q", op, n3, n2, n1, nb, nc, na, nt, len(chars)))
        f.write(struct.pack("<2Q", 1, op)); sub.tofile(f)
        for i, name in enumerate(STREAMS):
            f.write(struct.pack("<2Q", i + 2, lens[name] * 4))
            c.stream(name)[:lens[name]].tofile(f)
        f.write(struct.pack("<2Q", 10, na * 16))
        c.stream("angles")[:na].tofile(f)
        f.write(struct.pack("<2Q", 11, 8 * (nt + 1) + len(chars)))
        f.write(struct.pack("<Q", nt)); ends.tofile(f); f.write(chars)
    # Reader validation checks all packet sizes, counts, and final length.
    check = type(c)(out)
    if len(check.ops) != op or not np.array_equal(check.ops, sub):
        raise ValueError("prefix op stream differs from source")
    for name, n in lens.items():
        if not np.array_equal(check.stream(name), c.stream(name)[:n]):
            raise ValueError(f"prefix {name} stream differs from source")
    return {"schema": "kickmix-workbench-prefix-v1", "tier": "static model / projection",
            "source_sha256": sha(c.path), "prefix_sha256": sha(out), "op": op,
            "prefix_file": str(out), "qubits": c.qubits, "bits": c.bits,
            "note": "Diagnostic prefix only; no circuit correctness claim. Registers are omitted; caller supplies inputs."}
