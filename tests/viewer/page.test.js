// The inline <script> blocks of the circuit viewer page (tools/circuit-viewer/index.html) must parse.
// A stray apostrophe in a single-quoted string once stopped the hosted page from loading any circuit.
// Run: node --test tests/viewer/page.test.js   (tests/test_circuit_render.py runs every tests/viewer/*.test.js).
// Written with AI assistance (Claude, Anthropic) under the repository owner's direction.
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs'), path = require('path'), vm = require('vm');
const PAGE = path.resolve(__dirname, '..', '..', 'tools', 'circuit-viewer', 'index.html');

test('every inline script in index.html parses', () => {
  const html = fs.readFileSync(PAGE, 'utf8');
  const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  assert.ok(blocks.length >= 1, 'no inline script found');
  for (const [i, src] of blocks.entries())
    assert.doesNotThrow(() => new vm.Script(src, { filename: `index.html inline script ${i}` }), `inline script ${i} does not parse`);
});
