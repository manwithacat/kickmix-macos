# Circuit viewer

An in-browser viewer that runs a kickmix circuit for one basis-state input and shows what happens:
qubit values over every operation, stages, Toffoli-class gates, measurement-based clean-ups, branches
skipped, qubit occupancy and lifetimes, and the phase building up to its target. It ships with mrmeows'
63-qubit invsqrt circuit from the Google Quantum AI Verifiable Quantum Circuit Compilation competition,
and can load any `.kmx` circuit you give it.

Hosted copy: https://claude.ai/artifact/VQMERNoGu4nUUZo6NjrnY5

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
- `kmx.js` parses `.kmx` text exactly (Z_POW exponents as exact decimals); on the invsqrt circuit it
  reproduces the reference op list op for op.

## Data files

| File | What it is |
|---|---|
| `ops.json` | mrmeows' invsqrt circuit (kmb SHA-256 `0766a111…07d67d6`) as a compact op list |
| `stages.json` | 17 named stages and qubit groups; each tagged documented (from mrmeows' README and proofs) or inferred (from the op stream) |
| `errcurve.json` | error ÷ tolerance for all 3,133,452 inputs, computed with kickmix, binned |
| `livepeak.json` | qubits holding 1, maximum over 3,000 sampled inputs, and per-qubit lifetime envelopes |

## Credits

Circuit: mrmeows' hybrid invsqrt build (public Kaggle dataset `mrmeows/qcc-1375-open-circuits`,
Apache-2.0), on the public line of busyaprime, Phuc Thai, Dmitrii Gluzdov and marsyas. Simulator and
verifier: Google Quantum AI's kickmix (Qualtran, Apache-2.0). Viewer written with AI assistance (Claude)
under the repository owner's direction.
