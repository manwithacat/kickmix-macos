#!/usr/bin/env python3
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Transport a certified field-multiplication core to an exact tensor target."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from bilinear_basis_explorer import apply, recover
from bilinear_cert import analyze
from bilinear_synth import kmb_bytes, synthesize, validated_terms
from workbench import Circuit


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def transpose_mask(columns: list[int], mask: int) -> int:
    """Pull an output parity mask back through a GF(2) linear map."""
    return sum(((column & mask).bit_count() & 1) << i
               for i, column in enumerate(columns))


def transport_terms(terms: list[dict], witness: dict) -> list[dict]:
    return [{"a_mask": transpose_mask(witness["a_columns"], term["a_mask"]),
             "b_mask": transpose_mask(witness["b_columns"], term["b_mask"]),
             "output_mask": apply(witness["output_columns"], term["output_mask"])}
            for term in terms]


def run(target: Path, core: Path, candidate_out: Path, report_out: Path) -> dict:
    if candidate_out.exists() or report_out.exists():
        raise FileExistsError("candidate or report already exists")
    data = json.loads(target.read_text())
    width, modulus, table = (data[key] for key in
                             ("width", "modulus", "basis_products"))
    witness = recover(width, modulus, table)
    core_report = analyze(Circuit(core), "a", "b", "t", modulus)
    if core_report["width"] != width:
        raise ValueError("core width differs from target")
    terms = transport_terms(core_report["normalized_terms"], witness)
    _, _, checked = validated_terms({"width": width, "modulus": modulus,
                                     "basis_products": table, "terms": terms})
    ops, streams = synthesize(width, checked)
    candidate_out.write_bytes(kmb_bytes(width, ops, streams))
    proof = analyze(Circuit(candidate_out), "a", "b", "t", modulus,
                    expected_basis_products=table)
    report = {"schema": "kickmix-bilinear-transport-v1",
              "tier": "static model / projection", "target_sha256": digest(target),
              "core_sha256": digest(core), "candidate_sha256": digest(candidate_out),
              "witness": witness, "core_gate_counts": core_report["gate_counts"],
              "candidate_gate_counts": proof["gate_counts"],
              "candidate_tensor_identity": proof["tensor_identity"],
              "candidate_terms": terms}
    report_out.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="explicit target JSON")
    parser.add_argument("--core", type=Path, required=True,
                        help="certified standard multiplication KMB")
    parser.add_argument("--candidate-out", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="mechanism report")
    args = parser.parse_args()
    report = run(args.target, args.core, args.candidate_out, args.out)
    print(json.dumps({"report": str(args.out),
                      "candidate_sha256": report["candidate_sha256"],
                      "candidate_gate_counts": report["candidate_gate_counts"],
                      "verified_pairs": report["witness"]["verified_pairs"]}))


if __name__ == "__main__":
    main()
