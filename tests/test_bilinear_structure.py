# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Exact rank profiles expose structural zero divisors."""

import sys
import random
from functools import reduce
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/circuit-viewer"))
from bilinear_structure import analyze, kernel_basis  # noqa: E402
from bilinear_synth import field_mul  # noqa: E402


def test_two_independent_products_have_rank_one_zero_divisors():
    report = analyze([1, 0, 0, 2], 2)
    assert report["flattening_ranks"] == {"left": 2, "right": 2, "output": 2}
    assert report["nonzero_annihilating_input_pairs"] == 2
    assert report["common_annihilator_of_rank_one_left"] == []
    assert report["common_annihilator_of_rank_one_right"] == []
    assert [row["output_mask"] for row in report["rank_one_cross_products"]] == [1, 0, 0, 2]
    assert report["split_witness"]["mixed_products_zero"]
    assert report["split_witness"]["output_spaces_direct_sum"]
    for side in ("left", "right"):
        assert report["sides"][side]["rank_histogram"] == {"1": 2, "2": 1}
        assert report["sides"][side]["rank_one_masks"] == [1, 2]


def test_field_has_full_rank_for_every_nonzero_operand():
    table = [field_mul(1 << i, 1 << j, 2, 0x7)
             for i in range(2) for j in range(2)]
    report = analyze(table, 2)
    assert report["nonzero_annihilating_input_pairs"] == 0
    assert report["rank_one_cross_products"] == []
    assert report["split_witness"] is None
    for side in ("left", "right"):
        assert report["sides"][side]["rank_histogram"] == {"2": 3}
        assert report["sides"][side]["rank_one_masks"] == []


def test_kernel_basis_spans_nullspace():
    assert kernel_basis([1, 1, 0], 3) == [3, 4]


def test_kernel_elimination_matches_exhaustive_nullspace():
    rng = random.Random(20261101)
    for width in range(1, 7):
        for _ in range(20):
            columns = [rng.randrange(1 << width) for _ in range(width)]
            basis = kernel_basis(columns, width)
            span = {0}
            for vector in basis:
                span |= {mask ^ vector for mask in tuple(span)}
            exhaustive = {mask for mask in range(1 << width)
                          if not reduce(int.__xor__,
                                        (columns[j] for j in range(width)
                                         if mask >> j & 1), 0)}
            assert span == exhaustive


def test_matrix_product_plus_two_scalars_has_four_dimensional_core():
    def f(a, b):
        x = lambda v, i: (v >> i) & 1
        bits = [
            x(a, 0) & x(b, 0) ^ x(a, 1) & x(b, 2),
            x(a, 0) & x(b, 1) ^ x(a, 1) & x(b, 3),
            x(a, 2) & x(b, 0) ^ x(a, 3) & x(b, 2),
            x(a, 2) & x(b, 1) ^ x(a, 3) & x(b, 3),
            x(a, 4) & x(b, 4), x(a, 5) & x(b, 5),
        ]
        return sum(bit << i for i, bit in enumerate(bits))
    table = [f(1 << i, 1 << j) for i in range(6) for j in range(6)]
    split = analyze(table, 6)["split_witness"]
    assert split is not None
    assert len(split["left_core_basis"]) == 4
    assert len(split["right_core_basis"]) == 4
    assert len(split["core_output_basis"]) == 4
    assert len(split["interaction_output_basis"]) == 2
    assert split["mixed_products_zero"] and split["output_spaces_direct_sum"]
