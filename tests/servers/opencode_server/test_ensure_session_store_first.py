"""Tests for ensure_session() store-first and overwrite-prevention behaviour.

Covers TG-2, TG-5, TG-11, TG-17, TG-19, TG-32 scenarios from RFC-0028.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool.agents.base_agent import BaseAgent
from agentpool.sessions.models import SessionData
from agentpool_server.opencode_server.converters import session_data_to_opencode
from agentpool_server.opencode_server.models import (
    Session,
    SessionCreatedEvent,
    SessionIdleEvent,
    SessionStatusEvent,
    SessionUpdatedEvent,
    TimeCreatedUpdated,
)
from agentpool_server.opencode_server.session_pool_integration import ensure_session
from agentpool_server.opencode_server.state import ServerState


def create_mock_agent() -> MagicMock:
    """Create a properly configured mock agent."""
    agent = MagicMock(spec=BaseAgent)
    agent.name = "test_agent"
    agent.session_id = "original_session_id"
    agent.agent_pool = MagicMock()
    agent.agent_pool.manifest.config_file_path = "test_config.yml"
    agent.agent_pool.storage.save_session = AsyncMock()
    agent.agent_pool.storage.load_session = AsyncMock(return_value=None)
    agent.agent_pool.session_pool = MagicMock()
    agent.agent_pool.session_pool.sessions = MagicMock()
    agent.agent_pool.session_pool.sessions.store = None
    agent.env = MagicMock()
    agent.env.cwd = "/test/dir"
    return agent


def _make_session_data(
    session_id: str = "stored-session",
    *,
    agent_name: str = "stored_agent",
    agent_type: str = "acp",
    pool_id: str = "stored-pool",
    project_id: str = "stored-project",
    cwd: str = "/stored/dir",
    parent_id: str | None = None,
) -> SessionData:
    """Create a SessionData instance that simulates persisted data."""
    return SessionData(
        session_id=session_id,
        agent_name=agent_name,
        agent_type=agent_type,
        pool_id=pool_id,
        project_id=project_id,
        cwd=cwd,
        parent_id=parent_id,
        version="1",
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        last_active=datetime(2025, 6, 1, tzinfo=timezone.utc),
        metadata={"title": "Stored Session Title"},
    )


@pytest.fixture
def mock_state() -> ServerState:
    """Create a ServerState with mocked dependencies."""
    agent = create_mock_agent()
    state = ServerState(
        working_dir="/test/working/dir",
        agent=agent,
    )
    # Initialize backward-compat dicts removed from ServerState dataclass
    # so tests and helper fallbacks can access them.
    state.messages = {}
    state.session_status = {}
    state.todos = {}
    state.input_providers = {}
    state.pending_questions = {}
    return state


# ---------------------------------------------------------------------------
# TG-2: ensure_session preserves already-persisted agent_type/pool_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_first_preserves_agent_type_and_pool_id(
    mock_state: ServerState,
) -> None:
    """TG-2: Store-first path preserves persisted agent_type and pool_id.

    When a session was previously persisted with agent_type='acp' and
    pool_id='stored-pool', ensure_session must NOT overwrite those values
    with defaults from the current agent config.
    """
    session_id = "stored-session"
    sd = _make_session_data(session_id, agent_type="acp", pool_id="stored-pool")

    # Wire store.load to return the persisted data
    mock_store = MagicMock()
    mock_store.load = AsyncMock(return_value=sd)
    mock_state.pool.session_pool.sessions.store = mock_store

    # Also mock save so we can verify it's NOT called
    mock_store.save = AsyncMock()

    with patch.object(mock_state, "broadcast_event", new=AsyncMock()):
        session = await ensure_session(mock_state, session_id)

    # The session should have the stored title/directory
    assert session.title == "Stored Session Title"
    assert session.directory == "/stored/dir"
    assert session.project_id == "stored-project"

    # store.save must NOT be called — data is already persisted
    mock_store.save.assert_not_awaited()

    # Also verify pool.storage.save_session was NOT called
    mock_state.pool.storage.save_session.assert_not_awaited()


# ---------------------------------------------------------------------------
# TG-5: store-first child session is not overwritten by fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_first_child_not_overwritten(mock_state: ServerState) -> None:
    """TG-5: A child session loaded from store is not overwritten by fallback.

    If a child session (parent_id set) was persisted by
    create_child_session() and then ensure_session() is called for that
    session_id, the store-first path must restore it as-is rather than
    creating a fresh session with default values.
    """
    child_id = "child-session-stored"
    parent_id = "parent-session-stored"
    sd = _make_session_data(
        child_id,
        agent_name="child_agent",
        agent_type="native",
        pool_id="child-pool",
        parent_id=parent_id,
        cwd="/child/dir",
        project_id="child-project",
    )

    mock_store = MagicMock()
    mock_store.load = AsyncMock(return_value=sd)
    mock_store.save = AsyncMock()
    mock_state.pool.session_pool.sessions.store = mock_store

    with patch.object(mock_state, "broadcast_event", new=AsyncMock()):
        session = await ensure_session(mock_state, child_id, parent_id=parent_id)

    # Child fields from store must be preserved
    assert session.id == child_id
    assert session.parent_id == parent_id
    assert session.directory == "/child/dir"
    assert session.project_id == "child-project"

    # Must NOT have overwritten by creating a fresh "New Session"
    assert session.title != "New Session"

    # Must NOT have called save
    mock_store.save.assert_not_awaited()


# ---------------------------------------------------------------------------
# TG-11: concurrent calls produce one Session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_calls_produce_one_session(
    mock_state: ServerState,
) -> None:
    """TG-11: Concurrent ensure_session calls for same ID produce one Session.

    Multiple coroutines calling ensure_session with the same session_id
    concurrently must result in exactly one in-memory Session object.
    """
    session_id = "concurrent-session"

    # Use the conftest-style real store (pool.sessions.store is already
    # wired to storage_manager via the mock_pool fixture in conftest.py,
    # but mock_state uses a simpler mock).  Set store to None so the
    # store-first path yields None and falls through to creation.
    mock_state.pool.session_pool.sessions.store = None
    mock_state.pool.storage.load_session = AsyncMock(return_value=None)

    with (
        patch("agentpool_server.opencode_server.converters.opencode_to_session_data"),
        patch("agentpool_server.opencode_server.input_provider.OpenCodeInputProvider"),
        patch.object(mock_state, "broadcast_event", new=AsyncMock()),
    ):
        results = await asyncio.gather(
            ensure_session(mock_state, session_id),
            ensure_session(mock_state, session_id),
            ensure_session(mock_state, session_id),
        )

    # All calls should return the same Session object
    assert results[0] is results[1]
    assert results[1] is results[2]

    # Only one Session should exist in memory
    assert len([s for s in mock_state.sessions.values() if s.id == session_id]) == 1


@pytest.mark.asyncio
async def test_concurrent_store_first_produces_one_session(
    mock_state: ServerState,
) -> None:
    """TG-11 (store variant): Concurrent calls when data is in store.

    When multiple coroutines race to ensure_session for an ID that exists
    in the store, only one should trigger the store.load + conversion and
    the others should find it in memory via double-check locking.
    """
    session_id = "concurrent-store-session"
    sd = _make_session_data(session_id)

    mock_store = MagicMock()
    mock_store.load = AsyncMock(return_value=sd)
    mock_store.save = AsyncMock()
    mock_state.pool.session_pool.sessions.store = mock_store

    with patch.object(mock_state, "broadcast_event", new=AsyncMock()):
        results = await asyncio.gather(
            ensure_session(mock_state, session_id),
            ensure_session(mock_state, session_id),
        )

    # Both should return the same Session object
    assert results[0] is results[1]
    assert mock_state.sessions[session_id] is results[0]


# ---------------------------------------------------------------------------
# TG-17: In-memory session not overwritten by store data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_memory_session_not_overwritten_by_store(
    mock_state: ServerState,
) -> None:
    """TG-17: An in-memory session is not overwritten by store data.

    If a session is already in memory (from a prior ensure_session or
    create_session call), ensure_session must return the in-memory version
    even if the store has different data for that session_id.
    """
    session_id = "in-mem-session"

    existing_session = Session(
        id=session_id,
        project_id="in-mem-project",
        directory="/in-mem/dir",
        title="In-Memory Title",
        version="1",
        time=TimeCreatedUpdated(created=1000, updated=2000),
        parent_id=None,
    )
    mock_state.sessions[session_id] = existing_session

    # Store has different data
    sd = _make_session_data(session_id, cwd="/store/dir", project_id="store-project")
    mock_store = MagicMock()
    mock_store.load = AsyncMock(return_value=sd)
    mock_state.pool.session_pool.sessions.store = mock_store

    with patch.object(mock_state, "broadcast_event", new=AsyncMock()) as mock_broadcast:
        result = await ensure_session(mock_state, session_id)

    # Must return the in-memory session, not the store version
    assert result is existing_session
    assert result.title == "In-Memory Title"
    assert result.project_id == "in-mem-project"

    # Store.load should NOT have been called
    mock_store.load.assert_not_awaited()

    # Only SessionUpdatedEvent should be broadcast (not Created)
    created_events = [
        e for e in mock_broadcast.await_args_list if isinstance(e.args[0], SessionCreatedEvent)
    ]
    assert len(created_events) == 0


# ---------------------------------------------------------------------------
# TG-19: Store-first path does NOT call bind_agent_to_session for children
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_first_child_skips_agent_binding(
    mock_state: ServerState,
) -> None:
    """TG-19: Store-first child session does NOT call bind_agent_to_session.

    When a child session (parent_id is set) is loaded from the store,
    ensure_session must NOT call bind_agent_to_session — that would
    overwrite the parent's session_id and deadlock on agent_lock.
    """
    child_id = "child-no-bind"
    parent_id = "parent-no-bind"
    sd = _make_session_data(child_id, parent_id=parent_id)

    mock_store = MagicMock()
    mock_store.load = AsyncMock(return_value=sd)
    mock_state.pool.session_pool.sessions.store = mock_store

    original_session_id = mock_state.agent.session_id

    with patch.object(mock_state, "broadcast_event", new=AsyncMock()):
        session = await ensure_session(mock_state, child_id)

    assert session.id == child_id

    # Agent's session_id must NOT have been changed to the child's ID
    assert mock_state.agent.session_id == original_session_id
    assert mock_state.agent.session_id != child_id


# ---------------------------------------------------------------------------
# TG-32: Store-miss fallback still creates and persists new session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_miss_fallback_creates_and_persists(
    mock_state: ServerState,
) -> None:
    """TG-32: Store-miss fallback creates and persists a new session.

    When a session_id is absent from both memory and the store,
    ensure_session must fall back to creating a new session and
    persisting it (original behaviour).
    """
    session_id = "new-session-fallback"

    # Store returns None (session not found)
    mock_store = MagicMock()
    mock_store.load = AsyncMock(return_value=None)
    mock_store.save = AsyncMock()
    mock_state.pool.session_pool.sessions.store = mock_store

    with (
        patch("agentpool_server.opencode_server.converters.opencode_to_session_data") as mock_conv,
        patch(
            "agentpool_server.opencode_server.input_provider.OpenCodeInputProvider"
        ) as mock_prov_cls,
        patch.object(mock_state, "broadcast_event", new=AsyncMock()),
    ):
        mock_conv.return_value = MagicMock()
        mock_prov_cls.return_value = MagicMock()
        result = await ensure_session(mock_state, session_id)

    assert result.id == session_id
    assert result.title == "New Session"

    # Must have persisted via store.save
    mock_store.save.assert_awaited_once()

    # Must be in memory
    assert session_id in mock_state.sessions
    assert mock_state.sessions[session_id] is result


# ---------------------------------------------------------------------------
# Additional: Store-first path broadcasts created + updated events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_first_broadcasts_created_and_updated(
    mock_state: ServerState,
) -> None:
    """Store-first path broadcasts session.created and session.updated events."""
    session_id = "broadcast-test-session"
    sd = _make_session_data(session_id)

    mock_store = MagicMock()
    mock_store.load = AsyncMock(return_value=sd)
    mock_state.pool.session_pool.sessions.store = mock_store

    with patch.object(mock_state, "broadcast_event", new=AsyncMock()) as mock_broadcast:
        session = await ensure_session(mock_state, session_id)

    broadcast_events = [call.args[0] for call in mock_broadcast.await_args_list]

    created_events = [e for e in broadcast_events if isinstance(e, SessionCreatedEvent)]
    updated_events = [e for e in broadcast_events if isinstance(e, SessionUpdatedEvent)]

    assert len(created_events) == 1
    assert len(updated_events) >= 1  # at least the session.updated

    # The session from created event should match what we loaded
    assert created_events[0].properties.info.id == session_id


# ---------------------------------------------------------------------------
# Additional: Store-first path marks session idle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_first_marks_session_idle(mock_state: ServerState) -> None:
    """Store-first path marks the session as idle."""
    session_id = "idle-test-session"
    sd = _make_session_data(session_id)

    mock_store = MagicMock()
    mock_store.load = AsyncMock(return_value=sd)
    mock_state.pool.session_pool.sessions.store = mock_store

    with patch.object(mock_state, "broadcast_event", new=AsyncMock()) as mock_broadcast:
        await ensure_session(mock_state, session_id)

    # Should have idle events.
    # Status is now broadcast via set_session_status() + mark_session_idle()
    # instead of stored in the in-memory session_status dict.
    broadcast_events = [call.args[0] for call in mock_broadcast.await_args_list]
    status_events = [e for e in broadcast_events if isinstance(e, SessionStatusEvent)]
    idle_events = [e for e in broadcast_events if isinstance(e, SessionIdleEvent)]
    assert len(status_events) == 2  # set_session_status() + mark_session_idle() explicit broadcast
    assert len(idle_events) == 1


# ---------------------------------------------------------------------------
# Additional: session_data_to_opencode converter
# ---------------------------------------------------------------------------


def test_session_from_session_data_uses_converter() -> None:
    """session_data_to_opencode converts SessionData to OpenCode Session correctly."""
    sd = _make_session_data("converter-test")

    result = session_data_to_opencode(sd)

    assert result.id == "converter-test"
    assert result.title == "Stored Session Title"
    assert result.directory == "/stored/dir"
    assert result.project_id == "stored-project"


# ---------------------------------------------------------------------------
# Additional: Store-first creates runtime state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_first_creates_runtime_state(mock_state: ServerState) -> None:
    """Store-first path initializes runtime state for the session."""
    session_id = "runtime-state-session"
    sd = _make_session_data(session_id)

    mock_store = MagicMock()
    mock_store.load = AsyncMock(return_value=sd)
    mock_state.pool.session_pool.sessions.store = mock_store

    with patch.object(mock_state, "broadcast_event", new=AsyncMock()):
        await ensure_session(mock_state, session_id)

    # Session should be registered in memory
    assert session_id in mock_state.sessions
    # ensure_runtime_session_state initializes reverted_messages
    assert session_id in mock_state.reverted_messages
    assert mock_state.reverted_messages[session_id] == []


# ---------------------------------------------------------------------------
# Additional: Store-first top-level session does not bind agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_first_top_level_session_does_not_bind_agent(
    mock_state: ServerState,
) -> None:
    """Store-first path does not bind agent for top-level sessions.

    Agent binding was removed from ensure_session; sessions are now
    managed by the SessionPool orchestration layer.
    """
    session_id = "top-level-session"
    sd = _make_session_data(session_id, parent_id=None)

    mock_store = MagicMock()
    mock_store.load = AsyncMock(return_value=sd)
    mock_state.pool.session_pool.sessions.store = mock_store

    original_session_id = mock_state.agent.session_id

    with patch.object(mock_state, "broadcast_event", new=AsyncMock()):
        await ensure_session(mock_state, session_id)

    # Session should be created
    assert session_id in mock_state.sessions
    # Agent should NOT be bound to this session
    assert mock_state.agent.session_id == original_session_id
