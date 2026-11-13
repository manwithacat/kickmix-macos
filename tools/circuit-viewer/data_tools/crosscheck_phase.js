#!/usr/bin/env node
// Copyright 2026 James Barlow
// SPDX-License-Identifier: Apache-2.0
// AI-assisted (Claude, Anthropic), under the repository owner's direction.
// Compare the browser tracer's final phase (top 64 bits) with kickmix's, for the x in phase_sample.json.
//   node crosscheck_phase.js ops.json phase_sample.json
const fs = require('fs'), path = require('path');
require(path.join(__dirname, '..', 'tracer.js'));
const T = globalThis.InvsqrtTracer;
const P = T.prepare(JSON.parse(fs.readFileSync(process.argv[2], 'utf8')));
const S = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
let bad = 0, n = 0;
for (const [x, th] of Object.entries(S)) {
  const r = T.run(P, +x, (+x) >>> 0);
  const clean = r.q.every((v, k) => P.xq.includes(k) ? true : v === 0);
  if ((r.phase >> 64n) !== BigInt(th) || !clean) { bad++; console.log('MISMATCH x=' + x); }
  n++;
}
console.log(JSON.stringify({ n, mismatch: bad }));
process.exit(bad ? 1 : 0);
