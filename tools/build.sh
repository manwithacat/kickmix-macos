#!/usr/bin/env bash
# Build kickmix from the vendored source into a fresh virtual environment and run
# the upstream Python test suite. Works on macOS (Apple Silicon or Intel) and Linux.
#
# Usage:
#   tools/build.sh [venv-dir] [--disable-neon]
#
# --disable-neon compiles with -DKICKMIX_DISABLE_NEON so the portable scalar word
# types are used even on Apple Silicon (useful for A/B benchmarking).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${1:-$HERE/.venv}"
DISABLE_NEON=0
for arg in "$@"; do
  [[ "$arg" == "--disable-neon" ]] && DISABLE_NEON=1
done

PYTHON="${PYTHON:-python3}"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'

echo "==> venv: $VENV"
"$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip setuptools wheel "pybind11==2.12.0" numpy pytest

if [[ "$DISABLE_NEON" == "1" ]]; then
  echo "==> building with NEON disabled (portable word types)"
  export CFLAGS="${CFLAGS:-} -DKICKMIX_DISABLE_NEON"
  export CXXFLAGS="${CXXFLAGS:-} -DKICKMIX_DISABLE_NEON"
fi

echo "==> building kickmix ($(uname -m), $("$VENV/bin/python" --version))"
"$VENV/bin/python" -m pip install --no-build-isolation "$HERE/kickmix"

echo "==> upstream Python tests"
( cd "$HERE" && PYTHONPATH=kickmix "$VENV/bin/python" -m pytest kickmix/src -q -p no:cacheprovider )

echo "==> smoke"
"$VENV/bin/python" "$HERE/tests/smoke_neon_build.py"
echo "OK: $("$VENV/bin/python" -c 'import kickmix; print(kickmix.__file__)')"
