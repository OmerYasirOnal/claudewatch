# Code signing + notarization

Without signing, end users see a Gatekeeper "App is damaged and can't be
opened" warning on first launch and have to right-click → Open. For
frictionless distribution we need:

1. A **Developer ID Application** certificate (Apple Developer Program, $99/yr)
2. Hardened-runtime codesigning of the .app and DMG
3. Notarization upload to Apple's notary service
4. Stapling the notarization ticket to the DMG

This doc walks through the one-time setup (local + CI), the per-release
flow, and the most common failures.

---

## First-time setup (do this once, ever)

### 1. Enroll in the Apple Developer Program

https://developer.apple.com/programs/ — $99/yr. Personal enrollment is fine
for this app. Organization enrollment requires a D-U-N-S number and takes
~1 week longer.

### 2. Generate a Developer ID Application certificate

In Xcode: **Settings → Accounts → Manage Certificates** → `+` → **Developer
ID Application**.

(Or via the developer portal: Certificates → `+` → Developer ID Application,
then download the .cer and double-click to install in Keychain.)

Verify it landed in your login keychain:

```bash
security find-identity -v -p codesigning
```

You should see a line like:

```
1) ABCD1234EF567890...  "Developer ID Application: Your Name (TEAMID)"
```

The 10-character `TEAMID` is your **Team ID** — needed for notarization.

### 3. Generate an app-specific password

Notarization needs an *app-specific* password, NOT your real Apple ID
password. Create one at:

  https://appleid.apple.com → Sign-In and Security → App-Specific Passwords → `+`

Name it something like "claudewatch-notarize" and save the 16-char password
(format: `abcd-efgh-ijkl-mnop`) somewhere safe.

### 4. Pick a credential storage strategy

You can do local signing with EITHER mode below; CI uses env-var mode.

**Option A — Keychain profile (local dev, recommended).**

```bash
xcrun notarytool store-credentials claudewatch \
    --apple-id "your@email.com" \
    --team-id  "TEAMID" \
    --password "abcd-efgh-ijkl-mnop"
```

`claudewatch` is the profile name. `sign-and-notarize.sh` looks it up by
default — no env vars needed afterwards.

**Option B — Env vars (CI, ephemeral shells).**

```bash
export APPLE_ID_EMAIL="your@email.com"
export APPLE_ID_PASSWORD="abcd-efgh-ijkl-mnop"
export APPLE_TEAM_ID="TEAMID"
```

The script auto-detects which mode you've configured.

---

## CI setup (one-time, for automated release tagging)

The `.github/workflows/release.yml` workflow signs + notarizes automatically
**if all five secrets are present**. If any secret is missing the workflow
still completes — it just ships an unsigned DMG (Gatekeeper will warn).

### 1. Export your cert to a P12

In Keychain Access → My Certificates → right-click your "Developer ID
Application" cert → Export → set a strong password. Save it as `cert.p12`.

### 2. Base64-encode it for GitHub secret storage

```bash
base64 -i cert.p12 -o cert.p12.b64
```

### 3. Add the five repo secrets

```bash
gh secret set DEVELOPER_ID_APP_CERT          < cert.p12.b64
gh secret set DEVELOPER_ID_APP_CERT_PASSWORD --body "<p12 export password>"
gh secret set APPLE_ID_EMAIL                 --body "your@email.com"
gh secret set APPLE_ID_PASSWORD              --body "abcd-efgh-ijkl-mnop"
gh secret set APPLE_TEAM_ID                  --body "TEAMID"
```

Then delete the local files: `rm cert.p12 cert.p12.b64`.

### 4. Tag a release to trigger the workflow

```bash
git tag v0.4.0
git push --tags
```

The first run takes 5–15 min because Apple's notary service queues
submissions. Subsequent runs are usually ~5 min.

Watch it: `gh run watch` or open the Actions tab.

---

## Per-release flow (local — when you don't want to use CI)

```bash
cd mac

# 1. Build everything from clean state.
make release-signed

# release-signed is a convenience target that runs:
#   make clean app dmg sign-notarize
# i.e. it ends with a signed + notarized + stapled DMG in dist/.
```

If you want finer control:

```bash
make app
make dmg
make sign-notarize          # uses keychain profile (default)
# or:
SKIP_NOTARIZE=1 make sign   # sign only, for local testing
```

Verify the result will pass Gatekeeper on a fresh Mac:

