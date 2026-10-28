# SPDX-License-Identifier: Apache-2.0. AI-assisted code; see NOTICE.
"""Simulator.read_phases_u64 equals the official verifiers' per-shot conversion, bit for bit.

The reference below is `half_turns_to_units` copied from the competition's official verifier
(verify_submission_invsqrt.py) applied to the existing, per-shot `read_shot_phase`. Run with a venv
that has kickmix:
    python tests/test_phase_batch.py
"""
from fractions import Fraction

import numpy as np

import kickmix as km

MASK64 = (1 << 64) - 1


def half_turns_to_units(half_turns):
    n, d = half_turns.as_integer_ratio()
    return (((n << 64) // d + 1) >> 1) & MASK64


def reference(sim, batch):
    return np.array([half_turns_to_units(sim.read_shot_phase(i)) for i in range(batch)], dtype=np.uint64)


def check(sim, batch, what):
    want = reference(sim, batch)
    got = np.empty(batch, dtype=np.uint64)
    assert sim.read_phases_u64(got) is None
    assert np.array_equal(got, want), (what, batch, np.flatnonzero(got != want)[:5])
    return got


def decimal(num, e):
    """Exact decimal string of num / 2**e (num >= 0 or negative), no exponent notation."""
    sign = "-" if num < 0 else ""
    digits = str(abs(num) * 5**e).rjust(e + 1, "0")
    return f"{sign}{digits[:-e] or '0'}.{digits[-e:]}" if e else f"{sign}{digits}"


def edge_phases():
    """Half-turn values (as exact Fractions) around every rounding boundary."""
    u = Fraction(1, 2**63)  # one unit (2^-64 turn) in half turns
    out = [Fraction(0), Fraction(1), Fraction(1, 2), Fraction(1, 4), Fraction(3, 2), Fraction(2 ** 127 - 1, 2 ** 127)]
    for k in (0, 1, 2, 3, 2**62, 2**63 - 1, 2**63, 2**63 + 1, 2**64 - 2, 2**64 - 1):
        base = k * u
        for t in (Fraction(0), u / 2, u / 2 - Fraction(1, 2**127), u / 2 + Fraction(1, 2**127),
                  -u / 2, -u / 2 + Fraction(1, 2**127), u / 4, -u / 4, 3 * u / 4):
            out.append((base + t) % 2)
    for e in (1, 2, 10, 30, 60, 62, 63, 64, 65, 100, 126, 127):  # tiny angles, both signs
        out.append(Fraction(1, 2**e))
        out.append((-Fraction(1, 2**e)) % 2)
    return out


rng = np.random.default_rng(2028)
cases = 0
for batch in (1, 63, 64, 65, 255, 256, 257, 700, 1024):
    sim = km.Simulator(batch_size=batch, debug_print=False, seed=3)
    check(sim, batch, "fresh")

    # Edge phases written directly, then toggled with the global-phase bit (Z on a set qubit).
    edges = edge_phases()
    for flip in (False, True):
        sim = km.Simulator(batch_size=batch, debug_print=False, seed=3)
        for i in range(batch):
            sim.write_shot_phase(i, edges[i % len(edges)])
        if flip:
            sim.write_across_shots(km.q(0), np.ones(batch, dtype=bool))
            sim.do(km.Circuit("Z q0"))
        check(sim, batch, ("edges", flip))
        cases += 1

    # Random phases written directly (full 127-bit range, then rounded by write_shot_phase).
    sim = km.Simulator(batch_size=batch, debug_print=False, seed=4)
    for i in range(batch):
        num = int(rng.integers(0, 2**63)) << 64 | int(rng.integers(0, 2**63)) << 1 | int(rng.integers(0, 2))
        sim.write_shot_phase(i, Fraction(num, 2**127) * 2 % 2)
    check(sim, batch, "random written")
    cases += 1

    # Random circuits: Z_POW with random angles on random qubits, X, CX, Z, conditioned on random data.
    for trial in range(4):
        sim = km.Simulator(batch_size=batch, debug_print=False, seed=100 + trial)
        for q in range(4):
            sim.write_across_shots(km.q(q), rng.random(batch) < 0.5)
        lines = []
        for _ in range(30):
            q = int(rng.integers(0, 4))
            kind = int(rng.integers(0, 5))
            if kind == 0:
                lines.append(f"Z_POW q{q} {decimal(int(rng.integers(-2**40, 2**40)), 38)}")
            elif kind == 1:
                lines.append(f"Z_POW q{q} {decimal(1, int(rng.integers(1, 120)))}")
            elif kind == 2:
                lines.append(f"Z q{q}")
            elif kind == 3:
                lines.append(f"X q{q}")
            else:
                lines.append(f"CX q{q} q{(q + 1) % 4}")
        sim.do(km.Circuit("\n".join(lines)))
        check(sim, batch, ("circuit", trial))
        cases += 1

# Non-contiguous output, read-only and wrongly shaped/typed outputs.
sim = km.Simulator(batch_size=300, debug_print=False, seed=5)
for i in range(300):
    sim.write_shot_phase(i, Fraction(int(rng.integers(0, 2**62)), 2**62))
want = reference(sim, 300)
wide = np.zeros(600, dtype=np.uint64)
sim.read_phases_u64(wide[::2])
assert np.array_equal(wide[::2], want) and not wide[1::2].any()
for bad, exc in ((np.zeros(299, dtype=np.uint64), ValueError), (np.zeros(300, dtype=np.int64), (ValueError, TypeError)),
                 (np.zeros((300, 1), dtype=np.uint64), ValueError)):
    try:
        sim.read_phases_u64(bad)
    except exc:
        pass
    else:
        raise AssertionError(f"expected an error for {bad.dtype} {bad.shape}")
ro = np.zeros(300, dtype=np.uint64)
ro.flags.writeable = False
try:
    sim.read_phases_u64(ro)
except (ValueError, RuntimeError):
    pass
else:
    raise AssertionError("expected an error for a read-only array")
print(f"OK: {cases} cases, read_phases_u64 == half_turns_to_units(read_shot_phase) bit for bit")
