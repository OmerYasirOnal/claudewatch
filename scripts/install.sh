#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

PYTHON="${PYTHON:-/opt/homebrew/bin/python3.12}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    PYTHON="python3"
fi

echo "==> Using $($PYTHON --version) at $(command -v $PYTHON)"

if [ ! -d ".venv" ]; then
    echo "==> Creating virtualenv at .venv"
    "$PYTHON" -m venv .venv
fi

# shellcheck source=/dev/null
source .venv/bin/activate
echo "==> Installing dependencies"
pip install --upgrade pip --quiet
pip install -e . --quiet

echo "==> Ensuring ~/.local/bin exists"
mkdir -p "$HOME/.local/bin"
ln -sf "$PROJECT_DIR/.venv/bin/claudewatch" "$HOME/.local/bin/claudewatch"

echo "==> Creating ~/.claudewatch"
mkdir -p "$HOME/.claudewatch/logs"

cat <<EOF

✓ ClaudeWatch installed.

Quick start:
  claudewatch start            # foreground
  claudewatch start --daemon   # background
  claudewatch open             # opens http://127.0.0.1:7788

Permissions to grant (one time):
  - iTerm2 → Settings → General → Magic → "Enable Python API"
  - System Settings → Privacy & Security → Automation → allow Terminal/Python → iTerm

If \`claudewatch\` isn't found, ensure \$HOME/.local/bin is on your PATH:
  echo 'export PATH="\$HOME/.local/bin:\$PATH"' >> ~/.zshrc

EOF
