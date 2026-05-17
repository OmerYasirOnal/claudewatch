# Contributing to ClaudeWatch

Thanks for taking the time to contribute. ClaudeWatch is a small, local-only
tool — patches that keep it that way are very welcome.

## Repository layout

- `backend/` — FastAPI server, detectors, CLI (`claudewatch` entrypoint).
  - `backend/detectors/` — one file per data source (process, iterm, tmux,
    conversation_log, filesystem_watch, git_context, linker).
  - `backend/api/` — FastAPI routers, one per resource.
  - `backend/applescript/` — AppleScript templates for focus + new-session.
- `frontend/` — single-page HTML + Alpine.js + Tailwind CDN dashboard.
- `mac/` — native menu bar `.app` (Swift 6 / Xcode 16) that bundles the
  backend. See `mac/README.md` for build details.
- `tests/` — pytest suite (~290 tests), with fixtures from real conversation
  logs.
- `docs/` — architecture, API reference, troubleshooting, etc.
- `scripts/` — install + launchd plist + small helpers.

## Development setup

```bash
git clone https://github.com/OmerYasirOnal/claudewatch.git
cd claudewatch
./scripts/install.sh           # creates .venv and installs editable + dev deps
source .venv/bin/activate
```

If you skipped the script, the equivalent is:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Running tests

```bash
# Python suite — must pass before opening a PR.
pytest -q --timeout=30

# Lint + format check (matches CI).
ruff check backend/ tests/
ruff format --check backend/ tests/

# Swift / menu-bar app tests (only if you touched mac/).
cd mac && swift test
```

CI runs the Python suite on Python 3.10/3.11/3.12 (Ubuntu) and Swift build +
test on macos-15. Live integration tests (real running `claude` sessions) are
not in CI; drive them manually with `claudewatch start --daemon` and
`claudewatch sessions --once`.

## Branch naming

Use a short prefix that says what kind of change it is:

- `feat/<slug>` — new feature
- `fix/<slug>` — bug fix
- `chore/<slug>` — tooling, deps, docs, refactor with no behaviour change
- `test/<slug>` — tests only
- `docs/<slug>` — documentation only

Example: `fix/iterm-focus-on-detached-session`.

## Commit messages

We follow `type(scope): summary` for the subject, then an optional body
explaining the *why* (the diff already shows the *what*).

```
fix(detectors/iterm): handle detached panes without raising

The iterm detector assumed every visible session had a parent window; when
a user detaches a pane into its own window the lookup raised KeyError and
the whole detector loop crashed. Falls back to the pane's own session
identifier now.

Closes #142
```

Types we use: `feat`, `fix`, `chore`, `test`, `docs`, `refactor`, `perf`.
Scope is the package or area touched (`backend/api`, `detectors/iterm`,
`mac`, etc.).

## Pull requests

- Open against `main`.
- Fill in `.github/pull_request_template.md` — summary, linked issue, change
  list, test plan.
- Keep PRs focused. One logical change per PR is much easier to review.
- New behaviour needs new tests (use the existing fixture style under
  `tests/`).
- For changes that touch macOS APIs (AppleScript, iTerm, tmux), call out the
  manual test you ran in the test plan checkbox.
- Don't introduce new permission prompts or focus-stealing — both are
  explicit non-goals.

## Filing an issue

We use a lightweight audit-style format. Pick the closest template under
`.github/ISSUE_TEMPLATE/`:

- **Bug report** — what happened, expected behaviour, reproduction steps,
  macOS / ClaudeWatch / Python versions, relevant lines from
  `~/.claudewatch/logs/server.log`.
- **Feature request** — what you want, why it's useful, alternatives you
  considered.
- **Question** — free-form; check `docs/troubleshooting.md` first.

For anything security-sensitive, see [SECURITY.md](SECURITY.md) instead of
opening a public issue.

## Conventions (enforced by review)

- Pydantic v2; never use `.dict()` — use `model_dump()`.
- All times stored UTC, displayed local.
- Filesystem paths: always `pathlib.Path`, not strings, until JSON
  serialization.
- AppleScript paths passed via array args; never string-interpolate into a
  shell.

Thanks again — small, well-scoped PRs are very appreciated.
