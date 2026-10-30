// Bit-level tracer for the invsqrt kickmix circuit (port of outputs/invsqrt-dataflow-2026-10-28/tracer.py).
// One computational-basis trajectory: one bit per qubit and classical bit, a condition stack,
// and an exact global phase in units of 2^-128 turn (BigInt). Semantics as validated against kickmix.
// The machine state is a plain object (start / exec / clone), so a caller can checkpoint it and replay
// from a checkpoint; run() is the whole circuit in one pass and gives exactly the same results.
(function (root) {
  const K = { X: 0, CX: 1, CCX: 2, Z: 3, CZ: 4, CCZ: 5, SWAP: 6, CSWAP: 7, R: 8, HMR: 9, NEG: 10, ZPOW: 11,
              BINV: 12, BST0: 13, BST1: 14, PUSH: 15, POP: 16 };
  const HALF = 1n << 127n, MOD = 1n << 128n, MASK = MOD - 1n;

  // Seeded PRNG for measurement outcomes (mulberry32), with its state in S.rs so a clone resumes it exactly.
  // Final values and phase do not depend on it.
  function rnd(S) {
    S.rs = (S.rs + 0x6D2B79F5) >>> 0;
    let t = S.rs;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) & 1;
  }

  function prepare(data) {
    const ops = data.ops, n = ops.length;
    const zp = new Map();
    for (const [k, v] of Object.entries(data.zpow_exact)) zp.set(+k, BigInt(v));
    return { ops, n, zp, nq: data.n_qubits || 63, nb: data.n_bits || 8288, xq: data.x_qubits, xw: data.x_qubits.length };
  }

  // Machine state before op 0 for input x. `outcomes` (optional, anything with get(op) -> bit) overrides the PRNG for HMR.
  function start(P, x, seed, outcomes) {
    const q = new Uint8Array(P.nq), b = new Uint8Array(P.nb);
    for (let k = 0; k < P.xw; k++) q[P.xq[k]] = Math.floor(x / 2 ** k) % 2;
    return { i: 0, q, b, stack: [], nfalse: 0, phase: 0n, rs: seed >>> 0, tof: 0, dirty: 0, outcomes: outcomes || null,
             t: 0, ran: 1, m: -1 };
  }
  function clone(S) {
    return { ...S, q: S.q.slice(), b: S.b.slice(), stack: S.stack.slice() };
  }

  // Run op S.i and advance. Afterwards S.t = 1 for a Toffoli-class gate that ran, S.ran = 0 if a condition
  // skipped it, S.m = the HMR outcome (-1 for any other op or a skipped HMR).
  function exec(P, S) {
    const i = S.i, o = P.ops[i], c = o[0], q = S.q, b = S.b;
    let t = 0, ran = 1, m = -1;
    if (c === K.PUSH) { const v = b[o[1]]; S.stack.push(v); if (!v) S.nfalse++; }
    else if (c === K.POP) { if (S.stack.length && !S.stack.pop()) S.nfalse--; }
    else {
      const cond = o[o.length - (c === K.ZPOW ? 2 : 1)];
      if (S.nfalse || (cond >= 0 && !b[cond])) ran = 0;
      else {
        switch (c) {
          case K.CX: if (q[o[1]]) q[o[2]] ^= 1; break;
          case K.CZ: if (q[o[1]] && q[o[2]]) S.phase ^= HALF; break;
          case K.Z: if (q[o[1]]) S.phase ^= HALF; break;
          case K.X: q[o[1]] ^= 1; break;
          case K.CCX: t = 1; if (q[o[1]] && q[o[2]]) q[o[3]] ^= 1; break;
          case K.HMR: {
            m = S.outcomes ? S.outcomes.get(i) : rnd(S);
            m = m ? 1 : 0;
            b[o[2]] = m;
            if (q[o[1]]) { if (m) S.phase ^= HALF; q[o[1]] = 0; }
            break;
          }
          case K.CSWAP: t = 1; if (q[o[1]]) { const s = q[o[2]]; q[o[2]] = q[o[3]]; q[o[3]] = s; } break;
          case K.R: if (q[o[1]]) { S.dirty++; if (rnd(S)) S.phase ^= HALF; q[o[1]] = 0; } break;
          case K.ZPOW: if (q[o[1]]) S.phase = (S.phase + P.zp.get(i)) & MASK; break;
          case K.CCZ: t = 1; if (q[o[1]] && q[o[2]] && q[o[3]]) S.phase ^= HALF; break;
          case K.NEG: S.phase ^= HALF; break;
          case K.SWAP: { const s = q[o[1]]; q[o[1]] = q[o[2]]; q[o[2]] = s; break; }
          case K.BINV: b[o[1]] ^= 1; break;
          case K.BST0: b[o[1]] = 0; break;
          case K.BST1: b[o[1]] = 1; break;
        }
        S.tof += t;
      }
    }
    S.t = t; S.ran = ran; S.m = m; S.i = i + 1;
  }

  // Run ops [0, n). onStep(i, q, phase, tofHere, ran) is called after each op when given (ran = 0 if a condition skipped it).
  // `outcomes` (optional Map op->bit) overrides the PRNG for HMR.
  function run(P, x, seed, onStep, outcomes) {
    const S = start(P, x, seed, outcomes);
    for (let i = 0; i < P.n; i++) {
      exec(P, S);
      if (onStep) onStep(i, S.q, S.phase, S.t, S.ran);
    }
    return { q: S.q, b: S.b, phase: S.phase, tof: S.tof, dirty: S.dirty };
  }

  const turns = (p) => Number(p >> 64n) / 2 ** 64; // phase as a float in [0, 1)
  root.InvsqrtTracer = { K, prepare, start, exec, clone, run, turns, HALF, MOD };
})(typeof window !== 'undefined' ? window : globalThis);
