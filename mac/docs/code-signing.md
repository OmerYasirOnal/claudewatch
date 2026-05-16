# Code signing + notarization

Without signing, end users see a Gatekeeper warning on first launch and have
to right-click → Open. For frictionless distribution we need:

1. A **Developer ID Application** certificate (Apple Developer Program, \$99/yr)
2. Notarization upload to Apple
3. Stapling the notarization ticket to the DMG

This doc walks through the one-time setup and the per-release flow.

## One-time setup

### 1. Enroll in the Apple Developer Program

https://developer.apple.com/programs/ — \$99/yr. Personal enrollment is fine
for this app. Organization enrollment needs a D-U-N-S number.

### 2. Create a Developer ID Application certificate

In Xcode: **Settings → Accounts → Manage Certificates** → `+` → **Developer ID
Application**.

Or via the developer portal: Certificates → `+` → Developer ID Application.

The certificate ends up in your login keychain. Verify with:

```bash
security find-identity -v -p codesigning
```

You should see a line like:
```
1) ABCD1234... "Developer ID Application: Your Name (TEAMID)"
```

### 3. Store an app-specific password for notarytool

Create one at https://appleid.apple.com → Sign-In and Security → App-Specific Passwords → `+`.

Then save the credentials to your keychain (one-time):

```bash
xcrun notarytool store-credentials claudewatch \
    --apple-id "your@email.com" \
    --team-id "TEAMID" \
    --password "abcd-efgh-ijkl-mnop"
```

`claudewatch` here is just the **profile name** — used by the sign script.

## Per-release flow

```bash
cd mac
# 1. Build everything
make app
make dmg

# 2. Sign + notarize + staple in one go
export SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
./scripts/sign-and-notarize.sh
# (takes ~1-5 min for Apple's notary service to respond)

# 3. Verify it'll pass Gatekeeper on a fresh Mac:
spctl --assess --type execute --verbose dist/ClaudeWatch.app
# Should print: "dist/ClaudeWatch.app: accepted"
#                "source=Notarized Developer ID"
```

Upload `dist/ClaudeWatch.dmg` to GitHub Releases.

## Local-only signing (no notarization)

If you just want to sign locally for testing (skip the upload to Apple):

```bash
SKIP_NOTARIZE=1 ./scripts/sign-and-notarize.sh
```

This still requires a Developer ID Application certificate. The signed app
will pass Gatekeeper if the user has previously launched it via right-click →
Open, but a fresh download from another machine will still warn until
notarization.

## Troubleshooting

### "errSecInternalComponent" during codesign

Your keychain is locked. Run `security unlock-keychain` and re-run.

### Notarization fails with "Invalid Bundle" / "Hardened Runtime missing"

Run `codesign -dvvv dist/ClaudeWatch.app` to inspect the signature. Common
fixes:

- Add missing entitlement to `mac/Entitlements.plist`
- Re-run `sign-and-notarize.sh` (it does deep signing of nested executables —
  python-build-standalone ships pre-signed Python binaries that need to be
  re-signed with your identity)

### "The signature of the binary is invalid"

Usually means a stale notarized binary is still present. `make clean &&
make app && ./scripts/sign-and-notarize.sh`.

## Why not just disable Gatekeeper for users?

Telling users to `xattr -dr com.apple.quarantine ClaudeWatch.app` works but is
a bad pattern (and Apple is increasingly hostile to unsigned local-network
apps). Notarization is the right call for any app you actually want people to
use.

## Alternative: ship a notarized Homebrew Cask

For semi-technical users, a Homebrew Cask formula avoids the need for them to
do anything special. Out of scope for this V1 doc.
