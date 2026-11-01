# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""L-corner reductions preserve explicit bilinear tensors."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/circuit-viewer"))
from bilinear_corner import opportunities, reduce_corners  # noqa: E402
from bilinear_mechanisms import tensor_from_terms  # noqa: E402


def test_three_to_two_l_corner_identity():
    terms = [
        {"a_mask": 1, "b_mask": 8, "output_mask": 53},
        {"a_mask": 1, "b_mask": 16, "output_mask": 3},
        {"a_mask": 18, "b_mask": 8, "output_mask": 54},
    ]
    table = tensor_from_terms(terms, 6)
    moves = opportunities(terms)
    assert moves[0]["term_indices"] == [0, 1, 2]
    assert len(moves[0]["replacement_terms"]) == 2
    reduced, initial, history = reduce_corners(terms, 6, table)
    assert len(initial) == 1 and len(history) == 1
    assert len(reduced) == 2
    assert tensor_from_terms(reduced, 6) == table


def test_no_corner_is_noop():
    terms = [{"a_mask": 1, "b_mask": 2, "output_mask": 3}]
    reduced, initial, history = reduce_corners(terms, 3, tensor_from_terms(terms, 3))
    assert reduced == terms and initial == [] and history == []
