# ClaudeWatch v0.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local macOS web dashboard that detects every running `claude` CLI process and surfaces process, location, conversation, filesystem, and git metadata in one place — with focus/halt/new-session actions.

**Architecture:** A single FastAPI/Uvicorn server bound to `127.0.0.1:7788` runs periodic detectors (psutil, iterm2, tmux, JSONL log parser, watchfiles, git) in async tasks, aggregates into a unified `ClaudeSession` model, persists to SQLite, and streams updates over SSE to an Alpine.js + Tailwind single-page frontend. Actions are mediated by sanitized AppleScript templates.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, psutil, iterm2, watchfiles, GitPython, aiosqlite, tomli/tomli-w, Pydantic v2, rich (for TUI). Frontend: vanilla HTML + Tailwind CDN + Alpine.js + EventSource.

**Spec reference:** `docs/superpowers/specs/2026-05-12-claudewatch-design.md`

---

## File Structure

```
backend/
  __init__.py
  server.py            # FastAPI app + lifespan + scheduler
  config.py            # TOML loader + defaults
  models.py            # Pydantic schemas
  pricing.py           # TokenUsage → USD
  state.py             # aiosqlite wrapper for 24h history
  permissions.py       # macOS perm probes
  cli.py               # `claudewatch` entry
  detectors/
    __init__.py
    process_detector.py
    iterm_detector.py
    tmux_detector.py
    conversation_log.py
    filesystem_watch.py
    git_context.py
    linker.py          # Aggregator
  api/
    __init__.py
    sessions.py
    actions.py
    stream.py
    health.py
    history.py
    config_api.py
  applescript/
    focus_iterm.applescript
    new_iterm_window.applescript
    new_iterm_tab.applescript
frontend/
  index.html
  app.js
  styles.css
scripts/
  install.sh
  start.sh
  stop.sh
  uninstall.sh
  launchd.plist
tests/
  conftest.py
  test_process_detector.py
  test_conversation_log.py
  test_linker.py
  test_pricing.py
  test_sanitization.py
pyproject.toml
README.md
LICENSE
CLAUDE.md
```

---

## Task 1: Project skeleton + pyproject

**Files:**
- Create: `pyproject.toml`, `backend/__init__.py`, `backend/detectors/__init__.py`, `backend/api/__init__.py`, `tests/__init__.py`, `tests/conftest.py`

- [ ] **Step 1.1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "claudewatch"
version = "0.2.0"
description = "Local system-wide Claude Code session monitor for macOS"
readme = "README.md"
license = "MIT"
requires-python = ">=3.11"
authors = [{ name = "Omer Yasir Onal" }]
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.32",
  "psutil>=6.1",
  "iterm2>=2.7",
  "watchfiles>=1.0",
  "GitPython>=3.1",
  "aiosqlite>=0.20",
  "tomli>=2.0; python_version<'3.11'",
  "tomli-w>=1.0",
  "pydantic>=2.10",
  "rich>=13.9",
  "typer>=0.15",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-asyncio>=0.24", "httpx>=0.28"]

[project.scripts]
claudewatch = "backend.cli:app"

