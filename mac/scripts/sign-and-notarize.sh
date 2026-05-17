#!/usr/bin/env bash
# Sign + notarize + staple the .app and DMG so end users don't see a Gatekeeper
# warning. This script is the single entry point used by both local releases
# and CI (.github/workflows/release.yml).
#
# ┌─────────────────────────── Inputs ────────────────────────────────────────┐
# │ Positional (optional):                                                   │
# │   $1  APP_PATH        — default: <mac>/dist/ClaudeWatch.app              │
# │   $2  DMG_PATH        — default: <mac>/dist/ClaudeWatch.dmg              │
# │                                                                          │
# │ Notarization credentials — provide EITHER:                               │
# │   (a) NOTARY_PROFILE  — a `xcrun notarytool store-credentials` profile   │
# │                         (recommended for local dev; default name:        │
# │                         "claudewatch")                                   │
# │   (b) APPLE_ID_EMAIL + APPLE_ID_PASSWORD + APPLE_TEAM_ID                 │
# │                         (used by CI; env-var path)                       │
# │                                                                          │
# │ Signing identity — provide ONE of:                                       │
# │   SIGN_IDENTITY            — full "Developer ID Application: …(TEAMID)"  │
# │   CW_SIGN_IDENTITY         — same, alias for SIGN_IDENTITY               │
# │   DEVELOPER_ID_APP_CERT_ID — SHA-1 fingerprint (used by CI after import) │
# │                                                                          │
# │ Toggles:                                                                 │
# │   SKIP_NOTARIZE=1     — sign only; skip Apple upload + stapling          │
# │   FORCE_RESIGN=1      — re-sign even if the bundle is already signed     │
# │                         and valid (default skips when valid).            │
# └──────────────────────────────────────────────────────────────────────────┘
#
# One-time setup is documented in mac/docs/code-signing.md. CI secrets are
# wired in .github/workflows/release.yml — see the "Sign + notarize" step.

set -euo pipefail

# ───── Cosmetics ────────────────────────────────────────────────────────────
# ANSI colors are gated on a TTY so log files stay clean.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_OK="\033[32m"; C_INFO="\033[36m"; C_WARN="\033[33m"; C_ERR="\033[31m"; C_OFF="\033[0m"
else
    C_OK=""; C_INFO=""; C_WARN=""; C_ERR=""; C_OFF=""
fi

log()  { printf "%b→%b %s\n" "$C_INFO" "$C_OFF" "$*"; }
ok()   { printf "%b✓%b %s\n" "$C_OK"   "$C_OFF" "$*"; }
warn() { printf "%b⚠%b %s\n" "$C_WARN" "$C_OFF" "$*" >&2; }
err()  { printf "%b✗%b %s\n" "$C_ERR"  "$C_OFF" "$*" >&2; }
die()  { err "$*"; exit 1; }

# ───── Paths ────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAC_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DEFAULT="${MAC_DIR}/dist"
APP="${1:-${DIST_DEFAULT}/ClaudeWatch.app}"
DMG="${2:-${DIST_DEFAULT}/ClaudeWatch.dmg}"
ENTITLEMENTS="${MAC_DIR}/Entitlements.plist"

[ -d "${APP}" ] || die "${APP} not found. Run \`make app\` first."

# ───── Resolve signing identity ─────────────────────────────────────────────
# Three valid sources, in order of preference:
#   1. SIGN_IDENTITY / CW_SIGN_IDENTITY — friendly "Developer ID Application: …"
#   2. DEVELOPER_ID_APP_CERT_ID         — SHA-1 fingerprint (used by CI)
#   3. (none) → fail with helpful list of available identities
SIGN_IDENTITY="${SIGN_IDENTITY:-${CW_SIGN_IDENTITY:-${DEVELOPER_ID_APP_CERT_ID:-}}}"

if [ -z "${SIGN_IDENTITY}" ]; then
    warn "No signing identity set."
    echo "" >&2
    echo "Available codesigning identities on this machine:" >&2
    security find-identity -v -p codesigning 2>/dev/null | head -10 >&2 || \
        echo "  (none — install a Developer ID Application cert first)" >&2
    echo "" >&2
    cat >&2 <<EOF
Set ONE of these and re-run:
  SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
  DEVELOPER_ID_APP_CERT_ID="<SHA-1 fingerprint>"   # used by CI

One-time setup walkthrough: mac/docs/code-signing.md
EOF
    exit 1
fi

# ───── Resolve notarization credentials ─────────────────────────────────────
# Two valid modes: env-var trio (CI) OR a stored keychain profile (local dev).
# If neither is configured but the user passed SKIP_NOTARIZE=1 we don't care.
NOTARY_PROFILE="${NOTARY_PROFILE:-claudewatch}"
NOTARY_MODE=""

