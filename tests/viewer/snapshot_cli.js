// Node harness for the parity test: loads the viewer's own tracer.js and snapshot.js and prints, as JSON, the
// snapshot objects the viewer would copy at the requested stop points, plus the static arrays behind them.
//   node snapshot_cli.js <ops.json> <stages.json|-> '<[[op, x, seed, kick], ...]>'
// Written with AI assistance (Claude, Anthropic) under the repository owner's direction.
'use strict';
const fs = require('fs'), path = require('path');
const VIEWER = path.resolve(__dirname, '..', '..', 'tools', 'circuit-viewer');
require(path.join(VIEWER, 'tracer.js'));
require(path.join(VIEWER, 'snapshot.js'));
const T = globalThis.InvsqrtTracer, CS = globalThis.CircuitSnapshot;

const [opsPath, stagesPath, pointsJson] = process.argv.slice(2);
const data = JSON.parse(fs.readFileSync(opsPath, 'utf8'));
const meta = stagesPath && stagesPath !== '-' ? JSON.parse(fs.readFileSync(stagesPath, 'utf8')) : {};
const P = T.prepare(data);
const A = CS.analyse(data, { stages: meta.stages || [], groups: meta.groups || [] });
const tracks = new Map();
const trackFor = (x, seed, kick) => {
  const key = `${x}|${seed}|${kick}`;
  if (!tracks.has(key)) tracks.set(key, CS.track(P, A, x, seed, kick));
  return tracks.get(key);
};
const snapshots = JSON.parse(pointsJson).map(([op, x, seed, kick]) =>
  CS.snapshot({ A, P, x, seed, kick: !!kick, trK: trackFor(x, seed, true), trZ: trackFor(x, seed, false) }, op));
const V = A.values;
const order = V.q.map((_, i) => i).sort((i, j) => V.q[i] - V.q[j] || V.a[i] - V.a[j]);
process.stdout.write(JSON.stringify({
  snapshots,
  static: {
    profile: Array.from(A.profile),
    values: order.map(i => [V.q[i], V.a[i], V.b[i], V.kind[i], V.reg[i], V.init[i]]),
    liabilities: A.liab.op.map((op, j) => [op, A.liab.qubit[j], A.liab.settle[j], A.liab.reads[j]]),
    fixups: A.fix,
  },
}));
