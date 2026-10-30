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
- `kmx.js` parses `.kmx` text exactly (Z_POW exponents as exact decimals); on the invsqrt circuit it
  reproduces the reference op list op for op.

## Data files

| File | What it is |
|---|---|
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
under the repository owner's direction.