```bash
spctl --assess --type install --verbose dist/ClaudeWatch.dmg
# Expected: "dist/ClaudeWatch.dmg: accepted"
#           "source=Notarized Developer ID"
```

Upload `dist/ClaudeWatch.dmg` (and `.sha256`) to GitHub Releases.

---

## Why these entitlements?

`mac/Entitlements.plist` enables exactly what python-build-standalone and
Sparkle need under the hardened runtime:

| Entitlement                                          | Why                                                      |
|------------------------------------------------------|----------------------------------------------------------|
| `cs.allow-unsigned-executable-memory`                | CPython's `ctypes` JITs trampolines for foreign calls    |
| `cs.allow-dyld-environment-variables`                | We set `PYTHONHOME` / `PYTHONPATH` for the bundled tree  |
| `cs.disable-library-validation`                      | pip-installed wheels ship dylibs signed by a third party |
| `network.client`                                     | FastAPI binds 127.0.0.1; updater fetches the appcast     |
| `automation.apple-events`                            | Our focus/new-session helpers script iTerm via AppleScript |

If you tighten these later, re-test notarization end-to-end — even
removing one of the Python entitlements typically triggers a notarization
rejection along the lines of "The binary uses an SDK older than ...".

---

## Troubleshooting

### "errSecInternalComponent" during codesign

Your keychain is locked. Run `security unlock-keychain ~/Library/Keychains/login.keychain-db`
and re-run. In CI, this means the `set-key-partition-list` step didn't
include `apple-tool:` — check the workflow logs.

### Notarization rejected — "The binary is not signed with a valid Developer ID certificate"

Your cert is either an "Apple Development" or "Mac Developer" cert, not a
**Developer ID Application** cert. They're different products and only the
last one can be notarized. Re-issue via Xcode → Settings → Accounts →
Manage Certificates → `+` → **Developer ID Application**.

### Notarization rejected — "The executable does not have the hardened runtime enabled"

A nested binary (typically a Python `.so`, or a CLI helper inside
`Resources/`) was missed by the deep signer. `sign-and-notarize.sh` walks
the bundle and signs each one explicitly, but if you added new bundled
binaries since the last release, run:

```bash
xcrun notarytool log <submission-id> --keychain-profile claudewatch
```

and look at the `path` field of each rejected file. Add the file's
extension to the `find` clause in `sign-and-notarize.sh` and rerun.

### Notarization rejected — "Invalid team ID"

The `APPLE_TEAM_ID` env var doesn't match the team the certificate was
issued to. Recover the right team ID with:

```bash
security find-identity -v -p codesigning \
  | grep "Developer ID Application" | sed 's/.*(\(.*\)).*/\1/'
```

### Stapler error "CloudKit query for ClaudeWatch.dmg failed: 2"

Apple's notary service hasn't propagated the ticket yet. Wait 60 s and
retry `xcrun stapler staple dist/ClaudeWatch.dmg`. If it still fails
after 5 min, re-submit (the submission was probably rejected and you
didn't notice).

### "App is damaged and can't be opened" on a user's Mac after a signed release

99% of the time this means the DMG wasn't *stapled* (so Gatekeeper hits
Apple to verify and gets a stale/incomplete result). Verify locally:

```bash
xcrun stapler validate dist/ClaudeWatch.dmg
# Expected: "The validate action worked!"
```

If validate fails, re-run `make sign-notarize` (the script is idempotent
when the bundle is already signed — it'll just redo the staple).

### Local signing works but CI signing fails with "no identity found"

The keychain import in the workflow worked, but `find-identity` returned
empty when filtered by `Developer ID Application`. Usually means the P12
you exported didn't include the private key (export from Keychain Access
→ **My Certificates**, NOT → Certificates). Re-export and re-set the
secret.

---

## Why not just disable Gatekeeper for users?

Telling users to `xattr -dr com.apple.quarantine ClaudeWatch.app` works but
is a bad pattern — Apple is increasingly hostile to unsigned local-network
apps, and macOS 26 may remove the right-click-to-open escape hatch
entirely. Notarization is the right call for any app you actually want
people to use.

## Alternative: ship a notarized Homebrew Cask

For semi-technical users a Homebrew Cask formula avoids the need for them
to do anything special. Out of scope for this V1 doc — but once the
signing pipeline is live, generating a Cask is a 20-line PR against
homebrew-cask.
