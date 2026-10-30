// Zoom window, gestures, pinned qubits and the per-op trace of the circuit viewer (tools/circuit-viewer/view.js).
// Run: node --test tests/viewer/view.test.js   (tests/test_circuit_render.py runs every tests/viewer/*.test.js).
// Written with AI assistance (Claude, Anthropic) under the repository owner's direction.
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs'), path = require('path');
const VIEWER = path.resolve(__dirname, '..', '..', 'tools', 'circuit-viewer');
require(path.join(VIEWER, 'tracer.js'));
require(path.join(VIEWER, 'snapshot.js'));
require(path.join(VIEWER, 'view.js'));
const T = globalThis.InvsqrtTracer, CS = globalThis.CircuitSnapshot;
const { View, Gesture, Pins, Trace } = globalThis.CircuitView;

const N = 47248, W = 1000, MIN = View.minSpan(N, W);
const near = (a, b, eps = 1e-6) => Math.abs(a - b) <= eps;

test('the view window: full, minimum span and clamping', () => {
  assert.deepEqual(View.full(N), { a: 0, b: N });
  assert.ok(View.isFull(View.full(N), N));
  assert.equal(MIN, Math.ceil(W / View.MAX_PX_PER_OP));
  assert.equal(View.minSpan(10, 1000), 10);                       // never wider than the circuit
  assert.deepEqual(View.clamp({ a: -50, b: 100 }, N, MIN), { a: 0, b: 150 });              // slid back in, span kept
  assert.deepEqual(View.clamp({ a: N - 10, b: N + 90 }, N, MIN), { a: N - 100, b: N });
  assert.deepEqual(View.clamp({ a: 100, b: 101 }, N, MIN), { a: 100, b: 100 + MIN });      // too narrow: widened
  assert.deepEqual(View.clamp({ a: -5, b: 2 * N }, N, MIN), { a: 0, b: N });               // too wide: the whole circuit
});

test('zooming at a cursor keeps the op under it in place', () => {
  const v = { a: 10000, b: 20000 }, px = 250, anchor = View.opAt(v, px, W);
  const z = View.zoomAt(v, 2, anchor, N, MIN);
  assert.ok(near(z.b - z.a, 5000));
  assert.ok(near(View.opAt(z, px, W), anchor));
  const back = View.zoomAt(z, 0.5, anchor, N, MIN);                 // and zooming out again returns
  assert.ok(near(back.a, v.a) && near(back.b, v.b));
  // the deepest zoom stops at the minimum span, still anchored
  let d = v; for (let k = 0; k < 40; k++) d = View.zoomAt(d, 2, anchor, N, MIN);
  assert.ok(near(d.b - d.a, MIN));
  assert.ok(near(View.opAt(d, px, W), anchor, 1e-6));
  // zooming out at an edge clamps to the circuit
  const e = View.zoomAt({ a: 0, b: 1000 }, 0.01, 0, N, MIN);
  assert.deepEqual(e, { a: 0, b: N });
  // without an anchor, about the centre
  const c = View.zoomAt({ a: 0, b: 1000 }, 2, null, N, MIN);
  assert.deepEqual(c, { a: 250, b: 750 });
});

test('panning, keeping an op visible, and integer columns', () => {
  assert.deepEqual(View.pan({ a: 100, b: 200 }, 50, N), { a: 150, b: 250 });
  assert.deepEqual(View.pan({ a: 100, b: 200 }, -500, N), { a: 0, b: 100 });
  assert.deepEqual(View.pan({ a: 100, b: 200 }, 1e9, N), { a: N - 100, b: N });
  const v = { a: 100, b: 200 };
  assert.equal(View.reveal(v, 150, N), v);
  assert.deepEqual(View.reveal(v, 1000, N), { a: 950.5, b: 1050.5 });
  assert.deepEqual(View.columns({ a: 0, b: N }, N, 1000), { ia: 0, ib: N, cols: 1000 });
  assert.deepEqual(View.columns({ a: 10.5, b: 60.25 }, N, 1000), { ia: 10, ib: 61, cols: 51 });   // one column per op
});

test('pinch: the op under the fingers follows the fingers', () => {
  const v0 = { a: 10000, b: 20000 };
  const z = View.pinch(v0, 400, 600, 300, 700, W, N, MIN);          // fingers twice as far apart, same midpoint
  assert.ok(near(z.b - z.a, 5000));
  assert.ok(near(View.opAt(z, 500, W), View.opAt(v0, 500, W)));
  const p = View.pinch(v0, 400, 600, 500, 700, W, N, MIN);          // same distance, moved right: a pan
  assert.ok(near(p.b - p.a, 10000));
  assert.ok(near(View.opAt(p, 600, W), View.opAt(v0, 500, W)));
  const s = View.pinch(v0, 400, 600, 499, 501, W, N, MIN);          // fingers almost touching: span unchanged
  assert.ok(near(s.b - s.a, 10000));
});

