#!/usr/bin/env python
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
# AI-assisted (Claude, Anthropic), under the repository owner's direction.
"""errcurve.json for an invsqrt circuit: signed error / tolerance for EVERY x in 1..3,133,452, from kickmix.

Runs kickmix's batched Simulator (the official verifier's own to_bits / validate_registers / half_turns_to_units /
reference_values path), one shot per x, twice with different seeds (the phase must not depend on the measurement
outcomes), asserts x is restored and every scratch qubit is clean for every x, then bins the curve. Also writes
the final phase (top 64 bits) for a sample of x so the browser tracer can be cross-checked (see crosscheck_phase.js).

Usage (kickmix venv python):
  python build_errcurve.py CIRCUIT.kmb VERIFIER_DIR OUT_DIR "circuit label" [workers=3]
VERIFIER_DIR is data/official_full/verifiers/invsqrt of the competition checkout.
Writes OUT_DIR/errcurve.json, OUT_DIR/errtol_seed1.npy, errtol_seed2.npy, OUT_DIR/phase_sample.json.
"""
import json, multiprocessing as mp, random, sys, time
from pathlib import Path
import numpy as np

circ_path, ver_dir, out_dir, label = sys.argv[1:5]
NW = int(sys.argv[5]) if len(sys.argv) > 5 else 3
sys.path.insert(0, ver_dir)
import kickmix as km
import verify_submission_invsqrt as V

BS = 256
spec = V.ProblemSpecInvSqrt.for_n_p(10)
MAXX = spec.max_x
circuit = km.Circuit.from_file(circ_path, format='auto')
x_reg, scratch = V.validate_registers(circuit, spec)


def work(args):
    w, seed = args
    nb = (MAXX + BS - 1) // BS
    out = {}
    sim = km.Simulator(batch_size=BS, debug_print=False, seed=seed * 1000 + w)
    for b in range(w, nb, NW):
        lo = b * BS + 1
        hi = min(lo + BS, MAXX + 1)
        xs = np.arange(lo, lo + BS, dtype=np.uint64)
        xs[xs > MAXX] = 1
        sim.clear_for_shot()
        sim.write_across_shots(x_reg, V.to_bits(xs, spec.width))
        sim.do(circuit)
        assert np.array_equal(V.from_bits(sim.read_across_shots(x_reg)), xs), 'x not restored'
        assert not sim.read_across_shots(scratch).any(), 'scratch not clean'
        th = np.array([V.half_turns_to_units(sim.read_shot_phase(i)) for i in range(BS)], dtype=np.uint64)
        tg, tl = V.reference_values(spec, xs)
        e = (th - tg).view(np.int64).astype(np.float64) / tl.astype(np.float64)
        out[lo] = (e[:hi - lo], th[:hi - lo])
    return out


def full_pass(seed):
    arr = np.full(MAXX, np.nan)
    thr = np.zeros(MAXX, dtype=np.uint64)
    with mp.get_context('fork').Pool(NW) as p:
        for r in p.map(work, [(w, seed) for w in range(NW)]):
            for lo, (e, th) in r.items():
                arr[lo - 1:lo - 1 + len(e)] = e
                thr[lo - 1:lo - 1 + len(e)] = th
    assert not np.isnan(arr).any()
    return arr, thr


if __name__ == '__main__':
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t = time.time()
    a, th1 = full_pass(1)
    b, th2 = full_pass(2)
    np.save(out / 'errtol_seed1.npy', a)
    np.save(out / 'errtol_seed2.npy', b)
    same = bool(np.array_equal(a, b)) and bool(np.array_equal(th1, th2))
    print('seeds identical (all x):', same, 'secs', round(time.time() - t, 1))
    N = len(a)
    n = 2001
    while True:
        edges = np.unique(np.round(np.logspace(0, np.log10(N + 1), n)).astype(np.int64))
        if len(edges) >= 2001:
            break
        n += 10
    edges = edges[:2000].tolist() + [N + 1] if len(edges) > 2001 else edges.tolist()
    bins = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        s = a[lo - 1:hi - 1]
        if len(s) == 0:
            continue
        k = int(np.abs(s).argmax())
        bins.append([int(lo), int(hi - 1), round(float(s.min()), 5), round(float(s.max()), 5), round(float(s.mean()), 5), int(lo + k)])
    ab = np.abs(a)
    top = np.argsort(-ab)[:100]
    hist, _ = np.histogram(ab, bins=50, range=(0, 1))
    w = int(ab.argmax())
    res = dict(circuit=label, max_x=N, units='err/tol, signed; err=(phase-target) mod 1 turn, tol=2^-24/sqrt(x) (verifier units)',
               bin_fields=['x_lo', 'x_hi', 'min', 'max', 'mean', 'x_of_max_abs'], bins=bins,
               top100=[[int(i + 1), round(float(a[i]), 6)] for i in top],
               hist_abs=dict(range=[0, 1], nbins=50, counts=[int(c) for c in hist]),
               summary=dict(worst_x=w + 1, worst_err_over_tol=float(a[w]), frac_gt_0p5=float((ab > 0.5).mean()), frac_gt_0p9=float((ab > 0.9).mean()),
                            frac_gt_0p99=float((ab > 0.99).mean()), n_gt_0p9=int((ab > 0.9).sum()), mean_abs=float(ab.mean()),
                            max_pos=float(a.max()), x_max_pos=int(a.argmax() + 1), seeds_identical_all_x=same, max_abs_exceeds_1=int((ab > 1).sum())))
    (out / 'errcurve.json').write_text(json.dumps(res, separators=(',', ':')))
    print(res['summary'], len(bins))
    rng = random.Random(7)
    xs = sorted(set(rng.randint(1, N) for _ in range(300)) | {1, 2, 161, N, N - 1, 2 ** 21, w + 1, int(a.argmax() + 1)})
    (out / 'phase_sample.json').write_text(json.dumps({str(x): int(th1[x - 1]) for x in xs}))
    print('phase sample', len(xs))
