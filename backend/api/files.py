"""Cross-session file-change feed + per-file git diff + open-in-editor.

The single-session ``/api/sessions/{pid}/files`` route already exposes the
filesystem-watch deque for one session's cwd. The dashboard's "Files" panel
needs a flat feed across *every* active session, so this router pulls all
deques out of ``s.fs_watcher.changes``, joins them back to the sessions whose
cwd they belong to, dedupes by (cwd, path), and returns the latest activity
sorted newest-first.

The two file-content endpoints (``/api/files/diff`` and ``POST /api/files/open``)
are gated by the same path-safety rules:

* ``cwd`` must match an active session's cwd (so we can't be asked to peek at
  arbitrary directories on disk),
* the resolved ``cwd / path`` must stay under ``cwd`` (no ``../etc/passwd``),
* both must live under ``Path.home()`` (last-line defence against the
  config_api allow-list being bypassed someday).

``POST /api/files/open`` is additionally guarded by ``config.editor.enabled``
(opt-in, default False) and only accepts editor commands matching a strict
allow-list character class — see ``EditorConfig`` in ``config_api.py``.
"""

from __future__ import annotations

import asyncio
import heapq
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# Bound the /file-changes payload. 500 file events is more than enough for the
# dashboard's Files panel; anything larger is almost certainly a runaway build
# step (node_modules, compiled output) that shouldn't be in the deque anyway.
_MAX_FILE_CHANGES = 500

# Bound the diff context window — git supports any int but huge values can
# produce many-MB responses. 50 lines is plenty for any reasonable UI.
_DIFF_TIMEOUT_SECONDS = 5.0

# Cap untracked-file previews. 1 MB is the file-size hard limit (anything
# bigger returns None instead of a preview); 64 KB is what we actually slice
# off the head to send back.
_UNTRACKED_PREVIEW_BYTES = 64 * 1024
_UNTRACKED_PREVIEW_MAX_FILE_SIZE = 1024 * 1024

# Re-validate the editor command on the server side too. The ConfigUpdate
# model already enforces this, but config.toml can be hand-edited and the file
# might predate the validator — defence-in-depth.
_EDITOR_COMMAND_RE = re.compile(r"^[A-Za-z0-9_/.\- ]+$")


# ---------------------------------------------------------------------------
# /api/file-changes — flat, deduped, cross-session feed
# ---------------------------------------------------------------------------


@router.get("/file-changes")
async def all_file_changes(
    request: Request,
    minutes: int = Query(10, ge=1, le=120),
) -> list[dict[str, Any]]:
    """Return a flat, deduped list of recent file changes across all sessions.

    Dedup key is ``(cwd, path)``; for duplicate keys we keep the entry with
    the newest ``ts``. Sorted newest-first and capped at 500 entries so a
    pathological workspace can't bloat a single response.
    """
    s = request.app.state.s
    if s.fs_watcher is None:
        return []

    # Map cwd -> list of (pid,) for active sessions, so we can stamp each
    # change with the PIDs of the session(s) it belongs to. A single cwd may
    # host multiple sessions when the user has two `claude` REPLs in the same
    # project — we surface all of them.
    pids_by_cwd: dict[str, list[int]] = {}
    for sess in s.sessions.values():
        if not sess.cwd:
            continue
        pids_by_cwd.setdefault(sess.cwd, []).append(sess.pid)

    # Iterate fs_watcher.changes (dict[cwd, deque[FileChange]]). The watcher
    # may have changes for cwds that no longer have active sessions (e.g. the
    # session just ended and sync_active_cwds hasn't run yet); we still emit
    # them but with an empty session_pids list, so the dashboard can decide.
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for cwd, dq in s.fs_watcher.changes.items():
        recent = s.fs_watcher.get_recent(cwd, minutes)
        for change in recent:
            key = (cwd, change.path)
            existing = latest.get(key)
            if existing is not None and existing["_ts_obj"] >= change.ts:
                continue
            try:
                abs_path = str((Path(cwd) / change.path).resolve())
            except (OSError, RuntimeError):
                abs_path = str(Path(cwd) / change.path)
            latest[key] = {
                "path": change.path,
                "abs_path": abs_path,
                "cwd": cwd,
                "project": Path(cwd).name or cwd,
                "kind": change.kind,
                "ts": change.ts.isoformat(),
                "session_pids": list(pids_by_cwd.get(cwd, [])),
                # Internal sort key; popped before returning so it doesn't
                # leak into the JSON payload.
                "_ts_obj": change.ts,
            }
        # Silence the deque-only variable; the .get_recent call already pulled
        # what we need but ruff would flag the unused name otherwise.
        del dq

    # #90: avoid building a fully-sorted N-item list when we only ever return
    # _MAX_FILE_CHANGES. With ~10 active cwds during an `npm install` storm the
    # input is ~20k entries; heapq.nlargest keeps the cost at O(N log 500).
    top = heapq.nlargest(
        _MAX_FILE_CHANGES,
        latest.values(),
        key=lambda x: x["_ts_obj"],
    )
    for item in top:
        item.pop("_ts_obj", None)
    return top


