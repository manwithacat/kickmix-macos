#!/usr/bin/env bash
# Re-vendor kickmix from a quantumlib/Qualtran commit and re-apply every patch listed in
# patches/series (in order).
#
# Usage:
#   tools/sync_upstream.sh <qualtran-commit-or-ref> [path-to-qualtran-checkout]
#
# Patches live in patches/ (paths relative to the Qualtran root); patches/series lists them,
# one file name per line ('#' comments and blank lines ignored).
# If a patch no longer applies cleanly, resolve conflicts in a Qualtran checkout,
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

PATCHES=()
while IFS= read -r line; do
  line="${line%%#*}"; line="${line//[[:space:]]/}"
  [[ -n "$line" ]] && PATCHES+=("$HERE/patches/$line")
done < "$HERE/patches/series"
[[ ${#PATCHES[@]} -gt 0 ]] || { echo "patches/series lists no patches" >&2; exit 1; }

( cd "$SRC" && git checkout -q "$REF" )
APPLIED=()
revert_applied() {
  for ((i=${#APPLIED[@]}-1; i>=0; i--)); do ( cd "$SRC" && git apply -R "${APPLIED[$i]}" ) || true; done
}
for p in "${PATCHES[@]}"; do
  if ( cd "$SRC" && git apply --check "$p" && git apply "$p" ); then
    APPLIED+=("$p")
  else
    echo "patch does not apply cleanly (in series order): $p" >&2
    revert_applied
    [[ -n "$TMP" ]] && rm -rf "$TMP"
    exit 1
  fi
done
COMMIT="$(cd "$SRC" && git rev-parse HEAD)"

rm -rf "$HERE/kickmix"
cp -R "$SRC/kickmix" "$HERE/kickmix"
rm -rf "$HERE/kickmix/python_build_kickmix" "$HERE/kickmix/"*.egg-info
# A stale build/ directory only; on case-insensitive file systems build == BUILD (a file).
[[ -d "$HERE/kickmix/build" ]] && rm -rf "$HERE/kickmix/build"
revert_applied
[[ -n "$TMP" ]] && rm -rf "$TMP"

echo "vendored kickmix from quantumlib/Qualtran@$COMMIT with ${#PATCHES[@]} patch(es) from patches/series applied"
echo "now: update UPSTREAM_COMMIT in CHANGES.md and run tools/build.sh"
