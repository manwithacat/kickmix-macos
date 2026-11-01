# Circuit viewer

An in-browser viewer that runs a kickmix circuit for one basis-state input and shows what happens:
qubit values over every operation, stages, Toffoli-class gates, measurement-based clean-ups, branches
skipped, qubit occupancy and lifetimes, and the phase building up to its target. It ships with mrmeows'
63-qubit invsqrt circuit from the Google Quantum AI Verifiable Quantum Circuit Compilation competition,
and can load any `.kmx` circuit you give it.

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
stages. Circuits much larger than invsqrt (about 50,000 ops) will be slow in this gate-level browser view.

## Large-circuit workbench (agent-first)

From the fork root, run `tools/circuit-viewer/workbench` to use the fork's
`.venv` automatically. This launcher avoids the system-Python `numpy` import
failure seen in the first GF(16) agent trial. The underlying `workbench.py`
remains available for explicit Python environments.

`workbench.py` indexes a `.kmb` file without expanding its operations to text or running public source code.
Use a Python environment with NumPy, such as this fork's `.venv`. It produces bounded CLI answers and a
machine-readable JSON report with stable half-open operation ranges, nested tag paths, resource counts,
source attribution, and SHA-256. For example:

```sh
.venv/bin/python tools/circuit-viewer/workbench.py summarize circuit.kmb --out report.json \
  --author 'Circuit author' --source 'https://www.kaggle.com/code/author/notebook' \
  --version 1 --licence Apache-2.0
.venv/bin/python tools/circuit-viewer/workbench.py query report.json --match 'unextract' --depth 1
.venv/bin/python tools/circuit-viewer/workbench.py boundary report.json reverse_entry
.venv/bin/python tools/circuit-viewer/workbench.py cut circuit.kmb 60984848 --radius 4
.venv/bin/python tools/circuit-viewer/workbench.py compare older.json newer.json --out diff.json
.venv/bin/python tools/circuit-viewer/workbench.py trace-diff pinned-trace-manifest.json --out trace.json
.venv/bin/python tools/circuit-viewer/workbench.py trace-query trace.json --cut reverse_entry --seed 777
.venv/bin/python tools/circuit-viewer/workbench.py trace-plan trace.json host.json donor.json \
  --cohort fixed_weight --seed 777
.venv/bin/python tools/circuit-viewer/workbench.py prefix 9094.kmb 42582636 \
  --out diagnostic-prefix.kmb
.venv/bin/python tools/circuit-viewer/workbench.py sample-dqi 9094.kmb diagnostic-prefix.kmb inputs.npz \
  --seeds 777 778 --engine-package /path/to/compatible/engine-package --out diagnostic-dump.npz
.venv/bin/python tools/circuit-viewer/workbench.py state-map pinned-trace-manifest.json \
  --cut scan_exit --out coordinate-screen.json
.venv/bin/python tools/circuit-viewer/workbench.py state-map-query coordinate-screen.json --word 6
.venv/bin/python tools/circuit-viewer/workbench.py state-map-compare before-map.json before-manifest.json \
  after-map.json after-manifest.json --out map-change.json
.venv/bin/python tools/circuit-viewer/workbench.py stage-contract pinned-trace-manifest.json \
  scan-entry.json scan-exit.json --out scan-contract.json
.venv/bin/python tools/circuit-viewer/workbench.py wire-transport circuit.kmb static.json --out transport.json
.venv/bin/python tools/circuit-viewer/workbench.py transport-compare host-transport.json \
  donor-transport.json coordinate-screen.json --out transport-check.json
.venv/bin/python tools/circuit-viewer/workbench.py bilinear-cert gf16.kmb \
  --a a --b b --target t --modulus 0x13 --out multiplier-certificate.json
.venv/bin/python tools/circuit-viewer/workbench.py bilinear-project multiplier-certificate.json \
  --output-mask 0x1 --out scalar-projection.json
```

The `query` command returns at most 20 regions by default (maximum 100); its `matched` count shows what was
omitted. `cut` prints op addresses and operands around one position. `compare` subtracts static stage counts;
it does not measure per-shot cost or certify an interface. Open `workbench.html` and select a report or diff
for a searchable visual table. This page reads only the selected JSON and never loads the huge circuit.

The report's `conditional_toffoli` includes gates under a `PUSH_CONDITION` stack as well as `_IF` gates.
All reports are static evidence; use the official verifier for correctness and scored cost. Region ids use
tag path plus occurrence number, so compare only circuits with compatible tag layouts.
Optional context JSON can pin a circuit SHA and supply `regions` keyed by tag path or region id; these
annotations travel with query results and are marked as contextual notes rather than computed counts.
It can also carry `boundaries` keyed by name with an operation index, evidence tier, source card and a
bounded state/phase summary. The `boundary` verb returns these imported findings; it does not run the
simulator or promote a sampled observation to proof. The report viewer exposes them as buttons.

