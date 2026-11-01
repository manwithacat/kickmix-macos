# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""The sieve must keep exact frontier evidence and quarantine bad KMBs."""

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/circuit-viewer"))
from bilinear_sieve import finalize, scan, sha  # noqa: E402


def test_scan_review_and_frontier_guard(tmp_path):
    fixture = Path(__file__).parent / "fixtures"
    challenge = tmp_path / "challenge"
    challenge.mkdir()
    shutil.copyfile(fixture / "gf16_schoolbook.kmb", challenge / "start.kmb")
    row = {"id": "gf16", "kmb": "start.kmb", "sha256": sha(challenge / "start.kmb"),
           "width": 4, "modulus": 0x13, "start_ccx": 16,
           "proven_bilinear_target": 9}
    manifest = challenge / "manifest.json"
    manifest.write_text(json.dumps({"instances": [row]}))
    pool = tmp_path / "pool"
    pool.mkdir()
    shutil.copyfile(fixture / "gf16_schoolbook.kmb", pool / "schoolbook.kmb")
    shutil.copyfile(fixture / "gf16_karatsuba.kmb", pool / "optimum.kmb")
    (pool / "truncated.kmb").write_bytes(b"bad")
    scanned = tmp_path / "scan"
    summary = scan(manifest, "gf16", pool, scanned, review_size=2)
    assert summary["input_count"] == 3
    assert summary["valid_count"] == 2
    assert summary["invalid_count"] == 1
    assert summary["best_ccx"] == 9
    packet = json.loads((scanned / "review_packet.json").read_text())
    assert {row["ccx"] for row in packet["candidates"]} == {9, 16}
    assert packet["candidates"][0]["selection_reason"] == "best_exact_ccx"
    assert packet["candidates"][1]["cost_dominated_by"] == []  # lower CX
    assert len(packet["pairwise_term_comparison"]) == 1

    # An agent may reject the best candidate as uninteresting, but the exact
    # frontier remains in the returned set and its reviewer override is clear.
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({"decisions": [
        {"id": row["id"], "disposition": "discard" if row["ccx"] == 9 else "keep",
         "reason": "Keep this decision visible for later agent review."}
        for row in packet["candidates"]]}))
    result = finalize(scanned / "audit.json", scanned / "review_packet.json",
                      decisions, tmp_path / "final", max_shortlist=2)
    assert {row["ccx"] for row in result["shortlist"]} == {9, 16}
    assert next(r for r in result["shortlist"] if r["ccx"] == 9)[
        "mandatory_exact_frontier"]

    tampered = tmp_path / "tampered.json"
    packet["candidates"][0]["term_signature"] = "wrong"
    tampered.write_text(json.dumps(packet))
    with pytest.raises(ValueError, match="review packet"):
        finalize(scanned / "audit.json", tampered, decisions, tmp_path / "bad")
