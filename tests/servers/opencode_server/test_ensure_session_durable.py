"""Tests for ensure_session() durable-aware checkpoint restoration.

Covers Task 27: checkpointed session detection, ToolPart reconstruction,
spawn graph restoration, and route_message() deferred result replay.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.native_agent.checkpoint import CheckpointData
from agentpool.sessions.models import PendingDeferredCall, SessionData
from agentpool_server.opencode_server.models import (
    MessageWithParts,
    SessionStatusEvent,
)
from agentpool_server.opencode_server.models.parts import (
    ToolPart,
    ToolStateRunning,
)
from agentpool_server.opencode_server.session_pool_integration import (
    OpenCodeSessionPoolIntegration,
    ensure_session,
)
from agentpool_server.opencode_server.state import ServerState
from agentpool.lifecycle.types import DeliveryMode


def create_mock_agent() -> MagicMock:
    """Create a properly configured mock agent."""
    agent = MagicMock(spec=BaseAgent)
    agent.name = "test_agent"
    agent.session_id = "original_session_id"
    agent.host_context = MagicMock()
    agent._agent_pool = agent.host_context  # state.py resolves _pool via agent._agent_pool
    agent.host_context.manifest.config_file_path = "test_config.yml"
    agent.host_context.storage.save_session = AsyncMock()
    agent.host_context.storage.load_session = AsyncMock(return_value=None)
    agent.host_context.session_pool = MagicMock()
    agent.host_context.session_pool.sessions = MagicMock()
    agent.host_context.session_pool.sessions.store = None
    agent.host_context.session_pool.send_message = AsyncMock()
    agent.host_context.session_pool.resume_session = AsyncMock()
    agent.host_context.session_pool.close_session = AsyncMock()
    agent.host_context.session_pool.sessions.get_or_create_session_agent = AsyncMock()
    agent.host_context.session_pool.sessions.get_session = MagicMock(return_value=None)
    agent.host_context.session_pool.event_bus = MagicMock()
    from tests._helpers.mock_stream import EmptyReceiveStream

    agent.host_context.session_pool.event_bus.subscribe = AsyncMock(
        return_value=EmptyReceiveStream()
    )
    agent.host_context.session_pool.event_bus.unsubscribe = AsyncMock()
    agent.env = MagicMock()
    agent.env.cwd = "/test/dir"
    return agent


def _make_session_data(
    session_id: str = "stored-session",
    *,
    agent_name: str = "stored_agent",
    agent_type: str = "native",
    pool_id: str = "stored-pool",
    project_id: str = "stored-project",
    cwd: str = "/stored/dir",
    parent_id: str | None = None,
    status: str = "active",
    pending_calls: list[PendingDeferredCall] | None = None,
    metadata: dict[str, Any] | None = None,
) -> SessionData:
    """Create a SessionData instance with configurable fields."""
    meta = metadata or {}
    if "title" not in meta:
        meta["title"] = "Stored Session Title"
    return SessionData(
        session_id=session_id,
        agent_name=agent_name,
        agent_type=agent_type,
        pool_id=pool_id,
        project_id=project_id,
        cwd=cwd,
        parent_id=parent_id,
        version="1",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        last_active=datetime(2025, 6, 1, tzinfo=UTC),
        metadata=meta,
        status=status,
        pending_deferred_calls=pending_calls or [],
    )


def _make_pending_call(
    tool_call_id: str = "call_001",
    tool_name: str = "bash",
    deferred_kind: str = "external",
    deferred_strategy: str = "block",
) -> PendingDeferredCall:
    """Create a PendingDeferredCall."""
    return PendingDeferredCall(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        deferred_kind=deferred_kind,  # type: ignore[arg-type]
        deferred_strategy=deferred_strategy,  # type: ignore[arg-type]
    )


def _make_checkpoint_data(
    pending_calls: list[PendingDeferredCall] | None = None,
) -> CheckpointData:
    """Create CheckpointData with optional pending calls."""
    return CheckpointData(
        message_history=[],
        pending_calls=pending_calls or [],
    )


@pytest.fixture
def mock_state() -> ServerState:
    """Create a ServerState with mocked dependencies."""
    agent = create_mock_agent()
    state = ServerState(
        working_dir="/test/working/dir",
        agent=agent,
    )
    # Initialize backward-compat dicts
    state.messages = {}  # type: ignore[attr-defined]
    state.session_status = {}  # type: ignore[attr-defined]
    state.todos = {}  # type: ignore[attr-defined]
    state.input_providers = {}  # type: ignore[attr-defined]
    state.pending_questions = {}  # type: ignore[attr-defined]
    state.reverted_messages = {}  # type: ignore[attr-defined]
    return state


# ---------------------------------------------------------------------------
# Task 27.1: ensure_session detects checkpointed sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_session_detects_checkpointed_status(mock_state: ServerState) -> None:
    """ensure_session loads a session with status='checkpointed' successfully.

    The session should still be loaded from store, with runtime state
    initialised, even when status is 'checkpointed'.
    """
    session_id = "checkpointed-session"
    sd = _make_session_data(
        session_id,
        status="checkpointed",
        pending_calls=[_make_pending_call("call_001", "bash")],
    )

    mock_store = MagicMock()
    mock_store.load_session = AsyncMock(return_value=sd)
    mock_store.save_session = AsyncMock()
    mock_state.pool.session_pool.sessions.store = mock_store

    with patch.object(mock_state, "broadcast_event", new=AsyncMock()):
        session = await ensure_session(mock_state, session_id)

    # Session should be loaded and registered in memory
    assert session.id == session_id
    assert session_id in mock_state.sessions
    assert session.title == "Stored Session Title"

    # Runtime state should be initialised
    assert session_id in mock_state.reverted_messages
    assert mock_state.reverted_messages[session_id] == []
    assert session_id in mock_state.messages

    # store.save should NOT be called (data already persisted)
    mock_store.save_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_session_checkpointed_marks_idle(mock_state: ServerState) -> None:
    """Checkpointed session loaded from store is marked as idle."""
    session_id = "checkpointed-idle"
    sd = _make_session_data(session_id, status="checkpointed")

    mock_store = MagicMock()
    mock_store.load_session = AsyncMock(return_value=sd)
    mock_state.pool.session_pool.sessions.store = mock_store

    with patch.object(mock_state, "broadcast_event", new=AsyncMock()) as mock_broadcast:
        await ensure_session(mock_state, session_id)

    # Should be idle even though status is checkpointed in storage.
    # Status is broadcast via set_session_status() + mark_session_idle().
    status_events = [
        call.args[0]
        for call in mock_broadcast.await_args_list
        if isinstance(call.args[0], SessionStatusEvent)
    ]
    assert len(status_events) >= 1
    assert status_events[0].properties.status.type == "idle"


# ---------------------------------------------------------------------------
# Task 27.2: Running ToolParts re-inserted into in-memory message list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_session_reconstructs_tool_parts(mock_state: ServerState) -> None:
    """ensure_session creates running ToolParts for pending deferred calls.

    When a checkpointed session has pending_deferred_calls, the in-memory
    message list should contain an assistant message with ToolStateRunning
    ToolParts for each pending call.
    """
    session_id = "checkpointed-with-pending"
    sd = _make_session_data(
        session_id,
        status="checkpointed",
        pending_calls=[
            _make_pending_call("call_a", "bash"),
            _make_pending_call("call_b", "subagent"),
        ],
    )

    mock_store = MagicMock()
    mock_store.load_session = AsyncMock(return_value=sd)
    mock_store.save_session = AsyncMock()
    mock_state.pool.session_pool.sessions.store = mock_store

    with patch.object(mock_state, "broadcast_event", new=AsyncMock()):
        await ensure_session(mock_state, session_id)

    # In-memory messages should contain an assistant message with ToolParts
    messages: list[MessageWithParts] = mock_state.messages.get(session_id, [])
    assert len(messages) >= 1, f"Expected at least 1 message, got {len(messages)}"

    # Find the assistant message (should be the last one or the only one)
    assistant_msgs = [m for m in messages if m.info.role == "assistant"]
    assert len(assistant_msgs) == 1, f"Expected 1 assistant message, got {len(assistant_msgs)}"

    assistant_msg = assistant_msgs[0]
    # Find ToolParts
    tool_parts = [p for p in assistant_msg.parts if isinstance(p, ToolPart)]
    assert len(tool_parts) == 2, f"Expected 2 ToolParts, got {len(tool_parts)}"

    for tp in tool_parts:
        assert isinstance(tp, ToolPart)
        assert isinstance(tp.state, ToolStateRunning), (
            f"ToolPart state should be ToolStateRunning, got {type(tp.state).__name__}"
        )
        # Each ToolPart should have a call_id from pending calls
        call_ids = {call.tool_call_id for call in sd.pending_deferred_calls}
        assert tp.call_id in call_ids, (
            f"ToolPart call_id {tp.call_id!r} not in pending calls {call_ids}"
        )


@pytest.mark.asyncio
async def test_ensure_session_no_tool_parts_for_no_pending_calls(
    mock_state: ServerState,
) -> None:
    """ensure_session does NOT create ToolParts when no pending deferred calls."""
    session_id = "checkpointed-no-pending"
    sd = _make_session_data(session_id, status="checkpointed", pending_calls=[])

    mock_store = MagicMock()
    mock_store.load_session = AsyncMock(return_value=sd)
    mock_state.pool.session_pool.sessions.store = mock_store

    with patch.object(mock_state, "broadcast_event", new=AsyncMock()):
        await ensure_session(mock_state, session_id)

    # Still creates an assistant message (placeholder), but no ToolParts
    messages: list[MessageWithParts] = mock_state.messages.get(session_id, [])
    assistant_msgs = [m for m in messages if m.info.role == "assistant"]
    if assistant_msgs:
        tool_parts = [p for p in assistant_msgs[0].parts if isinstance(p, ToolPart)]
        assert len(tool_parts) == 0, (
            f"Expected 0 ToolParts when no pending calls, got {len(tool_parts)}"
        )


# ---------------------------------------------------------------------------
# Task 27.3: Parent/child spawn graph restored from checkpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_session_restores_child_topology(mock_state: ServerState) -> None:
    """ensure_session restores parent/child spawn graph from checkpoint.

    When a session was checkpointed with spawn_children stored in metadata,
    the spawn topology should be restored on the ServerState.
    """
    parent_id = "parent-checkpointed"
    child_id_1 = "child-1"
    child_id_2 = "child-2"

    # Parent is checkpointed with spawn_children in metadata
    parent_sd = _make_session_data(
        parent_id,
        status="checkpointed",
        pending_calls=[
            _make_pending_call("call_x", "task"),
        ],
        metadata={
            "title": "Stored Session Title",
            "spawn_children": [child_id_1, child_id_2],
        },
    )

    mock_store = MagicMock()
    mock_store.load_session = AsyncMock(return_value=parent_sd)
    mock_store.save_session = AsyncMock()
    mock_state.pool.session_pool.sessions.store = mock_store

    with patch.object(mock_state, "broadcast_event", new=AsyncMock()):
        session = await ensure_session(mock_state, parent_id)

    assert session.id == parent_id

    # The spawn topology should be stored on state for the integration to pick up.
    spawn_graph: dict[str, list[str]] = getattr(mock_state, "checkpoint_spawn_graph", None) or {}
    children = spawn_graph.get(parent_id, [])
    assert child_id_1 in children, f"Child {child_id_1!r} not found in spawn graph"
    assert child_id_2 in children, f"Child {child_id_2!r} not found in spawn graph"


@pytest.mark.asyncio
async def test_ensure_session_no_spawn_graph_for_no_children(
    mock_state: ServerState,
) -> None:
    """ensure_session does not create spawn graph entries for sessions with no children."""
    session_id = "no-children-session"
    sd = _make_session_data(session_id, status="checkpointed")

    mock_store = MagicMock()
    mock_store.load_session = AsyncMock(return_value=sd)
    mock_store.save_session = AsyncMock()
    mock_state.pool.session_pool.sessions.store = mock_store

    with patch.object(mock_state, "broadcast_event", new=AsyncMock()):
        await ensure_session(mock_state, session_id)

    spawn_graph: dict[str, list[str]] = getattr(mock_state, "checkpoint_spawn_graph", {})
    assert session_id in spawn_graph, "Spawn graph entry should exist (empty children list)"
    assert spawn_graph[session_id] == []


# ---------------------------------------------------------------------------
# Task 27.4: route_message() replays deferred results before new input
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_message_replays_deferred_results(mock_state: ServerState) -> None:
    """route_message calls resume_session when session is checkpointed with results."""
    session_id = "checkpointed-resume"
    sd = _make_session_data(
        session_id,
        status="checkpointed",
        pending_calls=[_make_pending_call("call_r", "bash")],
    )

    mock_store = MagicMock()
    mock_store.load_session = AsyncMock(return_value=sd)
    mock_store.save_session = AsyncMock()
    mock_state.pool.session_pool.sessions.store = mock_store

    # Wire up SessionPool
    sp = mock_state.pool.session_pool
    sp.sessions.store = mock_store
    sp.sessions.get_session = MagicMock(return_value=MagicMock(current_run_id=None))
    sp.sessions.get_or_create_session = AsyncMock(return_value=(MagicMock(), True))

    # Mock deferred tool results
    deferred_results = MagicMock()
    deferred_results.calls = {"call_r": MagicMock()}

    integration = OpenCodeSessionPoolIntegration(sp, mock_state)

    # Patch internal methods that spawn background tasks
    with (
        patch.object(integration, "_start_event_consumer", new=AsyncMock()),
        patch.object(mock_state, "broadcast_event", new=AsyncMock()),
    ):
        await integration.route_message(
            session_id,
            content="resume with results",
            mode=DeliveryMode.QUEUE,
            deferred_tool_results=deferred_results,
        )

    # resume_session should have been called because session is checkpointed
    # and deferred_tool_results were provided
    sp.resume_session.assert_awaited_once()

    # receive_request should still be called after resume
    sp.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_route_message_skips_resume_when_not_checkpointed(
    mock_state: ServerState,
) -> None:
    """route_message does NOT call resume_session when session is not checkpointed."""
    session_id = "active-session"

    sp = mock_state.pool.session_pool
    sp.sessions.store = None  # No store → no data → normal path
    sp.sessions.get_session = MagicMock(return_value=MagicMock(current_run_id=None))
    sp.sessions.get_or_create_session = AsyncMock(return_value=(MagicMock(), True))

    integration = OpenCodeSessionPoolIntegration(sp, mock_state)

    with (
        patch.object(integration, "_start_event_consumer", new=AsyncMock()),
        patch.object(mock_state, "broadcast_event", new=AsyncMock()),
    ):
        await integration.route_message(
            session_id,
            content="hello",
            mode=DeliveryMode.QUEUE,
        )

    # resume_session should NOT be called for non-checkpointed sessions
    sp.resume_session.assert_not_awaited()
    # receive_request should be called
    sp.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_session_checkpointed_restores_input_provider(
    mock_state: ServerState,
) -> None:
    """Checkpointed session restores input provider correctly."""
    session_id = "checkpointed-input-provider"
    sd = _make_session_data(session_id, status="checkpointed")

    mock_store = MagicMock()
    mock_store.load_session = AsyncMock(return_value=sd)
    mock_state.pool.session_pool.sessions.store = mock_store

    with (
        patch.object(mock_state, "broadcast_event", new=AsyncMock()),
        patch(
            "agentpool_server.opencode_server.input_provider.OpenCodeInputProvider"
        ) as mock_prov_cls,
    ):
        mock_prov = MagicMock()
        mock_prov_cls.return_value = mock_prov
        await ensure_session(mock_state, session_id)

    mock_prov_cls.assert_called()  # Called at least once (idempotent ensure)
    # Input provider is created (the mock tracks calls to OpenCodeInputProvider)
    assert mock_prov_cls.call_count >= 1


# ---------------------------------------------------------------------------
# Edge case: checkpointed session without store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_session_checkpointed_without_store(
    mock_state: ServerState,
) -> None:
    """ensure_session handles checkpointed session when store is None (falls back)."""
    session_id = "checkpointed-no-store"
    sd = _make_session_data(session_id, status="checkpointed")

    # Set store to None (simulates no checkpoint storage)
    mock_state.pool.session_pool.sessions.store = None
    # But storage.load_session still returns the data (via pool.storage)
    mock_state.pool.storage.load_session = AsyncMock(return_value=sd)

    with (
        patch.object(mock_state, "broadcast_event", new=AsyncMock()),
        patch(
            "agentpool_server.opencode_server.input_provider.OpenCodeInputProvider"
        ) as mock_prov_cls,
    ):
        mock_prov_cls.return_value = MagicMock()
        session = await ensure_session(mock_state, session_id)

    assert session.id == session_id
    assert session_id in mock_state.sessions
