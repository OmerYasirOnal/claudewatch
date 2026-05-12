#!/usr/bin/env bash
set -euo pipefail
"$HOME/.local/bin/claudewatch" stop || true
"$HOME/.local/bin/claudewatch" uninstall || true
rm -f "$HOME/.local/bin/claudewatch"
echo "✓ ClaudeWatch removed. (Project files at $(dirname "$0")/.. are kept.)"
