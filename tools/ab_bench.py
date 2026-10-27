#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0. AI-assisted code; see NOTICE.
#
# Written with the assistance of an AI coding model (Claude) under the repository owner's
# direction (see NOTICE).
"""Alternating A/B benchmark and parity check for two kickmix builds.

Takes the Python interpreters of two virtual environments (A = unmodified upstream at the
pinned commit, B = this fork), then:

  1. runs tests/parity_dump.py with both and requires identical output (--no-parity skips);
  2. runs bench/sim_bench.py on each workload for --rounds rounds, alternating the order
     (A B, B A, A B, ...) so drift (thermal state, other load) is spread over both;
  3. reports per-variant medians over rounds and the ratio B/A (above 1.0 = B faster),
     as a table on stdout, Markdown to $GITHUB_STEP_SUMMARY if set, and JSON to --json.

Workloads are synthetic circuits or circuit files you supply. Competition data must not
be passed from CI; use tools/verify_parity.sh locally for the official verifiers.

Exit status is 1 if parity fails, 0 otherwise. Performance is reported, never gated.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(py: str, args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run([py, *args], capture_output=True, text=True, check=False, **kw)


def parity(py_a: str, py_b: str) -> tuple[bool, str]:
    outs = []
    for py in (py_a, py_b):
        r = run(py, [str(ROOT / "tests" / "parity_dump.py")])
        if r.returncode != 0:
            return False, f"parity_dump failed under {py}:\n{r.stderr[-2000:]}"
        outs.append(r.stdout)
    if outs[0] == outs[1]:
        return True, f"identical ({len(outs[0].splitlines())} lines)"
    la, lb = outs[0].splitlines(), outs[1].splitlines()
    diff = [f"  A: {x}\n  B: {y}" for x, y in zip(la, lb) if x != y][:5]
    return False, "OUTPUT DIFFERS\n" + "\n".join(diff)


def bench_once(py: str, workload: dict, repeats: int) -> float:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "r.json"
        cmd = [str(ROOT / "bench" / "sim_bench.py"), "--repeats", str(repeats), "--json", str(out)]
        cmd += workload["args"]
        r = run(py, cmd)
        if r.returncode != 0:
            raise RuntimeError(f"sim_bench failed under {py}:\n{r.stderr[-2000:]}")
        return json.loads(out.read_text())["median_G"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a-python", required=True, help="interpreter of the baseline (upstream) venv")
    ap.add_argument("--b-python", required=True, help="interpreter of the fork venv")
    ap.add_argument("--a-label", default="upstream")
    ap.add_argument("--b-label", default="fork")
    ap.add_argument("--rounds", type=int, default=3, help="alternating rounds per workload (>= 3 recommended)")
    ap.add_argument("--repeats", type=int, default=3, help="repeats inside each sim_bench run (its median is used)")
    ap.add_argument("--ops", type=int, default=200_000)
    ap.add_argument("--batches", type=int, default=8)
    ap.add_argument("--circuit", action="append", default=[],
                    help="extra circuit file (.kmb/.kmx) to benchmark with counting on; repeatable")
    ap.add_argument("--no-parity", action="store_true")
    ap.add_argument("--json", default=None, help="write the full result to this JSON file")
    args = ap.parse_args()

    workloads = [
        {"name": "synthetic, counting off", "args": ["--ops", str(args.ops), "--batches", str(args.batches)]},
        {"name": "synthetic, counting on", "args": ["--ops", str(args.ops), "--batches", str(args.batches), "--counting"]},
    ]
    for c in args.circuit:
        workloads.append({"name": f"{Path(c).name}, counting on",
                          "args": ["--circuit", c, "--batches", str(max(1, args.batches // 4)), "--counting"]})

    result = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "runner_os": os.environ.get("RUNNER_OS"),
        "rounds": args.rounds,
        "labels": [args.a_label, args.b_label],
        "parity": None,
        "workloads": [],
    }
    status = 0
    lines = []
    if not args.no_parity:
        ok, msg = parity(args.a_python, args.b_python)
        result["parity"] = {"identical": ok, "detail": msg}
        print(f"parity_dump: {'PASS' if ok else 'FAIL'}: {msg}")
        lines.append(f"Parity (tests/parity_dump.py, {args.a_label} vs {args.b_label}): **{'identical' if ok else 'DIFFERS'}**\n")
        if not ok:
            status = 1

    rows = []
    for w in workloads:
        a_vals, b_vals = [], []
        for rnd in range(args.rounds):
            order = [("a", args.a_python), ("b", args.b_python)]
            if rnd % 2:
                order.reverse()
            for tag, py in order:
                v = bench_once(py, w, args.repeats)
                (a_vals if tag == "a" else b_vals).append(v)
                print(f"[{w['name']}] round {rnd + 1} {tag}: {v:.2f} G gate-shots/s", flush=True)
        ma, mb = statistics.median(a_vals), statistics.median(b_vals)
        ratios = [b / a for a, b in zip(a_vals, b_vals)]
        result["workloads"].append({"name": w["name"], "a": a_vals, "b": b_vals, "a_median": ma, "b_median": mb,
                                    "ratio_of_medians": mb / ma, "round_ratios": ratios})
        rows.append((w["name"], ma, mb, mb / ma, min(ratios), max(ratios)))

    header = f"| Workload | {args.a_label} (G gate-shots/s) | {args.b_label} (G gate-shots/s) | ratio {args.b_label}/{args.a_label} | per-round ratio range |"
    table = [header, "| --- | ---: | ---: | ---: | --- |"]
    for n, ma, mb, r, lo, hi in rows:
        table.append(f"| {n} | {ma:.2f} | {mb:.2f} | {r:.2f}x | {lo:.2f}-{hi:.2f} |")
    meta = (f"{platform.system()} {platform.machine()}, Python {result['python']}, {args.rounds} alternating rounds, "
            f"median of {args.repeats} repeats per run; ratio above 1.00 means {args.b_label} is faster.")
    print("\n".join(table))
    print(meta)
    lines += table + ["", meta]
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"### Perf: {result['runner_os'] or platform.system()} {platform.machine()}\n\n" + "\n".join(lines) + "\n\n")
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2))
    return status


if __name__ == "__main__":
    sys.exit(main())
