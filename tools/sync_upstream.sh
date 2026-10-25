#!/usr/bin/env bash
# Re-vendor kickmix from a quantumlib/Qualtran commit and re-apply the NEON overlay.
#
# Usage:
#   tools/sync_upstream.sh <qualtran-commit-or-ref> [path-to-qualtran-checkout]
#
# The overlay lives in patches/neon-simd.patch (paths relative to the Qualtran root).
# If the patch no longer applies cleanly, resolve conflicts in a Qualtran checkout,
# regenerate the patch with `git diff`, and update UPSTREAM_COMMIT in CHANGES.md.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REF="${1:?usage: sync_upstream.sh <commit> [checkout]}"
SRC="${2:-}"
TMP=""
if [[ -z "$SRC" ]]; then
  TMP="$(mktemp -d)"
  git clone --quiet https://github.com/quantumlib/Qualtran.git "$TMP/Qualtran"
  SRC="$TMP/Qualtran"
fi

( cd "$SRC" && git checkout -q "$REF" && git apply --check "$HERE/patches/neon-simd.patch" && git apply "$HERE/patches/neon-simd.patch" )
COMMIT="$(cd "$SRC" && git rev-parse HEAD)"

rm -rf "$HERE/kickmix"
cp -R "$SRC/kickmix" "$HERE/kickmix"
rm -rf "$HERE/kickmix/python_build_kickmix" "$HERE/kickmix/build" "$HERE/kickmix/"*.egg-info
( cd "$SRC" && git checkout -q -- . )
[[ -n "$TMP" ]] && rm -rf "$TMP"

echo "vendored kickmix from quantumlib/Qualtran@$COMMIT with the NEON overlay applied"
echo "now: update UPSTREAM_COMMIT in CHANGES.md and run tools/build.sh"
