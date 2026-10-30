# Changes

## Releases

- v0.3.1 (2026-10-28): package version 0.3.1; simulator code identical to v0.3.0. Ships `kickmix_tools` 0.3.0
  (kickmix-engine support) to Kaggle and publishes the quickstart notebook `manwithacat/kickmix-quickstart`
  (`notebooks/kaggle-quickstart.ipynb`) after the release.
- v0.3.0 (2026-10-28): package version 0.3.0; adds `patches/batch-phase-read.patch`
  (`Simulator.read_phases_u64`), Windows build fixes, and required Windows CI. Releases are now mirrored to
  Kaggle by `.github/workflows/kaggle-publish.yml` (wheels dataset `manwithacat/kickmix-fork-wheels` and the
  `kickmix_tools` utility script).
- v0.2.0 (2026-10-27): package version 0.2.0; wheels include the full patch series below
  (streamed `reaction_depth`, packed shot I/O, `read_shot_phase` reduction, PopCounter fast path and
  deferred counting, CX runs). v0.1.0 (2026-10-25) had the NEON patch only.

## Tooling changes

- 2026-10-30, `tools/circuit-viewer/` (no change to kickmix itself): the viewer now opens on the 62-qubit invsqrt
  circuit `67482db6c538`, the current public frontier. It combines yoyobar's own candidate (yoyobar published the
  first public 62-qubit invsqrt circuit, `a568bc793481`) with Ahmed Berat Özer's patch from `6dbb98b34c3b`, on
  busyaprime's `9a8627` and mrmeows' 63-qubit design. `ops/stages/errcurve/livepeak.json` were regenerated for it;
  busyaprime's 17 stage boundaries were carried over through op-shape alignment and live-wire value sets, and the
  stage notes say what changed against 9a8627. `9a8627` moved to `circuits/9a8627/` and stays selectable with
  `?circuit=9a8627`, as does `?circuit=0766a111`. The price card now uses the selected circuit's qubit count, and the
  Pages workflow now also deploys `circuits/`, which the hosted viewer's selector needs. `tracer.js` is unchanged
  and matched kickmix on the new circuit (2,299 inputs, 0 mismatches). Written with AI assistance (Claude,
  Anthropic) under the repository owner's direction.
- 2026-10-30, `tools/circuit-viewer/render.py`: new verb `ledger`, a double-entry bookkeeping view of a
  reversible circuit. Wires are accounts (registers grouped), ops are entries, and every wire lifetime is a value
  settled by unitary uncompute (contra), by HMR with later phase fix-ups (accrual: the HMR record is a liability
  until the last op it conditions, followed through `BIT_INVERT_IF` copies) or by a dead tail. Width is capital,
  priced at the task's exchange rate from the cost formula, and Toffolis are expenses. Outputs: T-accounts, the
  trial balance at each stage cut, aged liabilities (values held longest without an entry), ANDs paid for twice and
  the closing balance (PNG, a per-value CSV, a JSON table). invsqrt circuits also get an exact bit-parallel run over
  all 3,133,452 inputs, which gives value identity for repeated ANDs and the close check. `ledger --compare A B`
  reconciles two ledgers stage by stage and reads the change as capital against expense. On macOS the script
  re-executes itself once with `MallocLargeCache=0`: freed large blocks were held in libmalloc's cache, and the
  peak footprint of a 60 M-op DQI ledger falls from about 7.6 GB to 3.2 GB. Tests and examples were added
  alongside the earlier ones. It was written with AI assistance (Claude, Anthropic) under the repository owner's
  direction, from the owner's design.
