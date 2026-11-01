#!/usr/bin/env python3
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Expose and apply exact common-parity matrix-rank reductions on KMBs."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from functools import reduce
from pathlib import Path

from bilinear_cert import analyze
from bilinear_synth import field_mul, kmb_bytes, synthesize, validated_terms
from workbench import Circuit


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize(terms: list[dict]) -> list[dict]:
    grouped: dict[tuple[int, int], int] = {}
    for term in terms:
        key = (term["a_mask"], term["b_mask"])
        grouped[key] = grouped.get(key, 0) ^ term["output_mask"]
    return [{"a_mask": u, "b_mask": v, "output_mask": w}
            for (u, v), w in sorted(grouped.items()) if w]


def reduce_code(value: int, basis: list[int]) -> tuple[int, int]:
    code = 0
    for index, vector in enumerate(basis):
        if value >> (vector.bit_length() - 1) & 1:
            value ^= vector
            code ^= 1 << index
    return value, code


def factor_columns(columns: list[int]) -> tuple[list[int], list[int]]:
    """Factor output columns through an independent GF(2) output basis."""
    basis: list[int] = []
    for value in columns:
        remainder, _ = reduce_code(value, basis)
        if remainder:
            basis.append(remainder)
    coefficients = []
    for value in columns:
        remainder, code = reduce_code(value, basis)
        if remainder:
            raise ValueError("rank factorization failed")
        coefficients.append(code)
    input_masks = [sum(((code >> k) & 1) << j for j, code in enumerate(coefficients))
                   for k in range(len(basis))]
    return basis, input_masks


def opportunities(terms: list[dict], width: int) -> list[dict]:
    moves = []
    for fixed, varying in (("a_mask", "b_mask"), ("b_mask", "a_mask")):
        groups: dict[int, list[int]] = defaultdict(list)
        for index, term in enumerate(terms):
            groups[term[fixed]].append(index)
        for mask, indices in sorted(groups.items()):
            if len(indices) < 2:
                continue
            columns = [0] * width
            for index in indices:
                term = terms[index]
                for bit in range(width):
                    if term[varying] >> bit & 1:
                        columns[bit] ^= term["output_mask"]
            basis, input_masks = factor_columns(columns)
            replacements = [{fixed: mask, varying: input_mask,
                             "output_mask": output_mask}
                            for input_mask, output_mask in zip(input_masks, basis)
                            if input_mask]
            saving = len(indices) - len(replacements)
            if saving > 0:
                moves.append({"fixed_axis": fixed, "fixed_mask": mask,
                              "term_indices": indices, "input_term_count": len(indices),
                              "output_rank": len(replacements), "saving": saving,
                              "replacement_terms": replacements})
    return sorted(moves, key=lambda move: (-move["saving"], move["fixed_axis"],
                                         move["fixed_mask"], move["term_indices"]))


def tensor_from_terms(terms: list[dict], width: int) -> list[int]:
    return [reduce(int.__xor__,
            (term["output_mask"] for term in terms if
             (term["a_mask"] >> i & 1) and (term["b_mask"] >> j & 1)), 0)
            for i in range(width) for j in range(width)]


def compress(terms: list[dict], width: int, table: list[int],
             max_moves: int = 32) -> tuple[list[dict], list[dict], list[dict]]:
    terms = normalize(terms)
    initial = opportunities(terms, width)
    history = []
    for _ in range(max_moves):
        moves = opportunities(terms, width)
        if not moves:
            break
        move = moves[0]
        old_count = len(terms)
        indices = set(move["term_indices"])
        terms = normalize([term for index, term in enumerate(terms) if index not in indices]
                          + move["replacement_terms"])
        validated_terms({"width": width, "modulus": (1 << width) | 1,
                         "basis_products": table, "terms": terms})
        if len(terms) >= old_count:
            raise ValueError("candidate move failed to reduce term count")
        history.append({"fixed_axis": move["fixed_axis"],
                        "fixed_mask": move["fixed_mask"],
                        "before_terms": old_count, "after_terms": len(terms),
                        "saving": old_count - len(terms)})
    return terms, initial, history


def run(source: Path, a: str, b: str, target: str, modulus: int,
        tensor_json: Path | None, report_out: Path,
        candidate_out: Path | None = None) -> dict:
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
        raise ValueError("certificate term tensor differs from target")
    reduced, initial_moves, history = compress(initial_terms, width, table)
    result = {"schema": "kickmix-bilinear-mechanisms-v1",
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
    args = parser.parse_args()
    result = run(args.source, args.a, args.b, args.target, args.modulus,
                 args.tensor_json, args.out, args.candidate_out)
    print(json.dumps({"report": str(args.out), "input_ccx": result["input_ccx"],
                      "initial_opportunities": len(result["initial_opportunities"]),
                      "final_terms": result["final_terms"],
                      "candidate_sha256": result.get("candidate_sha256")}))


if __name__ == "__main__":
    main()
