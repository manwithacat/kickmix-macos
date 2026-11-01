# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""The term compiler must emit KMB bytes accepted by the independent certificate."""

import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools/circuit-viewer"
sys.path.insert(0, str(TOOLS))
import bilinear_cert as cert  # noqa: E402
import bilinear_synth as synth  # noqa: E402
import workbench as wb  # noqa: E402


def test_certificate_terms_roundtrip_to_kmb(tmp_path):
    fixture = Path(__file__).parent / "fixtures/gf16_karatsuba.kmb"
    original = cert.analyze(wb.Circuit(fixture), "a", "b", "t", 0x13)
    width, _, terms = synth.validated_terms(original)
    ops, streams = synth.synthesize(width, terms)
    path = tmp_path / "roundtrip.kmb"
    path.write_bytes(synth.kmb_bytes(width, ops, streams))
    reconstructed = cert.analyze(wb.Circuit(path), "a", "b", "t", 0x13)
    assert reconstructed["tensor_identity"] and reconstructed["affine_identity"]
    assert reconstructed["decomposition_length"] == 9
    assert reconstructed["gate_counts"]["ccx"] == 9


def test_synth_rejects_wrong_tensor():
    with pytest.raises(ValueError, match="tensor mismatch"):
        synth.validated_terms({"width": 4, "modulus": 0x13,
                               "terms": [{"a_mask": 1, "b_mask": 1, "output_mask": 1}]})


def test_explicit_tensor_roundtrip_and_wrong_contract(tmp_path):
    original = cert.analyze(wb.Circuit(Path(__file__).parent / "fixtures/gf16_karatsuba.kmb"),
                            "a", "b", "t", 0x13)

    def swap_low_bits(value):
        return (value & ~3) | ((value & 1) << 1) | ((value & 2) >> 1)

    basis_products = [swap_low_bits(synth.field_mul(1 << i, 1 << j, 4, 0x13))
                      for i in range(4) for j in range(4)]
    terms = [{"a_mask": term["a_mask"], "b_mask": term["b_mask"],
              "output_mask": swap_low_bits(term["output_mask"])}
             for term in original["terms"]]
    width, _, checked = synth.validated_terms({"width": 4, "modulus": 0x13,
        "basis_products": basis_products, "terms": terms})
    ops, streams = synth.synthesize(width, checked)
    path = tmp_path / "transformed.kmb"
    path.write_bytes(synth.kmb_bytes(width, ops, streams))
    report = cert.analyze(wb.Circuit(path), "a", "b", "t", 0x13,
                          expected_basis_products=basis_products)
    assert report["target_tensor_kind"] == "explicit"
    assert report["gate_counts"]["ccx"] == 9
    with pytest.raises(ValueError, match="tensor mismatch"):
        cert.analyze(wb.Circuit(path), "a", "b", "t", 0x13)
