# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Common-parity factoring exposes exact term-saving mechanisms."""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/circuit-viewer"))
from bilinear_mechanisms import compress, factor_columns, opportunities, tensor_from_terms  # noqa: E402


def test_factor_columns_reconstructs_random_binary_matrices():
    rng = random.Random(8114)
    for width in range(2, 9):
        for _ in range(100):
            columns = [rng.randrange(1 << width) for _ in range(width)]
            basis, inputs = factor_columns(columns)
            assert len(basis) == len(inputs)
            reconstructed = [0] * width
            for output_mask, input_mask in zip(basis, inputs):
                for bit in range(width):
                    if input_mask >> bit & 1:
                        reconstructed[bit] ^= output_mask
            assert reconstructed == columns


def test_common_parity_rank_compression_preserves_explicit_tensor():
    terms = [
        {"a_mask": 1, "b_mask": 1, "output_mask": 1},
        {"a_mask": 1, "b_mask": 2, "output_mask": 2},
        {"a_mask": 1, "b_mask": 4, "output_mask": 3},
    ]
    table = tensor_from_terms(terms, 3)
    moves = opportunities(terms, 3)
    assert moves[0]["fixed_axis"] == "a_mask"
    assert moves[0]["saving"] == 1
    reduced, initial, history = compress(terms, 3, table)
    assert len(initial) == 1
    assert len(history) == 1
    assert len(reduced) == 2
    assert tensor_from_terms(reduced, 3) == table
