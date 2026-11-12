#!/usr/bin/env node
// Copyright 2026 James Barlow
// SPDX-License-Identifier: Apache-2.0
// AI-assisted (Claude, Anthropic), under the repository owner's direction.
// livepeak.json and per-stage Toffoli counts from the viewer's own tracer (basis-state trajectories).
//   node build_live.js ops.json stages_in.json livepeak.json stages.json [threads=3]
// Sample for livepeak: x = 2^k and 2^k +- 1, 115 random x per leading-zero class k = 0..21, random fill to 3,000
// (seeded). Per-stage mean executed Toffolis: 300 uniformly random x (seeded), same tracer.
const fs = require('fs'), path = require('path');
const { Worker, isMainThread, parentPort, workerData } = require('worker_threads');
require(path.join(__dirname, '..', 'tracer.js'));
const T = globalThis.InvsqrtTracer;
const MAXX = 3133452;
function mulberry(a) { return () => { a = (a + 0x6D2B79F5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }

if (!isMainThread) {
  const { opsPath, xs, uni } = workerData;
  const data = JSON.parse(fs.readFileSync(opsPath, 'utf8')), P = T.prepare(data), N = P.n;
  const xset = new Set(data.x_qubits), other = []; for (let k = 0; k < P.nq; k++) if (!xset.has(k)) other.push(k);
  const mx = new Int16Array(N), sm = new Float64Array(N), life = other.map(() => new Uint8Array(N));
  let best = { c: 0, op: -1, x: -1, set: null };
  for (const x of xs) {
    T.run(P, x, (x * 7 + 3) >>> 0, (i, q) => {
      let c = 0; for (let j = 0; j < other.length; j++) if (q[other[j]]) { c++; life[j][i] = 1; }
      if (c > mx[i]) mx[i] = c; sm[i] += c;
      if (c > best.c || (c === best.c && c > 0 && (i < best.op || (i === best.op && x < best.x)))) best = { c, op: i, x, set: other.filter(k => q[k]) };
    });
  }
  const tof = new Float64Array(N);
  for (const x of uni) T.run(P, x, (x * 11 + 5) >>> 0, (i, q, ph, t, ran) => { if (t) tof[i]++; });
  parentPort.postMessage({ mx, sm, life, best, tof });
} else {
  const [opsPath, stagesIn, liveOut, stagesOut, thr] = process.argv.slice(2);
  const nThreads = +(thr || 3);
  const data = JSON.parse(fs.readFileSync(opsPath, 'utf8')), N = data.ops.length;
  const xset = new Set(data.x_qubits), other = []; for (let k = 0; k < data.n_qubits; k++) if (!xset.has(k)) other.push(k);
  const rng = mulberry(2026), xs = new Set();
  for (let k = 0; k < 22; k++) for (const d of [-1, 0, 1]) { const v = 2 ** k + d; if (v >= 1 && v <= MAXX) xs.add(v); }
  for (let k = 0; k < 22; k++) { const lo = 2 ** k, hi = Math.min(2 ** (k + 1) - 1, MAXX); for (let r = 0; r < 115; r++) xs.add(lo + Math.floor(rng() * (hi - lo + 1))); }
  while (xs.size < 3000) xs.add(1 + Math.floor(rng() * MAXX));
  const X = [...xs].sort((a, b) => a - b), uniform = []; const r2 = mulberry(77); for (let i = 0; i < 300; i++) uniform.push(1 + Math.floor(r2() * MAXX));
  const jobs = [...Array(nThreads)].map((_, w) => new Promise((res, rej) => {
    const wk = new Worker(__filename, { workerData: { opsPath, xs: X.filter((_, i) => i % nThreads === w), uni: uniform.filter((_, i) => i % nThreads === w) } });
    wk.on('message', res); wk.on('error', rej);
  }));
  Promise.all(jobs).then((rs) => {
    const mx = new Int16Array(N), sm = new Float64Array(N), tof = new Float64Array(N);
    const life = other.map((_, j) => { const u = new Uint8Array(N); for (const r of rs) for (let i = 0; i < N; i++) u[i] |= r.life[j][i]; return u; });
    for (const r of rs) for (let i = 0; i < N; i++) { if (r.mx[i] > mx[i]) mx[i] = r.mx[i]; sm[i] += r.sm[i]; tof[i] += r.tof[i]; }
    let G = 0; for (let i = 0; i < N; i++) if (mx[i] > G) G = mx[i];
    const atmax = []; for (let i = 0; i < N; i++) if (mx[i] === G) atmax.push(i);
    const cand = rs.map(r => r.best).filter(b => b.c === G).sort((a, b) => a.op - b.op || a.x - b.x)[0];
    const edges = []; for (let k = 0; k <= 2000; k++) edges.push(Math.floor(k * N / 2000));
    const dmx = [], dme = [];
    for (let k = 0; k < 2000; k++) { let m = 0, s = 0; const a = edges[k], b = edges[k + 1]; for (let i = a; i < b; i++) { if (mx[i] > m) m = mx[i]; s += sm[i] / X.length; } dmx.push(m); dme.push(Math.round(s / Math.max(1, b - a) * 1000) / 1000); }
    const lifetimes = {};
    other.forEach((q, j) => { const iv = []; let s = -1; for (let i = 0; i < N; i++) { const on = life[j][i]; if (on && s < 0) s = i; if (!on && s >= 0) { iv.push([s, i - 1]); s = -1; } } if (s >= 0) iv.push([s, N - 1]); lifetimes['q' + q] = iv; });
    fs.writeFileSync(liveOut, JSON.stringify({ n_ops: N, n_x: X.length, columns: 2000, col_edges: edges, max_downsampled: dmx, mean_downsampled: dme,
      global_max: G, ops_at_global_max: atmax, n_ops_at_global_max: atmax.length, first_max: cand ? { op: cand.op, x: cand.x, qubits_set: cand.set } : null, lifetimes,
      note: 'lifetimes: ops after which qubit is 1 for at least one sampled x, merged intervals' }));
    // stages: static Toffoli-class count (any CCX/CSWAP/CCZ in the span) and mean executed per 300 uniform x
    const si = JSON.parse(fs.readFileSync(stagesIn, 'utf8'));
    const isT = (o) => o[0] === 2 || o[0] === 7 || o[0] === 5;
    let totS = 0, totM = 0;
    for (const s of si.stages) {
      s.tof = 0; s.n_zpow = 0; s.n_hmr = 0; let m = 0;
      for (let i = s.start; i < s.end; i++) { const o = data.ops[i]; if (isT(o)) s.tof++; if (o[0] === 11) s.n_zpow++; if (o[0] === 9) s.n_hmr++; m += tof[i] / uniform.length; }
      s.tof_mean = Math.round(m * 1000) / 1000; totS += s.tof; totM += m;
    }
    si.totals = { toffoli_static: totS, toffoli_mean_executed_300x: Math.round(totM * 1000) / 1000, n_ops: N };
    fs.writeFileSync(stagesOut, JSON.stringify(si));
    console.log(`livepeak: global max ${G} of ${other.length} at ${atmax.length} ops, first ${cand && cand.op} x=${cand && cand.x}; stages: static ${totS}, mean executed ${totM.toFixed(2)}`);
  }).catch(e => { console.error(e); process.exit(1); });
}
