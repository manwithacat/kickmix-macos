# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
# Written with AI assistance (Claude, Anthropic) under the repository owner's direction.
"""tools/circuit-viewer/render.py: .kmb parsing, certified width against an independent allocator-style reference,
relabel-invariant hashes, the pure-Python tracer, and the CLI's JSON summary contract. Synthetic circuits only
(no circuit files in the repository); the kickmix cross-check is skipped when kickmix is not importable."""
import contextlib
import importlib.util
import io
import json
import random
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("render", ROOT / "tools" / "circuit-viewer" / "render.py")
R = importlib.util.module_from_spec(spec)
sys.modules["render"] = R  # dataclasses look the module up while the class is created
spec.loader.exec_module(R)

HAS_MPL = importlib.util.find_spec("matplotlib") is not None and importlib.util.find_spec("PIL") is not None
needs_mpl = pytest.mark.skipif(not HAS_MPL, reason="matplotlib/Pillow not installed")

Q1 = [R.X, R.Z, R.R, R.Z_POW]
Q2 = [R.CX, R.CZ, R.SWAP]
Q3 = [R.CCX, R.CCZ, R.CSWAP]


def random_circuit(path, seed, n_ops=400, nq=12, nb=6, hmr=True, conditions=True, reg=(0, 1, 2)):
    """A random valid circuit with resets, measurements, conditions and _IF forms, plus a 'x' register."""
    rng = random.Random(seed)
    ops, st = [], {k: [] for k in R.QSTREAMS + R.BSTREAMS}
    angles = bytearray()
    depth = 0

    def qs(k):
        return rng.sample(range(nq), k)
    for _ in range(n_ops):
        r = rng.random()
        if conditions and r < 0.05 and depth < 2:
            ops.append(R.PUSH)
            st['bc'].append(rng.randrange(nb))
            depth += 1
            continue
        if conditions and r < 0.10 and depth > 0:
            ops.append(R.POP)
            depth -= 1
            continue
        cond = conditions and rng.random() < 0.08
        kind = rng.random()
        if kind < 0.35:
            t = rng.choice(Q2)
            a, b = qs(2)
            st['qq0'].append(a)
            st['qq1'].append(b)
        elif kind < 0.5:
            t = rng.choice(Q3)
            a, b, c = qs(3)
            st['qqq0'].append(a)
            st['qqq1'].append(b)
            st['qqq2'].append(c)
        elif kind < 0.62 and hmr:
            t = R.HMR
            st['q0'].append(qs(1)[0])
            st['b0'].append(rng.randrange(nb))
        elif kind < 0.7:
            t = rng.choice([R.BIT_STORE0, R.BIT_STORE1, R.BIT_INVERT])
            st['b0'].append(rng.randrange(nb))
        else:
            t = rng.choice(Q1)
            st['q0'].append(qs(1)[0])
            if t == R.Z_POW:
                u = rng.getrandbits(128)
                angles += struct_u128(u)
        if cond:
            t |= R.HAS_COND
            st['bc'].append(rng.randrange(nb))
        ops.append(t)
    ops += [R.POP] * depth
    R.write_kmb(path, np.array(ops, np.uint8), {k: np.array(v, np.uint32) for k, v in st.items()}, bytes(angles),
                (), [('x', [(True, q) for q in reg])], num_qubits=nq, num_bits=nb)
    return path


def struct_u128(u):
    return (u & ((1 << 64) - 1)).to_bytes(8, 'little') + (u >> 64).to_bytes(8, 'little')