`trace-diff` is the semantic companion to the static index. A pinned manifest names the input metadata,
sampled host/donor cut dumps, their circuit hashes, cut operation indices, seeds, and SHA-256 checksums.
The verb compares syndrome words by input cohort and weight, identifies the first divergent cut, checks
scratch cleanliness and basis-state seed stability, and gives small witnesses. It **consumes** dumps from
a separate sampler; it does not generate them or assert full circuit equivalence. Intermediate raw phase
differences are reported with an explicit warning because measurement records can differ. `trace-query`
returns a bounded view of one cut and seed. `workbench.html` can open this trace report too.
`trace-plan` locates the interval between the last sampled cohort match and first mismatch, then suggests
aligned top-level tag cuts near its midpoint for a separately registered follow-up sampler. It only
plans a diagnostic and does not assume those cuts have equal quantum or phase states. If raw differences
resolve at a later cut, the tool reports `raw_state_nonmonotonic` and refuses further bisection: the two
circuits may use different temporary representations. The trace also reports cohort-specific differing
syndrome words and the GF(2) rank of their sampled XOR columns; rank is descriptive, not an adapter cost.
Use `prefix` to write an unchanged diagnostic KMB prefix at a depth-zero cut, then `sample-dqi` with a
compatible `kickmix_engine` package to generate a trace dump from fixed DQI inputs and seeds. The source,
prefix, input and engine hashes are recorded. This samples only a prefix and is never a verifier pass.
The sampler currently expects exactly 256 shots with 70 12-bit syndrome words and 4095 12-bit mask words;
the engine package must expose `compile_kmb`, `run_batches` and `kickmix_engine.dqi.pack_rows`.
Inspect `sample-dqi --help` and the output metadata before adding a dump to a SHA-pinned trace manifest.

`state-map` tests one narrow canonicalization model on a pinned cut: each varying donor syndrome bit equals
one host syndrome bit, optionally complemented. It fits on even-weight fixed fixtures and evaluates the
frozen choice separately on odd-weight, natural and known-failure holdouts. It refuses missing hashes and
seed-dependent syndrome basis states. `state-map-query` returns only one 12-bit donor word, including its
candidate host bits and holdout errors. The report viewer groups the mapping by word. A fit on sampled
states is a diagnostic hint; it is not an algebraic identity or a valid splice contract. Constant fit bits
are excluded because they cannot identify a coordinate.
`state-map-compare` checks that both reports use the same SHA-pinned inputs and metadata, then compares
complete 12-bit word matches on their regular-input holdouts. It reports added, removed and changed mappings;
it does not infer that raw syndrome differences are monotonic between cuts.

`stage-contract` checks two sampled cuts of **one** circuit. It validates the dump hashes, fixed inputs,
engine identity and seeds, requires the exit dump to be pinned by the trace manifest, and counts full-qubit
and syndrome mismatches by cohort. Raw phase differences are descriptive only. Use it to test whether a
stage preserves basis state on the fixed cohort; a sampled match does not prove an algebraic identity or
phase compatibility.

`wire-transport` is a deliberately restricted static screen: it follows unconditional SWAP opcodes through
synthesis and convert and reports which original S qubits occupy each final S register. It ignores value
changes, conditional CSWAPs, phase and measurement. `transport-compare` says `no_testable_words` with an
unavailable agreement count when no complete word origin survives; this is a coverage limit, not evidence
against a sampled coordinate map.

`bilinear-cert` recovers a multiplication tensor directly from KMB gate bytes, ignoring all INFO_TAG labels.
Give it three equal-width qubit registers by name, or half-open ranges such as `--a 0:4 --b 4:8
--target 8:12`, and the binary polynomial modulus. It follows affine wire forms and each CCX product
through CX/SWAP, reports the input parity masks and final output mask of every product, and checks all
input-bit-pair products against independent shift-and-reduce multiplication. It also requires the final
linear map to restore both operands and an arbitrary dirty target. Its `physical_gates` excludes INFO_TAGs.
The command fails closed on conditions, measurements, phases, ancilla, affine or nonlinear CCX controls,
nonidentity linear outputs and tensor mismatches. A passing report proves this **restricted bilinear
component contract**, with no qubit-basis phases because only permutation gates are accepted. It does not
prove the product count is optimal: `decomposition_length` counts the circuit's CCX terms, while
`bilinear_rank_upper_bound` merges repeated input-parity pairs and cancels zero outputs; neither is a
lower-bound proof, and `lower_bound_proved` is false. A multiplier certificate also does not establish that the
component is useful at a particular DQI site. The field
polynomial should be independently checked for irreducibility when field semantics are required.

