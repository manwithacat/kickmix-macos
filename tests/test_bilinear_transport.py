# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Field-isotopy witnesses must transport a byte-certified core exactly."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/circuit-viewer"))
from bilinear_basis_explorer import apply  # noqa: E402
from bilinear_synth import field_mul  # noqa: E402
from bilinear_synth import kmb_bytes, synthesize, validated_terms  # noqa: E402
from bilinear_transport import run  # noqa: E402


def test_transports_gf16_core_to_explicit_tensor(tmp_path):
    a, b, out = [3, 2, 4, 8], [1, 6, 4, 8], [1, 2, 12, 8]
    table = [apply(out, field_mul(apply(a, 1 << i), apply(b, 1 << j), 4, 0x13))
             for i in range(4) for j in range(4)]
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"width": 4, "modulus": 0x13,
                                  "basis_products": table}))
    core = Path(__file__).parent / "fixtures/gf16_karatsuba.kmb"
    candidate, report_path = tmp_path / "candidate.kmb", tmp_path / "report.json"
    report = run(target, core, candidate, report_path)
    assert report["candidate_tensor_identity"] is True
    assert report["candidate_gate_counts"]["ccx"] == 9
    assert report["witness"]["verified_pairs"] == 256
    assert len(report["candidate_terms"]) == 9
    with pytest.raises(FileExistsError):
        run(target, core, candidate, report_path)


def test_gf12_basis_certificate_transports_schoolbook_core(tmp_path):
    width, modulus = 12, 0x1053
    core_terms = [{"a_mask": 1 << i, "b_mask": 1 << j,
                   "output_mask": field_mul(1 << i, 1 << j, width, modulus)}
                  for i in range(width) for j in range(width)]
    _, _, checked = validated_terms({"width": width, "modulus": modulus,
                                     "terms": core_terms})
    ops, streams = synthesize(width, checked)
    core = tmp_path / "gf12-schoolbook.kmb"
    core.write_bytes(kmb_bytes(width, ops, streams))
    a = [(1 << i) ^ (1 << (i - 1) if i else 0) for i in range(width)]
    b = [(1 << i) ^ (1 << (i - 2) if i >= 2 else 0) for i in range(width)]
    out = [1 << ((i + 1) % width) for i in range(width)]
    table = [apply(out, field_mul(apply(a, 1 << i), apply(b, 1 << j),
                                  width, modulus))
             for i in range(width) for j in range(width)]
    target = tmp_path / "gf12-target.json"
    target.write_text(json.dumps({"width": width, "modulus": modulus,
                                  "basis_products": table}))
    result = run(target, core, tmp_path / "gf12-candidate.kmb",
                 tmp_path / "gf12-report.json")
    assert result["witness"]["verification_mode"] == "complete_bilinear_basis_pairs"
    assert result["witness"]["verified_pairs"] == 144
    assert result["candidate_tensor_identity"] is True
    assert result["candidate_gate_counts"]["ccx"] == 144
