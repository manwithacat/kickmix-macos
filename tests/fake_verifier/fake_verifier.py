# SPDX-License-Identifier: Apache-2.0. AI-assisted code; see NOTICE.
"""Stand-in with the same scheduling contract as the competition's official verifiers.

verify() derives seeded batch tasks from the salt, runs a closure over them through the
module-level _run_parallel(fn, tasks, max_workers) (serial unless `fork` exists, as in the
official files), and aggregates. Used by tests/test_verify_parallel.py, which cannot ship the
official verifiers.
"""
import multiprocessing as mp
import concurrent.futures
import hashlib
import sys
from pathlib import Path

import numpy as np
import kickmix as km

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bench"))
from sim_bench import synthetic_circuit  # noqa: E402

_WORKER_FN = None


def _call_worker(task):
    return _WORKER_FN(task)


def _run_parallel(fn, tasks, max_workers):
    global _WORKER_FN
    if (max_workers or 1) <= 1 or 'fork' not in mp.get_all_start_methods():
        return [fn(t) for t in tasks]
    _WORKER_FN = fn
    try:
        with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers, mp_context=mp.get_context('fork')) as pool:
            return list(pool.map(_call_worker, tasks))
    finally:
        _WORKER_FN = None


def make_spec():
    return {"batches": 13, "batch_size": 64}


def verify(circuit, spec, *, salt='', max_workers=None):
    del circuit  # the stand-in builds its own random circuit
    base = int.from_bytes(hashlib.sha256(salt.encode()).digest()[:8], 'little')
    seeds = np.random.SeedSequence(base).generate_state(2 * spec["batches"], dtype=np.uint64).reshape(-1, 2)
    tasks = [(i, int(a), int(b)) for i, (a, b) in enumerate(seeds)]

    def run_batch(task):
        index, rng_seed, sim_seed = task
        rng = np.random.default_rng(rng_seed)
        sim = km.Simulator(batch_size=spec["batch_size"], debug_print=False, counting_operations=True, seed=sim_seed)
        for q in range(8):
            sim.write_across_shots(km.q(q), rng.random(spec["batch_size"]) < 0.5)
        sim.do(synthetic_circuit(24, 3000, seed=index))
        bits = sim.read_across_shots(km.array([km.q(i) for i in range(24)]))
        return index, int(bits.sum()), int(sim.operation_statistics().num_toffoli_like)

    results = _run_parallel(run_batch, tasks, max_workers)
    return {
        "order": [r[0] for r in results],
        "ones": [r[1] for r in results],
        "toffolis": sum(r[2] for r in results),
    }