test('wheel: ctrl zooms at the cursor, sideways and shift pan, plain wheel scrolls the page unless zoomed', () => {
  const full = View.full(N);
  const z = View.wheel(full, { deltaY: -100, ctrlKey: true }, 250, W, N, MIN);
  assert.ok(z.consumed && z.view.b - z.view.a < N);
  assert.ok(near(View.opAt(z.view, 250, W), View.opAt(full, 250, W)));
  assert.deepEqual(View.wheel(full, { deltaY: 100 }, 250, W, N, MIN), { view: full, consumed: false });
  const v = { a: 1000, b: 2000 };
  let r = View.wheel(v, { deltaX: 100, deltaY: 3 }, 0, W, N, MIN);
  assert.ok(r.consumed); assert.deepEqual(r.view, { a: 1100, b: 2100 });
  r = View.wheel(v, { deltaY: 100, shiftKey: true }, 0, W, N, MIN);
  assert.deepEqual(r.view, { a: 1100, b: 2100 });
  r = View.wheel(v, { deltaY: 3, deltaMode: 1 }, 0, W, N, MIN);      // lines: 16 px each
  assert.deepEqual(r.view, { a: 1048, b: 2048 });
  r = View.wheel({ a: 0, b: 1000 }, { deltaY: -100 }, 0, W, N, MIN);  // at the start, scrolling up scrolls the page
  assert.equal(r.consumed, false);
  r = View.wheel(v, { deltaY: 100, metaKey: true }, 500, W, N, MIN);  // cmd + wheel zooms out
  assert.ok(r.view.b - r.view.a > 1000);
});

test('view URL round-trip', () => {
  assert.equal(View.format(View.full(N), N), '');
  assert.equal(View.format({ a: 1200.4, b: 3400.6 }, N), '1200-3401');
  assert.deepEqual(View.parse('1200-3401', N, MIN), { a: 1200, b: 3401 });
  assert.deepEqual(View.parse(View.format({ a: 5, b: 9000 }, N), N, MIN), { a: 5, b: 9000 });
  assert.deepEqual(View.parse('100-101', N, MIN), { a: 100, b: 100 + MIN });
  assert.deepEqual(View.parse('47000-99999', N, MIN), { a: 47000, b: N });
  for (const bad of ['', 'x', '5-5', '9-3', '-1-4', `${N}-${N + 9}`, '1e3-2e3']) assert.equal(View.parse(bad, N, MIN), null, bad);
});

test('gestures: a tap, a drag and a two-finger pinch', () => {
  const ctx = { w: W, n: N, minSpan: MIN }, v0 = { a: 10000, b: 20000 };
  let g = Gesture.down(Gesture.init(), 1, 300, 50, v0);
  let m = Gesture.move(g, 1, 302, 51, ctx);                          // under the drag threshold: still a tap
  assert.equal(m.view, null);
  let u = Gesture.up(m.g, 1, v0);
  assert.equal(u.tap, true);
  g = Gesture.down(Gesture.init(), 1, 300, 50, v0);                 // drag 100 px left: pan right by a tenth
  m = Gesture.move(g, 1, 200, 50, ctx);
  assert.deepEqual(m.view, { a: 11000, b: 21000 });
  u = Gesture.up(m.g, 1, m.view);
  assert.equal(u.tap, false);
  g = Gesture.down(Gesture.init(), 1, 400, 50, v0);                 // pinch out
  g = Gesture.down(g, 2, 600, 50, v0);
  m = Gesture.move(g, 1, 300, 50, ctx); g = m.g;
  m = Gesture.move(g, 2, 700, 50, ctx); g = m.g;
  assert.ok(near(m.view.b - m.view.a, 5000));
  assert.ok(near(View.opAt(m.view, 500, W), 15000));
  let v = m.view;
  u = Gesture.up(g, 2, v); g = u.g;                                  // one finger lifts: the other keeps panning
  assert.equal(u.tap, false);
  m = Gesture.move(g, 1, 350, 50, ctx);
  assert.ok(near(m.view.a, v.a - 50 / W * (v.b - v.a)));
  u = Gesture.up(m.g, 1, m.view);
  assert.equal(u.tap, false);
  assert.equal(u.g.ptrs.size, 0);
  assert.equal(Gesture.move(u.g, 7, 1, 1, ctx).view, null);         // an unknown pointer does nothing
});

