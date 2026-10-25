"""Smoke test for a freshly built kickmix: build a small circuit, simulate a batch,
check the classical result and a phase, and report the platform.

Run with the interpreter that has kickmix installed:
    python tests/smoke_neon_build.py
"""

import platform
import sys

import numpy as np

import kickmix as km


def main() -> None:
    # A compute-phase-uncompute circuit: |x>|0> -> (-1)^x |x>|0>, from the kickmix text format.
    circuit = km.Circuit("CX q0 q1\nZ q1\nCX q0 q1\n")
    batch = 256
    sim = km.Simulator(batch_size=batch, debug_print=False, seed=1)
    sim.clear_for_shot()
    xs = np.arange(batch) % 2 == 1
    sim.write_across_shots(km.q(0), xs)
    sim.do(circuit)
    assert np.array_equal(sim.read_across_shots(km.q(0)), xs), "input register changed"
    assert not sim.read_across_shots(km.q(1)).any(), "scratch qubit not clean"
    phases = np.array([sim.read_shot_phase(i) for i in range(batch)])
    # Z on |1> is a half turn.
    half_turns = np.array([float(p) for p in phases])
    assert np.allclose(half_turns[xs] % 2, 1.0) and np.allclose(half_turns[~xs] % 2, 0.0), half_turns[:8]
    stats = circuit.operation_statistics()
    print(
        f"kickmix smoke OK on {platform.system()} {platform.machine()} "
        f"({sys.version.split()[0]}): {circuit.num_qubits} qubits, "
        f"{stats.num_toffoli_like} Toffoli-like, module {km.__file__}"
    )


if __name__ == "__main__":
    main()
