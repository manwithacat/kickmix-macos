"""Simulator throughput benchmark for A/B comparison of kickmix builds.

Generates a deterministic synthetic kickmix circuit (reversible gates, phases and
measurement-based erasure), simulates it for a fixed number of 256-shot batches, and
reports gate throughput. Run it once with a NEON build and once with a build made
with --disable-neon (see tools/build.sh) to compare:

    .venv/bin/python bench/sim_bench.py --repeats 5
    .venv-noneon/bin/python bench/sim_bench.py --repeats 5

Optionally benchmark a real circuit file:

    .venv/bin/python bench/sim_bench.py --circuit path/to/circuit.kmb --batches 20

Numbers depend on the machine and on what else is running; quiet the machine and
take the best of several repeats.
"""

from __future__ import annotations

import argparse
import platform
import random
import statistics
import sys
import time

import numpy as np

import kickmix as km


def synthetic_circuit(num_qubits: int, num_ops: int, seed: int) -> km.Circuit:
    rng = random.Random(seed)
    lines = []
    qubits = list(range(num_qubits))
    for _ in range(num_ops):
        kind = rng.random()
        a, b, c = rng.sample(qubits, 3)
        if kind < 0.45:
            lines.append(f"CCX q{a} q{b} q{c}")
        elif kind < 0.80:
            lines.append(f"CX q{a} q{b}")
        elif kind < 0.90:
            lines.append(f"X q{a}")
        elif kind < 0.97:
            lines.append(f"CZ q{a} q{b}")
        else:
            lines.append(f"Z q{a}")
    return km.Circuit("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--circuit", default=None, help=".kmb/.kmx file to benchmark instead of a synthetic circuit")
    parser.add_argument("--qubits", type=int, default=96)
    parser.add_argument("--ops", type=int, default=200_000)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--batches", type=int, default=8, help="batches simulated per repeat")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20261025)
    args = parser.parse_args()

    if args.circuit:
        fmt = "kmb" if args.circuit.endswith(".kmb") else "kmx"
        circuit = km.Circuit.from_file(args.circuit, format=fmt)
        label = args.circuit
    else:
        circuit = synthetic_circuit(args.qubits, args.ops, args.seed)
        label = f"synthetic {args.qubits}q x {args.ops} ops"
    num_ops = len(circuit)
    print(f"kickmix {km.__file__}")
    print(f"{platform.system()} {platform.machine()} python {sys.version.split()[0]} numpy {np.__version__}")
    print(f"circuit: {label}; {circuit.num_qubits} qubits, {num_ops:,} ops; batch {args.batch_size}; {args.batches} batches/repeat")

    sim = km.Simulator(batch_size=args.batch_size, debug_print=False, seed=args.seed)
    rates = []
    for r in range(args.repeats):
        start = time.perf_counter()
        for _ in range(args.batches):
            sim.clear_for_shot()
            sim.do(circuit)
        elapsed = time.perf_counter() - start
        gate_shots = num_ops * args.batch_size * args.batches
        rates.append(gate_shots / elapsed / 1e9)
        print(f"  repeat {r + 1}: {elapsed:.3f} s, {rates[-1]:.2f} G gate-shots/s")
    print(f"best {max(rates):.2f} G gate-shots/s; median {statistics.median(rates):.2f}")


if __name__ == "__main__":
    main()
