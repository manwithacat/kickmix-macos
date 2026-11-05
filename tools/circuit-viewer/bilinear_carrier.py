#!/usr/bin/env python3
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Find exact one- or two-carrier term eliminations in bilinear tensors."""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

from bilinear_cert import analyze
from bilinear_mechanisms import digest, normalize, tensor_from_terms
from bilinear_synth import kmb_bytes, synthesize, validated_terms
from workbench import Circuit


def opportunities(terms: list[dict], max_carriers: int = 2) -> list[dict]:
    """List exact algebraic moves in fixed axis/index order."""
    moves = []
    for fixed, varying in (("a_mask", "b_mask"), ("b_mask", "a_mask")):
        for p, pivot in enumerate(terms):
            for q, compensator in enumerate(terms):
                if p == q or pivot[fixed] != compensator[fixed]:
                    continue
                carrier_variable = pivot[varying] ^ compensator[varying]
                compensated_output = pivot["output_mask"] ^ compensator["output_mask"]
                if not carrier_variable:
                    continue
                candidates = [i for i, term in enumerate(terms)
                              if i not in (p, q) and term[varying] == carrier_variable
                              and (term[fixed] ^ pivot[fixed])]
                for size in range(1, min(max_carriers, len(candidates)) + 1):
                    for carrier_indices in itertools.combinations(candidates, size):
                        weight = 0
                        for i in carrier_indices:
                            weight ^= terms[i]["output_mask"]
                        if weight != pivot["output_mask"]:
                            continue
                        replacements = [
                            {**terms[i], fixed: terms[i][fixed] ^ pivot[fixed]}
                            for i in carrier_indices]
                        if compensated_output:
                            replacements.append({**compensator,
                                                 "output_mask": compensated_output})
                        moves.append({"fixed_axis": fixed,
                                      "term_indices": [p, q, *carrier_indices],
                                      "source_terms": [pivot, compensator] +
                                                     [terms[i] for i in carrier_indices],
                                      "replacement_terms": replacements,
                                      "carrier_count": size})
    return moves


def reduce_carriers(terms: list[dict], width: int, table: list[int],
                    max_moves: int = 8) -> tuple[list[dict], list[dict], list[dict]]:
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
            raise ValueError("carrier move failed strict saving or tensor identity")
        history.append({"before_terms": prior, "after_terms": len(terms), **move})
    return terms, initial, history


def run(source: Path, target_json: Path | None, report_out: Path,
        candidate_out: Path | None = None, *, modulus: int | None = None,
        table: list[int] | None = None,
        registers: tuple[str, str, str] = ("a", "b", "t")) -> dict:
    """Without target_json, `modulus` and the full `table` describe the target."""
    if report_out.exists() or (candidate_out is not None and candidate_out.exists()):
        raise FileExistsError("report or candidate already exists")
    if target_json is not None:
        target = json.loads(target_json.read_text())
        width, modulus, table = (target[key] for key in
                                 ("width", "modulus", "basis_products"))
    elif modulus is None or table is None:
        raise ValueError("carrier search needs a target tensor JSON or a modulus and table")
    else:
        width = modulus.bit_length() - 1
    a, b, t = registers
    source_report = analyze(Circuit(source), a, b, t, modulus,
                            expected_basis_products=table)
    reduced, initial, history = reduce_carriers(source_report["normalized_terms"],
                                                width, table)
    result = {"schema": "kickmix-bilinear-carrier-v1",
              "tier": "static model / projection", "source_sha256": digest(source),
              "target_sha256": digest(target_json) if target_json else None,
              "target_tensor_kind": source_report["target_tensor_kind"], "width": width,
              "input_ccx": source_report["gate_counts"].get("ccx", 0),
              "initial_opportunities": initial,
              "move_history": history, "final_terms": len(reduced),
              "final_term_list": reduced}
    if candidate_out is not None:
        _, _, checked = validated_terms({"width": width, "modulus": modulus,
            "basis_products": table, "terms": reduced})
        ops, streams = synthesize(width, checked)
        candidate_out.write_bytes(kmb_bytes(width, ops, streams))
        proof = analyze(Circuit(candidate_out), "a", "b", "t", modulus,
                        expected_basis_products=table)
        result.update({"candidate_sha256": digest(candidate_out),
                       "candidate_gate_counts": proof["gate_counts"],
                       "candidate_tensor_identity": proof["tensor_identity"]})
    report_out.write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--tensor-json", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--candidate-out", type=Path)
    args = parser.parse_args()
    result = run(args.source, args.tensor_json, args.out, args.candidate_out)
    print(json.dumps({"report": str(args.out), "input_ccx": result["input_ccx"],
                      "opportunities": len(result["initial_opportunities"]),
                      "final_terms": result["final_terms"],
                      "candidate_sha256": result.get("candidate_sha256")}))
