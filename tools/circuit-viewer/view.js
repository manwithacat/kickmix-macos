// Zoom window, gestures, follow modes and pinned qubits for the circuit viewer (pure functions, no DOM).
// - View: the visible window {a, b} on the op axis (a <= op < b, fractional ends allowed so panning is smooth at
//   high zoom), zooming about an anchor op, panning, clamping, pinch maths and wheel handling, and `&view=a-b`.
// - Gesture: a small reducer for pointers on the diagram: one pointer drags (pans), two pointers pinch (zoom and pan).
// - Follow: where the window goes while the playhead plays or steps (continuous, page or off; `&follow=c|p|off`).
// - Pins: the highlighted qubits (`&hl=14,51`), at most Pins.MAX of them.
// Tested under Node (tests/viewer/view.test.js). Written with AI assistance (Claude, Anthropic) under the repository
// owner's direction.
(function (root) {
  const MAX_PX_PER_OP = 24;      // the deepest zoom: about 24 pixels per op, room for a gate glyph
  const STEP = 1.6;              // one press of + or -
  const DRAG_PX = 4;             // a pointer that moves less than this is a tap, not a drag

  const clampNum = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const View = {
    MAX_PX_PER_OP, STEP,
    full(n) { return { a: 0, b: n }; },
    isFull(v, n) { return !v || (v.a <= 0 && v.b >= n); },
    span(v) { return v.b - v.a; },
    // The narrowest window for a diagram `w` pixels wide.
    minSpan(n, w) { return Math.min(n, Math.max(4, Math.ceil((w || 1) / MAX_PX_PER_OP))); },
    // Keep the span within [minSpan, n] and the window inside [0, n].
    clamp(v, n, minSpan) {
      const lo = Math.min(n, Math.max(1, minSpan || 1));
      const s = clampNum(v.b - v.a, lo, n), a = clampNum(v.a, 0, n - s);
      return { a, b: a + s };
    },
    // The op under pixel px of a diagram w pixels wide (fractional).
    opAt(v, px, w) { return v.a + (px / w) * (v.b - v.a); },
    pxOf(v, op, w) { return (op - v.a) / (v.b - v.a) * w; },
    // Zoom by `factor` (> 1 zooms in) keeping `anchor` (an op) at the same place on screen.
    zoomAt(v, factor, anchor, n, minSpan) {
      const s0 = v.b - v.a, lo = Math.min(n, Math.max(1, minSpan || 1));
      const s1 = clampNum(s0 / factor, lo, n);
      const at = anchor == null ? (v.a + v.b) / 2 : clampNum(anchor, v.a, v.b);
      const a = at - (at - v.a) * s1 / s0;
      return View.clamp({ a, b: a + s1 }, n, lo);
    },
    pan(v, dOps, n) {
      const s = v.b - v.a, a = clampNum(v.a + dOps, 0, n - s);
      return { a, b: a + s };
    },
    // Keep op visible: unchanged if it is inside, otherwise centred, with the same span.
    reveal(v, op, n) {
      if (op >= v.a && op + 1 <= v.b) return v;
      const s = v.b - v.a;
      return View.pan(v, op + 0.5 - s / 2 - v.a, n);
    },
    // Pinch: the op under the first midpoint stays under the current midpoint, and the span scales with the
    // distance between the two pointers. x0a, x0b at the start; x1a, x1b now (pixels).
    pinch(v0, x0a, x0b, x1a, x1b, w, n, minSpan) {
      const d0 = Math.abs(x0b - x0a), d1 = Math.abs(x1b - x1a);
      const m0 = (x0a + x0b) / 2, m1 = (x1a + x1b) / 2, s0 = v0.b - v0.a;
      const lo = Math.min(n, Math.max(1, minSpan || 1));
      const s1 = d0 < 8 || d1 < 8 ? s0 : clampNum(s0 * d0 / d1, lo, n);
      const anchor = View.opAt(v0, m0, w);
      const a = anchor - (m1 / w) * s1;
      return View.clamp({ a, b: a + s1 }, n, lo);
    },
    // A wheel event, given its deltas, modifiers and the cursor's pixel. Returns { view, consumed }: consumed means
    // the page should not scroll (call preventDefault). ctrl/cmd + wheel (and a trackpad pinch, which arrives as
    // ctrl + wheel) zooms at the cursor; sideways wheel and shift + wheel pan; a plain vertical wheel pans when
    // zoomed in and scrolls the page once the window reaches that end of the circuit, or when not zoomed in.
    wheel(v, e, px, w, n, minSpan) {
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? w : 1;
      const dx = (e.deltaX || 0) * unit, dy = (e.deltaY || 0) * unit;
      if (e.ctrlKey || e.metaKey) {
        const f = clampNum(Math.exp(-dy * 0.01), 0.5, 2);
        return { view: View.zoomAt(v, f, View.opAt(v, px, w), n, minSpan), consumed: true };
      }
      if (View.isFull(v, n)) return { view: v, consumed: false };
      const sideways = e.shiftKey || Math.abs(dx) > Math.abs(dy);
      const d = e.shiftKey ? (dy || dx) : sideways ? dx : dy;
      const nv = View.pan(v, d / w * (v.b - v.a), n);
      return { view: nv, consumed: sideways || nv.a !== v.a };
    },
    // `&view=a-b` (integer ops). format gives '' for the whole circuit; parse gives null for anything unusable.
    format(v, n) {
      if (View.isFull(v, n)) return '';
      return `${Math.max(0, Math.round(v.a))}-${Math.min(n, Math.round(v.b))}`;
    },
    parse(str, n, minSpan) {
      const m = /^(\d+)-(\d+)$/.exec(String(str || '').trim());
      if (!m) return null;
      const a = +m[1], b = +m[2];
      if (!(b > a) || a >= n) return null;
      return View.clamp({ a, b: Math.min(b, n) }, n, minSpan);
    },
    // Integer op range drawn for a window: [ia, ib), and how many columns (one per op when they fit).
    columns(v, n, w) {
      const ia = Math.max(0, Math.floor(v.a)), ib = Math.min(n, Math.ceil(v.b)), span = Math.max(1, ib - ia);
      return { ia, ib, cols: Math.max(1, Math.min(Math.max(1, Math.floor(w)), span)) };
    },
  };

  // ---- pointers on the diagram
  // g = { ptrs: Map id -> {x, y}, base: { view, pts: Map id -> {x, y} } | null, dragging, moved }.
  const Gesture = {
    DRAG_PX,
    init() { return { ptrs: new Map(), base: null, dragging: false, moved: false }; },
    rebase(g, view) { return { ...g, base: { view, pts: new Map([...g.ptrs].map(([k, p]) => [k, { ...p }])) } }; },
    down(g, id, x, y, view) {
      const first = g.ptrs.size === 0, ptrs = first ? new Map() : new Map(g.ptrs);
      ptrs.set(id, { x, y });
      const multi = ptrs.size > 1;                     // a second pointer: never a tap
      return Gesture.rebase({ ptrs, base: null, dragging: !first && (g.dragging || multi), moved: !first && (g.moved || multi) }, view);
    },
    // Returns { g, view }: view is null when nothing should change (a tap in progress, or an unknown pointer).
    move(g, id, x, y, ctx) {
      if (!g.ptrs.has(id) || !g.base) return { g, view: null };
      const ptrs = new Map(g.ptrs); ptrs.set(id, { x, y });
      const ids = [...ptrs.keys()], b = g.base;
      if (ids.length === 1) {
        const p0 = b.pts.get(id);
        if (!p0) return { g: { ...g, ptrs }, view: null };
        const dx = x - p0.x, dy = y - p0.y;
        const dragging = g.dragging || Math.hypot(dx, dy) >= DRAG_PX;
        const ng = { ...g, ptrs, dragging, moved: g.moved || dragging };
        if (!dragging) return { g: ng, view: null };
        return { g: ng, view: View.pan(b.view, -dx / ctx.w * (b.view.b - b.view.a), ctx.n) };
      }
      const [ia, ib] = ids, A0 = b.pts.get(ia), B0 = b.pts.get(ib), A1 = ptrs.get(ia), B1 = ptrs.get(ib);
      if (!A0 || !B0) return { g: { ...g, ptrs }, view: null };
      const view = View.pinch(b.view, A0.x, B0.x, A1.x, B1.x, ctx.w, ctx.n, ctx.minSpan);
      return { g: { ...g, ptrs, dragging: true, moved: true }, view };
    },
    // A pointer lifts (or is cancelled). `tap` is true when a lone pointer lifted without dragging.
    up(g, id, view) {
      if (!g.ptrs.has(id)) return { g, tap: false };
      const ptrs = new Map(g.ptrs); ptrs.delete(id);
      const tap = ptrs.size === 0 && !g.moved;
      const ng = ptrs.size ? Gesture.rebase({ ...g, ptrs }, view) : { ...Gesture.init(), moved: g.moved };
      return { g: ng, tap };
    },
  };

  // ---- following the playhead while zoomed in (DAW-style follow modes)
  // Modes: 'c' continuous (the playhead stays ANCHOR of the way across and the trace slides under it; near the ends of
  // the circuit the window stops at op 0 or the last op and the playhead moves instead), 'p' page (the window jumps a
  // page when the playhead reaches an edge, leaving a lead-in of LEAD of the span), 'off' (the window never moves).
  // f = { mode, suspended }: a manual pan or zoom while playing suspends following until it is re-engaged (the Follow
  // button or F, pressing Play, or picking a mode). With the whole circuit visible every mode leaves the window alone.
  const Follow = {
    MODES: ['off', 'p', 'c'], DEFAULT: 'c',
    ANCHOR: 0.3,                 // continuous: the playhead sits 30% of the way across
    LEAD: 0.05,                  // page: a new page starts this share of the span before the playhead
    EDGE: 0.1,                   // paused steps re-place the window only when the op is off-screen or this close to an edge
    init(mode) { return { mode: Follow.MODES.includes(mode) ? mode : Follow.DEFAULT, suspended: false }; },
    // `&follow=c|p|off` (long names accepted); null for anything else. format gives '' for the default.
    parse(str) {
      const s = String(str == null ? '' : str).trim().toLowerCase();
      return { c: 'c', continuous: 'c', p: 'p', page: 'p', off: 'off', none: 'off', '0': 'off' }[s] || null;
    },
    format(mode) { return mode === Follow.DEFAULT ? '' : mode; },
    active(f) { return f.mode !== 'off' && !f.suspended; },
    // Actions: { type: 'mode', mode } | { type: 'manual', playing } (a pan or zoom by hand) | { type: 'play' } |
    // { type: 'toggle', last } (the Follow button or F; from Off it switches to `last`, default continuous).
    reduce(f, a) {
      switch (a.type) {
        case 'mode': return Follow.init(a.mode);
        case 'manual': return a.playing && f.mode !== 'off' && !f.suspended ? { ...f, suspended: true } : f;
        case 'play': return f.suspended ? { ...f, suspended: false } : f;
        case 'toggle': return f.mode === 'off' ? Follow.init(a.last && a.last !== 'off' ? a.last : 'c') : { ...f, suspended: !f.suspended };
        default: return f;
      }
    },
    // Continuous: op `pos` (fractional while playing) at ANCHOR of the width, clamped to the circuit.
    continuous(v, pos, n) {
      const s = v.b - v.a, a = clampNum(pos + 0.5 - Follow.ANCHOR * s, 0, Math.max(0, n - s));
      return a === v.a ? v : { a, b: a + s };
    },
    // Page: unchanged while op is inside; otherwise a new page with the op LEAD of the span in from the edge it entered
    // by (the left edge going forwards, or after a loop back to the start; the right edge going backwards).
    page(v, op, n, dir) {
      if (op >= v.a && op + 1 <= v.b) return v;
      const s = v.b - v.a, lead = Math.max(1, Follow.LEAD * s);
      const a0 = dir < 0 && op < v.a ? op + 1 + lead - s : op - lead;
      const a = clampNum(a0, 0, Math.max(0, n - s));
      return { a, b: a + s };
    },
    // The window for this frame of play. pos: the playhead's (fractional) op; dir: +1 or -1.
    playing(f, v, pos, n, dir) {
      if (!Follow.active(f) || View.isFull(v, n)) return v;
      return f.mode === 'c' ? Follow.continuous(v, pos, n) : Follow.page(v, Math.floor(pos), n, dir || 1);
    },
    // The window after a step, seek or jump while paused: continuous re-places it only when the op is off-screen or
    // within EDGE of an edge (so small steps do not jitter the view); page turns a page only when the op is off-screen.
    stepped(f, v, op, n, dir) {
      if (!Follow.active(f) || View.isFull(v, n)) return v;
      if (f.mode === 'p') return Follow.page(v, op, n, dir || 1);
      const e = Follow.EDGE * (v.b - v.a);
      if (op >= v.a + e && op + 1 <= v.b - e) return v;
      return Follow.continuous(v, op, n);
    },
  };

  // ---- pinned (highlighted) qubits
  const Pins = {
    MAX: 4,
    // Clicking a pinned qubit unpins it; pinning a fifth drops the oldest.
    toggle(list, q, max) {
      max = max || Pins.MAX;
      if (list.includes(q)) return list.filter(k => k !== q);
      const out = [...list, q];
      return out.length > max ? out.slice(out.length - max) : out;
    },
    parse(str, nq, max) {
      max = max || Pins.MAX;
      const out = [];
      for (const s of String(str || '').split(',')) {
        if (!/^\s*\d+\s*$/.test(s)) continue;
        const q = +s;
        if (q < nq && !out.includes(q)) out.push(q);
      }
      return out.slice(0, max);
    },
    format(list) { return list.join(','); },
  };

  // ---- the trace behind the diagram: one tracer pass per (circuit, x, seed, outcomes) keeps the state after every
  // op (bit-packed), so changing the window only re-bins; it never re-runs the circuit.
  const Trace = {
    // T: InvsqrtTracer; opq: qubits each op touches; want: ops whose cut (state before op `i`) is kept in snaps.
    run(T, P, opq, xq, x, seed, outcomes, want) {
      const n = P.n, NQ = P.nq, W = Math.ceil(NQ / 32), xset = new Set(xq);
      const st = new Int32Array(n * W), cur = new Int32Array(W);
      const turns = new Float64Array(n), live = new Uint16Array(n), tof = new Uint8Array(n), ran = new Uint8Array(n), pch = new Uint8Array(n);
      for (let k = 0; k < xq.length; k++) if (Math.floor(x / 2 ** k) % 2) cur[xq[k] >> 5] |= 1 << (xq[k] & 31);
      const bitOf = (k) => (cur[k >> 5] >>> (k & 31)) & 1;
      let nLive = 0, peak = 0, peakOp = 0, lastP = 0n;
      const snaps = new Map();
      const snap = (phase) => ({ x: xq.reduce((acc, k, j) => acc + bitOf(k) * 2 ** j, 0), live: nLive, phase: T.turns(phase) });
      if (want && want.has(0)) snaps.set(0, snap(0n));
      const r = T.run(P, x, seed, (i, q, phase, t, rn) => {
        for (const k of opq[i]) {
          const v = q[k], was = bitOf(k);
          if (v !== was) { cur[k >> 5] ^= 1 << (k & 31); if (!xset.has(k)) nLive += v ? 1 : -1; }
        }
        if (nLive > peak) { peak = nLive; peakOp = i; }
        st.set(cur, i * W);
        turns[i] = T.turns(phase); live[i] = nLive; tof[i] = t; ran[i] = rn;
        if (phase !== lastP) { pch[i] = 1; lastP = phase; }
        if (want && want.has(i + 1)) snaps.set(i + 1, snap(phase));
      }, outcomes);
      return { n, NQ, W, st, turns, live, tof, ran, pch, peak, peakOp, r, snaps };
    },
    // Value of qubit k after op i.
    bit(tr, i, k) { return (tr.st[i * tr.W + (k >> 5)] >>> (k & 31)) & 1; },
    // Columns over ops [ia, ib): a column holds the state after its first op, OR any later value of a qubit its ops
    // touch (so a brief 1 is never lost), with per-column counts and maxima. With one op per column it is exact.
    // extra: { openDebts, kicked } per-op arrays (optional).
    bin(tr, opq, isMeas, ia, ib, cols, extra) {
      const NQ = tr.NQ, span = Math.max(1, ib - ia);
      const bits = new Uint8Array(cols * NQ), tof = new Uint16Array(cols), meas = new Uint16Array(cols), skip = new Uint16Array(cols);
      const ph = new Float64Array(cols), occ = new Uint16Array(cols), pchg = new Uint16Array(cols), dS = new Uint16Array(cols), dK = new Uint16Array(cols);
      const od = extra && extra.openDebts, kd = extra && extra.kicked;
      let cur = -1;
      for (let i = ia; i < ib; i++) {
        const c = Math.min(cols - 1, Math.floor((i - ia) * cols / span));
        if (c !== cur) { for (let k = 0; k < NQ; k++) bits[c * NQ + k] = Trace.bit(tr, i, k); cur = c; }
        else for (const k of opq[i]) bits[c * NQ + k] |= Trace.bit(tr, i, k);
        tof[c] += tr.tof[i];
        if (tr.ran[i] && isMeas[i]) meas[c]++;
        if (!tr.ran[i]) skip[c]++;
        pchg[c] += tr.pch[i];
        if (tr.live[i] > occ[c]) occ[c] = tr.live[i];
        ph[c] = tr.turns[i];
        if (od && od[i] > dS[c]) dS[c] = od[i];
        if (kd && kd[i] > dK[c]) dK[c] = kd[i];
      }
      return { ia, ib, cols, NQ, bits, tof, meas, skip, ph, occ, pchg, dS, dK };
    },
  };

  root.CircuitView = { View, Gesture, Pins, Trace, Follow };
})(typeof window !== 'undefined' ? window : globalThis);
