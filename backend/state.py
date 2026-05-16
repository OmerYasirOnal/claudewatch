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
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self._conn is None:
            self._conn = await aiosqlite.connect(self.db_path)
            self._conn.row_factory = aiosqlite.Row

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def init_db(self) -> None:
        await self.connect()
        assert self._conn is not None
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def upsert_active(self, session: ClaudeSession) -> None:
        await self.connect()
        assert self._conn is not None
        now = datetime.now(timezone.utc).isoformat()
        total = session.usage.total_tokens if session.usage else 0
        cost = session.usage.cost_estimate_usd if session.usage else None
        summary = session.model_dump_json()
        await self._conn.execute(
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
        await self._conn.commit()

    async def mark_ended(self, pid: int, started_at: datetime) -> None:
        await self.connect()
        assert self._conn is not None
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "UPDATE sessions SET ended_at=? WHERE pid=? AND started_at=? AND ended_at IS NULL",
            (now, pid, started_at.isoformat()),
        )
        await self._conn.commit()

    async def list_history(self, hours: int = 24) -> list[dict]:
        await self.connect()
        assert self._conn is not None
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        rows = await self._conn.execute_fetchall(
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

    async def hourly_history(self, hours: int = 24) -> list[dict]:
        """Return one bin per hour for the trailing ``hours`` window.

        Each bin reports:
          * ``hour`` — ISO 8601 UTC timestamp of the bin start, oldest first.
          * ``sessions_started`` — count of rows whose ``started_at`` falls
            inside the bin.
          * ``tokens`` / ``cost`` — sums attributed at session end (rows whose
            ``ended_at`` falls inside the bin). Cost is attributed at end
            because the SQLite ``sessions`` table records the final totals
            only when a session ends.

        Empty bins are still emitted (zeros) so callers can render a stable
        time axis.
        """
        await self.connect()
        assert self._conn is not None
        # Mirror prune()'s clamp so a bad caller can't blow up timedelta.
        hours = min(max(int(hours), 1), 24 * 365 * 100)
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start = now - timedelta(hours=hours - 1)
        cutoff = start.isoformat()

        # started_at counts (one row per hour)
        started_rows = await self._conn.execute_fetchall(
            """
            SELECT substr(started_at, 1, 13) AS hour_key, COUNT(*) AS n
            FROM sessions
            WHERE started_at >= ?
            GROUP BY hour_key
            """,
            (cutoff,),
        )
        started_map: dict[str, int] = {dict(r)["hour_key"]: dict(r)["n"] for r in started_rows}

        # ended_at sums (tokens/cost attributed at end)
        ended_rows = await self._conn.execute_fetchall(
            """
            SELECT substr(ended_at, 1, 13) AS hour_key,
                   COALESCE(SUM(total_tokens), 0) AS tokens,
                   COALESCE(SUM(cost_estimate), 0) AS cost
            FROM sessions
            WHERE ended_at IS NOT NULL AND ended_at >= ?
            GROUP BY hour_key
            """,
            (cutoff,),
        )
        ended_map: dict[str, dict] = {dict(r)["hour_key"]: dict(r) for r in ended_rows}

        bins: list[dict] = []
        for i in range(hours):
            bin_start = start + timedelta(hours=i)
            # ISO with "Z"-style suffix; the table stores isoformat() of UTC
            # datetimes, which yields "...+00:00". substr(...,1,13) gives
            # "YYYY-MM-DDTHH" for both, matching cleanly.
            hour_key = bin_start.strftime("%Y-%m-%dT%H")
            ended = ended_map.get(hour_key, {})
            bins.append(
                {
                    "hour": bin_start.isoformat().replace("+00:00", "Z"),
                    "sessions_started": int(started_map.get(hour_key, 0)),
                    "tokens": int(ended.get("tokens", 0) or 0),
                    "cost": float(ended.get("cost", 0.0) or 0.0),
                }
            )
        return bins

    async def stats_today(self) -> dict:
        await self.connect()
        assert self._conn is not None
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        row = await self._conn.execute_fetchall(
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
        await self.connect()
        assert self._conn is not None
        # Issue #32: clamp to [1 hour, 100 years]. An unbounded `hours` (from a
        # misconfigured cron / API caller) lets `timedelta(hours=...)` raise
        # OverflowError, killing the scheduler loop. 100 years is well beyond
        # any realistic retention need and stays comfortably inside timedelta's
        # range (~999_999_999 days).
        hours = min(max(int(hours), 1), 24 * 365 * 100)
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        await self._conn.execute(
            "DELETE FROM sessions WHERE ended_at IS NOT NULL AND ended_at < ?",
            (cutoff,),
        )
        await self._conn.commit()
