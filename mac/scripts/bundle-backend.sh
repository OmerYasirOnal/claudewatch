#!/usr/bin/env bash
# Installs the claudewatch backend + its runtime dependencies into the bundled
# python's site-packages, so the .app can launch uvicorn with no external pip.
#
# Run AFTER `scripts/download-python.sh`.
#
# In default (single-arch) mode this targets mac/build/python.
# In UNIVERSAL=1 mode (when scripts/download-python.sh has produced
# mac/build/python-arm64 AND mac/build/python-x86_64) we install into BOTH
# trees. The cross-arch tree gets a pure-Python-only install: the host
# interpreter can't execute a foreign-arch binary, so pip is invoked on the
# host tree and its site-packages is mirrored into the foreign tree.
#
# This works because every runtime dep claudewatch ships is pure-Python at
# import time (fastapi, uvicorn, pydantic [v2 wheels include native code but
# the wheels are universal2 on macOS], iterm2, typer, …). If a future dep
# ships arch-specific .so files we'd need to invoke pip under Rosetta for the
# x86_64 tree — guard that here and fail loud.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${MAC_DIR}/.." && pwd)"
BUILD_DIR="${MAC_DIR}/build"

# Enumerate the python trees we should populate.
declare -a TARGETS=()
if [ -x "${BUILD_DIR}/python-arm64/bin/python3" ] && [ -x "${BUILD_DIR}/python-x86_64/bin/python3" ]; then
    TARGETS+=("${BUILD_DIR}/python-arm64" "${BUILD_DIR}/python-x86_64")
    echo "→ UNIVERSAL: bundling backend into both arm64 + x86_64 python trees"
elif [ -x "${BUILD_DIR}/python/bin/python3" ]; then
    TARGETS+=("${BUILD_DIR}/python")
else
    echo "✗ Bundled python not found. Run scripts/download-python.sh first." >&2
    exit 1
fi

HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
    arm64|aarch64) HOST_SUFFIX="arm64"  ;;
    x86_64)        HOST_SUFFIX="x86_64" ;;
    *) echo "Unsupported arch: $HOST_ARCH" >&2; exit 1 ;;
esac

# install_into <python-tree-dir>
install_into() {
    local tree="$1"
    local python="${tree}/bin/python3"
    local arch_suffix
    arch_suffix="$(basename "${tree}" | sed -E 's/^python-?//')"

    # If we can execute the interpreter directly, do a real pip install.
    # The host arch tree always satisfies this. For the cross-arch tree we
    # fall back to mirroring site-packages from the host tree below.
    if "${python}" -c "import sys" >/dev/null 2>&1; then
        echo "→ pip install into ${tree}"
        "${python}" -m pip install --upgrade pip --quiet
        "${python}" -m pip install --no-warn-script-location "${REPO_ROOT}" --quiet
        echo "✓ Backend bundled into ${tree}"
    else
        echo "→ Cannot execute ${python} on host (${HOST_ARCH}); mirroring site-packages from host tree"
        local host_tree="${BUILD_DIR}/python-${HOST_SUFFIX}"
        # Fall back to legacy single-tree path if host_tree doesn't exist.
        [ -d "${host_tree}" ] || host_tree="${BUILD_DIR}/python"
        local host_sp="${host_tree}/lib/python3.12/site-packages"
        local dest_sp="${tree}/lib/python3.12/site-packages"
        if [ ! -d "${host_sp}" ]; then
            echo "✗ Host site-packages missing at ${host_sp} — install host tree first." >&2
            exit 1
        fi
        mkdir -p "${dest_sp}"
        # rsync would be ideal but isn't guaranteed on minimal CI images; cp -R works.
        # Use `cp -R` from the contents of host_sp so we don't nest a dir.
        ( cd "${host_sp}" && tar -cf - . ) | ( cd "${dest_sp}" && tar -xf - )
        # Warn (loudly) if any platform-specific wheels slipped in — those won't
        # be importable on the foreign arch and we want to know.
        #
        # Issue #124: the old form had two bugs:
        #   1. `find -name '*.dylib' -o -name '*.so'` without explicit parens
        #      binds the implicit -print to only the rightmost clause, so
        #      .dylib files were silently skipped from the scan.
        #   2. `xargs -I{} sh -c 'file "{}"'` re-parses the path through the
        #      shell — a pathname with a quote / backtick / $ would break
        #      out of the inner quoting.
        # The fix uses explicit parens for correct precedence, and `-exec
        # file {} +` to invoke `file(1)` directly without involving a shell,
        # which is both safer and faster (no per-file fork).
        local foreign_libs
        foreign_libs="$(find "${dest_sp}" \( -name '*.dylib' -o -name '*.so' \) -exec file {} + 2>/dev/null \
            | grep -v "${arch_suffix}" \
            | grep -E 'Mach-O|dynamically linked' || true)"
        if [ -n "${foreign_libs}" ]; then
            echo "  ⚠ Some native libs in ${dest_sp} may not match ${arch_suffix}:"
            echo "${foreign_libs}" | head -5 | sed 's/^/    /'
            echo "  (If imports fail at runtime under ${arch_suffix}, run pip"
            echo "   under Rosetta against ${python}.)"
        fi
        echo "✓ Mirrored ${host_sp} → ${dest_sp}"
    fi

    local sp="${tree}/lib/python3.12/site-packages"
    du -sh "${sp}" 2>/dev/null | awk '{print "  " $1 "  " $2}'
}

for tree in "${TARGETS[@]}"; do
    install_into "${tree}"
done

echo "→ Smoke test on host tree: imports?"
HOST_TREE="${BUILD_DIR}/python-${HOST_SUFFIX}"
[ -d "${HOST_TREE}" ] || HOST_TREE="${BUILD_DIR}/python"
HOST_PY="${HOST_TREE}/bin/python3"
"${HOST_PY}" -c "from backend.server import app; print('  ✓ backend.server imports')"
"${HOST_PY}" -c "import uvicorn, fastapi; print(f'  ✓ uvicorn {uvicorn.__version__}, fastapi {fastapi.__version__}')"
