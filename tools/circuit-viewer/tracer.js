// Bit-level tracer for the invsqrt kickmix circuit (port of outputs/invsqrt-dataflow-2026-10-28/tracer.py).
// One computational-basis trajectory: one bit per qubit and classical bit, a condition stack,
// and an exact global phase in units of 2^-128 turns (BigInt). Semantics as validated against kickmix.
(function (root) {
  const K = { X: 0, CX: 1, CCX: 2, Z: 3, CZ: 4, CCZ: 5, SWAP: 6, CSWAP: 7, R: 8, HMR: 9, NEG: 10, ZPOW: 11,
              BINV: 12, BST0: 13, BST1: 14, PUSH: 15, POP: 16 };
  const HALF = 1n << 127n, MOD = 1n << 128n, MASK = MOD - 1n;

  // Seeded PRNG for measurement outcomes (mulberry32). Final values and phase do not depend on it.
  function rng32(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) & 1;
    };
  }

  function prepare(data) {
    const ops = data.ops, n = ops.length;
    const zp = new Map();
    for (const [k, v] of Object.entries(data.zpow_exact)) zp.set(+k, BigInt(v));
    return { ops, n, zp, nq: data.n_qubits || 63, nb: data.n_bits || 8288, xq: data.x_qubits, xw: data.x_qubits.length };
  }

  // Run ops [0, n). onStep(i, q, phase, tofHere, ran) is called after each op when given (ran = 0 if a condition skipped it).
  // `outcomes` (optional Map op->bit) overrides the PRNG for HMR.
  function run(P, x, seed, onStep, outcomes) {
    const q = new Uint8Array(P.nq), b = new Uint8Array(P.nb);
    for (let k = 0; k < P.xw; k++) q[P.xq[k]] = Math.floor(x / 2 ** k) % 2;
    const stack = []; let nfalse = 0; let phase = 0n;
    const rnd = rng32(seed);
    let tof = 0, dirty = 0;
    const ops = P.ops;
    for (let i = 0; i < P.n; i++) {
      const o = ops[i], c = o[0];
      let t = 0, ran = 1;
      if (c === K.PUSH) { const v = b[o[1]]; stack.push(v); if (!v) nfalse++; }
      else if (c === K.POP) { if (stack.length && !stack.pop()) nfalse--; }
      else {
        const cond = o[o.length - (c === K.ZPOW ? 2 : 1)];
        if (nfalse || (cond >= 0 && !b[cond])) ran = 0;
        else {
          switch (c) {
            case K.CX: if (q[o[1]]) q[o[2]] ^= 1; break;
            case K.CZ: if (q[o[1]] && q[o[2]]) phase ^= HALF; break;
            case K.Z: if (q[o[1]]) phase ^= HALF; break;
            case K.X: q[o[1]] ^= 1; break;
            case K.CCX: t = 1; if (q[o[1]] && q[o[2]]) q[o[3]] ^= 1; break;
            case K.HMR: {
              const m = outcomes ? outcomes.get(i) : rnd();
              b[o[2]] = m;
              if (q[o[1]]) { if (m) phase ^= HALF; q[o[1]] = 0; }
              break;
            }
            case K.CSWAP: t = 1; if (q[o[1]]) { const s = q[o[2]]; q[o[2]] = q[o[3]]; q[o[3]] = s; } break;
            case K.R: if (q[o[1]]) { dirty++; if (rnd()) phase ^= HALF; q[o[1]] = 0; } break;
            case K.ZPOW: if (q[o[1]]) phase = (phase + P.zp.get(i)) & MASK; break;
            case K.CCZ: t = 1; if (q[o[1]] && q[o[2]] && q[o[3]]) phase ^= HALF; break;
            case K.NEG: phase ^= HALF; break;
            case K.SWAP: { const s = q[o[1]]; q[o[1]] = q[o[2]]; q[o[2]] = s; break; }
            case K.BINV: b[o[1]] ^= 1; break;
            case K.BST0: b[o[1]] = 0; break;
            case K.BST1: b[o[1]] = 1; break;
          }
          tof += t;
        }
      }
      if (onStep) onStep(i, q, phase, t, ran);
    }
    return { q, phase, tof, dirty };
  }

  const turns = (p) => Number(p >> 64n) / 2 ** 64; // phase as a float in [0, 1)
  root.InvsqrtTracer = { K, prepare, run, turns, HALF, MOD };
})(typeof window !== 'undefined' ? window : globalThis);
