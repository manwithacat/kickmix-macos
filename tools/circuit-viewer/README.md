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

Agents: read the fork's [`AGENTS.md`](../../AGENTS.md) first. One launcher covers every verb:
`tools/circuit-viewer/workbench <verb>`, run from the fork root. It uses the fork's `.venv`
(NumPy) automatically, or the interpreter in `WORKBENCH_PYTHON`. `workbench --help` lists every verb
by question; `workbench <verb> --help` gives its flags. The older `bilinear-*` launchers are aliases for
the matching `workbench bilinear-*` verbs, and the bare `bilinear_*.py` scripts remain as standalone entry
points for staged benchmark packages that copy them.

Every verb prints one JSON object on stdout. A successful summary carries a short `next` list of commands
that work on its output. A bad input (wrong modulus, missing file, bad range, unknown verb) prints
`{"error": ..., "hint": ...}` and exits non-zero, never a Python traceback. Report files are never
overwritten.

`summarize` indexes a `.kmb` file without expanding its operations to text or running public source code.
It produces bounded CLI answers and a machine-readable JSON report with stable half-open operation ranges,
nested tag paths, resource counts, source attribution, and SHA-256. For example:

```sh
tools/circuit-viewer/workbench summarize circuit.kmb --out report.json --context context.json \
  --author 'Circuit author' --source 'https://www.kaggle.com/code/author/notebook' \
  --version 1 --licence Apache-2.0
tools/circuit-viewer/workbench query report.json --depth 0 --sort toffoli
tools/circuit-viewer/workbench query report.json --match '^scan/' --group path
tools/circuit-viewer/workbench boundary report.json reverse_entry
tools/circuit-viewer/workbench cut circuit.kmb 3 --radius 4
tools/circuit-viewer/workbench compare older.json newer.json --out diff.json
tools/circuit-viewer/workbench query diff.json --sort toffoli --limit 10
tools/circuit-viewer/workbench trace-diff pinned-trace-manifest.json --out trace.json
tools/circuit-viewer/workbench trace-query trace.json --cut reverse_entry --seed 777
tools/circuit-viewer/workbench trace-plan trace.json host.json donor.json \
  --cohort fixed_weight --seed 777
tools/circuit-viewer/workbench prefix 9094.kmb 42582636 \
  --out diagnostic-prefix.kmb
tools/circuit-viewer/workbench sample-dqi 9094.kmb diagnostic-prefix.kmb inputs.npz \
  --seeds 777 778 --engine-package /path/to/compatible/engine-package --out diagnostic-dump.npz
tools/circuit-viewer/workbench state-map pinned-trace-manifest.json \
  --cut scan_exit --out coordinate-screen.json
tools/circuit-viewer/workbench state-map-query coordinate-screen.json --word 6
tools/circuit-viewer/workbench state-map-compare before-map.json before-manifest.json \
  after-map.json after-manifest.json --out map-change.json
tools/circuit-viewer/workbench stage-contract pinned-trace-manifest.json \
  scan-entry.json scan-exit.json --out scan-contract.json
tools/circuit-viewer/workbench wire-transport 9094.kmb static.json --out transport.json
tools/circuit-viewer/workbench transport-compare host-transport.json \
  donor-transport.json coordinate-screen.json --out transport-check.json
tools/circuit-viewer/workbench bilinear-cert gf16.kmb \
  --a a --b b --target t --modulus 0x13 --out multiplier-certificate.json
tools/circuit-viewer/workbench bilinear-project multiplier-certificate.json \
  --output-mask 0x1 --out scalar-projection.json
```

Never print a large report: the frontier DQI report is about 2.8 MB. Use `query`, which is bounded.
Query `--depth` is tag nesting: 0 selects top-level stages (`unextract`, `scan`, ...), 1 their children,
and so on; omit it to search every level. `--match` is a case-insensitive regular expression on the
region id (`path#occurrence`). `--group path` sums every instance of a tag path into one row with an
`instances` count, so the 8,050 `scan/gf_iadd` instances of the frontier DQI become one row. A query that
matches nothing reports the depths present and a hint. Circuits without INFO_TAGs (the invsqrt frontier)
have no regions: `summarize` says so and suggests `cut` instead.

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
--target 8:12`, and the binary polynomial modulus. Without register flags it uses KMB registers named
`a`, `b`, `t`. With `--challenge manifest.json --id gf16-06` it reads the modulus, any explicit
`basis_products`, the register layout (a row's `registers`, else the benchmark contract: `a`, `b`, `t`
contiguous) and the KMB itself from the bilinear benchmark manifest, so nothing needs guessing:

```sh
tools/circuit-viewer/workbench bilinear-cert --challenge challenge/manifest.json --id gf16-06 \
  --out challenge-cert.json --tensor-out field-tensor.json
