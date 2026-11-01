# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Model-origin carrier extensions are exact GF(2) tensor identities."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/circuit-viewer"))
from bilinear_carrier import opportunities, reduce_carriers  # noqa: E402
from bilinear_mechanisms import tensor_from_terms  # noqa: E402


def one_carrier_terms():
    return [
        {"a_mask": 40, "b_mask": 16, "output_mask": 52},
        {"a_mask": 40, "b_mask": 1, "output_mask": 22},
        {"a_mask": 16, "b_mask": 17, "output_mask": 52},
    ]


def two_carrier_terms():
    return [
        {"a_mask": 40, "b_mask": 32, "output_mask": 47},
        {"a_mask": 40, "b_mask": 8, "output_mask": 49},
        {"a_mask": 21, "b_mask": 40, "output_mask": 46},
        {"a_mask": 22, "b_mask": 40, "output_mask": 1},
    ]


def test_one_carrier_extension():
    terms = one_carrier_terms()
    move = next(m for m in opportunities(terms) if m["fixed_axis"] == "a_mask")
    assert sorted((t["a_mask"], t["b_mask"], t["output_mask"])
                  for t in move["replacement_terms"]) == [(40, 1, 34), (56, 17, 52)]
    table = tensor_from_terms(terms, 6)
    reduced, _, history = reduce_carriers(terms, 6, table)
    assert len(reduced) == 2 and len(history) == 1
    assert tensor_from_terms(reduced, 6) == table


def test_two_carrier_extension_and_axis_symmetry():
    for terms in (two_carrier_terms(), [
        {"a_mask": t["b_mask"], "b_mask": t["a_mask"],
         "output_mask": t["output_mask"]} for t in two_carrier_terms()]):
        table = tensor_from_terms(terms, 6)
        reduced, _, history = reduce_carriers(terms, 6, table)
        assert len(reduced) == 3 and len(history) == 1
        assert history[0]["carrier_count"] == 2
        assert tensor_from_terms(reduced, 6) == table
