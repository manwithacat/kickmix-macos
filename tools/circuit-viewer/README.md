# Circuit viewer

An in-browser viewer that runs a kickmix circuit for one basis-state input and shows what happens:
qubit values over every operation, stages, Toffoli-class gates, measurement-based clean-ups, branches
skipped, qubit occupancy and lifetimes, and the phase building up to its target. It ships with two
63-qubit invsqrt circuits from the Google Quantum AI Verifiable Quantum Circuit Compilation competition,
chosen with the "Circuit" selector, and can load any `.kmx` circuit you give it:

- `9a8627c8…` (default): busyaprime's build, which is mrmeows' design with two regions re-synthesised by
  busyaprime, the coefficient lookup (186 → 172 AND gates) and window S1 in the front decoder (30 → 25).
  45,576 ops, 1,397 Toffoli-class gates (1,329.5 run per input on average). The official invsqrt
  verifier passes it on all 3,133,452 inputs, worst error 0.99932 of tolerance at x = 161.
- `0766a111…`: mrmeows' earlier file, 46,610 ops and 1,416 Toffoli-class gates
  (open it directly with `?circuit=0766a111`).

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
- `kmx.js` parses `.kmx` text exactly (Z_POW exponents as exact decimals); on the invsqrt circuit it
  reproduces the reference op list op for op.

## Data files

| File | What it is |
|---|---|
| `ops.json` | busyaprime's invsqrt circuit (kmb SHA-256 `9a8627c8…e36339be`) as a compact op list |
| `stages.json` | 17 named stages and qubit groups; each tagged documented (from mrmeows' README and proofs, and busyaprime's notebook for the two re-synthesised regions) or inferred (from the op stream). Boundaries carried over from mrmeows' stage map by an op-by-op diff: outside the two re-synthesised regions the op streams are identical |
| `errcurve.json` | error ÷ tolerance for all 3,133,452 inputs, computed with kickmix, binned. Its absolute values equal an independent all-x error map of this circuit for every x, and 308 inputs were re-checked with a bit-level tracer |
| `livepeak.json` | qubits holding 1, maximum over 3,000 sampled inputs, and per-qubit lifetime envelopes: at most 35 of the 41 non-x qubits hold a 1 at any instant |
| `circuits/0766a111/` | the same four files for mrmeows' 0766a111 circuit |

## Credits

Circuits: busyaprime's 9a8627 build (Kaggle notebook
[LB 22.72431 | 22x Cheaper Than Google's Circuits](https://www.kaggle.com/code/busyaprime/lb-22-72431-22x-cheaper-than-google-s-circuits),
version 10, Apache-2.0), with the re-synthesised coefficient lookup and window S1 by busyaprime, on
mrmeows' hybrid invsqrt design (public Kaggle dataset `mrmeows/qcc-1375-open-circuits`, Apache-2.0),
which is itself on the public line of busyaprime, Phuc Thai, Dmitrii Gluzdov and marsyas. Simulator and
verifier: Google Quantum AI's kickmix (Qualtran, Apache-2.0). Viewer written with AI assistance (Claude)
under the repository owner's direction.
