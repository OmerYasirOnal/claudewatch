from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from backend.models import ClaudeSession

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    pid INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    last_seen TEXT NOT NULL,
    cwd TEXT,
    model TEXT,
    total_tokens INTEGER DEFAULT 0,
    cost_estimate REAL,
    summary_json TEXT,
    PRIMARY KEY (pid, started_at)
);
CREATE INDEX IF NOT EXISTS idx_sessions_last_seen ON sessions(last_seen);
CREATE INDEX IF NOT EXISTS idx_sessions_ended ON sessions(ended_at);
"""


class State:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def init_db(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def upsert_active(self, session: ClaudeSession) -> None:
        now = datetime.now(timezone.utc).isoformat()
        total = session.usage.total_tokens if session.usage else 0
        cost = session.usage.cost_estimate_usd if session.usage else None
        summary = session.model_dump_json()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO sessions (pid, started_at, ended_at, last_seen, cwd, model, total_tokens, cost_estimate, summary_json)
                VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pid, started_at) DO UPDATE SET
                    last_seen=excluded.last_seen,
                    cwd=excluded.cwd,
                    model=excluded.model,
                    total_tokens=excluded.total_tokens,
                    cost_estimate=excluded.cost_estimate,
                    summary_json=excluded.summary_json
                """,
                (
                    session.pid,
                    session.started_at.isoformat(),
                    now,
                    session.cwd,
                    session.model,
                    total,
                    cost,
                    summary,
                ),
            )
            await db.commit()

    async def mark_ended(self, pid: int, started_at: datetime) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE sessions SET ended_at=? WHERE pid=? AND started_at=? AND ended_at IS NULL",
                (now, pid, started_at.isoformat()),
            )
            await db.commit()

    async def list_history(self, hours: int = 24) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """
                SELECT pid, started_at, ended_at, last_seen, cwd, model, total_tokens, cost_estimate, summary_json
                FROM sessions
                WHERE ended_at IS NOT NULL AND ended_at >= ?
                ORDER BY ended_at DESC
                """,
                (cutoff,),
            )
        out: list[dict] = []
        for row in rows:
            d = dict(row)
            try:
                d["summary"] = json.loads(d.pop("summary_json"))
            except Exception:  # noqa: BLE001
                d["summary"] = None
            out.append(d)
        return out

    async def stats_today(self) -> dict:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await db.execute_fetchall(
                """
                SELECT COUNT(*) AS sessions_today,
                       COALESCE(SUM(total_tokens), 0) AS tokens_today,
                       COALESCE(SUM(cost_estimate), 0) AS cost_today
                FROM sessions
                WHERE last_seen >= ?
                """,
                (cutoff,),
            )
        if not row:
            return {"sessions_today": 0, "tokens_today": 0, "cost_today": 0.0}
        return dict(row[0])

    async def prune(self, hours: int = 48) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ?",
                (cutoff,),
            )
            await db.commit()
