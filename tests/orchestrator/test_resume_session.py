"""Tests for SessionPool.resume_session() durable execution resume.

Covers native agent resume, ACP agent resume, error cases, and event emission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from agentpool.agents.events.events import SessionResumeEvent
from agentpool.orchestrator.core import SessionPool
from agentpool.sessions.models import PendingDeferredCall, SessionData


def _stream_empty(stream: anyio.abc.ObjectReceiveStream) -> bool:
    """Check if a memory receive stream has no buffered items."""
    try:
        stream.receive_nowait()
        return False
    except anyio.WouldBlock:
        return True
    except anyio.EndOfStream:
        return True


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_pending_call(
    tool_call_id: str = "call-1",
    tool_name: str = "bash",
    deferred_kind: str = "external",
    deferred_strategy: str = "block",
) -> PendingDeferredCall:
    return PendingDeferredCall(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        deferred_kind=deferred_kind,  # type: ignore[arg-type]
        deferred_strategy=deferred_strategy,  # type: ignore[arg-type]
    )


def make_session_data(
    session_id: str = "sess-1",
    agent_name: str = "test-agent",
    agent_type: str = "native",
    pending: list[PendingDeferredCall] | None = None,
    status: str = "checkpointed",
    agent_config_hash: str = "abc123",
    metadata: dict[str, Any] | None = None,
) -> SessionData:
    return SessionData(
        session_id=session_id,
        agent_name=agent_name,
        agent_type=agent_type,
        pending_deferred_calls=pending or [],
        status=status,
        agent_config_hash=agent_config_hash,
        metadata=metadata or {},
    )


def make_deferred_tool_results(
    call_ids: list[str],
) -> Any:
    """Create a DeferredToolResults-compatible object for tests.

    Returns a simple object with `calls` dict for matching tool_call_ids.
    """
    return _FakeDeferredResults(call_ids=call_ids)


@dataclass
class _FakeDeferredResults:
    """Fake DeferredToolResults for testing (avoids pydantic-ai import)."""

    call_ids: list[str] = field(default_factory=list)

    @property
    def calls(self) -> dict[str, str]:
        return {cid: f"result-{cid}" for cid in self.call_ids}

    @property
    def approvals(self) -> dict[str, Any]:
        return {}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return a mocked AgentPool."""
    pool = MagicMock()
    pool.storage = MagicMock()
    pool.storage.get_session_messages = AsyncMock(return_value=[])
    pool.storage.log_message = AsyncMock(return_value=None)
    pool.main_agent = MagicMock()
    pool.main_agent.name = "main-agent"
    pool.manifest = MagicMock()
    pool.manifest.agents = {}
    return pool


@pytest.fixture
async def session_pool(mock_pool: MagicMock) -> SessionPool:
    """Return a SessionPool backed by a MemorySessionStore."""
    from agentpool.sessions.store import MemorySessionStore

    store = MemorySessionStore()
    return SessionPool(pool=mock_pool, store=store)


# ---------------------------------------------------------------------------
# SessionNotFoundError
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_session_raises_session_not_found_error(
    session_pool: SessionPool,
) -> None:
    """resume_session raises SessionNotFoundError for non-existent session."""
    from agentpool.orchestrator.core import SessionNotFoundError

    results = make_deferred_tool_results(["call-1"])
    with pytest.raises(SessionNotFoundError, match="sess-nonexistent"):
        await session_pool.resume_session("sess-nonexistent", results)


# ---------------------------------------------------------------------------
# SessionBusyError
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_session_raises_busy_error_when_active_run(
    session_pool: SessionPool,
) -> None:
    """resume_session raises SessionBusyError when the session has an active run."""
    from agentpool.orchestrator.core import SessionBusyError

    # Create session and fake an active run
    state, _ = await session_pool.sessions.get_or_create_session("sess-1", agent_name="test-agent")
    state.current_run_id = "run-active"

    results = make_deferred_tool_results(["call-1"])
    with pytest.raises(SessionBusyError, match="already has an active run"):
        await session_pool.resume_session("sess-1", results)


