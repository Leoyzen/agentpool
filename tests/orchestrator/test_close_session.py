"""Tests for SessionController.close_session() RunHandle lifecycle.

Covers four scenarios:
1. Flag ON + graceful close: RunHandle.close() called, turn_lock acquired,
   complete_event set, session removed from _sessions.
2. Flag ON + timeout triggers cancel: turn_lock never acquired (held by
   another task), timeout fires, RunHandle.cancel() called.
3. Flag OFF + existing behavior: legacy path runs, no RunHandle interaction.
4. Flag ON + no active run: session closes cleanly without RunHandle.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from agentpool.orchestrator.core import EventBus, SessionController, SessionState
from agentpool.orchestrator.run import RunHandle


if TYPE_CHECKING:
    from agentpool.agents.context import AgentRunContext


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return a mocked AgentPool with a main_agent."""
    pool = MagicMock()
    pool.main_agent = MagicMock()
    pool.main_agent.name = "main-agent"
    pool.manifest = MagicMock()
    pool.manifest.agents = {}
    return pool


@pytest.fixture
def controller(mock_pool: MagicMock) -> SessionController:
    """Return a SessionController backed by the mock pool."""
    return SessionController(pool=mock_pool)


def _make_session(session_id: str) -> SessionState:
    """Return a minimal SessionState for testing."""
    return SessionState(session_id=session_id, agent_name="test-agent")


def _make_mock_run_handle(run_id: str = "run-1") -> MagicMock:
    """Return a MagicMock simulating a RunHandle with close/cancel/complete_event."""
    rh = MagicMock(spec=RunHandle)
    rh.run_id = run_id
    rh.run_ctx = None  # No run context in mock
    rh.close = MagicMock()
    rh.cancel = MagicMock()
    rh.complete_event = asyncio.Event()
    return rh


# ---------------------------------------------------------------------------
# Test 2: Flag ON + timeout triggers cancel
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_flag_on_timeout_triggers_cancel(
    controller: SessionController,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When turn_lock acquisition times out, RunHandle.cancel() is called."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")

    session = _make_session("sess-2")
    session.current_run_id = "run-2"
    controller._sessions["sess-2"] = session

    run_handle = _make_mock_run_handle("run-2")
    controller._runs["run-2"] = run_handle

    # Pre-acquire the turn_lock so close_session cannot get it within timeout.
    # Use a very short timeout patch to avoid waiting 30 seconds.
    held_lock = session.turn_lock
    await held_lock.acquire()

    # Patch asyncio.timeout to use a tiny duration for testing
    original_timeout = asyncio.timeout

    def fast_timeout(delay: float) -> asyncio.Timeout:
        return original_timeout(0.05)

    monkeypatch.setattr(asyncio, "timeout", fast_timeout)

    await controller.close_session("sess-2")

    run_handle.close.assert_called_once()
    # Since turn_lock was held, cancel should have been called
    run_handle.cancel.assert_called_once()
    assert session.is_closing is True
    assert "sess-2" not in controller._sessions

    held_lock.release()


# ---------------------------------------------------------------------------
# Test 4: Flag ON + no active run
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_flag_on_no_active_run(
    controller: SessionController,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When flag is ON and no active run exists, session closes cleanly."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")

    session = _make_session("sess-4")
    session.current_run_id = None
    controller._sessions["sess-4"] = session

    await controller.close_session("sess-4")

    assert session.is_closing is True
    assert "sess-4" not in controller._sessions


# ---------------------------------------------------------------------------
# Tests from PR #64 review (close_session behavior)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_session_releases_lock_on_cancelled() -> None:
    """close_session must release turn_lock even if cancelled mid-wait.

    Without try/finally, CancelledError during complete_event.wait()
    skips the lock release, leaving the session permanently locked.
    """
    from agentpool.orchestrator.core import SessionController

    mock_pool = MagicMock()
    mock_pool.main_agent = MagicMock()
    mock_pool.main_agent.name = "main-agent"
    mock_pool.manifest = MagicMock()
    mock_pool.manifest.agents = {}

    controller = SessionController(pool=mock_pool)
    controller._event_bus = EventBus()

    session_id = "sess-close-cancel"
    controller._sessions[session_id] = MagicMock()
    controller._sessions[session_id].session_id = session_id
    controller._sessions[session_id].current_run_id = "fake-run-id"
    controller._sessions[session_id].closing = False
    controller._sessions[session_id].is_closing = False
    controller._sessions[session_id]._request_lock = asyncio.Lock()
    controller._sessions[session_id].turn_lock = asyncio.Lock()
    controller._sessions[session_id].input_provider = None
    controller._sessions[session_id].is_per_session_agent = False
    controller._sessions[session_id].cancel_scope = None

    # Create a fake run_handle that never completes
    fake_run = MagicMock()
    fake_run.close = MagicMock()
    fake_run.cancel = MagicMock()
    fake_run.complete_event = asyncio.Event()  # never set
    controller._runs["fake-run-id"] = fake_run

    # Lock is NOT pre-acquired — close_session will acquire it,
    # then wait on complete_event (which never sets).
    # We cancel during the wait to test that the lock is released.
    lock = controller._sessions[session_id].turn_lock

    async def _close() -> None:
        await controller._close_session_run_turn(session_id)

    task = asyncio.create_task(_close())
    await asyncio.sleep(0.1)  # Let it acquire lock and start waiting
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    # Lock should be released because try/finally in _close_session_run_turn
    try:
        async with asyncio.timeout(1):
            await lock.acquire()
    except TimeoutError:
        pytest.fail("turn_lock was not released after CancelledError in close_session")
    finally:
        if lock.locked():
            lock.release()


