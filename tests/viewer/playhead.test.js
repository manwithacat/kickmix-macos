// Playhead state machine and checkpointed replay of the circuit viewer (tools/circuit-viewer/snapshot.js).
// Run: node --test tests/viewer/playhead.test.js   (name the files: older Node treats a directory as a module path;
//      tests/test_circuit_render.py runs every tests/viewer/*.test.js when Node is installed).
// Written with AI assistance (Claude, Anthropic) under the repository owner's direction.
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs'), path = require('path');
const VIEWER = path.resolve(__dirname, '..', '..', 'tools', 'circuit-viewer');
require(path.join(VIEWER, 'tracer.js'));
require(path.join(VIEWER, 'snapshot.js'));
const T = globalThis.InvsqrtTracer, CS = globalThis.CircuitSnapshot, PH = CS.Playhead;

const ctx = { n: 1000, lo: 0, hi: 1000, tof: [10, 20, 500], meas: [15, 600], stages: [0, 100, 400], loop: 'once' };
const R = (s, a, c = ctx) => PH.reduce(s, a, c);

test('play then pause keeps the position; only stop rewinds', () => {
  let s = PH.init();
  s = R(s, { type: 'play' });
  assert.equal(s.playing, true);
  s = R(s, { type: 'tick', dt: 0.25 });                         // 1000 ops * speed 4 / 32 per second
  assert.equal(s.op, 31);
  s = R(s, { type: 'pause' });
  assert.deepEqual([s.op, s.playing], [31, false]);
  s = R(s, { type: 'play' });
  assert.equal(s.op, 31);                                        // resume where it paused
  s = R(s, { type: 'tick', dt: 0.1 });
  assert.ok(s.op > 31);
  s = R(s, { type: 'stop' });
  assert.deepEqual([s.op, s.playing, s.active], [0, false, false]);
});

test('toggle is play/pause', () => {
  let s = R(PH.init(), { type: 'toggle' });
  assert.equal(s.playing, true);
  s = R(s, { type: 'tick', dt: 0.2 });
  const op = s.op;
  s = R(s, { type: 'toggle' });
  assert.deepEqual([s.playing, s.op], [false, op]);
});

test('ticks while paused do nothing; fractional progress accumulates', () => {
  let s = R(PH.init(), { type: 'seek', op: 5 });
  assert.equal(R(s, { type: 'tick', dt: 0.2 }), s);
  s = R({ ...s, speed: 1 }, { type: 'play' });
  for (let k = 0; k < 10; k++) s = R(s, { type: 'tick', dt: 0.01 });  // 31.25 ops/s: 0.3125 ops per tick
  assert.equal(s.op, 5 + 3);
});

test('stepping and jumps pause and clamp to the circuit', () => {
  let s = R(PH.init(), { type: 'play' });
  s = R(s, { type: 'step', delta: 1 });
  assert.deepEqual([s.op, s.playing, s.active], [1, false, true]);
  s = R(s, { type: 'step', delta: -5 });
  assert.equal(s.op, 0);
  s = R(s, { type: 'seek', op: 5000 });
  assert.equal(s.op, 999);
});

test('Toffoli, measurement and stage navigation', () => {
  let s = R(PH.init(), { type: 'seek', op: 12 });
  assert.equal(R(s, { type: 'nextTof' }).op, 20);
  assert.equal(R(s, { type: 'prevTof' }).op, 10);
  assert.equal(R(R(s, { type: 'seek', op: 10 }), { type: 'prevTof' }).op, 10);   // none before: stays
  assert.equal(R(s, { type: 'nextMeas' }).op, 15);
  assert.equal(R(R(s, { type: 'seek', op: 600 }), { type: 'nextMeas' }).op, 600);
  assert.equal(R(s, { type: 'nextStage' }).op, 100);
  assert.equal(R(R(s, { type: 'seek', op: 100 }), { type: 'prevStage' }).op, 0);
  assert.equal(R(R(s, { type: 'seek', op: 150 }), { type: 'prevStage' }).op, 100);
  assert.equal(R(R(s, { type: 'seek', op: 450 }), { type: 'nextStage' }).op, 999);
});

