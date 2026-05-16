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
