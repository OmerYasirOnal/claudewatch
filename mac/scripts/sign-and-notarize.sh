#!/usr/bin/env bash
# Sign + notarize + staple the .app and DMG so end users don't see a Gatekeeper warning.
#
# Requires:
# - A "Developer ID Application" certificate in your login keychain
# - An "App-specific password" for notarization stored as a keychain profile
#   (one-time setup: `xcrun notarytool store-credentials --keychain-profile claudewatch ...`)
#
# Environment overrides:
#   SIGN_IDENTITY     — full "Developer ID Application: Name (TEAMID)" string,
#                       or set CW_SIGN_IDENTITY in your shell
#   NOTARY_PROFILE    — keychain profile name (default: claudewatch)
#   SKIP_NOTARIZE     — set to 1 to only sign locally (no upload, no staple)
#
# Run after `make app` (or `make dmg`).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST="${MAC_DIR}/dist"
APP="${DIST}/ClaudeWatch.app"
DMG="${DIST}/ClaudeWatch.dmg"
ENTITLEMENTS="${MAC_DIR}/Entitlements.plist"

if [ ! -d "${APP}" ]; then
    echo "✗ ${APP} not found. Run \`make app\` first." >&2
    exit 1
fi

SIGN_IDENTITY="${SIGN_IDENTITY:-${CW_SIGN_IDENTITY:-}}"
if [ -z "${SIGN_IDENTITY}" ]; then
    echo "ℹ  Available signing identities:"
    security find-identity -v -p codesigning | head -10
    echo ""
    echo "Set SIGN_IDENTITY=\"Developer ID Application: Your Name (TEAMID)\" and re-run." >&2
    exit 1
fi

NOTARY_PROFILE="${NOTARY_PROFILE:-claudewatch}"

if [ ! -f "${ENTITLEMENTS}" ]; then
    echo "→ Writing default Entitlements.plist"
    cat > "${ENTITLEMENTS}" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <!-- Bundled Python interpreter calls system libraries (needed for runtime). -->
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    <true/>
    <key>com.apple.security.cs.allow-dyld-environment-variables</key>
    <true/>
    <key>com.apple.security.cs.disable-library-validation</key>
    <true/>
    <!-- Hardened runtime requires these to be explicit. -->
    <key>com.apple.security.network.client</key>
    <true/>
    <key>com.apple.security.automation.apple-events</key>
    <true/>
</dict>
</plist>
EOF
fi

echo "→ Signing nested binaries inside the bundle (Python + dylibs)"
# Sign every nested executable and dylib first (deep signing).
# python-build-standalone ships pre-signed binaries — we re-sign with our cert.
find "${APP}" -type f \( -name "*.dylib" -o -name "*.so" -o -name "python3*" \) | while read -r f; do
    codesign --force --timestamp --options runtime \
        --entitlements "${ENTITLEMENTS}" \
        --sign "${SIGN_IDENTITY}" \
        "${f}" 2>&1 | grep -v "replacing existing signature" || true
done

echo "→ Signing the .app bundle"
codesign --force --deep --timestamp --options runtime \
    --entitlements "${ENTITLEMENTS}" \
    --sign "${SIGN_IDENTITY}" \
    "${APP}"

echo "→ Verifying signature"
codesign --verify --deep --strict --verbose=2 "${APP}"
spctl --assess --type execute --verbose "${APP}" || echo "  (spctl will pass after notarization)"

if [ "${SKIP_NOTARIZE:-0}" = "1" ]; then
    echo "✓ Signed (notarization skipped via SKIP_NOTARIZE=1)"
    exit 0
fi

# Notarize the DMG (preferred — staples to the DMG and the .app inside)
if [ -f "${DMG}" ]; then
    echo "→ Submitting ${DMG} to Apple notary service (this can take 1-5 min)"
    xcrun notarytool submit "${DMG}" \
        --keychain-profile "${NOTARY_PROFILE}" \
        --wait
    echo "→ Stapling notarization ticket to the DMG"
    xcrun stapler staple "${DMG}"
    echo "→ Stapling notarization ticket to the .app (inside the DMG mount)"
    xcrun stapler staple "${APP}"
    echo ""
    echo "✓ Signed + notarized + stapled."
    echo "  Distribute: ${DMG}"
else
    # Fall back to notarizing the .app directly (zip it first)
    ZIP="${DIST}/ClaudeWatch.zip"
    echo "→ DMG not found; zipping .app for notarization"
    ditto -c -k --sequesterRsrc --keepParent "${APP}" "${ZIP}"
    xcrun notarytool submit "${ZIP}" \
        --keychain-profile "${NOTARY_PROFILE}" \
        --wait
    xcrun stapler staple "${APP}"
    rm -f "${ZIP}"
    echo "✓ Signed + notarized + stapled."
    echo "  Distribute: ${APP}"
fi
