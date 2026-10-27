# Shared helpers for tools/*.sh (source this file; do not execute).
# Works on macOS, Linux and Git Bash on Windows.

# Path of the Python interpreter inside a venv directory (bin/ on Unix, Scripts/ on Windows).
venv_python() {
  if [[ -x "$1/bin/python" ]]; then echo "$1/bin/python"; else echo "$1/Scripts/python.exe"; fi
}

# Normalise a path for Git Bash on Windows: `cygpath -m` gives D:/a/_temp/... which both
# MSYS tools (tar, cp) and native Windows Python accept; backslash forms break MSYS tar.
# A no-op on macOS and Linux.
portable_path() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else echo "$1"; fi
}

# Pinned upstream commit recorded in CHANGES.md.
upstream_commit() {
  sed -n 's/^- UPSTREAM_COMMIT: `\([0-9a-f]\{40\}\)`.*/\1/p' "$1/CHANGES.md" | head -n1
}

# Export the pinned upstream kickmix/ directory (no patches) into <dest>/kickmix and
# print the commit hash.
#   export_upstream_kickmix <repo-root> <dest> [qualtran-checkout]
# With no checkout argument, a blobless clone is cached in <repo-root>/.upstream-cache/Qualtran.
export_upstream_kickmix() {
  local root="$1" dest="$2" src="${3:-}"
  local commit; commit="$(upstream_commit "$root")"
  [[ -n "$commit" ]] || { echo "cannot read UPSTREAM_COMMIT from CHANGES.md" >&2; return 1; }
  if [[ -z "$src" ]]; then
    src="$root/.upstream-cache/Qualtran"
    if [[ ! -d "$src/.git" ]]; then
      mkdir -p "$root/.upstream-cache"
      git clone --quiet --filter=blob:none --no-checkout https://github.com/quantumlib/Qualtran.git "$src" >&2
    fi
  fi
  git -C "$src" cat-file -e "$commit^{commit}" 2>/dev/null || git -C "$src" fetch --quiet origin "$commit" >&2
  rm -rf "$dest"; mkdir -p "$dest"
  git -C "$src" archive "$commit" kickmix | tar -x -C "$dest"
  echo "$commit"
}
