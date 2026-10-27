#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0. AI-assisted code; see NOTICE.
#
# Run the competition's unmodified official verifiers with two kickmix builds and diff every
# summary field (timings excluded). Competition data is not in this repository and must not
# be redistributed: point KICKMIX_OFFICIAL_DATA at your own local copy, a directory holding
#   verifiers/{invsqrt,dqi,dfthcsos}/   and   sample_submission/  (or set SUBMISSION_DIR).
#
# Usage:
#   KICKMIX_OFFICIAL_DATA=/path/to/data tools/verify_parity.sh [--tasks "dfthcsos invsqrt dqi"]
#       [--a-python PY] [--b-python PY] [--workers N] [--dqi-shots N] [--out DIR]
#
# Default interpreters: the venvs that tools/ab_bench.sh builds in .ab-bench/ (run it once, or
# pass your own, for example one with the shipped competition kickmix.so and one with a build
# from this repository). Exit status 1 if any field differs.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
source "$HERE/tools/lib.sh"
: "${KICKMIX_OFFICIAL_DATA:?set KICKMIX_OFFICIAL_DATA to a local directory with verifiers/ and sample_submission/}"

TASKS="dfthcsos invsqrt dqi"; WORKERS=2; EXTRA=(); A_PY=""; B_PY=""
OUT="$HERE/.verify-parity/$(date +%Y%m%d-%H%M%S)"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tasks) TASKS="$2"; shift 2;;
    --a-python) A_PY="$2"; shift 2;;
    --b-python) B_PY="$2"; shift 2;;
    --workers) WORKERS="$2"; shift 2;;
    --dqi-shots) EXTRA+=(--dqi-shots "$2"); shift 2;;
    --out) OUT="$2"; shift 2;;
    -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}"; exit 0;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done
[[ -n "$A_PY" ]] || A_PY="$(venv_python "$HERE/.ab-bench/venv-upstream")"
[[ -n "$B_PY" ]] || B_PY="$(venv_python "$HERE/.ab-bench/venv-fork")"
[[ -x "$A_PY" && -x "$B_PY" ]] || { echo "interpreters not found; run tools/ab_bench.sh first or pass --a-python/--b-python" >&2; exit 2; }
SUB=()
[[ -n "${SUBMISSION_DIR:-}" ]] && SUB=(--submission "$SUBMISSION_DIR")
mkdir -p "$OUT"
PYTHON="${PYTHON:-python3}"
VP="$HERE/tools/verify_parity.py"

for tag in a b; do
  py="$A_PY"; [[ $tag == b ]] && py="$B_PY"
  echo "==> verifiers with $tag interpreter: $py"
  # shellcheck disable=SC2086
  "$py" "$VP" run --root "$KICKMIX_OFFICIAL_DATA" ${SUB[@]+"${SUB[@]}"} --out "$OUT/$tag.json" --tasks $TASKS --workers "$WORKERS" ${EXTRA[@]+"${EXTRA[@]}"}
done
echo "==> comparing every summary field"
# shellcheck disable=SC2086
"$PYTHON" "$VP" compare "$OUT/a.json" "$OUT/b.json" $TASKS
