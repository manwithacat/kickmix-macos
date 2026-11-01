#!/usr/bin/env python3
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Describe exact rank and annihilator structure of an explicit bilinear tensor."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def rank_basis(vectors: list[int]) -> list[int]:
    pivots: dict[int, int] = {}
    for original in vectors:
        value = original
        while value:
            pivot = value.bit_length() - 1
            if pivot in pivots:
                value ^= pivots[pivot]
            else:
                pivots[pivot] = value
                break
    return [pivots[p] for p in sorted(pivots, reverse=True)]


def operator_columns(table: list[int], width: int, side: str, mask: int) -> list[int]:
    if side == "left":
        return [xor(table[i * width + j] for i in range(width) if mask >> i & 1)
                for j in range(width)]
    if side == "right":
        return [xor(table[i * width + j] for j in range(width) if mask >> j & 1)
                for i in range(width)]
    raise ValueError("side must be left or right")


def xor(values) -> int:
    result = 0
    for value in values:
        result ^= value
    return result


def kernel_basis(columns: list[int], width: int) -> list[int]:
    kernel = [mask for mask in range(1, 1 << width)
              if xor(columns[j] for j in range(width) if mask >> j & 1) == 0]
    basis = []
    span = {0}
    for mask in kernel:
        if mask not in span:
            basis.append(mask)
            span |= {value ^ mask for value in tuple(span)}
    return basis


def flattening_ranks(table: list[int], width: int) -> dict[str, int]:
    left = [sum(((table[i * width + j] >> k) & 1) << (j * width + k)
                for j in range(width) for k in range(width)) for i in range(width)]
    right = [sum(((table[i * width + j] >> k) & 1) << (i * width + k)
                 for i in range(width) for k in range(width)) for j in range(width)]
    output = [sum(((table[i * width + j] >> k) & 1) << (i * width + j)
                  for i in range(width) for j in range(width)) for k in range(width)]
    return {"left": len(rank_basis(left)), "right": len(rank_basis(right)),
            "output": len(rank_basis(output))}


def analyze(table: list[int], width: int, *, witness_limit: int = 8) -> dict:
    if not isinstance(width, int) or not 1 <= width <= 12:
        raise ValueError("width must be in 1..12")
    if len(table) != width * width or any(
        not isinstance(value, int) or not 0 <= value < (1 << width) for value in table
    ):
        raise ValueError("basis_products must contain width*width width-bit integers")
    sides = {}
    for side in ("left", "right"):
        rows = []
        for mask in range(1, 1 << width):
            columns = operator_columns(table, width, side, mask)
            basis = rank_basis(columns)
            rows.append({"mask": mask, "rank": len(basis),
                         "image_basis": basis,
                         "kernel_basis": kernel_basis(columns, width)})
        minimum = min(row["rank"] for row in rows)
        histogram = {str(rank): sum(row["rank"] == rank for row in rows)
                     for rank in range(width + 1) if any(row["rank"] == rank for row in rows)}
        minimum_rows = [row for row in rows if row["rank"] == minimum]
        sides[side] = {"rank_histogram": histogram, "minimum_rank": minimum,
                       "minimum_witness_count": len(minimum_rows),
                       "minimum_witnesses": minimum_rows[:witness_limit],
                       "rank_one_masks": [row["mask"] for row in rows if row["rank"] == 1],
                       "nonzero_annihilating_pairs": sum(
                           (1 << (width - row["rank"])) - 1 for row in rows)}
    if sides["left"]["nonzero_annihilating_pairs"] != (
        sides["right"]["nonzero_annihilating_pairs"]
    ):
        raise ValueError("left/right zero-pair counts disagree")
    return {"schema": "kickmix-bilinear-structure-v1",
            "tier": "static model / projection", "width": width,
            "flattening_ranks": flattening_ranks(table, width),
            "nonzero_annihilating_input_pairs": sides["left"]["nonzero_annihilating_pairs"],
            "sides": sides}


def run(source: Path, out: Path, witness_limit: int = 8) -> dict:
    if out.exists():
        raise FileExistsError(out)
    target = json.loads(source.read_text())
    result = analyze(target["basis_products"], target["width"],
                     witness_limit=witness_limit)
    result["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"report": str(out), "flattening_ranks": result["flattening_ranks"],
                      "minimum_left_rank": result["sides"]["left"]["minimum_rank"],
                      "minimum_right_rank": result["sides"]["right"]["minimum_rank"],
                      "zero_pairs": result["nonzero_annihilating_input_pairs"]}))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--witness-limit", type=int, default=8)
    args = parser.parse_args()
    run(args.target, args.out, args.witness_limit)
