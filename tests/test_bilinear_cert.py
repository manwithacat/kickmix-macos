# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Circuit-byte certificates for small field multipliers."""

import struct
import sys
from pathlib import Path

import numpy as np
import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools/circuit-viewer"
sys.path.insert(0, str(TOOLS))
import bilinear_cert as bc  # noqa: E402
import bilinear_project as bp  # noqa: E402
import workbench as wb  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.mark.parametrize("name,rank,cx,modulus,swap", [
    ("gf16_karatsuba.kmb", 9, 52, 0x13, 0),
    ("gf16_schoolbook.kmb", 16, 12, 0x13, 0),
    ("gf8_unannotated.kmb", 6, 16, 0xB, 0),
    ("gf64_tower15.kmb", 15, 174, 0x49, 0),
    ("gf4096_unannotated.kmb", 42, 920, 0x1053, 30),
])
def test_certificate_from_circuit_bytes(name, rank, cx, modulus, swap):
    report = bc.analyze(wb.Circuit(FIXTURES / name), "a", "b", "t", modulus)
    assert report["decomposition_length"] == rank
    assert report["bilinear_rank_upper_bound"] == rank
    assert report["lower_bound_proved"] is False
    assert report["physical_gates"] == rank + cx + swap
    assert report["gate_counts"]["ccx"] == rank
    assert report["gate_counts"]["cx"] == cx
    assert report["gate_counts"].get("swap", 0) == swap
    assert report["tensor_identity"] and report["affine_identity"]
    assert report["ancilla"] == report["phase_operations"] == 0
    assert len(report["terms"]) == rank


def test_wrong_modulus_and_unsupported_gate_fail_closed(tmp_path):
    original = FIXTURES / "gf16_karatsuba.kmb"
    with pytest.raises(ValueError, match="tensor mismatch"):
        bc.analyze(wb.Circuit(original), "a", "b", "t", 0x19)
    src = wb.Circuit(original)
    data = bytearray(original.read_bytes())
    op_offset, _, _ = src.offsets["op_types"]
    ccx_position = next(i for i, typ in enumerate(src.ops) if typ == 49)
    data[op_offset + ccx_position] = 48  # unsupported CCZ
    mutated = tmp_path / "unsupported.kmb"
    mutated.write_bytes(data)
    with pytest.raises(ValueError, match="unsupported operation"):
        bc.analyze(wb.Circuit(mutated), "a", "b", "t", 0x13)


def test_tampered_product_target_fails_tensor_check(tmp_path):
    original = FIXTURES / "gf16_karatsuba.kmb"
    src = wb.Circuit(original)
    data = bytearray(original.read_bytes())
    target_offset, _, _ = src.offsets["qqq2"]
    struct.pack_into("<I", data, target_offset, 9)
    mutated = tmp_path / "bad-target.kmb"
    mutated.write_bytes(data)
    with pytest.raises(ValueError, match="tensor mismatch"):
        bc.analyze(wb.Circuit(mutated), "a", "b", "t", 0x13)


def test_nonlinear_control_fails_closed(tmp_path):
    original = FIXTURES / "gf16_karatsuba.kmb"
    src = wb.Circuit(original)
    data = bytearray(original.read_bytes())
    target_offset, _, _ = src.offsets["qqq2"]
    struct.pack_into("<I", data, target_offset, 1)
    mutated = tmp_path / "nonlinear-control.kmb"
    mutated.write_bytes(data)
    with pytest.raises(ValueError, match="nonlinear CCX control"):
        bc.analyze(wb.Circuit(mutated), "a", "b", "t", 0x13)


def test_cancelling_pair_does_not_claim_optimal_rank():
    source = wb.Circuit(FIXTURES / "gf16_karatsuba.kmb")

    class WithCancellingPair:
        def __init__(self):
            self.path = source.path
            self.qubits = source.qubits
            self.bits = source.bits
            self.tags = source.tags
            self.register_ids = source.register_ids
            self.ops = np.concatenate((source.ops, np.array([49, 49], dtype=np.uint8)))

        def stream(self, name):
            original = source.stream(name)
            if name in ("qqq0", "qqq1", "qqq2"):
                extra = {"qqq0": 0, "qqq1": 4, "qqq2": 8}[name]
                return np.concatenate((original, np.array([extra, extra], dtype=np.uint32)))
            return original

    report = bc.analyze(WithCancellingPair(), "a", "b", "t", 0x13)
    assert report["tensor_identity"]
    assert report["decomposition_length"] == 11
    assert report["bilinear_rank_upper_bound"] == 9
    assert report["lower_bound_proved"] is False


def test_scalar_projection_exact_rank_and_witness():
    gf4096 = bc.analyze(wb.Circuit(FIXTURES / "gf4096_unannotated.kmb"),
                        "a", "b", "t", 0x1053)
    scalar = bp.analyze(gf4096, 0x80)
    assert scalar["existing_terms_selected"] == 12
    assert scalar["exact_scalar_bilinear_rank"] == 12
    assert scalar["factorization_verified"]
    assert len(scalar["rank_factorization"]) == 12
    gf16 = bc.analyze(wb.Circuit(FIXTURES / "gf16_karatsuba.kmb"),
                      "a", "b", "t", 0x13)
    assert bp.analyze(gf16, 1)["exact_scalar_bilinear_rank"] == 4
    with pytest.raises(ValueError, match="output mask"):
        bp.analyze(gf4096, 0)
    with pytest.raises(ValueError, match="output mask"):
        bp.analyze(gf4096, 1 << 12)
