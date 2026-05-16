"""Tests for backend.state.State connection management."""

from __future__ import annotations

from backend.state import State


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
