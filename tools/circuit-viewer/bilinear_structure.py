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
    """Nullspace basis by elimination, without enumerating 2**width masks."""
    pivots: dict[int, tuple[int, int]] = {}
    dependencies = []
    for j in range(width):
        value, code = columns[j], 1 << j
        while value:
            pivot = value.bit_length() - 1
            if pivot in pivots:
                prior_value, prior_code = pivots[pivot]
                value ^= prior_value
                code ^= prior_code
            else:
                pivots[pivot] = (value, code)
                break
        if not value:
            dependencies.append(code)
    return dependencies


def flattening_ranks(table: list[int], width: int) -> dict[str, int]:
    left = [sum(((table[i * width + j] >> k) & 1) << (j * width + k)
                for j in range(width) for k in range(width)) for i in range(width)]
    right = [sum(((table[i * width + j] >> k) & 1) << (i * width + k)
                 for i in range(width) for k in range(width)) for j in range(width)]
    output = [sum(((table[i * width + j] >> k) & 1) << (i * width + j)
                  for i in range(width) for j in range(width)) for k in range(width)]
    return {"left": len(rank_basis(left)), "right": len(rank_basis(right)),
            "output": len(rank_basis(output))}


def common_annihilator_basis(table: list[int], width: int, side: str,
                             masks: list[int]) -> list[int]:
    if not masks:
        return []
    operators = [operator_columns(table, width, side, mask) for mask in masks]
    joint_columns = [sum(operator[j] << (index * width)
                         for index, operator in enumerate(operators))
                     for j in range(width)]
    return kernel_basis(joint_columns, width)


def product(table: list[int], width: int, left: int, right: int) -> int:
    return xor(table[i * width + j] for i in range(width) for j in range(width)
               if left >> i & right >> j & 1)


def split_witness(table: list[int], width: int, left_rank_one: list[int],
                  right_rank_one: list[int]) -> dict | None:
    """Expose a verified small rank-one interaction block, when present."""
    count = len(left_rank_one)
    if not 1 <= count <= 4 or len(right_rank_one) != count:
        return None
    left_core = common_annihilator_basis(table, width, "right", right_rank_one)
    right_core = common_annihilator_basis(table, width, "left", left_rank_one)
    if (len(left_core) != width - count or len(right_core) != width - count or
        len(rank_basis(left_core + left_rank_one)) != width or
        len(rank_basis(right_core + right_rank_one)) != width):
        return None
    core_products = [product(table, width, a, b)
                     for a in left_core for b in right_core]
    cross_products = [product(table, width, a, b)
                      for a in left_rank_one for b in right_rank_one]
    core_output_basis = rank_basis(core_products)
    cross_output_basis = rank_basis(cross_products)
    output_split = (len(core_output_basis) + len(cross_output_basis) == width
                    and len(rank_basis(core_output_basis + cross_output_basis)) == width)
    return {"left_core_basis": left_core, "right_core_basis": right_core,
            "left_interaction_basis": left_rank_one,
            "right_interaction_basis": right_rank_one,
            "core_basis_products": core_products,
            "interaction_basis_products": cross_products,
            "core_output_basis": core_output_basis,
            "interaction_output_basis": cross_output_basis,
            "mixed_products_zero": all(product(table, width, a, b) == 0
                for a in left_core for b in right_rank_one) and all(
                product(table, width, a, b) == 0
                for a in left_rank_one for b in right_core),
            "output_spaces_direct_sum": output_split}


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
    left_rank_one = sides["left"]["rank_one_masks"]
    right_rank_one = sides["right"]["rank_one_masks"]
    cross = [{"left_mask": left, "right_mask": right,
              "output_mask": xor(operator_columns(table, width, "left", left)[j]
                                 for j in range(width) if right >> j & 1)}
             for left in left_rank_one[:witness_limit]
             for right in right_rank_one[:witness_limit]]
    return {"schema": "kickmix-bilinear-structure-v1",
            "tier": "static model / projection", "width": width,
            "flattening_ranks": flattening_ranks(table, width),
            "nonzero_annihilating_input_pairs": sides["left"]["nonzero_annihilating_pairs"],
            "rank_one_cross_products": cross,
            "common_annihilator_of_rank_one_left": common_annihilator_basis(
                table, width, "left", left_rank_one),
            "common_annihilator_of_rank_one_right": common_annihilator_basis(
                table, width, "right", right_rank_one),
            "split_witness": split_witness(table, width, left_rank_one,
                                            right_rank_one),
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
