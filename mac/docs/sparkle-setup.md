# Sparkle auto-update — setup & release recipe

ClaudeWatch ships with [Sparkle 2](https://sparkle-project.org) for in-app
"Check for updates…". The framework + UI plumbing is already wired (see
`mac/Sources/ClaudeWatchTray/Update/UpdateManager.swift` and the **Updates**
tab in Settings), but the **first signed release requires one-time key
generation** plus a per-release signing step.

This document is the runbook.

---

## One-time: generate the EdDSA key pair

Sparkle 2 uses Ed25519 signatures. The maintainer generates one key pair,
stashes the private half in the macOS Keychain, and bakes the public half
into the app bundle.

```bash
brew install --cask sparkle    # ships the sparkle-cli binaries
generate_keys                  # provided by the Sparkle distribution
```

`generate_keys` prints something like:

```
A key has been generated and saved in your Keychain. Add the following lines
to your Info.plist:

<key>SUPublicEDKey</key>
<string>AbCdEf...base64...==</string>
```

1. Copy the public key string.
2. Open `mac/Info.plist` and replace `REPLACE_WITH_GENERATED_KEY` with it.
3. **Commit only the public key.** The private key never leaves the
   maintainer's Keychain (`sparkle-cli` reads it from there on every sign).

> **Lost the private key?** You'll have to rotate (generate a new pair,
> ship an update signed with the old key that bumps `SUPublicEDKey`, then
> use the new key going forward). Plan ahead — back up the Keychain item to
> a password manager.

---

## Per-release: sign the DMG and update the appcast

Performed manually for now; the GitHub Actions workflow stub is in
`.github/workflows/release.yml` but the actual signing line is left
commented out because CI doesn't have the private key.

### 1. Build the DMG (existing flow)

```bash
cd mac
make UNIVERSAL=1 app
make dmg                       # produces mac/dist/ClaudeWatch.dmg
```

### 2. Sign the DMG

```bash
sign_update mac/dist/ClaudeWatch.dmg
```

Output looks like:

```
sparkle:edSignature="abc...==" length="12345678"
```

Copy that whole attribute pair.

### 3. Append the item to `docs/appcast.xml`

The file is served by GitHub Pages (see below) so editing it is the entire
"publish" step. Add a new `<item>` inside `<channel>`:

```xml
<item>
  <title>Version 0.4.0</title>
  <pubDate>Sun, 17 May 2026 12:00:00 +0000</pubDate>
  <sparkle:version>0.4.0</sparkle:version>
  <sparkle:shortVersionString>0.4.0</sparkle:shortVersionString>
  <sparkle:minimumSystemVersion>14.0</sparkle:minimumSystemVersion>
  <description><![CDATA[
    <h3>What's new in 0.4.0</h3>
    <ul>
      <li>Sparkle auto-updates</li>
      <li>...</li>
    </ul>
  ]]></description>
  <enclosure
    url="https://github.com/OmerYasirOnal/claudewatch/releases/download/v0.4.0/ClaudeWatch.dmg"
    sparkle:edSignature="abc...=="
    length="12345678"
    type="application/octet-stream" />
</item>
```

### 4. Commit & push the appcast

```bash
git add docs/appcast.xml
git commit -m "release: appcast entry for v0.4.0"
git push
```

GitHub Pages picks up `docs/` automatically (you must enable it once — see
below), so the appcast is live within a minute. Existing ClaudeWatch
installs notice the new entry on their next scheduled check (default
weekly) or immediately if the user clicks **Check for updates…**.

---

## One-time: enable GitHub Pages on `docs/`

The appcast must be served over HTTPS for Sparkle to fetch it.

1. GitHub → repo → Settings → Pages.
2. Source: **Deploy from a branch**.
3. Branch: `main`, folder: `/docs`.
4. Save.

The URL becomes `https://omeryasironal.github.io/claudewatch/appcast.xml`
— already wired as `SUFeedURL` in `mac/Info.plist`.

Verify:

```bash
curl -sI https://omeryasironal.github.io/claudewatch/appcast.xml | head -1
# expect: HTTP/2 200
```

---

## CI integration (deferred)

`.github/workflows/release.yml` builds and uploads the DMG today. Once a
private key has been generated and stored in a GitHub Actions secret
(`SPARKLE_ED_PRIVATE_KEY`), we can automate the signing step:

```yaml
- name: Sign update for Sparkle
  env:
    PRIV: ${{ secrets.SPARKLE_ED_PRIVATE_KEY }}
  run: |
    echo "$PRIV" > /tmp/sparkle.key
    sign_update -f /tmp/sparkle.key mac/dist/ClaudeWatch.dmg \
      > mac/dist/ClaudeWatch.dmg.sparkle-sig
    rm /tmp/sparkle.key
```

For now we leave this as a manual step because the project doesn't yet
have a sparkle key in CI. Adding the secret is a one-line follow-up.

---

## Strict concurrency notes

Sparkle 2.6.x's headers don't yet provide full Swift 6 `Sendable`
annotations for `SPUStandardUpdaterController`. We import the framework
with `@preconcurrency` (see `UpdateManager.swift`) so the compiler treats
its types as "trust me" instead of erroring on every call site.

When Sparkle ships a release with proper Sendable conformances we can drop
the `@preconcurrency` attribute — the rest of the wrapper is already
`@MainActor`-isolated.

---

## Why opt-in & weekly by default?

- **Opt-in**: the user installs a local-only monitor; surprising them with
  a launch-time network request to GitHub Pages is rude. The Updates tab
  defaults to **off**; the welcome flow can encourage opt-in later.
- **Weekly**: ClaudeWatch isn't security-critical and ships sub-monthly.
  Daily would generate more chatter than signal; monthly may let a real
  bugfix go unnoticed for too long. Weekly is the conventional default for
  Sparkle-using apps (Xcode, Transmission, OmniGroup suite).

The user can override both via Settings → Updates.

---

## Testing the pipeline end-to-end

Until the first signed release ships, you can dry-run the wrapper:

```bash
cd mac
swift test --filter UpdateManagerTests   # state-machine tests only
```

Don't try to exercise the live Sparkle flow on an unsigned dev build — it
will silently refuse to ingest the appcast because the public key is the
placeholder. After the one-time key generation step above, a `swift run`
build that finds a freshly-published appcast will surface the standard
Sparkle update sheet.