`bilinear-project` takes a validated certificate and one nonzero parity mask on the target word. It
identifies the existing CCX products that affect that scalar output, computes its full GF(2) coefficient
matrix, and constructs a verified rank factorization. Matrix rank is the **exact bilinear minimum for
that single output bit or parity**, unlike the full multi-output tensor's upper bound. Its factors can
drive a new scalar circuit, but the report does not prove that a real caller needs only this output or
that the scalar circuit improves DQI's scored `Q²T`.

For an explicitly transformed bilinear target, `bilinear-cert --tensor-json target.json`
checks the row-major `basis_products` table instead of standard field multiplication.
The Python `analyze` API accepts the same table as `expected_basis_products`.
This remains a restricted exact tensor contract; the table must be pinned as
part of the benchmark input.
`bilinear_basis_explorer.py target.json --out witness.json` recovers a verified
field-isotopy witness `F(a,b)=H(A(a)*B(b))` when one exists. It exhaustively
checks every input pair for widths up to eight and reports the three linear
maps. It does not emit a rank-one decomposition or KMB candidate.
`bilinear-transport` applies that witness to a certified standard-field
multiplication core, synthesizes an ancilla-free KMB, and byte-certifies it
against the explicit target tensor:

```sh
tools/circuit-viewer/bilinear-transport target.json --core tower15.kmb \
  --candidate-out transported.kmb --out transport-report.json
```

The report retains the witness, transported terms, source hashes, and gate
counts. It requires an invertible field isotope and a core for the target's
modulus; it does not solve general non-field tensors.

`bilinear-mechanisms` exposes exact same-parity compression opportunities. It
groups rank-one terms by a common left or right input parity, factors the
remaining input-to-output binary matrix at GF(2) rank, and reports each
term-saving replacement with source indices and masks. With `--candidate-out`,
it repeatedly takes the largest saving, synthesizes a KMB, and byte-certifies
the result against the pinned tensor:

```sh
tools/circuit-viewer/bilinear-mechanisms input.kmb --modulus 0x43 \
  --tensor-json target.json --out mechanisms.json --candidate-out compressed.kmb
```

The report retains the initial alternatives and exact move history for an
agent to inspect. The greedy choice is a search heuristic, not a minimum-rank
proof; a smaller term count can also increase CX cost.

`bilinear-sieve` reduces a directory of candidate KMBs in two stages:

```sh
tools/circuit-viewer/bilinear-sieve scan --manifest challenge/manifest.json \
  --id gf64-transformed-01 --pool candidates/ --review-size 6 --out scan/
# Give scan/review_packet.json to an agent. Its decisions JSON contains one
# {"id": ..., "disposition": "keep|discard|needs_evidence", "reason": ...}
# per review candidate.
tools/circuit-viewer/bilinear-sieve finalize --audit scan/audit.json \
  --packet scan/review_packet.json --decisions decisions.json \
  --max-shortlist 4 --out shortlist/
```

The scan checks KMB bytes against the challenge tensor, quarantines invalid
files in its audit, collapses repeated normalized term sets and keeps distinct
best-count decompositions in a bounded review packet. Finalization verifies
the agent's decisions and source hashes, copies the selected KMBs and always
retains the best exact CCX candidate. It does not call a model or infer an
algebraic derivation from successful equivalence. The caller can supply any
agent reviewer through the JSON handoff and inspect the full audit when needed.

`bilinear_synth.py` accepts a JSON list of rank-one terms, each with
`a_mask`, `b_mask` and `output_mask`, plus `width` and `modulus`. Optional
`basis_products` selects an explicit transformed target. It checks the
complete field multiplication tensor, then writes an ancilla-free KMB using
temporary CX parities around each CCX. For example:

```sh
python3 tools/circuit-viewer/bilinear_synth.py proposed-terms.json --out candidate.kmb
tools/circuit-viewer/workbench bilinear-cert candidate.kmb \
  --a a --b b --target t --modulus 0x49 --out candidate-cert.json
```

The input may also be a `bilinear-cert` JSON report, since it contains the
same fields. The synthesizer refuses a tensor mismatch and will not overwrite
an existing output. It does not find decompositions or minimize CX counts;
the agent must supply a valid multi-output term list. Its tensor precheck is
not a byte certificate: run `bilinear-cert` on the written KMB.

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
