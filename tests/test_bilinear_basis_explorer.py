# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Field-isotopy witnesses must reproduce an explicit tensor exactly."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/circuit-viewer"))
from bilinear_basis_explorer import apply, recover  # noqa: E402
from bilinear_synth import field_mul  # noqa: E402


def test_recovers_transformed_field_and_rejects_mutation():
    a = [3, 2, 4, 8]
    b = [1, 6, 4, 8]
    out = [1, 2, 12, 8]
    table = [apply(out, field_mul(apply(a, 1 << i), apply(b, 1 << j), 4, 0x13))
             for i in range(4) for j in range(4)]
    report = recover(4, 0x13, table)
    assert report["verified_pairs"] == 256
    assert report["decomposition_terms"] is None
    for x in range(16):
        for y in range(16):
            expected = apply(out, field_mul(apply(a, x), apply(b, y), 4, 0x13))
            actual = apply(report["output_columns"], field_mul(
                apply(report["a_columns"], x),
                apply(report["b_columns"], y), 4, 0x13))
            assert actual == expected
    mutated = table.copy()
    mutated[0] ^= 1
    with pytest.raises(ValueError):
        recover(4, 0x13, mutated)