# ---------------------------------------------------------------------------
# /api/files/diff — per-file git diff or preview
# ---------------------------------------------------------------------------


def _resolve_safe_paths(s, cwd: str, path: str) -> tuple[Path, Path]:
    """Validate cwd + path and return (cwd_path, abs_file_path).

    Raises HTTPException(400) on any safety violation.
    """
    # Active-session whitelist: cwd MUST be the cwd of a session we're
    # currently watching. This is the load-bearing check — it stops the caller
    # from asking for diffs on arbitrary directories.
    active_cwds = {sess.cwd for sess in s.sessions.values() if sess.cwd}
    if cwd not in active_cwds:
        raise HTTPException(400, "cwd not associated with any active session")

    try:
        cwd_path = Path(cwd).resolve(strict=True)
    except (OSError, RuntimeError) as e:
        raise HTTPException(400, f"cwd does not exist: {e}") from e
    if not cwd_path.is_dir():
        raise HTTPException(400, "cwd is not a directory")

    # Reject obvious traversal early — even before we touch the FS.
    if path.startswith("/") or ".." in Path(path).parts:
        raise HTTPException(400, "path must be relative and contain no '..' segments")

    # #97: Path.resolve() raises RuntimeError on symlink loops (and OSError on
    # other filesystem errors). Both would otherwise bubble up as a generic 500
    # — clamp them to a 400 with a clear message instead.
    try:
        candidate = (cwd_path / path).resolve()
    except (OSError, RuntimeError) as e:
        raise HTTPException(400, f"path resolution failed: {e}") from e
    # Final containment check: candidate must be under cwd_path.
    try:
        candidate.relative_to(cwd_path)
    except ValueError as e:
        raise HTTPException(400, "path escapes cwd") from e

    # Defence-in-depth: never allow paths outside the user's home directory.
    home = Path.home().resolve()
    try:
        cwd_path.relative_to(home)
        candidate.relative_to(home)
    except ValueError as e:
        raise HTTPException(400, "cwd and path must live under $HOME") from e

    return cwd_path, candidate


def _run_git(args: list[str]) -> subprocess.CompletedProcess:
    """Helper for asyncio.to_thread — runs a single git invocation."""
    return subprocess.run(args, capture_output=True, timeout=_DIFF_TIMEOUT_SECONDS)


async def _git_diff(
    cwd_path: Path,
    rel_path: str,
    context: int,
) -> dict[str, Any]:
    """Run git ls-files / diff / diff --stat for ``rel_path`` concurrently.

    #96: the three git invocations used to run serially, so the worst-case
    wall time was ``3 * _DIFF_TIMEOUT_SECONDS`` (~15s). Running them via
    ``asyncio.gather(... asyncio.to_thread(...) ...)`` bounds the wall time
    to ~``_DIFF_TIMEOUT_SECONDS`` (~5s) — they all share the same budget and
    finish together.

    Returns a dict matching the endpoint's serialised shape. Never raises
    except for ``subprocess.TimeoutExpired`` which the endpoint maps to a
    504.
    """
    git_dir = cwd_path / ".git"
    if not git_dir.exists():
        # Not a git checkout — fall back to a plain content preview.
        preview = await asyncio.to_thread(_read_file_preview, cwd_path / rel_path)
        return {
            "is_git": False,
            "tracked": False,
            "diff": "",
            "stat": "",
            "untracked_preview": preview,
        }

    # Fan out all three git calls at once. `ls-files --error-unmatch` tells
    # us whether the file is tracked; diff / diff --stat are only meaningful
    # if it is. We run them speculatively in parallel — if the file turns out
    # to be untracked we just discard the diff/stat results. The wasted work
    # is bounded by `_DIFF_TIMEOUT_SECONDS` and saves us a serial round-trip
    # in the (much more common) tracked-file case.
    ls_args = ["git", "-C", str(cwd_path), "ls-files", "--error-unmatch", "--", rel_path]
    diff_args = ["git", "-C", str(cwd_path), "diff", f"-U{int(context)}", "--", rel_path]
    stat_args = ["git", "-C", str(cwd_path), "diff", "--stat", "--", rel_path]
    ls_proc, diff_proc, stat_proc = await asyncio.gather(
        asyncio.to_thread(_run_git, ls_args),
        asyncio.to_thread(_run_git, diff_args),
        asyncio.to_thread(_run_git, stat_args),
    )
    tracked = ls_proc.returncode == 0

    if not tracked:
        preview = await asyncio.to_thread(_read_file_preview, cwd_path / rel_path)
        return {
            "is_git": True,
            "tracked": False,
            "diff": "",
            "stat": "",
            "untracked_preview": preview,
        }

    return {
        "is_git": True,
        "tracked": True,
        "diff": diff_proc.stdout.decode("utf-8", errors="replace"),
        "stat": stat_proc.stdout.decode("utf-8", errors="replace").strip(),
        "untracked_preview": None,
    }