- 2026-10-30, `tools/circuit-viewer/render.py` 0.1.0 (new; no change to kickmix itself): a headless renderer of
  explanatory PNGs and GIFs from `.kmb` circuits, with verbs `width` (certified live-qubit profiles, one or two
  circuits, stage-by-stage zoom GIF), `diff` (stage-aligned structural diff: relabel-invariant hashes, recursive
  module comparison for tagged circuits, op-level edits), `trace` (one shot's qubits, registers and phase; pure
  Python engine, or kickmix's simulator run segment by segment, optionally in another interpreter), `cost`
  (dashboard from the official invsqrt and DQI cost formulas) and `doctor`, plus `--spec` batch runs. Every run
  prints one JSON summary of at most 2 KB with an evidence tier. Needs numpy, matplotlib and Pillow; kickmix only
  for `trace --engine kickmix`. Documentation `tools/circuit-viewer/RENDER.md`, examples in
  `tools/circuit-viewer/examples/` (invsqrt 63 vs 62 qubits, DQI 889 vs 888), tests
  `tests/test_circuit_render.py` (synthetic circuits; the width profile is checked against an independent port
  of the certified lifetime allocator, and the Python tracer against kickmix when it is installed). Written with
  AI assistance (Claude, Anthropic) under the repository owner's direction; it ports ideas from the owner's own
  competition scripts (lifetime allocator, surgery hashes, figure builders).
- 2026-10-28, `tools/kickmix_tools.py` 0.3.0 (no change to kickmix itself): kickmix-engine support.
  `install_engine(source)` installs the engine into its own directory from a wheel folder (default: found under
  `/kaggle/input`, e.g. the dataset `manwithacat/kickmix-engine-wheels`), a `.whl`, `"latest"` or a release tag
  (GitHub download), checking the wheel against `SHA256SUMS.txt`/`SHA256SUMS` and `release-manifest.json` and
  refusing a mismatch or an unlisted wheel. `verify(task, circuit, build="engine")` runs `kickmix-engine verify`
  with the competition's verifier file and returns the same result shape as the other builds, with the engine
  version, the selected simulator kernel and the kickmix build used for the verifier imports in `provenance`;
  `cross_check=True` adds the engine's own comparison with the official verifier. New `compare_builds()`,
  `format_comparison()`, `build_label()`, `engine_doctor()`, `find_wheel_dir()`, `official_available()`,
  `input_root()`. `provenance()` gains `engine_*` keys. Existing functions behave as before. Tests:
  `tests/test_kickmix_tools_engine.py` (stand-in engine, no network), run in CI with the 0.2.0 tests.
  New Kaggle entry point `notebooks/kaggle-quickstart.ipynb`, generated by `kaggle/quickstart/build.py`, and
  workflow `kaggle-quickstart.yml`.
- 2026-10-28, `tools/kickmix_tools.py` 0.2.0 (no change to kickmix itself): `install()` defaults to the newest
  GitHub release (`tag="latest"`), and accepts a directory of wheels (e.g. the attached Kaggle dataset) and picks
  the wheel for the running interpreter, so notebooks need not name a version. New `provenance()` (fork version,
  install route) is attached to every fork `verify()` result, and `check_latest()` warns when the installed fork
  is behind the newest release (needs internet; returns `up_to_date: None` offline).
- 2026-10-27, `tools/kickmix_tools.py` (no change to kickmix itself): `install()` given a local `.whl` now
  checks it against a `SHA256SUMS` file in the same directory when one exists, raising on a mismatch or if
  the file does not list the wheel; whether it was checked is recorded in the install description
  (`_STATE["fork_how"]`). On macOS, process-tree memory is sampled as `phys_footprint` via `proc_pid_rusage`
  (ctypes, no subprocess) when it works, falling back to RSS; reported in the `peak_pss_sum_mb` slot and labelled
  "footprint" by `memory_kind`. Linux PSS is unchanged. README gains "Installing alongside other kickmix builds"
  (PyPI `kickmix` 0.1.dev0 vs this fork's 0.2.0; offline Kaggle wheels dataset).

## Vendored upstream

- UPSTREAM_COMMIT: `638777fac4379bc62e2a1e4a52cf1a1753895d48` (quantumlib/Qualtran `main`,
  2026-10-22, "Add ci action to build kickmix wheels (#1982)"). Directory `kickmix/`
  is a verbatim copy of that commit's `kickmix/` directory plus the overlay below.

## Patch series (patches/series, applied in order)

All patches were written with the assistance of an AI coding model (Claude) under the
repository owner's direction; see NOTICE.

### patches/neon-simd.patch

- 2026-10-25: NEON word types `b128_neon` and `b256_neon` for AArch64; dispatch in
  `simd.h` with a `KICKMIX_DISABLE_NEON` opt-out; test and benchmark macro
  instantiations; perf goal entries. Validated: upstream Python suite (3,403 tests)
  passes; simulation output identical to the portable build; the three Kaggle
  "Verifiable Quantum Circuit Compilation" official verifiers reproduce their
  hosted results to the digit on Apple Silicon.

### patches/popcounter-fastpath.patch

- 2026-10-27: `PopCounter::masked_increments` (`kickmix/src/kickmix/util/pop_counter.h`)
  returns early with `shared_count++` when the mask is all ones (`!(~mask).non_zero()`),
  skipping the bit-sliced ripple and the `step` transfer schedule. Totals are unchanged by
  construction (`compute_total` adds `shared_count` to every position). All word types
  provide `non_zero()` and `operator~`. Validated locally (Apple M2 Pro): parity identical
  on 97 summary fields of the three official reference circuits and 98 fields of a
  3-task portfolio; 3,600/3,600 randomized equivalence trials; upstream Python suite
  (3,403 tests) passes; `tests/parity_dump.py` identical with and without the patch;
  C++ equivalence test `tests/cpp/pop_counter_fastpath.test.cc` passes for every word type.
  Known cost: ~4-12% slower per call on masks that are never all ones (micro-benchmark).

### patches/popcounter-deferred.patch

- 2026-10-27: `PopCounter::masked_increments` (`kickmix/src/kickmix/util/pop_counter.h`) stores each
  non-all-ones mask in a 4-entry buffer; every fourth mask a carry-save tree reduces the four to a
  3-bit per-position count that is added to the stripes with one adder ripple, and one step-transfer
  (previously one per call) moves the top stripe into `paired_totals`. To keep the overflow
  invariant (a position is transferred every 32 flushes = 128 masks) the stripe count grows from 6
  to 8 and `paired_totals` now carries weight 128; worst case 127 + 128 = 255 fits 8 bits (the old
  design: 31 + 32 = 63 in 6 bits). `compute_total` adds the still-buffered masks, so it is exact at any
  time and no reader (`py/operation_statistics.pybind.cc`, `cmd/main_count_operations_sampled.cc`,
  `sim/sim.perf.cc`, tests) needs a flush; `clear()` zeroes the buffer; `PopCounter` is not copied or
  merged anywhere else. The all-ones fast path is unchanged. `sizeof(PopCounter)` grows (6 -> 8
  stripes, +4 buffered words). Validated locally (Apple M2 Pro): `operation_statistics()` (aggregate and
  15-256 shots each) identical to b90c97f on the v3 DQI, invsqrt and 19 chemistry circuits, the 8
  upstream testdata circuits, 660 random circuits with measurements, conditions and
  PUSH/POP_CONDITION, and 48 stress runs of up to 3,000 consecutive conditioned ops with masks of
  density 0, 1/256, 1%, 50%, 90%, 99%, 1-1/256 and 1 (including `clear_operation_statistics`);
  `tests/parity_dump.py` identical; official verifiers on v3: 0 of 98 fields differ; upstream Python
  suite (3,403 tests) passes; C++ word-type suite (216 tests) incl. a naive per-bit comparison
  (`pop_counter_deferred.*` in `tests/cpp/pop_counter_fastpath.test.cc`). Speed: counting overhead
  of CX_IF/CZ_IF 3.2 -> 1.6 ns/op; DQI `do` per batch about 1.05x, invsqrt about 1.08x (see the
  evidence directory named in the commit). Written by an AI coding model (Claude) under the owner's
  direction.

### patches/stream-reaction-depth.patch

- 2026-10-27: `Circuit::reaction_depth()` streams over the packed op arrays via `iter_ops`
  into a `ReactionDepthTracker` (`circuit_util.cc`; the per-op logic is moved unchanged from
  `compute_reaction_depth`) instead of copying every op into a `std::vector<Op>`. The span
  overload is kept for the diagram code. On the 55 M-op DQI circuit: `reaction_depth()` 1.6 s
  to 0.3 s, peak RSS 7.1-7.5 GiB to 0.49 GiB; the official DQI verifier (4 workers) parent peak
  RSS 7.43 GB to 2.07 GB. Validated: identical reaction depth on the DQI, invsqrt and 19
  dfthcsos circuits, the upstream testdata, and 600 random circuits with measurements,
  conditioned ops and PUSH/POP_CONDITION; `tests/parity_dump.py` identical; DQI verifier 0 of 38
  fields differ vs the baseline; upstream Python suite (3,403 tests) passes.
### patches/bulk-shot-io.patch

- 2026-10-27: `write_across_shots` and `read_across_shots` with 2-D bool arrays
  (`interactive_simulator.pybind.cc`) pack/unpack each qubit's row into whole 64-bit
  words instead of calling `set_single_bit` / `bit()` per shot. Strided and
  non-contiguous arrays use a strided pack loop; the last partial sim keeps its untouched
  high bits (read-modify-write); validation, error messages and 1-D paths are unchanged.
  Validated locally (Apple M2 Pro): randomized old-vs-new equivalence (384 trials: batch
  sizes 1..1024, C/Fortran/sliced/negative-stride inputs, qubit+bit and duplicate indices,
  read after write and after `sim.do`, error paths) identical; `tests/parity_dump.py`
  identical; DQI and dfthcsos official verifiers on the v3 submission 0 differing fields;
  upstream suite (3,403 tests) passes. Micro-benchmark 882 x 256: write 6.0 -> 0.28 ns/bit,
  read 1.0 -> 0.20 ns/bit. DQI verifier (1 worker, 10240 shots) 16.1 s -> 12.6 s under load.
  Written by an AI coding model (Claude) under the owner's direction.
### patches/read-shot-phase.patch

- 2026-10-27: `sim_read_phase` (`kickmix/src/kickmix/py/interactive_simulator.pybind.cc`, Python
  `Simulator.read_shot_phase`) reduces the 128-bit angle natively (strips trailing zero bits against
  the 2^127 denominator) and builds the `fractions.Fraction` through a cached
  `Fraction._from_coprime_ints` (falls back to the cached `Fraction` class when absent, e.g. before
  Python 3.12), instead of importing `fractions` and running a GCD on each call. Returned values and
  types are unchanged: `int 0`, `int 1` for exactly one half-turn, otherwise a lowest-terms
  `Fraction` (never an int, since the numerator is non-zero and not 2^127). Validated locally
  (Apple M2 Pro): 18,946 shot phases (edge cases and Z_POW/NEG circuits) identical in value and
  type to the previous build on CPython 3.13 and 3.12; upstream Python suite (3,403 tests) passes
  on both; `tests/parity_dump.py` identical; official invsqrt verifier identical.

### patches/batch-phase-read.patch

- 2026-10-28: new `Simulator.read_phases_u64(out)` (`kickmix/src/kickmix/py/interactive_simulator.pybind.cc`).
  `out` is a writable 1-D `np.ndarray[np.uint64]` of length `batch_size` (any stride); element `i` is the
  shot's phase in units of 2^-64 turn, equal to the official verifiers'
  `half_turns_to_units(sim.read_shot_phase(i))` = `(((n << 64) // d + 1) >> 1) & (2**64 - 1)` for the
  phase `n/d` in half turns, i.e. the nearest multiple of 2^-64 turn, ties rounding up, modulo one turn.
  Natively, with the 128-bit angle `W = w0 + w1 * 2^64` (global-phase bit XORed into bit 63 of `w1`,
  value `W / 2^127` half turns): `((W >> 63) + 1) >> 1 = w1 + (w0 >> 63)` mod 2^64; the 0 and one-half-turn
  cases of `read_shot_phase` fall out of the same formula. No Python object is created per shot. Existing
  methods are untouched. Tests: `tests/test_phase_batch.py` compares against the per-shot conversion on
  edge phases (0, 1, tiny angles, exact ties at +-1/2 unit, values near one turn, with and without the
  global-phase bit), random written phases, random Z_POW circuits, batch sizes 1..1024, strided output
  and error paths. Motivation: in a full official invsqrt verification `read_shot_phase` plus the
  Fraction conversion was about 44% of the time per batch. Written by an AI coding model (Claude) under
  the owner's direction.

## Tooling added (no effect on the built package)

- 2026-10-27: `patches/series`; `tools/sync_upstream.sh` applies all patches in order;
  `tools/ab_bench.sh|.py`, `tools/verify_parity.sh|.py`, `tools/lib.sh`;
  `tests/cpp/pop_counter_fastpath.test.cc`; `bench/sim_bench.py --counting/--json`;
  `tests/parity_dump.py` also covers operation counting; workflows `perf.yml`,
  `upstream-sync.yml`; Linux aarch64 and Windows x86-64 jobs in `ci.yml` / `release.yml`.

### patches/cx-run.patch

- 2026-10-27: in `Sim::apply` (`kickmix/src/kickmix/sim/sim.h`) the `CX` case (not `CX_IF`) keeps
  consuming following `OpType::CX` ops in a `while` loop instead of returning to the dispatch
  `switch` per op; the operand streams advance exactly as before and `cond` (all-ones, or the
  active condition-stack mask, which CX does not change) is applied per op as before, so the XORs
  and their order are unchanged. Counting is done once per run: all-ones `cond` adds the run length
  to `PopCounter::shared_count` (`uint32_t`, modular like n separate `++`); any other mask calls
  `masked_increments(cond)` n times. Runs of length 1 take the old call. No ops are fused or
  reordered. Validated (Apple M2 Pro): `operation_statistics()` (aggregate and per shot) and
  qubit/bit/phase readouts identical to b90c97f on the v3 DQI and invsqrt circuits, 19
  chemistry circuits, upstream testdata, 660 random and 660 CX-run-rich circuits (runs up to 2,500 CX,
  CX_IF, measurements, PUSH/POP_CONDITION) and 48 stress circuits; `tests/test_cx_run.py`
  (run vs one-op-per-`do`); `tests/parity_dump.py` identical; composite v3 official verifiers
  0 of 98 fields differ; upstream suite (3,403 tests) and the C++ word-type suite pass.
  Speed (alternating A/B, counting on): DQI `do` per 256-shot batch 173 ms -> 109 ms; CX-run
  micro-benchmark 4.4 -> 1.2 ns/CX for long runs; invsqrt and chemistry `do` not slower.
  Written by an AI coding model (Claude) under the owner's direction.
