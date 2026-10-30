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
const { View, Gesture, Pins, Trace, Follow } = globalThis.CircuitView;

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

test('follow, continuous: the playhead stays at the anchor and the window slides under it', () => {
  const f = Follow.init('c'), v = { a: 10000, b: 10200 }, s = 200;
  assert.equal(Follow.ANCHOR, 0.3);
  for (const pos of [10100, 10100.25, 10133.7, 20000]) {             // fractional positions slide the window smoothly
    const w = Follow.playing(f, v, pos, N, 1);
    assert.ok(near(w.b - w.a, s));
    assert.ok(near(View.pxOf(w, pos + 0.5, W), Follow.ANCHOR * W), `pos ${pos}`);
  }
  const w1 = Follow.playing(f, v, 10100, N, 1), w2 = Follow.playing(f, w1, 10100.5, N, 1);
  assert.ok(near(w2.a - w1.a, 0.5));                                 // half an op of play moves the window half an op
  assert.equal(Follow.playing(f, v, 10059.5, N, 1), v);              // already there: the same object (no relayout)
  // near the start the window stops at op 0 and the playhead moves instead; likewise at the end
  for (const pos of [0, 10, 59]) assert.deepEqual(Follow.playing(f, v, pos, N, 1), { a: 0, b: s });
  assert.ok(near(View.pxOf({ a: 0, b: s }, 10.5, W), 10.5 / s * W));
  for (const pos of [N - 1, N - 50, N - 140]) assert.deepEqual(Follow.playing(f, v, pos, N, 1), { a: N - s, b: N });
  const mid = Follow.playing(f, v, N - 141, N, 1);                    // the last position that still slides
  assert.ok(mid.a < N - s && mid.b <= N);
  // the whole circuit visible: no mode moves anything
  for (const m of Follow.MODES) assert.deepEqual(Follow.playing(Follow.init(m), View.full(N), 30000, N, 1), View.full(N));
  // off: the playhead can leave the view
  assert.equal(Follow.playing(Follow.init('off'), v, 30000, N, 1), v);
});

test('follow, page: a new page at the edge with a lead-in, clamped at both ends', () => {
  const f = Follow.init('p'), v = { a: 10000, b: 10200 }, s = 200, lead = Follow.LEAD * s;
  assert.equal(Follow.playing(f, v, 10000, N, 1), v);                  // inside: unchanged
  assert.equal(Follow.playing(f, v, 10199.9, N, 1), v);                // the last op is still on the page
  const p = Follow.playing(f, v, 10200, N, 1);                         // reaches the edge: turn the page
  assert.deepEqual(p, { a: 10200 - lead, b: 10200 - lead + s });
  assert.ok(lead >= 1 && View.pxOf(p, 10200, W) > 0);                  // the playhead does not sit on the left edge
  assert.equal(Follow.page({ a: 0, b: 10 }, 10, N, 1).a, 9);           // a lead-in of at least one op
  assert.deepEqual(Follow.playing(f, v, 3, N, 1), { a: 0, b: s });     // a loop back to the start: page 0
  assert.deepEqual(Follow.playing(f, v, N - 2, N, 1), { a: N - s, b: N });   // the last page ends at the last op
  const back = Follow.playing(f, v, 9999, N, -1);                      // playing backwards: lead-in from the right
  assert.deepEqual(back, { a: 9999 + 1 + lead - s, b: 9999 + 1 + lead });
  assert.ok(View.pxOf(back, 10000, W) < W);
});

test('follow: paused steps re-place the window only off-screen or near an edge', () => {
  const c = Follow.init('c'), p = Follow.init('p'), v = { a: 10000, b: 10200 }, e = Follow.EDGE * 200;
  for (const op of [10000 + e, 10100, 10199 - e]) assert.equal(Follow.stepped(c, v, op, N, 1), v, `op ${op}`);
  for (const op of [10000, 10000 + e - 1, 10199, 10199 - e + 1, 5000, 30000]) {
    const w = Follow.stepped(c, v, op, N, 1);
    assert.ok(near(View.pxOf(w, op + 0.5, W), Follow.ANCHOR * W), `op ${op}`);
  }
  assert.deepEqual(Follow.stepped(c, v, 3, N, -1), { a: 0, b: 200 });  // clamped at the start
  assert.equal(Follow.stepped(p, v, 10199, N, 1), v);                  // page: on-screen stays
  assert.equal(Follow.stepped(p, v, 10200, N, 1).a, 10200 - Follow.LEAD * 200);
  assert.equal(Follow.stepped(Follow.init('off'), v, 30000, N, 1), v);
  assert.deepEqual(Follow.stepped(c, View.full(N), 30000, N, 1), View.full(N));
});

test('follow: manual pan or zoom while playing suspends; F, Play or a mode re-engages', () => {
  let f = Follow.init();
  assert.deepEqual(f, { mode: 'c', suspended: false });
  assert.equal(Follow.reduce(f, { type: 'manual', playing: false }), f);    // panning while paused: still following
  f = Follow.reduce(f, { type: 'manual', playing: true });
  assert.deepEqual(f, { mode: 'c', suspended: true });
  assert.equal(Follow.active(f), false);
  const v = { a: 10000, b: 10200 };
  assert.equal(Follow.playing(f, v, 30000, N, 1), v);                       // suspended: the view stays put
  assert.equal(Follow.stepped(f, v, 30000, N, 1), v);
  assert.equal(Follow.reduce(f, { type: 'manual', playing: true }), f);
  const g = Follow.reduce(f, { type: 'toggle' });                          // F or the Follow button: catch up
  assert.deepEqual(g, { mode: 'c', suspended: false });
  assert.ok(near(View.pxOf(Follow.playing(g, v, 30000, N, 1), 30000.5, W), Follow.ANCHOR * W));
  assert.deepEqual(Follow.reduce(g, { type: 'toggle' }), { mode: 'c', suspended: true });   // and off again
  assert.deepEqual(Follow.reduce(f, { type: 'play' }), { mode: 'c', suspended: false });
  assert.deepEqual(Follow.reduce(f, { type: 'mode', mode: 'p' }), { mode: 'p', suspended: false });
  let o = Follow.init('off');
  assert.equal(Follow.reduce(o, { type: 'manual', playing: true }), o);    // nothing to suspend
  assert.deepEqual(Follow.reduce(o, { type: 'toggle', last: 'p' }), { mode: 'p', suspended: false });
  assert.deepEqual(Follow.reduce(o, { type: 'toggle' }), { mode: 'c', suspended: false });
  assert.deepEqual(Follow.init('bogus'), { mode: 'c', suspended: false });
});

test('follow URL round-trip', () => {
  assert.equal(Follow.DEFAULT, 'c');
  for (const m of Follow.MODES) assert.equal(Follow.parse(Follow.format(m) || 'c'), m);
  assert.equal(Follow.format('c'), '');                                    // the default stays out of the address bar
  assert.equal(Follow.format('p'), 'p');
  assert.equal(Follow.format('off'), 'off');
  assert.equal(Follow.parse('Continuous'), 'c');
  assert.equal(Follow.parse(' page '), 'p');
  assert.equal(Follow.parse('OFF'), 'off');
  for (const bad of [null, undefined, '', 'x', 'cp', '1']) assert.equal(Follow.parse(bad), null, String(bad));
  const sp = new URLSearchParams('op=5&follow=p&view=100-300');
  assert.equal(Follow.parse(sp.get('follow')), 'p');
  assert.equal(Follow.parse(new URLSearchParams('op=5').get('follow')), null);   // absent: the caller keeps the default
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
