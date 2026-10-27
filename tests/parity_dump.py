"""Deterministic simulation transcript used by CI to show two builds agree bit for bit.

Run with the interpreter of each build and compare the outputs; any difference is a
behavioural difference. The second half simulates with operation counting enabled (as the
competition verifiers do), which exercises PopCounter, and prints the aggregate and
per-shot operation statistics.
"""
import hashlib
import sys
from pathlib import Path

import numpy as np

import kickmix as km

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))
from sim_bench import synthetic_circuit  # noqa: E402

circuit = synthetic_circuit(72, 40_000, seed=7)
sim = km.Simulator(batch_size=256, debug_print=False, seed=12345)
rng = np.random.default_rng(99)
for batch in range(3):
    sim.clear_for_shot()
    for q in range(16):
        sim.write_across_shots(km.q(q), rng.integers(0, 2, size=256).astype(bool))
    sim.do(circuit)
    bits = np.stack([sim.read_across_shots(km.q(q)) for q in range(72)])
    phases = [str(sim.read_shot_phase(i)) for i in range(256)]
    print(batch, int(bits.sum()), hashlib.sha256(bits.tobytes()).hexdigest()[:16], hashlib.sha256("|".join(phases).encode()).hexdigest()[:16])


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# Counting run: two circuits (different lengths so PopCounter crosses its 32-call step cycle
# at different offsets), measurement-free, random inputs, fixed seed.
for ops, seed in ((40_000, 7), (1_000, 8), (33, 9), (64, 10)):
    counted = synthetic_circuit(72, ops, seed=seed)
    csim = km.Simulator(batch_size=256, debug_print=False, counting_operations=True, seed=777)
    for batch in range(2):
        csim.clear_for_shot()
        for q in range(16):
            csim.write_across_shots(km.q(q), rng.integers(0, 2, size=256).astype(bool))
        csim.do(counted)
    shots = "|".join(str(csim.operation_statistics(i)) for i in range(256))
    print("count", ops, digest(str(csim.operation_statistics())), digest(shots))
