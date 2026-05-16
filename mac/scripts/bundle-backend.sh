#!/usr/bin/env bash
# Installs the claudewatch backend + its runtime dependencies into the bundled
# python's site-packages, so the .app can launch uvicorn with no external pip.
#
# Run AFTER `scripts/download-python.sh`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${MAC_DIR}/.." && pwd)"
PYTHON_DIR="${MAC_DIR}/build/python"
PYTHON="${PYTHON_DIR}/bin/python3"

if [ ! -x "${PYTHON}" ]; then
    echo "✗ Bundled python not found. Run scripts/download-python.sh first." >&2
    exit 1
fi

echo "→ Upgrading pip in bundled python"
"${PYTHON}" -m pip install --upgrade pip --quiet

echo "→ Installing claudewatch + runtime dependencies into bundled python"
# Install in-place from the repo. --no-warn-script-location silences the
# warnings about /bin not being on PATH (we never invoke the scripts directly;
# we use python -m uvicorn).
"${PYTHON}" -m pip install --no-warn-script-location "${REPO_ROOT}" --quiet

echo "✓ Backend bundled. Site-packages size:"
SP="${PYTHON_DIR}/lib/python3.12/site-packages"
du -sh "${SP}" | awk '{print "  " $1 "  " $2}'

echo "→ Smoke test: import works?"
"${PYTHON}" -c "from backend.server import app; print('  ✓ backend.server imports')"
"${PYTHON}" -c "import uvicorn, fastapi; print(f'  ✓ uvicorn {uvicorn.__version__}, fastapi {fastapi.__version__}')"
