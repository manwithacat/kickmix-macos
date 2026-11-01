# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Exact rank profiles expose structural zero divisors."""

import sys
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
    for side in ("left", "right"):
        assert report["sides"][side]["rank_histogram"] == {"1": 2, "2": 1}
        assert report["sides"][side]["rank_one_masks"] == [1, 2]


def test_field_has_full_rank_for_every_nonzero_operand():
    table = [field_mul(1 << i, 1 << j, 2, 0x7)
             for i in range(2) for j in range(2)]
    report = analyze(table, 2)
    assert report["nonzero_annihilating_input_pairs"] == 0
    assert report["rank_one_cross_products"] == []
    for side in ("left", "right"):
        assert report["sides"][side]["rank_histogram"] == {"2": 3}
        assert report["sides"][side]["rank_one_masks"] == []


def test_kernel_basis_spans_nullspace():
    assert kernel_basis([1, 1, 0], 3) == [3, 4]
