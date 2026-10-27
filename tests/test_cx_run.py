"""Equivalence test for patches/cx-run.patch (AI-assisted: written by Claude under the owner's direction).

Random circuits rich in plain-CX runs (lengths 1..~1200) mixed with CX_IF, CCX, measurements and
PUSH/POP_CONDITION are run (a) as one circuit and (b) one op per Simulator.do call, where every CX is a
run of length 1. Qubit/bit state and operation_statistics must be identical, with and without
counting, and with a condition stack active.   Run: python -m pytest tests/test_cx_run.py
"""
import random

import numpy as np
import pytest

import kickmix as km


def gen(seed):
    r = random.Random(seed)
    nq, nb = r.randint(3, 10), 4
    L, depth = [], 0
    while len(L) < r.choice([50, 400, 3000]):
        k = r.random()
        a, b, c = r.sample(range(nq), 3)
        if k < 0.55:
            L += [f"CX q{a} q{b}"
                  for _ in range(r.choice([1, 2, 3, 7, 28, 33, 300, 1200]))]
        elif k < 0.65:
            L.append(f"CX q{a} q{b} if b{r.randrange(nb)}")
        elif k < 0.70:
            L.append(f"CCX q{a} q{b} q{c}")
        elif k < 0.75:
            L.append(f"CZ q{a} q{b}")
        elif k < 0.80:
            L.append(f"HMR q{a} b{r.randrange(nb)}")
        elif k < 0.85:
            L.append(f"X q{a} if b{r.randrange(nb)}")
        elif k < 0.93:
            L.append(f"PUSH_CONDITION if b{r.randrange(nb)}")
            depth += 1
        elif depth:
            L.append("POP_CONDITION")
            depth -= 1
    return L, nq, nb


def snapshot(sim, nq, nb, counting):
    st = sim.operation_statistics() if counting else None
    return (
        [sim.read_across_shots(km.q(q)).tobytes() for q in range(nq)],
        [sim.read_across_shots(km.b(b)).tobytes() for b in range(nb)],
        sorted(dict(st.items()).items()) if counting else None,
        [sorted(dict(sim.operation_statistics(i).items()).items()) for i in (0, 1, 63, 64, 200, 255)] if counting else None,
    )


@pytest.mark.parametrize("counting", [False, True])
@pytest.mark.parametrize("seed", range(40))
def test_cx_run_matches_op_by_op(seed, counting):
    lines, nq, nb = gen(seed)
    rng = np.random.default_rng(seed)
    init = [rng.integers(0, 2, size=256).astype(bool) for _ in range(nq + nb)]
    # cheap bits half the time to get partial condition masks
    sims = [km.Simulator(batch_size=256, debug_print=False, counting_operations=counting, seed=seed) for _ in range(2)]
    for s in sims:
        s.clear_for_shot()
        for q in range(nq):
            s.write_across_shots(km.q(q), init[q])
        for b in range(nb):
            s.write_across_shots(km.b(b), init[nq + b])
    sims[0].do(km.Circuit("\n".join(lines) + "\n"))
    for ln in lines:
        sims[1].do(km.Circuit(ln + "\n"))
    a, b = snapshot(sims[0], nq, nb, counting), snapshot(sims[1], nq, nb, counting)
    assert a == b
