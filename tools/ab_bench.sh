#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0. AI-assisted code; see NOTICE.
#
# A/B comparison of unmodified upstream kickmix (at the commit pinned in CHANGES.md) against
# this repository's tree: builds (or reuses) one virtualenv for each, checks that
# tests/parity_dump.py output is identical, then alternates benchmarks and prints medians
# and ratios. Optionally compares kickmix's own C++ PopCounter perf target too.
#
# Usage:
#   tools/ab_bench.sh [--rounds N] [--repeats N] [--work DIR] [--circuit FILE]... [--cpp]
#                     [--rebuild] [--no-parity] [--json OUT.json]
#
#   --work DIR     scratch directory for venvs and sources (default: <repo>/.ab-bench)
#   --circuit F    extra .kmb/.kmx circuit to benchmark (repeatable); default none.
#                  upstream's own kickmix/testdata/big_circuit.kmb is a reasonable choice.
#   --cpp          also build and alternate kickmix/util/pop_counter.perf.cc from each tree
#                  with $CXX (default c++/clang++), without Bazel
#   --isolate-popcounter
#                  use as the baseline this repository's tree with only
#                  patches/popcounter-fastpath.patch reversed (keeps the NEON overlay on both
#                  sides), instead of unmodified upstream; isolates the PopCounter change
#   --rebuild      ignore cached venvs
#
# Environment: PYTHON (default python3), QUALTRAN_DIR (an existing Qualtran checkout to read
# the pinned commit from; otherwise a blobless clone is cached in <repo>/.upstream-cache).
# Competition data is never used; for the official verifiers see tools/verify_parity.sh.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
source "$HERE/tools/lib.sh"

WORK="$HERE/.ab-bench"; ROUNDS=3; REPEATS=3; REBUILD=0; CPP=0; ISOLATE=0; JSON=""; NOPARITY=0
CIRCUITS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --work) WORK="$2"; shift 2;;
    --rounds) ROUNDS="$2"; shift 2;;
    --repeats) REPEATS="$2"; shift 2;;
    --circuit) CIRCUITS+=(--circuit "$2"); shift 2;;
    --cpp) CPP=1; shift;;
    --isolate-popcounter) ISOLATE=1; shift;;
    --rebuild) REBUILD=1; shift;;
    --no-parity) NOPARITY=1; shift;;
    --json) JSON="$2"; shift 2;;
    -h|--help) sed -n "2,28p" "${BASH_SOURCE[0]}"; exit 0;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done
PYTHON="${PYTHON:-python3}"
HERE="$(portable_path "$HERE")"; WORK="$(portable_path "$WORK")"
mkdir -p "$WORK"

fingerprint() {  # hash of a kickmix source tree (portable: python does the hashing)
  "$PYTHON" - "$1" <<'PY'
import hashlib, pathlib, sys
h = hashlib.sha256()
root = pathlib.Path(sys.argv[1])
for p in sorted(root.rglob("*")):
    if p.is_file() and not any(x in p.parts for x in ("python_build_kickmix", "__pycache__")) and not p.name.endswith((".so", ".pyd", ".o")) and ".egg-info" not in str(p):
        h.update(str(p.relative_to(root)).encode()); h.update(p.read_bytes())
print(h.hexdigest()[:16])
PY
}

build_variant() {  # <label> <source-dir containing setup.py>
  local label="$1" src="$2" venv="$WORK/venv-$1" fp stamp
  fp="$(fingerprint "$src")"; stamp="$venv/.ab-stamp"
  if [[ "$REBUILD" == 0 && -f "$stamp" && "$(cat "$stamp")" == "$fp" ]]; then
    echo "==> reusing $label venv ($fp)" >&2
  else
    echo "==> building $label venv from $src ($fp)" >&2
    rm -rf "$venv"
    PYTHON="$PYTHON" SOURCE_DIR="$src" SKIP_TESTS=1 "$HERE/tools/build.sh" "$venv" >&2
    echo "$fp" > "$stamp"
  fi
}

if [[ "$ISOLATE" == 1 ]]; then
  rm -rf "$WORK/upstream-src"; mkdir -p "$WORK/upstream-src"
  tar -C "$HERE" --exclude=python_build_kickmix --exclude='*.egg-info' --exclude='*.so' -cf - kickmix | tar -C "$WORK/upstream-src" -xf -
  ( cd "$WORK/upstream-src" && git apply -R "$HERE/patches/popcounter-fastpath.patch" )
  echo "==> baseline: this tree with patches/popcounter-fastpath.patch reversed"
else
  COMMIT="$(export_upstream_kickmix "$HERE" "$WORK/upstream-src" "${QUALTRAN_DIR:-}")"
  echo "==> baseline: unmodified quantumlib/Qualtran@$COMMIT"
fi
build_variant upstream "$WORK/upstream-src/kickmix"
build_variant fork "$HERE/kickmix"

A_LABEL=upstream; [[ "$ISOLATE" == 1 ]] && A_LABEL="fork-without-popcounter"
A_PY="$(venv_python "$WORK/venv-upstream")"; B_PY="$(venv_python "$WORK/venv-fork")"
ARGS=(--a-python "$A_PY" --b-python "$B_PY" --a-label "$A_LABEL" --b-label fork --rounds "$ROUNDS" --repeats "$REPEATS")
[[ ${#CIRCUITS[@]} -gt 0 ]] && ARGS+=("${CIRCUITS[@]}")
[[ "$NOPARITY" == 1 ]] && ARGS+=(--no-parity)
[[ -n "$JSON" ]] && ARGS+=(--json "$JSON")
RC=0
"$PYTHON" "$HERE/tools/ab_bench.py" "${ARGS[@]}" || RC=$?

if [[ "$CPP" == 1 ]]; then
  CXX="${CXX:-$(command -v clang++ || command -v c++)}"
  for v in upstream fork; do
    [[ "$v" == upstream ]] && S="$WORK/upstream-src/kickmix/src" || S="$HERE/kickmix/src"
    echo "==> compiling pop_counter perf ($v) with $CXX"
    "$CXX" -std=c++20 -O3 -fno-strict-aliasing -I "$S" "$S/main.perf.cc" "$S/kickmix/util/pop_counter.perf.cc" \
      "$S/kickmix/util/arg_parse.cc" -o "$WORK/pcperf-$v"
  done
  echo "==> C++ pop_counter perf, alternating, $ROUNDS rounds (ns per masked_increments; ratio = upstream ns / fork ns, above 1 means the fork is faster)"
  "$PYTHON" - "$WORK" "$ROUNDS" <<'PY'
import re, statistics, subprocess, sys
work, rounds = sys.argv[1], int(sys.argv[2])
pat = re.compile(r"\]\s+([\d.]+) ns .*?(pop_counter_\S+)\s*$")
res = {"upstream": {}, "fork": {}}
for r in range(rounds):
    order = ["upstream", "fork"] if r % 2 == 0 else ["fork", "upstream"]
    for v in order:
        out = subprocess.run([f"{work}/pcperf-{v}"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True).stdout
        for line in out.splitlines():
            m = pat.search(line)
            if m:
                res[v].setdefault(m.group(2), []).append(float(m.group(1)))
print("note: upstream's perf target feeds varying masks, so the fork's all-ones fast path should not change it.")
for name in sorted(res["upstream"]):
    a = statistics.median(res["upstream"][name]); b = statistics.median(res["fork"].get(name, [float('nan')]))
    print(f"  {name:32s} upstream {a:6.2f} ns   fork {b:6.2f} ns   ratio {a / b:5.2f}x")
PY
fi
exit "$RC"
