#!/usr/bin/env node
// Copyright 2026 James Barlow
// SPDX-License-Identifier: Apache-2.0
// AI-assisted (Claude, Anthropic), under the repository owner's direction.
// Parse a kickmix .kmx text file with the viewer's own parser and write ops.json.
//   node build_ops.js circuit.kmx source_kmb_sha256 ops.json
const fs = require('fs'), path = require('path');
require(path.join(__dirname, '..', 'kmx.js'));
const [kmx, sha, out] = process.argv.slice(2);
if (!out) { console.error('usage: node build_ops.js circuit.kmx source_kmb_sha256 ops.json'); process.exit(2); }
const p = globalThis.KmxParser.parse(fs.readFileSync(kmx, 'utf8'));
const o = { n_ops: p.ops.length, kinds: p.kinds, ops: p.ops, zpow_exact: p.zpow_exact, x_qubits: p.x_qubits,
            n_qubits: p.n_qubits, n_bits: p.n_bits, source_kmb_sha256: sha };
fs.writeFileSync(out, JSON.stringify(o));
console.log(`${out}: ${o.n_ops} ops, ${o.n_qubits} qubits, ${o.n_bits} bits, x = q${o.x_qubits[0]}..q${o.x_qubits[o.x_qubits.length - 1]}`);