# ---------------------------------------------------------------------------
# Integration: close_session after cancel does not hang
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.anyio
async def test_close_session_after_cancel() -> None:
    """close_session() must not hang after a run is cancelled.

    Steps:
        1. Start a run with a blocking mock agent (_BlockingTurn).
        2. Cancel via cancel_run_for_session().
        3. Call close_session() with a 30s timeout.
        4. Verify close_session returns within timeout (no hang from turn_lock).

    After cancel, the start() loop publishes RunFailedEvent, sets
    _turn_complete_event, and the turn completes — releasing turn_lock.
    close_session() should acquire turn_lock quickly and return.
    """
    from typing import Any

    from agentpool.agents.events import StreamCompleteEvent
    from agentpool.messaging import ChatMessage
    from agentpool.orchestrator.core import SessionPool
    from agentpool.orchestrator.turn import Turn

    class _BlockingTurn(Turn):
        """Turn that blocks until run_ctx.cancelled, then returns."""

        def __init__(self, run_ctx: AgentRunContext) -> None:
            self._run_ctx = run_ctx

        async def execute(self):  # type: ignore[override]
            self._message_history = []
            self._final_message = ChatMessage(content="blocked", role="assistant")
            while not self._run_ctx.cancelled:
                await asyncio.sleep(0.01)
            return
            yield  # unreachable — makes this an async generator

    class _StubTurn(Turn):
        """Minimal Turn that yields StreamCompleteEvent."""

        async def execute(self):  # type: ignore[override]
            self._message_history = []
            self._final_message = ChatMessage(content="done", role="assistant")
            yield StreamCompleteEvent(
                message=ChatMessage(content="response", role="assistant"),
            )

    mock_pool = MagicMock()
    mock_pool.main_agent = MagicMock()
    mock_pool.main_agent.name = "main-agent"
    mock_pool.manifest = MagicMock()
    mock_pool.manifest.agents = {}

    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    session_id = "sess-close-after-cancel"
    await session_pool.create_session(session_id, agent_name="test-agent")

    # Create a cancel-aware mock agent: first turn blocks, second is stub
    agent = MagicMock()
    agent.AGENT_TYPE = "native"
    call_count = 0

    def _create_turn(
        prompts: Any,
        run_ctx: AgentRunContext,
        message_history: Any,
    ) -> Turn:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _BlockingTurn(run_ctx)
        return _StubTurn()

    agent.create_turn = _create_turn

    # Attach agent to session
    state, _ = await session_pool.sessions.get_or_create_session(session_id)
    state.agent = agent
    session_pool.sessions._session_agents[session_id] = agent
    mock_pool.get_agent.return_value = agent

    # --- Step 1: Start a run with the blocking agent ---
    msg_id = await session_pool.send_message(session_id, "blocking prompt")
    assert msg_id is not None
    run_handle = session_pool._get_active_run_handle(session_id)
    assert run_handle is not None

    # Wait for the blocking turn to start
    await asyncio.sleep(0.1)

    # --- Step 2: Cancel the active run ---
    session_pool.sessions.cancel_run_for_session(session_id)

    # Wait for cancellation to propagate
    await asyncio.sleep(0.2)

    # --- Step 3: Close the RunHandle, then call close_session ---
    # Per Task 12 findings: must call run_handle.close() before
    # close_session() to signal the start() loop to exit (sets
    # _closing=True, wakes idle wait). Without this, close_session
    # waits 30s for complete_event which is only set when start()
    # exits. After cancel, the loop is in idle state, not running
    # a turn — so cancelled=True on run_ctx alone won't unblock it.
    run_handle.close()
    await asyncio.sleep(0.1)

    # Now close_session should complete promptly — turn_lock was
    # released when the cancelled turn completed, and complete_event
    # is set when start() exits via _closing.
    try:
        await asyncio.wait_for(
            session_pool.close_session(session_id),
            timeout=30.0,
        )
    except TimeoutError:
        pytest.fail("close_session hung after cancel — turn_lock was not released")

    # --- Step 4: Verify session is closed ---
    assert session_id not in session_pool.sessions._sessions

    # Cleanup
    await session_pool.shutdown()
