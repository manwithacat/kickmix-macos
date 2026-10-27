# Changes

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
