# ClaudeWatch — Notes for Claude Code

Local web dashboard that monitors all running `claude` CLI sessions on macOS.

## Run

```bash
source .venv/bin/activate
claudewatch start              # foreground
pytest -q                      # tests
uvicorn backend.server:app --reload --port 7788   # dev mode
```

## Layout

- `backend/detectors/` — one file per data source (process, iterm, tmux, conversation_log, filesystem_watch, git_context, linker)
- `backend/api/` — FastAPI routers, one per resource
- `backend/applescript/` — AppleScript templates for focus + new-session actions
- `backend/cli.py` — `claudewatch` CLI entrypoint (typer)
- `backend/server.py` — FastAPI lifespan + scheduler loop
- `frontend/` — single-page HTML + Alpine.js + Tailwind CDN
- `tests/` — pytest with fixtures from real conversation logs

## Conversation log format

`~/.claude/projects/<cwd-as-dashes>/<session-uuid>.jsonl` — JSONL with one entry per line.
Common keys: `type`, `sessionId`, `cwd`, `gitBranch`, `version`, `timestamp`.
Assistant entries have `message.model`, `message.usage`, `message.content[]`.

## Conventions

- Pydantic v2; never use `dict()` (use `model_dump`)
- All times stored UTC, displayed in local
- Filesystem paths: always `Path`, not strings, until JSON serialization
- AppleScript paths passed via array args; never string-interpolate into shell

## Testing

`pytest -q` runs everything. Unit tests don't require any running services.
Live integration tests aren't in CI — drive them manually with the CLI:

```bash
claudewatch start --daemon
claudewatch sessions --once
claudewatch stop
```
