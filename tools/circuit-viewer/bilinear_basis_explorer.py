#!/usr/bin/env python3
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Recover a checkable field-isotopy witness from an explicit bilinear tensor.

For an invertible transform F(a,b)=H(A(a)*B(b)), return the three linear maps
A, B and H as GF(2) column vectors. No rank-one decomposition is produced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from bilinear_synth import field_mul


def apply(columns: list[int], value: int) -> int:
    result = 0
    for i, column in enumerate(columns):
        if value >> i & 1:
            result ^= column
    return result


def recover(width: int, modulus: int, basis_products: list[int]) -> dict:
    if not isinstance(width, int) or not 1 <= width <= 8:
        raise ValueError("width must be 1..8 for bounded exhaustive recovery")
    limit = 1 << width
    if modulus.bit_length() != width + 1 or not modulus & 1:
        raise ValueError("invalid field modulus")
    if len(basis_products) != width * width or any(
        not isinstance(x, int) or not 0 <= x < limit for x in basis_products
    ):
        raise ValueError("basis_products must contain width*width width-bit integers")

    def product(a: int, b: int) -> int:
        result = 0
        for i in range(width):
            if a >> i & 1:
                for j in range(width):
                    if b >> j & 1:
                        result ^= basis_products[i * width + j]
        return result

    # Normalize the isotope at external inputs 1,1. The two maps are
    # invertible whenever the target is an invertible transform of a field.
    left = [product(1 << i, 1) for i in range(width)]
    right = [product(1, 1 << j) for j in range(width)]
    left_inverse = {apply(left, x): x for x in range(limit)}
    right_inverse = {apply(right, x): x for x in range(limit)}
    if len(left_inverse) != limit or len(right_inverse) != limit:
        raise ValueError("normalizing slice is singular; no field-isotopy witness")
    identity = product(1, 1)

    def star(x: int, y: int) -> int:
        return product(left_inverse[x], right_inverse[y])

    def star_pow(x: int, exponent: int) -> int:
        result = identity
        for _ in range(exponent):
            result = star(result, x)
        return result

    # Find a root of the supplied field polynomial in the normalized output
    # field. Its powers supply a canonical-basis embedding H.
    coefficients = [i for i in range(width + 1) if modulus >> i & 1]
    root = next((x for x in range(1, limit)
                 if not _xor(star_pow(x, i) for i in coefficients)), None)
    if root is None:
        raise ValueError("normalized tensor has no root of the field modulus")
    output = [star_pow(root, i) for i in range(width)]
    output_inverse = {apply(output, x): x for x in range(limit)}
    if len(output_inverse) != limit:
        raise ValueError("root powers do not span the output field")
    a_columns = [output_inverse[x] for x in left]
    b_columns = [output_inverse[x] for x in right]
    for a in range(limit):
        for b in range(limit):
            expected = apply(output, field_mul(apply(a_columns, a),
                                               apply(b_columns, b), width, modulus))
            if product(a, b) != expected:
                raise ValueError(f"field-isotopy check failed at ({a},{b})")
    serialized = json.dumps(basis_products, separators=(",", ":")).encode()
    return {"schema": "bilinear-field-isotopy-witness-v1",
            "tier": "static model / projection", "width": width, "modulus": modulus,
            "tensor_sha256": hashlib.sha256(serialized).hexdigest(),
            "normalizing_inputs": [1, 1], "identity": identity,
            "root": root, "a_columns": a_columns, "b_columns": b_columns,
            "output_columns": output, "verified_pairs": limit * limit,
            "decomposition_terms": None}


def _xor(values) -> int:
    result = 0
    for value in values:
        result ^= value
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="JSON with width, modulus, basis_products")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    data = json.loads(args.target.read_text())
    result = recover(data["width"], data["modulus"], data["basis_products"])
    with args.out.open("x") as file:
        json.dump(result, file, indent=2)
        file.write("\n")
    print(json.dumps({"report": str(args.out), "verified_pairs": result["verified_pairs"],
                      "tensor_sha256": result["tensor_sha256"]}))


if __name__ == "__main__":
    main()
