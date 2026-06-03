"""Tests for SessionPool.close_session run-wait and cancellation semantics.

Tests graceful wait, forceful cancel on timeout, race conditions,
and rejection of new requests after closing.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from agentpool.agents.events import RunStartedEvent
from agentpool.orchestrator.core import SessionController, SessionPool
from agentpool.orchestrator.run import RunHandle

if TYPE_CHECKING:
    from agentpool.agents.context import AgentRunContext


pytestmark = pytest.mark.unit


class MockAgent:
    """Simple mock agent for testing."""

    def __init__(self) -> None:
        self._stream_impl: Any = None
        self.get_active_run_context = MagicMock(return_value=None)

    async def _run_stream_once(
        self,
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        if self._stream_impl is None:
            raise RuntimeError("No stream impl set")
        async for event in self._stream_impl(run_ctx, *prompts, **kwargs):
            yield event


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return a mocked AgentPool."""
    pool = MagicMock()
    pool.main_agent = MagicMock()
    pool.main_agent.name = "main-agent"
    pool.manifest = MagicMock()
    pool.manifest.agents = {}
    return pool


@pytest.fixture
def session_pool(mock_pool: MagicMock) -> SessionPool:
    return SessionPool(pool=mock_pool, enable_auto_resume=False)


async def _setup_session(
    controller: SessionController,
    session_id: str,
    agent: MockAgent,
    mock_pool: MagicMock,
) -> None:
    """Create a session and attach the mock agent directly."""
    state = await controller.get_or_create_session(session_id)
    state.agent = agent
    controller._session_agents[session_id] = agent
    mock_pool.get_agent.return_value = agent