if [ "${SKIP_NOTARIZE:-0}" != "1" ]; then
    if [ -n "${APPLE_ID_EMAIL:-}" ] && [ -n "${APPLE_ID_PASSWORD:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ]; then
        NOTARY_MODE="env"
        log "Notarization credentials: env-var mode (Apple ID: ${APPLE_ID_EMAIL})"
    elif xcrun notarytool history --keychain-profile "${NOTARY_PROFILE}" >/dev/null 2>&1; then
        NOTARY_MODE="profile"
        log "Notarization credentials: keychain profile '${NOTARY_PROFILE}'"
    else
        cat >&2 <<EOF
$(printf "%b✗%b" "$C_ERR" "$C_OFF") No notarization credentials found.

Pick ONE of:

  (a) Env vars (CI / scripted):
        export APPLE_ID_EMAIL='you@example.com'
        export APPLE_ID_PASSWORD='abcd-efgh-ijkl-mnop'   # app-specific password
        export APPLE_TEAM_ID='ABCDE12345'

  (b) Stored keychain profile (local dev — recommended, set up once):
        xcrun notarytool store-credentials ${NOTARY_PROFILE} \\
            --apple-id 'you@example.com' \\
            --team-id  'ABCDE12345' \\
            --password 'abcd-efgh-ijkl-mnop'

Or set SKIP_NOTARIZE=1 to only sign (Gatekeeper will still warn on fresh Macs).
EOF
        exit 1
    fi
fi

# Build a portable `notarytool` arg list once so both submit + log calls
# stay in sync.
notary_args() {
    if [ "${NOTARY_MODE}" = "env" ]; then
        printf -- '--apple-id\n%s\n--password\n%s\n--team-id\n%s\n' \
            "${APPLE_ID_EMAIL}" "${APPLE_ID_PASSWORD}" "${APPLE_TEAM_ID}"
    else
        printf -- '--keychain-profile\n%s\n' "${NOTARY_PROFILE}"
    fi
}

# ───── Entitlements ─────────────────────────────────────────────────────────
# Write a default Entitlements.plist if the maintainer hasn't customized one.
# These flags are what python-build-standalone + Sparkle need under the
# hardened runtime. See `mac/docs/code-signing.md` → "Why these entitlements".
if [ ! -f "${ENTITLEMENTS}" ]; then
    log "Writing default Entitlements.plist (hardened runtime + Python compat)"
    cat > "${ENTITLEMENTS}" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <!-- Bundled Python interpreter uses ctypes / loads dylibs the OS sees as
         unsigned because they were rebuilt by pip wheels. Required for any
         embedded Python that imports anything from PyPI. -->
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    <true/>
    <key>com.apple.security.cs.allow-dyld-environment-variables</key>
    <true/>
    <key>com.apple.security.cs.disable-library-validation</key>
    <true/>
    <!-- Hardened runtime requires every capability to be declared. -->
    <key>com.apple.security.network.client</key>
    <true/>
    <key>com.apple.security.automation.apple-events</key>
    <true/>
</dict>
</plist>
EOF
fi

# ───── Idempotency: skip resign if already signed and valid ─────────────────
# A clean Sparkle update flow re-runs the signing pipeline often; redoing the
# work when nothing changed wastes ~20-30s per run. FORCE_RESIGN=1 bypasses.
already_signed_and_valid() {
    codesign --verify --deep --strict "${APP}" >/dev/null 2>&1 || return 1
    # And make sure it's our identity, not the python-build-standalone
    # ad-hoc signature that ships in the wheel.
    codesign -dvv "${APP}" 2>&1 | grep -q "Authority=Developer ID Application" || return 1
    return 0
}

if [ "${FORCE_RESIGN:-0}" != "1" ] && already_signed_and_valid; then
    ok "Bundle is already signed and valid — skipping codesign (set FORCE_RESIGN=1 to override)."
else
    # ───── Sign nested binaries first (inside-out) ──────────────────────────
    # `--deep` on the outer bundle handles most things but misses dylibs that
    # are dynamically loaded via dlopen. Walk and sign them explicitly so
    # notarization doesn't reject a stray unsigned .so.
    log "Signing nested binaries (Python, .dylib, .so) inside the bundle"
    nested_count=0
    while IFS= read -r f; do
        codesign --force --timestamp --options runtime \
            --entitlements "${ENTITLEMENTS}" \
            --sign "${SIGN_IDENTITY}" \
            "${f}" 2>&1 | grep -v "replacing existing signature" || true
        nested_count=$((nested_count + 1))
    done < <(find "${APP}" -type f \( -name "*.dylib" -o -name "*.so" -o -name "python3*" \))
    log "  signed ${nested_count} nested binaries"

    log "Signing the .app bundle (hardened runtime)"
    codesign --force --deep --timestamp --options runtime \
        --entitlements "${ENTITLEMENTS}" \
        --sign "${SIGN_IDENTITY}" \
        "${APP}"

    log "Verifying signature"
    codesign --verify --deep --strict --verbose=2 "${APP}"
    spctl --assess --type execute --verbose "${APP}" 2>&1 || \
        log "  (spctl will pass after notarization — current state expected)"
fi

# ───── SKIP_NOTARIZE early-exit ─────────────────────────────────────────────
if [ "${SKIP_NOTARIZE:-0}" = "1" ]; then
    ok "Signed (notarization skipped via SKIP_NOTARIZE=1)"
    exit 0
fi

# ───── Re-sign the DMG (so it bundles the signed .app) ──────────────────────
# Order matters: the DMG must contain the already-signed .app, and the DMG
# itself must also be signed before notarization. If the caller never built a
# DMG, we fall back to notarizing the .app via a zip wrapper.
if [ ! -f "${DMG}" ]; then
    warn "DMG not found at ${DMG} — falling back to .app notarization"

    ZIP="$(dirname "${APP}")/ClaudeWatch.zip"
    log "Zipping .app for notarytool upload"
    ditto -c -k --sequesterRsrc --keepParent "${APP}" "${ZIP}"

    log "Submitting ${ZIP} to Apple notary (this can take 1-15 min)"
    submission_id=""
    if ! mapfile -t notary_args_arr < <(notary_args); then :; fi
    if ! out=$(xcrun notarytool submit "${ZIP}" "${notary_args_arr[@]}" --wait 2>&1); then
        echo "${out}" >&2
        submission_id=$(echo "${out}" | grep -E '^[[:space:]]*id:' | head -1 | awk '{print $2}')
        err "Notarization failed."
        if [ -n "${submission_id}" ]; then
            err "Run \`xcrun notarytool log ${submission_id} ${notary_args_arr[*]}\` for details."
        fi
        rm -f "${ZIP}"
        exit 1
    fi
    echo "${out}"

    log "Stapling notarization ticket to the .app"
    xcrun stapler staple "${APP}"
    rm -f "${ZIP}"
    ok "Signed + notarized + stapled (.app only — distribute via the bundle)"
    exit 0
fi

log "Re-signing the DMG (so the signed .app inside is preserved)"
codesign --force --sign "${SIGN_IDENTITY}" --timestamp "${DMG}"

# ───── Notarize the DMG ─────────────────────────────────────────────────────
log "Submitting ${DMG} to Apple notary service (this can take 1-15 min)"
mapfile -t notary_args_arr < <(notary_args)

# `notarytool submit --wait` blocks until the service returns, then exits 0
# on Accepted and non-zero on Invalid. We capture stdout so we can extract
# the submission ID for a follow-up `notarytool log` if it failed.
set +e
submit_out=$(xcrun notarytool submit "${DMG}" "${notary_args_arr[@]}" --wait 2>&1)
submit_rc=$?
set -e
echo "${submit_out}"

if [ ${submit_rc} -ne 0 ]; then
    submission_id=$(echo "${submit_out}" | grep -E '^[[:space:]]*id:' | head -1 | awk '{print $2}')
    err "Notarization rejected (exit ${submit_rc})."
    if [ -n "${submission_id}" ]; then
        err "Inspect the developer log:"
        err "  xcrun notarytool log ${submission_id} ${notary_args_arr[*]}"
        err "Common causes: missing hardened runtime, unsigned nested binary,"
        err "expired cert, wrong team ID, or missing entitlement."
    fi
    exit ${submit_rc}
fi

# ───── Staple ───────────────────────────────────────────────────────────────
# Stapling embeds the notarization ticket in the DMG (and the .app inside
# it) so users without internet on first launch still get a clean Gatekeeper
# pass.
log "Stapling notarization ticket to the DMG"
xcrun stapler staple "${DMG}"

# Best-effort: also staple the .app inside dist/ (mostly useful when the
# .app is shipped separately, e.g. via Sparkle delta updates).
log "Stapling notarization ticket to the .app (in dist/)"
xcrun stapler staple "${APP}" || warn "Could not staple .app (DMG staple is what matters for distribution)"

# ───── Final verification (informational) ───────────────────────────────────
log "Final Gatekeeper assessment"
spctl --assess --type install --verbose=4 "${DMG}" 2>&1 || \
    warn "spctl assessment returned non-zero — inspect output above"

ok "Signed + notarized + stapled."
echo "  Distribute: ${DMG}"
