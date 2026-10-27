# Changes

## Vendored upstream

- UPSTREAM_COMMIT: `638777fac4379bc62e2a1e4a52cf1a1753895d48` (quantumlib/Qualtran `main`,
  2026-10-22, "Add ci action to build kickmix wheels (#1982)"). Directory `kickmix/`
  is a verbatim copy of that commit's `kickmix/` directory plus the overlay below.

## Patch series (patches/series, applied in order)

Both patches were written with the assistance of an AI coding model (Claude) under the
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

## Tooling added (no effect on the built package)

- 2026-10-27: `patches/series`; `tools/sync_upstream.sh` applies all patches in order;
  `tools/ab_bench.sh|.py`, `tools/verify_parity.sh|.py`, `tools/lib.sh`;
  `tests/cpp/pop_counter_fastpath.test.cc`; `bench/sim_bench.py --counting/--json`;
  `tests/parity_dump.py` also covers operation counting; workflows `perf.yml`,
  `upstream-sync.yml`; Linux aarch64 and Windows x86-64 jobs in `ci.yml` / `release.yml`.