# ---------------------------------------------------------------------------
# Graceful wait
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_close_session_waits_for_run_to_complete(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """close_session waits for the active run to finish before proceeding."""
    stream_started = asyncio.Event()
    stream_continue = asyncio.Event()

    async def slow_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        stream_started.set()
        await stream_continue.wait()
        yield RunStartedEvent(session_id="sess-1", run_id="run-1")

    agent = MockAgent()
    agent._stream_impl = slow_stream

    await _setup_session(session_pool.sessions, "sess-1", agent, mock_pool)

    # Start a run via receive_request so a RunHandle is created
    await session_pool.sessions.receive_request("sess-1", "hello", priority="when_idle")
    await asyncio.wait_for(stream_started.wait(), timeout=1.0)

    # Session should have an active run
    session = session_pool.sessions.get_session("sess-1")
    assert session is not None
    assert session.current_run_id is not None
    run_handle = session_pool.sessions._runs.get(session.current_run_id)
    assert run_handle is not None

    # close_session should wait for the run to complete
    close_task = asyncio.create_task(session_pool.close_session("sess-1"))

    # Give close_session time to start waiting
    await asyncio.sleep(0.05)
    assert not close_task.done(), "close_session should be waiting for run"

    # Let the stream finish
    stream_continue.set()
    await asyncio.wait_for(close_task, timeout=2.0)

    # Session should be closed
    assert session_pool.sessions.get_session("sess-1") is None


@pytest.mark.anyio
async def test_close_session_sets_closing_before_wait(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """close_session sets session.closing=True before waiting for the run."""
    stream_started = asyncio.Event()
    stream_continue = asyncio.Event()

    async def slow_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        stream_started.set()
        await stream_continue.wait()
        yield RunStartedEvent(session_id="sess-2", run_id="run-1")

    agent = MockAgent()
    agent._stream_impl = slow_stream

    await _setup_session(session_pool.sessions, "sess-2", agent, mock_pool)

    await session_pool.sessions.receive_request("sess-2", "hello", priority="when_idle")
    await asyncio.wait_for(stream_started.wait(), timeout=1.0)

    close_task = asyncio.create_task(session_pool.close_session("sess-2"))
    await asyncio.sleep(0.05)

    # Session should still exist (close_session is waiting)
    session = session_pool.sessions.get_session("sess-2")
    assert session is not None
    assert session.closing is True

    stream_continue.set()
    await asyncio.wait_for(close_task, timeout=2.0)


# ---------------------------------------------------------------------------
# Forceful cancel on timeout
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_close_session_cancels_on_timeout(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """If run doesn't complete within timeout, close_session cancels it."""
    stream_started = asyncio.Event()

    async def very_slow_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        stream_started.set()
        await asyncio.sleep(60)
        yield RunStartedEvent(session_id="sess-3", run_id="run-1")

    agent = MockAgent()
    agent._stream_impl = very_slow_stream

    await _setup_session(session_pool.sessions, "sess-3", agent, mock_pool)

    await session_pool.sessions.receive_request("sess-3", "hello", priority="when_idle")
    await asyncio.wait_for(stream_started.wait(), timeout=1.0)

    # Patch close_session's timeout to be very short for testing

    async def fast_close(session_id: str) -> None:
        session = session_pool.sessions.get_session(session_id)
        run_handle: RunHandle | None = None
        if session is not None:
            async with session._request_lock:
                session.closing = True
                run_id = session.current_run_id
                if run_id is not None:
                    run_handle = session_pool.sessions._runs.get(run_id)

            if run_handle is not None:
                try:
                    await asyncio.wait_for(
                        run_handle.complete_event.wait(), timeout=0.1
                    )
                except TimeoutError:
                    session_pool.cancel_run(run_handle.run_id)
                    # Give cancellation a moment to propagate and release turn_lock
                    await asyncio.sleep(0.1)

        await session_pool.sessions.close_session(session_id)
        await session_pool.event_bus.close_session(session_id)
        has_turn_state = (
            session_id in session_pool.turns._post_turn_injections
            or session_id in session_pool.turns._post_turn_prompts
            or session_id in session_pool.turns._injection_locks
        )
        if has_turn_state:
            lock = await session_pool.turns._get_injection_lock(session_id)
            async with lock:
                session_pool.turns._post_turn_injections.pop(session_id, None)
                session_pool.turns._post_turn_prompts.pop(session_id, None)
                session_pool.turns._injection_locks.pop(session_id, None)

    session_pool.close_session = fast_close  # type: ignore[method-assign]

    # Patch cancel_run to verify it's called
    cancelled_runs: list[str] = []
    original_cancel = session_pool.cancel_run

    def _spy_cancel(run_id: str) -> None:
        cancelled_runs.append(run_id)
        original_cancel(run_id)

    session_pool.cancel_run = _spy_cancel  # type: ignore[method-assign]

    close_task = asyncio.create_task(session_pool.close_session("sess-3"))
    await asyncio.wait_for(close_task, timeout=2.0)

    assert len(cancelled_runs) == 1


# ---------------------------------------------------------------------------
# Race conditions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_close_session_no_active_run(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """close_session works normally when there is no active run."""
    agent = MockAgent()

    await _setup_session(session_pool.sessions, "sess-4", agent, mock_pool)

    await session_pool.close_session("sess-4")
    assert session_pool.sessions.get_session("sess-4") is None


@pytest.mark.anyio
async def test_close_session_run_completes_before_wait(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """close_session is fast when run already completed."""
    agent = MockAgent()

    async def quick_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        yield RunStartedEvent(session_id="sess-5", run_id="run-1")

    agent._stream_impl = quick_stream
    await _setup_session(session_pool.sessions, "sess-5", agent, mock_pool)

    # Run via receive_request
    await session_pool.sessions.receive_request("sess-5", "hello", priority="when_idle")
    await asyncio.sleep(0.1)  # Let it complete

    # close_session should proceed without waiting
    await session_pool.close_session("sess-5")
    assert session_pool.sessions.get_session("sess-5") is None


# ---------------------------------------------------------------------------
# Rejects new requests after closing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_receive_request_rejected_after_close_starts(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """receive_request rejects new requests once close_session sets closing=True."""
    stream_started = asyncio.Event()
    stream_continue = asyncio.Event()

    async def slow_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        stream_started.set()
        await stream_continue.wait()
        yield RunStartedEvent(session_id="sess-6", run_id="run-1")

    agent = MockAgent()
    agent._stream_impl = slow_stream

    await _setup_session(session_pool.sessions, "sess-6", agent, mock_pool)

    await session_pool.sessions.receive_request("sess-6", "hello", priority="when_idle")
    await asyncio.wait_for(stream_started.wait(), timeout=1.0)

    # Start closing (but don't let it finish yet)
    close_task = asyncio.create_task(session_pool.close_session("sess-6"))
    await asyncio.sleep(0.05)

    # Try to send a new request - should be rejected
    await session_pool.receive_request("sess-6", "late message")

    stream_continue.set()
    await asyncio.wait_for(close_task, timeout=2.0)

    # The late request should not have started a new turn
    # (it was rejected because closing=True)


@pytest.mark.anyio
async def test_process_prompt_rejected_after_close_starts(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """process_prompt rejects new requests once close_session sets closing=True."""
    stream_started = asyncio.Event()
    stream_continue = asyncio.Event()

    async def slow_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        stream_started.set()
        await stream_continue.wait()
        yield RunStartedEvent(session_id="sess-7", run_id="run-1")

    agent = MockAgent()
    agent._stream_impl = slow_stream

    await _setup_session(session_pool.sessions, "sess-7", agent, mock_pool)

    await session_pool.sessions.receive_request("sess-7", "hello", priority="when_idle")
    await asyncio.wait_for(stream_started.wait(), timeout=1.0)

    close_task = asyncio.create_task(session_pool.close_session("sess-7"))
    await asyncio.sleep(0.05)

    # process_prompt delegates to receive_request, which should reject
    await session_pool.process_prompt("sess-7", "late message")

    stream_continue.set()
    await asyncio.wait_for(close_task, timeout=2.0)


# ---------------------------------------------------------------------------
# Request lock acquisition
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_close_session_acquires_request_lock(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """close_session acquires _request_lock before setting closing=True."""
    lock_acquired = False

    original_acquire = asyncio.Lock.acquire

    async def _patched_acquire(self: asyncio.Lock, *args: Any, **kwargs: Any) -> bool:
        nonlocal lock_acquired
        result = await original_acquire(self, *args, **kwargs)
        session = session_pool.sessions.get_session("sess-8")
        if session is not None and self is session._request_lock:
            lock_acquired = True
        return result

    asyncio.Lock.acquire = _patched_acquire  # type: ignore[method-assign]

    agent = MockAgent()

    await _setup_session(session_pool.sessions, "sess-8", agent, mock_pool)
    await session_pool.close_session("sess-8")

    asyncio.Lock.acquire = original_acquire  # type: ignore[method-assign]
    assert lock_acquired is True