def reference_profile(c):
    """Independent port of the certified lifetime allocator's live_profile (global sort, no chunking)."""
    t = c.types
    depth = R.condition_depth(t)
    is_reset = ((t == R.R) | (t == R.HMR)) & (depth == 0)
    refq, refop, _ = R.op_major_refs(c, 'q')
    order = np.lexsort((refop, refq))
    sq, so = refq[order], refop[order].astype(np.int64)
    first = np.ones(len(sq), bool)
    first[1:] = sq[1:] != sq[:-1]
    last = np.ones(len(sq), bool)
    last[:-1] = sq[1:] != sq[:-1]
    reset_ref = is_reset[so]
    start = first.copy()
    start[1:] |= reset_ref[:-1] & ~first[1:]
    end = reset_ref | last
    s, e, logical = so[start], so[end], sq[start]
    regs = sorted({q for qs in c.qubit_registers().values() for q in qs})
    keep = ~np.isin(logical, regs)
    delta = np.zeros(c.n + 1, np.int64)
    np.add.at(delta, s[keep], 1)
    np.add.at(delta, e[keep] + 1, -1)
    return np.cumsum(delta)[:c.n] + len(regs)


def run_cli(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = R.main(argv)
    return code, buf.getvalue()


# ------------------------------------------------------------------------------------------------ parsing
def test_read_roundtrip(tmp_path):
    p = random_circuit(tmp_path / 'c.kmb', 1)
    c = R.read_kmb(p)
    assert c.num_qubits == 12 and c.num_bits == 6
    assert c.qubit_registers() == {'x': [0, 1, 2]}
    assert c.n == len(c.types) and c.counts['ops'] == c.n
    zp = int(((c.types & 0x7F) == R.Z_POW).sum())
    assert len(c.angle_units()) == zp


def test_read_errors_have_hints(tmp_path):
    bad = tmp_path / 'bad.kmb'
    bad.write_bytes(b'\0' * 200)
    with pytest.raises(R.UserError) as e:
        R.read_kmb(bad)
    assert 'magic' in str(e.value) and e.value.hint
    with pytest.raises(R.UserError):
        R.read_kmb(tmp_path / 'missing.kmb')
    good = random_circuit(tmp_path / 'g.kmb', 2)
    (tmp_path / 'trunc.kmb').write_bytes(Path(good).read_bytes()[:-5])
    with pytest.raises(R.UserError):
        R.read_kmb(tmp_path / 'trunc.kmb')
    (tmp_path / 'c.kmx').write_text('X q0\n')
    with pytest.raises(R.UserError) as e:
        R.read_kmb(tmp_path / 'c.kmx')
    assert 'kmx' in str(e.value)


# ------------------------------------------------------------------------------------------------ width
def test_width_hand_example(tmp_path):
    # q0 is the register (always live). q1 lives over ops 0..2 (ends at R), q2 over 3..4 (ends at HMR), q3 at 5.
    ops = [R.X, R.CX, R.R, R.X, R.HMR, R.X]
    st = dict(q0=[1, 1, 2, 2, 3], qq0=[0], qq1=[1], b0=[0])
    # op0 X q1, op1 CX q0 q1, op2 R q1, op3 X q2, op4 HMR q2 b0, op5 X q3 (last touch: live at 5 only)
    R.write_kmb(tmp_path / 'h.kmb', np.array(ops, np.uint8), {k: np.array(v, np.uint32) for k, v in st.items()},
                b'', (), [('x', [(True, 0)])])
    c = R.read_kmb(tmp_path / 'h.kmb')
    assert R.width_profile(c).tolist() == [2, 2, 2, 2, 2, 2]
    ops2 = [R.X, R.X, R.CX, R.R, R.R]  # q1 and q2 overlap at op 2
    st2 = dict(q0=[1, 2, 1, 2], qq0=[1], qq1=[2])
    R.write_kmb(tmp_path / 'h2.kmb', np.array(ops2, np.uint8), {k: np.array(v, np.uint32) for k, v in st2.items()},
                b'', (), [('x', [(True, 0)])])
    assert R.width_profile(R.read_kmb(tmp_path / 'h2.kmb')).tolist() == [2, 3, 3, 3, 2]


@pytest.mark.parametrize('seed', range(8))
def test_width_matches_allocator_reference(tmp_path, seed):
    c = R.read_kmb(random_circuit(tmp_path / f'r{seed}.kmb', seed, n_ops=600))
    ref = reference_profile(c)
    for chunk in (4_000_000, 37, 1):
        assert np.array_equal(R.sweep(c, chunk=chunk)['profile'].astype(np.int64), ref)


# ------------------------------------------------------------------------------------------------ structure
def relabelled(src, dst, perm):
    c = R.read_kmb(src)
    st = {n: np.array([perm[int(q)] for q in c.stream(n)], np.uint32) for n in R.QSTREAMS}
    st.update({n: np.array(c.stream(n)) for n in R.BSTREAMS})
    off = c.offsets['angles'][0]
    ang = bytes(c.raw[off:off + 16 * c.counts['angle']])
    regs = [(nm, [(q, perm[i] if q else i) for q, i in cont]) for nm, cont in c.registers]
    R.write_kmb(dst, np.array(c.types), st, ang, (), regs, num_qubits=c.num_qubits, num_bits=c.num_bits)
    return dst


def test_canon_invariant_under_relabelling(tmp_path):
    a = random_circuit(tmp_path / 'a.kmb', 5)
    perm = list(range(12))
    random.Random(3).shuffle(perm)
    b = relabelled(a, tmp_path / 'b.kmb', perm)
    ca, cb = R.read_kmb(a), R.read_kmb(b)
    sa, sb = R.Structure(ca), R.Structure(cb)
    assert sa.canon(0, ca.n) == sb.canon(0, cb.n)
    assert sa.canon(10, 200) == sb.canon(10, 200)
    assert np.array_equal(sa.op_tokens(0, ca.n), sb.op_tokens(0, cb.n))
    other = R.Structure(R.read_kmb(random_circuit(tmp_path / 'o.kmb', 6)))
    assert other.canon(0, 300) != sa.canon(0, 300)


def test_canon_invariant_under_lifetime_recolouring(tmp_path):
    # value A lives on q1 then is reset; value B then uses q2 (version 1) or q1 again (version 2)
    def build(path, wire_b):
        ops = [R.X, R.CX, R.R, R.X, R.CX, R.R]
        st = {'q0': [1, 1, wire_b, wire_b], 'qq0': [0, 0], 'qq1': [1, wire_b]}
        R.write_kmb(path, np.array(ops, np.uint8), {k: np.array(v, np.uint32) for k, v in st.items()}, b'', (),
                    [('x', [(True, 0)])], num_qubits=3)
        return R.read_kmb(path)
    c1, c2 = build(tmp_path / 'l1.kmb', 2), build(tmp_path / 'l2.kmb', 1)
    assert R.Structure(c1).canon(0, 6) == R.Structure(c2).canon(0, 6)


def test_sparse_edits_finds_insertion():
    rng = np.random.default_rng(0)
    a = rng.integers(0, 1 << 20, 200_000)
    b = np.concatenate([a[:120_000], rng.integers(0, 1 << 20, 50), a[120_000:]])
    edits, blocks = R.sparse_edits(a, b)
    assert edits == [(120_000, 120_000, 120_000, 120_050)]
    assert sum(s for _, _, s in blocks) == 200_000


def test_stages_rotation_and_equal(tmp_path):
    c = R.read_kmb(random_circuit(tmp_path / 's.kmb', 7))
    st, src = R.stages_of(c, 'equal:4')
    assert src == 'equal:4' and st[0]['a'] == 0 and st[-1]['b'] == c.n and len(st) == 4
    st, src = R.stages_of(c, 'auto')
    assert src == 'rot' and st[0]['a'] == 0 and st[-1]['b'] == c.n
    assert all(x['b'] == y['a'] for x, y in zip(st, st[1:]))
    with pytest.raises(R.UserError):
        R.stages_of(c, 'tags')


# ------------------------------------------------------------------------------------------------ tracer
def test_python_tracer_semantics(tmp_path):
    # x = q0,q1. CCX q0 q1 q2; Z_POW q2 (1/8 turn); HMR q2 b0 (outcome 0); PUSH b0; X q3; POP; CX_IF q0 q3 if b1
    ops = [R.CCX, R.Z_POW, R.HMR, R.PUSH, R.X, R.POP, R.BIT_STORE1, R.CX | R.HAS_COND]
    st = dict(qqq0=[0], qqq1=[1], qqq2=[2], q0=[2, 2, 3], b0=[0, 1], bc=[0, 1], qq0=[0], qq1=[3])
    eighth = 1 << 125
    R.write_kmb(tmp_path / 't.kmb', np.array(ops, np.uint8), {k: np.array(v, np.uint32) for k, v in st.items()},
                struct_u128(eighth), (), [('x', [(True, 0), (True, 1)])])
    c = R.read_kmb(tmp_path / 't.kmb')
    cuts = np.array([0, 1, 2, 3, c.n])
    res = R.PyTracer(c).run(cuts, {0: 1, 1: 1}, {}, 'zero')
    q = res['qubits']
    assert q[2].tolist() == [0, 1, 1, 0, 0]          # CCX sets q2, HMR clears it
    assert q[3, -1] == 1                             # PUSH b0 (=0) skips X; CX_IF b1 (=1) flips q3 from q0
    assert res['phase_exact'][-1] == str(eighth)     # Z_POW applied once, HMR outcome 0 adds nothing
    assert res['toffolis'][-1] == 1


def test_resolve_inputs_errors(tmp_path):
    c = R.read_kmb(random_circuit(tmp_path / 'i.kmb', 8))
    qv, bv, shown = R.resolve_inputs(c, {'x': 5})
    assert qv == {0: 1, 1: 0, 2: 1} and shown == {'x': 5}
    with pytest.raises(R.UserError):
        R.resolve_inputs(c, {'x': 8})
    with pytest.raises(R.UserError):
        R.resolve_inputs(c, {'nope': 1})


@pytest.mark.skipif(not R.kickmix_importable(), reason='kickmix not installed')
@pytest.mark.parametrize('seed', range(4))
def test_python_tracer_matches_kickmix(tmp_path, seed):
    c = R.read_kmb(random_circuit(tmp_path / f'k{seed}.kmb', seed, hmr=False))
    st, _ = R.stages_of(c, 'equal:5')
    cuts = R.trace_cuts(c, 20, st)
    qv, bv, _ = R.resolve_inputs(c, {'x': 6})
    a = R.PyTracer(c).run(cuts, qv, bv, 'zero')
    b = R.run_kickmix(c, cuts, qv, bv, 1, tmp_path)
    assert np.array_equal(a['qubits'][:, -1], b['qubits'][:c.num_qubits, -1])
    assert np.array_equal(a['qubits'], b['qubits'][:a['qubits'].shape[0]])


# ------------------------------------------------------------------------------------------------ CLI contract
SUMMARY_KEYS = {'ok', 'verb', 'tier', 'basis', 'files', 'rerun', 'tool', 'facts'}


def check_summary(text):
    assert len(text.encode()) <= R.MAX_BYTES
    d = json.loads(text)
    assert SUMMARY_KEYS <= set(d), set(d)
    assert d['ok'] is True
    tier = d['tier'].split('(')[0]
    assert tier in R.TIERS
    assert len(d.get('rows', [])) <= R.MAX_ROWS
    for f in d['files']:
        assert Path(f['path']).exists() and Path(f['path']).stat().st_size == f['bytes']
    return d


@needs_mpl
def test_cli_verbs_json_contract(tmp_path):
    a = str(random_circuit(tmp_path / 'a.kmb', 11, n_ops=500))
    b = str(random_circuit(tmp_path / 'b.kmb', 12, n_ops=520))
    out = str(tmp_path / 'out')
    common = ['--out', out, '--px', '400', '--no-cache']
    for argv in (['width', a, b, '--max-frames', '6', '--tween', '1'] + common,
                 ['diff', a, b, '--max-frames', '4'] + common,
                 ['trace', a, '--set', 'x=5', '--frames', '3', '--columns', '30'] + common,
                 ['cost', a, b] + common):
        code, text = run_cli(argv)
        assert code == 0, text
        d = check_summary(text)
        assert d['verb'] == argv[0]
        kinds = {f['kind'] for f in d['files']}
        assert 'png' in kinds and 'json' in kinds


@needs_mpl
def test_cli_deterministic(tmp_path):
    a = str(random_circuit(tmp_path / 'a.kmb', 21))
    outs = []
    for k in range(2):
        code, text = run_cli(['width', a, '--out', str(tmp_path / f'o{k}'), '--px', '400', '--max-frames', '5',
                              '--no-cache'])
        assert code == 0
        outs.append([Path(f['path']).read_bytes() for f in json.loads(text)['files'] if f['kind'] != 'json'])
    assert outs[0] == outs[1]


def test_cli_errors_are_json_with_hints(tmp_path):
    code, text = run_cli(['width', str(tmp_path / 'missing.kmb'), '--out', str(tmp_path)])
    d = json.loads(text)
    assert code == 2 and d['ok'] is False and 'not found' in d['error']
    code, text = run_cli(['width'])
    d = json.loads(text)
    assert code == 2 and d['ok'] is False and d['hint']
    code, text = run_cli(['frobnicate'])
    assert code == 2 and json.loads(text)['ok'] is False


@needs_mpl
def test_spec_runs_several_jobs(tmp_path):
    a = str(random_circuit(tmp_path / 'a.kmb', 31))
    spec = tmp_path / 'jobs.json'
    spec.write_text(json.dumps({'defaults': ['--out', str(tmp_path / 'o'), '--px', '400', '--no-cache'],
                                'jobs': [['width', a, '--no-gif'], f'cost {a}',
                                         ['width', str(tmp_path / 'nope.kmb')]]}))
    code, text = run_cli(['--spec', str(spec)])
    d = json.loads(text)
    assert code == 1 and d['verb'] == 'spec'
    assert [r['ok'] for r in d['rows']] == [True, True, False]
    assert d['rows'][2]['error']


def test_render_summary_trims_to_budget():
    d = dict(ok=True, verb='x', tier='static', basis='b', files=[], rows=[{'k': 'v' * 200}] * 50,
             next=['n' * 100])
    text = R.render_summary(d)
    assert len(text.encode()) <= R.MAX_BYTES
    out = json.loads(text)
    assert out['rows_total'] == 50 and out['rows_shown'] < 50


# ------------------------------------------------------------------------------------------------ ledger
def ledger_hand_circuit(path):
    """x = (q0, q1, q2) register. q3: AND computed, released by HMR, phase repaid by CZ_IF (accrual).
    q4: the same AND computed again (paying twice), then uncomputed (contra). q5: X then R (dead tail).
    b1 = parity copy of b0 (BIT_INVERT_IF), repaid later by Z_IF on b1: the debt follows the bit."""
    ops = [R.CCX, R.HMR, R.CZ | R.HAS_COND, R.CCX, R.CCX, R.X, R.R, R.HMR, R.BIT_INVERT | R.HAS_COND,
           R.Z | R.HAS_COND]
    st = dict(qqq0=[0, 0, 0], qqq1=[1, 1, 1], qqq2=[3, 4, 4], q0=[3, 5, 5, 4, 0], qq0=[0], qq1=[1],
              b0=[0, 2, 1], bc=[0, 2, 1])
    # op7 HMR q4 b2 (q4 is |0> again: a record nobody needs), op8 b1 ^= b2, op9 Z q0 if b1 repays b2
    R.write_kmb(path, np.array(ops, np.uint8), {k: np.array(v, np.uint32) for k, v in st.items()}, b'', (),
                [('x', [(True, q) for q in (0, 1, 2)])], num_qubits=6, num_bits=3)
    return R.read_kmb(path)


def test_ledger_hand_example(tmp_path):
    c = ledger_hand_circuit(tmp_path / 'l.kmb')
    prof = R.width_profile(c)
    L = R.ledger_core(c, prof)
    v = L['values']
    inv = ~v['reg'] & ~v['init']
    got = {(int(q), int(a)): (R.KIND_NAMES[int(k)], int(t)) for q, a, k, t in
           zip(v['q'][inv], v['a'][inv], v['kind'][inv], v['tof'][inv])}
    assert got[(3, 0)] == ('accrual', 1)
    assert got[(4, 3)][1] == 2  # q4: two Toffoli entries (compute, uncompute), then a needless HMR
    assert got[(5, 5)] == ('dead-tail', 0)
    li = L['liab']
    assert li['op'].tolist() == [1, 7]
    assert li['settle'].tolist() == [2, 9]  # the second record is repaid through the BIT_INVERT_IF copy
    rep = L['repeats']
    assert rep['groups'] == 1 and rep['repeats'] == 2 and rep['uncompute_pairs'] == 1
    ex = R.ExactLedger(c, rows=np.arange(1, 8))
    runs = [ex.run('zero'), ex.run(7)]
    X = R.exact_repeats(runs, ex.W)
    assert [g for g in X['groups']] == [[0, 3, 4]]
    assert all(r['x_restored'] and not r['dirty'] for r in runs)


@pytest.mark.parametrize('seed', range(4))
def test_ledger_values_reproduce_width(tmp_path, seed):
    c = R.read_kmb(random_circuit(tmp_path / 'c.kmb', 100 + seed, n_ops=600))
    prof = R.width_profile(c)
    v = R.ledger_core(c, prof)['values']
    nr = ~v['reg']
    d = np.zeros(c.n + 1, np.int64)
    np.add.at(d, v['a'][nr], 1)
    np.add.at(d, v['b'][nr] + 1, -1)
    assert np.array_equal(np.cumsum(d[:c.n]) + len(R.register_qubit_ids(c)), prof.astype(np.int64))


def test_exact_ledger_matches_python_tracer(tmp_path):
    c = R.read_kmb(random_circuit(tmp_path / 'c.kmb', 7, n_ops=500, nq=10))
    rows = np.arange(0, 8)
    ex = R.ExactLedger(c, rows=rows)
    res = ex.run('zero')
    tr = R.PyTracer(c)
    xq = c.qubit_registers()['x']
    tof = 0
    for x in rows.tolist():
        out = tr.run([c.n], {q: (x >> j) & 1 for j, q in enumerate(xq)}, {}, 'zero')
        tof += int(out['toffolis'][-1])
    assert abs(res['mean_toffoli'] - tof / len(rows)) < 1e-9


@needs_mpl
def test_cli_ledger_contract(tmp_path):
    a = str(random_circuit(tmp_path / 'a.kmb', 41, n_ops=500))
    b = str(random_circuit(tmp_path / 'b.kmb', 42, n_ops=520))
    common = ['--out', str(tmp_path / 'o'), '--px', '500', '--no-cache']
    code, text = run_cli(['ledger', a] + common)
    assert code == 0, text
    d = check_summary(text)
    assert d['verb'] == 'ledger' and d['tier'] == 'static'
    assert {f['kind'] for f in d['files']} == {'png', 'csv', 'json'}
    assert {'values', 'liabilities', 'paying_twice', 'close'} <= set(d['facts'])
    full = json.loads(Path([f['path'] for f in d['files'] if f['kind'] == 'json'][0]).read_text())
    assert {'trial_balance', 'aged_values', 'aged_debts', 'paying_twice', 'close'} <= set(full)
    code, text = run_cli(['ledger', '--compare', a, b] + common)
    assert code == 0, text
    d = check_summary(text)
    assert {'capital_teq', 'expense_teq', 'net_teq', 'reading'} <= set(d['facts'])
    code, text = run_cli(['ledger', a, '--task', 'invsqrt', '--models', 'zero'] + common + ['--name', 'ex'])
    assert code == 0, text
    d = check_summary(text)
    assert d['tier'] == 'exact-all-x' and d['facts']['close']['tier'] == 'exact-all-x'
    code, text = run_cli(['ledger', a, b] + common)
    assert code == 2 and '--compare' in json.loads(text)['hint']


@needs_mpl
def test_cli_ledger_deterministic(tmp_path):
    a = str(random_circuit(tmp_path / 'a.kmb', 43))
    outs = []
    for k in range(2):
        code, text = run_cli(['ledger', a, '--out', str(tmp_path / f'o{k}'), '--px', '400', '--no-cache'])
        assert code == 0
        outs.append([Path(f['path']).read_bytes() for f in json.loads(text)['files'] if f['kind'] != 'json'])
    assert outs[0] == outs[1]


# ------------------------------------------------------------------------------------------------ viewer snapshot
# The viewer's "Copy snapshot JSON" (tools/circuit-viewer/snapshot.js on tracer.js, run here under Node) and
# `render.py snapshot` must give the same object at the same stop point. Static parts (width profile, values,
# liabilities) are also compared array by array.
import shutil  # noqa: E402
import subprocess  # noqa: E402

VIEWER = ROOT / "tools" / "circuit-viewer"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js not installed")
VIEWER_CIRCUITS = ["", "circuits/9a8627", "circuits/0766a111"]
MAXX = 3133452


def node_snapshots(ops_path, stages_path, points):
    r = subprocess.run([NODE, str(ROOT / "tests" / "viewer" / "snapshot_cli.js"), str(ops_path),
                        str(stages_path) if stages_path else "-", json.dumps(points)],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def assert_same(a, b, where="snapshot"):
    if isinstance(a, dict):
        assert isinstance(b, dict) and set(a) == set(b), f"{where}: keys {sorted(set(a) ^ set(b))}"
        for k in a:
            assert_same(a[k], b[k], f"{where}.{k}")
    elif isinstance(a, list):
        assert isinstance(b, list) and len(a) == len(b), f"{where}: lengths {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            assert_same(x, y, f"{where}[{i}]")
    else:
        assert a == b and type(a) is type(b) or (isinstance(a, (int, float)) and not isinstance(a, bool)
                                                 and not isinstance(b, bool) and a == b), f"{where}: {a!r} vs {b!r}"


def stop_points(ops, xs):
    """Fixed stop points: ends, checkpoint edges, the middle, and the first op of several kinds."""
    n = len(ops)
    first = lambda pred: next(i for i, o in enumerate(ops) if pred(o))  # noqa: E731
    picks = [0, 1, 999, 1000, 1001, n // 2, n - 1,
             first(lambda o: o[0] == 9), first(lambda o: o[0] == 11), first(lambda o: o[0] == 15),
             first(lambda o: o[0] in (2, 5, 7)), first(lambda o: o[0] not in (11, 15, 16) and o[-1] >= 0)]
    pts = [[op, x, 1, 1] for x in xs for op in sorted(set(min(n - 1, p) for p in picks))]
    pts += [[n // 3, xs[1], 1, 0], [n - 1, xs[1], 1, 0], [n // 3, xs[-1], 7, 1], [2 * n // 3, xs[0], 7, 0]]
    return pts


def static_python(st):
    vals = sorted(([v['q'], v['a'], v['b'], v['kind'], int(v['reg']), int(v['init'])] for v in st.values))
    return dict(profile=st.profile, values=vals,
                liabilities=[[li['op'], li['qubit'], li['settle'], li['reads']] for li in st.liab], fixups=st.fix)


@needs_node
@pytest.mark.parametrize("sub", VIEWER_CIRCUITS)
def test_viewer_snapshot_parity(sub):
    ops_path, stages_path = VIEWER / sub / "ops.json", VIEWER / sub / "stages.json"
    data = json.loads(ops_path.read_text())
    stages, groups = R.load_viewer_stages(str(stages_path), str(ops_path))
    st = R.ViewerStatic(data, stages, groups)
    pts = stop_points(data["ops"], [1, 161, MAXX])
    js = node_snapshots(ops_path, stages_path, pts)
    assert_same(js["static"], static_python(st), "static")
    for (op, x, seed, kick), snap in zip(pts, js["snapshots"]):
        py = R.viewer_snapshot(data, st, op, x, seed, bool(kick))
        assert_same(snap, py, f"{sub or '67482db6c538'} op={op} x={x} seed={seed} kick={kick}")
        tb = py["ledger"]["trial_balance"]
        assert tb["live"] == tb["registers"] + tb["open_values"] + tb["closing_here"]
        assert tb["records"] == tb["repaid"] + tb["open_debts"] + tb["never_repaid"]
        assert tb["opened"] - sum(tb["closed"].values()) == tb["open_values"]


@needs_node
@pytest.mark.parametrize("seed", [3, 4])
def test_viewer_snapshot_parity_random(tmp_path, seed):
    """Random circuits with conditions, conditional HMRs and bit copies: the liability and lifetime ports."""
    c = R.read_kmb(random_circuit(tmp_path / "r.kmb", seed, n_ops=2500, nq=12, nb=6))
    data = R.viewer_data_from_kmb(c)
    ops_path = tmp_path / "ops.json"
    ops_path.write_text(json.dumps(data))
    st = R.ViewerStatic(data, [], [])
    assert st.profile == R.width_profile(c).tolist()
    n = len(data["ops"])
    pts = [[op, x, s, k] for op in (0, 7, n // 2, n - 1, 1000, 1999) for x in (1, 5) for s, k in ((1, 1), (9, 0))]
    js = node_snapshots(ops_path, None, pts)
    assert_same(js["static"], static_python(st), "static")
    for (op, x, s, k), snap in zip(pts, js["snapshots"]):
        assert_same(snap, R.viewer_snapshot(data, st, op, x, s, bool(k)), f"op={op} x={x}")


def test_viewer_ops_kmb_roundtrip(tmp_path):
    c = R.read_kmb(random_circuit(tmp_path / "r.kmb", 5, n_ops=800))
    data = R.viewer_data_from_kmb(c)
    R.kmb_from_viewer_data(data, tmp_path / "back.kmb")
    back = R.read_kmb(tmp_path / "back.kmb")
    assert R.viewer_data_from_kmb(back)["ops"] == data["ops"]
    assert np.array_equal(R.width_profile(back), R.width_profile(c))
    ops = json.loads((VIEWER / "ops.json").read_text())
    R.kmb_from_viewer_data(ops, tmp_path / "v.kmb")
    again = R.viewer_data_from_kmb(R.read_kmb(tmp_path / "v.kmb"))
    assert again["ops"] == ops["ops"] and again["zpow_exact"] == ops["zpow_exact"]


def test_cli_snapshot_contract(tmp_path):
    ops = str(VIEWER / "ops.json")
    code, text = run_cli(["snapshot", ops, "--op", "15213", "--x", "161", "--out", str(tmp_path)])
    assert code == 0, text
    d = check_summary(text)
    assert d["verb"] == "snapshot" and d["facts"]["stage"] == "coeff_lookup"
    full = json.loads(Path(d["files"][0]["path"]).read_text())
    code, text = run_cli(["snapshot", ops, "--op", "15213", "--x", "161", "--print"])
    assert code == 0 and json.loads(text) == full and full["schema"] == R.SNAPSHOT_SCHEMA
    code, text = run_cli(["snapshot", ops, "--op", "99999999"])
    assert code == 2 and "outside" in json.loads(text)["error"]


@needs_node
def test_viewer_playhead_node():
    # Name the test files: older Node (as on CI runners) treats a directory argument to --test as a module path.
    files = sorted(str(p) for p in (ROOT / "tests" / "viewer").glob("*.test.js"))
    assert files, "no viewer tests found"
    r = subprocess.run([NODE, "--test", *files], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
