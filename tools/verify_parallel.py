#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0. AI-assisted code; see NOTICE.
#
# Written with the assistance of an AI coding model (Claude) under the repository owner's
# direction. Logic adapted from the owner's competition script local_verify_reference.py.
"""Run an official invsqrt or DQI verifier on several cores where `fork` is unavailable (Windows).

The official verifiers of the Kaggle competition "Google Quantum AI - Verifiable Quantum Circuit
Compilation" split their shots into seeded batch tasks and hand them to a module-level
`_run_parallel(fn, tasks, max_workers)`. That helper only parallelises with the `fork` start
method and runs serially elsewhere, because the batch function is a closure over the loaded
circuit and cannot be pickled.

This tool uses `spawn` instead and changes nothing in the verifier files:

  1. each of N worker processes imports the verifier, loads the circuit itself and calls the
     verifier's own `verify()`, with `_run_parallel` replaced so the worker evaluates only
     tasks[k::N] (the task list and its seeds come from the verifier, derived from the circuit
     hash and salt) and returns those results;
  2. the parent calls `verify()` once more with `_run_parallel` returning the gathered results
     in task order, so every aggregation step and final check is the verifier's own code.

Each task carries its own seeds and results are reassembled in order, so the report is the same
as a serial or forked run. Memory: every worker holds its own copy of the circuit.

The competition data (verifiers, circuits) is NOT part of this repository; point --verifiers at
your own local copy obtained from Kaggle under the competition rules.

Usage:
  python tools/verify_parallel.py --verifiers DIR --task {invsqrt,dqi} --circuit FILE.kmb
                                  [--workers N] [--salt S] [--out report.json]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import importlib
import json
import multiprocessing as mp
import os
import sys
import time

# Verifier module and the spec each task is verified against by default.
TASKS = {
    "invsqrt": ("invsqrt.verify_submission_invsqrt", lambda m: m.ProblemSpecInvSqrt.for_n_p(10)),
    "dqi": ("dqi.verify_submission_dqi", lambda m: m.ProblemSpecDQI.benchmark_instance()),
}


class _Collected(Exception):
    """Carries one worker's task results out of the verifier's verify()."""

    def __init__(self, results):
        super().__init__()
        self.results = results


def load(verifiers: str, module_name: str, spec_name: str | None, circuit_path: str):
    """Imports the verifier module and returns (module, circuit, spec).

    spec_name selects a TASKS entry; None calls the module's own `make_spec()` (used by tests).
    """
    if verifiers not in sys.path:
        sys.path.insert(0, verifiers)
    import kickmix as km

    module = importlib.import_module(module_name)
    spec = TASKS[spec_name][1](module) if spec_name else module.make_spec()
    circuit = km.Circuit.from_file(circuit_path, format="kmb") if circuit_path else None
    return module, circuit, spec


def _worker(job):
    load_args, salt, k, n = job
    module, circuit, spec = load(*load_args)

    def run_share(fn, tasks, max_workers):
        raise _Collected([fn(task) for task in tasks[k::n]])

    module._run_parallel = run_share
    try:
        module.verify(circuit, spec, salt=salt, max_workers=n)
    except _Collected as collected:
        return collected.results
    raise RuntimeError(f"{module.__name__}.verify() returned without calling _run_parallel")


def verify_spawned(load_args, salt: str, workers: int):
    """Returns the verifier's own result dict, with its batch tasks run on `workers` processes."""
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn")) as pool:
        shares = list(pool.map(_worker, [(load_args, salt, k, workers) for k in range(workers)]))
    module, circuit, spec = load(*load_args)

    def gathered(fn, tasks, max_workers):
        if sum(map(len, shares)) != len(tasks):
            raise RuntimeError("workers saw a different task list from the parent")
        results = [None] * len(tasks)
        for k, share in enumerate(shares):
            results[k::workers] = share
        return results

    original = module._run_parallel
    module._run_parallel = gathered
    try:
        return module.verify(circuit, spec, salt=salt, max_workers=workers)
    finally:
        module._run_parallel = original


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verifiers", required=True, help="directory containing invsqrt/ and dqi/ verifier packages")
    parser.add_argument("--task", required=True, choices=sorted(TASKS))
    parser.add_argument("--circuit", required=True, help="the .kmb circuit to verify")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1,
                        help="worker processes (default: all logical CPUs; DQI needs ≈ 1 GB each)")
    parser.add_argument("--salt", default="")
    parser.add_argument("--out", help="also write the result as JSON here (must not exist)")
    args = parser.parse_args()
    if args.out and os.path.exists(args.out):
        raise FileExistsError(f"refusing to overwrite {args.out}")

    load_args = (os.path.abspath(args.verifiers), TASKS[args.task][0], args.task, os.path.abspath(args.circuit))
    start = time.monotonic()
    result = verify_spawned(load_args, args.salt, max(1, args.workers))
    elapsed = time.monotonic() - start
    text = json.dumps({"task": args.task, "workers": args.workers, "wall_seconds": elapsed, "result": result},
                      indent=2, default=str)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    main()
