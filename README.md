# kickmix-macos

[![ci](https://github.com/manwithacat/kickmix-macos/actions/workflows/ci.yml/badge.svg)](https://github.com/manwithacat/kickmix-macos/actions/workflows/ci.yml)

A self-contained, Mac-native build of **kickmix**, the fast classical simulator for
reversible-plus-phase quantum circuits from Google's
[Qualtran](https://github.com/quantumlib/Qualtran) project, with **NEON word types
for Apple Silicon**. It exists so that developers on macOS can build, test and
verify kickmix circuits locally at native speed, including the official verifiers
of the Kaggle competition
[Google Quantum AI – Verifiable Quantum Circuit Compilation](https://www.kaggle.com/competitions/google-verifiable-quantum-circuit-compilation),
whose shipped `kickmix.so` is an x86-64 Linux / CPython 3.13 binary only.

- Builds in about two minutes with Apple clang 17 (Xcode command line tools), no
  Bazel required. Intel Macs and Linux build the same source with the upstream
  SSE/AVX or portable types; behaviour there is unchanged.
- Upstream's Python test suite (3,403 tests) passes on Apple Silicon.
- Simulation output is bit-identical to the portable build, and the three Kaggle
  verifiers reproduce their hosted results to the last digit (see *Validation*).
- One command to build into a virtualenv and run the tests: `tools/build.sh`.

> Not affiliated with Google, Qualtran or Kaggle. Upstream code is Apache-2.0,
> copyright Google LLC; modifications are listed in `NOTICE` and `CHANGES.md`.
> See *Provenance and licensing* below before reusing anything.

## Quick start

```bash
git clone https://github.com/manwithacat/kickmix-macos.git
cd kickmix-macos
tools/build.sh            # creates .venv, builds kickmix, runs upstream tests + smoke
.venv/bin/python -c 'import kickmix as km; print(km.__file__)'
```

Or install straight into an existing environment (Python 3.10 or newer):

```bash
pip install "pybind11==2.12.0" setuptools wheel numpy
pip install "git+https://github.com/manwithacat/kickmix-macos.git#subdirectory=kickmix"
```

Requirements: macOS with the Xcode command line tools (`xcode-select --install`),
Python 3.10+. Tested with Apple clang 17 on macOS 26 (arm64) and CPython 3.13.
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
CPython 3.13.16, with the vendored upstream commit plus the NEON overlay.

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

## What changed relative to upstream

- `kickmix/src/kickmix/simd/b128_neon.h`, `b256_neon.h`: NEON implementations with
  the same interface as `b128_sse` / `b256_avx` (lane order, `u32_*`/`u64_*`
  operations, run-time shift counts via `USHL` with negative counts for right shifts).
- `simd.h`: on `__aarch64__ && __ARM_NEON` select the NEON types unless
  `KICKMIX_DISABLE_NEON` is defined. `b512` remains the portable type on AArch64.
- `simd.test.h`, `simd.perf.h`: instantiate the word-type tests and benchmarks for
  the NEON types; `sim/sim.perf.cc`: goal entries for the new type names.
- `patches/neon-simd.patch` holds the exact diff against the upstream commit in
  `CHANGES.md`; `tools/sync_upstream.sh <commit>` re-vendors upstream and
  re-applies it.

Nothing else in kickmix is modified. The Python API, file formats (`.kmx`/`.kmb`),
RNG and numerical behaviour are upstream's.

## Repository layout

```
kickmix/                 vendored upstream package + overlay (pip install ./kickmix)
patches/neon-simd.patch  the overlay as a diff against UPSTREAM_COMMIT
tools/build.sh           venv + build + upstream tests + smoke  [--disable-neon]
tools/sync_upstream.sh   re-vendor from quantumlib/Qualtran@<commit>
tools/report_word_types.py
tools/cpp_word_type_tests.sh  C++ word-type unit tests (simd/ChaCha/popcount) without Bazel
tests/smoke_neon_build.py, tests/parity_dump.py
bench/sim_bench.py       throughput benchmark for A/B comparison
.github/workflows/ci.yml macOS arm64 / Intel / Linux builds, parity, Bazel C++ tests, wheels
NOTICE, CHANGES.md, LICENSE (Apache-2.0)
```

## Provenance and licensing

- Upstream: `quantumlib/Qualtran`, directory `kickmix/`, commit recorded in
  `CHANGES.md`. Apache License 2.0, copyright 2026 Google LLC. The license and
  Google's notices are preserved; modified files are listed in `NOTICE` as the
  license requires.
- The NEON additions were written with the assistance of an AI coding model under
  the repository owner's direction and validated as described above. Qualtran's
  contribution policy states that AI-generated code does not qualify as a
  contributor's original creation under the Google CLA, so this overlay has **not**
  been offered upstream. If you want NEON support in Qualtran itself, please author
  it independently and follow their `CONTRIBUTING.md`.
- Issues and pull requests here are welcome, especially benchmark reports from other
  Apple Silicon generations and a `b512` NEON type.
