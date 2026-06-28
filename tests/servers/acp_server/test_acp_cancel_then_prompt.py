"""Integration test: ACP cancel-then-prompt does not hang.

Tests the SessionPool-level behavior that the ACP handler relies on
when a client sends session/cancel followed immediately by session/prompt.

The ACP handler's ``cancel_session()`` delegates to
``SessionPool.sessions.cancel_run_for_session()``, and its ``handle_prompt()``
delegates to ``SessionPool.receive_request()``. This test verifies that the
underlying SessionPool correctly handles the cancel-then-prompt sequence
without hanging — the same sequence that occurs when an ACP client cancels
a run and immediately sends a new prompt.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import MagicMock

import anyio
import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunFailedEvent, RunStartedEvent, StreamCompleteEvent
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import EventBus, EventEnvelope, SessionPool
from agentpool.orchestrator.run import RunHandle, RunStatus
from agentpool.orchestrator.turn import Turn


pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def _unwrap_event(event: Any) -> Any:
    """Unwrap EventEnvelope if present, otherwise return the event as-is."""
    return event.event if isinstance(event, EventEnvelope) else event


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _BlockingTurn(Turn):
    """Turn that blocks until run_ctx.cancelled, then returns without StreamCompleteEvent."""

    def __init__(self, run_ctx: AgentRunContext) -> None:
        self._run_ctx = run_ctx

    async def execute(self):  # type: ignore[override]
        self._message_history = []
        self._final_message = ChatMessage(content="blocked", role="assistant")
        while not self._run_ctx.cancelled:
            await asyncio.sleep(0.01)
        return
        yield  # noqa: unreachable — makes this an async generator


class _StubTurn(Turn):
    """Minimal Turn that yields events from a list and sets message history."""

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


# ---------------------------------------------------------------------------
# Helpers
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


async def _attach_agent(
    pool: SessionPool,
    session_id: str,
    agent: MagicMock,
) -> None:
    """Attach a mock agent to an existing session."""
    state, _ = await pool.sessions.get_or_create_session(session_id)
    state.agent = agent
    pool.sessions._session_agents[session_id] = agent
    pool.pool.get_agent.return_value = agent  # type: ignore[attr-defined]


def _make_cancel_aware_agent() -> MagicMock:
    """Create a mock agent whose first create_turn returns _BlockingTurn.

    Subsequent calls return _StubTurn instances that yield StreamCompleteEvent.
    """
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
        return _StubTurn(
            events=[
                StreamCompleteEvent(
                    message=ChatMessage(content="response", role="assistant"),
                ),
            ],
            message_history=["msg"],
        )

    agent.create_turn = _create_turn
    return agent


async def _drain_queue(queue: anyio.streams.memory.MemoryObjectReceiveStream) -> list[Any]:
    """Drain all currently-available events from a queue without blocking."""
    events: list[Any] = []
    while True:
        with contextlib.suppress(anyio.WouldBlock):
            events.append(queue.receive_nowait())
            continue
        break
    return events


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_acp_cancel_then_prompt_no_hang(
    mock_pool: MagicMock,
) -> None:
    """ACP cancel-then-prompt sequence does not hang at the SessionPool level.

    Simulates the ACP handler flow:
        1. ``handle_prompt()`` → ``receive_request()`` starts a blocking run.
        2. ``cancel_session()`` → ``cancel_run_for_session()`` cancels it.
        3. ``handle_prompt()`` → ``receive_request()`` sends a new prompt.

    The second ``receive_request()`` must return within 30s (no hang),
    and the new prompt must be processed (RunStartedEvent + StreamCompleteEvent).

    Uses ``asyncio.wait_for()`` with a 30s timeout to catch hangs.
    """
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    session_id = "sess-acp-cancel-prompt"
    await session_pool.create_session(session_id, agent_name="test-agent")

    agent = _make_cancel_aware_agent()
    await _attach_agent(session_pool, session_id, agent)

    # Subscribe to events BEFORE sending the first prompt
    queue = await session_pool.event_bus.subscribe(session_id)

    # --- Step 1: Start a run with the blocking agent (simulates handle_prompt) ---
    first_handle = await session_pool.receive_request(session_id, "first prompt")
    assert first_handle is not None, (
        "receive_request should return a RunHandle for idle session"
    )

    # Wait for the blocking turn to start
    await asyncio.sleep(0.1)

    # --- Step 2: Cancel the active run (simulates cancel_session) ---
    session_pool.sessions.cancel_run_for_session(session_id)

    # Wait for cancellation to propagate: the start() loop should
    # publish RunFailedEvent, set _turn_complete_event, clear the
    # message queue, and continue.
    await asyncio.sleep(0.2)

    # Drain events published so far
    pre_events = await _drain_queue(queue)
    pre_event_types = [type(_unwrap_event(e)) for e in pre_events]

    # RunFailedEvent must have been published as a result of the cancel
    assert RunFailedEvent in pre_event_types, (
        f"Expected RunFailedEvent from cancelled turn, got: {pre_event_types}"
    )

    # --- Step 3: Send a new prompt (simulates second handle_prompt) ---
    # Use asyncio.wait_for to catch hangs.
    second_handle = await asyncio.wait_for(
        session_pool.receive_request(session_id, "second prompt"),
        timeout=30.0,
    )

    # --- Step 4: Verify new prompt is processed (events published, no hang) ---
    post_events: list[Any] = []
    try:
        async with asyncio.timeout(30.0):
            while True:
                try:
                    event = await asyncio.wait_for(queue.receive(), timeout=5.0)
                    post_events.append(event)
                    unwrapped = _unwrap_event(event)
                    if isinstance(unwrapped, StreamCompleteEvent):
                        break
                except asyncio.TimeoutError:
                    break
    except TimeoutError:
        pytest.fail("Timed out waiting for events after cancel-then-prompt")

    post_event_types = [type(_unwrap_event(e)) for e in post_events]

    assert RunStartedEvent in post_event_types, (
        f"Expected RunStartedEvent for new prompt, got: {post_event_types}"
    )
    assert StreamCompleteEvent in post_event_types, (
        f"Expected StreamCompleteEvent for new prompt, got: {post_event_types}"
    )

    # --- Step 5: Verify RunHandle state ---
    if second_handle is not None:
        assert second_handle is not first_handle, (
            "New RunHandle should be a different instance if old one was cleaned up"
        )

    assert first_handle._status in (RunStatus.idle, RunStatus.done), (
        f"First RunHandle should be idle or done, got: {first_handle._status}"
    )

    # Cleanup: close the RunHandle first so the start() loop exits and
    # releases turn_lock. Otherwise close_session waits 30s for the lock.
    first_handle.close()
    await asyncio.sleep(0.1)
    await session_pool.shutdown()
