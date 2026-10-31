#!/usr/bin/env python3
# Copyright 2026 James Barlow
# SPDX-License-Identifier: Apache-2.0
# Written with AI assistance (Claude, Anthropic) under the repository owner's direction.
"""kickmix-render: headless explanatory images and GIFs from kickmix circuit files (.kmb).

One entry point, one verb per picture. Every run prints one short JSON summary on stdout (files written, sizes,
key numbers and their evidence tier) and nothing else, so agents can parse it.

  width   live qubits along the circuit, stage by stage, with zoom (PNG + GIF); one or two circuits
  diff    what changed between two circuits, aligned by stage: modules spliced, ops added/removed (PNG + GIF)
  trace   follow one shot's qubit and register values through the circuit (PNG + GIF)
  cost    static cost dashboard: Q, Toffolis, cost and score ratio per stage (PNG)
  ledger  double-entry view: values, phase debts, capital and expense, trial balance per stage (PNG + CSV);
          values idle at the peak, full-width runs no release can narrow, fewest releases covering the rest;
          `ledger --compare A B` reconciles two circuits
  snapshot  the circuit viewer's stop point as JSON (gate, width, cost, phase, registers, ledger at the cut)
  doctor  check dependencies (numpy, matplotlib, Pillow; kickmix only for `trace --engine kickmix`)

Quick start:
  python render.py doctor
  python render.py width a.kmb                     # a_width.png + a_width.gif in ./render-out
  python render.py width a.kmb b.kmb --out figs    # comparison, B aligned to A stage by stage
  python render.py diff a.kmb b.kmb
  python render.py trace a.kmb --set x=161
  python render.py cost a.kmb b.kmb
  python render.py ledger a.kmb                    # a_ledger.png + .csv + .json
  python render.py ledger --compare a.kmb b.kmb    # reconciliation of two ledgers
  python render.py --spec jobs.json                # several renders, one summary

`python render.py <verb> --help` lists every option with copy-paste examples. RENDER.md documents the output
schema and limits. Static verbs (width, diff, cost, ledger) need only numpy, matplotlib and Pillow; they never run a
circuit. Circuit files are treated as untrusted data: parsed, never executed.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import io
import json
import math
import os
import random
import shlex
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

TOOL_VERSION = '0.1.0'
DEFAULT_OUT = 'render-out'
MAX_ROWS = 20
MAX_BYTES = 2048
DEFAULT_GIF_MB = 5.0
TIERS = ['static', 'sampled', 'exact-all-x', 'engine-parity', 'official-local', 'official-hosted']

# Official reference costs (the competition's reference circuits, measured with the official verifiers) and the
# DQI repetition factor for zero failures in the default 102,400 shots (Clopper-Pearson upper bound).
REFERENCE_COST = {'invsqrt': 2_076_774.23, 'dqi': 24_439_463_583_672.93}
DQI_R_ZERO_FAIL = 1.1913
INVSQRT_ROT_T = 25.0  # T per non-Clifford, non-T rotation; the verifier derives it from the error slack


class UserError(Exception):
    """An error the caller can fix; `hint` says how."""

    def __init__(self, msg, hint=None):
        super().__init__(msg)
        self.hint = hint


def _np():
    try:
        import numpy as np
    except ImportError as e:  # pragma: no cover
        raise UserError('numpy is not installed', 'pip install numpy matplotlib pillow') from e
    return np


np = _np()

# ------------------------------------------------------------------------------------------------ kmb format
# Layout follows kickmix Circuit::write_kmb_to (src/kickmix/circuit/circuit.cc in this repository): a 16-byte magic,
# u32 version/num_qubits/num_bits/num_registers, nine u64 counts, register packets, then one packet per stream.
# Op types: src/kickmix/circuit/op_type.h.
MAGIC = bytes.fromhex('d750c7d5c329d326e3cc9f6834f2b8bf')
HAS_1Q, HAS_2Q, HAS_3Q, HAS_1B, HAS_COND = 0x10, 0x20, 0x30, 0x40, 0x80
NEG, POP, INFO_TAG = 0x00, 0x01, 0x06
Z, X, R, Z_POW, HMR = 0x10, 0x11, 0x12, 0x13, 0x50
CZ, CX, SWAP, CCZ, CCX, CSWAP = 0x20, 0x21, 0x22, 0x30, 0x31, 0x32
BIT_STORE0, BIT_STORE1, BIT_INVERT = 0x40, 0x41, 0x42
PUSH = 0x81
QSTREAMS = ['qqq0', 'qqq1', 'qqq2', 'qq0', 'qq1', 'q0']
BSTREAMS = ['b0', 'bc']
OP_NAMES = {0x00: 'NEG', 0x01: 'POP_CONDITION', 0x06: 'INFO_TAG', 0x07: 'DEBUG_PRINT', 0x40: 'BIT_STORE0',
            0x41: 'BIT_STORE1', 0x42: 'BIT_INVERT', 0x47: 'DEBUG_PRINT', 0x10: 'Z', 0x11: 'X', 0x12: 'R',
            0x13: 'Z_POW', 0x17: 'DEBUG_PRINT', 0x50: 'HMR', 0x20: 'CZ', 0x21: 'CX', 0x22: 'SWAP', 0x30: 'CCZ',
            0x31: 'CCX', 0x32: 'CSWAP', 0x81: 'PUSH_CONDITION'}


def op_name(t):
    t = int(t)
    if t == PUSH:
        return 'PUSH_CONDITION'
    base = OP_NAMES.get(t & 0x7F, hex(t))
    return base + '_IF' if t & HAS_COND else base


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 22), b''):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Circuit:
    path: str
    raw: object
    num_qubits: int
    num_bits: int
    counts: dict
    registers: list  # [(name, [(is_qubit, id)])]
    offsets: dict = field(default_factory=dict)
    _sha: str | None = None
    _tags: list | None = None
    _cache: dict = field(default_factory=dict)

    @property
    def n(self):
        return int(self.counts['ops'])

    @property
    def types(self):
        return self.stream('op_types')

    @property
    def sha256(self):
        if self._sha is None:
            self._sha = sha256_file(self.path)
        return self._sha

    @property
    def sha12(self):
        return self.sha256[:12]

    @property
    def label(self):
        return self.sha12

    def stream(self, name):
        off, n, dt = self.offsets[name]
        return np.frombuffer(self.raw, dtype=dt, count=n, offset=off)

    def tags(self):
        if self._tags is None:
            off, n, _ = self.offsets['info_tags']
            ends = np.frombuffer(self.raw, dtype='<u8', count=n, offset=off).tolist()
            chars = bytes(self.raw[off + 8 * n: off + 8 * n + self.counts['info_tag_chars']])
            out, start = [], 0
            for e in ends:
                out.append(chars[start:e].decode('utf-8', 'replace'))
                start = e
            self._tags = out
        return self._tags

    def qubit_registers(self):
        return {name: [i for is_q, i in c if is_q] for name, c in self.registers if any(q for q, _ in c)}

    def bit_registers(self):
        return {name: [i for is_q, i in c if not is_q] for name, c in self.registers if any(not q for q, _ in c)}

    def angle_units(self):
        """Z_POW angles as Python ints in units of 2^-128 turn (FixedPrecisionAngle128: two little-endian u64)."""
        off, n, _ = self.offsets['angles']
        w = np.frombuffer(self.raw, dtype='<u8', count=2 * n, offset=off).reshape(n, 2)
        return [(int(hi) << 64) | int(lo) for lo, hi in w.tolist()]


def read_kmb(path) -> Circuit:
    """Parse a version-2 .kmb file (memory-mapped). Raises UserError with a fix-it hint on anything unexpected."""
    p = Path(path)
    if not p.exists():
        raise UserError(f'circuit file not found: {path}', 'check the path; circuits are .kmb files')
    if p.suffix.lower() == '.kmx':
        raise UserError(f'{path} is kickmix text (.kmx); render.py reads binary .kmb',
                        'convert with kickmix: python -c "import kickmix as km; '
                        "km.Circuit.from_file('c.kmx', format='auto').to_file('c.kmb', format='kmb')\"")
    size = p.stat().st_size
    if size < 120:
        raise UserError(f'{path} is too small to be a .kmb circuit ({size} bytes)')
    raw = np.memmap(p, dtype=np.uint8, mode='r')
    if bytes(raw[:16]) != MAGIC:
        raise UserError(f'{path} is not a kickmix .kmb file (bad magic bytes)',
                        'pass a binary circuit written by kickmix Circuit.to_file(..., format="kmb")')
    buf = memoryview(raw)
    pos = 16
    version, num_qubits, num_bits, num_regs = struct.unpack_from('<4I', buf, pos)
    if version != 2:
        raise UserError(f'{path}: .kmb format version {version} is not supported (expected 2)',
                        're-save the circuit with a current kickmix')
    pos += 16
    keys = ['ops', 'qqq', 'qq', 'q', 'b', 'c', 'angle', 'info_tags', 'info_tag_chars']
    counts = dict(zip(keys, struct.unpack_from('<9Q', buf, pos)))
    pos += 72
    bad = UserError(f'{path}: truncated or corrupt .kmb', 're-download or re-export the circuit')
    registers = []
    try:
        for _ in range(num_regs):
            ptype, _size = struct.unpack_from('<2Q', buf, pos)
            if ptype != 0:
                raise bad
            pos += 16
            name_len, reg_len = struct.unpack_from('<2I', buf, pos)
            pos += 8
            name = bytes(buf[pos:pos + name_len]).decode('utf-8', 'replace')
            pos += name_len
            vals = np.frombuffer(raw, dtype='<u4', count=reg_len, offset=pos).tolist()
            pos += 4 * reg_len
            registers.append((name, [(bool(v & 0x80000000), v & 0x7FFFFFFF) for v in vals]))
        c = Circuit(str(p), raw, int(num_qubits), int(num_bits), counts, registers)

        def packet(expected, n, dt, name):
            nonlocal pos
            ptype, size_ = struct.unpack_from('<2Q', buf, pos)
            if ptype != expected or size_ != n * np.dtype(dt).itemsize:
                raise bad
            c.offsets[name] = (pos + 16, int(n), dt)
            pos += 16 + size_

        packet(1, counts['ops'], 'u1', 'op_types')
        for i, (nm, cnt) in enumerate([('qqq0', 'qqq'), ('qqq1', 'qqq'), ('qqq2', 'qqq'), ('qq0', 'qq'),
                                        ('qq1', 'qq'), ('q0', 'q'), ('b0', 'b'), ('bc', 'c')]):
            packet(2 + i, counts[cnt], '<u4', nm)
        packet(10, counts['angle'], 'V16', 'angles')
        ptype, size_ = struct.unpack_from('<2Q', buf, pos)
        (ntags,) = struct.unpack_from('<Q', buf, pos + 16)
        if ptype != 11 or ntags != counts['info_tags']:
            raise bad
        c.offsets['info_tags'] = (pos + 24, int(ntags), '<u8')
        pos += 16 + size_
    except struct.error:
        raise bad from None
    if pos != size:
        raise bad
    t = c.types
    cls = t & 0x30
    if (int((cls == 0x30).sum()) != counts['qqq'] or int((cls == 0x20).sum()) != counts['qq']
            or int((cls == 0x10).sum()) != counts['q']):
        raise bad
    return c


def write_kmb(path, op_types, streams, angles=b'', tags=(), registers=(), num_qubits=None, num_bits=None):
    """Write a version-2 .kmb from arrays (used to cut circuits into segments for the kickmix trace engine)."""
    ot = np.ascontiguousarray(op_types, dtype=np.uint8)
    s = {n: np.ascontiguousarray(streams.get(n, np.zeros(0, np.uint32)), dtype='<u4') for n in QSTREAMS + BSTREAMS}
    qmax = max([int(s[n].max()) for n in QSTREAMS if len(s[n])] + [-1])
    bmax = max([int(s[n].max()) for n in BSTREAMS if len(s[n])] + [-1])
    for _, contents in registers:
        for is_q, i in contents:
            qmax, bmax = (max(qmax, i), bmax) if is_q else (qmax, max(bmax, i))
    nq = qmax + 1 if num_qubits is None else num_qubits
    nb = bmax + 1 if num_bits is None else num_bits
    tag_bytes = [t.encode() if isinstance(t, str) else bytes(t) for t in tags]
    ends = np.cumsum([len(t) for t in tag_bytes]).astype('<u8') if tag_bytes else np.zeros(0, '<u8')
    chars = b''.join(tag_bytes)
    angles = bytes(angles)
    out = bytearray(MAGIC)
    out += struct.pack('<4I', 2, nq, nb, len(registers))
    out += struct.pack('<9Q', len(ot), len(s['qqq0']), len(s['qq0']), len(s['q0']), len(s['b0']), len(s['bc']),
                       len(angles) // 16, len(tag_bytes), len(chars))
    for name, contents in registers:
        nm = name.encode()
        out += struct.pack('<2Q', 0, len(nm) + 4 * len(contents) + 8)
        out += struct.pack('<2I', len(nm), len(contents)) + nm
        for is_q, i in contents:
            out += struct.pack('<I', i | (0x80000000 if is_q else 0))
    out += struct.pack('<2Q', 1, len(ot)) + ot.tobytes()
    for i, n in enumerate(QSTREAMS + BSTREAMS):
        out += struct.pack('<2Q', 2 + i, 4 * len(s[n])) + s[n].tobytes()
    out += struct.pack('<2Q', 10, len(angles)) + angles
    out += struct.pack('<2Q', 11, 8 * (len(tag_bytes) + 1) + len(chars)) + struct.pack('<Q', len(tag_bytes))
    out += ends.tobytes() + chars
    Path(path).write_bytes(bytes(out))


class SeqSlicer:
    """Cuts consecutive op ranges [a, b), [b, c), ... into standalone segments, keeping running stream offsets
    (constant memory, so DQI-size circuits can be cut too)."""

    GROUPS = {'qqq0': 'qqq', 'qqq1': 'qqq', 'qqq2': 'qqq', 'qq0': 'qq', 'qq1': 'qq', 'q0': 'q', 'b0': 'b', 'bc': 'c'}

    def __init__(self, c: Circuit):
        self.c, self.pos = c, 0
        self.off = dict(qqq=0, qq=0, q=0, b=0, c=0, angle=0)

    def write(self, path, a, b):
        if a != self.pos:
            raise ValueError(f'segments must be consecutive (expected start {self.pos}, got {a})')
        c = self.c
        t = np.asarray(c.types[a:b])
        q = t & 0x30
        cnt = dict(qqq=int((q == 0x30).sum()), qq=int((q == 0x20).sum()), q=int((q == 0x10).sum()),
                   b=int(((t & HAS_1B) > 0).sum()), c=int(((t & HAS_COND) > 0).sum()),
                   angle=int(((t & 0x7F) == Z_POW).sum()))
        o = self.off
        st = {n: c.stream(n)[o[g]:o[g] + cnt[g]] for n, g in self.GROUPS.items()}
        aoff = c.offsets['angles'][0]
        ang = bytes(c.raw[aoff + 16 * o['angle']: aoff + 16 * (o['angle'] + cnt['angle'])])
        keep = t != INFO_TAG  # tags have no effect on behaviour
        # kickmix wants header counts equal to the largest id used + 1, so let write_kmb derive them
        write_kmb(path, t[keep], st, ang, (), ())
        for g in o:
            o[g] += cnt[g]
        self.pos = b


# ------------------------------------------------------------------------------------------------ analysis
def condition_depth(t):
    """Condition-stack depth in force at each op (a PUSH is itself unconditional)."""
    delta = np.zeros(len(t), dtype=np.int8)
    delta[t == PUSH] = 1
    delta[t == POP] = -1
    after = np.cumsum(delta, dtype=np.int32)
    return after - delta


def op_major_refs(c: Circuit, kind='q'):
    """Operand references in op order: (ref_q, ref_op, start) with start[i] the first ref of op i (len n+1).

    kind 'q': qubit operands (qqq0..2, qq0..1, q0 in slot order); kind 'b': bit operands (b0 then bc)."""
    t = c.types
    n = len(t)
    if kind == 'q':
        cls = t & 0x30
        ar = np.zeros(n, np.uint8)
        ar[cls == 0x30], ar[cls == 0x20], ar[cls == 0x10] = 3, 2, 1
        groups = [(cls == 0x30, ['qqq0', 'qqq1', 'qqq2']), (cls == 0x20, ['qq0', 'qq1']), (cls == 0x10, ['q0'])]
    else:
        hb, hc = (t & HAS_1B) > 0, (t & HAS_COND) > 0
        ar = hb.astype(np.uint8) + hc.astype(np.uint8)
        groups = None
    start = np.zeros(n + 1, np.int64)
    np.cumsum(ar, out=start[1:])
    total = int(start[-1])
    refq = np.empty(total, np.uint32)
    if kind == 'q':
        for mask, names in groups:
            p = start[:-1][mask]
            for j, nm in enumerate(names):
                refq[p + j] = c.stream(nm)
            del p
    else:
        p = start[:-1][hb]
        refq[p] = c.stream('b0')
        p = start[:-1][hc] + hb[hc]
        refq[p] = c.stream('bc')
        del p
    refop = np.repeat(np.arange(n, dtype=np.uint32), ar)
    return refq, refop, start


def register_qubit_ids(c: Circuit):
    return sorted({q for qs in c.qubit_registers().values() for q in qs})


def sweep(c: Circuit, back=False, chunk=4_000_000):
    """One pass over the op stream in chunks (bounded memory), tracking every qubit's current value lifetime.

    A lifetime (the certified lifetime allocator's interval) starts at a wire's first touch or its first touch after
    an unconditional R/HMR (condition depth 0), and ends at such a reset or at the wire's last touch. Register qubits
    are live for the whole circuit. Returns dict(profile=int16 live qubits after each op, and with back=True
    qstart (first qubit ref of each op, op-major) and qback (op distance from each qubit ref to the previous touch of
    the same value, 0 when the ref starts a lifetime))."""
    t = c.types
    n = c.n
    regs = register_qubit_ids(c)
    maxq = max([c.num_qubits] + [int(c.stream(nm).max()) + 1 for nm in QSTREAMS if len(c.stream(nm))]
               + [q + 1 for q in regs])
    is_reg = np.zeros(maxq, bool)
    is_reg[regs] = True
    open_ = np.zeros(maxq, bool)
    last = np.full(maxq, -1, np.int64)
    delta = np.zeros(n + 1, np.int32)
    out = {}
    if back:
        nref = 3 * c.counts['qqq'] + 2 * c.counts['qq'] + c.counts['q']
        qstart = np.empty(n + 1, np.uint32 if nref < 2**32 else np.int64)
        qback = np.empty(nref, np.int32)
    streams = {nm: c.stream(nm) for nm in QSTREAMS}
    off = dict(qqq=0, qq=0, q=0)
    depth0, rpos = 0, 0
    for a in range(0, n, chunk):
        b = min(n, a + chunk)
        tt = np.asarray(t[a:b])
        dl = np.zeros(len(tt), np.int32)
        dl[tt == PUSH] = 1
        dl[tt == POP] = -1
        after = np.cumsum(dl, dtype=np.int32) + depth0
        reset_op = ((tt == R) | (tt == HMR)) & ((after - dl) == 0)
        depth0 = int(after[-1])
        del dl, after
        cls = tt & 0x30
        ar = np.zeros(len(tt), np.uint8)
        groups = [(cls == 0x30, 'qqq', ['qqq0', 'qqq1', 'qqq2']), (cls == 0x20, 'qq', ['qq0', 'qq1']),
                  (cls == 0x10, 'q', ['q0'])]
        for mask, _, names in groups:
            ar[mask] = len(names)
        st = np.zeros(len(tt) + 1, np.int64)
        np.cumsum(ar, out=st[1:])
        refq = np.empty(int(st[-1]), np.uint32)
        for mask, g, names in groups:
            p = st[:-1][mask]
            k = len(p)
            for j, nm in enumerate(names):
                refq[p + j] = streams[nm][off[g]:off[g] + k]
            off[g] += k
        refo = np.repeat(np.arange(a, b, dtype=np.int64), ar)
        if back:
            qstart[a:b + 1] = st + rpos
        order = np.argsort(refq, kind='stable')
        sq, so = refq[order], refo[order]
        del refo
        first = np.ones(len(sq), bool)
        first[1:] = sq[1:] != sq[:-1]
        rr = reset_op[so - a]
        start = np.empty(len(sq), bool)
        start[1:] = rr[:-1]
        start[first] = ~open_[sq[first]]
        nonreg = ~is_reg[sq]
        delta[a:b + 1] += np.bincount(so[start & nonreg] - a, minlength=b - a + 1).astype(np.int32)
        delta[a:b + 1] -= np.bincount(so[rr & nonreg] - a + 1, minlength=b - a + 1).astype(np.int32)
        if back:
            prev = np.empty(len(sq), np.int64)
            prev[1:] = so[:-1]
            prev[first] = last[sq[first]]
            d = so - prev
            d[start] = 0
            qback[rpos + order] = np.minimum(d, 2**31 - 1).astype(np.int32)
            del prev, d
        lastm = np.ones(len(sq), bool)
        lastm[:-1] = first[1:]
        lq = sq[lastm]
        open_[lq] = ~rr[lastm]
        last[lq] = so[lastm]
        rpos += len(refq)
        del refq, order, sq, so, first, rr, start, nonreg
    oq = np.flatnonzero(open_ & ~is_reg)
    np.subtract.at(delta, last[oq] + 1, 1)
    out['profile'] = (np.cumsum(delta[:n], dtype=np.int32) + len(regs)).astype(np.int16)
    if back:
        out['qstart'], out['qback'] = qstart, qback
    return out


def width_profile(c: Circuit):
    """Live qubits after each op (int16, length n): the certified width at every point. Its maximum is the number
    of wires a lifetime-respecting relabelling needs (what the lifetime allocator reaches)."""
    return sweep(c)['profile']


class Structure:
    """Relabel-invariant hashing of op ranges: two ranges hash equal iff they have the same op types, angles and
    the same pattern of value and bit reuse inside the range. Qubit and bit ids may differ, and so may the wire a
    value lives on after a reset (lifetime recolouring)."""

    def __init__(self, c: Circuit, sw=None):
        self.c = c
        self.t = c.types
        sw = sw or sweep(c, back=True)
        self.qstart, self.qback = sw['qstart'], sw['qback']
        t = self.t
        hb, hc = (t & HAS_1B) > 0, (t & HAS_COND) > 0
        self.bidx = np.flatnonzero(hb | hc)  # ops with bit operands (few, compared with qubit ops)
        hb, hc = hb[self.bidx], hc[self.bidx]
        ar = hb.astype(np.int64) + hc
        self.bst = np.zeros(len(self.bidx) + 1, np.int64)
        np.cumsum(ar, out=self.bst[1:])
        bq = np.empty(int(self.bst[-1]), np.uint32)
        bq[self.bst[:-1][hb]] = c.stream('b0')
        bq[self.bst[:-1][hc] + hb[hc]] = c.stream('bc')
        self.bop = np.repeat(self.bidx, ar)
        order = np.argsort(bq, kind='stable')
        sb, sob = bq[order], self.bop[order]
        d = np.zeros(len(sb), np.int64)
        d[1:] = sob[1:] - sob[:-1]
        fb = np.ones(len(sb), bool)
        fb[1:] = sb[1:] != sb[:-1]
        d[fb] = 0
        self.bback = np.empty(len(d), np.int32)
        self.bback[order] = np.minimum(d, 2**31 - 1)
        del bq, order, sb, sob, d
        self.zidx = np.flatnonzero((t & 0x7F) == Z_POW)
        self.aoff = c.offsets['angles'][0]

    def _brange(self, a, b):
        i0, i1 = np.searchsorted(self.bidx, [a, b])
        return int(self.bst[i0]), int(self.bst[i1])

    def _qops(self, a, b):
        return np.repeat(np.arange(a, b, dtype=np.int64), np.diff(self.qstart[a:b + 1].astype(np.int64)))

    def _local(self, back, ops, a):
        loc = back.astype(np.int64)
        loc[(loc == 0) | (loc > ops.astype(np.int64) - a)] = -1
        return loc

    def canon(self, a, b):
        h = hashlib.sha256()
        h.update(np.ascontiguousarray(self.t[a:b]).tobytes())
        r0, r1 = int(self.qstart[a]), int(self.qstart[b])
        h.update(self._local(self.qback[r0:r1], self._qops(a, b), a).tobytes())
        r0, r1 = self._brange(a, b)
        h.update(self._local(self.bback[r0:r1], self.bop[r0:r1], a).tobytes())
        z0, z1 = np.searchsorted(self.zidx, [a, b])
        h.update(bytes(self.c.raw[self.aoff + 16 * int(z0): self.aoff + 16 * int(z1)]))
        return h.hexdigest()[:16]

    def op_tokens(self, a, b, cap=32):
        """One relabel-invariant int per op: type plus capped back-distances of its qubit operands."""
        t = self.t[a:b].astype(np.int64)
        tok = t.copy()
        qs = self.qstart[a:b + 1].astype(np.int64)
        loc = self._local(self.qback[qs[0]:qs[-1]], self._qops(a, b), a)
        loc = np.where(loc < 0, cap + 1, np.minimum(loc, cap)).astype(np.int64)
        ar = np.diff(qs)
        rel = qs[:-1] - qs[0]
        for j in range(3):
            has = ar > j
            v = np.zeros(len(t), np.int64)
            v[has] = loc[rel[has] + j]
            tok = tok * 64 + v
        return tok


def tag_tree(c: Circuit):
    """All @enter:/@exit: regions as (depth, name, a, b) with b exclusive, in order of their start."""
    t = c.types
    if c.counts['info_tags'] == 0:
        return []
    pos = np.flatnonzero(t == INFO_TAG).tolist()
    stack, out = [], []
    for o, s in zip(pos, c.tags()):
        kind, _, name = s.partition(':')
        if kind == '@enter':
            stack.append((name, o, len(out)))
            out.append(None)
        elif kind == '@exit' and stack and stack[-1][0] == name:
            nm, a, slot = stack.pop()
            out[slot] = (len(stack), nm, a, o + 1)
    return [r for r in out if r is not None]


def _uniq(names):
    seen, res = {}, []
    for n in names:
        seen[n] = seen.get(n, 0) + 1
        res.append(n if seen[n] == 1 else f'{n}#{seen[n]}')
    return res


def stages_of(c: Circuit, how='auto'):
    """Named stages covering [0, n): list of dict(name, a, b) plus a source string.

    how: 'auto' (INFO_TAG regions if present, else rotation-anchored segments), 'tags', 'rot', 'equal:N', or a
    JSON file: a list of {name|id, start, end} or {"stages": [...]} (the circuit viewer's stages.json)."""
    n = c.n
    if how.endswith('.json'):
        p = Path(how)
        if not p.exists():
            raise UserError(f'stage file not found: {how}', 'pass --stages auto, or a JSON list of {name,start,end}')
        d = json.loads(p.read_text())
        rows = d['stages'] if isinstance(d, dict) else d
        out = [dict(name=str(r.get('name', r.get('id'))), a=int(r['start']), b=int(r['end'])) for r in rows]
        out.sort(key=lambda r: r['a'])
        if not out or out[0]['a'] < 0 or out[-1]['b'] > n:
            raise UserError(f'stage file {how} does not fit this circuit ({n} ops)',
                            'stage files are per circuit; use --stages auto for others')
        return _fill_gaps(out, n), f'file:{p.name}'
    if how.startswith('equal:'):
        k = max(1, int(how.split(':')[1]))
        e = np.linspace(0, n, k + 1).astype(int)
        return [dict(name=f's{i + 1}', a=int(e[i]), b=int(e[i + 1])) for i in range(k) if e[i + 1] > e[i]], how
    if how in ('auto', 'tags'):
        top = [r for r in tag_tree(c) if r[0] == 0]
        if top:
            names = _uniq([nm[:-len('_lookahead')] if nm.endswith('_lookahead') else nm for _, nm, _, _ in top])
            out = [dict(name=nm, a=a, b=b) for nm, (_, _, a, b) in zip(names, top)]
            return _fill_gaps(out, n), 'tags'
        if how == 'tags':
            raise UserError('this circuit has no @enter/@exit INFO_TAG regions', 'use --stages auto or equal:N')
    if how in ('auto', 'rot'):
        return rotation_stages(c), 'rot'
    raise UserError(f'unknown --stages value {how!r}', 'use auto, tags, rot, equal:N or a stages .json file')


def _fill_gaps(rows, n):
    out, pos = [], 0
    for r in rows:
        if r['a'] > pos:
            out.append(dict(name='gap', a=pos, b=r['a']))
        out.append(r)
        pos = max(pos, r['b'])
    if pos < n:
        out.append(dict(name='gap', a=pos, b=n))
    names = _uniq([r['name'] for r in out])
    for r, nm in zip(out, names):
        r['name'] = nm
    return out


def rotation_stages(c: Circuit):
    """Stages for untagged circuits: clusters of Z_POW rotations (phase kickback steps) and the arithmetic between.

    Anchors are Z_POW ops with a non-Clifford, non-T angle (T and Clifford phases move between designs; the
    rotations that build the target phase do not). Clusters are anchor runs whose gaps are below max(64, n/200)
    ops. Names: pre, rot1, mid1, rot2, ..., post. Without rotations: Z_POW of any angle, else eight equal slices."""
    n = c.n
    zp = np.flatnonzero((c.types & 0x7F) == Z_POW)
    rot = np.array([u % (1 << 125) != 0 for u in c.angle_units()], bool)
    if rot.any():
        zp = zp[rot]
    if len(zp) == 0:
        return stages_of(c, 'equal:8')[0]
    gap = max(64, n // 200)
    cuts = np.flatnonzero(np.diff(zp) > gap)
    clusters = [(int(zp[i]), int(zp[j]) + 1) for i, j in zip(np.r_[0, cuts + 1], np.r_[cuts, len(zp) - 1])]
    out, pos = [], 0
    for k, (a, b) in enumerate(clusters, 1):
        if a > pos:
            out.append(dict(name='pre' if k == 1 else f'mid{k - 1}', a=pos, b=a))
        out.append(dict(name=f'rot{k}', a=a, b=b))
        pos = b
    if pos < n:
        out.append(dict(name='post', a=pos, b=n))
    return out


def align_stages(sa, sb):
    """Pairs of stage indices (i, j) whose names match, in order (difflib on the name sequences)."""
    na, nb = [s['name'] for s in sa], [s['name'] for s in sb]
    sm = difflib.SequenceMatcher(a=na, b=nb, autojunk=False)
    pairs = []
    for blk in sm.get_matching_blocks():
        pairs += [(blk.a + k, blk.b + k) for k in range(blk.size)]
    return pairs


def position_map(sa, na, sb, nb):
    """Piecewise-linear map of op positions from circuit B to circuit A through matched stage boundaries.

    Returns (xb, xa) knots for np.interp(pos_b, xb, xa)."""
    knots = {0: 0, nb: na}
    for i, j in align_stages(sa, sb):
        knots.setdefault(sb[j]['a'], sa[i]['a'])
        knots.setdefault(sb[j]['b'], sa[i]['b'])
    xb = sorted(knots)
    xa = [knots[k] for k in xb]
    ok = [0]
    for i in range(1, len(xb)):  # keep monotone
        if xa[i] >= xa[ok[-1]]:
            ok.append(i)
    return np.array([xb[i] for i in ok], float), np.array([xa[i] for i in ok], float)


def static_stats(c: Circuit, a=0, b=None):
    """Static op census of [a, b): Toffoli-like gates (all / conditional), measurements, rotations by class."""
    b = c.n if b is None else b
    t = c.types[a:b]
    base = t & 0x7F
    q3 = (t & 0x30) == 0x30
    return dict(ops=int(b - a), toffoli=int(q3.sum()), hmr=int((base == HMR).sum()), r=int((base == R).sum()),
                zpow=int((base == Z_POW).sum()), push=int((t == PUSH).sum()))


def cumulative_toffolis(c: Circuit, positions):
    """{position: (Toffoli-like ops before it, of which conditional)} for the given op positions. Conditional means
    inside a PUSH_CONDITION block or an _IF form: such a gate may be skipped in a shot."""
    pos = sorted({int(p) for p in positions} | {0, c.n})
    depth = condition_depth(c.types)
    out, tof, cond = {0: (0, 0)}, 0, 0
    for a, b in zip(pos[:-1], pos[1:]):
        t = np.asarray(c.types[a:b])
        q3 = (t & 0x30) == 0x30
        tof += int(q3.sum())
        cond += int((q3 & ((depth[a:b] > 0) | ((t & HAS_COND) > 0))).sum())
        out[b] = (tof, cond)
    return out


def zpow_classes(c: Circuit):
    """Counts of Z_POW angles by verifier class: clifford (multiple of 1/4 turn), t (1/8 turn), rotation."""
    units = c.angle_units()
    out = {'clifford': 0, 't': 0, 'rotation': 0}
    for u in units:
        if u % (1 << 126) == 0:
            out['clifford'] += 1
        elif u % (1 << 125) == 0:
            out['t'] += 1
        else:
            out['rotation'] += 1
    return out


def detect_task(c: Circuit):
    regs = c.qubit_registers()
    if 'x' in regs and len(regs['x']) == 22:
        return 'invsqrt'
    if all(f'S{i}' in regs for i in range(70)):
        return 'dqi'
    return 'generic'


# ------------------------------------------------------------------------------------------------ caching
def cache_dir(args):
    if getattr(args, 'no_cache', False):
        return None
    d = getattr(args, 'cache', None) or os.environ.get('KICKMIX_RENDER_CACHE') or \
        os.path.join(os.environ.get('XDG_CACHE_HOME', os.path.expanduser('~/.cache')), 'kickmix-render')
    try:
        Path(d).mkdir(parents=True, exist_ok=True)
        return Path(d)
    except OSError:
        return None


def cached_profile(c: Circuit, args, sw=None):
    """Width profile, cached by circuit SHA-256 (the cache only saves time; results are identical without it)."""
    if 'profile' in c._cache:
        return c._cache['profile']
    d = cache_dir(args)
    f = d / f'{c.sha256}.width-v1.npy' if d else None
    if f is not None and f.exists():
        prof = np.load(f)
        if len(prof) == c.n:
            c._cache['profile'] = prof
            return prof
    prof = sw['profile'] if sw is not None else width_profile(c)
    if f is not None:
        try:
            tmp = f.with_suffix('.tmp.npy')
            np.save(tmp, prof)
            os.replace(tmp, f)
        except OSError:
            pass
    c._cache['profile'] = prof
    return prof


def binned_max(prof, lo, hi, nbins):
    lo, hi = int(max(0, lo)), int(min(len(prof), hi))
    if hi <= lo:
        return np.array([lo], float), np.array([int(prof[min(lo, len(prof) - 1)])])
    edges = np.unique(np.linspace(lo, hi, min(nbins, hi - lo) + 1).astype(np.int64))
    mx = np.maximum.reduceat(np.asarray(prof[lo:hi]), edges[:-1] - lo)
    return (edges[:-1] + edges[1:]) / 2.0, mx


# ------------------------------------------------------------------------------------------------ summary envelope
def tool_sha():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(', ', ': '), default=str)


def render_summary(d, max_bytes=MAX_BYTES, max_rows=MAX_ROWS):
    """One JSON object, at most max_rows rows and max_bytes bytes (rows, then `next`, are trimmed to fit)."""
    d = dict(d)
    rows = d.pop('rows', None)
    nxt = d.pop('next', None)

    def build(k, with_next):
        out = dict(d)
        if rows:
            out['rows'] = rows[:k]
            out['rows_shown'], out['rows_total'] = min(k, len(rows)), len(rows)
        if with_next and nxt:
            out['next'] = nxt
        return dumps(out)

    k = min(max_rows, len(rows or []))
    while True:
        text = build(k, True)
        if len(text.encode()) <= max_bytes or k == 0:
            break
        k -= 1
    if len(text.encode()) > max_bytes:
        text = build(0, False)
    return text


def file_entry(path, **extra):
    p = Path(path)
    e = dict(path=str(p), bytes=p.stat().st_size)
    e.update(extra)
    return e


def circuit_entry(c: Circuit):
    return dict(path=c.path, sha12=c.sha12, ops=c.n, qubits=c.num_qubits)


# ------------------------------------------------------------------------------------------------ one-shot tracing
PHASE_MOD = 1 << 128
HALF_TURN = 1 << 127


def trace_cuts(c: Circuit, columns, stages):
    """Op indices at which to snapshot the state: evenly spaced plus stage boundaries, each moved to the next point
    where the condition stack is empty (so the kickmix engine can cut the circuit there). Always includes 0 and n."""
    n = c.n
    depth = condition_depth(c.types)
    free = np.flatnonzero(depth == 0)
    want = set(np.linspace(0, n, max(2, columns) + 1).astype(np.int64).tolist())
    want |= {s['a'] for s in stages} | {s['b'] for s in stages}
    out = set()
    for w in want:
        if w >= n:
            out.add(n)
            continue
        k = int(np.searchsorted(free, w))
        out.add(int(free[k]) if k < len(free) else n)
    out |= {0, n}
    return np.array(sorted(out), np.int64)


def resolve_inputs(c: Circuit, assignments):
    """{'x': 161, 'S*': [..]} -> (qubit_values {id: 0/1}, bit_values {id: 0/1}, shown {name: value})."""
    qregs, bregs = c.qubit_registers(), c.bit_registers()
    qv, bv, shown = {}, {}, {}
    items = []
    for k, v in assignments.items():
        if k.endswith('*'):
            if not isinstance(v, list):
                raise UserError(f'{k} needs a JSON list of values (one per register {k[:-1]}0, {k[:-1]}1, ...)')
            items += [(f'{k[:-1]}{i}', x) for i, x in enumerate(v)]
        else:
            items.append((k, v))
    for name, v in items:
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise UserError(f'register value for {name} is not an integer: {v!r}') from None
        if name in qregs:
            ids, dst = qregs[name], qv
        elif name in bregs:
            ids, dst = bregs[name], bv
        else:
            known = ', '.join(list(qregs)[:6] + list(bregs)[:3])
            raise UserError(f'no register named {name!r} in {Path(c.path).name}', f'registers include: {known}')
        if v < 0 or v >= (1 << len(ids)):
            raise UserError(f'{name}={v} does not fit its {len(ids)} bits')
        for j, i in enumerate(ids):
            dst[i] = (v >> j) & 1
        shown[name] = v
    return qv, bv, shown


class PyTracer:
    """Pure-Python one-shot simulator of a kickmix circuit in the computational basis (semantics of kickmix's
    simulator for basis states, as in tracer.js): an op runs only when every bit on the condition stack and its own
    `if b` are 1; PUSH_CONDITION pushes a bit's current value; HMR q b sets b := m, flips the sign when q = m = 1,
    then q := 0; R q sets q := 0; Z/CZ/CCZ/NEG flip the sign; Z_POW adds its angle when q = 1. The phase is exact,
    in units of 2^-128 turn. HMR outcomes come from `outcomes`: 'zero' (always 0) or an integer seed."""

    MAX_OPS = 5_000_000

    def __init__(self, c: Circuit):
        self.c = c
        self.t = c.types.tolist()
        rq, _, qs = op_major_refs(c, 'q')
        self.rq, self.qs = rq.tolist(), qs.tolist()
        rb, _, bs = op_major_refs(c, 'b')
        self.rb, self.bs = rb.tolist(), bs.tolist()
        self.angles = c.angle_units()

    def run(self, cuts, qv, bv, outcomes='zero'):
        c = self.c
        t, rq, qs, rb, bs, angles = self.t, self.rq, self.qs, self.rb, self.bs, self.angles
        nq = max(c.num_qubits, (max(rq) + 1) if rq else 0)
        nb = max(c.num_bits, (max(rb) + 1) if rb else 0, 1)
        q, b = bytearray(nq), bytearray(nb)
        for i, v in qv.items():
            q[i] = v
        for i, v in bv.items():
            b[i] = v
        rng = None if outcomes == 'zero' else random.Random(int(outcomes))
        stack, zeros, phase, ai, tof, dirty = [], 0, 0, 0, 0, 0
        cut_list = [int(x) for x in cuts]
        snaps, phases, tofs = [], [], []
        ci = 0
        n = len(t)
        for i in range(n + 1):
            while ci < len(cut_list) and cut_list[ci] == i:
                snaps.append(bytes(q))
                phases.append(phase)
                tofs.append(tof)
                ci += 1
            if i == n:
                break
            op = t[i]
            if op == INFO_TAG or (op & 0x7F) in (0x07, 0x17, 0x47):
                continue
            if op == PUSH:
                v = b[rb[bs[i]]]
                stack.append(v)
                zeros += v == 0
                continue
            if op == POP:
                zeros -= stack.pop() == 0
                continue
            base = op & 0x7F
            if base == Z_POW:
                ang = angles[ai]
                ai += 1
            if zeros or (op & HAS_COND and not b[rb[bs[i + 1] - 1]]):
                continue
            k = qs[i]
            if base == CX:
                if q[rq[k]]:
                    q[rq[k + 1]] ^= 1
            elif base == CZ:
                if q[rq[k]] and q[rq[k + 1]]:
                    phase += HALF_TURN
            elif base == X:
                q[rq[k]] ^= 1
            elif base == Z:
                if q[rq[k]]:
                    phase += HALF_TURN
            elif base == CCX:
                tof += 1
                if q[rq[k]] and q[rq[k + 1]]:
                    q[rq[k + 2]] ^= 1
            elif base == CCZ:
                tof += 1
                if q[rq[k]] and q[rq[k + 1]] and q[rq[k + 2]]:
                    phase += HALF_TURN
            elif base == CSWAP:
                tof += 1
                if q[rq[k]]:
                    x1, x2 = rq[k + 1], rq[k + 2]
                    q[x1], q[x2] = q[x2], q[x1]
            elif base == SWAP:
                x1, x2 = rq[k], rq[k + 1]
                q[x1], q[x2] = q[x2], q[x1]
            elif base == HMR:
                m = rng.getrandbits(1) if rng else 0
                b[rb[bs[i]]] = m
                if m and q[rq[k]]:
                    phase += HALF_TURN
                q[rq[k]] = 0
            elif base == R:
                dirty += q[rq[k]]
                q[rq[k]] = 0
            elif base == Z_POW:
                if q[rq[k]]:
                    phase += ang
            elif base == NEG:
                phase += HALF_TURN
            elif base == BIT_STORE0:
                b[rb[bs[i]]] = 0
            elif base == BIT_STORE1:
                b[rb[bs[i]]] = 1
            elif base == BIT_INVERT:
                b[rb[bs[i]]] ^= 1
            else:
                raise UserError(f'op {i}: unsupported op type {op_name(op)} ({op:#x})')
            phase %= PHASE_MOD
        qubits = np.frombuffer(b''.join(snaps), np.uint8).reshape(len(snaps), nq).T.copy()
        return dict(cuts=np.asarray(cut_list, np.int64), qubits=qubits,
                    phase=np.array([p / PHASE_MOD for p in phases]), phase_exact=[str(p) for p in phases],
                    toffolis=np.array(tofs, np.int64), dirty_resets=dirty, final_bits=bytes(b),
                    engine='python', outcomes=str(outcomes))


def run_kickmix(c: Circuit, cuts, qv, bv, seed, tmpdir):
    """One shot on kickmix's own simulator: the circuit is cut at `cuts` into segments run back to back on one
    Simulator, reading every qubit and the phase between segments. HMR/R outcomes follow kickmix's seeded PRNG."""
    import kickmix as km
    from fractions import Fraction
    sl = SeqSlicer(c)
    nq = c.num_qubits
    sim = km.Simulator(batch_size=1, debug_print=False, counting_operations=True, seed=int(seed))
    allq = km.array([km.q(i) for i in range(nq)])
    if qv:
        ids = sorted(qv)
        sim.write_within_shot(km.array([km.q(i) for i in ids]), 0, np.array([bool(qv[i]) for i in ids]))
    if bv:
        ids = sorted(bv)
        sim.write_within_shot(km.array([km.b(i) for i in ids]), 0, np.array([bool(bv[i]) for i in ids]))
    snaps, phases, tofs = [], [], []
    seg = Path(tmpdir) / 'segment.kmb'

    def snap():
        snaps.append(np.asarray(sim.read_within_shot(allq, 0), dtype=np.uint8))
        ph = Fraction(sim.read_shot_phase(0))  # half turns
        u = (ph.numerator * HALF_TURN) // ph.denominator % PHASE_MOD
        phases.append(u)
        st = sim.operation_statistics()
        tofs.append(int(getattr(st, 'num_toffoli_like', 0)))

    snap()
    for a, b in zip(cuts[:-1], cuts[1:]):
        if b > a:
            sl.write(seg, int(a), int(b))
            sim.do(km.Circuit.from_file(str(seg), format='kmb'))
        snap()
    return dict(cuts=np.asarray(cuts, np.int64), qubits=np.stack(snaps, axis=1),
                phase=np.array([p / PHASE_MOD for p in phases]), phase_exact=[str(p) for p in phases],
                toffolis=np.array(tofs, np.int64), dirty_resets=-1, engine='kickmix', outcomes=f'seed {seed}')


def kickmix_importable():
    try:
        import kickmix
        return hasattr(kickmix, 'Simulator')  # not a stray source directory named kickmix
    except Exception:
        return False


def engine_subprocess(c: Circuit, cuts, assignments, seed, python):
    """Run run_kickmix in another interpreter that has kickmix (e.g. a venv without matplotlib)."""
    with tempfile.TemporaryDirectory(prefix='kickmix-render-') as td:
        td = Path(td)
        np.save(td / 'cuts.npy', np.asarray(cuts, np.int64))
        (td / 'inputs.json').write_text(json.dumps(assignments))
        cmd = [python, '-I', str(Path(__file__).resolve()), '_engine', c.path, str(td), '--seed', str(seed)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise UserError(f'kickmix engine failed in {python}: {r.stderr.strip()[-400:]}',
                            'check that interpreter can `import kickmix, numpy`, or use --engine python')
        d = np.load(td / 'trace.npz', allow_pickle=False)
        meta = json.loads((td / 'trace.json').read_text())
        out = {k: d[k] for k in d.files}
        out.update(meta)
        return out


def cmd_engine(argv):
    """Internal: `render.py _engine CIRCUIT DIR --seed S` (numpy + kickmix only; used by engine_subprocess)."""
    ap = argparse.ArgumentParser(prog='render.py _engine')
    ap.add_argument('circuit')
    ap.add_argument('dir')
    ap.add_argument('--seed', type=int, default=0)
    a = ap.parse_args(argv)
    d = Path(a.dir)
    c = read_kmb(a.circuit)
    qv, bv, _ = resolve_inputs(c, json.loads((d / 'inputs.json').read_text()))
    res = run_kickmix(c, np.load(d / 'cuts.npy'), qv, bv, a.seed, d)
    np.savez(d / 'trace.npz', cuts=res['cuts'], qubits=res['qubits'], phase=res['phase'], toffolis=res['toffolis'])
    (d / 'trace.json').write_text(json.dumps({k: res[k] for k in ('phase_exact', 'dirty_resets', 'engine',
                                                                   'outcomes')}))
    return 0


# ------------------------------------------------------------------------------------------------ drawing
# Palette: the dataviz reference palette (light). Blue = circuit A, orange = circuit B, gray = context; red marks
# ops at the circuit's full width and zero words in traces, always with a text label.
SURF, INK, INK2, MUTED, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#8c8b86', '#e8e7e2'
BLUE, ORANGE, AQUA, VIOLET, RED, YELLOW = '#2a78d6', '#eb6834', '#1baf7a', '#6250d6', '#e34948', '#eda100'
BAND, BAND_ON = '#f1f0ec', '#dbe8f8'
DPI = 100
FIT_PAD_IN = 0.15  # margin round a fitted PNG (save_png(fit=True)), inches
_PLT = None


def plt_():
    global _PLT
    if _PLT is None:
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
        except ImportError as e:
            raise UserError('matplotlib is not installed', 'pip install matplotlib pillow') from e
        plt.rcParams.update({
            'figure.facecolor': SURF, 'axes.facecolor': SURF, 'savefig.facecolor': SURF, 'text.color': INK,
            'axes.labelcolor': INK2, 'xtick.color': INK2, 'ytick.color': INK2, 'axes.edgecolor': '#bdbcb6',
            'axes.spines.top': False, 'axes.spines.right': False, 'axes.grid': True, 'grid.color': GRID,
            'grid.linewidth': 0.8, 'font.size': 10.5, 'axes.titlesize': 12, 'axes.titleweight': 'bold',
            'axes.titlelocation': 'left', 'legend.frameon': False, 'font.family': 'DejaVu Sans',
            'svg.hashsalt': 'kickmix-render', 'path.simplify': True})
        _PLT = plt
    return _PLT


def pil_():
    try:
        from PIL import Image
    except ImportError as e:
        raise UserError('Pillow is not installed', 'pip install pillow') from e
    return Image


def new_fig(px, aspect, rows=1, cols=1, **kw):
    plt = plt_()
    w = px / DPI
    return plt.subplots(rows, cols, figsize=(w, w * aspect), dpi=DPI, **kw)


def fig_image(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=DPI)
    plt_().close(fig)
    buf.seek(0)
    return pil_().open(buf).convert('RGB')


def save_png(fig, path, fit=False):
    # fit=True grows (or trims) the canvas to every artist plus a fixed margin, so labels drawn with clip_on=False
    # or titles wider than --px are not cropped; the PNG is then about --px wide rather than exactly --px. Every
    # visible text is passed explicitly: matplotlib's tight box otherwise ignores the width of a long axis label.
    kw = {}
    if fit:
        from matplotlib.text import Text
        texts = [t for t in fig.findobj(Text) if t.get_visible() and t.get_text()]
        kw = dict(bbox_inches='tight', pad_inches=FIT_PAD_IN, bbox_extra_artists=texts)
    fig.savefig(path, dpi=DPI, metadata={'Software': None}, **kw)
    plt_().close(fig)
    return path


def save_gif(frames, durations, path, max_mb=DEFAULT_GIF_MB):
    """Animated GIF with one shared palette (no colour flicker). Shrinks, then drops frames, to fit max_mb."""
    Image = pil_()
    limit = int(max_mb * 1024 * 1024)
    frames, durations = list(frames), list(durations)
    scale = 1.0
    for attempt in range(8):
        fr = frames if scale == 1.0 else [f.resize((max(1, int(f.width * scale)), max(1, int(f.height * scale))),
                                                   Image.LANCZOS) for f in frames]
        step = max(1, len(fr) // 8)
        sample = fr[::step] + [fr[-1]]
        w, h = sample[0].size
        montage = Image.new('RGB', (w, h * len(sample)))
        for i, f in enumerate(sample):
            montage.paste(f, (0, h * i))
        pal = montage.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in fr]
        q[0].save(path, save_all=True, append_images=q[1:], duration=durations, loop=0, optimize=True, disposal=1)
        size = Path(path).stat().st_size
        if size <= limit:
            return dict(frames=len(fr), scale=round(scale, 3), px=[w, h])
        if attempt % 2 == 0 and scale > 0.5:
            scale *= 0.8
        elif len(frames) > 4:  # drop every other inner frame, keep first and last, keep total duration
            keep = [0] + list(range(1, len(frames) - 1, 2)) + [len(frames) - 1]
            nd = []
            for k, i in enumerate(keep):
                j = keep[k + 1] if k + 1 < len(keep) else len(frames)
                nd.append(sum(durations[i:j]))
            frames = [frames[i] for i in keep]
            durations = nd
        else:
            scale *= 0.8
    return dict(frames=len(frames), scale=round(scale, 3), px=list(q[0].size), over_budget=True)


def ops_axis(n):
    if n >= 2_000_000:
        return 1e6, 'millions of operations'
    if n >= 20_000:
        return 1e3, 'thousands of operations'
    return 1.0, 'operations'


def draw_bands(ax, stages, lo, hi, scale, y_text, highlight=None, labels=True):
    span = max(1, hi - lo)
    for i, s in enumerate(stages):
        a, b = max(s['a'], lo), min(s['b'], hi)
        if b <= a:
            continue
        on = highlight is not None and s['name'] == highlight
        ax.axvspan(a / scale, b / scale, color=BAND_ON if on else (BAND if i % 2 == 0 else SURF), zorder=0, lw=0)
        if labels and (b - a) / span > 0.04:
            nm = s['name'] if len(s['name']) <= 18 else s['name'][:17] + '…'
            ax.text((a + b) / 2 / scale, y_text, nm, ha='center', va='bottom', fontsize=8.5, clip_on=True,
                    color=INK if on else INK2, fontweight='bold' if on else 'normal')


def ease(f):
    return 0.5 - 0.5 * math.cos(math.pi * f)


# ------------------------------------------------------------------------------------------------ common verb plumbing
def stem(c: Circuit):
    return Path(c.path).stem


def out_base(args, circuits, verb):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = args.name or '_vs_'.join(stem(c) for c in circuits)
    return out / f'{name}_{verb}'


def labels_for(args, circuits):
    if getattr(args, 'labels', None):
        labs = [s.strip() for s in args.labels.split(',')]
        if len(labs) != len(circuits):
            raise UserError(f'--labels has {len(labs)} names for {len(circuits)} circuits',
                            'give one comma-separated label per circuit, e.g. --labels "63 q,62 q"')
        return labs
    return [stem(c)[:12] for c in circuits]


def load_circuits(paths, maxn=2):
    if not paths:
        raise UserError('no circuit given', 'pass one or two .kmb paths')
    if len(paths) > maxn:
        raise UserError(f'this verb takes at most {maxn} circuits, got {len(paths)}')
    return [read_kmb(p) for p in paths]


def write_table(base, payload):
    p = Path(str(base) + '.json')
    p.write_text(json.dumps(payload, indent=1, default=str) + '\n')
    return p


def rerun_cmd(argv):
    return 'python render.py ' + ' '.join(shlex.quote(a) for a in argv)


def stage_peaks(prof, stages):
    top = int(prof.max())
    out = []
    for s in stages:
        seg = prof[s['a']:s['b']]
        out.append(dict(peak=int(seg.max()) if len(seg) else 0, at_top=int((seg == top).sum()) if len(seg) else 0))
    return out


# ------------------------------------------------------------------------------------------------ verb: width
class WidthView:
    """Profiles of one or two circuits in A's op coordinates (B mapped through matched stage boundaries)."""

    def __init__(self, circuits, profiles, stages, labels):
        self.c, self.p, self.s, self.lab = circuits, profiles, stages, labels
        self.na = circuits[0].n
        if len(circuits) == 2:
            self.xb, self.xa = position_map(stages[0], circuits[0].n, stages[1], circuits[1].n)
        lo = min(int(p.min()) for p in profiles)
        hi = max(int(p.max()) for p in profiles)
        pad = max(3, int((hi - lo) * 0.16))
        self.ylim = (max(0, lo - pad), hi + max(2, (hi - lo) // 5))
        self.scale, self.unit = ops_axis(self.na)

    def series(self, k, lo, hi, nbins):
        if k == 0:
            return binned_max(self.p[0], lo, hi, nbins)
        blo, bhi = np.interp([lo, hi], self.xa, self.xb)
        cx, mx = binned_max(self.p[1], blo, bhi, nbins)
        return np.interp(cx, self.xb, self.xa), mx

    def draw(self, ax, lo, hi, nbins, highlight=None, peaks_dots=True):
        y0, y1 = self.ylim
        self.scale, self.unit = ops_axis(max(hi, 1) if hi - lo > hi / 50 else hi - lo)
        draw_bands(ax, self.s[0], lo, hi, self.scale, y1 - (y1 - y0) * 0.08, highlight)
        colours = [BLUE, ORANGE]
        for k in range(len(self.c)):
            cx, mx = self.series(k, lo, hi, nbins)
            top = int(self.p[k].max())
            ax.step(cx / self.scale, mx, where='mid', color=colours[k], lw=1.5,
                    label=f'{self.lab[k]}: width {top} (Q = {self.c[k].num_qubits})')
            if peaks_dots:  # rug of bins that reach this circuit's full width
                hit = mx >= top
                if hit.any():
                    xs = cx[hit] / self.scale
                    ax.vlines(xs, 0.015 + 0.035 * k, 0.045 + 0.035 * k, colors=colours[k], lw=1.2,
                              transform=ax.get_xaxis_transform())
        ax.set_xlim(lo / self.scale, hi / self.scale)
        ax.ticklabel_format(axis='x', useOffset=False, style='plain')
        ax.set_ylim(y0, y1)
        ax.set_ylabel('live qubits')
        ax.set_xlabel(f'position in {Path(self.c[0].path).stem[:12]} ({self.unit})'
                      + ('; B aligned to A' if len(self.c) == 2 else ''))
        ax.legend(loc='lower left', bbox_to_anchor=(0, 1.0), fontsize=8.5, ncol=2, borderaxespad=0.2,
                  handlelength=1.6)
        ax.text(0.995, 0.075 + 0.035 * (len(self.c) - 1), 'ticks: ops at full width', transform=ax.transAxes,
                ha='right', va='bottom', fontsize=7.5, color=INK2)


def cmd_width(args):
    cs = load_circuits(args.circuits)
    labs = labels_for(args, cs)
    profs = [cached_profile(c, args) for c in cs]
    stg = [stages_of(c, args.stages) for c in cs]
    stages, src = [s for s, _ in stg], stg[0][1]
    view = WidthView(cs, profs, stages, labs)
    base = out_base(args, cs, 'width')
    peaks = [stage_peaks(p, s) for p, s in zip(profs, stages)]
    pairs = dict(align_stages(stages[0], stages[1])) if len(cs) == 2 else {}
    rows = []
    for i, s in enumerate(stages[0]):
        r = dict(stage=s['name'], ops=s['b'] - s['a'], peak=peaks[0][i]['peak'], at_full_width=peaks[0][i]['at_top'])
        if len(cs) == 2:
            j = pairs.get(i)
            r['peak_b'] = peaks[1][j]['peak'] if j is not None else None
            r['at_full_width_b'] = peaks[1][j]['at_top'] if j is not None else None
        rows.append(r)
    nb = max(200, args.px)
    files = []
    title_all = (f'Live qubits: {labs[0]}' + (f' vs {labs[1]}' if len(cs) == 2 else '') +
                 '  (' + ' → '.join(str(int(p.max())) for p in profs) + ' wide)')
    zoom = parse_zoom(args.zoom, stages[0], cs[0].n) if args.zoom else None
    if not args.no_png:
        fig, ax = new_fig(args.px, 0.45)
        lo, hi = zoom if zoom else (0, cs[0].n)
        view.draw(ax, lo, hi, nb)
        ax.set_title(title_all if not zoom else f'{title_all}, zoom {lo:,}..{hi:,}', pad=26)
        fig.tight_layout()
        files.append(file_entry(save_png(fig, str(base) + '.png'), kind='png'))
    if not args.no_gif:
        frames, durs = width_frames(view, stages[0], rows, args, title_all, zoom)
        gif = str(base) + '.gif'
        info = save_gif(frames, durs, gif, args.max_mb)
        files.append(file_entry(gif, kind='gif', **info))
    facts = dict(certified_width=[int(p.max()) for p in profs], header_qubits=[c.num_qubits for c in cs],
                 ops=[c.n for c in cs], stages=src, n_stages=len(stages[0]))
    payload = dict(verb='width', circuits=[circuit_entry(c) for c in cs], facts=facts, rows=rows)
    table = write_table(base, payload)
    return dict(ok=True, verb='width', tier='static',
                basis='lifetime intervals from the op stream (R/HMR at condition depth 0 end a value; '
                      'registers live throughout); no simulation',
                files=files + [file_entry(table, kind='json')], circuits=[circuit_entry(c) for c in cs],
                facts=facts, rows=rows,
                next=[f'python render.py width {cs[0].path} --zoom '
                      f'{max(rows, key=lambda r: r["at_full_width"])["stage"]}'])


def parse_zoom(z, stages, n):
    for s in stages:
        if s['name'] == z:
            return s['a'], s['b']
    if ':' in z:
        a, b = z.split(':', 1)
        try:
            a, b = int(a or 0), int(b or n)
        except ValueError:
            pass
        else:
            if 0 <= a < b <= n:
                return a, b
    raise UserError(f'--zoom {z!r} is neither a stage name nor a range a:b within 0..{n}',
                    'stages: ' + ', '.join(s['name'] for s in stages[:12]))


def width_frames(view, stages, rows, args, title_all, zoom):
    n = view.na
    nb = max(200, args.px)
    tw = max(0, args.tween)
    if zoom:
        targets = [(f'zoom {zoom[0]:,}..{zoom[1]:,}', zoom[0], zoom[1], None)]
    else:
        big = [i for i, s in enumerate(stages) if s['b'] - s['a'] >= n * args.min_stage]
        budget = max(1, (args.max_frames - 2) // (tw + 1))
        if len(big) > budget:
            big = sorted(sorted(big, key=lambda i: -(stages[i]['b'] - stages[i]['a']))[:budget])
        targets = []
        for i in big:
            r = rows[i]
            pk = f'peak {r["peak"]}' + (f' → {r["peak_b"]}' if r.get('peak_b') is not None else '')
            targets.append((f'Stage {stages[i]["name"]}: {pk} qubits, {r["ops"]:,} ops', stages[i]['a'],
                            stages[i]['b'], stages[i]['name']))
    frames, durs = [], []

    def frame(lo, hi, title, hl, ms):
        fig, ax = new_fig(args.px, 0.45)
        view.draw(ax, lo, hi, nb, hl)
        ax.set_title(title, pad=26)
        fig.tight_layout()
        frames.append(fig_image(fig))
        durs.append(ms)

    frame(0, n, title_all, None, args.hold * 2)
    cur = (0, n)
    for title, a, b, hl in targets + [(title_all, 0, n, None)]:
        pad = (b - a) * 0.04
        tgt = (max(0, a - pad), min(n, b + pad)) if (a, b) != (0, n) else (0, n)
        for k in range(1, tw + 1):
            f = ease(k / (tw + 1))
            lo = cur[0] + (tgt[0] - cur[0]) * f
            hi = cur[1] + (tgt[1] - cur[1]) * f
            frame(lo, hi, title, hl, 90)
        frame(tgt[0], tgt[1], title, hl, args.hold)
        cur = tgt
    durs[-1] = args.hold * 3
    return frames, durs


# ------------------------------------------------------------------------------------------------ verb: diff
OP_CLASSES = [  # (name, colour, test on op type) in drawing priority order
    ('Toffoli-like', VIOLET, lambda t: (t & 0x30) == 0x30),
    ('measure/reset', AQUA, lambda t: ((t & 0x7F) == HMR) | ((t & 0x7F) == R)),
    ('rotation', YELLOW, lambda t: (t & 0x7F) == Z_POW),
    ('classical/control', '#5e5d59', lambda t: ((t & 0x30) == 0) & (t != INFO_TAG)),
    ('Clifford', '#c9c8c2', lambda t: ((t & 0x30) != 0)),
]


def op_class_codes(t):
    """0..4 per op (index into OP_CLASSES), 5 for INFO_TAG."""
    code = np.full(len(t), 5, np.int8)
    for k in range(len(OP_CLASSES) - 1, -1, -1):
        code[OP_CLASSES[k][2](t)] = k
    return code


def kgram_tokens(tok, k=6):
    """Hash of each run of k consecutive op tokens (makes sequence matching selective)."""
    if len(tok) < k:
        return tok.tolist()
    h = np.zeros(len(tok) - k + 1, np.uint64)
    with np.errstate(over='ignore'):
        for j in range(k):
            h = h * np.uint64(1000003) + tok[j:len(tok) - k + 1 + j].astype(np.uint64)
    return h.tolist()


def sparse_edits(ta, tb, k=6, window=4096, max_window=65536, minmatch=32):
    """Edits between two long, mostly identical op-token arrays in near-linear time: skip equal stretches with
    vectorised compares, and at each mismatch resynchronise with difflib on a growing window of k-gram hashes.
    Returns (a_lo, a_hi, b_lo, b_hi) relative ranges and matched (a, b, size) blocks."""
    na, nb = len(ta), len(tb)
    i = j = 0
    edits, blocks = [], []
    while i < na and j < nb:
        i_eq, j_eq = i, j
        while i < na and j < nb:
            L = min(65536, na - i, nb - j)
            neq = np.flatnonzero(ta[i:i + L] != tb[j:j + L])
            if len(neq):
                i, j = i + int(neq[0]), j + int(neq[0])
                break
            i, j = i + L, j + L
        if i > i_eq:
            blocks.append((i_eq, j_eq, i - i_eq))
        if i >= na or j >= nb:
            break
        w = window
        while True:
            ga = kgram_tokens(ta[i:i + w], k)
            gb = kgram_tokens(tb[j:j + w], k)
            sm = difflib.SequenceMatcher(a=ga, b=gb, autojunk=False)
            m = [blk for blk in sm.get_matching_blocks() if blk.size >= minmatch]
            if m:
                edits.append((i, i + m[0].a, j, j + m[0].b))
                i, j = i + m[0].a, j + m[0].b
                break
            if (i + w >= na and j + w >= nb) or w >= max_window:
                edits.append((i, na, j, nb))
                i, j = na, nb
                break
            w *= 2
    if i < na or j < nb:
        edits.append((i, na, j, nb))
    return edits, blocks


def op_edits(sa: Structure, a0, a1, sb: Structure, b0, b1, k=6, merge=16, knots=None, sparse_from=60_000):
    """Op-level edits between A[a0:a1] and B[b0:b1] as (kind, a_lo, a_hi, b_lo, b_hi) with absolute op indices.
    If `knots` is a dict, matched positions {b_op: a_op} (block starts and ends) are added to it."""
    if max(a1 - a0, b1 - b0) > sparse_from:
        eds, blocks = sparse_edits(sa.op_tokens(a0, a1), sb.op_tokens(b0, b1), k)
        if knots is not None:
            for x, y, size in blocks:
                knots[b0 + y], knots[b0 + y + size] = a0 + x, a0 + x + size
        res = []
        for x0, x1, y0, y1 in eds:
            if x1 == x0 and y1 == y0:
                continue
            kind = 'replace' if x1 > x0 and y1 > y0 else ('insert' if y1 > y0 else 'delete')
            res.append((kind, a0 + x0, a0 + x1, b0 + y0, b0 + y1))
        return res
    ta = kgram_tokens(sa.op_tokens(a0, a1), k)
    tb = kgram_tokens(sb.op_tokens(b0, b1), k)
    sm = difflib.SequenceMatcher(a=ta, b=tb, autojunk=False)
    if knots is not None:
        for blk in sm.get_matching_blocks():
            if blk.size:
                knots[b0 + blk.b] = a0 + blk.a
                knots[b0 + blk.b + blk.size + k - 1] = a0 + blk.a + blk.size + k - 1
    out = []
    for tag, i0, i1, j0, j1 in sm.get_opcodes():
        if tag == 'equal':
            continue
        # a k-gram edit at i covers ops i .. i+k-1; trim the shared context back to ops
        lo_a, hi_a = i0, min(a1 - a0, i1 + k - 1) if i1 > i0 else i0
        lo_b, hi_b = j0, min(b1 - b0, j1 + k - 1) if j1 > j0 else j0
        if i1 > i0 and j1 > j0:
            hi_a, hi_b = i1, j1
        out.append([lo_a + a0, hi_a + a0, lo_b + b0, hi_b + b0])
    merged = []
    for e in out:
        if merged and e[0] - merged[-1][1] <= merge and e[2] - merged[-1][3] <= merge:
            merged[-1][1], merged[-1][3] = max(merged[-1][1], e[1]), max(merged[-1][3], e[3])
        else:
            merged.append(e)
    res = []
    for a_lo, a_hi, b_lo, b_hi in merged:
        kind = 'replace' if a_hi > a_lo and b_hi > b_lo else ('insert' if b_hi > b_lo else 'delete')
        res.append((kind, a_lo, a_hi, b_lo, b_hi))
    return res


def module_items(tree, lo, hi):
    """Child modules of the region [lo, hi) and the gaps between them: [(name, a, b)], or None without children."""
    inside = [r for r in tree if r[2] >= lo and r[3] <= hi and (r[2], r[3]) != (lo, hi)]
    if not inside:
        return None
    d = min(r[0] for r in inside)
    out, pos = [], lo
    for r in (r for r in inside if r[0] == d):
        if r[2] > pos:
            out.append(('ops between modules', pos, r[2]))
        out.append((r[1], r[2], r[3]))
        pos = r[3]
    if pos < hi:
        out.append(('ops between modules', pos, hi))
    return out


def module_edits(sa: Structure, ta, a0, a1, sb: Structure, tb, b0, b1):
    """Edits between matching tagged regions, by child module (name + relabel-invariant hash), counting the
    untagged gaps between modules as items too. Returns (kind, a_lo, a_hi, b_lo, b_hi, names) or None."""
    ia, ib = module_items(ta, a0, a1), module_items(tb, b0, b1)
    if ia is None or ib is None:
        return None
    toka = [(nm, sa.canon(x, y)) for nm, x, y in ia]
    tokb = [(nm, sb.canon(x, y)) for nm, x, y in ib]
    sm = difflib.SequenceMatcher(a=toka, b=tokb, autojunk=False)
    res = []
    for tag, i0, i1, j0, j1 in sm.get_opcodes():
        if tag == 'equal':
            continue
        alo = ia[i0][1] if i0 < len(ia) else a1
        ahi = ia[i1 - 1][2] if i1 > i0 else alo
        blo = ib[j0][1] if j0 < len(ib) else b1
        bhi = ib[j1 - 1][2] if j1 > j0 else blo
        res.append((tag, alo, ahi, blo, bhi, ia[i0:i1], ib[j0:j1]))
    return res


def tagged_edits(sa, ta, sb, tb, a0, a1, b0, b1, refine, stage, out, depth=0, op_limit=30_000_000):
    """Recursive module diff: descend into changed modules that pair up by name; refine small ones op by op."""
    me = module_edits(sa, ta, a0, a1, sb, tb, b0, b1) if depth < 12 else None
    if me is None:  # no child modules: align op by op (untagged regions are usually near-identical)
        if 0 < max(a1 - a0, b1 - b0) <= op_limit and a1 > a0 and b1 > b0:
            for k2, x0, x1, y0, y1 in op_edits(sa, a0, a1, sb, b0, b1):
                out.append(dict(stage=stage, level='op', kind=k2, a=[x0, x1], b=[y0, y1]))
        else:
            out.append(dict(stage=stage, level='module', kind='replace', a=[a0, a1], b=[b0, b1]))
        return
    for kind, alo, ahi, blo, bhi, xa, xb in me:
        names = sorted({x[0] for x in xa} | {x[0] for x in xb})[:3]
        if kind == 'replace' and [x[0] for x in xa] == [x[0] for x in xb]:
            for (nm, p0, p1), (_, q0, q1) in zip(xa, xb):
                sub = []
                if sa.canon(p0, p1) != sb.canon(q0, q1):
                    tagged_edits(sa, ta, sb, tb, p0, p1, q0, q1, refine, stage, sub, depth + 1, op_limit)
                for e in sub:
                    e.setdefault('names', [nm])
                out.extend(sub)
        elif ahi > alo and bhi > blo and (max(ahi - alo, bhi - blo) <= refine or
                                          (names == ['ops between modules'] and max(ahi - alo, bhi - blo) <= op_limit)):
            for k2, x0, x1, y0, y1 in op_edits(sa, alo, ahi, sb, blo, bhi):
                out.append(dict(stage=stage, level='op', kind=k2, a=[x0, x1], b=[y0, y1], names=names))
        else:
            out.append(dict(stage=stage, level='module', kind=kind, a=[alo, ahi], b=[blo, bhi], names=names))


def diff_by_stage(ca, cb, sta, stb, strs, trees, profs, args, stage_rows, edits):
    pairs = align_stages(sta, stb)
    matched_a, matched_b = {i for i, _ in pairs}, {j for _, j in pairs}
    for i, j in pairs:
        A, B = sta[i], stb[j]
        same = strs[0].canon(A['a'], A['b']) == strs[1].canon(B['a'], B['b'])
        ra, rb = range_stats(ca, A['a'], A['b']), range_stats(cb, B['a'], B['b'])
        row = dict(stage=A['name'], status='same' if same else 'changed', ops=[ra['ops'], rb['ops']],
                   toffoli=[ra['toffoli'], rb['toffoli']], hmr=[ra['hmr'], rb['hmr']],
                   peak=[int(profs[0][A['a']:A['b']].max()), int(profs[1][B['a']:B['b']].max())])
        if not same:
            if module_items(trees[0], A['a'], A['b']) is not None:
                row['level'] = 'module'
                tagged_edits(strs[0], trees[0], strs[1], trees[1], A['a'], A['b'], B['a'], B['b'], args.refine,
                             A['name'], edits, op_limit=args.op_limit)
            elif max(ra['ops'], rb['ops']) <= args.op_limit:
                row['level'] = 'op'
                for kind, alo, ahi, blo, bhi in op_edits(strs[0], A['a'], A['b'], strs[1], B['a'], B['b']):
                    edits.append(dict(stage=A['name'], level='op', kind=kind, a=[alo, ahi], b=[blo, bhi]))
            else:
                row['level'] = 'stage'
                edits.append(dict(stage=A['name'], level='stage', kind='replace', a=[A['a'], A['b']],
                                  b=[B['a'], B['b']]))
        stage_rows.append(row)
    for i, A in enumerate(sta):
        if i not in matched_a:
            stage_rows.append(dict(stage=A['name'], status='only in A', ops=[A['b'] - A['a'], 0]))
            edits.append(dict(stage=A['name'], level='stage', kind='delete', a=[A['a'], A['b']], b=[0, 0]))
    for j, B in enumerate(stb):
        if j not in matched_b:
            stage_rows.append(dict(stage=B['name'], status='only in B', ops=[0, B['b'] - B['a']]))
            edits.append(dict(stage=B['name'], level='stage', kind='insert', a=[0, 0], b=[B['a'], B['b']]))


def range_stats(c: Circuit, a, b):
    s = static_stats(c, a, b)
    return dict(ops=s['ops'], toffoli=s['toffoli'], hmr=s['hmr'])


def cmd_diff(args):
    cs = load_circuits(args.circuits)
    if len(cs) != 2:
        raise UserError('diff needs two circuits', 'python render.py diff A.kmb B.kmb')
    ca, cb = cs
    labs = labels_for(args, cs)
    (sta, src), (stb, _) = stages_of(ca, args.stages), stages_of(cb, args.stages)
    sws = [sweep(c, back=True) for c in cs]
    profs = [cached_profile(c, args, sw) for c, sw in zip(cs, sws)]
    strs = [Structure(c, sw) for c, sw in zip(cs, sws)]
    del sws
    trees = [tag_tree(c) for c in cs]
    tagged = all(any(r[0] == 0 for r in t) for t in trees)
    stage_rows, edits = [], []
    view = WidthView(cs, profs, [sta, stb], labs)
    if not tagged and max(ca.n, cb.n) <= args.op_limit:
        # untagged circuits: one op-level alignment of the whole circuits; edits are then assigned to A's stages
        knots = {0: 0, cb.n: ca.n}
        for kind, alo, ahi, blo, bhi in op_edits(strs[0], 0, ca.n, strs[1], 0, cb.n, knots=knots):
            st = next((s['name'] for s in sta if s['a'] <= alo < s['b']), sta[-1]['name'])
            edits.append(dict(stage=st, level='op', kind=kind, a=[alo, ahi], b=[blo, bhi]))
        xb = sorted(knots)
        xa = np.maximum.accumulate(np.array([knots[x] for x in xb], float))
        view.xb, view.xa = np.array(xb, float), xa
        mode = 'whole-circuit op alignment'
        for s in sta:
            mine = [e for e in edits if e['a'][0] < s['b'] and max(e['a'][1], e['a'][0] + 1) > s['a']]
            b_lo, b_hi = np.interp([s['a'], s['b']], xa, view.xb).astype(int)
            ra, rb = range_stats(ca, s['a'], s['b']), range_stats(cb, int(b_lo), int(b_hi))
            stage_rows.append(dict(stage=s['name'], status='changed' if mine else 'same', level='op',
                                   ops=[ra['ops'], rb['ops']], toffoli=[ra['toffoli'], rb['toffoli']],
                                   edits=len(mine)))
    else:
        mode = 'stage alignment by name' + (' + module hashes' if tagged else '')
        diff_by_stage(ca, cb, sta, stb, strs, trees, profs, args, stage_rows, edits)
        knots = {0: 0, cb.n: ca.n}  # align B to A through stage boundaries and every edit's endpoints
        for i, j in align_stages(sta, stb):
            knots.setdefault(stb[j]['a'], sta[i]['a'])
            knots.setdefault(stb[j]['b'], sta[i]['b'])
        for e in edits:
            if e['a'][1] > e['a'][0] or e['b'][1] > e['b'][0]:
                knots.setdefault(e['b'][0], e['a'][0])
                knots.setdefault(e['b'][1], e['a'][1])
        xb = sorted(knots)
        view.xb = np.array(xb, float)
        view.xa = np.maximum.accumulate(np.array([knots[x] for x in xb], float))
    for e in edits:
        sa_, sb_ = range_stats(ca, *e['a']), range_stats(cb, *e['b'])
        e['ops'] = [sa_['ops'], sb_['ops']]
        e['d_toffoli'] = sb_['toffoli'] - sa_['toffoli']
        e['d_hmr'] = sb_['hmr'] - sa_['hmr']
    edits.sort(key=lambda e: e['a'][0])
    base = out_base(args, cs, 'diff')
    files = []
    if not args.no_png:
        files.append(file_entry(draw_diff_png(view, ca, cb, sta, stage_rows, edits, labs, args, base), kind='png'))
    if not args.no_gif:
        frames, durs = diff_frames(view, ca, cb, sta, edits, labs, args)
        info = save_gif(frames, durs, str(base) + '.gif', args.max_mb)
        files.append(file_entry(str(base) + '.gif', kind='gif', **info))
    n_changed = sum(1 for r in stage_rows if r['status'] != 'same')
    facts = dict(stages=src, alignment=mode, stages_same=sum(1 for r in stage_rows if r['status'] == 'same'),
                 stages_changed=n_changed, edits=len(edits),
                 ops=[ca.n, cb.n], toffoli_static=[static_stats(ca)['toffoli'], static_stats(cb)['toffoli']],
                 certified_width=[int(p.max()) for p in profs], header_qubits=[ca.num_qubits, cb.num_qubits])
    payload = dict(verb='diff', circuits=[circuit_entry(c) for c in cs], facts=facts, stages=stage_rows,
                   edits=edits)
    table = write_table(base, payload)
    rows = [dict(stage=e['stage'], kind=e['kind'], level=e['level'], a=e['a'], b=e['b'], ops=e['ops'],
                 d_toffoli=e['d_toffoli']) for e in sorted(edits, key=lambda e: -max(e['ops']))]
    return dict(ok=True, verb='diff', tier='static',
                basis='relabel-invariant structural hashes of stages/modules and op-level sequence alignment; '
                      'no simulation (equal hashes mean identical ops up to qubit/bit renaming)',
                files=files + [file_entry(table, kind='json')], circuits=[circuit_entry(c) for c in cs],
                facts=facts, rows=rows,
                next=[f'python render.py width {ca.path} {cb.path}', f'python render.py cost {ca.path} {cb.path}'])


def barcode(ax, c: Circuit, lo, hi, y, h, x_of, px):
    """Op-class strip for ops [lo, hi) of c, drawn at height y..y+h with x positions x_of(op index)."""
    lo, hi = int(lo), int(hi)
    if hi <= lo:
        return
    code = op_class_codes(np.asarray(c.types[lo:hi]))
    nb = min(hi - lo, px)
    edges = np.linspace(0, hi - lo, nb + 1).astype(np.int64)
    edges = np.unique(edges)
    pri = np.where(code == 5, 9, code)  # lowest class index wins inside a bin
    binc = np.minimum.reduceat(pri, edges[:-1])
    for k, (name, colour, _) in enumerate(OP_CLASSES):
        sel = np.flatnonzero(binc == k)
        if len(sel) == 0:
            continue
        xs0 = x_of(lo + edges[sel])
        xs1 = x_of(lo + edges[sel + 1])
        ax.broken_barh(list(zip(xs0, np.maximum(xs1 - xs0, 1e-9))), (y, h), facecolors=colour, lw=0)


def draw_diff_png(view, ca, cb, sta, stage_rows, edits, labs, args, base):
    fig, (ax0, ax1) = new_fig(args.px, 0.62, 2, 1, gridspec_kw=dict(height_ratios=[2.2, 1.3]))
    n = ca.n
    view.draw(ax0, 0, n, max(200, args.px))
    nch = sum(1 for r in stage_rows if r['status'] != 'same')
    ax0.set_title(f'{labs[0]} → {labs[1]}: {nch} of {len(stage_rows)} stages changed, {len(edits)} edits '
                  f'(ops {ca.n:,} → {cb.n:,})', pad=26)
    draw_change_tracks(ax1, view, ca, cb, sta, edits, labs, 0, n)
    fig.tight_layout()
    return save_png(fig, str(base) + '.png')


def draw_change_tracks(ax, view, ca, cb, sta, edits, labs, lo, hi):
    sc, unit = ops_axis(view.na)
    for i, s in enumerate(sta):
        a, b = max(lo, s['a']), min(hi, s['b'])
        if b > a:
            ax.axvspan(a / sc, b / sc, color=BAND if i % 2 == 0 else SURF, zorder=0, lw=0)
    ax.broken_barh([(lo / sc, (hi - lo) / sc)], (1.15, 0.6), facecolors='#e3e2dd', lw=0)
    ax.broken_barh([(lo / sc, (hi - lo) / sc)], (0.15, 0.6), facecolors='#e3e2dd', lw=0)
    for e in edits:
        if e['a'][1] > e['a'][0]:
            w = max((e['a'][1] - e['a'][0]) / sc, (hi - lo) / sc / 400)
            ax.broken_barh([(e['a'][0] / sc, w)], (1.15, 0.6), facecolors=BLUE, lw=0)
        if e['b'][1] > e['b'][0]:
            x0, x1 = np.interp([e['b'][0], e['b'][1]], view.xb, view.xa)
            w = max((x1 - x0) / sc, (hi - lo) / sc / 400)
            ax.broken_barh([(x0 / sc, w)], (0.15, 0.6), facecolors=ORANGE, lw=0)
    ax.set_yticks([0.45, 1.45], [f'B {labs[1]}\n(ops only in B)', f'A {labs[0]}\n(ops only in A)'])
    ax.set_ylim(0, 2.0)
    ax.set_xlim(lo / sc, hi / sc)
    ax.grid(axis='y', visible=False)
    ax.set_xlabel(f'position in A ({unit}); B aligned to A')


def diff_frames(view, ca, cb, sta, edits, labs, args):
    n = ca.n
    frames, durs = [], []
    plt = plt_()

    def overview(title, ms):
        fig, (ax0, ax1) = new_fig(args.px, 0.62, 2, 1, gridspec_kw=dict(height_ratios=[2.2, 1.3]))
        view.draw(ax0, 0, n, max(200, args.px))
        ax0.set_title(title, pad=26)
        draw_change_tracks(ax1, view, ca, cb, sta, edits, labs, 0, n)
        fig.tight_layout()
        frames.append(fig_image(fig))
        durs.append(ms)

    overview(f'{labs[0]} → {labs[1]}: {len(edits)} edits; blue = only in A, orange = only in B', args.hold * 2)
    show = sorted(edits, key=lambda e: -max(e['ops']))[:max(1, args.max_frames - 2)]
    show.sort(key=lambda e: e['a'][0])
    for e in show:
        la, lb = e['a'][1] - e['a'][0], e['b'][1] - e['b'][0]
        pad = max(24, int(0.6 * max(la, lb)))
        a_lo, a_hi = max(0, e['a'][0] - pad), min(ca.n, e['a'][1] + pad)
        b_lo, b_hi = max(0, e['b'][0] - pad), min(cb.n, e['b'][1] + pad)
        fig, axes = new_fig(args.px, 0.62, 3, 1, gridspec_kw=dict(height_ratios=[1.6, 1, 1]))
        lo_w, hi_w = (a_lo, a_hi) if la else tuple(np.interp([b_lo, b_hi], view.xb, view.xa))
        span = max(hi_w - lo_w, 400)
        mid = (lo_w + hi_w) / 2
        view.draw(axes[0], max(0, mid - span * 4), min(n, mid + span * 4), max(200, args.px), e['stage'])
        axes[0].axvspan(e['a'][0] / view.scale, max(e['a'][1], e['a'][0] + 1) / view.scale, color=BLUE, alpha=0.18,
                        lw=0)
        axes[0].get_legend().remove()
        for ax, c, lo, hi, (s0, s1), colour, lab in ((axes[1], ca, a_lo, a_hi, e['a'], BLUE, labs[0]),
                                                     (axes[2], cb, b_lo, b_hi, e['b'], ORANGE, labs[1])):
            w = max(hi - lo, 1)
            barcode(ax, c, lo, hi, 0.2, 0.6, lambda o, lo=lo: (np.asarray(o) - lo) / w, args.px)
            if s1 > s0:
                ax.add_patch(plt.Rectangle(((s0 - lo) / w, 0.05), (s1 - s0) / w, 0.9, fill=False, ec=colour, lw=2.2))
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.set_yticks([])
            ax.grid(False)
            ax.set_xticks([0, 1], [f'op {lo:,}', f'op {hi:,}'])
            ax.set_ylabel(lab, rotation=0, ha='right', va='center', fontsize=9)
            for sp in ('left', 'bottom'):
                ax.spines[sp].set_visible(False)
        handles = [plt.Rectangle((0, 0), 1, 1, fc=col) for _, col, _ in OP_CLASSES]
        axes[2].legend(handles, [nm for nm, _, _ in OP_CLASSES], loc='upper center', bbox_to_anchor=(0.5, -0.35),
                       ncol=5, fontsize=8.5)
        what = {'replace': f'{la:,} ops in A replaced by {lb:,} in B', 'insert': f'{lb:,} ops inserted in B',
                'delete': f'{la:,} ops removed from A'}[e['kind']]
        mods = f" ({', '.join(e['names'])})" if e.get('names') else ''
        axes[0].set_title(f"{e['stage']}: {what}{mods}; Toffolis {e['d_toffoli']:+d}, HMR {e['d_hmr']:+d}",
                          pad=8, fontsize=11)
        fig.tight_layout()
        frames.append(fig_image(fig))
        durs.append(args.hold + 600)
    overview(f'{labs[0]} → {labs[1]}: Toffolis {sum(e["d_toffoli"] for e in edits):+d} over {len(edits)} edits, '
             f'width {int(view.p[0].max())} → {int(view.p[1].max())}', args.hold * 3)
    return frames, durs


# ------------------------------------------------------------------------------------------------ verb: trace
def parse_assignments(c: Circuit, args):
    d = {}
    if args.set_json:
        p = Path(args.set_json)
        if not p.exists():
            raise UserError(f'--set-json file not found: {p}')
        try:
            d.update(json.loads(p.read_text()))
        except json.JSONDecodeError as e:
            raise UserError(f'--set-json {p} is not valid JSON: {e}') from None
    for item in args.set or []:
        if '=' not in item:
            raise UserError(f'--set {item!r} must look like NAME=VALUE', 'e.g. --set x=161')
        k, v = item.split('=', 1)
        d[k.strip()] = int(v, 0)
    return d


def expand_names(c: Circuit, spec):
    regs = list(c.qubit_registers())
    out = []
    for s in [x.strip() for x in spec.split(',') if x.strip()]:
        if s.endswith('*'):
            out += [r for r in regs if r.startswith(s[:-1]) and r[len(s) - 1:].isdigit()]
        elif s in regs:
            out.append(s)
        else:
            raise UserError(f'--show/--watch: no qubit register {s!r}', 'registers: ' + ', '.join(regs[:8]))
    return out


def register_values(c: Circuit, qubits, names):
    regs = c.qubit_registers()
    out = {}
    for nm in names:
        ids = regs[nm]
        w = (1 << np.arange(len(ids), dtype=object))
        bits = qubits[ids].astype(object)
        out[nm] = [int(x) for x in (bits * w[:, None]).sum(axis=0)]
    return out


def choose_engine(c: Circuit, args):
    kpy = args.kickmix_python or os.environ.get('KICKMIX_PYTHON')
    if args.engine == 'python':
        if c.n > PyTracer.MAX_OPS and not args.force:
            raise UserError(f'{c.n:,} ops is too many for the pure-Python engine (limit {PyTracer.MAX_OPS:,})',
                            'use --engine kickmix (needs kickmix), or --force to wait')
        return 'python', None
    if args.engine == 'kickmix' or c.n > PyTracer.MAX_OPS:
        if kickmix_importable():
            return 'kickmix', None
        if kpy:
            return 'kickmix', kpy
        raise UserError('this trace needs kickmix (`import kickmix` failed here)' if args.engine == 'kickmix' else
                        f'{c.n:,} ops needs the kickmix engine, and `import kickmix` failed here',
                        'on Kaggle: attach the dataset manwithacat/kickmix-fork-wheels and pip install its wheel; '
                        'elsewhere: --kickmix-python /path/to/python-with-kickmix (or set KICKMIX_PYTHON)')
    return 'python', None


def cmd_trace(args):
    (c,) = load_circuits(args.circuits, 1)
    task = detect_task(c)
    assign = parse_assignments(c, args)
    note = None
    if not assign:
        if task == 'invsqrt':
            assign, note = {'x': 161}, 'no --set given: x=161 (the worst-error input of the public invsqrt line)'
        else:
            note = 'no --set given: every register starts at 0'
    qv, bv, shown = resolve_inputs(c, assign)
    stages, src = stages_of(c, args.stages)
    cuts = trace_cuts(c, args.columns, stages)
    engine, kpy = choose_engine(c, args)
    t0 = time.time()
    if engine == 'python':
        outcomes = args.outcomes
        res = PyTracer(c).run(cuts, qv, bv, 'zero' if outcomes == 'zero' else int(outcomes))
        tier_basis = (f'pure-Python basis-state tracer, HMR outcomes {res["outcomes"]}; one shot')
    elif kpy:
        res = engine_subprocess(c, cuts, assign, args.seed, kpy)
        tier_basis = f'kickmix Simulator (via {kpy}), seed {args.seed}; one shot'
    else:
        with tempfile.TemporaryDirectory(prefix='kickmix-render-') as td:
            res = run_kickmix(c, cuts, qv, bv, args.seed, td)
        tier_basis = f'kickmix Simulator, seed {args.seed}; one shot'
    secs = round(time.time() - t0, 2)
    show = expand_names(c, args.show) if args.show else [r for r in c.qubit_registers()][:8]
    watch = expand_names(c, args.watch) if args.watch else []
    vals = register_values(c, res['qubits'], sorted(set(show) | set(watch), key=(show + watch).index))
    prof = cached_profile(c, args)
    base = out_base(args, [c], 'trace')
    ctx = dict(c=c, res=res, stages=stages, prof=prof, vals=vals, show=show, watch=watch, shown=shown,
               label=labels_for(args, [c])[0])
    files = []
    if not args.no_png:
        files.append(file_entry(draw_trace_png(ctx, args, base), kind='png'))
    if not args.no_gif:
        frames, durs = trace_frames(ctx, args)
        info = save_gif(frames, durs, str(base) + '.gif', args.max_mb)
        files.append(file_entry(str(base) + '.gif', kind='gif', **info))
    q = res['qubits']
    regq = sorted({i for ids in c.qubit_registers().values() for i in ids})
    scratch = np.setdiff1d(np.arange(q.shape[0]), regq)
    final = {nm: v[-1] for nm, v in vals.items() if nm in show[:8]}
    facts = dict(engine=res['engine'], outcomes=res['outcomes'], inputs=shown if len(shown) <= 8 else
                 f'{len(shown)} registers set', final_registers=final,
                 scratch_ones_at_end=int(q[scratch, -1].sum()), max_ones=int(q.sum(axis=0).max()),
                 toffolis_run=int(res['toffolis'][-1]), phase_turns_end=round(float(res['phase'][-1]), 12),
                 phase_exact_end=f"{res['phase_exact'][-1]} / 2^128 turn", seconds=secs, columns=len(cuts))
    if note:
        facts['note'] = note
    rows = []
    for nm in watch:
        v = vals[nm]
        z = [int(cuts[k]) for k in range(len(cuts)) if v[k] == 0]
        rows.append(dict(register=nm, zero_at_cuts=len(z), first_zero_op=z[0] if z else None,
                         start=v[0], end=v[-1]))
    rows.sort(key=lambda r: (r['first_zero_op'] is None, r['first_zero_op'] or 0))
    payload = dict(verb='trace', circuits=[circuit_entry(c)], facts=facts, cuts=res['cuts'].tolist(),
                   phase_exact=res['phase_exact'], toffolis=res['toffolis'].tolist(),
                   registers={k: v for k, v in vals.items()}, watch=rows)
    table = write_table(base, payload)
    return dict(ok=True, verb='trace', tier='sampled(1)', basis=tier_basis,
                files=files + [file_entry(table, kind='json')], circuits=[circuit_entry(c)], facts=facts,
                rows=rows, next=[f'python render.py trace {c.path} --set x=1' if task == 'invsqrt' else
                                 f'python render.py width {c.path}'])


def trace_rows(c: Circuit, nrows):
    regs = c.qubit_registers()
    order, marks = [], []
    for nm, ids in regs.items():
        marks.append((nm, len(order), len(order) + len(ids)))
        order += ids
    seen = set(order)
    order += [i for i in range(nrows) if i not in seen]
    return np.array(order, np.int64), marks


def draw_raster(ax, ctx, upto=None):
    from matplotlib.colors import ListedColormap
    c, res = ctx['c'], ctx['res']
    order, marks = trace_rows(c, res['qubits'].shape[0])
    data = res['qubits'][order]
    cuts = res['cuts']
    sc, unit = ops_axis(c.n)
    ax.pcolormesh(cuts / sc, np.arange(len(order) + 1), data[:, :-1], cmap=ListedColormap(['#ecebe6', BLUE]),
                  vmin=0, vmax=1, shading='flat', rasterized=True)
    for s in ctx['stages']:
        ax.axvline(s['a'] / sc, color=SURF, lw=1.2)
        if (s['b'] - s['a']) / c.n > 0.05:
            ax.text((s['a'] + s['b']) / 2 / sc, -0.01, s['name'], transform=ax.get_xaxis_transform(), ha='center',
                    va='top', fontsize=8, color=INK2, clip_on=False)
    ylabel = 'qubits (registers first)'
    if len(marks) <= 8:
        for nm, a, b in marks:
            ax.axhline(b, color=INK2, lw=0.6)
            ax.text(-0.005, (a + b) / 2, nm, transform=ax.get_yaxis_transform(), ha='right', va='center',
                    fontsize=8.5, color=INK)
    elif marks:
        ax.axhline(marks[-1][2], color=INK2, lw=0.6)
        ylabel = f'qubits: {len(marks)} registers\nabove the line'
    if upto is not None:
        ax.axvspan(upto / sc, c.n / sc, color=SURF, alpha=0.82, lw=0, zorder=3)
        ax.axvline(upto / sc, color=RED, lw=1.4, zorder=4)
    ax.set_ylim(len(order), 0)
    ax.set_xlim(0, c.n / sc)
    ax.set_yticks([])
    ax.grid(False)
    ax.set_ylabel(ylabel, labelpad=14 if len(marks) <= 8 else 4)
    ax.set_xlabel('')
    ax.tick_params(axis='x', labelbottom=False)


def draw_occupancy(ax, ctx, upto=None):
    c, res = ctx['c'], ctx['res']
    sc, unit = ops_axis(c.n)
    cx, mx = binned_max(ctx['prof'], 0, c.n, 600)
    ax.step(cx / sc, mx, where='mid', color=MUTED, lw=1.2, label='live wires (static width)')
    ones = res['qubits'].sum(axis=0)
    k = len(ones) if upto is None else int(np.searchsorted(res['cuts'], upto, 'right'))
    ax.step(res['cuts'][:k] / sc, ones[:k], where='post', color=BLUE, lw=1.6, label='qubits holding 1 (this shot)')
    ax.set_xlim(0, c.n / sc)
    ax.set_ylim(0, max(int(mx.max()), int(ones.max())) * 1.45 + 1)
    ax.set_ylabel('qubits')
    ax.legend(loc='upper right', fontsize=8.5, ncol=2)
    ax.set_xlabel(f'position in the circuit ({unit})')


def draw_trace_png(ctx, args, base):
    c, res = ctx['c'], ctx['res']
    fig, axes = new_fig(args.px, 0.72, 3, 1, gridspec_kw=dict(height_ratios=[3.2, 1.2, 1.0]), sharex=True)
    draw_raster(axes[0], ctx)
    draw_occupancy(axes[1], ctx)
    sc, unit = ops_axis(c.n)
    if ctx['watch']:
        zeros = np.zeros(len(res['cuts']), int)
        for nm in ctx['watch']:
            zeros += np.array(ctx['vals'][nm]) == 0
        axes[2].step(res['cuts'] / sc, zeros, where='post', color=RED, lw=1.6)
        axes[2].set_ylabel('watched\nregisters = 0')
    else:
        axes[2].step(res['cuts'] / sc, res['phase'], where='post', color=VIOLET, lw=1.6)
        axes[2].set_ylabel('phase (turns)')
        axes[2].set_ylim(-0.02, 1.02)
    axes[2].set_xlabel(f'position in the circuit ({unit})')
    axes[1].set_xlabel('')
    ins = ', '.join(f'{k}={v}' for k, v in list(ctx['shown'].items())[:3]) or 'all-zero input'
    axes[0].set_title(f'One shot through {ctx["label"]} ({ins}): blue = qubit holds 1', pad=16)
    fig.tight_layout()
    return save_png(fig, str(base) + '.png')


def trace_frames(ctx, args):
    c, res = ctx['c'], ctx['res']
    plt = plt_()
    cuts = res['cuts']
    nf = max(2, min(args.max_frames, args.frames))
    picks = np.unique(np.linspace(1, len(cuts) - 1, nf).astype(int))
    frames, durs = [], []
    regs = ctx['watch'] or ctx['show']
    grid = len(regs) > 8
    for k in picks:
        op = int(cuts[k])
        fig = plt.figure(figsize=(args.px / DPI, args.px / DPI * 0.56), dpi=DPI)
        gs = fig.add_gridspec(2, 2, width_ratios=[3, 1.25], height_ratios=[3, 1.2])
        ax0, ax1, axr = fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[1, 0]), fig.add_subplot(gs[:, 1])
        draw_raster(ax0, ctx, op)
        draw_occupancy(ax1, ctx, op)
        ax1.axvline(op / ops_axis(c.n)[0], color=RED, lw=1.2)
        stage = next((s['name'] for s in ctx['stages'] if s['a'] <= op < s['b']), ctx['stages'][-1]['name'])
        axr.axis('off')
        lines = [f'op {op:,} of {c.n:,}', f'stage: {stage}', f'Toffolis run: {int(res["toffolis"][k]):,}',
                 f'qubits holding 1: {int(res["qubits"][:, k].sum())}', f'phase: {res["phase"][k]:.6f} turn']
        axr.text(0, 1, '\n'.join(lines), va='top', fontsize=10, family='DejaVu Sans Mono', transform=axr.transAxes)
        if grid:
            vals = [ctx['vals'][nm][k] for nm in regs]
            cols = 10
            for j, (nm, v) in enumerate(zip(regs, vals)):
                r, cc = divmod(j, cols)
                x, y = cc / cols, 0.62 - r * 0.075
                axr.add_patch(plt.Rectangle((x, y), 0.92 / cols, 0.065, transform=axr.transAxes,
                                            fc=RED if v == 0 else '#e3e2dd', ec='none'))
                axr.text(x + 0.46 / cols, y + 0.032, '0' if v == 0 else '', transform=axr.transAxes,
                         ha='center', va='center', fontsize=7, color='white', fontweight='bold')
            nz = sum(1 for v in vals if v == 0)
            axr.text(0, 0.7, f'{nz} of {len(regs)} registers equal 0 (red)\n{regs[0]}, {regs[1]}, ... row by row',
                     transform=axr.transAxes, fontsize=9.5, color=INK)
        else:
            body = '\n'.join(f'{nm} = {ctx["vals"][nm][k]}' for nm in regs[:8])
            axr.text(0, 0.62, 'registers\n' + body, va='top', fontsize=10, family='DejaVu Sans Mono',
                     transform=axr.transAxes)
        ins = ', '.join(f'{a}={b}' for a, b in list(ctx['shown'].items())[:3]) or 'all-zero input'
        fig.suptitle(f'One shot through {ctx["label"]} ({ins})', x=0.01, ha='left', fontsize=12, fontweight='bold')
        fig.tight_layout()
        frames.append(fig_image(fig))
        durs.append(args.hold // 2 if k != picks[-1] else args.hold * 3)
    return frames, durs


# ------------------------------------------------------------------------------------------------ verb: cost
INVSQRT_MAX_X = 3_133_452


def sample_toffolis(c: Circuit, stages, shots, seed):
    """Mean Toffoli-like gates executed per shot (and per stage) over `shots` random invsqrt inputs x in
    1..3,133,452 with random HMR outcomes, on the pure-Python tracer."""
    tr = PyTracer(c)
    cuts = np.array(sorted({0, c.n} | {s['a'] for s in stages} | {s['b'] for s in stages}), np.int64)
    rng = random.Random(seed)
    xq = c.qubit_registers()['x']
    tot = np.zeros(len(cuts), float)
    for _ in range(shots):
        x = rng.randint(1, INVSQRT_MAX_X)
        res = tr.run(cuts, {q: (x >> j) & 1 for j, q in enumerate(xq)}, {}, rng.getrandbits(32))
        tot += res['toffolis']
    mean = tot / shots
    per = {s['name']: float(mean[np.searchsorted(cuts, s['b'])] - mean[np.searchsorted(cuts, s['a'])])
           for s in stages}
    return float(mean[-1]), per


def cost_model(c: Circuit, task, args, mean_t=None, basis=None):
    s = static_stats(c)
    T = float(mean_t) if mean_t is not None else float(s['toffoli'])
    Q = c.num_qubits
    out = dict(task=task, Q=Q, toffoli_static=s['toffoli'], toffoli_used=round(T, 2),
               toffoli_basis=basis or ('caller-supplied mean (--mean-toffolis)' if mean_t is not None
                                       else 'static count (upper bound)'))
    if task == 'invsqrt':
        zc = zpow_classes(c)
        rot_t = args.rot_t
        C = T + (zc['t'] + zc['rotation'] * rot_t) / 4.0
        out.update(t_angles=zc['t'], rotations=zc['rotation'], rot_t=rot_t, toffoli_equiv=round(C, 2),
                   formula='Q × (Toffolis + (T + rotations × rot_t) / 4)', cost=Q * C)
    elif task == 'dqi':
        out.update(R=args.dqi_r, formula='R × Q² × Toffolis', cost=args.dqi_r * Q * Q * T)
    else:
        out.update(formula='Q × Toffolis (generic)', cost=Q * T)
    ref = REFERENCE_COST.get(task)
    if ref:
        out['reference_cost'] = ref
        out['score_ratio'] = round(ref / out['cost'], 5)
    out['cost'] = round(out['cost'], 2)
    return out


def cmd_cost(args):
    cs = load_circuits(args.circuits)
    labs = labels_for(args, cs)
    tasks = [args.task if args.task != 'auto' else detect_task(c) for c in cs]
    means, bases, per_stage = [None] * len(cs), [None] * len(cs), [None] * len(cs)
    stg = [stages_of(c, args.stages) for c in cs]
    stages = [s for s, _ in stg]
    tier, tier_basis = 'static', ('static op counts: Toffolis = every Toffoli-like gate in the file (an upper bound '
                                  'on the per-shot mean the verifier measures; conditional ones may be skipped)')
    if args.mean_toffolis:
        vals = [float(x) for x in args.mean_toffolis.split(',')]
        if len(vals) != len(cs):
            raise UserError('--mean-toffolis needs one value per circuit', 'e.g. --mean-toffolis 1329.5,1301.2')
        means = vals
        tier_basis = 'static Q and rotation counts; Toffoli means supplied by the caller (--mean-toffolis)'
    else:
        shots = args.sample if args.sample is not None else 200
        if shots > 0 and all(t == 'invsqrt' and c.n <= PyTracer.MAX_OPS for c, t in zip(cs, tasks)):
            for k, c in enumerate(cs):
                means[k], per_stage[k] = sample_toffolis(c, stages[k], shots, args.seed)
                bases[k] = f'sampled mean over {shots} random x (python tracer, seed {args.seed})'
            tier = f'sampled({shots})'
            tier_basis = (f'Toffolis: mean executed per shot over {shots} random x with random HMR outcomes '
                          '(pure-Python tracer); Q and rotations static')
    models = [cost_model(c, t, args, m, b) for c, t, m, b in zip(cs, tasks, means, bases)]
    profs = [cached_profile(c, args) for c in cs]
    rows = []
    pairs = dict(align_stages(stages[0], stages[1])) if len(cs) == 2 else {}
    curve = [np.linspace(0, c.n, 801).astype(np.int64) for c in cs]
    cc = [cumulative_toffolis(c, [x for st in stages[k] for x in (st['a'], st['b'])] + curve[k].tolist())
          for k, c in enumerate(cs)]
    cum = [{p: v[0] for p, v in d.items()} for d in cc]
    condcum = [{p: v[1] for p, v in d.items()} for d in cc]
    totT = [int(cum[k][c.n]) for k, c in enumerate(cs)]
    for i, s in enumerate(stages[0]):
        r = dict(stage=s['name'], ops=s['b'] - s['a'], toffoli=int(cum[0][s['b']] - cum[0][s['a']]),
                 toffoli_cond=int(condcum[0][s['b']] - condcum[0][s['a']]),
                 peak=int(profs[0][s['a']:s['b']].max()))
        r['share'] = round(r['toffoli'] / max(1, totT[0]), 4)
        if per_stage[0]:
            r['toffoli_mean'] = round(per_stage[0][s['name']], 1)
        if len(cs) == 2:
            j = pairs.get(i)
            if j is not None:
                B = stages[1][j]
                r['toffoli_b'] = int(cum[1][B['b']] - cum[1][B['a']])
                r['peak_b'] = int(profs[1][B['a']:B['b']].max())
                if per_stage[1]:
                    r['toffoli_mean_b'] = round(per_stage[1][B['name']], 1)
        rows.append(r)
    for j, B in enumerate(stages[1] if len(cs) == 2 else []):
        if j not in pairs.values():
            rows.append(dict(stage=B['name'] + ' (B only)', ops=B['b'] - B['a'], toffoli=0, toffoli_cond=0, peak=0,
                             share=0.0, toffoli_b=int(cum[1][B['b']] - cum[1][B['a']]),
                             peak_b=int(profs[1][B['a']:B['b']].max())))
    facts = dict(models=[{k: m[k] for k in ('task', 'Q', 'toffoli_used', 'cost', 'score_ratio') if k in m}
                         for m in models], certified_width=[int(p.max()) for p in profs], stages=stg[0][1])
    if len(cs) == 2:
        facts['b_over_a_score'] = round(models[0]['cost'] / models[1]['cost'], 5)
    base = out_base(args, cs, 'cost')
    files = []
    if not args.no_png:
        curves = [(curve[k], np.array([cum[k][int(x)] for x in curve[k]])) for k in range(len(cs))]
        files.append(file_entry(draw_cost_png(cs, labs, models, profs, stages, rows, curves, args, base), kind='png'))
    payload = dict(verb='cost', circuits=[circuit_entry(c) for c in cs], models=models, facts=facts, rows=rows)
    table = write_table(base, payload)
    return dict(ok=True, verb='cost', tier=tier, basis=tier_basis, files=files + [file_entry(table, kind='json')],
                circuits=[circuit_entry(c) for c in cs], facts=facts, rows=rows,
                next=['official verifier for the measured cost: kickmix + the competition verify script'])


def fmt_num(x):
    x = float(x)
    if abs(x) >= 1e12:
        return f'{x / 1e12:.3f} T'
    if abs(x) >= 1e9:
        return f'{x / 1e9:.3f} G'
    if abs(x) >= 1e6:
        return f'{x / 1e6:.3f} M'
    return f'{x:,.1f}'


def draw_cost_png(cs, labs, models, profs, stages, rows, curves, args, base):
    plt = plt_()
    fig = plt.figure(figsize=(args.px / DPI, args.px / DPI * 0.78), dpi=DPI)
    gs = fig.add_gridspec(3, 2, height_ratios=[0.8, 2.2, 1.6], hspace=0.75, wspace=0.32)
    axk = fig.add_subplot(gs[0, :])
    axk.axis('off')
    colours = [BLUE, ORANGE]
    sampled = 'sampled' in models[0]['toffoli_basis']
    tiles = [('qubits Q', [str(m['Q']) for m in models]),
             ('Toffolis/shot' + (' (mean)' if sampled else ' (static)'),
              [f"{m['toffoli_used']:,.1f}" for m in models]),
             ('cost', [fmt_num(m['cost']) for m in models])]
    if 'score_ratio' in models[0]:
        tiles.append(('score ratio (ref ÷ cost)', [f"{m.get('score_ratio', 0):.4f}" for m in models]))
    if len(cs) == 2:
        tiles.append(('B vs A (cost A ÷ B)', [f"{models[0]['cost'] / models[1]['cost']:.4f}×"]))
    ntile = len(tiles)
    for k, (h, vals) in enumerate(tiles):
        x = k / ntile
        axk.add_patch(plt.Rectangle((x + 0.005, 0.0), 1 / ntile - 0.01, 1.0, transform=axk.transAxes, fc='#f1f0ec',
                                    ec='none'))
        axk.text(x + 0.015, 0.84, h, transform=axk.transAxes, fontsize=8.5, color=INK2, va='center')
        for j, v in enumerate(vals):
            yv = 0.5 - 0.3 * j if len(vals) > 1 else 0.4
            if len(vals) > 1:
                axk.add_patch(plt.Rectangle((x + 0.015, yv - 0.07), 0.008, 0.14, transform=axk.transAxes,
                                            fc=colours[j], ec='none'))
            axk.text(x + (0.03 if len(vals) > 1 else 0.015), yv, v, transform=axk.transAxes,
                     fontsize=12 if len(vals) > 1 else 14, fontweight='bold', color=INK, va='center')
    m0 = models[0]
    axk.set_title(f"{' vs '.join(labs)}: {m0['task']} cost", pad=18, fontsize=12)
    axk.text(0, 1.04, f"cost = {m0['formula']}" + (f"; rot_t = {m0['rot_t']:g}" if 'rot_t' in m0 else '')
             + (f"; R = {m0['R']:g}" if 'R' in m0 else '') + '. Static Q; see the summary for tiers.',
             transform=axk.transAxes, fontsize=8.5, color=INK2)
    # per-stage Toffolis
    axb = fig.add_subplot(gs[1, 0])
    show = [r for r in rows if r['toffoli'] or r.get('toffoli_b')]
    if len(show) > 14:
        show = sorted(sorted(show, key=lambda r: -(r['toffoli'] + r.get('toffoli_b', 0)))[:14],
                      key=lambda r: rows.index(r))
    y = np.arange(len(show))[::-1]
    h = 0.38 if len(cs) == 2 else 0.6
    ka, kb = ('toffoli_mean', 'toffoli_mean_b') if 'toffoli_mean' in rows[0] else ('toffoli', 'toffoli_b')
    axb.barh(y + (h / 2 if len(cs) == 2 else 0), [r.get(ka, 0) for r in show], height=h, color=BLUE, label=labs[0])
    if len(cs) == 2:
        axb.barh(y - h / 2, [r.get(kb, 0) for r in show], height=h, color=ORANGE, label=labs[1])
        axb.legend(loc='upper center', bbox_to_anchor=(0.5, -0.2), fontsize=8.5, ncol=2)
    axb.set_yticks(y, [r['stage'][:16] for r in show], fontsize=8.5)
    axb.set_xlabel('Toffolis in the stage' + (' (mean per shot, sampled)' if ka == 'toffoli_mean' else ' (static)'))
    axb.set_title('Where the Toffolis are', fontsize=11)
    axb.grid(axis='y', visible=False)
    # per-stage peak width
    axw = fig.add_subplot(gs[1, 1])
    yy = np.arange(len(rows))[::-1]
    pa = [(r['peak'], y_) for r, y_ in zip(rows, yy) if r['peak']]
    axw.scatter([p for p, _ in pa], [y_ + (0.15 if len(cs) == 2 else 0) for _, y_ in pa], color=BLUE, s=36, zorder=3,
                label=labs[0])
    if len(cs) == 2:
        pb = [(r.get('peak_b'), y_) for r, y_ in zip(rows, yy) if r.get('peak_b') is not None]
        axw.scatter([p for p, _ in pb], [y_ - 0.15 for _, y_ in pb], color=ORANGE, s=36, zorder=3, label=labs[1])
    for k, c in enumerate(cs):
        axw.axvline(c.num_qubits, color=colours[k], lw=1, ls=':')
    axw.set_yticks(yy, [r['stage'][:16] for r in rows], fontsize=8.5)
    axw.set_xlabel('peak live qubits in the stage (dotted: Q)', labelpad=2)
    axw.set_title('Which stages set the width', fontsize=11)
    axw.grid(axis='y', visible=False)
    allp = [r['peak'] for r in rows if r['peak']] + [r['peak_b'] for r in rows if r.get('peak_b')]
    allp += [c.num_qubits for c in cs]
    axw.set_xlim(min(allp) - max(2, (max(allp) - min(allp)) * 0.1), max(allp) + 1)
    axw.xaxis.get_major_locator().set_params(integer=True)
    axw.legend(loc='upper center', bbox_to_anchor=(0.5, -0.2), fontsize=8.5, ncol=2)
    # cumulative Toffolis along the circuit
    axc = fig.add_subplot(gs[2, :])
    sc, unit = ops_axis(cs[0].n)
    xs, ys = curves[0]
    axc.plot(xs / sc, ys, color=BLUE, lw=1.8, label=labs[0])
    if len(cs) == 2:
        xb, xa = position_map(stages[0], cs[0].n, stages[1], cs[1].n)
        xsb, ysb = curves[1]
        axc.plot(np.interp(xsb, xb, xa) / sc, ysb, color=ORANGE, lw=1.8, label=labs[1])
    draw_bands(axc, stages[0], 0, cs[0].n, sc, axc.get_ylim()[1], None, labels=False)
    axc.set_xlim(0, cs[0].n / sc)
    axc.set_ylabel('Toffolis so far\n(static)')
    axc.set_xlabel(f'position in A ({unit})' + ('; B aligned stage by stage' if len(cs) == 2 else ''))
    axc.legend(loc='upper left', fontsize=8.5)
    axc.set_title('Toffolis accumulated along the circuit', fontsize=11)
    fig.subplots_adjust(left=0.13, right=0.98, top=0.93, bottom=0.07)
    return save_png(fig, str(base) + '.png')


# ------------------------------------------------------------------------------------------------ verb: ledger
# Double-entry view of a reversible circuit. Accounts are wires (grouped into registers); journal entries are the ops
# that touch them. A value (an "invoice") is one lifetime of a non-register wire, as in `width`: it opens at the wire's
# first touch after a reset and is settled by a unitary uncompute (contra: it ends at its last touch), by an HMR whose
# bit later conditions phase fix-ups (accrual: a phase liability stays open until the last fix-up), or by a reset or
# an HMR nobody repays (dead tail). Width is capital priced at the task's exchange rate (T-eq per qubit for the whole
# circuit); Toffolis are expenses. Balances are per basis trajectory, not statements about superpositions.
SETTLE_KINDS = ('contra', 'accrual', 'dead-tail')
ENTRY_CLASSES = ('toffoli', 'data', 'phase', 'control', 'settle')  # how an op touches one of its qubit operands
E_TOF, E_DATA, E_PHASE, E_CTRL, E_SETTLE = range(5)


def entry_table():
    """Entry class for (op base type, operand slot): index base * 4 + slot."""
    tab = np.full(128 * 4, E_CTRL, np.uint8)

    def put(base, *classes):
        for s, k in enumerate(classes):
            tab[base * 4 + s] = k
    put(CCX, E_CTRL, E_CTRL, E_TOF)
    put(CSWAP, E_CTRL, E_TOF, E_TOF)
    put(CCZ, E_PHASE, E_PHASE, E_PHASE)
    put(CX, E_CTRL, E_DATA)
    put(SWAP, E_DATA, E_DATA)
    put(CZ, E_PHASE, E_PHASE)
    put(X, E_DATA)
    put(Z, E_PHASE)
    put(Z_POW, E_PHASE)
    put(R, E_SETTLE)
    put(HMR, E_SETTLE)
    return tab


def sorted_qubit_refs(c: Circuit):
    """Every qubit operand reference as (qubit, op, slot), sorted by qubit then op. About 9 bytes per reference after
    the sort (DQI: 113 M references, about 2.5 GB at the peak)."""
    t = c.types
    cls = t & 0x30
    total = 3 * c.counts['qqq'] + 2 * c.counts['qq'] + c.counts['q']
    key = np.empty(total, np.uint64)
    pos = 0
    for code, names in ((0x30, ['qqq0', 'qqq1', 'qqq2']), (0x20, ['qq0', 'qq1']), (0x10, ['q0'])):
        ops = np.flatnonzero(cls == code).view(np.uint64)
        ops <<= np.uint64(2)
        for j, nm in enumerate(names):
            k = len(ops)
            seg = key[pos:pos + k]
            seg[:] = c.stream(nm)
            seg <<= np.uint64(34)
            seg |= ops
            seg |= np.uint64(j)
            pos += k
        del ops
    key.sort()
    q = np.empty(total, np.uint32)
    op = np.empty(total, np.uint32)
    slot = np.empty(total, np.uint8)
    for a in range(0, total, 8_000_000):  # decode in chunks: no full-size temporaries
        k = key[a:a + 8_000_000]
        q[a:a + len(k)] = k >> np.uint64(34)
        op[a:a + len(k)] = (k >> np.uint64(2)) & np.uint64(0xFFFFFFFF)
        slot[a:a + len(k)] = k & np.uint64(3)
    del key, k
    return q, op, slot


def bit_liabilities(c: Circuit):
    """Phase liabilities: one per HMR. The HMR's bit is a classical record; the debt is repaid by ops it conditions
    (PUSH_CONDITION blocks and _IF forms: the phase fix-ups). Bits are followed through BIT_INVERT_IF/BIT_STORE_IF,
    which fold one bit into another, so a fix-up conditioned on a parity of records repays all of them.

    Returns dict of arrays over liabilities: op (HMR position), qubit, depth0 (unconditional HMR), settle (last op
    conditioned on the record or anything derived from it; -1 = never: a dead tail), reads (direct fix-up reads)."""
    t = c.types
    hb, hc = (t & HAS_1B) > 0, (t & HAS_COND) > 0
    idx = np.flatnonzero(hb | hc)
    tt = np.asarray(t[idx])
    h0, h1 = hb[idx], hc[idx]
    b0 = np.full(len(idx), -1, np.int64)
    b0[h0] = c.stream('b0')
    bcv = np.full(len(idx), -1, np.int64)
    bcv[h1] = c.stream('bc')
    depth = condition_depth(t)
    cur = {}  # bit -> current version
    v_last, v_reads, v_p1, v_p2 = [], [], [], []
    hmr_v, hmr_op, fix_at = [], [], []
    BITW = (BIT_STORE0, BIT_STORE1, BIT_INVERT)
    for i, op, x0, xc in zip(idx.tolist(), tt.tolist(), b0.tolist(), bcv.tolist()):
        base = op & 0x7F
        if xc >= 0 and not (base in BITW and op != PUSH):
            v = cur.get(xc)
            if v is not None:  # a quantum op (or a PUSH) conditioned on the bit: a repayment entry
                v_last[v] = i
                v_reads[v] += 1
                fix_at.append(i)
        if x0 < 0 or op == PUSH:
            continue
        if base == HMR:
            old = cur.get(x0, -1) if op & HAS_COND else -1
            cur[x0] = len(v_last)
            hmr_v.append(len(v_last))
            hmr_op.append(i)
            v_last.append(-1), v_reads.append(0), v_p1.append(old), v_p2.append(-1)
        elif base in BITW:
            if op & HAS_COND:  # b0 now depends on its old value and on the condition bit
                p1, p2 = cur.get(x0, -1), cur.get(xc, -1)
                if p1 < 0 and p2 < 0:
                    continue
                cur[x0] = len(v_last)
                v_last.append(-1), v_reads.append(0), v_p1.append(p1), v_p2.append(p2)
            elif base != BIT_INVERT:
                cur.pop(x0, None)  # overwritten with a constant: earlier debts no longer flow through this bit
    desc = list(v_last)
    for v in range(len(desc) - 1, -1, -1):  # parents are older versions: one reverse pass gives the latest repayment
        d = desc[v]
        for p in (v_p1[v], v_p2[v]):
            if p >= 0 and d > desc[p]:
                desc[p] = d
    hv = np.array(hmr_v, np.int64)
    hop = np.array(hmr_op, np.int64)
    settle = np.array(desc, np.int64)[hv] if len(hv) else np.zeros(0, np.int64)
    reads = np.array(v_reads, np.int64)[hv] if len(hv) else np.zeros(0, np.int64)
    # the HMR's qubit: q0 stream entry of that op
    q1 = np.flatnonzero((t & 0x30) == 0x10)
    qubit = np.asarray(c.stream('q0'))[np.searchsorted(q1, hop)] if len(hop) else np.zeros(0, np.uint32)
    return dict(op=hop, qubit=qubit.astype(np.int64), depth0=depth[hop] == 0 if len(hop) else np.zeros(0, bool),
                settle=settle, reads=reads, fixups=np.array(fix_at, np.int64))


def register_accounts(c: Circuit):
    """Account names: register names, with numbered families folded (S0..S69 -> S*). Returns (names, qubit->index)."""
    regs = c.qubit_registers()
    fam = {}
    for name, qs in regs.items():
        root = name.rstrip('0123456789')
        key = root + '*' if root != name and sum(1 for n in regs if n.rstrip('0123456789') == root) > 1 else name
        fam.setdefault(key, []).extend(qs)
    names = sorted(fam, key=lambda k: min(fam[k]))
    return names, {q: k for k, nm in enumerate(names) for q in fam[nm]}


def seg_sum(mask, seg, endr):
    """Sum of a boolean array over the segments [seg[i], endr[i]] (int32 running sum: no int64 copy)."""
    cs = np.cumsum(mask, dtype=np.int32)
    before = np.zeros(len(seg), np.int64)
    nz = seg > 0
    before[nz] = cs[seg[nz] - 1]
    return cs[endr].astype(np.int64) - before


def ledger_core(c: Circuit, prof, nbins=240):
    """The static ledger of one circuit: values (lifetimes) with their entries and settlement, phase liabilities,
    identical-operand Toffoli repeats, and entry counts per account over time (T-accounts)."""
    n = c.n
    t = c.types
    depth = condition_depth(t)
    base = np.asarray(t & 0x7F)
    tt = np.asarray(t)
    reset = ((tt == R) | (tt == HMR)) & (depth == 0)  # unconditional only (an R_IF/HMR_IF may be skipped)
    del tt
    q, op, slot = sorted_qubit_refs(c)
    N = len(q)
    first = np.ones(N, bool)
    first[1:] = q[1:] != q[:-1]
    start = first.copy()
    start[1:] |= reset[op[:-1]]
    seg = np.flatnonzero(start)
    endr = np.r_[seg[1:], N] - 1
    lt_q = q[seg].astype(np.int64)
    lt_a = op[seg].astype(np.int64)
    lt_b = op[endr].astype(np.int64)
    touches = np.diff(np.r_[seg, N])
    # idle gaps and idle time at full width
    gap = np.zeros(N, np.uint32)
    gap[1:] = op[1:] - op[:-1]
    gap[start] = 0
    idle_max = np.maximum.reduceat(gap, seg).astype(np.int64)
    top = int(prof.max())
    atpk = np.asarray(prof == top)
    pk = np.zeros(n + 1, np.int32)  # pk[k] = ops at full width before op k
    np.cumsum(atpk, out=pk[1:])
    # ops at full width strictly inside the value's idle gaps = full-width ops in [a, b) minus the touches made at
    # full width (all touches but the last)
    touched_pk = seg_sum(atpk[op], seg, endr) - atpk[lt_b]
    idle_peak = pk[lt_b].astype(np.int64) - pk[lt_a] - touched_pk
    del touched_pk
    # release candidates: scratch values (not register wires, not a reset on a fresh wire)
    regq = register_qubit_ids(c)
    isr = np.zeros(max(int(q.max()) + 1 if N else 1, regq[-1] + 1 if regq else 0), bool)
    isr[regq] = True
    cand = ~isr[lt_q] & ~((touches == 1) & reset[lt_b])
    gap_at = {}  # for the 64 candidates ranked first: (longest idle gap) + (the gap holding most full-width ops)
    for i in peak_order(np.flatnonzero(cand), idle_peak, idle_max, lt_a)[:64].tolist():
        if idle_max[i] > 0:
            s0, s1 = int(seg[i]), int(endr[i]) + 1
            j = s0 + int(np.argmax(gap[s0:s1]))
            o = op[s0:s1].astype(np.int64)
            k = int(np.argmax(pk[o[1:]] - pk[o[:-1] + 1]))
            gap_at[i] = (int(op[j - 1]), int(op[j]), int(o[k]), int(o[k + 1]))
    ra, rb = full_width_runs(atpk)
    pgaps = peak_covering_gaps(op, seg, gap, pk, ra, rb, cand)
    del gap
    # entries per value by class
    tab = entry_table()
    ec = np.empty(N, np.uint8)
    for a in range(0, N, 8_000_000):
        ec[a:a + 8_000_000] = tab[base[op[a:a + 8_000_000]].astype(np.uint16) * 4 + slot[a:a + 8_000_000]]
    counts = np.zeros((5, len(seg)), np.int64)
    for k in range(5):
        counts[k] = seg_sum(ec == k, seg, endr)
    # Toffoli controls with identical wire versions (version = writes on the wire before this reference)
    w = ((ec == E_TOF) | (ec == E_DATA) | (ec == E_SETTLE))
    cw = np.cumsum(w, dtype=np.int32)
    tofmask = (base[op] == CCX) & (slot < 2)
    sel = np.flatnonzero(tofmask)
    del tofmask
    qfirst = np.flatnonzero(first)
    q0 = qfirst[np.searchsorted(qfirst, sel, side='right') - 1]
    ver = cw[sel].astype(np.int64) - w[sel] - (cw[q0].astype(np.int64) - w[q0])
    del cw, w, q0, qfirst
    rep = identical_operand_repeats(op[sel], slot[sel], q[sel], ver, c)
    del ver, sel
    # T-accounts: entries per account and time bin
    names, acc_of = register_accounts(c)
    regs = np.array(sorted(acc_of), np.int64)
    maxq = int(q.max()) + 1 if N else 1
    is_reg = np.zeros(maxq + 1, bool)
    if len(regs):
        is_reg[regs] = True
    scratch = np.flatnonzero(~is_reg[:maxq])
    per = 1 if len(scratch) <= 48 else -(-len(scratch) // 16)  # one account per scratch wire, or 16 bands
    acc = np.zeros(maxq + 1, np.int64)
    acc[scratch] = len(names) + np.arange(len(scratch)) // per
    if len(regs):
        acc[regs] = [acc_of[r] for r in regs.tolist()]
    bands = [f'q{scratch[i]}' if per == 1 else f'q{scratch[i]}..q{scratch[min(i + per, len(scratch)) - 1]}'
             for i in range(0, len(scratch), per)]
    nacc = len(names) + len(bands)
    tacc = np.zeros(nacc * nbins * 5, np.int64)
    for a in range(0, N, 16_000_000):
        b = min(N, a + 16_000_000)
        bins = (op[a:b].astype(np.int64) * nbins) // max(1, n)
        tacc += np.bincount((acc[q[a:b]] * nbins + bins) * 5 + ec[a:b], minlength=len(tacc))
    tacc = tacc.reshape(nacc, nbins, 5)
    del q, op, slot, ec, first, start
    reg_lt = is_reg[lt_q]
    liab = bit_liabilities(c)
    # settlement of each value
    kind = np.zeros(len(seg), np.int8)  # 0 contra (unitary uncompute presumed), 1 accrual, 2 dead tail
    ends_reset = reset[lt_b]
    lbase = base[lt_b]
    kind[ends_reset & (lbase == R)] = 2
    hm = ends_reset & (lbase == HMR)
    lpos = np.searchsorted(liab['op'], lt_b[hm])
    lpos = np.minimum(lpos, max(0, len(liab['op']) - 1))
    paid = liab['settle'][lpos] >= 0 if len(liab['op']) else np.zeros(int(hm.sum()), bool)
    kind[np.flatnonzero(hm)[paid]] = 1
    kind[np.flatnonzero(hm)[~paid]] = 2
    liab_end = np.full(len(seg), -1, np.int64)
    if len(liab['op']):
        liab_end[np.flatnonzero(hm)] = liab['settle'][lpos]
    init = (touches == 1) & ends_reset  # a reset on a fresh wire: not an invoice
    vals = dict(q=lt_q, a=lt_a, b=lt_b, touches=touches, idle_max=idle_max, idle_peak=idle_peak,
                tof=counts[E_TOF], data=counts[E_DATA], phase=counts[E_PHASE], ctrl=counts[E_CTRL],
                kind=kind, liab_end=liab_end, reg=reg_lt, init=init)
    return dict(values=vals, gap_at=gap_at, liab=liab, repeats=rep, taccounts=tacc, accounts=names + bands, n_reg_accounts=len(names), top=top,
                regs=len(regs), runs=dict(a=ra, b=rb, **pgaps))


def peak_order(idx, idle_peak, idle_max, a):
    """Values ranked for release at the peak: most ops idle at full width first, then longest idle, then earliest."""
    idx = np.asarray(idx, np.int64)
    return idx[np.lexsort((a[idx], -idle_max[idx], -idle_peak[idx]))]


def full_width_runs(atpk):
    """Maximal op ranges [a, b] at full width (inclusive)."""
    d = np.diff(np.r_[0, np.asarray(atpk, np.int8), 0])
    return np.flatnonzero(d == 1).astype(np.int64), np.flatnonzero(d == -1).astype(np.int64) - 1


def peak_covering_gaps(op, seg, gap, pk, ra, rb, cand, chunk=8_000_000):
    """Idle gaps of candidate values that contain whole full-width runs. A gap is the stretch between two consecutive
    touches p < n of one value; it covers runs i0..i1 when p < ra[i0] and rb[i1] < n (no touch inside any of them).
    Returns value index, first and last run covered, per gap (gaps of one value are disjoint, so no run twice)."""
    gv, g0, g1 = [], [], []
    for s in range(0, len(op), chunk):
        j = s + np.flatnonzero(gap[s:s + chunk] > 1)  # a non-empty interior; lifetime starts have gap 0
        if not len(j) or not len(ra):
            continue
        p, nn = op[j - 1].astype(np.int64), op[j].astype(np.int64)
        k = (pk[nn] - pk[p + 1]) > 0
        j, p, nn = j[k], p[k], nn[k]
        i0 = np.searchsorted(ra, p, side='right')
        i1 = np.searchsorted(rb, nn, side='left') - 1
        k = i1 >= i0
        v = np.searchsorted(seg, j[k], side='right') - 1
        ok = cand[v]
        gv.append(v[ok]), g0.append(i0[k][ok]), g1.append(i1[k][ok])
    cat = (lambda xs: np.concatenate(xs).astype(np.int64) if xs else np.zeros(0, np.int64))
    return dict(gap_value=cat(gv), gap_first=cat(g0), gap_last=cat(g1))


def run_cover(runs, nvals):
    """Per run: how many candidate values are idle across it; the (value, run) cover pairs, sorted by run; per value:
    how many full-width runs it is idle across."""
    nr = len(runs['a'])
    gv, g0, g1 = runs['gap_value'], runs['gap_first'], runs['gap_last']
    ln = g1 - g0 + 1
    d = np.zeros(nr + 1, np.int64)
    np.add.at(d, g0, 1)
    np.add.at(d, g1 + 1, -1)
    through = np.cumsum(d)[:nr]
    tot = int(ln.sum())
    off = np.repeat(np.cumsum(ln) - ln, ln)
    pr = np.arange(tot, dtype=np.int64) - off + np.repeat(g0, ln)
    pv = np.repeat(gv, ln)
    o = np.lexsort((pv, pr))
    per_value = np.bincount(gv, weights=ln, minlength=nvals).astype(np.int64)
    return through, pv[o], pr[o], per_value


def min_hitting_set(sets, rank=None, budget=200_000):
    """Fewest candidates whose sets together contain every element (unit cost per candidate).

    sets: {candidate: iterable of elements}; rank: {candidate: tie-break, lower first} (default: key order).
    Greedy gives an upper bound. The instance is then reduced (an element whose candidates include all of another
    element's is dropped: hitting the smaller hits it; candidates with equal or dominated sets are dropped) and
    solved by branch and bound, with a disjoint-element packing as the lower bound, within `budget` nodes.
    Returns dict(chosen, size, exact, method, lower_bound, elements, candidates, kernel)."""
    sets = {k: frozenset(v) for k, v in sets.items() if v}
    rank = rank or {k: i for i, k in enumerate(sets)}
    U = frozenset().union(*sets.values()) if sets else frozenset()
    res = dict(elements=len(U), candidates=len(sets))
    if not U:
        return dict(res, chosen=[], size=0, exact=True, method='empty', lower_bound=0, kernel=[0, 0])
    keys = sorted(sets, key=lambda k: rank[k])
    unc, greedy = set(U), []
    while unc:
        k = max(keys, key=lambda k: (len(sets[k] & unc), -rank[k]))
        greedy.append(k)
        unc -= sets[k]
    cov = {}
    for k in keys:
        for e in sets[k]:
            cov.setdefault(e, []).append(k)
    cov = {e: frozenset(v) for e, v in cov.items()}
    kept = []
    for e in sorted(U, key=lambda e: (len(cov[e]), repr(e))):
        if not any(cov[x] <= cov[e] for x in kept):
            kept.append(e)
    E = frozenset(kept)
    red, seen = {}, set()
    for k in keys:
        s = sets[k] & E
        if s and s not in seen:
            red[k] = s
            seen.add(s)
    cands = sorted(red, key=lambda k: (-len(red[k]), rank[k]))
    maxi = []
    for k in cands:
        if not any(red[k] <= red[x] for x in maxi):
            maxi.append(k)
    red = {k: red[k] for k in maxi}
    kcov = {e: [k for k in maxi if e in red[k]] for e in kept}

    def packing(elems):
        used, m = set(), 0
        for e in sorted(elems, key=lambda e: (len(kcov[e]), repr(e))):
            if not used.intersection(kcov[e]):
                used.update(kcov[e])
                m += 1
        return m
    lb = packing(kept)
    best, nodes, aborted = list(greedy), 0, False

    def rec(un, chosen):
        nonlocal best, nodes, aborted
        if not un:
            if len(chosen) < len(best):
                best = list(chosen)
            return
        nodes += 1
        if nodes > budget:
            aborted = True
            return
        if len(chosen) + packing(un) >= len(best):
            return
        e = min(un, key=lambda e: (len(kcov[e]), repr(e)))
        for k in sorted(kcov[e], key=lambda k: (-len(red[k] & un), rank[k])):
            chosen.append(k)
            rec(un - red[k], chosen)
            chosen.pop()
            if aborted:
                return
    if lb < len(greedy):
        rec(frozenset(kept), [])
    exact = not aborted
    if lb == len(greedy):
        method = 'greedy, equal to the packing lower bound (optimal)'
    elif exact:
        method = f'exact: branch and bound on the reduced instance ({nodes:,} nodes)'
    else:
        method = f'greedy upper bound; exact search stopped at {budget:,} nodes'
    chosen = sorted(best, key=lambda k: rank[k])
    return dict(res, chosen=chosen, size=len(chosen), exact=exact, method=method,
                lower_bound=len(chosen) if exact else lb, kernel=[len(kept), len(maxi)])


def identical_operand_repeats(op, slot, q, ver, c: Circuit):
    """CCX whose two controls hold the same wire versions (no write on either in between) as an earlier CCX:
    the same AND computed again (static, exact for this op stream). Same target = a unitary uncompute pair."""
    if len(op) == 0:
        return dict(groups=0, repeats=0, uncompute_pairs=0, ops=[])
    o = np.argsort(op.astype(np.int64) * 2 + slot, kind='stable')
    op, q, ver = op[o].astype(np.int64), q[o].astype(np.int64), ver[o].astype(np.int64)
    a0, a1 = q[0::2], q[1::2]
    v0, v1 = ver[0::2], ver[1::2]
    ops = op[0::2]
    swap = a0 > a1
    lo_q, hi_q = np.where(swap, a1, a0), np.where(swap, a0, a1)
    lo_v, hi_v = np.where(swap, v1, v0), np.where(swap, v0, v1)
    keys = np.stack([lo_q, lo_v, hi_q, hi_v], 1)
    _, inv, cnt = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    # targets of these CCX (qqq2 stream, in op order)
    q3 = np.flatnonzero((c.types & 0x30) == 0x30)
    tgt = np.asarray(c.stream('qqq2'))[np.searchsorted(q3, ops)].astype(np.int64)
    order = np.lexsort((ops, inv))
    groups, repeats, same_t, rows = 0, 0, 0, []
    gi = inv[order]
    bounds = np.flatnonzero(np.r_[True, gi[1:] != gi[:-1], True])
    for s, e in zip(bounds[:-1], bounds[1:]):
        if e - s < 2:
            continue
        members = order[s:e]
        groups += 1
        repeats += e - s - 1
        ts = tgt[members]
        same_t += int((ts[1:] == ts[:-1]).sum())
        if len(rows) < 200:
            rows.append(dict(ops=ops[members].tolist()[:6], targets=ts.tolist()[:6]))
    return dict(groups=groups, repeats=repeats, uncompute_pairs=same_t, ops=rows)


# ---------------------------------------------------------------- exact value identity (bit-parallel over all x)
def pack_rows(boolarr):
    W = (len(boolarr) + 63) // 64
    b = np.zeros(W * 64, np.uint8)
    b[:len(boolarr)] = boolarr
    return np.packbits(b, bitorder='little').view('<u8').copy()


class ExactLedger:
    """Bit-parallel run of an invsqrt circuit over every legal input x = 1..3,133,452 at once (one bit per x), in
    the semantics of the tracer: an op runs where every bit on the condition stack and its own `if b` are 1; HMR sets
    its bit to the outcome and clears the qubit. Outcome models: 'zero' or an integer seed (numpy default_rng, one
    draw of W words per executed HMR). Phase is not tracked (the close check covers values; the verifier owns phase).

    For every CCX it hashes the AND it computes (controls, masked by the active rows): two CCX with equal hashes in
    every model compute the same value for every x, so the later one pays for the same product again."""

    def __init__(self, c: Circuit, rows=None):
        self.c = c
        self.rows = np.arange(1, INVSQRT_MAX_X + 1, dtype=np.int64) if rows is None else np.asarray(rows, np.int64)
        self.N = len(self.rows)
        self.valid = pack_rows(np.ones(self.N, bool))
        self.W = len(self.valid)
        self.t = c.types.tolist()
        rq, _, qs = op_major_refs(c, 'q')
        self.rq, self.qs = rq.tolist(), qs.tolist()
        rb, _, bs = op_major_refs(c, 'b')
        self.rb, self.bs = rb.tolist(), bs.tolist()
        self.xq = c.qubit_registers().get('x', [])
        self.nq = max(c.num_qubits, (max(self.rq) + 1) if self.rq else 0)

    def run(self, outcome='zero'):
        t, rq, qs, rb, bs = self.t, self.rq, self.qs, self.rb, self.bs
        W, valid = self.W, self.valid
        q = np.zeros((self.nq, W), np.uint64)
        xin = {}
        for k, qq in enumerate(self.xq):
            q[qq] = pack_rows(((self.rows >> k) & 1).astype(bool))
            xin[qq] = q[qq].copy()
        bits = {}
        rng = None if outcome == 'zero' else np.random.default_rng(int(outcome))
        stack, act = [], None
        hashes, freq = {}, {}
        tof_rows = 0
        N = self.N

        def bit(b):
            v = bits.get(b)
            if v is None:
                v = bits[b] = np.zeros(W, np.uint64)
            return v

        def recompute():
            if not stack:
                return None
            a = stack[0].copy()
            for s in stack[1:]:
                a &= s
            return a
        for i, op in enumerate(t):
            if op == PUSH:
                stack.append(bit(rb[bs[i]]).copy())
                act = recompute()
                continue
            if op == POP:
                if stack:
                    stack.pop()
                act = recompute()
                continue
            base = op & 0x7F
            if op == INFO_TAG or base in (0x07, 0x17, 0x47):
                continue
            a = act
            if op & HAS_COND:
                cb = bit(rb[bs[i + 1] - 1])
                a = cb.copy() if a is None else (a & cb)
            k = qs[i]
            if base == CCX:
                p = q[rq[k]] & q[rq[k + 1]]
                if a is not None:
                    p &= a
                hashes[i] = hashlib.blake2b(p.tobytes(), digest_size=12).digest()
            if (op & 0x30) == 0x30:
                f = N if a is None else int(np.bitwise_count(a).sum()) if hasattr(np, 'bitwise_count') else \
                    int(np.unpackbits(a.view(np.uint8)).sum())
                freq[i] = f / N
                tof_rows += f
            if a is not None and not a.any():
                continue
            if base == CX:
                m = q[rq[k]] if a is None else (q[rq[k]] & a)
                q[rq[k + 1]] ^= m
            elif base == CCX:
                q[rq[k + 2]] ^= p
            elif base == X:
                q[rq[k]] ^= valid if a is None else a
            elif base == CSWAP:
                d = (q[rq[k + 1]] ^ q[rq[k + 2]]) & q[rq[k]]
                if a is not None:
                    d &= a
                q[rq[k + 1]] ^= d
                q[rq[k + 2]] ^= d
            elif base == SWAP:
                d = q[rq[k]] ^ q[rq[k + 1]]
                if a is not None:
                    d &= a
                q[rq[k]] ^= d
                q[rq[k + 1]] ^= d
            elif base == HMR:
                m = np.zeros(W, np.uint64) if rng is None else \
                    rng.integers(0, 1 << 64, W, dtype=np.uint64, endpoint=False) & valid
                b = bit(rb[bs[i]])
                if a is None:
                    b[:] = m
                    q[rq[k]][:] = 0
                else:
                    b[:] = (b & ~a) | (m & a)
                    q[rq[k]] &= ~a
            elif base == R:
                if a is None:
                    q[rq[k]][:] = 0
                else:
                    q[rq[k]] &= ~a
            elif base == BIT_STORE0:
                b = bit(rb[bs[i]])
                b &= ~(valid if a is None else a)
            elif base == BIT_STORE1:
                b = bit(rb[bs[i]])
                b |= valid if a is None else a
            elif base == BIT_INVERT:
                b = bit(rb[bs[i]])
                b ^= valid if a is None else a
        x_ok = all(np.array_equal(q[qq], v) for qq, v in xin.items())
        dirty = [int(i) for i in range(self.nq) if i not in xin and q[i].any()]
        return dict(hashes=hashes, freq=freq, mean_toffoli=tof_rows / N, x_restored=x_ok, dirty=dirty,
                    outcome=str(outcome))


def exact_repeats(runs, W):
    """Groups of CCX whose AND is equal on every x in every outcome model (all-zero products set aside)."""
    zh = hashlib.blake2b(np.zeros(W, np.uint64).tobytes(), digest_size=12).digest()
    groups = {}
    for i in sorted(runs[0]['hashes']):
        groups.setdefault(tuple(r['hashes'][i] for r in runs), []).append(i)
    rnd = [r for r in runs if r['outcome'] != 'zero'] or runs
    freq = {i: sum(r['freq'][i] for r in rnd) / len(rnd) for i in runs[0]['freq']}
    zero_ccx = groups.pop(tuple(zh for _ in runs), [])
    G = sorted((m for m in groups.values() if len(m) > 1), key=lambda m: m[0])
    return dict(groups=G, zero_ccx=zero_ccx, freq=freq,
                mean_toffoli=sum(r['mean_toffoli'] for r in rnd) / len(rnd))


KIND_NAMES = {0: 'contra', 1: 'accrual', 2: 'dead-tail'}
KIND_COLOURS = {0: BLUE, 1: VIOLET, 2: MUTED}


def exchange_rate(c: Circuit, task, args, mean_t):
    """Price of one qubit held for the whole circuit, in T-eq (the cost formula's derivative in Q, in Toffolis)."""
    m = cost_model(c, task, args, mean_t)
    if args.rate:
        return float(args.rate), 'caller (--rate)', m
    if task == 'invsqrt':
        return m['toffoli_equiv'] / m['Q'], '(Toffolis + T/4) / Q: cost = Q × (Toffolis + T/4)', m
    if task == 'dqi':
        return 2 * m['toffoli_used'] / m['Q'], '2 T / Q: d(R Q² T)/dQ in Toffolis', m
    return m['toffoli_used'] / m['Q'], 'T / Q (generic Q × T)', m


def ledger_analyse(c: Circuit, args, mean_t=None):
    """Everything `ledger` reports for one circuit, as plain Python data plus the per-value arrays."""
    task = args.task if args.task != 'auto' else detect_task(c)
    prof = cached_profile(c, args)
    stages, src = stages_of(c, args.stages)
    core = ledger_core(c, prof)
    n = c.n
    exact = None
    if task == 'invsqrt' and not args.no_exact and c.n <= PyTracer.MAX_OPS:
        ex = ExactLedger(c)
        runs = [ex.run(m.strip()) for m in args.models.split(',') if m.strip()]
        exact = exact_repeats(runs, ex.W)
        exact['runs'] = [dict(outcome=r['outcome'], x_restored=r['x_restored'], dirty=r['dirty'],
                              mean_toffoli=round(r['mean_toffoli'], 3)) for r in runs]
        del runs, ex
    if mean_t is None and exact is not None:
        mean_t = exact['mean_toffoli']
    rate, rate_basis, model = exchange_rate(c, task, args, mean_t)
    v, li = core['values'], core['liab']
    inv = ~v['reg'] & ~v['init']
    va, vb, kind, tof = v['a'][inv], v['b'][inv], v['kind'][inv], v['tof'][inv]
    srt_a, srt_b = np.sort(va), np.sort(vb)
    lop, lset = li['op'], li['settle']
    paid = lset >= 0
    srt_lo, srt_ls = np.sort(lop[paid]), np.sort(lset[paid])
    fix = li['fixups']
    win = max(64, n // 100)
    psum = np.zeros(n + 1, np.float64)
    np.cumsum(prof, out=psum[1:])
    cumt = cumulative_toffolis(c, [x for s in stages for x in (s['a'], s['b'])])
    freq = exact['freq'] if exact else None
    fops = np.array(sorted(freq), np.int64) if freq else None
    fval = np.array([freq[i] for i in fops.tolist()]) if freq else None
    rows = []
    for s in stages:
        a, b = s['a'], s['b']
        cut = b
        r = dict(stage=s['name'], ops=b - a, peak=int(prof[a:b].max()) if b > a else 0,
                 live=int(prof[b - 1]) if b > 0 else 0,
                 open=int(np.searchsorted(srt_a, cut) - np.searchsorted(srt_b, cut)),
                 debts=int(np.searchsorted(srt_lo, cut) - np.searchsorted(srt_ls, cut)),
                 opened=int(((va >= a) & (va < b)).sum()))
        inb = (vb >= a) & (vb < b)
        for k, nm in KIND_NAMES.items():
            r[nm] = int((inb & (kind == k)).sum())
        r['accum'] = int(((va >= a) & (va < b) & (tof >= 2)).sum())
        r['hmr'] = int(((lop >= a) & (lop < b)).sum())
        f0, f1 = np.searchsorted(fix, [a, b])
        r['fixups'] = int(f1 - f0)
        if f1 > f0:  # densest window of `win` ops: the largest repayment batch, and how many records it closes
            f = fix[f0:f1]
            cnt = np.searchsorted(f, f + win) - np.arange(len(f))
            k = int(np.argmax(cnt))
            x0 = int(f[k])
            r['batch'] = [int(cnt[k]), x0, int(((lset >= x0) & (lset < x0 + win)).sum())]
        else:
            r['batch'] = [0, a, 0]
        r['toffoli'] = int(cumt[b][0] - cumt[a][0])
        if freq:
            i0, i1 = np.searchsorted(fops, [a, b])
            r['toffoli_mean'] = round(float(fval[i0:i1].sum()), 2)
        r['capital'] = round(float(psum[b] - psum[a]) / n * rate, 1)
        rows.append(r)
    names = [s['name'] for s in stages]
    starts = np.array([s['a'] for s in stages])

    def stage_of(x):
        return names[max(0, int(np.searchsorted(starts, x, side='right')) - 1)]
    peak = peak_report(core, stage_of)
    # idle at the peak: values ranked by ops held idle at full width (width is a maximum, so only idleness at the
    # peak can lower it), then by longest idle stretch
    order = peak_order(np.flatnonzero(inv), v['idle_peak'], v['idle_max'], v['a'])[:args.top]
    aged = []
    for i in order.tolist():
        span = int(v['b'][i] - v['a'][i] + 1)
        g = core['gap_at'].get(i)
        aged.append(dict(wire=f"q{int(v['q'][i])}", open=int(v['a'][i]), close=int(v['b'][i]), span=span,
                         idle_at_peak=int(v['idle_peak'][i]), runs_covered=int(peak['per_value'][i]),
                         idle=int(v['idle_max'][i]), idle_from=g[0] if g else None, idle_to=g[1] if g else None,
                         peak_gap_from=g[2] if g else None, peak_gap_to=g[3] if g else None,
                         settle=KIND_NAMES[int(v['kind'][i])], toffoli_entries=int(v['tof'][i]),
                         idle_capital_teq=round(int(v['idle_max'][i]) / n * rate, 1),
                         stages=f'{stage_of(int(v["a"][i]))}→{stage_of(int(v["b"][i]))}'))
    # aged debts: HMR records repaid latest
    age = np.where(paid, lset - lop, -1)
    dorder = np.lexsort((lop, -age))[:args.top]
    debts = [dict(wire=f"q{int(li['qubit'][i])}", hmr=int(lop[i]), repaid=int(lset[i]), age=int(age[i]),
                  fixups=int(li['reads'][i]), stages=f'{stage_of(int(lop[i]))}→{stage_of(int(lset[i]))}')
             for i in dorder.tolist() if age[i] > 0]
    carried = {}
    for x, y in zip(lop[paid].tolist(), lset[paid].tolist()):
        sx, sy = stage_of(x), stage_of(y)
        if sx != sy:
            carried[f'{sx}→{sy}'] = carried.get(f'{sx}→{sy}', 0) + 1
    # paying twice
    rep = core['repeats']
    pay = dict(static=dict(tier='static', groups=rep['groups'], recomputed=int(rep['repeats']),
                           uncompute_pairs=rep['uncompute_pairs'],
                           basis='CCX whose controls hold the same wire versions as an earlier CCX'))
    pay_rows = []
    if exact:
        G = exact['groups']
        fq = exact['freq']
        t_twice = sum(fq.get(i, 1.0) for g in G for i in g[1:])
        pay['exact'] = dict(tier='exact-all-x', groups=len(G), recomputed=sum(len(g) - 1 for g in G),
                            toffoli_per_shot=round(t_twice, 2), zero_products=len(exact['zero_ccx']),
                            models=[r['outcome'] for r in exact['runs']],
                            basis='CCX whose AND is equal on every legal x in every outcome model')
        for g in G:
            pay_rows.append(dict(ops=g, first=g[0], last=g[-1], recomputed=len(g) - 1,
                                 toffoli_per_shot=round(sum(fq.get(i, 1.0) for i in g[1:]), 4)))
    else:
        for g in rep['ops']:
            pay_rows.append(dict(ops=g['ops'], first=g['ops'][0], last=g['ops'][-1], recomputed=len(g['ops']) - 1,
                                 targets=g['targets']))
    # close check against the contract
    close = dict(tier='static', unpaid_records=int((~paid).sum()), open_debts=0,
                 contra_unverified=int(((kind == 0)).sum()))
    if exact:
        lines = sorted({w for r in exact['runs'] for w in r['dirty']})
        close.update(tier='exact-all-x', x_restored=all(r['x_restored'] for r in exact['runs']),
                     nonzero_lines=[f'q{w}' for w in lines][:20], models=[r['outcome'] for r in exact['runs']])
        close['balanced'] = close['x_restored'] and not lines
    width = int(prof.max())
    used = float(psum[-1]) / n
    summary = dict(task=task, Q=c.num_qubits, width=width, rate=round(rate, 2), rate_basis=rate_basis,
                   values={nm: int((kind == k).sum()) for k, nm in KIND_NAMES.items()},
                   liabilities=int(len(lop)), fixups=int(len(fix)), registers=core['regs'],
                   capital_charged=round(width * rate, 1), capital_used=round(used * rate, 1),
                   utilisation=round(used / max(1, width), 4), model=model)
    return dict(c=c, task=task, prof=prof, stages=stages, stage_src=src, core=core, inv=inv, exact=exact,
                rate=rate, rows=rows, aged=aged, debts=debts, carried=carried, pay=pay, pay_rows=pay_rows,
                close=close, summary=summary, stage_of=stage_of, mean_t=mean_t, peak=peak)


RUN_ROWS, RUN_COVERERS = 5000, 8  # JSON caps: runs listed, covering values named per run


def peak_report(core, stage_of, budget=200_000):
    """Full-width runs, which candidate values are idle across each, and the fewest releases covering every run
    that can be covered. A run nobody is idle across cannot be narrowed by any release, so Q cannot fall by
    releases alone while one exists."""
    v, runs, top = core['values'], core['runs'], core['top']
    ra, rb = runs['a'], runs['b']
    nv = len(v['a'])
    through, pv, pr, per_value = run_cover(runs, nv)
    nr = len(ra)
    ln = rb - ra + 1
    unc = np.flatnonzero(through == 0)
    wire = (lambda i: f"q{int(v['q'][i])}@{int(v['a'][i])}")
    rank_ix = peak_order(np.unique(pv), v['idle_peak'], v['idle_max'], v['a']).tolist()
    rank = {i: r for r, i in enumerate(rank_ix)}
    sets = {}
    ov = np.argsort(pv, kind='stable')
    spv, spr = pv[ov], pr[ov]
    bnd = np.flatnonzero(np.r_[True, spv[1:] != spv[:-1], True]) if len(spv) else np.zeros(1, np.int64)
    for s, e in zip(bnd[:-1].tolist(), bnd[1:].tolist()):
        sets[int(spv[s])] = spr[s:e].tolist()
    hs = min_hitting_set(sets, rank, budget)
    by_stage = {}
    for k in range(nr):
        st = by_stage.setdefault(stage_of(int(ra[k])), [0, 0, 0])
        st[0] += 1
        st[1] += int(through[k] == 0)
        st[2] += int(ln[k]) if through[k] == 0 else 0
    rb_ = np.flatnonzero(np.r_[True, pr[1:] != pr[:-1], True]) if len(pr) else np.zeros(1, np.int64)
    who = {int(pr[s]): pv[s:e] for s, e in zip(rb_[:-1].tolist(), rb_[1:].tolist())}
    rows = []
    for k in range(min(nr, RUN_ROWS)):
        w = who.get(k, np.zeros(0, np.int64))
        w = sorted(w.tolist(), key=lambda i: rank[i])[:RUN_COVERERS]
        rows.append(dict(run=k, start=int(ra[k]), end=int(rb[k]), ops=int(ln[k]), stage=stage_of(int(ra[k])),
                         idle_values=int(through[k]), covered_by=[wire(i) for i in w]))
    if not nr:
        verdict = 'no full-width runs'
    elif len(unc):
        verdict = (f'Q cannot fall by releases alone: {len(unc)} of {nr} full-width runs have no idle value '
                   f'({int(ln[unc].sum()):,} ops); narrowing them needs a different computation, not a release')
    else:
        verdict = (f'every full-width run has an idle value; {hs["size"]} release(s) cover all {nr} '
                   f'({"exact" if hs["exact"] else "greedy upper bound"}): necessary for Q - 1, not sufficient')
    releases = [dict(value=wire(i), wire=f"q{int(v['q'][i])}", open=int(v['a'][i]), close=int(v['b'][i]),
                     runs_covered=int(per_value[i]), idle_at_peak=int(v['idle_peak'][i])) for i in hs['chosen']]
    return dict(width=top, runs=nr, ops_at_full=int(ln.sum()), coverable=int(nr - len(unc)),
                uncoverable=int(len(unc)), uncoverable_ops=int(ln[unc].sum()), verdict=verdict,
                by_stage={k: dict(runs=a, uncoverable=b, uncoverable_ops=c) for k, (a, b, c) in by_stage.items()},
                uncoverable_runs=[[int(ra[k]), int(rb[k]), int(ln[k]), stage_of(int(ra[k]))] for k in unc.tolist()],
                hitting_set=dict(size=hs['size'], exact=hs['exact'], lower_bound=hs['lower_bound'],
                                 method=hs['method'], runs_to_cover=hs['elements'], candidates=hs['candidates'],
                                 kernel=dict(runs=hs['kernel'][0], candidates=hs['kernel'][1]),
                                 releases=releases, runs_left_uncovered=int(len(unc))),
                run_rows=rows, run_rows_total=nr, per_value=per_value)


def write_ledger_csv(A, path):
    v, inv, n, rate = A['core']['values'], A['inv'], A['c'].n, A['rate']
    so = A['stage_of']
    pv = A['peak']['per_value']
    cols = ['wire', 'open_op', 'close_op', 'span', 'touches', 'toffoli_entries', 'data_entries', 'phase_entries',
            'control_entries', 'settlement', 'debt_repaid_at', 'idle_max', 'idle_at_full_width', 'full_width_runs_idle', 'capital_teq',
            'open_stage', 'close_stage']
    idx = np.flatnonzero(inv)
    idx = idx[np.lexsort((v['q'][idx], v['a'][idx]))]
    with open(path, 'w') as f:
        f.write(','.join(cols) + '\n')
        for i in idx.tolist():
            a, b = int(v['a'][i]), int(v['b'][i])
            f.write(f"q{int(v['q'][i])},{a},{b},{b - a + 1},{int(v['touches'][i])},{int(v['tof'][i])},"
                    f"{int(v['data'][i])},{int(v['phase'][i])},{int(v['ctrl'][i])},{KIND_NAMES[int(v['kind'][i])]},"
                    f"{int(v['liab_end'][i]) if v['liab_end'][i] >= 0 else ''},{int(v['idle_max'][i])},"
                    f"{int(v['idle_peak'][i])},{int(pv[i])},{(b - a + 1) / n * rate:.3f},{so(a)},{so(b)}\n")
    return path


def fmt_ops(x):
    x = int(x)
    if abs(x) >= 1_000_000:
        return f'{x / 1e6:.1f}M'
    if abs(x) >= 10_000:
        return f'{x / 1e3:.0f}k'
    return f'{x:,}'


def ledger_facts(A):
    s = A['summary']
    f = dict(task=s['task'], width=s['width'], Q=s['Q'], rate_teq_per_qubit=s['rate'], values=s['values'],
             liabilities=s['liabilities'], fixups=s['fixups'], utilisation=s['utilisation'])
    if A['aged']:
        a = A['aged'][0]
        f['idle_at_peak_top'] = f"{a['wire']} {a['idle_at_peak']:,} ops idle at peak, {a['runs_covered']:,} runs"
    pk = A['peak']
    hs = pk['hitting_set']
    f['full_width_runs'] = (f"{pk['runs']:,} runs, {pk['uncoverable']:,} with no idle value; {hs['size']} "
                            f"release(s) cover the rest ({'exact' if hs['exact'] else 'greedy'})")
    f['peak_verdict'] = ('Q cannot fall by releases alone' if pk['uncoverable'] else
                         'all runs coverable (necessary, not sufficient)') if pk['runs'] else 'no full-width runs'
    if A['carried']:
        k, m = max(A['carried'].items(), key=lambda kv: kv[1])
        f['debts_carried'] = f'{sum(A["carried"].values())} across stages; most {k} ({m})'
    p = A['pay'].get('exact') or A['pay']['static']
    f['paying_twice'] = {k: p[k] for k in ('tier', 'groups', 'recomputed', 'toffoli_per_shot') if k in p}
    cl = A['close']
    f['close'] = ({'tier': cl['tier'], 'balanced': cl['balanced'], 'lines': len(cl['nonzero_lines'])}
                  if 'balanced' in cl else {'tier': 'static', 'unpaid_records': cl['unpaid_records'],
                                            'contra_unverified': cl['contra_unverified']})
    return f


def compact_row(r):
    keep = ('stage', 'live', 'open', 'debts', 'accrual', 'fixups', 'toffoli')
    return {k: r[k] for k in keep if k in r}


def cmd_ledger(args):
    if args.compare:
        return cmd_reconcile(args)
    if len(args.circuits) != 1:
        raise UserError('ledger takes one circuit, or two with --compare',
                        'python render.py ledger A.kmb, or python render.py ledger --compare A.kmb B.kmb')
    cs = load_circuits(args.circuits, maxn=1)
    c = cs[0]
    labs = labels_for(args, cs)
    mean_t = float(args.mean_toffolis) if args.mean_toffolis else None
    A = ledger_analyse(c, args, mean_t)
    base = out_base(args, cs, 'ledger')
    files = []
    if not args.no_png:
        files.append(file_entry(draw_ledger_png(A, labs[0], args, base), kind='png'))
    if not args.no_csv:
        files.append(file_entry(write_ledger_csv(A, str(base) + '.csv'), kind='csv'))
    facts = ledger_facts(A)
    payload = dict(verb='ledger', circuits=[circuit_entry(c)], summary=A['summary'], facts=facts,
                   trial_balance=A['rows'], aged_values=A['aged'], aged_debts=A['debts'],
                   full_width_runs={k: x for k, x in A['peak'].items() if k != 'per_value'},
                   debts_carried=A['carried'], paying_twice=A['pay'], paying_twice_groups=A['pay_rows'],
                   close=A['close'], accounts=A['core']['accounts'],
                   account_totals={nm: dict(zip(ENTRY_CLASSES, A['core']['taccounts'][k].sum(0).tolist()))
                                   for k, nm in enumerate(A['core']['accounts'])},
                   stages=A['stage_src'], definitions=LEDGER_DEFS)
    table = write_table(base, payload)
    tier = 'exact-all-x' if A['exact'] else 'static'
    basis = ('values, debts, entries: static (op stream)'
             + ('; paying twice and close: exact over all 3,133,452 x, models '
                + ','.join(r['outcome'] for r in A['exact']['runs']) if A['exact'] else '')
             + f"; capital {A['rate']:.1f} T-eq/qubit = {A['summary']['rate_basis'].split(':')[0]}")
    nxt = [f'python render.py ledger --compare {Path(c.path).name} OTHER.kmb']

    return dict(ok=True, verb='ledger', tier=tier, basis=basis, files=files + [file_entry(table, kind='json')],
                circuits=[circuit_entry(c)], facts=facts, rows=[compact_row(r) for r in A['rows']], next=nxt)


LEDGER_DEFS = {
    'account': 'a wire; register wires grouped by register (numbered families folded: S0..S69 -> S*)',
    'entry': 'an op touching the account: toffoli (CCX/CSWAP target), data (CX/X/SWAP target), phase (Z/CZ/CCZ/'
             'Z_POW), control, settle (R/HMR)',
    'value': 'one lifetime of a non-register wire (the width verb\'s interval): opened at its first touch after a '
             'reset, closed at an unconditional R/HMR or at its last touch',
    'contra': 'value closed at its last touch: erased by unitary uncompute (presumed statically; checked exactly '
              'for invsqrt)',
    'accrual': 'value closed by HMR whose record later conditions fix-ups: a phase liability until the last one',
    'dead-tail': 'value closed by R, or by HMR whose record nothing reads',
    'liability': 'one HMR record, open from the HMR to the last op it (or a bit derived from it) conditions',
    'trial balance': 'at each stage cut: values open across the cut, liabilities open across it, live width',
    'capital': 'qubit-ops × rate / ops: T-eq of width used; charged = certified width × rate',
    'idle at peak': 'ops at full width strictly inside a value\'s idle gaps (no entry on the wire); the aged list is '
                    'ranked by it, then by the longest idle gap (idle)',
    'idle capital': 'idle_capital_teq = longest idle gap × rate / ops. Additive bookkeeping, NOT a saving: width is a '
                    'maximum, so Q falls only if every full-width run is covered by a release',
    'full-width run': 'a maximal op range at the certified width; a value covers it when one of its idle gaps holds '
                      'the whole run (static)',
    'uncoverable run': 'a full-width run no scratch value is idle across: no release can narrow it, so Q cannot fall '
                       'by releases alone',
    'hitting set': 'fewest release candidates (values) covering every coverable full-width run, unit cost each; '
                   'necessary for Q - 1, not sufficient (the release and any recompute must fit below the peak)',
    'paying twice': 'a CCX computing the same AND as an earlier one (exact: equal on every x; static: same wire '
                    'versions)',
}


# ---------------------------------------------------------------- reconciliation (two circuits)
def value_signatures(A, a, b):
    v, inv = A['core']['values'], A['inv']
    m = inv & (v['a'] >= a) & (v['a'] < b)
    sig = np.stack([v['kind'][m].astype(np.int64), v['touches'][m], v['tof'][m], v['data'][m], v['phase'][m],
                    v['ctrl'][m], v['b'][m] - v['a'][m]], 1)
    from collections import Counter
    return Counter(map(tuple, sig.tolist())), v['a'][m], v['tof'][m], v['b'][m]


def cmd_reconcile(args):
    paths = list(args.circuits or [])
    if len(paths) != 2:
        raise UserError('--compare takes two circuits', 'python render.py ledger --compare A.kmb B.kmb')
    means = [None, None]
    if args.mean_toffolis:
        vals = [float(x) for x in args.mean_toffolis.split(',')]
        if len(vals) != 2:
            raise UserError('--mean-toffolis needs one value per circuit', 'e.g. --mean-toffolis 430413,430837')
        means = vals
    keep = []
    for k, p in enumerate(paths):  # one circuit at a time: DQI ledgers need several GB each
        c = read_kmb(p)
        A = ledger_analyse(c, args, means[k])
        st = A['stages']
        sigs = [value_signatures(A, s['a'], s['b']) for s in st]
        lsig = []
        li = A['core']['liab']
        for s in st:
            m = (li['op'] >= s['a']) & (li['op'] < s['b'])
            lsig.append(int(m.sum()))
        keep.append(dict(c=c, rows=A['rows'], stages=st, sigs=sigs, summary=A['summary'], rate=A['rate'],
                         model=A['summary']['model'], pay=A['pay'], close=A['close'], aged=A['aged'][:3],
                         peak={k: x for k, x in A['peak'].items() if k not in ('per_value', 'run_rows')},
                         prof_max=int(A['prof'].max()), exact=bool(A['exact']), stage_src=A['stage_src']))
        del A
    cs = [k['c'] for k in keep]
    labs = labels_for(args, cs)
    KA, KB = keep
    rate = KA['rate']
    pairs = align_stages(KA['stages'], KB['stages'])
    rows = []
    for i, j in pairs:
        ra, rb = KA['rows'][i], KB['rows'][j]
        ca, pa, ta, ea = KA['sigs'][i]
        cb, pb, tb, eb = KB['sigs'][j]
        matched = sum((ca & cb).values())
        r = dict(stage=ra['stage'], matched=matched, a_only=sum(ca.values()) - matched,
                 b_only=sum(cb.values()) - matched, d_peak=rb['peak'] - ra['peak'],
                 d_toffoli=rb['toffoli'] - ra['toffoli'], d_hmr=rb['hmr'] - ra['hmr'],
                 d_accrual=rb['accrual'] - ra['accrual'], d_fixups=rb['fixups'] - ra['fixups'],
                 d_accum=rb['accum'] - ra['accum'])
        if 'toffoli_mean' in ra and 'toffoli_mean' in rb:
            r['d_toffoli_mean'] = round(rb['toffoli_mean'] - ra['toffoli_mean'], 2)
        # accumulating values (>= 2 Toffoli entries) that B opens before A opens its first one in this stage
        sa, sb = KA['stages'][i], KB['stages'][j]
        acc_a = np.sort(pa[(ta >= 2) & (ea >= sa['b'])] - sa['a'])  # accumulating and still open at the cut
        acc_b = np.sort(pb[(tb >= 2) & (eb >= sb['b'])] - sb['a'])
        if len(acc_a) and len(acc_b):
            r['b_accum_before_a'] = int((acc_b < acc_a[0]).sum())
            r['a_first_accum'] = int(acc_a[0] + sa['a'])
            r['b_accum_span'] = [int(acc_b[0] + sb['a']), int(acc_b[min(len(acc_b) - 1, max(0, r['b_accum_before_a'] - 1))] + sb['a'])]
        r['batch_a'], r['batch_b'] = ra['batch'], rb['batch']
        rows.append(r)
    ma, mb = KA['model'], KB['model']
    exp_key = 'toffoli_equiv' if ma['task'] == 'invsqrt' else 'toffoli_used'
    d_q = KB['prof_max'] - KA['prof_max']
    d_exp = mb[exp_key] - ma[exp_key]
    d_hmr = KB['summary']['liabilities'] - KA['summary']['liabilities']
    d_fix = KB['summary']['fixups'] - KA['summary']['fixups']
    capital = d_q * rate
    net = capital + d_exp
    if d_q < 0 and d_hmr > 0 and d_exp > 0:
        mech = (f'accrual: B releases values by HMR ({d_hmr:+,} records, repaid by {d_fix:+,} fix-ups) and pays '
                f'{d_exp:+,.1f} T-eq to recompute, to hold {-d_q} wire(s) less')
    elif d_q < 0 and d_exp > 0:
        big = max(rows, key=lambda r: r.get('b_accum_before_a', 0)) if rows else None
        mech = f're-packing: {-d_q} wire(s) less for {d_exp:+,.1f} T-eq'
        if big and big.get('b_accum_before_a'):
            mech += (f"; in {big['stage']} B opens {big['b_accum_before_a']} accumulating values (>= 2 Toffoli "
                     f"entries, open at the cut) before A opens its first (op {big['a_first_accum']:,})")
        bb = max(rows, key=lambda r: r['batch_b'][0] - r['batch_a'][0]) if rows else None
        win_b = max(64, KB['c'].n // 100)
        if bb and bb['batch_b'][0] > 2 * max(1, bb['batch_a'][0]):
            mech += (f"; B repays {bb['batch_b'][2]} records with {bb['batch_b'][0]} fix-ups in one {win_b}-op "
                     f"window at op {bb['batch_b'][1]:,} (A at most {bb['batch_a'][0]})")
    elif d_q == 0 and d_exp < 0:
        mech = f'expense cut: same width, {d_exp:+,.1f} T-eq'
    elif d_q > 0 and d_exp < 0:
        mech = f'investment: {d_q:+} wire(s) to save {-d_exp:,.1f} T-eq'
    else:
        mech = 'mixed'
    facts = dict(width=[KA['prof_max'], KB['prof_max']], rate_teq_per_qubit=round(rate, 2),
                 capital_teq=round(capital, 1), expense_teq=round(d_exp, 2), net_teq=round(net, 1),
                 cost_b_over_a=round(mb['cost'] / ma['cost'], 6), d_liabilities=d_hmr, d_fixups=d_fix,
                 values_matched=sum(r['matched'] for r in rows), values_b_only=sum(r['b_only'] for r in rows),
                 values_a_only=sum(r['a_only'] for r in rows), reading=mech, expense_basis=exp_key,
                 uncoverable_runs=[KA['peak']['uncoverable'], KB['peak']['uncoverable']],
                 releases_to_cover=[KA['peak']['hitting_set']['size'], KB['peak']['hitting_set']['size']])
    if KA['pay'].get('exact') and KB['pay'].get('exact'):
        facts['paying_twice'] = [KA['pay']['exact']['recomputed'], KB['pay']['exact']['recomputed']]
    base = out_base(args, cs, 'ledger')
    files = []
    if not args.no_png:
        files.append(file_entry(draw_reconcile_png(keep, labs, rows, facts, args, base), kind='png'))
    payload = dict(verb='ledger', mode='compare', circuits=[circuit_entry(c) for c in cs], facts=facts,
                   reconciliation=rows, trial_balance=[KA['rows'], KB['rows']],
                   summaries=[KA['summary'], KB['summary']], paying_twice=[KA['pay'], KB['pay']],
                   close=[KA['close'], KB['close']], aged_top=[KA['aged'], KB['aged']],
                   full_width_runs=[KA['peak'], KB['peak']], definitions=LEDGER_DEFS)
    table = write_table(base, payload)
    tier = 'exact-all-x' if KA['exact'] and KB['exact'] else 'static'
    short = [{k: r[k] for k in ('stage', 'matched', 'a_only', 'b_only', 'd_peak', 'd_toffoli', 'd_hmr', 'd_fixups')
              if k in r} for r in rows if r['a_only'] or r['b_only'] or r['d_toffoli'] or r['d_peak']]
    return dict(ok=True, verb='ledger', tier=tier,
                basis=f'reconciliation of two static ledgers by stage (values matched by signature: settlement, '
                      f'entries, span); capital priced at A\'s rate {rate:.1f} T-eq per qubit; expense = {exp_key}'
                      + (' (exact mean over all x)' if tier != 'static' else ' (static count unless --mean-toffolis)'),
                files=files + [file_entry(table, kind='json')], circuits=[circuit_entry(c) for c in cs],
                facts=facts, rows=short,
                next=[f'python render.py diff {cs[0].path} {cs[1].path}', 'python render.py ledger ' + cs[1].path])


# ---------------------------------------------------------------- figures
def draw_taccounts(ax, A, lo, hi, nbins_show=120):
    tacc = A['core']['taccounts']
    names = A['core']['accounts']
    nacc, nb, _ = tacc.shape
    if nb > nbins_show:  # merge bins
        f = nb // nbins_show
        tacc = tacc[:, :f * nbins_show].reshape(nacc, nbins_show, f, 5).sum(2)
        nb = nbins_show
    tot = tacc.sum((1, 2))
    show = [k for k in range(nacc) if tot[k] > 0]
    if len(show) > 44:
        show = sorted(sorted(show, key=lambda k: -tot[k])[:44])
    n = A['c'].n
    x = (np.arange(nb) + 0.5) * n / nb
    wbar = n / nb * 0.9
    sc, unit = ops_axis(n)
    for row, k in enumerate(show):
        y0 = len(show) - 1 - row
        acc = tacc[k]
        deb = acc[:, E_TOF] + acc[:, E_DATA]
        cred = acc[:, E_PHASE] + acc[:, E_SETTLE]
        m = max(1, int(np.max(deb)), int(np.max(cred)))
        h = 0.45 / m
        ax.bar(x / sc, acc[:, E_DATA] * h, bottom=y0, width=wbar / sc, color=BLUE, lw=0)
        ax.bar(x / sc, acc[:, E_TOF] * h, bottom=y0 + acc[:, E_DATA] * h, width=wbar / sc, color=ORANGE, lw=0)
        ax.bar(x / sc, -acc[:, E_PHASE] * h, bottom=y0, width=wbar / sc, color=VIOLET, lw=0)
        ax.bar(x / sc, -acc[:, E_SETTLE] * h, bottom=y0 - acc[:, E_PHASE] * h, width=wbar / sc, color=RED, lw=0)
        ax.axhline(y0, color='#bdbcb6', lw=0.5, zorder=0)
    ax.set_yticks(range(len(show)), [names[k][:14] for k in show[::-1]],
                  fontsize=7 if len(show) > 24 else 8.5)
    ax.set_ylim(-0.6, len(show) - 0.4)
    ax.set_xlim(0, n / sc)
    ax.grid(axis='y', visible=False)
    ax.set_xlabel(f'position ({unit})')
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=BLUE, label='debit: data entry'), Patch(color=ORANGE, label='debit: Toffoli entry'),
                       Patch(color=VIOLET, label='credit: phase entry'), Patch(color=RED, label='credit: R/HMR')],
              loc='lower left', bbox_to_anchor=(0, 1.0), ncol=4, fontsize=8)


def draw_ledger_png(A, lab, args, base):
    plt = plt_()
    n = A['c'].n
    nacc = min(44, len(A['core']['accounts']))
    W = args.px / DPI
    k = W / 10.0  # panel heights below are for a 1000-px-wide figure
    H = [max(2.4, 0.15 * nacc + 0.7) * k, 2.9 * k, max(2.4, 0.24 * min(12, len(A['aged'])) + 1.4) * k, 2.7 * k]
    gap = 1.05 * k
    height = sum(H) + gap * len(H) + 0.3 * k
    fig = plt.figure(figsize=(W, height), dpi=DPI)
    gs = fig.add_gridspec(4, 2, height_ratios=H, hspace=gap / (sum(H) / len(H)), wspace=0.42, left=0.1,
                          right=0.93, top=1 - (0.75 * k + 0.35 * gap) / height, bottom=0.55 * k / height)
    s = A['summary']
    fig.suptitle(f"Ledger of {lab}: {s['task']}, width {s['width']} (Q = {s['Q']}), capital at {s['rate']:.1f} T-eq "
                 f"per qubit", x=0.02, y=1 - 0.12 * k / height, ha='left', va='top', fontsize=12.5,
                 fontweight='bold')
    sc, unit = ops_axis(n)
    ax = fig.add_subplot(gs[0, :])
    draw_taccounts(ax, A, 0, n)
    ax.set_title('T-accounts: entries per account over the circuit (each row scaled to its own maximum)',
                 fontsize=11, pad=24)
    # trial balance at stage cuts
    ax = fig.add_subplot(gs[1, 0])
    rows = A['rows']
    xs = np.arange(len(rows))
    ax.bar(xs - 0.2, [r['open'] for r in rows], 0.4, color=BLUE, label='values open')
    ax.bar(xs + 0.2, [r['debts'] for r in rows], 0.4, color=VIOLET, label='phase debts open')
    ax.set_xticks(xs, [r['stage'][:10] for r in rows], rotation=35, ha='right', fontsize=8)
    ax.set_ylabel('open at the cut')
    ax.set_title('Trial balance at each stage cut', fontsize=11)
    ax2 = ax.twinx()
    ax2.plot(xs, [r['live'] for r in rows], 'o', color=ORANGE, ms=5, label='live width')
    ax2.set_ylabel('live qubits', color=ORANGE)
    ax2.grid(False)
    ax2.spines['right'].set_visible(True)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=7.5, loc='upper left')
    # settlements by kind per stage
    ax = fig.add_subplot(gs[1, 1])
    bottom = np.zeros(len(rows))
    for k, nm in KIND_NAMES.items():
        vals = np.array([r[nm] for r in rows], float)
        ax.bar(xs, vals, 0.6, bottom=bottom, color=KIND_COLOURS[k], label=nm)
        bottom += vals
    ax.set_xticks(xs, [r['stage'][:10] for r in rows], rotation=35, ha='right', fontsize=8)
    ax.set_title('How values are settled, per stage', fontsize=11)
    ax.set_ylabel('values closed')
    ax.legend(fontsize=7.5)
    # aged liabilities
    ax = fig.add_subplot(gs[2, :])
    aged = A['aged'][:12]
    for k, a in enumerate(aged):
        y = len(aged) - 1 - k
        ax.plot([a['open'] / sc, a['close'] / sc], [y, y], color=KIND_COLOURS[{'contra': 0, 'accrual': 1}.get(
            a['settle'], 2)], lw=2.2, solid_capstyle='butt')
        if a['peak_gap_from'] is not None:
            ax.plot([a['peak_gap_from'] / sc, a['peak_gap_to'] / sc], [y, y], color=INK, lw=5.5,
                    solid_capstyle='butt')
        ax.text(a['close'] / sc, y, f"  {fmt_ops(a['idle_at_peak'])} idle at peak, {a['runs_covered']:,} runs",
                va='center', fontsize=7.5, color=INK2, clip_on=False)
    for k, d in enumerate(A['debts'][:3]):
        y = -1 - k * 0.6
        ax.plot([d['hmr'] / sc, d['repaid'] / sc], [y, y], color=VIOLET, lw=1.3, ls=(0, (3, 2)))
    ax.set_yticks(list(range(len(aged))) + [-1 - k * 0.6 for k in range(min(3, len(A['debts'])))],
                  [a['wire'] for a in aged][::-1] + [f"debt {d['wire']}" for d in A['debts'][:3]], fontsize=8)
    draw_bands(ax, A['stages'], 0, n, sc, len(aged) - 0.2, None)
    pk = A['peak']
    y_lo = -1 - 0.6 * min(3, len(A['debts'])) - 0.2
    for r in pk['uncoverable_runs']:  # full-width runs nobody is idle across: no release can narrow them
        ax.axvspan(r[0] / sc, (r[1] + 1) / sc, color=RED, alpha=0.35, lw=0)
        ax.plot([(r[0] + r[1]) / 2 / sc], [y_lo + 0.05], marker='^', color=RED, ms=4, clip_on=False)
    ax.set_xlim(0, n / sc)
    ax.set_ylim(y_lo, len(aged) + 0.6)
    ax.grid(axis='y', visible=False)
    ax.set_xlabel(f'position ({unit}); thick black = idle gap holding most full-width ops; red = full-width run with '
                  f'no idle value; dashed = longest-open phase debts', fontsize=8.5)
    hs = pk['hitting_set']
    ax.set_title(f"Idle at peak: values ranked by ops idle at full width. {pk['runs']:,} full-width runs, "
                 f"{pk['uncoverable']:,} with no idle value; {hs['size']} release(s) cover the rest "
                 f"({'exact' if hs['exact'] else 'greedy'})", fontsize=10)
    # paying twice
    ax = fig.add_subplot(gs[3, 0])
    pr = A['pay_rows']
    pt = A['pay'].get('exact') or A['pay']['static']
    if pr:
        xs_, ys_, ss_ = [], [], []
        for g in pr:
            for o in g['ops'][1:]:
                xs_.append(o / sc)
                ys_.append((o - g['first']) / sc)
                ss_.append(10 + 30 * min(1.0, g.get('toffoli_per_shot', 1.0) / max(1, g['recomputed'])))
        ax.scatter(xs_, ys_, s=ss_, color=ORANGE, alpha=0.7, lw=0)
        ax.set_xlim(0, n / sc)
        ax.set_ylabel(f'gap to first computation ({unit})', fontsize=8.5)
    else:
        ax.text(0.5, 0.5, 'no recomputed AND found', ha='center', va='center', transform=ax.transAxes, color=INK2)
    ax.set_xlabel(f'recomputation position ({unit})', fontsize=8.5)
    extra = f", {pt['toffoli_per_shot']:,.1f} Toffolis per shot" if 'toffoli_per_shot' in pt else ''
    ax.set_title(f"Paying twice: {pt['recomputed']:,} ANDs recomputed in {pt['groups']:,} groups{extra} "
                 f"[{pt['tier']}]", fontsize=10)
    # close check
    ax = fig.add_subplot(gs[3, 1])
    ax.axis('off')
    cl = A['close']
    lines = [('Closing balance against the contract', True)]
    if 'balanced' in cl:
        lines += [(f"inputs restored on every x: {'yes' if cl['x_restored'] else 'NO'}", False),
                  (f"scratch lines nonzero at close: {len(cl['nonzero_lines'])}"
                   + (f" ({', '.join(cl['nonzero_lines'][:6])})" if cl['nonzero_lines'] else ''), False),
                  (f"outcome models: {', '.join(cl['models'])} [exact-all-x]", False),
                  ('phase within 2^-24: not checked here (official verifier)', False),
                  (f"verdict: {'books close' if cl['balanced'] else 'books do NOT close'}", True)]
    else:
        lines += [(f"HMR records never repaid (dead tails): {cl['unpaid_records']:,}", False),
                  (f"values closed by unitary uncompute: {cl['contra_unverified']:,} (unverified statically)", False),
                  ('phase debts open at close: 0 (all repaid inside the circuit)', False),
                  ('[static] verify values with: render.py trace, or the official verifier', False)]
    lines += [(f"capital charged {s['capital_charged']:,.0f} T-eq (width × rate), used {s['capital_used']:,.0f} "
               f"({s['utilisation']:.0%})", False),
              (('Q cannot fall by releases alone' if pk['uncoverable'] else
                'every full-width run has an idle value') + f" ({pk['uncoverable']} uncoverable runs)", True)]
    for k, (txt, bold) in enumerate(lines):
        ax.text(0, 1 - k * 0.15, txt, transform=ax.transAxes, fontsize=9.5 if bold else 8.5,
                fontweight='bold' if bold else 'normal', color=INK if bold else INK2, va='top')
    return save_png(fig, str(base) + '.png', fit=True)


def draw_reconcile_png(keep, labs, rows, facts, args, base):
    plt = plt_()
    W = args.px / DPI
    fig = plt.figure(figsize=(W, W * 0.78), dpi=DPI)
    gs = fig.add_gridspec(2, 3, height_ratios=[1.0, 1.5], hspace=0.55, wspace=0.35)
    import textwrap
    head = f"Reconciliation {labs[0]} → {labs[1]}\n" + textwrap.fill(facts['reading'], max(60, int(args.px / 8.8)))
    fig.suptitle(head, x=0.02, y=1 - 0.12 / (W * 0.78), ha='left', va='top', fontsize=10, fontweight='bold')
    ax = fig.add_subplot(gs[0, :2])
    items = [('capital\n(width × rate)', facts['capital_teq']), (f"expense\n({facts['expense_basis']})",
                                                                 facts['expense_teq'])]
    cum = 0.0
    for k, (nm, val) in enumerate(items):
        ax.bar(k, abs(val), bottom=min(cum, cum + val), color=ORANGE if val > 0 else AQUA, width=0.6)
        ax.text(k, max(cum, cum + val), f'{val:+,.1f}', ha='center', va='bottom', fontsize=9)
        cum += val
    ax.bar(len(items), abs(cum), bottom=min(0, cum), color=INK2, width=0.6)
    ax.text(len(items), max(0, cum), f'{cum:+,.1f}', ha='center', va='bottom', fontsize=9, fontweight='bold')
    ax.axhline(0, color=INK, lw=0.8)
    ax.set_xticks(range(len(items) + 1), [i[0] for i in items] + ['net (B − A)'], fontsize=8.5)
    ax.set_ylabel('T-eq (B − A)')
    ax.set_title(f"B − A in T-eq at {facts['rate_teq_per_qubit']:.1f} T-eq per qubit; cost B/A = "
                 f"{facts['cost_b_over_a']:.5f}", fontsize=10)
    ax = fig.add_subplot(gs[0, 2])
    ax.axis('off')
    txt = [f"width {facts['width'][0]} → {facts['width'][1]}", f"liabilities {facts['d_liabilities']:+,}",
           f"fix-ups {facts['d_fixups']:+,}", f"values matched {facts['values_matched']:,}",
           f"only in A {facts['values_a_only']:,}, only in B {facts['values_b_only']:,}"]
    if 'paying_twice' in facts:
        txt.append(f"paying twice {facts['paying_twice'][0]} → {facts['paying_twice'][1]}")
    for k, t in enumerate(txt):
        ax.text(0, 0.95 - k * 0.16, t, transform=ax.transAxes, fontsize=9, color=INK2, va='top')
    stg = [r['stage'][:12] for r in rows]
    y = np.arange(len(rows))[::-1]
    for col, (key, title, colr) in enumerate([('d_toffoli', 'Δ Toffolis (static)', ORANGE),
                                              ('d_hmr', 'Δ HMR records (accruals)', VIOLET),
                                              ('b_only', 'values unmatched', BLUE)]):
        ax = fig.add_subplot(gs[1, col])
        if key == 'b_only':
            ax.barh(y + 0.2, [r['b_only'] for r in rows], 0.4, color=ORANGE, label=f'only in {labs[1]}')
            ax.barh(y - 0.2, [r['a_only'] for r in rows], 0.4, color=BLUE, label=f'only in {labs[0]}')
            ax.legend(fontsize=7.5, loc='lower right')
        else:
            vals = [r[key] for r in rows]
            ax.barh(y, vals, 0.6, color=colr)
            ax.axvline(0, color=INK, lw=0.8)
        ax.set_yticks(y, stg if col == 0 else [''] * len(stg), fontsize=8)
        ax.set_title(title, fontsize=10)
        ax.grid(axis='y', visible=False)
    fig.subplots_adjust(left=0.12, right=0.98, top=1 - (0.75 + 0.19 * head.count('\n')) / (W * 0.78), bottom=0.06)
    return save_png(fig, str(base) + '.png')


# ------------------------------------------------------------------------------------------------ verb: snapshot
# The circuit viewer's stop point, headless. The viewer (snapshot.js on tracer.js) and this verb build the same JSON
# object (schema below, documented in RENDER.md); tests/test_circuit_render.py runs both and compares them. Op indices
# are the viewer's: INFO_TAG and DEBUG_PRINT are not ops. Measurement outcomes come from the viewer's PRNG (mulberry32,
# seeded), not from Python's `random`, so the trajectory is the one the viewer shows for the same x and seed.
SNAPSHOT_SCHEMA = 'kickmix-viewer-snapshot/1'
VIEWER_KINDS = ['X', 'CX', 'CCX', 'Z', 'CZ', 'CCZ', 'SWAP', 'CSWAP', 'R', 'HMR', 'NEG', 'Z_POW',
                'BIT_INVERT', 'BIT_STORE0', 'BIT_STORE1', 'PUSH_CONDITION', 'POP_CONDITION']
VIEWER_TO_KMB = [X, CX, CCX, Z, CZ, CCZ, SWAP, CSWAP, R, HMR, NEG, Z_POW, BIT_INVERT, BIT_STORE0, BIT_STORE1, PUSH, POP]
KMB_TO_VIEWER = {b: i for i, b in enumerate(VIEWER_TO_KMB)}
SNAPSHOT_IDLE_K = 1000
_M32 = 0xFFFFFFFF


def v_qubits(o):
    c = o[0]
    if c in (15, 16, 10) or c >= 12:
        return list()
    if c in (11, 9):
        return [o[1]]
    return list(o[1:-1])


def v_cond(o):
    c = o[0]
    return -1 if c in (15, 16) else o[-2 if c == 11 else -1]


def v_bits(o):
    c = o[0]
    return [o[2]] if c == 9 else [o[1]] if 12 <= c <= 15 else []


def viewer_data_from_kmb(c: Circuit):
    """A .kmb circuit as the viewer's op list (ops.json layout): INFO_TAG and DEBUG_PRINT dropped."""
    t = c.types.tolist()
    rq, _, qs = op_major_refs(c, 'q')
    rb, _, bs = op_major_refs(c, 'b')
    rq, qs, rb, bs = rq.tolist(), qs.tolist(), rb.tolist(), bs.tolist()
    angles = c.angle_units()
    ops, zp, ai = [], {}, 0
    for i, op in enumerate(t):
        base = op & 0x7F
        if op == PUSH:
            ops.append([15, rb[bs[i]]])
            continue
        if base == Z_POW:
            units = angles[ai]
            ai += 1
        if op == INFO_TAG or base in (0x07, 0x17, 0x47):
            continue
        if base not in KMB_TO_VIEWER:
            raise UserError(f'op {i}: {op_name(op)} has no viewer equivalent')
        code = KMB_TO_VIEWER[base]
        if code == 16:
            ops.append([16, -1])
            continue
        cond = rb[bs[i + 1] - 1] if op & HAS_COND else -1
        o = [code] + [rq[k] for k in range(qs[i], qs[i + 1])]
        if op & HAS_1B:
            o.append(rb[bs[i]])
        o.append(cond)
        if code == 11:
            o.append(float(units) / 2.0 ** 128)
            zp[str(len(ops))] = str(units)
        ops.append(o)
    regs = c.qubit_registers()
    xname = 'x' if 'x' in regs else (next(iter(regs)) if regs else None)
    return dict(ops=ops, zpow_exact=zp, x_qubits=regs.get(xname, []) if xname else [], n_qubits=c.num_qubits,
                n_bits=max(1, c.num_bits), source_kmb_sha256=c.sha256,
                registers=[dict(r=k, name=nm, qubits=q) for k, (nm, q) in enumerate(regs.items())])


def kmb_from_viewer_data(data, path):
    """Write the viewer's op list as a .kmb (same op indices), so render.py's own width and ledger code reads it."""
    types, st, angles = [], {k: [] for k in QSTREAMS + BSTREAMS}, bytearray()
    zp = data.get('zpow_exact', {})
    for i, o in enumerate(data['ops']):
        c = o[0]
        if c == 15:
            types.append(PUSH)
            st['bc'].append(o[1])
            continue
        if c == 16:
            types.append(POP)
            continue
        cond = v_cond(o)
        types.append(VIEWER_TO_KMB[c] | (HAS_COND if cond >= 0 else 0))
        q = v_qubits(o)
        for j, w in enumerate(q):
            st[(['q0'], ['qq0', 'qq1'], ['qqq0', 'qqq1', 'qqq2'])[len(q) - 1][j]].append(w)
        if c == 9 or 12 <= c <= 14:
            st['b0'].append(v_bits(o)[0])
        if cond >= 0:
            st['bc'].append(cond)
        if c == 11:
            u = int(zp[str(i)]) if str(i) in zp else int(round(o[3] * 2.0 ** 128)) % PHASE_MOD
            angles += struct.pack('<2Q', u & (2**64 - 1), u >> 64)
    regs = [('x', [(True, q) for q in data['x_qubits']])]
    for r in data.get('registers') or []:
        if (r.get('name') or '') != 'x' and r['qubits'] != data['x_qubits']:
            regs.append((r.get('name') or f"r{r['r']}", [(True, q) for q in r['qubits']]))
    write_kmb(path, np.array(types, np.uint8), {k: np.array(v, np.uint32) for k, v in st.items()}, bytes(angles),
              registers=regs, num_qubits=data.get('n_qubits') or 63, num_bits=data.get('n_bits') or 8288)


def load_viewer_data(path):
    p = Path(path)
    if not p.exists():
        raise UserError(f'circuit file not found: {path}', 'pass a viewer ops.json or a .kmb file')
    if p.suffix.lower() == '.json':
        try:
            d = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            raise UserError(f'{path} is not valid JSON: {e}') from None
        if not isinstance(d, dict) or 'ops' not in d or 'x_qubits' not in d:
            raise UserError(f'{path} is not a viewer op list', 'expected the viewer\'s ops.json (keys ops, x_qubits, '
                            'zpow_exact)')
        return d
    return viewer_data_from_kmb(read_kmb(path))


def load_viewer_stages(spec, circuit_path):
    """Viewer stages.json (stages and qubit groups). Default: stages.json beside an ops.json, if there is one."""
    if spec in (None, 'auto'):
        cand = Path(circuit_path).with_name('stages.json')
        if Path(circuit_path).suffix.lower() != '.json' or not cand.exists():
            return [], []
        spec = cand
    elif spec == 'none':
        return [], []
    p = Path(spec)
    if not p.exists():
        raise UserError(f'--stages file not found: {spec}', "pass the viewer's stages.json, or 'none'")
    d = json.loads(p.read_text())
    stages = d.get('stages', d) if isinstance(d, dict) else d
    out = []
    for s in stages:
        a, b = s.get('start', s.get('a')), s.get('end', s.get('b'))
        sid = s.get('id', s.get('name'))
        out.append(dict(id=sid, title=s.get('title', sid), start=int(a), end=int(b)))
    groups = [dict(name=g['name'], qubits=list(g['qubits'])) for g in (d.get('groups') or [])] if isinstance(d, dict) \
        else []
    return out, groups


class ViewerMachine:
    """Port of tracer.js (start/exec) with the viewer's per-op bookkeeping (snapshot.js stepD): one basis trajectory,
    exact phase in units of 2^-128 turn, HMR outcomes from mulberry32 (seeded) or forced to 0 (kickback off)."""

    def __init__(self, data, x, seed, kick):
        self.ops = data['ops']
        self.zp = {int(k): int(v) for k, v in data.get('zpow_exact', {}).items()}
        self.nq, self.nb = data.get('n_qubits') or 63, data.get('n_bits') or 8288
        self.q, self.b = bytearray(self.nq), bytearray(self.nb)
        for k, w in enumerate(data['x_qubits']):
            self.q[w] = (x >> k) & 1
        self.xset = set(data['x_qubits'])
        self.stack, self.nfalse, self.phase, self.rs = [], 0, 0, seed & _M32
        self.tof, self.dirty, self.kick = 0, 0, kick
        self.t, self.ran, self.m = 0, 1, -1
        self.ones, self.peak, self.peak_op = 0, 0, -1
        self.rotations = self.measurements = self.resets = 0
        self.kicked = bytearray(len(self.ops))

    def rnd(self):
        self.rs = (self.rs + 0x6D2B79F5) & _M32
        t = self.rs
        t = ((t ^ (t >> 15)) * (t | 1)) & _M32
        t ^= (t + (((t ^ (t >> 7)) * (t | 61)) & _M32)) & _M32
        return ((t ^ (t >> 14)) & _M32) & 1

    def step(self, i):
        o, q, b = self.ops[i], self.q, self.b
        c = o[0]
        qs = v_qubits(o)
        before = [q[w] for w in qs]
        hq = q[o[1]] if c == 9 else 0
        t, ran, m = 0, 1, -1
        if c == 15:
            v = b[o[1]]
            self.stack.append(v)
            self.nfalse += v == 0
        elif c == 16:
            if self.stack and not self.stack.pop():
                self.nfalse -= 1
        else:
            cond = o[-2 if c == 11 else -1]
            if self.nfalse or (cond >= 0 and not b[cond]):
                ran = 0
            elif c == 1:
                if q[o[1]]:
                    q[o[2]] ^= 1
            elif c == 4:
                if q[o[1]] and q[o[2]]:
                    self.phase ^= HALF_TURN
            elif c == 3:
                if q[o[1]]:
                    self.phase ^= HALF_TURN
            elif c == 0:
                q[o[1]] ^= 1
            elif c == 2:
                t = 1
                if q[o[1]] and q[o[2]]:
                    q[o[3]] ^= 1
            elif c == 9:
                m = self.rnd() if self.kick else 0
                b[o[2]] = m
                if q[o[1]]:
                    if m:
                        self.phase ^= HALF_TURN
                    q[o[1]] = 0
            elif c == 7:
                t = 1
                if q[o[1]]:
                    q[o[2]], q[o[3]] = q[o[3]], q[o[2]]
            elif c == 8:
                if q[o[1]]:
                    self.dirty += 1
                    if self.rnd():
                        self.phase ^= HALF_TURN
                    q[o[1]] = 0
            elif c == 11:
                if q[o[1]]:
                    self.phase = (self.phase + self.zp[i]) % PHASE_MOD
            elif c == 5:
                t = 1
                if q[o[1]] and q[o[2]] and q[o[3]]:
                    self.phase ^= HALF_TURN
            elif c == 10:
                self.phase ^= HALF_TURN
            elif c == 6:
                q[o[1]], q[o[2]] = q[o[2]], q[o[1]]
            elif c == 12:
                b[o[1]] ^= 1
            elif c == 13:
                b[o[1]] = 0
            elif c == 14:
                b[o[1]] = 1
            if ran:
                self.tof += t
        self.t, self.ran, self.m = t, ran, m
        for w, was in zip(qs, before):
            if q[w] != was and w not in self.xset:
                self.ones += 1 if q[w] else -1
        if self.ones > self.peak:
            self.peak, self.peak_op = self.ones, i
        if ran:
            if c == 11:
                self.rotations += 1
            elif c == 9:
                self.measurements += 1
                if m == 1 and hq:
                    self.kicked[i] = 1
            elif c == 8:
                self.resets += 1

    def run(self, stop):
        """Run every op; return the state just before and just after op `stop`."""
        o = self.ops[stop]
        qs, bits, cond = v_qubits(o), v_bits(o), v_cond(o)
        at = None
        for i in range(len(self.ops)):
            if i == stop:
                pre = dict(q=[self.q[w] for w in qs], b=[self.b[w] for w in bits],
                           cond=self.b[cond] if cond >= 0 else None, depth=len(self.stack), open=self.nfalse == 0,
                           phase=self.phase)
            self.step(i)
            if i == stop:
                at = dict(pre=pre, q=bytes(self.q), b=bytes(self.b), phase=self.phase, tof=self.tof, ran=self.ran,
                          m=self.m, ones=self.ones, peak=self.peak, peak_op=self.peak_op, rotations=self.rotations,
                          measurements=self.measurements, resets=self.resets)
        return at


def _turns(p):
    return float(p >> 64) / 2.0 ** 64


class ViewerStatic:
    """Static facts for snapshots, from this script's own width (sweep) and ledger (ledger_core) code run on the
    op list."""

    def __init__(self, data, stages, groups):
        ops = data['ops']
        self.n = len(ops)
        with tempfile.TemporaryDirectory(prefix='kickmix-render-') as td:
            kp = Path(td) / 'viewer.kmb'
            kmb_from_viewer_data(data, kp)
            c = read_kmb(kp)
            self.profile = [int(v) for v in sweep(c)['profile']]
            core = ledger_core(c, np.asarray(self.profile, np.int16))
            self.regs = register_qubit_ids(c)
            v, li = core['values'], core['liab']
            self.values = [dict(q=int(q), a=int(a), b=int(b), kind=int(k), reg=bool(r), init=bool(i))
                           for q, a, b, k, r, i in zip(v['q'], v['a'], v['b'], v['kind'], v['reg'], v['init'])]
            self.liab = [dict(op=int(o), qubit=int(q), settle=int(s), reads=int(r))
                         for o, q, s, r in zip(li['op'], li['qubit'], li['settle'], li['reads'])]
            self.fix = [int(f) for f in li['fixups']]
            del c, core
        self.nq = data.get('n_qubits') or 63
        self.nb = data.get('n_bits') or 8288
        self.xq = list(data['x_qubits'])
        opq = [v_qubits(o) for o in ops]
        self.maxq = max([self.nq, (max(self.regs) + 1) if self.regs else 0] + [w + 1 for qs in opq for w in qs])
        self.touches = [[] for _ in range(self.maxq)]
        for i, qs in enumerate(opq):
            for w in qs:
                self.touches[w].append(i)
        self.is_reg = set(self.regs)
        self.static = {k: [0] * (self.n + 1) for k in ('toffolis', 'rotations', 'measurements', 'resets')}
        for i, o in enumerate(ops):
            c = o[0]
            for k, hit in (('toffolis', c in (2, 5, 7)), ('rotations', c == 11), ('measurements', c == 9),
                           ('resets', c == 8)):
                self.static[k][i + 1] = self.static[k][i] + hit
        self.stages = [dict(index=j, **s) for j, s in enumerate(stages)]
        self.groups = groups or ([dict(name='x', qubits=self.xq)] + [
            dict(name=r.get('name') or f"r{r['r']}", qubits=r['qubits']) for r in data.get('registers') or []
            if r['qubits'] != self.xq and (r.get('name') or '') != 'x'])
        sha = data.get('source_kmb_sha256')
        self.sha12 = sha[:12] if sha else None


def viewer_snapshot(data, st: ViewerStatic, op, x, seed=1, kick=True, idle_k=SNAPSHOT_IDLE_K):
    """The viewer's snapshot object at stop point `op` (the state after op `op` ran) for input x."""
    import bisect
    n, ops = st.n, data['ops']
    if not 0 <= op < n:
        raise UserError(f'--op {op} is outside the circuit (0..{n - 1})')
    xmax = 2 ** len(st.xq) - 1
    if not 1 <= x <= xmax:
        raise UserError(f'--x {x} is outside 1..{xmax:,} (the input register has {len(st.xq)} qubits)')
    mk, mz = ViewerMachine(data, x, seed, True), ViewerMachine(data, x, seed, False)
    ak, az = mk.run(op), mz.run(op)
    mode, a = (mk, ak) if kick else (mz, az)
    o = ops[op]
    c, qs, bits, cond = o[0], v_qubits(o), v_bits(o), v_cond(o)
    pre = a['pre']
    changed_q = [w for w, was in zip(qs, pre['q']) if a['q'][w] != was]
    changed_b = [w for w, was in zip(bits, pre['b']) if a['b'][w] != was]
    stg = next((s for s in st.stages if s['start'] <= op < s['end']), None)
    gate = dict(op=op, kind=VIEWER_KINDS[c], qubits=qs, bits=bits, cond=cond if cond >= 0 else None,
                cond_value=pre['cond'], stack_depth=pre['depth'], stack_open=pre['open'], ran=a['ran'] == 1,
                toffoli_class=c in (2, 5, 7), outcome=a['m'] if c == 9 and a['ran'] == 1 else None,
                kicked=c == 9 and a['ran'] == 1 and a['m'] == 1 and pre['q'][0] == 1,
                angle_turns=o[3] if c == 11 else None, changed_qubits=changed_q, changed_bits=changed_b,
                phase_changed=a['phase'] != pre['phase'])
    prof = st.profile
    gpk = max(prof)
    lpk = max(prof[:op + 1])
    width = dict(
        ones=dict(now=a['ones'], peak_so_far=a['peak'], peak_so_far_op=a['peak_op'] if a['peak_op'] >= 0 else None,
                  peak=mode.peak, peak_op=mode.peak_op if mode.peak_op >= 0 else None, of=st.nq - len(st.xq)),
        live=dict(now=prof[op], peak_so_far=lpk, peak_so_far_op=prof.index(lpk), peak=gpk, peak_op=prof.index(gpk),
                  registers=len(st.regs)))
    cls = ('toffolis', 'rotations', 'measurements', 'resets')
    so = dict(toffolis=a['tof'], rotations=a['rotations'], measurements=a['measurements'], resets=a['resets'])
    tot = dict(toffolis=mode.tof, rotations=mode.rotations, measurements=mode.measurements, resets=mode.resets)
    cost = dict(so_far=so, remaining={k: tot[k] - so[k] for k in cls}, total=tot,
                static_remaining={k: st.static[k][n] - st.static[k][op + 1] for k in cls},
                static_total={k: st.static[k][n] for k in cls})
    ph = a['phase']
    tgt = math.fmod(1 / math.sqrt(x), 1)
    tol = 2 ** -24 / math.sqrt(x)
    err = math.fmod(math.fmod(_turns(ph) - tgt, 1) + 1.5, 1) - 0.5
    phase = dict(outcomes='seeded' if kick else 'zero', units=str(ph), turns=_turns(ph), target_turns=tgt,
                 error_turns=err, tolerance_turns=tol, share_of_tolerance=abs(err) / tol,
                 without_kickback=dict(units=str(az['phase']), turns=_turns(az['phase'])),
                 kickback_turns=_turns((ak['phase'] - az['phase']) % PHASE_MOD))
    chg = set(changed_q)
    registers = []
    for g in st.groups:
        vals = [a['q'][w] for w in g['qubits']]
        registers.append(dict(name=g['name'], qubits=list(g['qubits']),
                              value=sum(v << j for j, v in enumerate(vals)) if len(vals) <= 53 else None,
                              bits=''.join(str(v) for v in reversed(vals)),
                              changed=[j for j, w in enumerate(g['qubits']) if w in chg]))
    by_wire = {}
    for v in st.values:
        by_wire.setdefault(v['q'], []).append(v)
    wires = []
    for w in range(st.maxq):
        tl = st.touches[w]
        j = bisect.bisect_right(tl, op)
        nxt = tl[j] if j < len(tl) else None
        live = w in st.is_reg
        if not live:
            for v in reversed(by_wire.get(w, [])):
                if v['a'] <= op:
                    live = v['b'] > op
                    break
        if live and (nxt is None or nxt - op > idle_k):
            wires.append(dict(wire=w, register=w in st.is_reg, next_touch=nxt,
                              idle_for=None if nxt is None else nxt - op))
    cut = op + 1
    open_v, closed = [], {nm: 0 for nm in KIND_NAMES.values()}
    opened = closing = 0
    for v in st.values:
        if v['reg']:
            continue
        if v['b'] == op:
            closing += 1
        if v['init']:
            continue
        if v['a'] < cut:
            opened += 1
        if v['b'] < cut:
            closed[KIND_NAMES[v['kind']]] += 1
        elif v['a'] < cut:
            open_v.append(dict(wire=v['q'], opened=v['a'], closes=v['b'], held=op - v['a'],
                               settlement=KIND_NAMES[v['kind']]))
    open_v.sort(key=lambda r: (r['opened'], r['wire']))
    debts, records, repaid, never = [], 0, 0, 0
    for li in st.liab:
        if li['op'] >= cut:
            continue
        records += 1
        if li['settle'] < 0:
            never += 1
        elif li['settle'] < cut:
            repaid += 1
        else:
            debts.append(dict(wire=li['qubit'], hmr=li['op'], repaid_at=li['settle'], fixups=li['reads'],
                              kicked=bool(mode.kicked[li['op']])))
    tb = dict(live=prof[op], registers=len(st.regs), open_values=len(open_v), closing_here=closing, opened=opened,
              closed=closed, records=records, repaid=repaid, open_debts=len(debts), never_repaid=never,
              open_debts_kicked=sum(1 for d in debts if d['kicked']), fixups_so_far=bisect.bisect_right(st.fix, op))
    line = (f"live {tb['live']} = {tb['registers']} registers + {tb['open_values']} open values + "
            f"{tb['closing_here']} closing here; records {records} = {repaid} repaid + {len(debts)} open debts + "
            f"{never} never repaid")
    return dict(
        schema=SNAPSHOT_SCHEMA,
        circuit=dict(sha12=st.sha12, ops=n, qubits=st.nq, bits=st.nb, x_qubits=len(st.xq)),
        x=x, seed=seed, kickback=bool(kick), op=op,
        position=dict(op=op, ops=n, done=op + 1, stage=dict(index=stg['index'], id=stg['id'], title=stg['title'],
                                                             start=stg['start'], end=stg['end']) if stg else None),
        gate=gate, width=width, cost=cost, phase=phase, registers=registers, idle=dict(k=idle_k, wires=wires),
        ledger=dict(trial_balance=tb, line=line, open_values=open_v, open_debts=debts,
                    longest_held=open_v[0] if open_v else None))


def cmd_snapshot(args):
    data = load_viewer_data(args.circuit)
    stages, groups = load_viewer_stages(args.stages, args.circuit)
    st = ViewerStatic(data, stages, groups)
    x = args.x if args.x is not None else min(161, 2 ** len(st.xq) - 1)
    snap = viewer_snapshot(data, st, args.op, x, args.seed, not args.no_kickback, args.idle_k)
    if args.print:
        return dict(_print=snap)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem_ = args.name or f"{st.sha12 or Path(args.circuit).stem}_op{args.op}_x{x}"
    path = out / f'{stem_}_snapshot.json'
    path.write_text(json.dumps(snap, indent=1) + '\n')
    g, tb = snap['gate'], snap['ledger']['trial_balance']
    stg = snap['position']['stage']
    facts = dict(op=args.op, gate=f"{g['kind']} {' '.join('q%d' % w for w in g['qubits'])}".strip() +
                 ('' if g['ran'] else ' (skipped)'), stage=stg['id'] if stg else None, x=x, seed=args.seed,
                 kickback=snap['kickback'], phase_turns=round(snap['phase']['turns'], 12),
                 share_of_tolerance=round(snap['phase']['share_of_tolerance'], 6),
                 ones=snap['width']['ones']['now'], live=snap['width']['live']['now'],
                 toffolis_so_far=snap['cost']['so_far']['toffolis'], line=snap['ledger']['line'],
                 open_debts_kicked=tb['open_debts_kicked'], idle_wires=len(snap['idle']['wires']))
    return dict(ok=True, verb='snapshot', tier='sampled(1)',
                basis='viewer tracer (tracer.js port), HMR outcomes '
                      + (f'mulberry32 seed {args.seed}' if not args.no_kickback else 'forced to 0') + '; '
                      'one basis trajectory; ledger and width static (render.py ledger/width semantics)',
                files=[file_entry(path, kind='json')],
                circuits=[dict(path=str(args.circuit), sha12=st.sha12, ops=st.n, qubits=st.nq)], facts=facts,
                next=[f'python render.py snapshot {args.circuit} --op {min(st.n - 1, args.op + 1)} --x {x}',
                      f'python render.py snapshot {args.circuit} --op {args.op} --x {x} --print'])


# ------------------------------------------------------------------------------------------------ verb: doctor
def cmd_doctor(args):
    checks, ok = [], True

    def add(name, good, detail, required=True, hint=None):
        nonlocal ok
        ok &= good or not required
        r = dict(check=name, ok=bool(good), detail=detail, required=required)
        if hint and not good:
            r['hint'] = hint
        checks.append(r)

    add('python', sys.version_info >= (3, 9), sys.version.split()[0], hint='use Python 3.9 or newer')
    add('numpy', True, np.__version__)
    for mod, pipname in (('matplotlib', 'matplotlib'), ('PIL', 'pillow')):
        try:
            m = __import__(mod)
            add(pipname, True, getattr(m, '__version__', '?'))
        except ImportError:
            add(pipname, False, 'missing', hint=f'pip install {pipname}')
    if kickmix_importable():
        import kickmix
        add('kickmix', True, f'importable ({getattr(kickmix, "__file__", "?")})', required=False)
    else:
        add('kickmix', False, 'not importable here; only trace --engine kickmix (DQI-size traces) needs it',
            required=False, hint='large traces: --kickmix-python /path/to/python-with-kickmix or KICKMIX_PYTHON')
    kpy = args.kickmix_python or os.environ.get('KICKMIX_PYTHON')
    if kpy:
        r = subprocess.run([kpy, '-I', '-c', 'import kickmix, numpy; print(numpy.__version__)'], capture_output=True,
                           text=True)
        add('kickmix-python', r.returncode == 0, kpy if r.returncode == 0 else r.stderr.strip()[-200:],
            required=False, hint='that interpreter needs kickmix and numpy')
    try:
        with tempfile.TemporaryDirectory() as td:
            fig, ax = new_fig(300, 0.5)
            ax.plot([0, 1], [0, 1])
            img = fig_image(fig)
            info = save_gif([img, img], [100, 100], Path(td) / 't.gif')
            add('render', True, f'png+gif ok ({info["px"][0]}x{info["px"][1]})')
    except UserError as e:
        add('render', False, str(e), hint=e.hint)
    d = cache_dir(args)
    add('cache', d is not None, str(d) if d else 'disabled or not writable', required=False,
        hint='set --cache DIR or KICKMIX_RENDER_CACHE; the cache only saves time')
    return dict(ok=ok, verb='doctor', tier='static', basis='local environment checks',
                facts=dict(tool_version=TOOL_VERSION, platform=sys.platform), rows=checks)


# ------------------------------------------------------------------------------------------------ CLI
EXAMPLES = {
    'width': """examples:
  python render.py width a.kmb                          # a_width.png + a_width.gif
  python render.py width a.kmb b.kmb --labels "63 q,62 q"
  python render.py width dqi.kmb --zoom unextract --no-gif
  python render.py width a.kmb --zoom 16000:18000 --tween 0
  python render.py width a.kmb --stages ../stages.json  # named stages from a file""",
    'diff': """examples:
  python render.py diff a.kmb b.kmb
  python render.py diff host.kmb spliced.kmb --labels "host,splice" --max-frames 12
  python render.py diff a.kmb b.kmb --no-gif --op-limit 100000""",
    'trace': """examples:
  python render.py trace invsqrt.kmb --set x=161
  python render.py trace invsqrt.kmb --set x=3133452 --outcomes 7      # random HMR outcomes, seed 7
  python render.py trace dqi.kmb --set-json shot.json --watch 'S*' --engine kickmix
  python render.py trace dqi.kmb --set-json shot.json --watch 'S*' --kickmix-python ~/kickmix-venv/bin/python
shot.json: {"x": 161} or {"S*": [2500, 2667, ...], "E*": [...]} (NAME* fills NAME0, NAME1, ...)""",
    'cost': """examples:
  python render.py cost a.kmb
  python render.py cost a.kmb b.kmb --labels "63 q,62 q"
  python render.py cost dqi_a.kmb dqi_b.kmb --dqi-r 1.1913
  python render.py cost a.kmb --mean-toffolis 1329.5     # use a measured per-shot mean""",
    'ledger': """examples:
  python render.py ledger invsqrt.kmb                          # exact value identity over all x (about 10 s)
  python render.py ledger dqi.kmb --no-csv                     # static; about 18 s and 3 GB for 60 M ops
  python render.py ledger --compare 9a8627.kmb a568bc.kmb --labels "63 q,62 q"
  python render.py ledger --compare 7f3d64.kmb 40fa4e.kmb --mean-toffolis 430413,430837
  python render.py ledger invsqrt.kmb --no-exact --rate 26      # static only, own exchange rate
Bookkeeping: accounts = wires (registers grouped), entries = ops, values = wire lifetimes settled by
contra (unitary uncompute), accrual (HMR + later phase fix-ups) or dead tail (R, or HMR never repaid).""",
    'snapshot': """examples:
  python render.py snapshot ops.json --op 15213 --x 161              # end of the coefficient lookup
  python render.py snapshot ops.json --op 15213 --x 161 --print      # the JSON object itself on stdout
  python render.py snapshot ops.json --op 15213 --x 161 --no-kickback
  python render.py snapshot a.kmb --op 1000 --x 3133452 --seed 7""",
    'doctor': """examples:
  python render.py doctor
  python render.py doctor --kickmix-python /path/to/venv/bin/python""",
}


def common(p, gif=True, two=True, png=True):
    if two:
        p.add_argument('circuits', nargs='+', metavar='CIRCUIT.kmb', help='one or two .kmb files (A, then B)')
    p.add_argument('--out', default=DEFAULT_OUT, help=f'output directory (default ./{DEFAULT_OUT})')
    p.add_argument('--name', help='file name stem (default: circuit stems joined by _vs_)')
    p.add_argument('--labels', help='comma-separated display labels, one per circuit')
    p.add_argument('--stages', default='auto',
                   help='auto (INFO_TAG regions, else rotation-anchored), tags, rot, equal:N, or a stages .json')
    p.add_argument('--px', type=int, default=1000, help='image width in pixels (default 1000)')
    if png:
        p.add_argument('--no-png', action='store_true', help='skip the PNG')
    if gif:
        p.add_argument('--no-gif', action='store_true', help='skip the GIF')
        p.add_argument('--max-mb', type=float, default=DEFAULT_GIF_MB, help='GIF size budget in MB (default 5)')
        p.add_argument('--max-frames', type=int, default=60, help='upper bound on GIF frames (default 60)')
        p.add_argument('--hold', type=int, default=1400, help='ms to hold each key frame (default 1400)')
    else:  # accepted and ignored, so one flag set works for every verb (e.g. in --spec defaults)
        for flag in ('--no-gif',):
            p.add_argument(flag, action='store_true', help=argparse.SUPPRESS)
        for flag, typ in (('--max-mb', float), ('--max-frames', int), ('--hold', int)):
            p.add_argument(flag, type=typ, help=argparse.SUPPRESS)
    p.add_argument('--cache', help='cache directory for width profiles (default ~/.cache/kickmix-render)')
    p.add_argument('--no-cache', action='store_true', help='do not read or write the cache')
    p.add_argument('--all', action='store_true', help='print every row (default: at most 20 rows / 2 KB)')


def build_parser():
    ap = argparse.ArgumentParser(prog='render.py', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--spec', help='JSON file of several renders: [["width","a.kmb"], {"argv": [...]}, "cost a.kmb"]')
    ap.add_argument('--debug', action='store_true', help='show Python tracebacks on errors')
    ap.add_argument('--version', action='version', version=f'kickmix-render {TOOL_VERSION}')
    sub = ap.add_subparsers(dest='verb', metavar='{width,diff,trace,cost,ledger,snapshot,doctor}')
    fmt = argparse.RawDescriptionHelpFormatter

    p = sub.add_parser('width', help='live-qubit profile, stage by stage, with zoom', epilog=EXAMPLES['width'],
                       formatter_class=fmt, description='Live qubits (certified width) along one or two circuits. '
                       'B is aligned to A stage by stage. The GIF tours the stages with zooms.')
    common(p)
    p.add_argument('--zoom', help='stage name or op range a:b to zoom into (PNG and GIF)')
    p.add_argument('--tween', type=int, default=4, help='in-between frames per zoom move (default 4; 0 = cuts)')
    p.add_argument('--min-stage', type=float, default=0.004, help='skip stages shorter than this fraction in the GIF')
    p.set_defaults(fn=cmd_width)

    p = sub.add_parser('diff', help='what changed between two circuits, aligned by stage', epilog=EXAMPLES['diff'],
                       formatter_class=fmt,
                       description='Stage-aligned structural diff of A -> B: stages identical up to '
                       'qubit renaming, module splices (tagged circuits), op-level insertions/removals.')
    common(p)
    p.add_argument('--op-limit', type=int, default=30_000_000,
                   help='largest untagged region (ops) to align op by op (default 30M)')
    p.add_argument('--refine', type=int, default=20_000,
                   help='changed modules up to this many ops are refined to op-level edits (default 20000)')
    p.set_defaults(fn=cmd_diff)

    p = sub.add_parser('trace', help="follow one shot's qubits and registers through the circuit",
                       epilog=EXAMPLES['trace'], formatter_class=fmt,
                       description='One shot in the computational basis. Engines: python (pure Python, any machine, '
                       f'up to {PyTracer.MAX_OPS:,} ops) and kickmix (kickmix Simulator; needed for DQI-size '
                       'circuits).')
    common(p)
    p.add_argument('--set', action='append', metavar='NAME=VALUE', help='register input, repeatable (x=161)')
    p.add_argument('--set-json', help='JSON object of register inputs; "S*": [..] fills S0, S1, ...')
    p.add_argument('--show', help='registers to print in the GIF, comma-separated, NAME* allowed')
    p.add_argument('--watch', help='registers to watch for the value 0 (e.g. S*): rows report when each hits 0')
    p.add_argument('--engine', choices=['auto', 'python', 'kickmix'], default='auto',
                   help='auto: python up to 5M ops, else kickmix (needs kickmix here or --kickmix-python)')
    p.add_argument('--outcomes', default='zero', help="python engine HMR outcomes: 'zero' or an integer seed")
    p.add_argument('--seed', type=int, default=1, help='kickmix engine simulator seed (default 1)')
    p.add_argument('--kickmix-python', help='interpreter with kickmix, when this one lacks it (or KICKMIX_PYTHON)')
    p.add_argument('--columns', type=int, default=240, help='state snapshots along the circuit (default 240)')
    p.add_argument('--frames', type=int, default=24, help='GIF frames (default 24)')
    p.add_argument('--force', action='store_true', help='allow the python engine on very large circuits')
    p.set_defaults(fn=cmd_trace)

    p = sub.add_parser('cost', help='static cost dashboard (PNG)', epilog=EXAMPLES['cost'], formatter_class=fmt,
                       description='Q, Toffolis, cost and score ratio, per stage, from the official cost formulas: '
                       'invsqrt Q x (Toffolis + T/4), DQI R x Q^2 x Toffolis. invsqrt Toffolis are a sampled per-shot '
                       'mean (python tracer, --sample); otherwise the static count in the file, an upper bound on '
                       'the mean (or pass --mean-toffolis). invsqrt rotations default to {INVSQRT_ROT_T} T '
                       'each (the verifier derives the exact value from the error slack; set --rot-t).')
    common(p, gif=False)
    p.add_argument('--task', choices=['auto', 'invsqrt', 'dqi', 'generic'], default='auto')
    p.add_argument('--mean-toffolis', help='measured per-shot Toffoli means, one per circuit, comma-separated')
    p.add_argument('--sample', type=int, help='invsqrt: shots for the sampled Toffoli mean (default 200; 0 = static)')
    p.add_argument('--seed', type=int, default=1, help='sampling seed (default 1)')
    p.add_argument('--rot-t', type=float, default=INVSQRT_ROT_T, help='T count per invsqrt rotation')
    p.add_argument('--dqi-r', type=float, default=DQI_R_ZERO_FAIL,
                   help=f'DQI repetition factor R (default {DQI_R_ZERO_FAIL}: zero failures in 102,400 shots)')
    p.set_defaults(fn=cmd_cost)

    p = sub.add_parser('ledger', help='double-entry ledger: values, phase debts, capital, paying twice (PNG + CSV)',
                       epilog=EXAMPLES['ledger'], formatter_class=fmt,
                       description='Treat the circuit as double-entry bookkeeping: wires are accounts, ops are '
                       'entries, every wire lifetime is a value settled by unitary uncompute (contra), by HMR plus '
                       'later phase fix-ups (accrual) or by a dead tail. Width is capital priced at the task\'s '
                       'exchange rate; Toffolis are expenses. Reports the trial balance at each stage cut, values '
                       'ranked by ops idle at full width, the full-width runs (those with no idle value cannot be '
                       'narrowed by any release) and the fewest releases covering the rest, ANDs computed twice and '
                       'the closing balance. invsqrt circuits also get an exact '
                       'run over all 3,133,452 x (value identity, close check). --compare reconciles two circuits.')
    common(p, gif=False)
    p.add_argument('--compare', action='store_true', help='reconcile two circuits A B (stage by stage)')
    p.add_argument('--task', choices=['auto', 'invsqrt', 'dqi', 'generic'], default='auto')
    p.add_argument('--rate', type=float, help='exchange rate in T-eq per qubit (default: from the cost formula)')
    p.add_argument('--mean-toffolis', help='measured per-shot Toffoli means (one per circuit, comma-separated)')
    p.add_argument('--models', default='zero,7,11',
                   help='invsqrt exact run: HMR outcome models, zero and/or integer seeds (default zero,7,11)')
    p.add_argument('--no-exact', action='store_true', help='skip the exact all-x run (invsqrt)')
    p.add_argument('--no-csv', action='store_true', help='skip the per-value CSV')
    p.add_argument('--top', type=int, default=20, help='values (idle at peak) and debts to list (default 20)')
    p.add_argument('--rot-t', type=float, default=INVSQRT_ROT_T, help='T count per invsqrt rotation')
    p.add_argument('--dqi-r', type=float, default=DQI_R_ZERO_FAIL, help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_ledger)

    p = sub.add_parser('snapshot', help="the viewer's stop point as JSON: gate, width, cost, phase, registers, ledger",
                       epilog=EXAMPLES['snapshot'], formatter_class=fmt,
                       description="The circuit viewer's snapshot at one stop point, headless: the same JSON object "
                       "the viewer's 'Copy snapshot JSON' button gives (schema kickmix-viewer-snapshot/1, RENDER.md). "
                       'The state is the one after op N has run, for input x. Takes the viewer\'s ops.json (op indices '
                       'as in the viewer) or a .kmb (INFO_TAG and DEBUG_PRINT are not counted as ops).')
    p.add_argument('circuit', metavar='CIRCUIT', help="the viewer's ops.json, or a .kmb file")
    p.add_argument('--op', type=int, required=True, help='stop point: the op that has just run (0-based)')
    p.add_argument('--x', type=int, help='input x (default 161, or the largest x if the register is narrower)')
    p.add_argument('--seed', type=int, default=1, help="HMR outcome seed, the viewer's PRNG (default 1)")
    p.add_argument('--no-kickback', action='store_true',
                   help='force every HMR outcome to 0 (the viewer\'s "phase without kickback" toggle)')
    p.add_argument('--stages', default='auto',
                   help="viewer stages.json (stages and qubit groups); auto = stages.json beside an ops.json; 'none'")
    p.add_argument('--idle-k', type=int, default=SNAPSHOT_IDLE_K,
                   help=f'idle wires: live but untouched for more than this many ops (default {SNAPSHOT_IDLE_K})')
    p.add_argument('--print', action='store_true', help='print the snapshot object itself instead of the summary')
    p.add_argument('--out', default=DEFAULT_OUT, help=f'output directory (default ./{DEFAULT_OUT})')
    p.add_argument('--name', help='file name stem')
    p.add_argument('--all', action='store_true', help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_snapshot)

    p = sub.add_parser('doctor', help='check dependencies', epilog=EXAMPLES['doctor'], formatter_class=fmt)
    p.add_argument('--kickmix-python', help='also check this interpreter for kickmix')
    p.add_argument('--cache')
    p.add_argument('--no-cache', action='store_true')
    p.add_argument('--all', action='store_true')
    p.set_defaults(fn=cmd_doctor)
    return ap


def run_argv(argv):
    """Parse and run one command; returns (summary dict, all_rows flag). Raises UserError."""
    ap = build_parser()
    errors = []

    def fail(msg):
        errors.append(msg)
        raise UserError(f'bad arguments: {msg}', 'see python render.py <verb> --help for examples')
    ap.error = fail
    for sp in ap._subparsers._group_actions[0].choices.values():
        sp.error = fail
    a = ap.parse_args(argv)
    if a.spec:
        res = run_spec(a.spec)
        res['rerun'] = rerun_cmd(argv)
        res['tool'] = f'kickmix-render {TOOL_VERSION} ({tool_sha()})'
        return res, False
    if not a.verb:
        raise UserError('no verb given', 'python render.py {width,diff,trace,cost,ledger,snapshot,doctor} --help')
    t0 = time.time()
    res = a.fn(a)
    res.setdefault('rerun', rerun_cmd(argv))
    res['tool'] = f'kickmix-render {TOOL_VERSION} ({tool_sha()})'
    res.setdefault('facts', {})
    res['seconds'] = round(time.time() - t0, 1)
    return res, getattr(a, 'all', False)


def run_spec(path):
    p = Path(path)
    if not p.exists():
        raise UserError(f'spec file not found: {path}')
    try:
        d = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise UserError(f'spec {path} is not valid JSON: {e}') from None
    jobs = d.get('jobs', []) if isinstance(d, dict) else d
    defaults = d.get('defaults', []) if isinstance(d, dict) else []
    rows, ok, written = [], True, []
    for k, job in enumerate(jobs):
        argv = shlex.split(job) if isinstance(job, str) else (job.get('argv') if isinstance(job, dict) else list(job))
        if not argv or '--spec' in argv:
            rows.append(dict(job=k, ok=False, error='empty job or nested --spec'))
            ok = False
            continue
        argv = [str(x) for x in argv]
        if argv[0] != 'doctor':
            argv = argv + [str(x) for x in defaults]
        try:
            res, _ = run_argv(argv)
            files = [f['path'] for f in res.get('files', []) if f.get('kind') != 'json']
            written += [f['path'] for f in res.get('files', [])]
            rows.append(dict(job=k, verb=argv[0], ok=True, tier=res.get('tier'), files=files,
                             seconds=res.get('seconds')))
        except UserError as e:
            ok = False
            rows.append(dict(job=k, verb=argv[0], ok=False, error=str(e), hint=e.hint))
    return dict(ok=ok, verb='spec', tier='static', basis='each job has its own tier (rows)', files=[],
                facts=dict(jobs=len(jobs), failed=sum(1 for r in rows if not r['ok']), files_written=len(written)),
                rows=rows)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == '_engine':
        return cmd_engine(argv[1:])
    if argv and argv[0] in ('-h', '--help') or not argv:
        build_parser().print_help()
        return 0 if argv else 2
    debug = '--debug' in argv
    if debug:
        argv.remove('--debug')
    try:
        res, all_rows = run_argv(argv)
        if '_print' in res:  # snapshot --print: the snapshot object itself
            print(json.dumps(res['_print'], ensure_ascii=False, separators=(',', ':')))
            return 0
        if res.get('files'):  # keep stdout short: drop the per-file extras beyond path/bytes/kind/frames
            res['files'] = [{k: v for k, v in f.items() if k in ('path', 'bytes', 'kind', 'frames')}
                            for f in res['files']]
        print(dumps(res) if all_rows else render_summary(res))
        return 0 if res.get('ok', True) else 1
    except UserError as e:
        print(dumps(dict(ok=False, error=str(e), hint=e.hint) if e.hint else dict(ok=False, error=str(e))))
        return 2
    except SystemExit as e:  # --help / --version from a subparser
        return int(e.code or 0)
    except MemoryError:
        print(dumps(dict(ok=False, error='out of memory', hint='DQI-size circuits need about 4 GB free; close other '
                                                                  'work or use a larger machine')))
        return 1
    except Exception as e:  # pragma: no cover
        if debug:
            raise
        print(dumps(dict(ok=False, error=f'internal error: {type(e).__name__}: {e}',
                         hint='rerun with --debug for the traceback, and please report it')))
        return 1


def _release_freed_memory():
    """On macOS, libmalloc keeps freed large blocks in a cache that counts toward the process footprint (about 5 GB
    for a DQI ledger or diff). Re-exec once with the cache off: the peak drops by half, the speed is unchanged."""
    if sys.platform != 'darwin' or os.environ.get('MallocLargeCache') is not None:
        return
    env = dict(os.environ, MallocLargeCache='0')
    argv = [sys.executable] + list(getattr(sys, 'orig_argv', [sys.executable] + sys.argv)[1:])
    try:
        os.execve(sys.executable, argv, env)
    except OSError:
        pass


if __name__ == '__main__':
    _release_freed_memory()
    sys.exit(main())