```

`--tensor-out` writes the certified target as `{width, modulus, basis_products}`, the input that
`bilinear-structure`, `bilinear-isotopy`, `bilinear-carrier` and `bilinear-transport` take. Every bilinear
verb accepts `--modulus`, `--tensor-json` and `--challenge/--id` with the same meaning; the tensor-only
verbs synthesise the standard field tensor from `--modulus` when no tensor file is given. It follows affine wire forms and each CCX product
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
`workbench bilinear-isotopy target.json --out witness.json` (formerly `bilinear_basis_explorer.py`) recovers a verified
field-isotopy witness `F(a,b)=H(A(a)*B(b))` when one exists. It exhaustively
checks every input pair for widths up to eight. For widths nine through twelve,
it checks every input basis pair, which is exact for bilinear maps, and labels
that verification mode in the report. It does not emit a rank-one decomposition
or KMB candidate.
`bilinear-transport` applies that witness to a certified standard-field
multiplication core, synthesizes an ancilla-free KMB, and byte-certifies it
against the explicit target tensor:

```sh
tools/circuit-viewer/workbench bilinear-isotopy target.json --out witness.json
tools/circuit-viewer/workbench bilinear-transport target.json --core core.kmb \
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
tools/circuit-viewer/workbench bilinear-mechanisms transported.kmb --modulus 0x13 \
  --tensor-json target.json --out mechanisms.json
```

The report retains the initial alternatives and exact move history for an
agent to inspect. The greedy choice is a search heuristic, not a minimum-rank
proof; a smaller term count can also increase CX cost.

`bilinear-corner` finds a complementary exact three-term pattern: two terms
share a left parity, two share a right parity, and the pivot output mask is
the XOR of the two arm masks. It lists every initial move with source indices
and replacement terms, then applies the first strict saving repeatedly. The
optional KMB is checked against the explicit tensor after synthesis:

```sh
tools/circuit-viewer/workbench bilinear-corner gf16.kmb --modulus 0x13 \
  --out corners.json --candidate-out reduced.kmb
tools/circuit-viewer/workbench bilinear-corner --challenge challenge/manifest.json --id gf16-06 \
  --out challenge-corners.json
```

This mechanism was identified in a private agent benchmark on two fresh
six-bit tensors. It is a local exact rewrite; the report makes no optimality
or competition-score claim.

`bilinear-structure` reads an explicit tensor JSON (or, with `--modulus` alone, the standard field
tensor) and reports the exact
rank of every nonzero left and right multiplication map, its rank histogram,
small minimum-rank witnesses with kernel and image bases, the number of
nonzero annihilating input pairs, the three tensor flattening ranks, and
cross-products and common annihilators of rank-one input directions:

```sh
tools/circuit-viewer/workbench bilinear-structure target.json --out structure.json
tools/circuit-viewer/workbench bilinear-structure --modulus 0x13 --out field-structure.json
```

This gives an agent a bounded way to distinguish a division-like field
product from a tensor with zero divisors or low-rank operand directions.
The report describes invariants and witnesses; it does not recover a hidden
formula or a rank-one decomposition.
When a small set of rank-one directions complements a common annihilator,
`split_witness` additionally checks whether the interaction block and
remaining core occupy independent output spaces. It includes exact basis
products for each block and explicit zero-mixed-product checks. This is a
verified coordinate split, not an algorithm for the core.

`bilinear-carrier` reports and applies an exact carrier-extension identity.
It finds a pivot and compensating term with one shared input parity, plus
one or two carrier terms on the XOR of their other input parities whose
output masks XOR to the pivot's output. Extending the carriers' shared-side
input masks and changing the compensator's output mask removes the pivot.
Both input-axis orientations are searched. Every move is checked against
the full tensor, and `--candidate-out` emits a byte-certified KMB:

```sh
tools/circuit-viewer/workbench bilinear-carrier reduced.kmb --tensor-json field-tensor.json \
  --out carriers.json
tools/circuit-viewer/workbench bilinear-carrier gf16.kmb --modulus 0x13 --out field-carriers.json
```

The report lists source indices, replacement terms and each exact move.
This is a local rewrite heuristic, not a tensor-rank lower bound.

`bilinear-sieve` reduces a directory of candidate KMBs in two stages:

```sh
tools/circuit-viewer/workbench bilinear-sieve scan --manifest challenge/manifest.json \
  --id gf16-06 --pool candidates/ --review-size 6 --out scan/
# Give scan/review_packet.json to an agent. Its decisions JSON contains one
# {"id": ..., "disposition": "keep|discard|needs_evidence", "reason": ...}
# per review candidate.
tools/circuit-viewer/workbench bilinear-sieve finalize --audit scan/audit.json \
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

`workbench bilinear-synth` (also the standalone `bilinear_synth.py`) accepts a JSON list of rank-one terms, each with
`a_mask`, `b_mask` and `output_mask`, plus `width` and `modulus`. Optional
`basis_products` selects an explicit transformed target. It checks the
complete field multiplication tensor, then writes an ancilla-free KMB using
temporary CX parities around each CCX. For example:

```sh
tools/circuit-viewer/workbench bilinear-synth multiplier-certificate.json --out candidate.kmb
tools/circuit-viewer/workbench bilinear-cert candidate.kmb --modulus 0x13 --out candidate-cert.json
```

The input may also be a `bilinear-cert` JSON report, since it contains the
same fields; `--modulus` supplies one when the terms file lacks it. The synthesizer refuses a tensor mismatch and will not overwrite
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
