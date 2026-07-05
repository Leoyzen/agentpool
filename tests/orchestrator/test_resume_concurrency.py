"""Tests for resume_session concurrency safety (per-session resume_lock).

Covers serialization of concurrent resume calls, SessionBusyError for
second caller, and safe status transitions on success/failure.

See Decision 8 and Task 19.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool.orchestrator.core import SessionBusyError, SessionPool


pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers for async generator mocking
# ---------------------------------------------------------------------------


async def _ok_gen(**kwargs: Any) -> Any:
    """Async generator that yields nothing and completes silently."""
    return
    yield  # pragma: no cover


async def _fail_gen(**kwargs: Any) -> Any:
    """Async generator that raises RuntimeError during iteration."""
    raise RuntimeError("Boom")
    yield  # pragma: no cover


# ---------------------------------------------------------------------------
# Reuse helpers from test_resume_session.py
# ---------------------------------------------------------------------------

from tests.orchestrator.test_resume_session import (  # noqa: E402
    make_deferred_tool_results,
    make_pending_call,
    make_session_data,
)


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
# _with_resume_lock context manager
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_with_resume_lock_acquires_lock(
    session_pool: SessionPool,
) -> None:
    """_with_resume_lock acquires the per-session resume lock."""
    lock = await session_pool._get_resume_lock("sess-1")
    assert lock is not None
    # Lock should be free initially
    assert not lock.locked()

    async with session_pool._with_resume_lock("sess-1") as session:
        # Lock should be held inside the context
        assert lock.locked()
        assert session is None  # No live session exists

    # Lock should be released after context exit
    assert not lock.locked()


@pytest.mark.anyio
async def test_with_resume_lock_raises_busy_for_active_run(
    session_pool: SessionPool,
) -> None:
    """_with_resume_lock raises SessionBusyError when session has active run."""
    state, _ = await session_pool.sessions.get_or_create_session("sess-1", agent_name="test-agent")
    state.current_run_id = "run-active"

    with pytest.raises(SessionBusyError, match="already has an active run"):
        async with session_pool._with_resume_lock("sess-1"):
            pass  # pragma: no cover


@pytest.mark.anyio
async def test_with_resume_lock_raises_busy_for_resumed_session(
    session_pool: SessionPool,
) -> None:
    """_with_resume_lock raises SessionBusyError when session status is not 'checkpointed'."""
    store = session_pool.sessions.store
    assert store is not None

    # Save session data with status "active" (already resumed)
    await store.save(
        make_session_data(
            session_id="sess-1",
            agent_name="test-agent",
            status="active",
            metadata={"agent_type": "native"},
        )
    )

    with pytest.raises(SessionBusyError, match="already has an active run"):
        async with session_pool._with_resume_lock("sess-1"):
            pass  # pragma: no cover


@pytest.mark.anyio
async def test_with_resume_lock_allows_checkpointed_session(
    session_pool: SessionPool,
) -> None:
    """_with_resume_lock allows sessions with status 'checkpointed'."""
    store = session_pool.sessions.store
    assert store is not None

    await store.save(
        make_session_data(
            session_id="sess-1",
            agent_name="test-agent",
            status="checkpointed",
            metadata={"agent_type": "native"},
        )
    )

    async with session_pool._with_resume_lock("sess-1") as session:
        assert session is None  # No live session, only persisted data

    # No exception raised


# ---------------------------------------------------------------------------
# Concurrent resume_session calls serialize
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_session_concurrent_calls_serialize(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """Concurrent resume_session calls serialize via per-session lock.

    The second call receives SessionBusyError while the first is in progress.
    """
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

    # A slow agent.run_stream() that blocks until an event is set
    block_event = asyncio.Event()

    async def slow_run_stream(**kwargs: Any) -> Any:
        await block_event.wait()
        return  # StopAsyncIteration
        yield  # pragma: no cover

    mock_native = MagicMock()
    mock_native.name = "test-agent"
    mock_native._model = None
    mock_native.model_settings = None
    mock_agentlet = MagicMock()
    mock_native.get_agentlet = AsyncMock(return_value=mock_agentlet)
    mock_native.run_stream = slow_run_stream

    mock_pool.get_agent = MagicMock(return_value=mock_native)

    with (
        patch.object(
            session_pool,
            "_load_checkpoint_data",
            AsyncMock(return_value=checkpoint_data),
        ),
        patch.object(
            session_pool,
            "_reconstruct_native_agent",
            AsyncMock(return_value=mock_native),
        ),
    ):
        results = make_deferred_tool_results(["call-1"])

        # Fire two concurrent resume calls
        task1 = asyncio.create_task(session_pool.resume_session("sess-1", results))
        task2 = asyncio.create_task(session_pool.resume_session("sess-1", results))

        # Give tasks time to start: one acquires lock, the other waits
        await asyncio.sleep(0.05)

        # task1 should be running (blocked on slow_run_stream inside the lock)
        assert not task1.done(), "First resume should be blocked on slow_run_stream"
        # task2 should be blocked waiting for the lock
        assert not task2.done(), "Second resume should be waiting for lock"

        # Release the block so task1 can finish
        block_event.set()
        await task1

        # Now task2 acquires the lock, sees status is "active", and raises
        with pytest.raises(SessionBusyError):
            await task2


@pytest.mark.anyio
async def test_resume_session_rejects_second_after_success(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """A second resume_session call after a successful resume gets SessionBusyError.

    Verifies the status re-check inside the lock catches already-resumed sessions.
    """
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
    mock_native.run_stream = _ok_gen

    mock_pool.get_agent = MagicMock(return_value=mock_native)

    with (
        patch.object(
            session_pool,
            "_load_checkpoint_data",
            AsyncMock(return_value=checkpoint_data),
        ),
        patch.object(
            session_pool,
            "_reconstruct_native_agent",
            AsyncMock(return_value=mock_native),
        ),
    ):
        results = make_deferred_tool_results(["call-1"])

        # First call succeeds (pending calls are cleared)
        await session_pool.resume_session("sess-1", results)

        # Second call: pending_deferred_calls is now empty, so pass empty results.
        # The call should reach the lock, see status="active", and raise.
        with pytest.raises(SessionBusyError):
            await session_pool.resume_session("sess-1", make_deferred_tool_results([]))


# ---------------------------------------------------------------------------
# Failed resume does NOT clear pending_deferred_calls
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_session_does_not_clear_pending_on_failure(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """pending_deferred_calls are NOT cleared if agent.run() fails.

    Status reverts to 'checkpointed' so the session can be retried.
    """
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
    mock_native.run_stream = _fail_gen

    mock_pool.get_agent = MagicMock(return_value=mock_native)

    with (
        patch.object(
            session_pool,
            "_load_checkpoint_data",
            AsyncMock(return_value=checkpoint_data),
        ),
        patch.object(
            session_pool,
            "_reconstruct_native_agent",
            AsyncMock(return_value=mock_native),
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

    # Status should be reverted to 'checkpointed' for retry
    assert session_data.status == "checkpointed"


# ---------------------------------------------------------------------------
# Retry after failure
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_session_allows_retry_after_failure(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """A second resume_session call succeeds after a failed attempt.

    Verifies that when the first resume fails and reverts status to
    'checkpointed', the second call is allowed to proceed.
    """
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

    fail_run = True

    mock_native = MagicMock()
    mock_native.name = "test-agent"
    mock_native._model = None
    mock_native.model_settings = None
    mock_agentlet = MagicMock()
    mock_native.get_agentlet = AsyncMock(return_value=mock_agentlet)

    async def run_fail_then_succeed_stream(**kwargs: Any) -> Any:
        nonlocal fail_run
        if fail_run:
            fail_run = False
            raise RuntimeError("First attempt fails")
        return  # StopAsyncIteration
        yield  # pragma: no cover

    mock_native.run_stream = run_fail_then_succeed_stream
    mock_pool.get_agent = MagicMock(return_value=mock_native)

    with (
        patch.object(
            session_pool,
            "_load_checkpoint_data",
            AsyncMock(return_value=checkpoint_data),
        ),
        patch.object(
            session_pool,
            "_reconstruct_native_agent",
            AsyncMock(return_value=mock_native),
        ),
    ):
        results = make_deferred_tool_results(["call-1"])

        # First call fails
        with pytest.raises(RuntimeError, match="First attempt fails"):
            await session_pool.resume_session("sess-1", results)

        # Status should be checkpointed again
        session_data = await store.load("sess-1")
        assert session_data is not None
        assert session_data.status == "checkpointed"
        assert len(session_data.pending_deferred_calls) == 1

        # Second call should succeed (status was reverted to checkpointed)
        await session_pool.resume_session("sess-1", results)

        # Status should now be active
        session_data = await store.load("sess-1")
        assert session_data is not None
        assert session_data.status == "active"
        assert session_data.pending_deferred_calls == []


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_resume_session_status_transitions_checkpointed_to_resuming_to_active(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """Status transitions: checkpointed -> resuming -> active on success."""
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

    # Track status transitions
    observed_statuses: list[str] = []
    original_save = store.save

    async def tracking_save(data: Any) -> None:
        observed_statuses.append(data.status)
        await original_save(data)

    store.save = tracking_save  # type: ignore[method-assign]

    mock_native = MagicMock()
    mock_native.name = "test-agent"
    mock_native._model = None
    mock_native.model_settings = None
    mock_agentlet = MagicMock()
    mock_native.get_agentlet = AsyncMock(return_value=mock_agentlet)
    mock_native.run_stream = _ok_gen

    mock_pool.get_agent = MagicMock(return_value=mock_native)

    with (
        patch.object(
            session_pool,
            "_load_checkpoint_data",
            AsyncMock(return_value=checkpoint_data),
        ),
        patch.object(
            session_pool,
            "_reconstruct_native_agent",
            AsyncMock(return_value=mock_native),
        ),
    ):
        results = make_deferred_tool_results(["call-1"])
        await session_pool.resume_session("sess-1", results)

    # Should have observed: resuming, then active
    assert "resuming" in observed_statuses
    assert "active" in observed_statuses
    # active should be the final saved status
    assert observed_statuses[-1] == "active"


@pytest.mark.anyio
async def test_resume_session_status_reverts_to_checkpointed_on_failure(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """Status reverts from resuming to checkpointed on agent.run() failure."""
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

    observed_statuses: list[str] = []
    original_save = store.save

    async def tracking_save(data: Any) -> None:
        observed_statuses.append(data.status)
        await original_save(data)

    store.save = tracking_save  # type: ignore[method-assign]

    mock_native = MagicMock()
    mock_native.name = "test-agent"
    mock_native._model = None
    mock_native.model_settings = None
    mock_agentlet = MagicMock()
    mock_native.get_agentlet = AsyncMock(return_value=mock_agentlet)
    mock_native.run_stream = _fail_gen

    mock_pool.get_agent = MagicMock(return_value=mock_native)

    with (
        patch.object(
            session_pool,
            "_load_checkpoint_data",
            AsyncMock(return_value=checkpoint_data),
        ),
        patch.object(
            session_pool,
            "_reconstruct_native_agent",
            AsyncMock(return_value=mock_native),
        ),
    ):
        results = make_deferred_tool_results(["call-1"])
        with pytest.raises(RuntimeError, match="Boom"):
            await session_pool.resume_session("sess-1", results)

    # Status transitions: resuming -> checkpointed (revert)
    assert "resuming" in observed_statuses
    assert observed_statuses[-1] == "checkpointed"