test('repeat modes: once stops at the end, loop wraps, back and forth reflects', () => {
  let s = R(R(PH.init(), { type: 'seek', op: 990 }), { type: 'play' });
  s = R(s, { type: 'tick', dt: 0.25 });
  assert.deepEqual([s.op, s.playing], [999, false]);
  s = R(s, { type: 'play' });                                        // at the very end, play starts again
  assert.equal(s.op, 0);
  const loop = { ...ctx, loop: 'loop' };
  s = R(R(R(PH.init(), { type: 'seek', op: 990 }, loop), { type: 'play' }, loop), { type: 'tick', dt: 0.25 }, loop);
  assert.deepEqual([s.op, s.playing], [(990 + 31) % 1000, true]);
  const bounce = { ...ctx, loop: 'bounce' };
  s = R(R(R(PH.init(), { type: 'seek', op: 990 }, bounce), { type: 'play' }, bounce), { type: 'tick', dt: 0.25 }, bounce);
  assert.deepEqual([s.op, s.dir], [999 - (31 - 9), -1]);
});

test('playing inside a zoomed stage stays inside it', () => {
  const z = { ...ctx, lo: 100, hi: 400, loop: 'loop' };
  let s = R(R(PH.init(), { type: 'seek', op: 50 }), { type: 'play' }, z);
  assert.equal(s.op, 100);
  for (let k = 0; k < 40; k++) { s = R(s, { type: 'tick', dt: 0.25 }, z); assert.ok(s.op >= 100 && s.op < 400); }
});

test('replay from a checkpoint equals a straight run, forwards and backwards', () => {
  const data = JSON.parse(fs.readFileSync(path.join(VIEWER, 'ops.json'), 'utf8'));
  const P = T.prepare(data), A = CS.analyse(data, {});
  for (const kick of [true, false]) {
    const tr = CS.track(P, A, 161, 1, kick);
    const want = new Map();
    T.run(P, 161, 1, (i, q, phase) => { if (i % 3001 === 7 || i === P.n - 1) want.set(i, Buffer.from(q).toString('hex') + phase); }, kick ? undefined : CS.ZERO);
    const opsWanted = [...want.keys()].sort((a, b) => b - a);           // backwards: every stop is a fresh replay
    for (const op of opsWanted) {
      const { S } = CS.at(tr, op);
      assert.equal(Buffer.from(S.q).toString('hex') + S.phase, want.get(op), `op ${op} kick ${kick}`);
    }
  }
});

test('kickback off: same final phase and qubits', () => {
  for (const dir of ['', 'circuits/9a8627/', 'circuits/0766a111/']) {
    const data = JSON.parse(fs.readFileSync(path.join(VIEWER, dir, 'ops.json'), 'utf8'));
    const P = T.prepare(data);
    for (const x of [1, 161, 3133452]) {
      const a = T.run(P, x, 1), b = T.run(P, x, 1, null, CS.ZERO);
      assert.equal(a.phase, b.phase, `${dir} x=${x}`);
      assert.deepEqual(Buffer.from(a.q), Buffer.from(b.q));
    }
  }
});

test('a stop point costs at most one checkpoint of replay', () => {
  const data = JSON.parse(fs.readFileSync(path.join(VIEWER, 'ops.json'), 'utf8'));
  const P = T.prepare(data), A = CS.analyse(data, {}), tr = CS.track(P, A, 161, 1, true);
  assert.equal(tr.cps.length, Math.ceil(P.n / CS.CHECKPOINT));
  const t0 = process.hrtime.bigint();
  for (let op = P.n - 1; op > P.n - 200; op--) CS.at(tr, op);       // stepping backwards 200 times
  const ms = Number(process.hrtime.bigint() - t0) / 1e6;
  assert.ok(ms < 2000, `200 backward steps took ${ms} ms`);
});
