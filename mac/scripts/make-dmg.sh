#!/usr/bin/env bash
# Wraps dist/ClaudeWatch.app into a drag-to-Applications DMG.
# Run AFTER `make app`. Output: dist/ClaudeWatch.dmg

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST="${MAC_DIR}/dist"
APP="${DIST}/ClaudeWatch.app"
DMG="${DIST}/ClaudeWatch.dmg"
STAGING="${DIST}/.dmg-staging"
VOLUME_NAME="ClaudeWatch"

if [ ! -d "${APP}" ]; then
    echo "✗ ${APP} not found. Run \`make app\` first." >&2
    exit 1
fi

rm -f "${DMG}"
rm -rf "${STAGING}"
mkdir -p "${STAGING}"

# Copy the app into the staging dir, then create the /Applications symlink
# so the DMG window shows the canonical drag-to-install layout.
cp -R "${APP}" "${STAGING}/"
ln -s /Applications "${STAGING}/Applications"

# Optional: a friendly DS_Store-style window layout would go here. Skipping
# for V1 — the default Finder grid view + the /Applications shortcut is
# enough to get users to drag-install.

echo "→ Creating ${DMG} from ${STAGING}"
hdiutil create \
    -volname "${VOLUME_NAME}" \
    -srcfolder "${STAGING}" \
    -ov \
    -format UDZO \
    -fs HFS+ \
    "${DMG}" \
    >/dev/null

rm -rf "${STAGING}"

echo "✓ DMG ready: ${DMG}"
du -sh "${DMG}" | awk '{print "  Size: " $1}'
echo ""
echo "  Test it: open ${DMG}"
echo "  Distribute: share the .dmg file. Users drag ClaudeWatch.app to /Applications."