test('pinned qubits: toggle, cap, URL round-trip', () => {
  let p = [];
  p = Pins.toggle(p, 14); p = Pins.toggle(p, 51);
  assert.deepEqual(p, [14, 51]);
  assert.equal(Pins.format(p), '14,51');
  assert.deepEqual(Pins.parse(Pins.format(p), 62), p);
  assert.deepEqual(Pins.toggle(p, 14), [51]);                        // clicking again clears
  for (const q of [1, 2, 3]) p = Pins.toggle(p, q);
  assert.deepEqual(p, [51, 1, 2, 3]);                                // a fifth drops the oldest
  assert.equal(p.length, Pins.MAX);
  assert.deepEqual(Pins.parse('3, 3,99,x,7,,8,9,10', 62), [3, 7, 8, 9]);
  assert.deepEqual(Pins.parse('', 62), []);
  assert.deepEqual(Pins.parse(null, 62), []);
});

// The bins the viewer drew before zooming existed (one tracer pass per window), as a reference for Trace.bin.
function oldBins(P, opq, isMeas, xq, x, seed, a, b, cols) {
  const NQ = P.nq, span = b - a, xset = new Set(xq);
  const bits = new Uint8Array(cols * NQ), tof = new Uint16Array(cols), meas = new Uint16Array(cols), skip = new Uint16Array(cols);
  const ph = new Float64Array(cols), occ = new Uint16Array(cols), pchg = new Uint16Array(cols);
  const mirror = new Uint8Array(NQ); for (let k = 0; k < xq.length; k++) mirror[xq[k]] = Math.floor(x / 2 ** k) % 2;
  let live = 0, cur = -1, lastP = 0n;
  T.run(P, x, seed, (i, q, phase, t, ran) => {
    for (const k of opq[i]) if (q[k] !== mirror[k]) { if (!xset.has(k)) live += q[k] ? 1 : -1; mirror[k] = q[k]; }
    if (i < a || i >= b) { lastP = phase; return; }
    const c = Math.min(cols - 1, Math.floor((i - a) * cols / span));
    if (c !== cur) { bits.set(q, c * NQ); cur = c; } else for (const k of opq[i]) bits[c * NQ + k] |= q[k];
    tof[c] += t; if (ran && isMeas[i]) meas[c]++; if (!ran) skip[c]++;
    if (phase !== lastP) { pchg[c]++; lastP = phase; }
    if (live > occ[c]) occ[c] = live; ph[c] = T.turns(phase);
  });
  return { bits, tof, meas, skip, ph, occ, pchg };
}

test('re-binning the stored trace gives the same columns as a fresh tracer pass', () => {
  const data = JSON.parse(fs.readFileSync(path.join(VIEWER, 'ops.json'), 'utf8'));
  const P = T.prepare(data), opq = data.ops.map(CS.opQubits), isMeas = data.ops.map(o => o[0] === 8 || o[0] === 9);
  for (const x of [161, 3133452]) {
    const t0 = process.hrtime.bigint();
    const tr = Trace.run(T, P, opq, data.x_qubits, x, 1, undefined, new Set([0, 1000, P.n]));
    const ms = Number(process.hrtime.bigint() - t0) / 1e6;
    assert.ok(ms < 3000, `trace took ${ms} ms`);
    assert.equal(T.turns(tr.r.phase), tr.turns[P.n - 1]);
    for (const [a, b, cols] of [[0, P.n, 1200], [14000, 15000, 1000], [18190, 18240, 50], [P.n - 37, P.n, 37]]) {
      const want = oldBins(P, opq, isMeas, data.x_qubits, x, 1, a, b, cols);
      const t1 = process.hrtime.bigint();
      const got = Trace.bin(tr, opq, isMeas, a, b, cols);
      const binMs = Number(process.hrtime.bigint() - t1) / 1e6;
      if (a === 0) assert.ok(binMs < 500, `whole-circuit re-bin took ${binMs} ms`);
      for (const k of Object.keys(want)) assert.deepEqual(Array.from(got[k]), Array.from(want[k]), `${k} x=${x} [${a},${b})`);
    }
    // the state after every op, spot-checked against the tracer
    const probe = new Set([0, 1, 777, 18201, P.n - 1]);
    T.run(P, x, 1, (i, q) => { if (probe.has(i)) for (let k = 0; k < P.nq; k++) assert.equal(Trace.bit(tr, i, k), q[k], `op ${i} q${k}`); });
    assert.equal(tr.snaps.get(P.n).x, x);                           // x handed back
  }
});
