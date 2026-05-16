"""Tests for backend.state.State connection management."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.models import ClaudeSession, TokenUsage
from backend.state import State, _to_utc_iso


async def test_state_connection_reused(tmp_path):
    """connect() should be idempotent; close() should reset _conn to None."""
    state = State(tmp_path / "state.db")
    await state.init_db()

    await state.connect()
    first_id = id(state._conn)
    await state.connect()
    second_id = id(state._conn)

    assert first_id == second_id
    assert state._conn is not None

    # Use a method that exercises the connection.
    await state.prune()
    assert id(state._conn) == first_id

    await state.close()
    assert state._conn is None


async def test_prune_clamps_huge_hours(tmp_path):
    """Issue #32: a pathologically large `hours` (e.g. from a typo in config or
    an API caller passing seconds-as-hours) used to make `timedelta(hours=N)`
    raise OverflowError, killing the scheduler loop. State.prune now clamps
    the value before constructing the timedelta."""
    state = State(tmp_path / "state.db")
    await state.init_db()
    try:
        # Far past int range that timedelta would normally choke on.
        await state.prune(hours=99_999_999_999)
        # Negative / zero gets clamped up to the 1-hour floor (still safe).
        await state.prune(hours=0)
        await state.prune(hours=-5)
    finally:
        await state.close()


def test_to_utc_iso_normalizes_naive_and_aware():
    """``_to_utc_iso`` should add UTC tzinfo to naive datetimes and convert
    tz-aware datetimes to UTC before serializing.
    """
    naive = datetime(2026, 5, 17, 12, 0, 0)
    aware_utc = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)

    assert _to_utc_iso(naive).endswith("+00:00")
    assert _to_utc_iso(aware_utc).endswith("+00:00")


async def test_upsert_active_normalizes_naive_started_at_to_utc(tmp_path):
    """#91: upsert_active must normalize a naive ``started_at`` to UTC before
    persisting, so ``hourly_history``'s ``substr(started_at, 1, 13)`` bin lands
    in the right hour regardless of caller's tzinfo discipline.
    """
    state = State(tmp_path / "state.db")
    await state.init_db()
    try:
        # Naive datetime — no tzinfo. This is the regression case: the old
        # implementation called ``.isoformat()`` directly, dropping the
        # timezone suffix and (worse) leaving the bin-key vulnerable to a
        # local-time shift if the caller had passed a non-UTC tz-aware dt.
        # Use a fixed timestamp ~5 hours ago in UTC so it lands inside the
        # hourly_history window without depending on the wall-clock day.
        now_utc = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        target_hour = now_utc - timedelta(hours=5)
        naive_started = target_hour.replace(tzinfo=None) + timedelta(minutes=30)
        expected_bin_key = target_hour.strftime("%Y-%m-%dT%H")

        sess = ClaudeSession(
            pid=99001,
            cwd="/tmp",
            started_at=naive_started,
            last_activity_at=naive_started,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        )
        await state.upsert_active(sess)

        assert state._conn is not None
        rows = await state._conn.execute_fetchall(
            "SELECT started_at FROM sessions WHERE pid=?",
            (99001,),
        )
        assert len(rows) == 1
        stored = dict(rows[0])["started_at"]
        assert stored.endswith("+00:00"), stored
        # The bin key (used by hourly_history) is substr(...,1,13) → "YYYY-MM-DDTHH".
        assert stored[:13] == expected_bin_key

        # Exercise the full hourly_history path: the bin should report the
        # session as started in the target hour.
        bins = await state.hourly_history(hours=24)
        matches = [b for b in bins if b["hour"].startswith(expected_bin_key)]
        assert matches, [b["hour"] for b in bins[:5]]
        assert matches[0]["sessions_started"] >= 1
    finally:
        await state.close()


async def test_mark_ended_accepts_naive_started_at(tmp_path):
    """#91: ``mark_ended`` looks up by ``started_at``; if the caller passes a
    naive datetime it must still match the row inserted via ``upsert_active``
    (which normalized to UTC). Verifies the round-trip is consistent.
    """
    state = State(tmp_path / "state.db")
    await state.init_db()
    try:
        naive_started = datetime(2026, 5, 17, 8, 0, 0)
        sess = ClaudeSession(
            pid=99002,
            cwd="/tmp",
            started_at=naive_started,
            last_activity_at=naive_started,
        )
        await state.upsert_active(sess)
        await state.mark_ended(99002, naive_started)

        assert state._conn is not None
        rows = await state._conn.execute_fetchall(
            "SELECT ended_at FROM sessions WHERE pid=?",
            (99002,),
        )
        ended_at = dict(rows[0])["ended_at"]
        assert ended_at is not None, "mark_ended did not match the row"
        assert ended_at.endswith("+00:00")
    finally:
        await state.close()
