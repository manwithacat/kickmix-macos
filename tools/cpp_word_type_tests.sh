#!/usr/bin/env bash
# Build and run the upstream C++ unit tests that instantiate every SIMD word type
# (simd, ChaCha, popcount suites and the PopCounter fast-path
# equivalence test in tests/cpp/) without Bazel and without sanitizers.
#
# On Apple Silicon this includes the b128_neon / b256_neon cases. Needs a C++20
# compiler and network access the first time (fetches googletest 1.14.0 into
# .gtest/ unless GTEST_DIR points at an existing checkout).
#
# Usage:
#   tools/cpp_word_type_tests.sh            # build + run everything
#   tools/cpp_word_type_tests.sh '*neon*'   # gtest filter
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$HERE/kickmix/src"
OUT="$HERE/.cpp-tests"
GTEST_DIR="${GTEST_DIR:-$HERE/.gtest}"
CXX="${CXX:-clang++}"
FILTER="${1:-*}"

if [[ ! -f "$GTEST_DIR/googletest/src/gtest-all.cc" ]]; then
  echo "==> fetching googletest 1.14.0 into $GTEST_DIR"
  git clone --quiet --depth 1 --branch v1.14.0 https://github.com/google/googletest.git "$GTEST_DIR"
fi

mkdir -p "$OUT"
SOURCES=(
  "$SRC/kickmix/simd/simd.test.cc"
  "$SRC/kickmix/util/chacha.test.cc"
  "$SRC/kickmix/util/pop_counter.test.cc"
  "$SRC/test.test.cc"
  "$HERE/tests/cpp/pop_counter_fastpath.test.cc"
  "$GTEST_DIR/googletest/src/gtest-all.cc"
  "$GTEST_DIR/googletest/src/gtest_main.cc"
)
# Non-test implementation files the popcount suite links against, if any exist.
while IFS= read -r f; do SOURCES+=("$f"); done < <(ls "$SRC"/kickmix/util/pop_counter*.cc 2>/dev/null | grep -vE '\.(test|perf)\.cc$' || true)

echo "==> compiling word-type tests with $CXX ($(uname -m))"
"$CXX" -std=c++20 -O2 -fno-strict-aliasing \
  -I "$SRC" -I "$GTEST_DIR/googletest/include" -I "$GTEST_DIR/googletest" \
  "${SOURCES[@]}" -o "$OUT/word_type_tests"

echo "==> cases: $("$OUT/word_type_tests" --gtest_list_tests | grep -c '^  ') total, $("$OUT/word_type_tests" --gtest_list_tests | grep -c neon || true) NEON"
"$OUT/word_type_tests" --gtest_filter="$FILTER" --gtest_brief=1
