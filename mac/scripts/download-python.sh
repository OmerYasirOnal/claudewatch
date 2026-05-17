#!/usr/bin/env bash
# Downloads python-build-standalone, a portable, redistributable Python build.
# https://github.com/astral-sh/python-build-standalone
#
# Output (default — host arch only):
#   mac/build/python/                  → single tree for this machine's arch
#
# Output (UNIVERSAL=1):
#   mac/build/python-arm64/            → aarch64-apple-darwin tree
#   mac/build/python-x86_64/           → x86_64-apple-darwin  tree
#   mac/build/python/                  → symlink/copy of the host-arch tree so
#                                        local `swift run` (dev mode) still works
#
# We deliberately do NOT lipo-merge the full python-build-standalone
# distribution — it ships hundreds of .so extension modules and the merge is
# fragile across releases. Instead PythonRunner.swift picks the right tree at
# runtime based on the host arch. See mac/README.md → "Universal builds".

set -euo pipefail

PY_VERSION="${PY_VERSION:-3.12.7}"
PBS_RELEASE="${PBS_RELEASE:-20241016}"   # python-build-standalone release tag
UNIVERSAL="${UNIVERSAL:-0}"

BUILD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/build"
CACHE_DIR="${BUILD_DIR}/.cache"
mkdir -p "${CACHE_DIR}"

HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
    arm64|aarch64) HOST_PBS_ARCH="aarch64-apple-darwin"; HOST_SUFFIX="arm64" ;;
    x86_64)        HOST_PBS_ARCH="x86_64-apple-darwin";  HOST_SUFFIX="x86_64" ;;
    *) echo "Unsupported arch: $HOST_ARCH" >&2; exit 1 ;;
esac

# fetch_arch <pbs-arch> <dest-dir>
# Downloads (with cache) and extracts a single arch into dest-dir.
fetch_arch() {
    local pbs_arch="$1"
    local dest="$2"
    local tarball="cpython-${PY_VERSION}+${PBS_RELEASE}-${pbs_arch}-install_only.tar.gz"
    local url="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${tarball}"
    local tarball_path="${CACHE_DIR}/${tarball}"

    if [ -x "${dest}/bin/python3" ]; then
        echo "✓ Python ${PY_VERSION} (${pbs_arch}) already present at ${dest}"
        return 0
    fi

    if [ ! -f "${tarball_path}" ]; then
        echo "→ Downloading ${tarball} (~22 MB)..."
        curl -L --fail --progress-bar -o "${tarball_path}" "${url}"
    else
        echo "✓ Cached tarball: ${tarball_path}"
    fi

    echo "→ Extracting ${pbs_arch} into ${dest}..."
    rm -rf "${dest}"
    local parent
    parent="$(dirname "${dest}")"
    mkdir -p "${parent}"
    # The tarball expands into a top-level `python/` dir. Extract into a
    # scratch dir then rename so we land at exactly ${dest}.
    local scratch
    scratch="$(mktemp -d "${BUILD_DIR}/.extract.XXXXXX")"
    tar -xzf "${tarball_path}" -C "${scratch}"
    mv "${scratch}/python" "${dest}"
    rmdir "${scratch}"
    echo "✓ Extracted ${pbs_arch} → ${dest}"
}

if [ "${UNIVERSAL}" = "1" ]; then
    echo "→ UNIVERSAL=1: fetching arm64 + x86_64 python-build-standalone"
    fetch_arch "aarch64-apple-darwin" "${BUILD_DIR}/python-arm64"
    fetch_arch "x86_64-apple-darwin"  "${BUILD_DIR}/python-x86_64"

    # For dev-mode `swift run` we still want a `mac/build/python/` pointing at
    # the host-arch tree so the existing PythonRunner fallback resolves.
    # We use a symlink (cheap, no extra disk) — on macOS it's a portable choice.
    rm -rf "${BUILD_DIR}/python"
    ln -s "python-${HOST_SUFFIX}" "${BUILD_DIR}/python"
    echo "✓ Universal trees ready:"
    echo "  arm64:   ${BUILD_DIR}/python-arm64"
    echo "  x86_64:  ${BUILD_DIR}/python-x86_64"
    echo "  symlink: ${BUILD_DIR}/python → python-${HOST_SUFFIX} (dev-mode shim)"
    "${BUILD_DIR}/python-arm64/bin/python3" --version 2>/dev/null \
        || echo "  (arm64 python3 not runnable on this host — expected on x86_64)"
    "${BUILD_DIR}/python-x86_64/bin/python3" --version 2>/dev/null \
        || echo "  (x86_64 python3 not runnable on this host — expected on arm64)"
    echo "  Sizes:"
    du -sh "${BUILD_DIR}/python-arm64"  | awk '{print "    arm64:  " $1}'
    du -sh "${BUILD_DIR}/python-x86_64" | awk '{print "    x86_64: " $1}'
    exit 0
fi

# Single-arch (default) path — preserves the historical behavior so
# `make app` keeps working unchanged on arm64-only checkouts.
PYTHON_DIR="${BUILD_DIR}/python"
if [ -L "${PYTHON_DIR}" ]; then
    # Replace a UNIVERSAL=1 symlink left over from a prior build with a real tree.
    rm -f "${PYTHON_DIR}"
fi
fetch_arch "${HOST_PBS_ARCH}" "${PYTHON_DIR}"
"${PYTHON_DIR}/bin/python3" --version
echo "  Size: $(du -sh "${PYTHON_DIR}" | awk '{print $1}')"
