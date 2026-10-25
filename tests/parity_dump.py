"""Deterministic simulation transcript used by CI to show two builds agree bit for bit."""
import hashlib
import sys

import numpy as np

import kickmix as km

sys.path.insert(0, __file__.rsplit("/", 2)[0] + "/bench")
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
