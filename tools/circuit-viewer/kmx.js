// Parser for kickmix .kmx text into the tracer's op format (see tracer.js / ops.json layout).
// op = [kind, operands..., cond]; Z_POW = [11, q, cond, angleTurnsFloat]; HMR = [9, q, outbit, cond];
// PUSH_CONDITION = [15, bit]; POP_CONDITION = [16, -1]. Exact Z_POW angles go to zpow_exact (units of 2^-128 turn).
(function (root) {
  const KINDS = ['X', 'CX', 'CCX', 'Z', 'CZ', 'CCZ', 'SWAP', 'CSWAP', 'R', 'HMR', 'NEG', 'Z_POW',
                 'BIT_INVERT', 'BIT_STORE0', 'BIT_STORE1', 'PUSH_CONDITION', 'POP_CONDITION'];
  const CODE = Object.fromEntries(KINDS.map((k, i) => [k, i]));
  const SHAPE = { X: 'q', CX: 'qq', CCX: 'qqq', Z: 'q', CZ: 'qq', CCZ: 'qqq', SWAP: 'qq', CSWAP: 'qqq', R: 'q',
                  HMR: 'qb', NEG: '', Z_POW: 'qe', BIT_INVERT: 'b', BIT_STORE0: 'b', BIT_STORE1: 'b',
                  PUSH_CONDITION: '', POP_CONDITION: '' };
  const MOD = 1n << 128n;

  // Exact decimal exponent (half-turns) -> phase units of 2^-128 turn, rounded to nearest (kickmix's 2^-127 half-turn grid).
  function exponentUnits(s) {
    const m = /^([-+]?)(\d+)\.(\d+)$/.exec(s);
    if (!m) throw new Error(`bad exponent "${s}" (expected a plain decimal like 0.25)`);
    const num = BigInt(m[2] + m[3]), den = 10n ** BigInt(m[3].length);
    let u = (num * (1n << 127n) * 2n + den) / (2n * den);      // round(num/den * 2^127)
    if (m[1] === '-') u = -u;
    return ((u % MOD) + MOD) % MOD;
  }

  function parse(text) {
    const ops = [], zpow = {}, regs = new Map(), regNames = new Map();
    let maxQ = -1, maxB = -1, lineNo = 0;
    for (let line of text.split('\n')) {
      lineNo++;
      const h = line.indexOf('#'); if (h >= 0) line = line.slice(0, h);
      const quoted = /"([^"]*)"/.exec(line); if (quoted) line = line.replace(quoted[0], '');
      const t = line.trim().split(/\s+/).filter(Boolean);
      if (!t.length) continue;
      const name = t[0];
      if (name === 'INFO_TAG' || name === 'DEBUG_PRINT') continue;
      if (name === 'APPEND_TO_REGISTER') {
        const q = +t[1].slice(1), r = +t[2].slice(1);
        if (!regs.has(r)) regs.set(r, []); regs.get(r).push(q); maxQ = Math.max(maxQ, q); continue;
      }
      if (name === 'REGISTER') { regNames.set(+t[1].slice(1), quoted ? quoted[1] : ''); continue; }
      if (!(name in CODE)) throw new Error(`line ${lineNo}: unsupported instruction ${name}`);
      let cond = -1, args = t.slice(1);
      if (args.length >= 2 && args[args.length - 2] === 'if') { cond = +args[args.length - 1].slice(1); maxB = Math.max(maxB, cond); args = args.slice(0, -2); }
      const shape = SHAPE[name];
      if (args.length !== shape.length) throw new Error(`line ${lineNo}: ${name} expects ${shape.length} targets`);
      const op = [CODE[name]];
      let units = null;
      for (let i = 0; i < shape.length; i++) {
        const a = args[i], k = shape[i];
        if (k === 'e') { units = exponentUnits(a); continue; }
        if (a[0] !== k) throw new Error(`line ${lineNo}: expected a ${k === 'q' ? 'qubit' : 'bit'} id, got ${a}`);
        const v = +a.slice(1); op.push(v);
        if (k === 'q') maxQ = Math.max(maxQ, v); else maxB = Math.max(maxB, v);
      }
      if (name === 'PUSH_CONDITION') {                                     // written 'PUSH_CONDITION if bN' -> [15, N]
        if (cond < 0) throw new Error(`line ${lineNo}: PUSH_CONDITION needs 'if bN'`);
        ops.push([15, cond]); continue;
      }
      if (name === 'POP_CONDITION') { ops.push([16, -1]); continue; }
      if (name === 'Z_POW') { op.push(cond, Number(units) / 2 ** 128); zpow[ops.length] = units.toString(); }
      else op.push(cond);
      ops.push(op);
    }
    // the input register: the one named "x" if present, else the first register
    let xr = [...regNames.entries()].find(([, n]) => n === 'x');
    const xKey = xr ? xr[0] : (regs.size ? [...regs.keys()][0] : null);
    const xq = xKey != null ? regs.get(xKey) : [];
    return { ops, kinds: KINDS, zpow_exact: zpow, x_qubits: xq, n_qubits: maxQ + 1, n_bits: Math.max(1, maxB + 1),
             registers: [...regs.entries()].map(([r, qs]) => ({ r, name: regNames.get(r) || '', qubits: qs })) };
  }
  root.KmxParser = { parse, exponentUnits };
})(typeof window !== 'undefined' ? window : globalThis);
