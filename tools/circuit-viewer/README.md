# Circuit viewer

An in-browser viewer that runs a kickmix circuit for one basis-state input and shows what happens:
qubit values over every operation, stages, Toffoli-class gates, measurement-based clean-ups, branches
skipped, qubit occupancy and lifetimes, and the phase building up to its target. It ships with three
invsqrt circuits from the Google Quantum AI Verifiable Quantum Circuit Compilation competition, chosen with the
"Circuit" selector, and can load any `.kmx` circuit you give it:

- `67482db6c538` (default, 62 qubits): the current public frontier. yoyobar's Kaggle notebook
  [*LB 22.99 | invsqrt 101 - 62 qubits*](https://www.kaggle.com/code/yoyobar/lb-22-99-invsqrt-101-62-qubits) (version 8) published the first public
  62-qubit invsqrt circuit, `a568bc793481`; this file combines yoyobar's own candidate with Ahmed Berat Özer's
  patch from `6dbb98b34c3b`, which removes one controlled swap's AND. It is built on busyaprime's `9a8627`, on
  mrmeows' 63-qubit design. 47,248 ops, 1,497 Toffoli-class gates, many of them in measurement-conditioned
  blocks (1,327.5 run per input on average). Our local run of the official invsqrt verifier passes it on all
  3,133,452 inputs: cost 100,692.58, 62 qubits, 1,327.48 mean Toffolis, worst error 0.99932 of tolerance at x = 161.
- `9a8627c8…` (63 qubits, `?circuit=9a8627`): busyaprime's build, which is mrmeows' design with two regions
  re-synthesised by busyaprime, the coefficient lookup (186 → 172 AND gates) and window S1 in the front decoder
  (30 → 25). 45,576 ops, 1,397 Toffoli-class gates (1,329.5 run per input on average). The official invsqrt
  verifier passes it on all 3,133,452 inputs, worst error 0.99932 of tolerance at x = 161.
- `0766a111…` (63 qubits, `?circuit=0766a111`): mrmeows' earlier file, 46,610 ops and 1,416 Toffoli-class gates.

Hosted copy: https://manwithacat.github.io/kickmix-macos/viewer/

## Run locally

The page fetches its data files, so serve the folder rather than opening `index.html` directly:

```sh
cd tools/circuit-viewer
python -m http.server 8000      # then open http://localhost:8000
```

Everything runs in your browser; a circuit you load is never uploaded anywhere.

To view your own circuit, convert it to text first (needs kickmix):

```sh
python kmb_to_kmx.py my_circuit.kmb    # writes my_circuit.kmx
```

then use "Load your own .kmx" on the page. Uploaded circuits get equal-width slices instead of named
stages. Circuits much larger than invsqrt (about 50,000 ops) will be slow in a browser; a local
summariser for large circuits is planned.

## Playhead and snapshot

Play runs the playhead through the circuit and Pause stops it where it is; Play again resumes from there, and only
Stop goes back to the start. Move it with the scrubber under the width profile, the buttons, or the keys:
<kbd>Space</kbd> play or pause, <kbd>←</kbd>/<kbd>→</kbd> one op, <kbd>Shift</kbd>+<kbd>←</kbd>/<kbd>→</kbd> the
previous or next Toffoli, <kbd>[</kbd>/<kbd>]</kbd> stage boundaries, <kbd>M</kbd> the next measurement (HMR). The
address bar keeps the stop point, so `?circuit=9a8627&op=14000&x=161` opens exactly there (`seed` and `kick=0` too).

While paused, the "At the playhead" panel shows the state after that op has run: the gate (type, operands,
condition; its wires are marked in the raster while paused), width now and its peaks, cost so far and still to come, the phase
against the target, register values with the bits the gate changed, wires that are live but idle for the next
1,000 ops, and the ledger at that cut (open values, open phase debts, the trial-balance line, the longest-held
value). The tracer keeps a checkpoint every 1,000 ops, so stepping backwards replays at most 1,000 ops.

**Phase without kickback.** The phase track jumps by half a turn mid-circuit, and sweeping x makes the coefficient
lookup's phase flash. That is measurement kickback: an HMR that reads 1 on a qubit holding 1 adds half a turn, and a
later Z or CZ conditioned on that reading pays it back. The toggle replays the same x with every HMR outcome forced
to 0: no kickback, no fix-ups, the same final phase (checked on x = 1, 161 and the maximum for all three circuits).
The band along the bottom of the phase track counts open phase debts, darker where they actually kicked back for
this x.

"Copy snapshot JSON" copies the panel as one JSON object, and `python render.py snapshot ops.json --op N --x X`
gives the same object headlessly (schema in [RENDER.md](RENDER.md#snapshot)); a test runs the viewer's code under
Node and checks the two agree.

## Zoom, pan and highlighted qubits

The diagram (stage ribbon, gate tracks, qubit raster and occupancy, and the phase track, all on one op axis) zooms
and pans on its own, without zooming the page:

- **Zoom**: pinch on a touch screen or trackpad, <kbd>Ctrl</kbd>/<kbd>⌘</kbd> + scroll (zooms at the cursor), the
  − and + buttons, or the keys <kbd>+</kbd> and <kbd>−</kbd>; <kbd>0</kbd> or "Whole circuit" goes back to the whole
  circuit. Clicking a stage band (or a stage-table row) zooms to that stage.
- **Pan**: drag, scroll sideways, <kbd>Shift</kbd> + scroll, or a plain scroll while zoomed in (it hands back to the
  page at either end of the circuit). The overview strip above the ribbon shows the whole circuit with the visible
  window boxed: drag the box, click to centre it, or focus the strip and use the arrow keys, Page Up/Down, Home, End.
- One-finger vertical swipes over the diagram still scroll the page (`touch-action: pan-y`), so the diagram cannot
  trap the page on a tablet; horizontal drags and pinches go to the diagram.
- Every zoom level is redrawn from the trace, not scaled: the tracer runs once per x, seed and outcome mode and keeps
  the state after every op (bit-packed, about 0.4 MB for invsqrt), so zooming and panning only re-bin the ops in the
  window. Zoomed in to about 5 pixels per op or more, each gate gets a glyph across its wires: dots on controls (and
  on Z-type gates), a ring on an X target, crosses on swapped wires, a square for R/HMR, a diamond for a rotation;
  red for Toffoli-class, gold for measurement-based, grey when its condition skipped it. The deepest zoom is about
  24 pixels per op.
- Play runs within the visible window. Stepping or jumping out of it moves the window (into the next stage when a
  stage was picked). The scrubber covers the visible window.
- The address bar keeps the window: `&view=18000-18400`.

"Taller lanes" doubles the lane height, which makes lanes easier to tap and glyphs easier to read.

**Highlighting qubits.** Click or tap a qubit's lane to pin a highlight on it, and click again to clear it; or type
its number into "Highlight qubit". Up to four qubits can be pinned (a fifth drops the oldest), each with its own
outline colour and its name at the left edge of its lane, in both themes. Each pinned qubit has a chip that removes
it. The address bar keeps them (`&hl=14,51`), and the "At the playhead" panel lists each one's value after the op at
the playhead, its lifetime at that op (`render.py width` semantics), and its last and next touch.

**Why the gate outlines no longer flash.** While playing, the playhead moves through hundreds of ops per second,
so the current gate's wires changed on every frame and the outlines flickered without telling you anything. The
outlines are now drawn only when paused or stepping, where they are steady and mark one gate. To follow particular
wires while playing, highlight them: those outlines do not change from frame to frame.

The zoom maths, gestures, highlight state and URL round-trips are in `view.js` and tested under Node
(`tests/viewer/view.test.js`), including a check that re-binning the stored trace gives exactly the columns that a
fresh tracer pass gave before. The pinch and drag tests feed simulated pointer positions to the gesture reducer;
real multi-touch was not tried on a tablet.

## Headless pictures and GIFs: `render.py`

For figures you can post or put in a notebook, `render.py` renders the same kind of views from `.kmb` files on
the command line, with no browser: live-qubit width profiles with stage-by-stage zoom GIFs, a stage-aligned diff
of two circuits, one-shot traces, a cost dashboard and a bookkeeping ledger. It prints a short JSON summary for agents.
See [RENDER.md](RENDER.md); examples are in [`examples/`](examples/).

```sh
python render.py width a.kmb b.kmb      # PNG + GIF in ./render-out
```

## What it is, and is not

- A viewer, not a verifier. The official verifiers decide correctness and cost.
- The tracer (`tracer.js`) follows one computational-basis trajectory with measurement outcomes from a
  seeded generator, and keeps the phase exactly in units of 2^-128 turn. On mrmeows' invsqrt circuit it
  matched kickmix exactly: all qubits, classical bits and the phase on 2,299 inputs, including
  outcome-matched replays.
- On busyaprime's 9a8627 circuit the same `tracer.js`, run under Node, matched kickmix exactly on 2,299
  inputs (x = 1..4, 161, 221, the maximum 3,133,452 and its neighbours, powers of two and their
  neighbours, a 1,000-point grid and random x): final qubits and phase on a free run with each side's own
  measurement outcomes, and all 63 qubits, all 8,288 classical bits and the phase on a replay with every
  HMR outcome copied from kickmix. 0 mismatches. A deliberately perturbed rotation angle and an altered
  outcome were both caught. The same check on 0766a111 also gave 0 mismatches. (`run()` now also returns the
  classical bits, for this comparison.)
- On the 62-qubit 67482db6c538 circuit the same check, with the same 2,299 inputs, gave 0 mismatches: final
  qubits and phase on a free run, and all 62 qubits, all 30,001 classical bits and the phase on an outcome-matched
  replay. A perturbed rotation angle (1,145 phase mismatches) and one flipped HMR outcome (1 classical-bit
  mismatch) were both caught. `tracer.js` itself is unchanged; `ops.json` now states `n_qubits` and `n_bits`.
- `tracer.js` now keeps its machine state in an object (`start`, `exec`, `clone`) so `snapshot.js` can checkpoint
  and replay it; `run()` is built on the same step. On all three circuits it gives results identical to the
  previous `tracer.js` (qubits, classical bits, phase, Toffolis and per-op states) on 2,376 runs: 132 inputs
  including x = 1, 161 and the maximum, three seeds, free and outcome-overridden. That is how the kickmix match
  above carries over; the kickmix comparison itself was not re-run.
- `kmx.js` parses `.kmx` text exactly (Z_POW exponents as exact decimals); on the invsqrt circuit it
  reproduces the reference op list op for op.

## Data files

| File | What it is |
|---|---|
| `snapshot.js` | the playhead state machine, the static analysis (lifetimes, liabilities) and the snapshot object |
| `view.js` | the zoom window, pointer gestures (drag, pinch), highlighted qubits, and the per-op trace the diagram is binned from |
| `ops.json` | the 62-qubit invsqrt circuit (kmb SHA-256 `67482db6…37ae4997`) as a compact op list, with `n_qubits` and `n_bits` |
| `stages.json` | busyaprime's 17 named stages and qubit groups, carried over to this circuit. Where the two op streams run the same gate sequence on relabelled wires (front decoder and its phase repair, `lookup_b`, most of `arith_b`, `lookup_c`, the seed retirement: 36,700 ops in aligned blocks) a boundary moves with its block. Elsewhere it sits at the matching op pattern, checked against live-wire value sets and the stage anchors of the 62-qubit mechanism analysis. Stage notes say what changed against 9a8627: the lookup builds 27 of its 32 products in place and ends with an HMR block with Clifford fix-ups, later stages move fix-up parities into classical logic on measurement bits, plus yoyobar's four AND rewrites in `arith_a` and Özer's patch in `unnormalize` |
| `errcurve.json` | error ÷ tolerance for all 3,133,452 inputs, computed with kickmix, binned. It is identical, input for input, to 9a8627's; 308 inputs were re-checked with a bit-level tracer |
| `livepeak.json` | qubits holding 1, maximum over 3,000 sampled inputs, and per-qubit lifetime envelopes: at most 34 of the 40 non-x qubits hold a 1 at any instant |
| `circuits/9a8627/` | the same four files for busyaprime's 9a8627 circuit |
| `circuits/0766a111/` | the same four files for mrmeows' 0766a111 circuit |

## Credits

Circuits: the 62-qubit `67482db6c538`, which combines yoyobar's own candidate with Ahmed Berat Özer's patch
(from `6dbb98b34c3b`); yoyobar published the first public 62-qubit invsqrt circuit, `a568bc793481`, in the
Kaggle notebook [*LB 22.99 | invsqrt 101 - 62 qubits*](https://www.kaggle.com/code/yoyobar/lb-22-99-invsqrt-101-62-qubits) (version 8). It is built on
busyaprime's 9a8627 build (Kaggle notebook
[LB 22.72431 | 22x Cheaper Than Google's Circuits](https://www.kaggle.com/code/busyaprime/lb-22-72431-22x-cheaper-than-google-s-circuits),
version 10, Apache-2.0), with the re-synthesised coefficient lookup and window S1 by busyaprime, on
mrmeows' hybrid invsqrt design (public Kaggle dataset `mrmeows/qcc-1375-open-circuits`, Apache-2.0),
which is itself on the public line of busyaprime, Phuc Thai, Dmitrii Gluzdov and marsyas. Simulator and
verifier: Google Quantum AI's kickmix (Qualtran, Apache-2.0). Viewer written with AI assistance (Claude)
under the repository owner's direction; the playhead and snapshot (`snapshot.js`, `render.py snapshot`) were
written the same way, from the owner's plan, as were the zoom, pan and qubit highlighting (`view.js`), from the owner's feedback.
