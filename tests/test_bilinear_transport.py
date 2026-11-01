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