# ---------------------------------------------------------------------------
# CheckpointMismatchError
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_session_raises_mismatch_error_missing_results(
    session_pool: SessionPool,
) -> None:
    """resume_session raises CheckpointMismatchError when results don't cover all pending calls."""
    from agentpool.orchestrator.core import CheckpointMismatchError

    store = session_pool.sessions.store
    assert store is not None
    await store.save(
        make_session_data(
            session_id="sess-1",
            pending=[make_pending_call("call-1"), make_pending_call("call-2")],
            status="checkpointed",
        )
    )

    # Only provide result for call-1, not call-2
    results = make_deferred_tool_results(["call-1"])
    with pytest.raises(CheckpointMismatchError, match="call-2"):
        await session_pool.resume_session("sess-1", results)


@pytest.mark.anyio
async def test_resume_session_raises_mismatch_error_extra_results(
    session_pool: SessionPool,
) -> None:
    """resume_session raises CheckpointMismatchError when results include unknown call IDs."""
    from agentpool.orchestrator.core import CheckpointMismatchError

    store = session_pool.sessions.store
    assert store is not None
    await store.save(
        make_session_data(
            session_id="sess-1",
            pending=[make_pending_call("call-1")],
            status="checkpointed",
        )
    )

    results = make_deferred_tool_results(["call-1", "call-unknown"])
    with pytest.raises(CheckpointMismatchError, match="call-unknown"):
        await session_pool.resume_session("sess-1", results)


