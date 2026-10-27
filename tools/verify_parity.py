#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0. AI-assisted code; see NOTICE.
#
# Written with the assistance of an AI coding model (Claude) under the repository owner's
# direction. Logic adapted from the owner's competition scripts (kickmix_parity_compare.py,
# local_verify_reference.py).
"""Run the unmodified official verifiers of the Kaggle competition "Google Quantum AI -
Verifiable Quantum Circuit Compilation" with a given kickmix build, and compare reports.

The competition data (verifiers and submissions) is NOT part of this repository and must not
be redistributed; point --root at your own local copy obtained from Kaggle under the rules.

Subcommands:
  run      python verify_parity.py run --root DIR --out report.json [--tasks ...] [--workers N]
           (run with the interpreter whose kickmix build you want to test)
  compare  python verify_parity.py compare A.json B.json [task ...]
           diffs every field of each task's summary except timings; exit 1 on any difference

--root must contain verifiers/{invsqrt,dqi,dfthcsos}/ and sample_submission/ (or pass
--submission). tools/verify_parity.sh drives both subcommands with two interpreters.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

SKIP_FIELDS = {"wall_seconds", "seconds", "elapsed"}
TASKS = ["dfthcsos", "invsqrt", "dqi"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_run(args: argparse.Namespace) -> int:
    out = Path(args.out)
    if out.exists():
        raise FileExistsError(f"refusing to overwrite {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    root = Path(args.root).resolve()
    submission = Path(args.submission).resolve() if args.submission else root / "sample_submission"
    if not (root / "verifiers").is_dir():
        raise SystemExit(f"{root}/verifiers not found; --root must hold the official verifiers")
    sys.path.insert(0, str(root / "verifiers"))
    import numpy as np  # noqa: E402

    import kickmix as km  # noqa: E402

    if not hasattr(__import__("os"), "fork"):
        print("note: this platform has no fork(); the official verifiers run single-process here "
              "(use WSL2 for multi-core verification). --workers has no effect.", file=sys.stderr)

    kfile = Path(km.__file__)
    report = {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "kickmix_module": str(kfile),
        "kickmix_sha256": sha256(kfile),
        "numpy": np.__version__,
        "root": str(root),
        "submission": str(submission),
        "settings": {"salt": args.salt, "workers": args.workers, "invsqrt_workers": args.invsqrt_workers},
        "results": {},
    }

    def save(name: str, summary: dict, elapsed: float) -> None:
        report["results"][name] = {"summary": summary, "wall_seconds": elapsed}
        out.write_text(json.dumps(report, indent=2, default=str))
        print(name, "status", summary.get("status"), "score", summary.get("score"), "seconds", round(elapsed, 1), flush=True)

    out.write_text(json.dumps(report, indent=2, default=str))
    print("kickmix from", kfile, flush=True)

    if "dfthcsos" in args.tasks:
        from dfthcsos.verify import verify as verify_chemistry

        t = time.monotonic()
        save("dfthcsos", verify_chemistry(submission / "dfthcsos", salt=args.salt), time.monotonic() - t)
    if "invsqrt" in args.tasks:
        from invsqrt.verify_submission_invsqrt import ProblemSpecInvSqrt, verify as verify_invsqrt

        circuit = km.Circuit.from_file(str(submission / "invsqrt.kmb"), format="kmb")
        spec = ProblemSpecInvSqrt.for_n_p(10)
        if args.invsqrt_shots_per_x is not None:
            spec = ProblemSpecInvSqrt.for_n_p(10, shots_per_x=args.invsqrt_shots_per_x)
        t = time.monotonic()
        save("invsqrt", verify_invsqrt(circuit, spec, salt=args.salt, max_workers=args.invsqrt_workers), time.monotonic() - t)
        del circuit
    if "dqi" in args.tasks:
        from dqi.verify_submission_dqi import ProblemSpecDQI, verify as verify_dqi

        circuit = km.Circuit.from_file(str(submission / "dqi.kmb"), format="kmb")
        spec = ProblemSpecDQI.benchmark_instance()
        if args.dqi_shots is not None:
            spec = ProblemSpecDQI.benchmark_instance(num_shots=args.dqi_shots)
        t = time.monotonic()
        save("dqi", verify_dqi(circuit, spec, salt=args.salt, max_workers=args.workers), time.monotonic() - t)
        del circuit
    print("saved", out)
    return 0


def _summaries(path: str) -> dict:
    data = json.load(open(path))
    results = data.get("results", data)
    return {task: (v.get("summary", v) if isinstance(v, dict) else v) for task, v in results.items()}


def _flat(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k not in SKIP_FIELDS:
                yield from _flat(v, f"{prefix}{k}.")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _flat(v, f"{prefix}{i}.")
    else:
        yield prefix.rstrip("."), obj


def cmd_compare(args: argparse.Namespace) -> int:
    a, b = _summaries(args.a), _summaries(args.b)
    tasks = args.tasks or sorted(set(a) & set(b))
    if not tasks:
        print("no common tasks to compare")
        return 1
    total_diffs = 0
    for task in tasks:
        fa, fb = dict(_flat(a[task])), dict(_flat(b[task]))
        diffs = [(k, fa.get(k), fb.get(k)) for k in sorted(set(fa) | set(fb)) if fa.get(k) != fb.get(k)]
        total_diffs += len(diffs)
        print(f"{task}: {len(fa)} fields, {len(diffs)} differ")
        for d in diffs[:20]:
            print("   ", d)
    print("RESULT:", "IDENTICAL" if total_diffs == 0 else f"{total_diffs} DIFFERENCES")
    return 0 if total_diffs == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--root", required=True)
    r.add_argument("--submission", default=None)
    r.add_argument("--out", required=True)
    r.add_argument("--tasks", nargs="+", default=TASKS, choices=TASKS)
    r.add_argument("--workers", type=int, default=2, help="DQI workers (about 630 MB each; parent peaks near 5 GB)")
    r.add_argument("--invsqrt-workers", type=int, default=4)
    r.add_argument("--salt", default="")
    r.add_argument("--dqi-shots", type=int, default=None)
    r.add_argument("--invsqrt-shots-per-x", type=int, default=None)
    r.set_defaults(fn=cmd_run)
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("tasks", nargs="*")
    c.set_defaults(fn=cmd_compare)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
