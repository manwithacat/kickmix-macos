# kickmix-macos

[![ci](https://github.com/manwithacat/kickmix-macos/actions/workflows/ci.yml/badge.svg)](https://github.com/manwithacat/kickmix-macos/actions/workflows/ci.yml)

An openly AI-assisted fork of `kickmix/`, the classical simulator for
reversible-plus-phase quantum circuits in Google's
[Qualtran](https://github.com/quantumlib/Qualtran) project. It is vendored from one
pinned upstream commit (see `CHANGES.md`) with a small, listed set of patches on top.
The patches were written by an AI coding model (Claude) under the repository owner's
direction; they are not the owner's hand-written code and have not been contributed
upstream (see *Provenance and licensing*). The fork exists to support the owner's work on the Kaggle
competition
[Google Quantum AI – Verifiable Quantum Circuit Compilation](https://www.kaggle.com/competitions/google-verifiable-quantum-circuit-compilation),
whose shipped `kickmix.so` is an x86-64 Linux / CPython 3.13 binary only.

> Not affiliated with Google, Qualtran or Kaggle. Upstream code is Apache-2.0,
> copyright Google LLC; every modification is listed in `NOTICE` and `CHANGES.md`.

## Why this fork exists

Upstream kickmix is a carefully engineered, fast simulator: it packs 256 shots into each
machine word, uses hand-vectorised word types, and keeps per-shot state small enough to stay
in the CPU's fastest cache. This fork does not change that design. Its aim is narrower:
to make kickmix comfortable to run for the many people who verify circuits on their own,
resource-constrained hardware, such as laptops, Kaggle notebooks, Windows machines and
8–16 GB of RAM, rather than on a large compute cluster.

Concretely, the fork works towards, and tests, these properties:

- **Memory that depends on circuit width, not length.** One-pass summaries such as
  `reaction_depth()` stream over the circuit instead of copying it (on a 55 M-op circuit, a
  full official DQI verification at one worker peaks at 2.0 GiB instead of 7.8 GiB).
- **Predictable peaks.** Memory is a fixed amount plus a per-worker amount, so users can pick
  a worker count that fits their machine.
- **Every core on every OS.** `tools/verify_parallel.py` runs the official verifiers on
  multiple cores where `fork` is unavailable (Windows).
- **No trade of exactness for speed.** Every change gives bit-identical results to upstream,
  checked in CI on macOS, Linux and Windows.

## What differs from upstream

Seven patches, applied in order from `patches/series` (each is a plain diff against the
pinned upstream commit):

| Patch | Effect | Applies to |
| --- | --- | --- |
| `patches/neon-simd.patch` | NEON implementations of the 128- and 256-bit word types, selected on AArch64 unless `KICKMIX_DISABLE_NEON` is defined; test/benchmark instantiations; a macOS build note in `docs/kickmix/dev.md` | AArch64 only (Apple Silicon, Linux aarch64); other platforms compile the same code as upstream |
| `patches/popcounter-fastpath.patch` | `PopCounter::masked_increments` returns early when the mask is all ones, adding to `shared_count` instead of running the bit-sliced ripple. Results are defined to be the same; only the work differs | All platforms, whenever `counting_operations=True` (the official verifiers always set it) |
| `patches/popcounter-deferred.patch` | `PopCounter` buffers up to four non-all-ones masks and adds them to the bit-striped counters in one carry-save step (8 stripes instead of 6, one step-transfer per flush instead of per call); `compute_total` also counts buffered masks, so every reader is exact without a flush. Counts are bit-identical; only the work differs | All platforms, whenever `counting_operations=True` and operations are conditioned |
| `patches/stream-reaction-depth.patch` | `Circuit.reaction_depth()` streams over the packed operations instead of copying them all into a list; same result. Removes a ≈ 7 GiB transient on a 55 M-op circuit | All platforms |
| `patches/bulk-shot-io.patch` | `write_across_shots` / `read_across_shots` with 2-D bool arrays pack and unpack whole 64-bit words instead of one bit at a time; same values | All platforms |
| `patches/read-shot-phase.patch` | `Simulator.read_shot_phase` reduces the angle natively and constructs the `Fraction` without importing `fractions` or running a GCD per call; same values and types | All platforms |
| `patches/cx-run.patch` | `Sim::apply` applies a run of consecutive unconditioned `CX` ops in one tight loop (no per-op `switch`) and adds the run length to the op counter once (all-ones mask: `shared_count += n`; otherwise n `masked_increments`). Same XORs in the same order, same counts | All platforms, all word types; the saving is mainly with `counting_operations=True` |

Nothing else in kickmix is modified: the Python API, file formats (`.kmx`/`.kmb`), RNG and
numerical behaviour are upstream's.

### PopCounter fast path

Upstream's simulator counts every operation (`sim.h` calls `masked_increments(cond)`
after each one when counting is on). For an unconditional operation the mask is all ones,
every position is incremented, and `PopCounter` already has a `shared_count` that
`compute_total` adds to every total. The patch uses it. Local profiling of a DQI
reference circuit showed `PopCounter::masked_increments` at about 74% of `sim.do`.

Measurements (local, Apple M2 Pro, official verifiers, 2026-10-27; one machine, not a
benchmark suite):

- Parity: all 97 summary fields of the three official reference circuits are identical
  with and without the patch, as are 98 fields of a three-task portfolio.
- C++ equivalence: 3,600 of 3,600 randomized trials match upstream's counter
  (`tests/cpp/pop_counter_fastpath.test.cc` is the maintained version of that check).
- Official-verifier wall time on Apple M2 Pro (local measurement, 2026-10-27; 4 worker processes,
  three alternating rounds, upstream `638777fa` vs the same commit plus this patch only, identical
  results in every run): an 882-qubit competition DQI circuit, median 47.6 s → 29.1 s (1.63×); a 63-qubit inverse-square-root circuit
  163.2 s → 135.9 s (1.20×). Gains depend on how much of a circuit runs unconditionally; your
  circuits may differ, and `tools/ab_bench.sh` / `tools/verify_parity.sh` let you check.
- Cost on other inputs: upstream's own `pop_counter` micro-benchmark feeds varying masks
  and never takes the fast path; the extra test made the per-call time 4-12% higher
  across word types there in a local run (`tools/ab_bench.sh --cpp`, 2026-10-27). Workloads whose counted operations
  are mostly conditional gain nothing and pay that small cost.

## When to prefer upstream

Use upstream Qualtran `kickmix` if any of these apply:

- You need the officially supported, reviewed code, or you want fixes and features as
  soon as the Qualtran maintainers publish them. This fork follows upstream by manual
  re-vendoring and lags it.
- You are producing results that must match what Kaggle runs. The competition's hosted
  verifiers use the shipped upstream binary; a verified score is the hosted one. This
  fork reproduces the reference results locally (below), but it is not the reference.
- You are on x86-64 Linux with the shipped binary and are not constrained by speed;
  there the NEON patch is inactive and only the PopCounter patch differs.
- You cannot accept code that was written by an AI model.

## How to verify these claims yourself

Everything below runs locally; none of it needs this repository's CI.

```bash
tools/build.sh                         # build + upstream's 3,403 Python tests + smoke
tools/cpp_word_type_tests.sh           # C++ suites incl. the PopCounter equivalence test
tools/ab_bench.sh --rounds 3           # upstream vs fork: parity check + alternating timings
tools/ab_bench.sh --isolate-popcounter # fork vs fork minus the two PopCounter patches (keeps the rest on both)
KICKMIX_OFFICIAL_DATA=/path/to/your/copy tools/verify_parity.sh   # official verifiers, two builds
```

`tools/ab_bench.sh` rebuilds the baseline from the pinned upstream commit, so it does
not depend on any binary from this repository. See *Developer tools* for details. The
`perf` workflow runs the same comparison on macOS (arm64, Intel), Linux (x86-64,
aarch64) and Windows hosted runners; the `ci` workflow runs the tests.

## Kaggle entry point and hello world

`tools/kickmix_tools.py` is a single-file helper (intended as a Kaggle utility script) that installs the
fork into an isolated directory and runs the unchanged official verifiers with either the shipped
`kickmix.so` or the fork in a subprocess: `install()`, `verify(task, circuit, build=...)` (result plus wall
time and peak memory of the process tree), `compare(task, circuit)` (field-by-field equality, time,
memory) and `angle_strict_check(...)`. `examples/hello_world.ipynb` is a five-minute tour of the Python API
(CI executes it).

## Multi-core verification without `fork` (Windows)

The competition's invsqrt and DQI verifiers parallelise only through the `fork` start method
and run serially where it does not exist, as on native Windows. `tools/verify_parallel.py`
runs them with `spawn` workers instead, leaving the verifier files unchanged: each worker
loads the circuit and evaluates every n-th of the verifier's own seeded batch tasks, and the
parent passes the results back into the verifier's `verify()` for aggregation. It works with
any kickmix build, upstream included.

```bash
python tools/verify_parallel.py --verifiers /path/to/your/verifiers --task dqi \
    --circuit dqi.kmb --workers 6
```

Measured on an M2 Pro (spawn forced) against the verifiers' own `fork` pool, same circuits:
DQI 28 s vs 22–24 s, invsqrt 84 s vs 82 s; reports identical field for field. Each DQI worker
holds its own copy of the circuit (about 1 GB). `tests/test_verify_parallel.py` checks the
scheduling against a serial run on every CI platform using a stand-in verifier, since the
official files cannot be redistributed. WSL2 with the verifiers' own `fork` pool is the
alternative.

## Quick start

```bash
git clone https://github.com/manwithacat/kickmix-macos.git
cd kickmix-macos
tools/build.sh            # creates .venv, builds kickmix, runs upstream tests + smoke
.venv/bin/python -c 'import kickmix as km; print(km.__file__)'
```

Or install a prebuilt wheel from the [Releases](https://github.com/manwithacat/kickmix-macos/releases)
page (Apple Silicon wheels for CPython 3.10–3.14, plus Intel macOS, Linux x86-64 and Linux
aarch64 wheels built by the `release` workflow on GitHub's hosted runners; Windows x86-64
wheels are configured but unproven, see *Windows*), or install straight into an
existing environment from source (Python 3.10 or newer):

```bash
pip install "pybind11==2.12.0" setuptools wheel numpy
pip install "git+https://github.com/manwithacat/kickmix-macos.git#subdirectory=kickmix"
```

Requirements: macOS with the Xcode command line tools (`xcode-select --install`) or a
Linux C++20 toolchain, Python 3.10+. Tested locally with Apple clang 17 on macOS 26 (arm64) and CPython 3.13.
Apple clang 15 is reported to reject some C++20 constructs upstream uses; if you
hit that, install `llvm` from Homebrew and build with `CC`/`CXX` pointing at it.

## Using it with the Kaggle competition verifiers

The competition's `verifiers/` package imports `kickmix` and nothing else exotic
(NumPy and SciPy). With this build on `sys.path` the unmodified verifiers run
locally:

```bash
# layout: <root>/verifiers/{invsqrt,dqi,dfthcsos}, <root>/sample_submission/...
.venv/bin/pip install scipy
cd <root>
.venv/bin/python - <<'EOF'
import sys; sys.path.insert(0, "verifiers")
import kickmix as km
from invsqrt.verify_submission_invsqrt import ProblemSpecInvSqrt, verify
circuit = km.Circuit.from_file("sample_submission/invsqrt.kmb", format="kmb")
print(verify(circuit, ProblemSpecInvSqrt.for_n_p(10), max_workers=8)["score"])
EOF
```

The competition data itself is distributed only through Kaggle under its rules; this
repository contains none of it.

## Validation

All of the following were measured on a 10-core Apple Silicon Mac (16 GB), macOS 26,
CPython 3.13.16, with the vendored upstream commit plus the NEON patch only (measured
2026-10-25, before `popcounter-fastpath.patch` was added; the PopCounter numbers are above).

**Upstream tests.** `PYTHONPATH=kickmix pytest kickmix/src`: 3,403 passed.
`tools/cpp_word_type_tests.sh` compiles upstream's C++ `simd`, ChaCha and popcount
suites with googletest and no Bazel: 174 tests pass, 58 of them the `b128_neon` /
`b256_neon` instantiations. The full Bazel C++ suite (`bazel test
kickmix:kickmix_test`) runs in CI on `macos-15`.

**Portable vs NEON.** `tests/parity_dump.py` simulates a 72-qubit, 40,000-gate
synthetic circuit with measurements and phases in both builds; the transcripts are
identical (CI job `neon_disabled_parity`).

**Official verifiers, unchanged reference circuits, empty salt.** Hosted = the
competition's own Kaggle run (x86-64 Linux, shipped `kickmix.so`).

| Task | Hosted cost | This build | Hosted Q / T | This build Q / T | Hosted wall | Local wall |
| --- | ---: | ---: | --- | --- | ---: | ---: |
| `invsqrt` | 2,076,774.233715282 | 2,076,774.233715282 | 260 / 7,735 | 260 / 7,735 | 860 s (2 workers) | 160 s (8 workers) |
| `dqi` | 24,439,463,583,672.93 | 24,439,463,583,672.93 | 1,893 / 5,725,108.3821 mean | identical | 228 s (2 workers) | 158 s (2 workers) |
| `dfthcsos` | 3,355,599,588.3155375 | 3,355,599,588.3155365 | 730 / 93,237 | 730 / 93,237 | 77 s | 41 s |

The DQI mean Toffoli count depends on the sampled shots, so its exact agreement
shows the seeded ChaCha sampling matches the shipped binary. The chemistry cost
differs in the 16th significant digit from NumPy summation order, not from kickmix.
A verified public 11.38-point portfolio (three third-party circuits) also matched
its hosted run to every printed digit.

## Benchmark: NEON versus portable word types

`bench/sim_bench.py` simulates a deterministic synthetic circuit (96 qubits,
200,000 gates, 256 shots per batch). Build both variants and compare:

```bash
tools/build.sh .venv                 # NEON (default on Apple Silicon)
tools/build.sh .venv-noneon --disable-neon
.venv/bin/python bench/sim_bench.py
.venv-noneon/bin/python bench/sim_bench.py
```

Measured on a 10-core Apple Silicon Mac (macOS 26, Apple clang 17, CPython 3.13),
best of three repeats each, machine otherwise idle. "Portable" is the upstream
build without the overlay (identical to `--disable-neon`).

| Circuit | Portable | NEON | Speed-up |
| --- | ---: | ---: | ---: |
| Competition `invsqrt.kmb` reference (260 qubits, 400 batches × 256 shots) | 29.4 G gate-shots/s | 56.0 G gate-shots/s | **1.90×** |
| Competition `dqi.kmb` reference (1,893 qubits, 2 batches × 256 shots) | 71.8 G gate-shots/s | 83.1 G gate-shots/s | **1.16×** |
| Synthetic random gates (96 qubits, 1,000,000 ops, 40 batches) | 37.6 G gate-shots/s | 36.2 G gate-shots/s | 0.96× |

The synthetic circuit is random CCX/CX/X/CZ/Z over 96 qubits and is already well
auto-vectorized by clang in the portable build; the real circuits, with their
register-structured reads, measurement erasure and phase bookkeeping, are where the
explicit 128-bit lanes pay off. End-to-end verifier wall time improves less than
the inner loop (for example the chemistry verifier 41 s → 38 s) because much of
that time is NumPy and Python outside the simulator. Treat these as one machine's
numbers; please file an issue with yours.

What the numbers mean: the simulator's Python-facing `Simulator` uses 256-shot
words (`b256`). On x86-64 that is one AVX register; on AArch64 it was four scalar
`uint64` operations per word before this overlay and is now two NEON `uint64x2_t`
operations. Gains are bounded by memory traffic and by the scalar parts of the
simulator; the per-gate SIMD work is a fraction of the total.

## Windows

Status: configured, never yet built. `kickmix/setup.py` has an MSVC branch (`/std:c++20`)
that upstream does not exercise, and the `ci`, `perf` and `release` workflows include
`windows-latest` marked as non-blocking (`continue-on-error`) until a first green run.
Expect fixes. No Windows wheel is published until that happens.

Independently of this fork: the official verifiers fork worker processes only where
`os.fork` exists. On Windows there is no `fork`, so they silently run single-process
whatever `max_workers` says. For multi-core verification on a Windows machine use WSL2
(Linux inside Windows). Peak memory is also relevant: roughly 630 MB per DQI worker plus a
parent transient of about 5 GB (local profiling, 2026-10-26).

## Developer tools

- `tools/ab_bench.sh [--rounds N] [--circuit FILE] [--cpp] [--isolate-popcounter] [--json OUT]`:
  builds (or reuses, keyed by a source fingerprint) one virtualenv from the unmodified
  upstream tree at the pinned commit and one from this repository, requires
  `tests/parity_dump.py` to produce identical output from both, then alternates
  `bench/sim_bench.py` runs (A B, B A, ...) and prints per-variant medians and the ratio,
  with and without operation counting. `--cpp` also builds upstream's
  `pop_counter.perf.cc` for each tree without Bazel. The driver is `tools/ab_bench.py`;
  it writes Markdown to `$GITHUB_STEP_SUMMARY` when set. Scratch state lives in `.ab-bench/`
  and `.upstream-cache/` (both ignored by Git).
- `tools/verify_parity.sh`: runs the competition's unmodified official verifiers under two
  interpreters (default: the two venvs from `ab_bench.sh`) and diffs every summary field
  except timings. It needs `KICKMIX_OFFICIAL_DATA`, a directory on your machine holding
  `verifiers/` and `sample_submission/` as downloaded from Kaggle (`SUBMISSION_DIR`
  overrides the submission). The competition data is not in this repository and must not
  be redistributed.
- `tools/sync_upstream.sh <commit>`: re-vendors from Qualtran and applies every patch in
  `patches/series`, in order; it stops with the tree untouched if one does not apply.
- `tools/cpp_word_type_tests.sh [filter]`: upstream's simd/ChaCha/popcount suites plus
  `tests/cpp/pop_counter_fastpath.test.cc` (randomized comparison with a verbatim copy of
  upstream's counter across every word type, with 31/32/33 and 63/64/65 call boundaries
  and all-ones, zero and partial masks).

## GitHub Actions

- `ci`: build and test on macOS arm64/Intel, Linux x86-64/aarch64 and Windows
  (non-blocking); NEON-disabled parity; C++ suites; Bazel C++ tests; arm64 wheel.
- `perf`: on pull requests, manually and weekly: per OS, build upstream and fork, require
  identical `parity_dump` output, alternate benchmarks for at least three rounds, publish
  medians and ratios to the job summary and a JSON artifact. Synthetic circuits and
  upstream's `testdata` only. Hosted runners are shared machines; read ratios within one
  job, not across jobs.
- `release`: tag-triggered wheels (macOS arm64/Intel, Linux x86-64/aarch64, Windows x86-64)
  and sdist to GitHub Releases.
- `upstream-sync`: weekly, looks for new Qualtran commits touching `kickmix/`, tries the
  sync, build, tests and parity in a throwaway checkout, and opens or updates a single
  issue with the outcome. It never opens pull requests or pushes.

## Troubleshooting

- **`bazel test` hangs before printing anything** (macOS 26, Apple clang 17): the
  upstream test target is built with AddressSanitizer, and the ASan runtime can
  deadlock in its own start-up on this toolchain (`AsanInitInternal →
  StaticSpinMutex::LockSlow` in a `sample` trace). Add
  `--copt=-fno-sanitize=all --linkopt=-fno-sanitize=all`, or rely on the Python
  suite and the parity checks, which do not use Bazel.
- **Apple clang rejects C++20 code**: update the command line tools (clang 17 is
  known good) or build with Homebrew LLVM (`CC=$(brew --prefix llvm)/bin/clang`).
- **`pip install ./kickmix` cannot find pybind11**: install `pybind11==2.12.0` first;
  the build runs without isolation so that pin is honoured.
- **DQI verifier runs out of memory**: each worker needs roughly 6 GB; pass
  `max_workers=2` on a 16 GB machine.

## Files changed relative to upstream

- `kickmix/src/kickmix/simd/b128_neon.h`, `b256_neon.h`: NEON implementations with
  the same interface as `b128_sse` / `b256_avx` (lane order, `u32_*`/`u64_*`
  operations, run-time shift counts via `USHL` with negative counts for right shifts).
- `simd.h`: on `__aarch64__ && __ARM_NEON` select the NEON types unless
  `KICKMIX_DISABLE_NEON` is defined. `b512` remains the portable type on AArch64.
- `simd.test.h`, `simd.perf.h`: instantiate the word-type tests and benchmarks for
  the NEON types; `sim/sim.perf.cc`: goal entries for the new type names.
- `util/pop_counter.h`: the all-ones fast path and the 4-mask deferred accumulation. They rely on
  `non_zero()`, `operator~`, `^ & |`, `bit()`, `u64_*` shifts and adds, which every word type provides (`b64`/`b128`/`b256`/`b512` polyfill,
  `b128_sse`, `b256_avx`, `b512_avx`, `b128_neon`, `b256_neon`).
- `docs/kickmix/dev.md` (macOS build note) is touched by the NEON patch only.

The exact diffs are `patches/neon-simd.patch` and `patches/popcounter-fastpath.patch`.

## Repository layout

```
kickmix/                 vendored upstream package + patches (pip install ./kickmix)
patches/series           patch order; neon-simd.patch ... popcounter-deferred.patch (see patches/series)
tools/build.sh           venv + build + upstream tests + smoke  [--disable-neon]
tools/sync_upstream.sh   re-vendor from quantumlib/Qualtran@<commit>, apply patches/series
tools/ab_bench.sh/.py    upstream-vs-fork parity + alternating benchmarks
tools/verify_parity.sh/.py  official verifiers under two builds, field-by-field diff (local data)
tools/cpp_word_type_tests.sh  C++ unit tests (simd/ChaCha/popcount + fast-path equivalence), no Bazel
tools/lib.sh, tools/report_word_types.py
tests/smoke_neon_build.py, tests/parity_dump.py, tests/cpp/pop_counter_fastpath.test.cc
bench/sim_bench.py       throughput benchmark (--counting mimics the verifiers)
.github/workflows/       ci, perf, release, upstream-sync
NOTICE, CHANGES.md, LICENSE (Apache-2.0)
```

## Provenance and licensing

- Upstream: `quantumlib/Qualtran`, directory `kickmix/`, commit recorded in
  `CHANGES.md`. Apache License 2.0, copyright 2026 Google LLC. The license and
  Google's notices are preserved; modified files are listed in `NOTICE` as the
  license requires.
- The NEON additions, the PopCounter fast path, the test and the tooling in this
  repository were written by an AI coding model (Claude) under the repository owner's
  direction and validated as described above; they are not hand-written by the owner.
  Qualtran's
  contribution policy states that AI-generated code does not qualify as a
  contributor's original creation under the Google CLA, so these patches have **not**
  been offered upstream. If you want this functionality in Qualtran itself, please
  author it independently and follow their `CONTRIBUTING.md`. Pieces would only go
  upstream if Qualtran's AI-contribution policy changes.
- Issues and pull requests here are welcome, especially benchmark reports from other
  Apple Silicon generations and a `b512` NEON type.