[tool.hatch.build.targets.wheel]
packages = ["backend"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 1.2: Create empty `__init__.py` files**

```python
# backend/__init__.py
__version__ = "0.2.0"
```

Other `__init__.py` files: empty.

- [ ] **Step 1.3: Create `tests/conftest.py`**

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

- [ ] **Step 1.4: Set up venv and install editable**

Run:
```bash
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```
Expected: clean install, no errors.

- [ ] **Step 1.5: Commit**

```bash
git add pyproject.toml backend tests
git commit -m "feat: project skeleton + dependencies"
```

---

## Task 2: Pydantic models

**Files:**
- Create: `backend/models.py`

- [ ] **Step 2.1: Write `backend/models.py`** with all schemas from spec §5:
  - `TokenUsage`, `ToolCallStats`, `FileChange`, `GitContext`, `ClaudeSession`
  - `Literal` types for `status`, `location_type`
  - Computed `total_tokens` property on TokenUsage

- [ ] **Step 2.2: Write basic test** `tests/test_models.py` that instantiates each model with defaults.

- [ ] **Step 2.3: Run tests**
```bash
pytest tests/test_models.py -v
```

- [ ] **Step 2.4: Commit** `feat: pydantic schemas`

---

## Task 3: Config loader + pricing

**Files:**
- Create: `backend/config.py`, `backend/pricing.py`, `tests/test_pricing.py`

- [ ] **Step 3.1: `backend/config.py`**
  - `CONFIG_DIR = Path.home() / ".claudewatch"`
  - `CONFIG_PATH = CONFIG_DIR / "config.toml"`
  - `STATE_DB = CONFIG_DIR / "state.db"`
  - `DEFAULT_CONFIG` dict (all keys from spec §9)
  - `load_config()` → merges file over defaults; creates dir + writes default file if missing
  - `save_config(updates)` → write merged config back

- [ ] **Step 3.2: `backend/pricing.py`**
  - `estimate_cost(model: str, usage: TokenUsage, pricing: dict) → float | None`
  - Formula: `(input * input_price + output * output_price + cache_read * cache_read_price + cache_creation * cache_write_price) / 1_000_000`
  - Return `None` for unknown models.

- [ ] **Step 3.3: Tests**
```python
def test_pricing_known_model():
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    pricing = {"claude-opus-4-7": {"input": 15, "output": 75, "cache_read": 1.5, "cache_write": 18.75}}
    assert estimate_cost("claude-opus-4-7", usage, pricing) == 90.0

def test_pricing_unknown_model():
    assert estimate_cost("claude-mystery-9", TokenUsage(), {}) is None
```

- [ ] **Step 3.4: Commit** `feat: config loader + pricing calculator`

---

## Task 4: Process detector

**Files:**
- Create: `backend/detectors/process_detector.py`, `tests/test_process_detector.py`

- [ ] **Step 4.1: Implement `process_detector.py`**

Functions:
- `is_claude_process(proc: psutil.Process) -> bool` — name == `claude`, user matches, or cmdline contains the claude-code binary path.
- `parse_cmdline(cmdline: list[str]) -> dict` — extract `model`, `permission_mode` (from `--dangerously-skip-permissions`), `extra_flags`.
- `scan_claude_processes() -> list[dict]` — iterate `psutil.process_iter`, return raw process info dicts with: `pid`, `cwd`, `started_at` (ISO), `cpu_percent`, `memory_mb`, `cmdline_parsed`, `ppid`.
- `infer_status(cpu_history: list[float], last_activity_seconds_ago: int) -> str` — per spec §4.5 rules.

- [ ] **Step 4.2: Unit tests**

```python
def test_parse_cmdline_dangerously_skip():
    out = parse_cmdline(["claude", "--dangerously-skip-permissions"])
    assert out["permission_mode"] == "dangerously-skip"

def test_parse_cmdline_with_model():
    out = parse_cmdline(["claude", "--model", "claude-opus-4-7"])
    assert out["model"] == "claude-opus-4-7"

def test_infer_status_working():
    assert infer_status([10.0, 12.0, 8.0], 1) == "working"

def test_infer_status_idle():
    assert infer_status([0.0]*10, 600) == "idle"
```

- [ ] **Step 4.3: Manual integration check**
```bash
python -c "from backend.detectors.process_detector import scan_claude_processes; import json; print(json.dumps(scan_claude_processes(), indent=2, default=str))"
```
Expected: any currently-running `claude` processes appear in output.

- [ ] **Step 4.4: Commit** `feat: process detector with cmdline parse`

---

## Task 5: tmux detector

**Files:**
- Create: `backend/detectors/tmux_detector.py`

- [ ] **Step 5.1: Implement**

```python
def list_tmux_panes() -> list[TmuxPane]:
    """Run `tmux list-panes -a -F '#{session_name}|#{window_index}|#{pane_index}|#{pane_pid}|#{pane_current_command}|#{pane_current_path}'`.
    Returns empty list if tmux not running."""

def walk_pid_descendants(pid: int, max_depth: int = 10) -> set[int]:
    """Return all descendant PIDs of `pid` using psutil."""

def link_pids_to_tmux(claude_pids: list[int]) -> dict[int, TmuxLocation]:
    """For each claude pid, find a tmux pane whose pane_pid is in its parent chain or whose descendants include it."""
```

- [ ] **Step 5.2: Test in tmux session manually** — open tmux, run claude in a pane, ensure linker returns pane info.

- [ ] **Step 5.3: Commit** `feat: tmux pane linker`

---

## Task 6: iTerm detector

**Files:**
- Create: `backend/detectors/iterm_detector.py`

- [ ] **Step 6.1: Implement async iTerm linker**

```python
async def list_iterm_sessions() -> list[ItermSession]:
    """Use iterm2 lib: connect, walk windows/tabs/sessions, read jobPid variable.
    Return list of {window_id, tab_id, session_id, tab_title, job_pid}.
    Return [] on any connection error."""

async def link_pids_to_iterm(claude_pids: list[int]) -> dict[int, ItermLocation]:
    """For each claude pid, find an iterm session whose job_pid == pid or is in pid's ancestor chain.
    iTerm's jobPid is the foreground process; for claude run inside tmux, jobPid will be tmux server,
    so a tmux-detected pid won't match here — that's correct."""
```

- [ ] **Step 6.2: Failsafe** — if iterm2 connection raises, log once, return empty dict. Don't crash scheduler.

- [ ] **Step 6.3: Commit** `feat: iterm session linker via iterm2 Python API`

---

## Task 7: Conversation log parser

**Files:**
- Create: `backend/detectors/conversation_log.py`, `tests/test_conversation_log.py`, `tests/fixtures/sample_log.jsonl`

- [ ] **Step 7.1: Create fixture**

Take real lines from `~/.claude/projects/-Users-me-Projects-example/&lt;session-uuid&gt;.jsonl` (10 lines covering assistant + user + permission-mode + tool_use), sanitize any sensitive content, save to fixture.

- [ ] **Step 7.2: Implement `conversation_log.py`**

```python
def cwd_to_project_folder(cwd: str) -> str:
    """E.g. /Users/x/Projects/y -> -Users-x-Projects-y"""

def find_log_dir() -> Path | None:
    """Probe ~/.claude/projects/, ~/.config/claude/projects/, ~/Library/Application Support/Claude/projects/.
    Return the first one that exists."""

def find_logs_for_cwd(cwd: str) -> list[Path]:
    """Return all *.jsonl in matching project folder, sorted by mtime desc."""

@dataclass
class ParsedLog:
    conversation_id: str
    log_path: Path
    model: str | None
    cli_version: str | None
    permission_mode: str | None
    message_count: int
    usage: TokenUsage
    thinking_enabled: bool
    tool_calls: ToolCallStats
    last_activity_at: datetime

def parse_log(path: Path) -> ParsedLog:
    """Walk all JSONL entries.
    - Track latest permission-mode entry
    - Sum usage from assistant.message.usage
    - Count tool_use blocks in content arrays, by name
    - Detect thinking blocks
    - Track last entry timestamp"""
```

- [ ] **Step 7.3: Tests** — parse fixture, assert correct totals, model, permission_mode, tool breakdown.

- [ ] **Step 7.4: Commit** `feat: conversation log parser`

---

## Task 8: Linker (aggregator)

**Files:**
- Create: `backend/detectors/linker.py`, `tests/test_linker.py`

- [ ] **Step 8.1: Implement**

```python
async def build_sessions(config: dict, state: AppState) -> list[ClaudeSession]:
    """Pipeline:
    1. scan_claude_processes()
    2. link_pids_to_iterm + link_pids_to_tmux (concurrent)
    3. For each pid: find_logs_for_cwd(cwd) → parse_log() (cached by mtime)
    4. Get git context (cached 10s)
    5. Get recent file changes from filesystem watcher
    6. Apply pricing
    7. Build ClaudeSession instances
    Return list."""
```

`AppState` holds caches: `log_parse_cache: dict[Path, (mtime, ParsedLog)]`, `git_cache: dict[str, (ts, GitContext)]`, `file_changes: dict[str, deque[FileChange]]`.

- [ ] **Step 8.2: Commit** `feat: session aggregator linker`

---

## Task 9: Filesystem watcher

**Files:**
- Create: `backend/detectors/filesystem_watch.py`

- [ ] **Step 9.1: Implement**

```python
class FilesystemWatcher:
    def __init__(self, retention_minutes: int, ignore_patterns: list[str]):
        self.changes: dict[str, deque[FileChange]] = {}
        self.tasks: dict[str, asyncio.Task] = {}

    async def watch_cwd(self, cwd: str):
        """Use watchfiles.awatch; append to self.changes[cwd]; prune old."""

    async def sync_active_cwds(self, cwds: set[str]):
        """Start watchers for new cwds, stop for removed."""

    def get_recent(self, cwd: str, minutes: int) -> list[FileChange]:
        """Return changes within last `minutes`."""
```

Use a default ignore matcher (fnmatch over the relative path).

- [ ] **Step 9.2: Commit** `feat: per-cwd filesystem watcher`

---

## Task 10: Git context

**Files:**
- Create: `backend/detectors/git_context.py`

- [ ] **Step 10.1: Implement**

```python
def get_git_context(cwd: str) -> GitContext | None:
    """If cwd/.git missing → None.
    Use subprocess with 1s timeout.
    Run: git -C <cwd> branch --show-current
    Run: git -C <cwd> status --porcelain
    Return GitContext(branch, is_dirty=bool, modified_count=lines)."""
```

- [ ] **Step 10.2: Commit** `feat: git context detector`

---

## Task 11: SQLite state + permissions probe

**Files:**
- Create: `backend/state.py`, `backend/permissions.py`

- [ ] **Step 11.1: `state.py`**

```python
class State:
    async def init_db(self): ...
    async def insert_active(self, session: ClaudeSession): ...
    async def mark_ended(self, pid: int): ...
    async def list_history(self, hours: int = 24) -> list[ClaudeSession]: ...
    async def prune(self): ...  # delete history older than 24h
```

Schema: `sessions(pid, cwd, started_at, ended_at, last_seen, model, total_tokens, cost_estimate, summary_json TEXT)`.

- [ ] **Step 11.2: `permissions.py`**

```python
def probe_iterm_api() -> bool:
    """Try connecting to iterm2 cookie file or making a quick connection.
    Return False if not granted."""

def probe_automation() -> bool: ...

def health_report() -> dict:
    return {
      "iterm_api": probe_iterm_api(),
      "automation": probe_automation(),
      "tmux_available": shutil.which("tmux") is not None,
      "log_dir_found": find_log_dir() is not None,
    }
```

- [ ] **Step 11.3: Commit** `feat: sqlite history + permission probes`

---

## Task 12: FastAPI app + scheduler

**Files:**
- Create: `backend/server.py`

- [ ] **Step 12.1: Implement**

```python
class AppState:
    sessions: dict[int, ClaudeSession] = {}
    log_parse_cache: dict[Path, tuple[float, ParsedLog]] = {}
    git_cache: dict[str, tuple[float, GitContext]] = {}
    fs_watcher: FilesystemWatcher
    state: State
    sse_listeners: set[asyncio.Queue]
    config: dict

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.app = AppState(...)
    await app.state.app.state.init_db()
    scheduler_task = asyncio.create_task(scheduler_loop(app.state.app))
    yield
    scheduler_task.cancel()

async def scheduler_loop(s: AppState):
    while True:
        new = await build_sessions(s.config, s)
        diff = compute_diff(s.sessions, new)
        await broadcast(s.sse_listeners, diff)
        s.sessions = {x.pid: x for x in new}
        await s.state.persist(new)
        await s.fs_watcher.sync_active_cwds({x.cwd for x in new})
        await asyncio.sleep(s.config["process_scan_interval_seconds"])

app = FastAPI(lifespan=lifespan)
app.include_router(sessions_router)
app.include_router(actions_router)
app.include_router(stream_router)
app.include_router(health_router)
app.include_router(history_router)
app.include_router(config_router)

@app.get("/")
async def index():
    return FileResponse("frontend/index.html")
```

- [ ] **Step 12.2: Mount static**
```python
app.mount("/static", StaticFiles(directory="frontend"), name="static")
```

- [ ] **Step 12.3: Commit** `feat: FastAPI app with scheduler lifespan`

---

## Task 13: API routers

**Files:**
- Create: `backend/api/sessions.py`, `actions.py`, `stream.py`, `health.py`, `history.py`, `config_api.py`

- [ ] **Step 13.1: `sessions.py`**
  - `GET /api/sessions` → list current sessions from app.state
  - `GET /api/sessions/{pid}` → 404 if absent
  - `GET /api/sessions/{pid}/files?minutes=10`
  - `GET /api/sessions/{pid}/log-tail?limit=20` — privacy gate: if `show_log_text` is false, strip text/thinking content, keep only types + timestamps

- [ ] **Step 13.2: `stream.py`** — SSE generator using `app.state.app.sse_listeners`

- [ ] **Step 13.3: `health.py`** — calls `health_report()`

- [ ] **Step 13.4: `history.py`** — calls `state.list_history()`

- [ ] **Step 13.5: `config_api.py`** — GET/POST config; GET/POST pricing (a subset of config)

- [ ] **Step 13.6: Commit** `feat: API endpoints for sessions/health/history/config/stream`

---

## Task 14: Actions (focus, halt, new-session)

**Files:**
- Create: `backend/api/actions.py`, `backend/applescript/*.applescript`, `tests/test_sanitization.py`

- [ ] **Step 14.1: AppleScripts**

`focus_iterm.applescript`:
```applescript
on run argv
    set windowId to item 1 of argv as integer
    set targetTabId to item 2 of argv as integer
    tell application "iTerm"
        activate
        tell window id windowId
            select
            repeat with t in tabs
                if id of t is targetTabId then
                    tell t to select
                    exit repeat
                end if
            end repeat
        end tell
    end tell
end run
```

`new_iterm_window.applescript`:
```applescript
on run argv
    set targetCwd to item 1 of argv
    set claudeCmd to item 2 of argv
    tell application "iTerm"
        activate
        set newWindow to (create window with default profile)
        tell current session of newWindow
            write text "cd " & quoted form of targetCwd
            write text claudeCmd
        end tell
    end tell
end run
```

`new_iterm_tab.applescript` analog with `create tab`.

- [ ] **Step 14.2: Implement `actions.py`**

```python
SAFE_FLAG_RE = re.compile(r"^--[a-z][a-z0-9-]*(=[A-Za-z0-9._/=:-]+)?$")
VALUE_FLAGS = {"--model", "--system-prompt-file", "--print"}

def sanitize_new_session_body(body: NewSessionRequest) -> None:
    cwd = Path(body.cwd).expanduser().resolve()
    if not cwd.is_dir():
        raise HTTPException(400, "cwd does not exist")
    if not str(cwd).startswith(str(Path.home())):
        raise HTTPException(400, "cwd must be under home directory")
    if body.command != "claude":
        # allow absolute path under ~/.local/bin/
        cmd_path = Path(body.command).resolve()
        if not str(cmd_path).startswith(str(Path.home() / ".local" / "bin")):
            raise HTTPException(400, "invalid command path")
    i = 0
    while i < len(body.flags):
        f = body.flags[i]
        if not SAFE_FLAG_RE.match(f):
            raise HTTPException(400, f"unsafe flag: {f}")
        if f in VALUE_FLAGS and i + 1 < len(body.flags):
            v = body.flags[i+1]
            if any(c in v for c in [";", "&", "|", "`", "$", "\n", "\r"]):
                raise HTTPException(400, f"unsafe flag value")
            i += 2
        else:
            i += 1

@router.post("/api/sessions/new")
async def new_session(body: NewSessionRequest, app_state=Depends(get_state)):
    sanitize_new_session_body(body)
    cmd = body.command + " " + shlex.join(body.flags)
    script = "new_iterm_window.applescript" if body.window_type == "new-window" else "new_iterm_tab.applescript"
    subprocess.run(["osascript", APPLESCRIPT_DIR / script, body.cwd, cmd], check=True, timeout=10)
    return {"success": True}

@router.post("/api/sessions/{pid}/halt")
async def halt(pid: int, app_state=Depends(get_state)):
    if app_state.config.get("read_only"): raise HTTPException(403)
    if pid not in app_state.sessions: raise HTTPException(404)
    os.kill(pid, signal.SIGINT)
    return {"success": True}

@router.post("/api/sessions/{pid}/focus")
async def focus(pid: int, app_state=Depends(get_state)):
    if app_state.config.get("read_only"): raise HTTPException(403)
    s = app_state.sessions.get(pid)
    if not s: raise HTTPException(404)
    if s.location_type == "headless": raise HTTPException(400, "headless")
    if s.location_type == "iterm":
        subprocess.run(["osascript", APPLESCRIPT_DIR / "focus_iterm.applescript", str(s.iterm_window_id), str(s.iterm_tab_id)], check=True, timeout=5)
    else:  # tmux
        # first focus parent iterm window if any
        if s.iterm_window_id:
            subprocess.run(["osascript", APPLESCRIPT_DIR / "focus_iterm.applescript", str(s.iterm_window_id), str(s.iterm_tab_id)], check=True, timeout=5)
        subprocess.run(["tmux", "select-window", "-t", f"{s.tmux_session}:{s.tmux_window}"], check=True, timeout=5)
        subprocess.run(["tmux", "select-pane", "-t", f"{s.tmux_session}:{s.tmux_window}.{s.tmux_pane}"], check=True, timeout=5)
    return {"success": True}
```

- [ ] **Step 14.3: Sanitization tests**

```python
def test_reject_shell_injection():
    body = NewSessionRequest(cwd=str(Path.home()), flags=["--model", "; rm -rf /"])
    with pytest.raises(HTTPException) as ei:
        sanitize_new_session_body(body)
    assert ei.value.status_code == 400

def test_reject_flag_not_matching_regex():
    body = NewSessionRequest(cwd=str(Path.home()), flags=["--model; rm"])
    with pytest.raises(HTTPException):
        sanitize_new_session_body(body)

def test_accept_safe_flags():
    body = NewSessionRequest(cwd=str(Path.home()), flags=["--dangerously-skip-permissions"])
    sanitize_new_session_body(body)  # no raise
```

- [ ] **Step 14.4: Commit** `feat: actions (focus, halt, new) with sanitization`

---

## Task 15: Frontend HTML + Alpine

**Files:**
- Create: `frontend/index.html`, `frontend/app.js`, `frontend/styles.css`

- [ ] **Step 15.1: `frontend/index.html`** — single file with Tailwind CDN, Alpine CDN, x-data root component, three views: dashboard, history, settings (hash routing).

- [ ] **Step 15.2: `frontend/app.js`** — Alpine component:
  - State: `sessions, history, stats, health, config, pricing, filter, view, detailPid, showNewModal`
  - `init()`: fetch sessions + health + stats + config, open SSE
  - SSE handler: patch `sessions` keyed by pid
  - `focus(pid), halt(pid), openNew(cwd?), submitNew()`
  - Filter chips
  - Card template uses spec §4.4 layout

- [ ] **Step 15.3: `frontend/styles.css`** — minimal additions over Tailwind.

- [ ] **Step 15.4: Commit** `feat: frontend dashboard + history + settings`

---

## Task 16: CLI entry point

**Files:**
- Create: `backend/cli.py`

- [ ] **Step 16.1: Implement with typer**

```python
import typer, uvicorn, webbrowser, subprocess, os, signal
from pathlib import Path
app = typer.Typer()

@app.command()
def start(daemon: bool = False):
    if daemon:
        # spawn detached uvicorn process, write pidfile to ~/.claudewatch/server.pid
        ...
    else:
        uvicorn.run("backend.server:app", host="127.0.0.1", port=load_port(), log_level="info")

@app.command()
def stop(): ...
@app.command()
def status(): ...
@app.command()
def open(): webbrowser.open(f"http://127.0.0.1:{load_port()}")
@app.command()
def sessions(): ...   # rich Live table polling /api/sessions
@app.command()
def info(pid: int): ...
@app.command()
def new(directory: str): ...
@app.command()
def config(): subprocess.run([os.environ.get("EDITOR","nano"), CONFIG_PATH])
@app.command()
def pricing(): subprocess.run([os.environ.get("EDITOR","nano"), CONFIG_PATH])
@app.command()
def logs(): ...
@app.command()
def uninstall(): ...
```

- [ ] **Step 16.2: Test `claudewatch --help`**
- [ ] **Step 16.3: Commit** `feat: claudewatch CLI`

---

## Task 17: Install scripts + launchd plist

**Files:**
- Create: `scripts/install.sh`, `scripts/start.sh`, `scripts/stop.sh`, `scripts/uninstall.sh`, `scripts/launchd.plist`

- [ ] **Step 17.1: `install.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
PYTHON="${PYTHON:-/opt/homebrew/bin/python3.12}"
"$PYTHON" -m venv .venv
source .venv/bin/activate
pip install -e .
mkdir -p "$HOME/.claudewatch"
ln -sf "$(pwd)/.venv/bin/claudewatch" "$HOME/.local/bin/claudewatch" 2>/dev/null || true
echo "Installed. Run: claudewatch start"
echo "Grant permissions: iTerm2 Settings → Magic → Enable Python API"
```

- [ ] **Step 17.2: `launchd.plist`**

`com.claudewatch.server.plist` template — runs `claudewatch start` on login, restarts on crash. Document `launchctl load` in README.

- [ ] **Step 17.3: Commit** `chore: install + launchd scripts`

---

## Task 18: README + docs

**Files:**
- Create: `README.md`, `docs/permissions-setup.md`, `docs/conversation-log-format.md`, `docs/troubleshooting.md`, `docs/architecture.md`, `LICENSE`, `CLAUDE.md`

- [ ] **Step 18.1: README.md** — features, quick start, permissions, screenshots (placeholder paths), CLI ref, FAQ.

- [ ] **Step 18.2: Each doc** — focused content per spec exit criteria.

- [ ] **Step 18.3: MIT LICENSE.**

- [ ] **Step 18.4: CLAUDE.md** — project-specific Claude instructions: testing commands, layout, where to look.

- [ ] **Step 18.5: Commit** `docs: README + setup/permissions/troubleshooting`

---

## Task 19: Smoke test + tag

- [ ] **Step 19.1: Start server**
```bash
source .venv/bin/activate
claudewatch start &
sleep 3
curl -s http://127.0.0.1:7788/api/health | python -m json.tool
curl -s http://127.0.0.1:7788/api/sessions | python -m json.tool
```
Expected: at least the current `claude` (this very process) is detected.

- [ ] **Step 19.2: Run pytest**
```bash
pytest -v
```
Expected: all green.

- [ ] **Step 19.3: Final commit + tag**
```bash
git add -A
git commit -m "chore: v0.2.0 ready" || true
git tag v0.2.0
```

---

## Self-Review Notes

**Spec coverage:**
- §1 purpose → README + spec ✓
- §2 scope → tasks 1-19 ✓
- §3 schema findings → encoded in Task 7 parser ✓
- §4.2 process model → Task 12 scheduler ✓
- §5 data model → Task 2 ✓
- §6 API → Tasks 13–14 ✓
- §7 permissions → Task 11 + README + UI banner in Task 15 ✓
- §8 frontend → Task 15 ✓
- §9 pricing → Task 3 ✓
- §10 CLI → Task 16 ✓
- §11 phases → mapped 1:1 to tasks ✓
- §12 exit criteria → Task 19 smoke ✓

**Privacy default:** `privacy_mode = true` enforced in `config.DEFAULT_CONFIG` and respected in `sessions.py` log-tail endpoint.

**Security:** Sanitization tests in Task 14.3. Localhost-only bind in `cli.start`.

**Risks not yet mitigated:**
- iTerm permission missing → Task 11 health endpoint flags it; Task 15 frontend shows banner. ✓
- AppleScript failures during action → log error + return 500 with stderr in Task 14. (Add to Task 14 implementation.)

Plan is internally consistent. Proceeding.