def _read_file_preview(abs_path: Path) -> str | None:
    """Read the head of ``abs_path`` for the untracked-file preview.

    Returns None for missing / too-large files; otherwise up to 64 KB decoded
    as UTF-8 (with replacement on bad bytes).
    """
    try:
        st = abs_path.stat()
    except OSError:
        return None
    if st.st_size > _UNTRACKED_PREVIEW_MAX_FILE_SIZE:
        return None
    try:
        with open(abs_path, "rb") as f:
            chunk = f.read(_UNTRACKED_PREVIEW_BYTES)
    except OSError:
        return None
    return chunk.decode("utf-8", errors="replace")


@router.get("/files/diff")
async def file_diff(
    request: Request,
    cwd: str = Query(...),
    path: str = Query(...),
    context: int = Query(3, ge=0, le=50),
) -> dict[str, Any]:
    """Return ``git diff`` (or a preview) for a single file in a session cwd."""
    s = request.app.state.s
    cwd_path, abs_path = _resolve_safe_paths(s, cwd, path)
    rel_path = str(abs_path.relative_to(cwd_path))

    try:
        result = await _git_diff(cwd_path, rel_path, context)
    except subprocess.TimeoutExpired as e:
        raise HTTPException(504, "git diff timed out") from e
    except Exception as e:  # noqa: BLE001
        log.warning("file_diff failed for %s/%s: %s", cwd_path, rel_path, e)
        raise HTTPException(500, "failed to compute diff") from e

    return {
        "cwd": str(cwd_path),
        "path": rel_path,
        **result,
    }


# ---------------------------------------------------------------------------
# POST /api/files/open — opt-in open-in-editor
# ---------------------------------------------------------------------------


class OpenFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cwd: str
    path: str


@router.post("/files/open")
async def open_file(body: OpenFileRequest, request: Request) -> dict[str, Any]:
    """Open ``cwd/path`` in the user's configured editor. Opt-in via config."""
    s = request.app.state.s

    editor_cfg = s.config.get("editor") or {}
    if not editor_cfg.get("enabled", False):
        raise HTTPException(403, "editor integration is disabled")

    command = editor_cfg.get("command") or "code"
    # Belt-and-braces — see the comment on _EDITOR_COMMAND_RE.
    if not _EDITOR_COMMAND_RE.match(command):
        raise HTTPException(400, "configured editor command contains unsafe characters")

    cwd_path, abs_path = _resolve_safe_paths(s, body.cwd, body.path)
    if not abs_path.exists():
        raise HTTPException(404, "file does not exist")

    # Allow ``open -t`` style configs by splitting on whitespace. The
    # character-class validator above already rejects shell metachars, so
    # this can't smuggle a pipeline or chained command.
    argv = command.split() + [str(abs_path)]

    def _spawn() -> None:
        # start_new_session=True so the editor process isn't killed when
        # claudewatch exits (the user wants their editor to keep running).
        subprocess.Popen(  # noqa: S603 — argv list + validated bin name
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    try:
        await asyncio.to_thread(_spawn)
    except FileNotFoundError as e:
        raise HTTPException(400, f"editor command not found: {command}") from e
    except OSError as e:
        raise HTTPException(500, f"failed to spawn editor: {e}") from e

    return {
        "success": True,
        "command": command,
        "path": str(abs_path),
        "cwd": str(cwd_path),
    }
