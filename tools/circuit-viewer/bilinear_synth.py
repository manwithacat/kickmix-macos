#!/usr/bin/env python3
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Synthesize a fixed-width dirty-target bilinear KMB from rank-one terms.

Input JSON: {"width": 4, "modulus": 19, "terms":
  [{"a_mask": 1, "b_mask": 1, "output_mask": 1}, ...]}
The complete term tensor must equal multiplication modulo the polynomial.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

MAGIC = bytes.fromhex("d750c7d5c329d326e3cc9f6834f2b8bf")


def field_mul(a: int, b: int, width: int, modulus: int) -> int:
    out = 0
    for _ in range(width):
        if b & 1:
            out ^= a
        b >>= 1
        a <<= 1
        if a >> width & 1:
            a ^= modulus
    return out


def validated_terms(data: dict) -> tuple[int, int, list[tuple[int, int, int]]]:
    width, modulus = data["width"], data["modulus"]
    if not isinstance(width, int) or not 1 <= width <= 30:
        raise ValueError("width must be an integer in 1..30")
    if not isinstance(modulus, int) or modulus.bit_length() != width + 1 or not modulus & 1:
        raise ValueError("modulus must be a monic degree-width binary polynomial with constant 1")
    limit = 1 << width
    terms = []
    for i, row in enumerate(data["terms"]):
        if not isinstance(row, dict):
            raise ValueError(
                f"term {i}: expected an object with a_mask, b_mask, output_mask "
                f"(for example {{\"a_mask\": 1, \"b_mask\": 1, "
                f"\"output_mask\": 1}}); got {type(row).__name__}")
        missing = [name for name in ("a_mask", "b_mask", "output_mask")
                   if name not in row]
        if missing:
            raise ValueError(f"term {i}: missing {', '.join(missing)}; expected "
                             "a_mask, b_mask, output_mask")
        values = tuple(row[name] for name in ("a_mask", "b_mask", "output_mask"))
        if any(not isinstance(v, int) or not 0 < v < limit for v in values):
            raise ValueError(f"term {i}: masks must be nonzero width-bit integers")
        terms.append(values)
    if not terms:
        raise ValueError("empty term list")
    basis_products = data.get("basis_products")
    if basis_products is not None and (len(basis_products) != width * width or any(
        not isinstance(value, int) or not 0 <= value < limit for value in basis_products
    )):
        raise ValueError("basis_products must contain width*width width-bit integers")
    for i in range(width):
        for j in range(width):
            actual = 0
            for a, b, out in terms:
                if a >> i & b >> j & 1:
                    actual ^= out
            expected = (basis_products[i * width + j] if basis_products is not None
                        else field_mul(1 << i, 1 << j, width, modulus))
            if actual != expected:
                raise ValueError(f"tensor mismatch at basis pair ({i},{j}): {actual} != {expected}")
    return width, modulus, terms


def synthesize(width: int, terms: list[tuple[int, int, int]]) -> tuple[list[int], dict[str, list[int]]]:
    ops: list[int] = []
    streams: dict[str, list[int]] = {name: [] for name in
                                    ("qqq0", "qqq1", "qqq2", "qq0", "qq1")}

    def cx(control: int, target: int) -> None:
        ops.append(33)
        streams["qq0"].append(control)
        streams["qq1"].append(target)

    for a_mask, b_mask, output_mask in terms:
        a = [i for i in range(width) if a_mask >> i & 1]
        b = [width + i for i in range(width) if b_mask >> i & 1]
        target = [2 * width + i for i in range(width) if output_mask >> i & 1]
        for wire in target[1:]:
            cx(target[0], wire)
        for wire in a[1:]:
            cx(wire, a[0])
        for wire in b[1:]:
            cx(wire, b[0])
        ops.append(49)
        for name, wire in zip(("qqq0", "qqq1", "qqq2"), (a[0], b[0], target[0])):
            streams[name].append(wire)
        for wire in reversed(b[1:]):
            cx(wire, b[0])
        for wire in reversed(a[1:]):
            cx(wire, a[0])
        for wire in reversed(target[1:]):
            cx(target[0], wire)
    return ops, streams


def kmb_bytes(width: int, ops: list[int], streams: dict[str, list[int]]) -> bytes:
    out = bytearray(MAGIC)
    out.extend(struct.pack("<4I", 2, 3 * width, 0, 3))
    out.extend(struct.pack("<9Q", len(ops), len(streams["qqq0"]),
                           len(streams["qq0"]), 0, 0, 0, 0, 0, 0))

    def packet(kind: int, body: bytes) -> None:
        out.extend(struct.pack("<2Q", kind, len(body)))
        out.extend(body)

    for index, name in enumerate(("a", "b", "t")):
        name_bytes = name.encode("utf8")
        values = [(0x80000000 | (index * width + i)) for i in range(width)]
        body = struct.pack("<2I", len(name_bytes), width) + name_bytes
        body += struct.pack(f"<{width}I", *values)
        packet(0, body)
    packet(1, bytes(ops))
    for kind, name in enumerate(("qqq0", "qqq1", "qqq2", "qq0", "qq1"), 2):
        values = streams[name]
        packet(kind, struct.pack(f"<{len(values)}I", *values))
    for kind in range(7, 11):
        packet(kind, b"")
    packet(11, struct.pack("<Q", 0))
    return bytes(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("terms", type=Path, help="JSON with width, modulus and rank-one terms")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        width, modulus, terms = validated_terms(json.loads(args.terms.read_text()))
        ops, streams = synthesize(width, terms)
        raw = kmb_bytes(width, ops, streams)
        with args.out.open("xb") as file:
            file.write(raw)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.exit(2, f"bilinear-synth: {exc}\n")
    print(json.dumps({"schema": "bilinear-synth-v1", "tier": "static model / projection",
                      "out": str(args.out), "sha256": hashlib.sha256(raw).hexdigest(),
                      "width": width, "modulus": modulus,
                      "tensor_precheck": True, "ccx": len(terms),
                      "cx": len(streams["qq0"]),
                      "next": "Run workbench bilinear-cert on the written KMB bytes."}))


if __name__ == "__main__":
    main()
