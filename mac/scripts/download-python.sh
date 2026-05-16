#!/usr/bin/env bash
# Downloads python-build-standalone, a portable, redistributable Python build.
# https://github.com/astral-sh/python-build-standalone
#
# Output: mac/build/python/ — a self-contained Python install we can copy into
# the .app bundle and ship to users with no system-Python dependency.

set -euo pipefail

PY_VERSION="${PY_VERSION:-3.12.7}"
PBS_RELEASE="${PBS_RELEASE:-20241016}"   # python-build-standalone release tag

ARCH="$(uname -m)"
case "$ARCH" in
    arm64)  PBS_ARCH="aarch64-apple-darwin" ;;
    x86_64) PBS_ARCH="x86_64-apple-darwin" ;;
    *) echo "Unsupported arch: $ARCH" >&2; exit 1 ;;
esac

# `install_only` variant: minimal stdlib + interpreter, no test suite.
TARBALL_NAME="cpython-${PY_VERSION}+${PBS_RELEASE}-${PBS_ARCH}-install_only.tar.gz"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${TARBALL_NAME}"

BUILD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/build"
PYTHON_DIR="${BUILD_DIR}/python"
CACHE_DIR="${BUILD_DIR}/.cache"
TARBALL_PATH="${CACHE_DIR}/${TARBALL_NAME}"

mkdir -p "${CACHE_DIR}"

if [ -x "${PYTHON_DIR}/bin/python3" ]; then
    echo "✓ Python ${PY_VERSION} already present at ${PYTHON_DIR}"
    "${PYTHON_DIR}/bin/python3" --version
    exit 0
fi

if [ ! -f "${TARBALL_PATH}" ]; then
    echo "→ Downloading ${TARBALL_NAME} (~22 MB)..."
    curl -L --fail --progress-bar -o "${TARBALL_PATH}" "${URL}"
else
    echo "✓ Cached tarball: ${TARBALL_PATH}"
fi

echo "→ Extracting into ${PYTHON_DIR}..."
rm -rf "${PYTHON_DIR}"
mkdir -p "${PYTHON_DIR}"
# The tarball extracts a top-level "python/" dir — strip it.
tar -xzf "${TARBALL_PATH}" -C "${BUILD_DIR}" --strip-components=0
# After extraction we have BUILD_DIR/python/ already. Done.

echo "✓ Python bundled at ${PYTHON_DIR}"
"${PYTHON_DIR}/bin/python3" --version
echo "  Size: $(du -sh "${PYTHON_DIR}" | awk '{print $1}')"
