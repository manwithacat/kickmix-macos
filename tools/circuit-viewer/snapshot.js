// Playhead and snapshot for the circuit viewer.
// - Playhead: a pure state machine (op index, playing or paused, speed). Play and Pause keep the position; Stop is the
//   only action that rewinds. Tested under Node (tests/viewer/playhead.test.js).
// - analyse(): static facts about the op list: qubit lifetimes and the certified width profile, phase liabilities
//   (HMR records and the fix-ups that repay them), with the semantics of render.py's `width` and `ledger` verbs.
// - track() / at(): the tracer with a checkpoint every 1,000 ops, so any stop point is at most 1,000 ops of replay.
// - snapshot(): the stop point as one JSON object (schema kickmix-viewer-snapshot/1, documented in RENDER.md).
//   render.py's `snapshot` verb builds the same object headlessly; tests/test_circuit_render.py checks the two agree.
// Written with AI assistance (Claude, Anthropic) under the repository owner's direction.
(function (root) {
  const SCHEMA = 'kickmix-viewer-snapshot/1';
  const KINDS = ['X', 'CX', 'CCX', 'Z', 'CZ', 'CCZ', 'SWAP', 'CSWAP', 'R', 'HMR', 'NEG', 'Z_POW',
                 'BIT_INVERT', 'BIT_STORE0', 'BIT_STORE1', 'PUSH_CONDITION', 'POP_CONDITION'];
  const KIND_NAMES = ['contra', 'accrual', 'dead-tail'];
  const CHECKPOINT = 1000;
  const IDLE_K = 1000;
  const ZERO = { get: () => 0 };                     // HMR outcomes forced to 0: no kickback, no fix-ups
  const isTof = (c) => c === 2 || c === 5 || c === 7;

  // ---- operands of one op in the viewer's format (see kmx.js)
  function opQubits(o) {
    const c = o[0];
    if (c === 15 || c === 16 || c === 10 || c >= 12) return [];
    if (c === 11 || c === 9) return [o[1]];
    return o.slice(1, o.length - 1);
  }
  function opCond(o) { const c = o[0]; return c === 15 || c === 16 ? -1 : o[o.length - (c === 11 ? 2 : 1)]; }
  function opBits(o) { const c = o[0]; return c === 9 ? [o[2]] : (c >= 12 && c <= 15) ? [o[1]] : []; }
  function lowerBound(arr, v) { let lo = 0, hi = arr.length; while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] < v) lo = m + 1; else hi = m; } return lo; }
  function upperBound(arr, v) { let lo = 0, hi = arr.length; while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] <= v) lo = m + 1; else hi = m; } return lo; }

  // ---- playhead state machine
  // ctx: { n, lo, hi (the range played: the zoomed stage or the whole circuit), tof, meas, stages (sorted op lists),
  //        loop: 'once' | 'loop' | 'bounce' }. tick advances (hi - lo) * speed / 32 ops per second.
  const Playhead = {
    init() { return { op: 0, playing: false, speed: 4, dir: 1, acc: 0, active: false }; },
    reduce(s, a, ctx) {
      const n = ctx.n, lo = ctx.lo ?? 0, hi = ctx.hi ?? n;
      const clamp = (v) => Math.max(0, Math.min(n - 1, v));
      const jump = (op) => ({ ...s, op: clamp(op), playing: false, acc: 0, active: true });
      switch (a.type) {
        case 'play': {
          let op = s.op;
          if (op < lo || op >= hi) op = lo;
          else if (op >= hi - 1 && (ctx.loop || 'once') === 'once') op = lo;   // at the very end: play again
          return { ...s, op, playing: true, active: true, acc: 0 };
        }
        case 'pause': return { ...s, playing: false, acc: 0 };
        case 'toggle': return Playhead.reduce(s, { type: s.playing ? 'pause' : 'play' }, ctx);
        case 'stop': return { ...s, op: lo, playing: false, dir: 1, acc: 0, active: false };
        case 'speed': return { ...s, speed: Math.max(1, Math.min(10, +a.value || 1)) };
        case 'seek': return jump(Math.round(+a.op || 0));
        case 'step': return jump(s.op + (a.delta || 1));
        case 'nextTof': { const k = upperBound(ctx.tof, s.op); return k < ctx.tof.length ? jump(ctx.tof[k]) : jump(s.op); }
        case 'prevTof': { const k = lowerBound(ctx.tof, s.op) - 1; return k >= 0 ? jump(ctx.tof[k]) : jump(s.op); }
        case 'nextMeas': { const k = upperBound(ctx.meas, s.op); return k < ctx.meas.length ? jump(ctx.meas[k]) : jump(s.op); }
        case 'prevMeas': { const k = lowerBound(ctx.meas, s.op) - 1; return k >= 0 ? jump(ctx.meas[k]) : jump(s.op); }
        case 'nextStage': { const k = upperBound(ctx.stages, s.op); return k < ctx.stages.length ? jump(ctx.stages[k]) : jump(n - 1); }
        case 'prevStage': { const k = lowerBound(ctx.stages, s.op) - 1; return k >= 0 ? jump(ctx.stages[k]) : jump(0); }
        case 'tick': {
          if (!s.playing) return s;
          const span = Math.max(1, hi - lo);
          let acc = s.acc + Math.max(0, Math.min(0.25, +a.dt || 0)) * span * s.speed / 32;
          const adv = Math.floor(acc); acc -= adv;
          if (!adv) return { ...s, acc };
          const loop = ctx.loop || 'once';
          let op = s.op, dir = s.dir;
          if (op < lo || op >= hi) op = lo;
          if (loop === 'once') {
            op += adv;
            if (op >= hi - 1) return { ...s, op: hi - 1, playing: false, acc: 0 };
            return { ...s, op, acc };
          }
          if (loop === 'loop') return { ...s, op: lo + ((op - lo + adv) % span), acc };
          let left = adv;                                                   // back and forth
          while (left > 0 && span > 1) {
            const room = dir > 0 ? (hi - 1 - op) : (op - lo);
            if (left <= room) { op += dir * left; left = 0; }
            else { op += dir * room; left -= room; dir = -dir; }
          }
          return { ...s, op, dir, acc };
        }
        default: return s;
      }
    },
  };

  // ---- static analysis (render.py width and ledger semantics)
  // regs: register qubits (live for the whole circuit). A lifetime starts at a wire's first touch or its first touch
  // after an unconditional R/HMR at condition depth 0, and ends at such a reset or at the wire's last touch.
  function analyse(data, opts) {
    opts = opts || {};
    const ops = data.ops, n = ops.length;
    const nq = data.n_qubits || 63;
    const regList = new Set(data.x_qubits);
    for (const r of data.registers || []) for (const q of r.qubits) regList.add(q);
    const regs = [...regList].sort((a, b) => a - b), isReg = new Uint8Array(Math.max(nq, (regs[regs.length - 1] ?? -1) + 1));
    for (const q of regs) isReg[q] = 1;
    const xset = new Set(data.x_qubits);
    const opq = ops.map(opQubits);
    let maxq = isReg.length;
    for (const qs of opq) for (const q of qs) if (q + 1 > maxq) maxq = q + 1;
    // condition depth before each op, resets
    const reset = new Uint8Array(n);
    let depth = 0;
    for (let i = 0; i < n; i++) {
      const o = ops[i], c = o[0];
      if ((c === 8 || c === 9) && opCond(o) < 0 && depth === 0) reset[i] = 1;
      if (c === 15) depth++; else if (c === 16) depth--;
    }
    // touches per wire, in op order
    const touches = Array.from({ length: maxq }, () => []);
    for (let i = 0; i < n; i++) for (const q of opq[i]) touches[q].push(i);
    // liabilities (port of render.py bit_liabilities): one per HMR, followed through conditional bit copies
    const cur = new Map(), vLast = [], vReads = [], vP1 = [], vP2 = [], hmrV = [], hmrOp = [], hmrQ = [], fix = [];
    for (let i = 0; i < n; i++) {
      const o = ops[i], c = o[0];
      let x0 = -1, xc = -1;
      if (c === 15) xc = o[1];
      else if (c === 16) continue;
      else { xc = opCond(o); if (c === 9) x0 = o[2]; else if (c >= 12 && c <= 14) x0 = o[1]; }
      if (x0 < 0 && xc < 0) continue;
      const bitw = c >= 12 && c <= 14;
      if (xc >= 0 && !bitw) {
        const v = cur.get(xc);
        if (v !== undefined) { vLast[v] = i; vReads[v]++; fix.push(i); }
      }
      if (x0 < 0 || c === 15) continue;
      if (c === 9) {
        const old = xc >= 0 ? (cur.get(x0) ?? -1) : -1;
        cur.set(x0, vLast.length); hmrV.push(vLast.length); hmrOp.push(i); hmrQ.push(o[1]);
        vLast.push(-1); vReads.push(0); vP1.push(old); vP2.push(-1);
      } else if (xc >= 0) {
        const p1 = cur.get(x0) ?? -1, p2 = cur.get(xc) ?? -1;
        if (p1 < 0 && p2 < 0) continue;
        cur.set(x0, vLast.length); vLast.push(-1); vReads.push(0); vP1.push(p1); vP2.push(p2);
      } else if (c !== 12) cur.delete(x0);
    }
    const desc = vLast.slice();
    for (let v = desc.length - 1; v >= 0; v--) for (const p of [vP1[v], vP2[v]]) if (p >= 0 && desc[v] > desc[p]) desc[p] = desc[v];
    const liab = { op: hmrOp, qubit: hmrQ, settle: hmrV.map(v => desc[v]), reads: hmrV.map(v => vReads[v]) };
    const liabAt = new Map(hmrOp.map((op, j) => [op, j]));
    // values (lifetimes) and the width profile
    const V = { q: [], a: [], b: [], kind: [], reg: [], init: [] }, byWire = Array.from({ length: maxq }, () => []);
    const delta = new Int32Array(n + 1);
    for (let q = 0; q < maxq; q++) {
      const tl = touches[q]; let s = 0;
      for (let k = 0; k < tl.length; k++) {
        const last = k === tl.length - 1 || reset[tl[k]];
        if (!last) continue;
        const a = tl[s], b = tl[k], cnt = k - s + 1;
        let kind = 0;
        if (reset[b]) {
          if (ops[b][0] === 8) kind = 2;
          else { const j = liabAt.get(b); kind = j !== undefined && liab.settle[j] >= 0 ? 1 : 2; }
        }
        byWire[q].push(V.q.length);
        V.q.push(q); V.a.push(a); V.b.push(b); V.kind.push(kind); V.reg.push(isReg[q] ? 1 : 0); V.init.push(cnt === 1 && reset[b] ? 1 : 0);
        if (!isReg[q]) { delta[a]++; delta[b + 1]--; }
        s = k + 1;
      }
    }
    const profile = new Int16Array(n);
    let live = regs.length;
    for (let i = 0; i < n; i++) { live += delta[i]; profile[i] = live; }
    // static prefix counts: pre[k] = ops of that class in [0, k)
    const pre = { toffolis: new Int32Array(n + 1), rotations: new Int32Array(n + 1), measurements: new Int32Array(n + 1), resets: new Int32Array(n + 1) };
    const tof = [], meas = [];
    for (let i = 0; i < n; i++) {
      const c = ops[i][0];
      pre.toffolis[i + 1] = pre.toffolis[i] + (isTof(c) ? 1 : 0);
      pre.rotations[i + 1] = pre.rotations[i] + (c === 11 ? 1 : 0);
      pre.measurements[i + 1] = pre.measurements[i] + (c === 9 ? 1 : 0);
      pre.resets[i + 1] = pre.resets[i] + (c === 8 ? 1 : 0);
      if (isTof(c)) tof.push(i);
      if (c === 9) meas.push(i);
    }
    // open static debts after each op (HMR at or before op, repaid after it)
    const dd = new Int32Array(n + 1);
    for (let j = 0; j < hmrOp.length; j++) if (liab.settle[j] >= 0) { dd[hmrOp[j]]++; dd[liab.settle[j]]--; }
    const openDebts = new Int16Array(n); let od = 0;
    for (let i = 0; i < n; i++) { od += dd[i]; openDebts[i] = od; }
    const stages = (opts.stages || []).map((s, j) => ({ index: j, id: s.id, title: s.title, start: s.start, end: s.end }));
    const groups = opts.groups && opts.groups.length ? opts.groups.map(g => ({ name: g.name, qubits: g.qubits }))
      : [{ name: 'x', qubits: data.x_qubits }, ...(data.registers || []).filter(r => r.qubits !== data.x_qubits && (r.name || '') !== 'x')
          .map(r => ({ name: r.name || 'r' + r.r, qubits: r.qubits }))];
    return { n, ops, nq, maxq, regs, isReg, xset, opq, touches, values: V, byWire, profile, liab, liabAt, fix, pre, tof, meas,
             openDebts, stages, stageStarts: stages.map(s => s.start).filter((v, i, a) => a.indexOf(v) === i).sort((a, b) => a - b),
             groups, sha12: opts.sha12 ?? (data.source_kmb_sha256 ? data.source_kmb_sha256.slice(0, 12) : null) };
  }

  // ---- tracker: the tracer with checkpoints
  function freshD() { return { ones: 0, peak: 0, peakOp: -1, rotations: 0, measurements: 0, resets: 0 }; }
  function stepD(P, A, S, D, kicked) {
    const i = S.i, o = P.ops[i], c = o[0], qs = A.opq[i], q = S.q;
    let before = 0;
    for (let k = 0; k < qs.length; k++) before |= q[qs[k]] << k;
    const hq = c === 9 ? q[o[1]] : 0;
    root.InvsqrtTracer.exec(P, S);
    for (let k = 0; k < qs.length; k++) {
      const w = qs[k], was = (before >> k) & 1;
      if (q[w] !== was && !A.xset.has(w)) D.ones += q[w] ? 1 : -1;
    }
    if (D.ones > D.peak) { D.peak = D.ones; D.peakOp = i; }
    if (S.ran) {
      if (c === 11) D.rotations++;
      else if (c === 9) { D.measurements++; if (kicked && S.m === 1 && hq) kicked[i] = 1; }
      else if (c === 8) D.resets++;
    }
  }
  function track(P, A, x, seed, kick) {
    const T = root.InvsqrtTracer;
    const S = T.start(P, x, seed, kick ? null : ZERO), D = freshD();
    const cps = [], kicked = new Uint8Array(P.n);
    for (let i = 0; i < P.n; i++) {
      if (i % CHECKPOINT === 0) cps.push([T.clone(S), { ...D }]);
      stepD(P, A, S, D, kicked);
    }
    return { P, A, x, seed, kick, cps, kicked, final: { S, D } };
  }
  // State after op `op` has run, plus what op `op` saw before it ran.
  function at(tr, op) {
    const T = root.InvsqrtTracer, P = tr.P, A = tr.A;
    const [cs, cd] = tr.cps[Math.floor(op / CHECKPOINT)];
    const S = T.clone(cs), D = { ...cd };
    while (S.i < op) stepD(P, A, S, D, null);
    const o = P.ops[op], qs = A.opq[op], bits = opBits(o), cond = opCond(o);
    const before = { q: qs.map(w => S.q[w]), b: bits.map(w => S.b[w]), cond: cond >= 0 ? S.b[cond] : null,
                     depth: S.stack.length, open: S.nfalse === 0, phase: S.phase };
    stepD(P, A, S, D, null);
    return { S, D, before };
  }

  // ---- the snapshot object
  const wrap = (e) => ((e % 1) + 1.5) % 1 - 0.5;
  const turnsOf = (p) => Number(p >> 64n) / 2 ** 64;
  function snapshot(ctx, op) {
    const A = ctx.A, P = ctx.P, n = A.n, ops = A.ops, x = ctx.x;
    const trK = ctx.trK, trZ = ctx.trZ, mode = ctx.kick ? trK : trZ;
    const st = at(mode, op), sk = ctx.kick ? st : at(trK, op), sz = ctx.kick ? at(trZ, op) : st;
    const o = ops[op], c = o[0], qs = A.opq[op], bits = opBits(o), cond = opCond(o), S = st.S, D = st.D;
    const changedQ = qs.filter((w, k) => S.q[w] !== st.before.q[k]);
    const changedB = bits.filter((w, k) => S.b[w] !== st.before.b[k]);
    const kickedHere = c === 9 && S.ran === 1 && S.m === 1 && st.before.q[0] === 1;
    const stg = A.stages.find(s => op >= s.start && op < s.end) || null;
    const gate = { op, kind: KINDS[c], qubits: qs, bits, cond: cond >= 0 ? cond : null, cond_value: st.before.cond,
                   stack_depth: st.before.depth, stack_open: st.before.open, ran: S.ran === 1, toffoli_class: isTof(c),
                   outcome: c === 9 && S.ran === 1 ? S.m : null, kicked: kickedHere, angle_turns: c === 11 ? o[3] : null,
                   changed_qubits: changedQ, changed_bits: changedB, phase_changed: S.phase !== st.before.phase };
    // width: qubits holding 1 for this x, and the certified (lifetime) width
    let lpk = -1, lpkOp = null, gpk = -1, gpkOp = null;
    for (let i = 0; i < n; i++) { const v = A.profile[i]; if (v > gpk) { gpk = v; gpkOp = i; } if (i <= op && v > lpk) { lpk = v; lpkOp = i; } }
    const FD = mode.final.D;
    const width = {
      ones: { now: D.ones, peak_so_far: D.peak, peak_so_far_op: D.peakOp >= 0 ? D.peakOp : null, peak: FD.peak,
              peak_op: FD.peakOp >= 0 ? FD.peakOp : null, of: A.nq - A.xset.size },
      live: { now: A.profile[op], peak_so_far: lpk, peak_so_far_op: lpkOp, peak: gpk, peak_op: gpkOp, registers: A.regs.length } };
    // cost
    const so = { toffolis: S.tof, rotations: D.rotations, measurements: D.measurements, resets: D.resets };
    const tot = { toffolis: mode.final.S.tof, rotations: FD.rotations, measurements: FD.measurements, resets: FD.resets };
    const cls = ['toffolis', 'rotations', 'measurements', 'resets'];
    const obj = (f) => Object.fromEntries(cls.map(k => [k, f(k)]));
    const cost = { so_far: so, remaining: obj(k => tot[k] - so[k]), total: tot,
                   static_remaining: obj(k => A.pre[k][n] - A.pre[k][op + 1]), static_total: obj(k => A.pre[k][n]) };
    // phase
    const ph = S.phase, tgt = (1 / Math.sqrt(x)) % 1, tol = 2 ** -24 / Math.sqrt(x), err = wrap(turnsOf(ph) - tgt);
    const kb = (((sk.S.phase - sz.S.phase) % root.InvsqrtTracer.MOD) + root.InvsqrtTracer.MOD) % root.InvsqrtTracer.MOD;
    const phase = { outcomes: ctx.kick ? 'seeded' : 'zero', units: ph.toString(), turns: turnsOf(ph), target_turns: tgt,
                    error_turns: err, tolerance_turns: tol, share_of_tolerance: Math.abs(err) / tol,
                    without_kickback: { units: sz.S.phase.toString(), turns: turnsOf(sz.S.phase) }, kickback_turns: turnsOf(kb) };
    // registers
    const chg = new Set(changedQ);
    const registers = A.groups.map(g => {
      const vals = g.qubits.map(w => S.q[w]);
      return { name: g.name, qubits: g.qubits, value: g.qubits.length <= 53 ? vals.reduce((acc, v, j) => acc + v * 2 ** j, 0) : null,
               bits: vals.slice().reverse().join(''), changed: g.qubits.map((w, j) => chg.has(w) ? j : -1).filter(j => j >= 0) };
    });
    // idle wires: live after this op but not touched in the next k ops
    const k = ctx.idleK ?? IDLE_K, wires = [];
    for (let w = 0; w < A.maxq; w++) {
      const tl = A.touches[w], j = upperBound(tl, op), next = j < tl.length ? tl[j] : null;
      let live = !!A.isReg[w];
      if (!live) { const vs = A.byWire[w]; for (let t = vs.length - 1; t >= 0; t--) { const v = vs[t]; if (A.values.a[v] <= op) { live = A.values.b[v] > op; break; } } }
      if (live && (next === null || next - op > k)) wires.push({ wire: w, register: !!A.isReg[w], next_touch: next, idle_for: next === null ? null : next - op });
    }
    // ledger at the cut after this op
    const V = A.values, L = A.liab, cut = op + 1;
    const openV = [], closed = { contra: 0, accrual: 0, 'dead-tail': 0 };
    let opened = 0, closingHere = 0;
    for (let v = 0; v < V.q.length; v++) {
      if (V.reg[v]) continue;
      if (V.b[v] === op) closingHere++;
      if (V.init[v]) continue;
      if (V.a[v] < cut) opened++;
      if (V.b[v] < cut) closed[KIND_NAMES[V.kind[v]]]++;
      else if (V.a[v] < cut) openV.push({ wire: V.q[v], opened: V.a[v], closes: V.b[v], held: op - V.a[v], settlement: KIND_NAMES[V.kind[v]] });
    }
    openV.sort((p, q) => p.opened - q.opened || p.wire - q.wire);
    const debts = []; let records = 0, repaid = 0, never = 0;
    for (let j = 0; j < L.op.length; j++) {
      if (L.op[j] >= cut) continue;
      records++;
      if (L.settle[j] < 0) never++;
      else if (L.settle[j] < cut) repaid++;
      else debts.push({ wire: L.qubit[j], hmr: L.op[j], repaid_at: L.settle[j], fixups: L.reads[j], kicked: !!mode.kicked[L.op[j]] });
    }
    const fixSoFar = upperBound(A.fix, op);
    const tb = { live: A.profile[op], registers: A.regs.length, open_values: openV.length, closing_here: closingHere, opened, closed,
                 records, repaid, open_debts: debts.length, never_repaid: never, open_debts_kicked: debts.filter(d => d.kicked).length,
                 fixups_so_far: fixSoFar };
    const line = `live ${tb.live} = ${tb.registers} registers + ${tb.open_values} open values + ${tb.closing_here} closing here; ` +
                 `records ${records} = ${repaid} repaid + ${debts.length} open debts + ${never} never repaid`;
    const lh = openV.length ? openV[0] : null;
    return {
      schema: SCHEMA,
      circuit: { sha12: A.sha12, ops: n, qubits: P.nq, bits: P.nb, x_qubits: A.xset.size },
      x, seed: ctx.seed, kickback: !!ctx.kick, op,
      position: { op, ops: n, done: op + 1, stage: stg ? { index: stg.index, id: stg.id, title: stg.title, start: stg.start, end: stg.end } : null },
      gate, width, cost, phase, registers, idle: { k, wires },
      ledger: { trial_balance: tb, line, open_values: openV, open_debts: debts, longest_held: lh },
    };
  }

  root.CircuitSnapshot = { SCHEMA, KINDS, CHECKPOINT, IDLE_K, ZERO, Playhead, analyse, track, at, snapshot, opQubits, opCond, opBits };
})(typeof window !== 'undefined' ? window : globalThis);