# ---------------------------------------------------------------------------
# Resume lock serialization
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_session_serialized_via_resume_lock(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """Concurrent resume_session calls are serialized via per-session lock."""
    store = session_pool.sessions.store
    assert store is not None
    await store.save(
        make_session_data(
            session_id="sess-1",
            pending=[make_pending_call("call-1")],
            status="checkpointed",
        )
    )

    # Verify lock exists
    lock = await session_pool._get_resume_lock("sess-1")
    assert lock is not None

    # Lock is per-session, can be acquired
    async with lock:
        assert lock.locked()


# ---------------------------------------------------------------------------
# Native agent resume — message_history + deferred_tool_results flow
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_native_agent_loads_checkpoint_and_runs(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """resume_session for native agent loads checkpoint, reconstructs agent, runs with history+results."""
    from unittest.mock import AsyncMock

    store = session_pool.sessions.store
    assert store is not None
    await store.save(
        make_session_data(
            session_id="sess-1",
            agent_name="test-agent",
            agent_type="native",
            pending=[make_pending_call("call-1")],
            status="checkpointed",
            metadata={"agent_type": "native"},
        )
    )

    # Create a fake checkpoint
    from agentpool.agents.native_agent.checkpoint import CheckpointData

    checkpoint_data = CheckpointData(
        message_history=[],
        pending_calls=[make_pending_call("call-1")],
    )

    # Mock the checkpoint manager load
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(return_value=MagicMock())
    mock_agentlet = MagicMock()
    mock_agent.get_agentlet = AsyncMock(return_value=mock_agentlet)

    # Mock pool.get_agent returns a mock native agent
    from agentpool.agents.native_agent import Agent as NativeAgent

    mock_native = MagicMock(spec=NativeAgent)
    mock_native.name = "test-agent"
    mock_native._model = None
    mock_native.model_settings = None
    mock_native.get_agentlet = AsyncMock(return_value=mock_agentlet)

    mock_pool.get_agent = MagicMock(return_value=mock_native)

    mock_load = AsyncMock(return_value=checkpoint_data)
    mock_reconstruct = AsyncMock(return_value=mock_native)
    with (
        patch.object(session_pool, "_load_checkpoint_data", mock_load),
        patch.object(session_pool, "_reconstruct_native_agent", mock_reconstruct),
    ):
        results = make_deferred_tool_results(["call-1"])
        await session_pool.resume_session("sess-1", results)

    # Verify checkpoint was loaded
    mock_load.assert_awaited_once_with("sess-1")

    # Verify agent was reconstructed
    mock_reconstruct.assert_awaited_once_with("sess-1", "test-agent")

    # Verify agent.run was called with message_history and deferred_tool_results
    mock_native.run.assert_called_once()
    kwargs = mock_native.run.call_args.kwargs
    assert "message_history" in kwargs
    assert kwargs["message_history"] == []
    assert "deferred_tool_results" in kwargs


# ---------------------------------------------------------------------------
# pending_deferred_calls cleared only after agent.run() succeeds
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_native_agent_clears_pending_after_success(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """pending_deferred_calls are cleared ONLY after agent.run() succeeds."""
    store = session_pool.sessions.store
    assert store is not None
    await store.save(
        make_session_data(
            session_id="sess-1",
            agent_type="native",
            pending=[make_pending_call("call-1")],
            status="checkpointed",
            metadata={"agent_type": "native"},
        )
    )

    from agentpool.agents.native_agent.checkpoint import CheckpointData

    checkpoint_data = CheckpointData(
        message_history=[],
        pending_calls=[make_pending_call("call-1")],
    )

    mock_native = MagicMock()
    mock_native.name = "test-agent"
    mock_native._model = None
    mock_native.model_settings = None

    mock_agentlet = MagicMock()
    mock_native.get_agentlet = AsyncMock(return_value=mock_agentlet)
    mock_native.run = AsyncMock(return_value=MagicMock())

    mock_pool.get_agent = MagicMock(return_value=mock_native)

    with (
        patch.object(
            session_pool, "_load_checkpoint_data", AsyncMock(return_value=checkpoint_data)
        ),
        patch.object(
            session_pool, "_reconstruct_native_agent", AsyncMock(return_value=mock_native)
        ),
    ):
        results = make_deferred_tool_results(["call-1"])
        await session_pool.resume_session("sess-1", results)

    # After successful resume, pending_deferred_calls should be cleared
    session_data = await store.load("sess-1")
    assert session_data is not None
    assert session_data.pending_deferred_calls == []


@pytest.mark.anyio
async def test_resume_native_agent_does_not_clear_pending_on_failure(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """pending_deferred_calls are NOT cleared if agent.run() fails."""
    store = session_pool.sessions.store
    assert store is not None
    await store.save(
        make_session_data(
            session_id="sess-1",
            agent_type="native",
            pending=[make_pending_call("call-1")],
            status="checkpointed",
            metadata={"agent_type": "native"},
        )
    )

    from agentpool.agents.native_agent.checkpoint import CheckpointData

    checkpoint_data = CheckpointData(
        message_history=[],
        pending_calls=[make_pending_call("call-1")],
    )

    mock_native = MagicMock()
    mock_native.name = "test-agent"
    mock_native._model = None
    mock_native.model_settings = None

    mock_agentlet = MagicMock()
    mock_native.get_agentlet = AsyncMock(return_value=mock_agentlet)
    mock_native.run = AsyncMock(side_effect=RuntimeError("Boom"))

    mock_pool.get_agent = MagicMock(return_value=mock_native)

    with (
        patch.object(
            session_pool, "_load_checkpoint_data", AsyncMock(return_value=checkpoint_data)
        ),
        patch.object(
            session_pool, "_reconstruct_native_agent", AsyncMock(return_value=mock_native)
        ),
    ):
        results = make_deferred_tool_results(["call-1"])
        with pytest.raises(RuntimeError, match="Boom"):
            await session_pool.resume_session("sess-1", results)

    # After failed resume, pending_deferred_calls should NOT be cleared
    session_data = await store.load("sess-1")
    assert session_data is not None
    assert len(session_data.pending_deferred_calls) == 1
    assert session_data.pending_deferred_calls[0].tool_call_id == "call-1"


# ---------------------------------------------------------------------------
# SessionResumeEvent emission
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_session_emits_resume_event(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """resume_session emits SessionResumeEvent on successful resume."""
    store = session_pool.sessions.store
    assert store is not None
    await store.save(
        make_session_data(
            session_id="sess-1",
            agent_type="native",
            pending=[make_pending_call("call-1"), make_pending_call("call-2")],
            status="checkpointed",
            metadata={"agent_type": "native"},
        )
    )

    from agentpool.agents.native_agent.checkpoint import CheckpointData

    checkpoint_data = CheckpointData(
        message_history=[],
        pending_calls=[make_pending_call("call-1"), make_pending_call("call-2")],
    )

    mock_native = MagicMock()
    mock_native.name = "test-agent"
    mock_native._model = None
    mock_native.model_settings = None

    mock_agentlet = MagicMock()
    mock_native.get_agentlet = AsyncMock(return_value=mock_agentlet)
    mock_native.run = AsyncMock(return_value=MagicMock())

    mock_pool.get_agent = MagicMock(return_value=mock_native)

    # Subscribe to event bus before resume
    queue = await session_pool.event_bus.subscribe("sess-1")

    with (
        patch.object(
            session_pool, "_load_checkpoint_data", AsyncMock(return_value=checkpoint_data)
        ),
        patch.object(
            session_pool, "_reconstruct_native_agent", AsyncMock(return_value=mock_native)
        ),
    ):
        results = make_deferred_tool_results(["call-1", "call-2"])
        await session_pool.resume_session("sess-1", results)

    # Collect events
    events: list[Any] = []
    try:
        while True:
            envelope = queue.receive_nowait()
            if envelope is not None:
                events.append(envelope.event)
    except anyio.WouldBlock:
        pass
    except anyio.EndOfStream:
        pass

    # Should find SessionResumeEvent
    resume_events = [e for e in events if isinstance(e, SessionResumeEvent)]
    assert len(resume_events) == 1
    assert resume_events[0].session_id == "sess-1"
    assert resume_events[0].resolved_call_count == 2
    assert resume_events[0].source == "resume_prompt"


# ---------------------------------------------------------------------------
# Status transition: checkpointed → active
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_session_transitions_status_to_active(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """resume_session transitions status from 'checkpointed' to 'active' on success."""
    store = session_pool.sessions.store
    assert store is not None
    await store.save(
        make_session_data(
            session_id="sess-1",
            agent_type="native",
            pending=[make_pending_call("call-1")],
            status="checkpointed",
            metadata={"agent_type": "native"},
        )
    )

    from agentpool.agents.native_agent.checkpoint import CheckpointData

    checkpoint_data = CheckpointData(
        message_history=[],
        pending_calls=[make_pending_call("call-1")],
    )

    mock_native = MagicMock()
    mock_native.name = "test-agent"
    mock_native._model = None
    mock_native.model_settings = None

    mock_agentlet = MagicMock()
    mock_native.get_agentlet = AsyncMock(return_value=mock_agentlet)
    mock_native.run = AsyncMock(return_value=MagicMock())

    mock_pool.get_agent = MagicMock(return_value=mock_native)

    with (
        patch.object(
            session_pool, "_load_checkpoint_data", AsyncMock(return_value=checkpoint_data)
        ),
        patch.object(
            session_pool, "_reconstruct_native_agent", AsyncMock(return_value=mock_native)
        ),
    ):
        results = make_deferred_tool_results(["call-1"])
        await session_pool.resume_session("sess-1", results)

    session_data = await store.load("sess-1")
    assert session_data is not None
    assert session_data.status == "active"


# ---------------------------------------------------------------------------
# Status transition: checkpointed → checkpointed on failure
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_session_keeps_checkpointed_status_on_failure(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """resume_session keeps status as 'checkpointed' when agent.run() fails."""
    store = session_pool.sessions.store
    assert store is not None
    await store.save(
        make_session_data(
            session_id="sess-1",
            agent_type="native",
            pending=[make_pending_call("call-1")],
            status="checkpointed",
            metadata={"agent_type": "native"},
        )
    )

    from agentpool.agents.native_agent.checkpoint import CheckpointData

    checkpoint_data = CheckpointData(
        message_history=[],
        pending_calls=[make_pending_call("call-1")],
    )

    mock_native = MagicMock()
    mock_native.name = "test-agent"
    mock_native._model = None
    mock_native.model_settings = None

    mock_agentlet = MagicMock()
    mock_native.get_agentlet = AsyncMock(return_value=mock_agentlet)
    mock_native.run = AsyncMock(side_effect=RuntimeError("Boom"))

    mock_pool.get_agent = MagicMock(return_value=mock_native)

    with (
        patch.object(
            session_pool, "_load_checkpoint_data", AsyncMock(return_value=checkpoint_data)
        ),
        patch.object(
            session_pool, "_reconstruct_native_agent", AsyncMock(return_value=mock_native)
        ),
    ):
        results = make_deferred_tool_results(["call-1"])
        with pytest.raises(RuntimeError, match="Boom"):
            await session_pool.resume_session("sess-1", results)

    session_data = await store.load("sess-1")
    assert session_data is not None
    assert session_data.status == "checkpointed"


# ---------------------------------------------------------------------------
# ACP agent resume path
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_acp_agent_reopens_subprocess(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """resume_session for ACP agent reopens subprocess and sends session/resume."""
    store = session_pool.sessions.store
    assert store is not None
    await store.save(
        make_session_data(
            session_id="sess-acp",
            agent_name="acp-agent",
            agent_type="acp",
            pending=[make_pending_call("call-acp-1")],
            status="checkpointed",
            metadata={"agent_type": "acp"},
        )
    )

    # Mock ACP agent
    mock_acp = MagicMock()
    mock_acp.name = "acp-agent"
    mock_acp.run = AsyncMock(return_value=MagicMock())
    mock_acp._resume_session = AsyncMock(return_value=None)

    mock_pool.get_agent = MagicMock(return_value=mock_acp)

    from agentpool.agents.native_agent.checkpoint import CheckpointData

    checkpoint_data = CheckpointData(
        message_history=[],
        pending_calls=[make_pending_call("call-acp-1")],
    )

    mock_load = AsyncMock(return_value=checkpoint_data)
    mock_reconstruct = AsyncMock(return_value=mock_acp)
    with (
        patch.object(session_pool, "_load_checkpoint_data", mock_load),
        patch.object(session_pool, "_reconstruct_acp_agent", mock_reconstruct),
    ):
        results = make_deferred_tool_results(["call-acp-1"])
        await session_pool.resume_session("sess-acp", results)

    # Verify ACP subprocess was reopened
    mock_reconstruct.assert_awaited_once_with("sess-acp", "acp-agent")

    # Verify agent.run() was called (ACP agents use run, not _resume_session at this level)
    mock_acp.run.assert_called_once()


# ---------------------------------------------------------------------------
# No pending calls — edge case
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_session_with_empty_pending_calls(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """resume_session handles empty pending_deferred_calls gracefully."""
    store = session_pool.sessions.store
    assert store is not None
    await store.save(
        make_session_data(
            session_id="sess-1",
            agent_type="native",
            pending=[],
            status="checkpointed",
            metadata={"agent_type": "native"},
        )
    )

    from agentpool.agents.native_agent.checkpoint import CheckpointData

    checkpoint_data = CheckpointData(
        message_history=[],
        pending_calls=[],
    )

    mock_native = MagicMock()
    mock_native.name = "test-agent"
    mock_native._model = None
    mock_native.model_settings = None

    mock_agentlet = MagicMock()
    mock_native.get_agentlet = AsyncMock(return_value=mock_agentlet)
    mock_native.run = AsyncMock(return_value=MagicMock())

    mock_pool.get_agent = MagicMock(return_value=mock_native)

    with (
        patch.object(
            session_pool, "_load_checkpoint_data", AsyncMock(return_value=checkpoint_data)
        ),
        patch.object(
            session_pool, "_reconstruct_native_agent", AsyncMock(return_value=mock_native)
        ),
    ):
        # Empty results should be fine when no pending calls
        results = make_deferred_tool_results([])
        await session_pool.resume_session("sess-1", results)

    mock_native.run.assert_called_once()
