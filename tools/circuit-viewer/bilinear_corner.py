#!/usr/bin/env python3
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Find and apply exact 3-to-2 L-corner reductions in bilinear KMBs.

The identity is (u,v,x^y)+(u,v2,y)+(u2,v,x)
              = (u^u2,v,x)+(u,v^v2,y) over GF(2).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bilinear_cert import analyze
from bilinear_mechanisms import digest, normalize, tensor_from_terms
from bilinear_synth import field_mul, kmb_bytes, synthesize, validated_terms
from workbench import Circuit


def opportunities(terms: list[dict]) -> list[dict]:
    """Return all strict-saving moves in stable normalized-term order."""
    moves = []
    for p, pivot in enumerate(terms):
        for q, arm_b in enumerate(terms):
            if p == q or pivot["a_mask"] != arm_b["a_mask"]:
                continue
            new_b = pivot["b_mask"] ^ arm_b["b_mask"]
            if not new_b:
                continue
            for r, arm_a in enumerate(terms):
                if r in (p, q) or pivot["b_mask"] != arm_a["b_mask"]:
                    continue
                new_a = pivot["a_mask"] ^ arm_a["a_mask"]
                if not new_a or pivot["output_mask"] != (
                    arm_b["output_mask"] ^ arm_a["output_mask"]
                ):
                    continue
                replacements = [
                    {"a_mask": new_a, "b_mask": pivot["b_mask"],
                     "output_mask": arm_a["output_mask"]},
                    {"a_mask": pivot["a_mask"], "b_mask": new_b,
                     "output_mask": arm_b["output_mask"]},
                ]
                moves.append({"term_indices": [p, q, r],
                              "source_terms": [pivot, arm_b, arm_a],
                              "replacement_terms": replacements,
                              "saving": 1})
    return moves


def reduce_corners(terms: list[dict], width: int, table: list[int],
                   max_moves: int = 32) -> tuple[list[dict], list[dict], list[dict]]:
    """Apply first available move repeatedly, checking the target tensor."""
    terms = normalize(terms)
    initial = opportunities(terms)
    history = []
    for _ in range(max_moves):
        moves = opportunities(terms)
        if not moves:
            break
        move = moves[0]
        prior = len(terms)
        removed = set(move["term_indices"])
        terms = normalize([term for i, term in enumerate(terms) if i not in removed]
                          + move["replacement_terms"])
        if len(terms) >= prior or tensor_from_terms(terms, width) != table:
            raise ValueError("L-corner move failed strict saving or tensor identity")
        history.append({"before_terms": prior, "after_terms": len(terms), **move})
    return terms, initial, history


def run(source: Path, a: str, b: str, target: str, modulus: int,
        tensor_json: Path | None, report_out: Path,
        candidate_out: Path | None = None, max_moves: int = 32) -> dict:
    if report_out.exists() or (candidate_out is not None and candidate_out.exists()):
        raise FileExistsError("report or candidate already exists")
    tensor = json.loads(tensor_json.read_text()) if tensor_json else None
    table = tensor["basis_products"] if isinstance(tensor, dict) else tensor
    source_report = analyze(Circuit(source), a, b, target, modulus,
                            expected_basis_products=table)
    width = source_report["width"]
    if table is None:
        table = [field_mul(1 << i, 1 << j, width, modulus)
                 for i in range(width) for j in range(width)]
    initial_terms = normalize(source_report["normalized_terms"])
    if tensor_from_terms(initial_terms, width) != table:
        raise ValueError("source term tensor differs from target")
    reduced, initial_moves, history = reduce_corners(initial_terms, width, table,
                                                      max_moves=max_moves)
    result = {"schema": "kickmix-bilinear-corner-v1",
              "tier": "static model / projection", "source_sha256": digest(source),
              "target_sha256": digest(tensor_json) if tensor_json else None,
              "width": width, "input_ccx": source_report["gate_counts"].get("ccx", 0),
              "input_normalized_terms": len(initial_terms),
              "initial_opportunities": initial_moves,
              "move_history": history, "final_terms": len(reduced),
              "final_term_list": reduced}
    if candidate_out is not None:
        _, _, checked = validated_terms({"width": width, "modulus": modulus,
            "basis_products": table, "terms": reduced})
        ops, streams = synthesize(width, checked)
        candidate_out.write_bytes(kmb_bytes(width, ops, streams))
        proof = analyze(Circuit(candidate_out), a, b, target, modulus,
                        expected_basis_products=table)
        result.update({"candidate_sha256": digest(candidate_out),
                       "candidate_path": str(candidate_out),
                       "candidate_gate_counts": proof["gate_counts"],
                       "candidate_tensor_identity": proof["tensor_identity"]})
    report_out.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--a", default="a")
    parser.add_argument("--b", default="b")
    parser.add_argument("--target", default="t")
    parser.add_argument("--modulus", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--tensor-json", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--candidate-out", type=Path)
    parser.add_argument("--max-moves", type=int, default=32)
    args = parser.parse_args()
    result = run(args.source, args.a, args.b, args.target, args.modulus,
                 args.tensor_json, args.out, args.candidate_out, args.max_moves)
    print(json.dumps({"report": str(args.out), "input_ccx": result["input_ccx"],
                      "initial_opportunities": len(result["initial_opportunities"]),
                      "final_terms": result["final_terms"],
                      "candidate_sha256": result.get("candidate_sha256")}))


if __name__ == "__main__":
    main()
