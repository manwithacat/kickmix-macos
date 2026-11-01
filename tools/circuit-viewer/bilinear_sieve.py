#!/usr/bin/env python3
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
"""Exact-first, two-stage candidate sieve for fixed-width bilinear KMBs.

scan: certify a pool, collapse repeated term sets, select a bounded and diverse
review packet. finalize: validate an agent's structured decisions and return a
small shortlist, always retaining the best exact candidate. No model is called.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
from collections import Counter
from pathlib import Path

from bilinear_cert import analyze
from workbench import Circuit


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def term_key(terms: list[dict]) -> tuple[tuple[int, int, int], ...]:
    return tuple(sorted((int(t["a_mask"]), int(t["b_mask"]),
                         int(t["output_mask"])) for t in terms))


def key_hash(key: tuple[tuple[int, int, int], ...]) -> str:
    return hashlib.sha256(json.dumps(key, separators=(",", ":")).encode()).hexdigest()


def distance(a: dict, b: dict) -> float:
    left, right = set(map(tuple, a["term_key"])), set(map(tuple, b["term_key"]))
    union = left | right
    return 1.0 - len(left & right) / len(union) if union else 0.0


def candidate(path: Path, row: dict, manifest: Path) -> dict:
    record = {"id": path.name, "path": str(path.resolve()), "sha256": sha(path),
              "size_bytes": path.stat().st_size, "valid": False}
    try:
        circuit = Circuit(path)
        report = analyze(circuit, "a", "b", "t", row["modulus"],
                         expected_basis_products=row.get("basis_products"))
        n = row["width"]
        if report["width"] != n or report["registers"] != {
            "a": list(range(n)), "b": list(range(n, 2 * n)),
            "target": list(range(2 * n, 3 * n))}:
            raise ValueError("candidate register layout differs from challenge")
        terms = term_key(report["normalized_terms"])
        record.update({"valid": True, "ccx": report["gate_counts"].get("ccx", 0),
                       "cx": report["gate_counts"].get("cx", 0),
                       "normalized_rank_upper": report["bilinear_rank_upper_bound"],
                       "term_key": terms, "term_signature": key_hash(terms),
                       "output_weight_histogram": dict(sorted(Counter(
                           int(t[2]).bit_count() for t in terms).items())),
                       "a_parities": len({t[0] for t in terms}),
                       "b_parities": len({t[1] for t in terms})})
    except (OSError, ValueError, IndexError, KeyError, TypeError, AssertionError,
            struct.error, UnicodeDecodeError) as exc:
        record["error"] = str(exc)
    return record


def representatives(records: list[dict]) -> tuple[list[dict], dict[str, str]]:
    groups: dict[str, list[dict]] = {}
    for record in records:
        if record["valid"]:
            groups.setdefault(record["term_signature"], []).append(record)
    reps = []
    duplicates = {}
    for group in groups.values():
        ordered = sorted(group, key=lambda r: (r["ccx"], r["cx"], r["sha256"], r["id"]))
        representative = ordered[0]
        reps.append(representative)
        for other in ordered[1:]:
            duplicates[other["id"]] = representative["id"]
    return sorted(reps, key=lambda r: (r["ccx"], r["cx"], r["sha256"], r["id"])), duplicates


def select_review(reps: list[dict], limit: int) -> list[dict]:
    if not reps or limit < 1:
        raise ValueError("at least one valid candidate and positive review limit required")
    selected = [reps[0]]
    remaining = {r["id"]: r for r in reps[1:]}
    best_ccx = reps[0]["ccx"]
    # Preserve distinct best-count decompositions before sampling cost levels.
    while len(selected) < limit:
        equal = [r for r in remaining.values() if r["ccx"] == best_ccx]
        if not equal:
            break
        chosen = max(equal, key=lambda r: (min(distance(r, s) for s in selected),
                                           -r["cx"], r["sha256"]))
        selected.append(chosen)
        del remaining[chosen["id"]]
    # One representative at each higher CCX level, then fill by diversity.
    levels = sorted({r["ccx"] for r in remaining.values()})
    for level in levels:
        if len(selected) >= limit:
            break
        options = [r for r in remaining.values() if r["ccx"] == level]
        chosen = min(options, key=lambda r: (r["cx"], r["sha256"], r["id"]))
        selected.append(chosen)
        del remaining[chosen["id"]]
    while remaining and len(selected) < limit:
        chosen = max(remaining.values(), key=lambda r: (
            min(distance(r, s) for s in selected), -r["ccx"], -r["cx"], r["sha256"]))
        selected.append(chosen)
        del remaining[chosen["id"]]
    return selected


def scan(manifest: Path, challenge_id: str, pool: Path, out: Path,
         review_size: int = 6, max_candidates: int = 10_000) -> dict:
    if out.exists():
        raise FileExistsError(out)
    data = json.loads(manifest.read_text())
    row = next((r for r in data["instances"] if r["id"] == challenge_id), None)
    if row is None:
        raise ValueError("unknown challenge ID")
    source = manifest.parent / row["kmb"]
    if sha(source) != row["sha256"]:
        raise ValueError("challenge source hash mismatch")
    paths = sorted(pool.glob("*.kmb"))
    if not paths or len(paths) > max_candidates:
        raise ValueError("candidate pool is empty or exceeds the stated cap")
    if any(p.is_symlink() or not p.is_file() or p.stat().st_size > 16_000_000
           for p in paths):
        raise ValueError("pool contains a symlink, non-file or oversized KMB")
    records = [candidate(p, row, manifest) for p in paths]
    reps, duplicates = representatives(records)
    selected = select_review(reps, review_size)
    min_ccx = selected[0]["ccx"]
    pairwise = []
    for i, left in enumerate(selected):
        for right in selected[i + 1:]:
            a, b = set(map(tuple, left["term_key"])), set(map(tuple, right["term_key"]))
            pairwise.append({"a": left["id"], "b": right["id"],
                             "term_overlap": len(a & b),
                             "jaccard_distance": distance(left, right)})
    review_rows = []
    for index, record in enumerate(selected):
        review_row = {k: record[k] for k in ("id", "sha256", "ccx", "cx",
            "normalized_rank_upper", "term_signature", "term_key",
            "output_weight_histogram", "a_parities", "b_parities")}
        review_row["selection_reason"] = ("best_exact_ccx" if index == 0 else
            "distinct_best_ccx" if record["ccx"] == min_ccx else "cost_level")
        review_row["cost_dominated_by"] = [other["id"] for other in selected
            if other["id"] != record["id"] and
            other["ccx"] <= record["ccx"] and other["cx"] <= record["cx"] and
            (other["ccx"], other["cx"]) != (record["ccx"], record["cx"])]
        review_rows.append(review_row)
    out.mkdir(parents=True)
    audit = {"schema": "bilinear-sieve-audit-v1", "tier": "static model / projection",
             "challenge_id": challenge_id, "manifest_sha256": sha(manifest),
             "source_sha256": row["sha256"], "pool": str(pool.resolve()),
             "records": records, "duplicate_term_sets": duplicates}
    packet = {"schema": "bilinear-sieve-review-v1", "challenge_id": challenge_id,
              "best_ccx": min_ccx, "review_size": len(selected),
              "instructions": ("Review structure and possible follow-up value. Validity and "
                               "CCX are exact; do not infer a derivation from a passing checker. "
                               "Return one keep/discard/needs_evidence decision per ID with a reason."),
              "candidates": review_rows, "pairwise_term_comparison": pairwise}
    summary = {"schema": "bilinear-sieve-summary-v1", "tier": "static model / projection",
               "input_count": len(records), "valid_count": sum(r["valid"] for r in records),
               "invalid_count": sum(not r["valid"] for r in records),
               "distinct_term_sets": len(reps), "review_count": len(selected),
               "best_ccx": min_ccx, "review_ids": [r["id"] for r in selected]}
    for name, value in (("audit.json", audit), ("review_packet.json", packet),
                        ("summary.json", summary)):
        (out / name).write_text(json.dumps(value, indent=2) + "\n")
    return summary


def finalize(audit_path: Path, packet_path: Path, decisions_path: Path,
             out: Path, max_shortlist: int = 6) -> dict:
    if out.exists():
        raise FileExistsError(out)
    audit = json.loads(audit_path.read_text())
    packet = json.loads(packet_path.read_text())
    decisions = json.loads(decisions_path.read_text())
    if audit["challenge_id"] != packet["challenge_id"]:
        raise ValueError("audit and review packet challenge IDs differ")
    review = {r["id"]: r for r in packet["candidates"]}
    exact = {r["id"]: r for r in audit["records"] if r["valid"]}
    if not review or any(
        row["id"] not in exact or
        row["sha256"] != exact[row["id"]]["sha256"] or
        row["term_signature"] != exact[row["id"]]["term_signature"]
        for row in packet["candidates"]
    ):
        raise ValueError("review packet does not match exact scan audit")
    if max_shortlist < 1:
        raise ValueError("max_shortlist must be positive")
    proposed = decisions.get("decisions", [])
    if len(proposed) != len(review) or {d.get("id") for d in proposed} != set(review):
        raise ValueError("one decision is required for every review candidate")
    for decision in proposed:
        if decision.get("disposition") not in ("keep", "discard", "needs_evidence"):
            raise ValueError("invalid reviewer disposition")
        if len(str(decision.get("reason", "")).strip()) < 12:
            raise ValueError("reviewer reason is missing or too short")
        record = exact[decision["id"]]
        if record["sha256"] != review[decision["id"]]["sha256"]:
            raise ValueError("reviewed candidate hash differs from audit")
        if sha(Path(record["path"])) != record["sha256"]:
            raise ValueError("reviewed candidate bytes changed")
    frontier = min(exact.values(), key=lambda r: (
        r["ccx"], r["cx"], r["sha256"], r["id"]))
    selected = [frontier]
    decisions_by_id = {d["id"]: d for d in proposed}
    for disposition in ("keep", "needs_evidence"):
        for row in packet["candidates"]:
            record = exact[row["id"]]
            if (decisions_by_id[row["id"]]["disposition"] == disposition and
                    record["id"] != frontier["id"] and len(selected) < max_shortlist):
                selected.append(record)
    out.mkdir(parents=True)
    copies = out / "candidates"
    copies.mkdir()
    entries = []
    for record in selected:
        destination = copies / record["id"]
        shutil.copyfile(record["path"], destination)
        if sha(destination) != record["sha256"]:
            raise ValueError("shortlist copy hash mismatch")
        entries.append({"id": record["id"], "sha256": record["sha256"],
                        "ccx": record["ccx"], "cx": record["cx"],
                        "term_signature": record["term_signature"],
                        "review": decisions_by_id.get(record["id"]),
                        "mandatory_exact_frontier": record["id"] == frontier["id"],
                        "path": str(destination)})
    result = {"schema": "bilinear-sieve-shortlist-v1",
              "tier": "static model / projection", "challenge_id": audit["challenge_id"],
              "audit_sha256": sha(audit_path), "packet_sha256": sha(packet_path),
              "decisions_sha256": sha(decisions_path), "shortlist": entries}
    (out / "shortlist.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("scan")
    start.add_argument("--manifest", type=Path, required=True)
    start.add_argument("--id", required=True)
    start.add_argument("--pool", type=Path, required=True)
    start.add_argument("--out", type=Path, required=True)
    start.add_argument("--review-size", type=int, default=6)
    end = commands.add_parser("finalize")
    end.add_argument("--audit", type=Path, required=True)
    end.add_argument("--packet", type=Path, required=True)
    end.add_argument("--decisions", type=Path, required=True)
    end.add_argument("--out", type=Path, required=True)
    end.add_argument("--max-shortlist", type=int, default=6)
    args = parser.parse_args()
    if args.command == "scan":
        result = scan(args.manifest, args.id, args.pool, args.out, args.review_size)
    else:
        result = finalize(args.audit, args.packet, args.decisions,
                          args.out, args.max_shortlist)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
