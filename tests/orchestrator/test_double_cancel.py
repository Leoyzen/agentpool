"""Regression tests for double cancel() killing the start() generator.

Bug: abort_session in OpenCode server called cancel() twice — once via
interrupt() → cancel_run_for_session() and again via session_pool.cancel_run().
The second cancel() threw CancelledError at _idle_event.wait() inside
_idle_loop(), which was OUTSIDE the except CancelledError handler in start().
This killed the generator and all subsequent messages got stuck.

Fix: Three-layer defense:
1. abort_session no longer calls cancel_run() for per-session agents
   (interrupt() already does it internally).
2. start() now wraps the entire while-loop body (including _idle_loop())
   in except CancelledError.
3. cancel() is idempotent — if _force_cancelling is already True, it
   returns without calling task.cancel() again.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.agents.events import (
    StreamCompleteEvent,
)
from agentpool.lifecycle import RunState
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.run import RunHandle
from agentpool.orchestrator.turn import Turn


if TYPE_CHECKING:
    from agentpool.agents.context import AgentRunContext


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test doubles (mirrors test_run_handle.py patterns)
# ---------------------------------------------------------------------------


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
        yield  # makes this an async generator


class _StubTurn(Turn):
    """Minimal Turn that yields events from a list."""

    def __init__(
        self,
        *,
        events: list[Any] | None = None,
        message_history: list[Any] | None = None,
    ) -> None:
        self._events = events or []
        self._history = message_history or []

    async def execute(self):  # type: ignore[override]
        self._message_history = self._history
        self._final_message = ChatMessage(content="done", role="assistant")
        for event in self._events:
            yield event


def _stream_complete_event() -> StreamCompleteEvent[Any]:
    return StreamCompleteEvent(message=ChatMessage(content="done", role="assistant"))


def _make_run_handle(
    *,
    agent: Any | None = None,
    event_bus: Any | None = None,
    session: Any | None = None,
) -> RunHandle:
    """Create a RunHandle with mocked dependencies."""
    if agent is None:
        agent = MagicMock()
        agent.create_turn = MagicMock(return_value=_StubTurn())
    if event_bus is None:
        event_bus = AsyncMock()
    if session is None:
        session = MagicMock()
        session.turn_lock = asyncio.Lock()
    return RunHandle(
        run_id="test-run",
        session_id="test-session",
        agent_type="native",
        agent=agent,
        event_bus=event_bus,
        session=session,
    )


async def _consume_gen(gen: Any) -> None:
    """Consume an async generator to completion, discarding all events."""
    async for _ in gen:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_double_cancel_does_not_kill_generator() -> None:
    """Double cancel() must not kill the start() generator.

    Simulates the abort_session bug: interrupt() calls cancel(), then
    abort_session calls cancel_run() which calls cancel() again. The
    generator must survive and return to idle.
    """
    handle = _make_run_handle()
    blocking_turn = _BlockingTurn(handle.run_ctx)
    stub_turn = _StubTurn(
        events=[_stream_complete_event()],
        message_history=["msg"],
    )
    handle.agent.create_turn = MagicMock(side_effect=[blocking_turn, stub_turn])

    gen = handle.start("hello")
    consumer_task = asyncio.create_task(_consume_gen(gen))
    await asyncio.sleep(0.05)

    # First cancel (simulates interrupt() → cancel_run_for_session())
    handle.cancel()
    await asyncio.sleep(0.1)

    # At this point, the generator should have caught CancelledError,
    # reset _force_cancelling, and entered _idle_loop().
    assert handle._force_cancelling is False, (
        "_force_cancelling should be False after first cancel was handled"
    )

    # Second cancel (simulates session_pool.cancel_run())
    # This must NOT kill the generator.
    handle.cancel()
    await asyncio.sleep(0.1)

    # Generator must still be alive — _closed should be False
    assert not handle._closed, (
        "Generator must not be killed by double cancel; _closed should be False"
    )

    # RunHandle should be in IDLE state, not DONE
    assert handle._run_state == RunState.IDLE, (
        f"RunHandle should be idle after double cancel, got {handle._run_state}"
    )

    # Clean up
    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task


@pytest.mark.unit
async def test_double_cancel_then_followup_processes_message() -> None:
    """After double cancel, followup() must still deliver messages.

    This is the core regression: after the bug, messages sent after
    cancel were stuck because the generator was dead.
    """
    handle = _make_run_handle()
    blocking_turn = _BlockingTurn(handle.run_ctx)
    stub_turn = _StubTurn(
        events=[_stream_complete_event()],
        message_history=["msg"],
    )
    handle.agent.create_turn = MagicMock(side_effect=[blocking_turn, stub_turn])

    gen = handle.start("hello")
    consumer_task = asyncio.create_task(_consume_gen(gen))
    await asyncio.sleep(0.05)

    # Double cancel (simulates abort_session)
    handle.cancel()
    await asyncio.sleep(0.1)
    handle.cancel()
    await asyncio.sleep(0.1)

    # Generator survived — now send a followup message
    msg_id = handle.followup("second prompt")
    assert msg_id is not None, "followup() must return a message_id after double cancel"

    # Wait for the second turn to execute and the generator to process it
    await asyncio.sleep(0.3)

    # The second turn should have been created (second call to create_turn)
    assert handle.agent.create_turn.call_count >= 2, (
        f"Expected at least 2 create_turn calls, got {handle.agent.create_turn.call_count}"
    )

    # RunHandle should be idle (after second turn completed)
    assert handle._run_state == RunState.IDLE, (
        f"RunHandle should be idle after followup turn, got {handle._run_state}"
    )

    # Clean up
    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task


@pytest.mark.unit
async def test_cancel_idempotent_when_force_cancelling_already_true() -> None:
    """cancel() must be idempotent when _force_cancelling is already True.

    If _force_cancelling is True (from a previous cancel that hasn't
    been processed yet), cancel() must NOT call task.cancel() again.
    """
    handle = _make_run_handle()
    blocking_turn = _BlockingTurn(handle.run_ctx)
    handle.agent.create_turn = MagicMock(side_effect=[blocking_turn, _StubTurn()])

    gen = handle.start("hello")
    consumer_task = asyncio.create_task(_consume_gen(gen))
    await asyncio.sleep(0.05)

    # First cancel sets _force_cancelling = True
    handle.cancel()
    assert handle._force_cancelling is True, "_force_cancelling should be True after first cancel"

    # Second cancel while _force_cancelling is still True
    # (hasn't been processed by the generator yet)
    handle.cancel()

    # _force_cancelling should still be True (second cancel didn't reset it)
    # but task.cancel() should NOT have been called again.
    # The key assertion: the generator is still alive.
    await asyncio.sleep(0.1)
    assert not handle._closed, "Generator must survive idempotent double cancel"

    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task


@pytest.mark.unit
async def test_followup_rejected_after_close() -> None:
    """followup() must return None after close() sets _closed.

    This is the defense-in-depth fix: even if the generator somehow
    dies, followup() must not silently deliver messages to a dead run.
    """
    handle = _make_run_handle()
    stub_turn = _StubTurn(
        events=[_stream_complete_event()],
        message_history=["msg"],
    )
    handle.agent.create_turn = MagicMock(return_value=stub_turn)

    gen = handle.start("hello")
    consumer_task = asyncio.create_task(_consume_gen(gen))
    await asyncio.sleep(0.1)

    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task

    # followup after close must return None
    result = handle.followup("should be rejected")
    assert result is None, "followup() must return None after close()"


@pytest.mark.unit
async def test_cancelled_event_loop_survives_idle_cancel() -> None:
    """CancelledError thrown during _idle_loop() must be caught.

    This directly tests Fix #2: the widened except CancelledError handler
    in start(). A CancelledError at _idle_event.wait() inside _idle_loop()
    must be caught by the outer handler, not kill the generator.
    """
    handle = _make_run_handle()
    blocking_turn = _BlockingTurn(handle.run_ctx)
    stub_turn = _StubTurn(
        events=[_stream_complete_event()],
        message_history=["msg"],
    )
    handle.agent.create_turn = MagicMock(side_effect=[blocking_turn, stub_turn])

    gen = handle.start("hello")
    consumer_task = asyncio.create_task(_consume_gen(gen))
    await asyncio.sleep(0.05)

    # First cancel: enters _idle_loop() after handling
    handle.cancel()
    await asyncio.sleep(0.15)

    # Now the generator is in _idle_loop(), waiting on _idle_event
    # Second cancel: throws CancelledError at _idle_event.wait()
    handle.cancel()
    await asyncio.sleep(0.15)

    # Generator must have caught the CancelledError and re-entered idle
    assert handle._run_state == RunState.IDLE, (
        f"RunHandle should be idle after idle-loop cancel, got {handle._run_state}"
    )
    assert not handle._closed, "Generator must survive CancelledError in _idle_loop"

    # Send a message — it must be processed
    handle.followup("after idle cancel")
    await asyncio.sleep(0.2)

    # Clean up
    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task
