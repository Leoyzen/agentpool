"""End-to-end integration test for cancel-then-prompt full flow.

Tests the complete lifecycle: start a run with a slow mock agent,
cancel it, send a new prompt, and verify the new prompt is processed
without hanging.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import MagicMock

import anyio
import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import (
    RunFailedEvent,
    RunStartedEvent,
    StreamCompleteEvent,
    ToolCallStartEvent,
)
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


class _ToolBlockingTurn(Turn):
    """Turn that yields ToolCallStartEvent then blocks until run_ctx.cancelled."""

    def __init__(self, run_ctx: AgentRunContext) -> None:
        self._run_ctx = run_ctx

    async def execute(self):  # type: ignore[override]
        self._message_history = []
        self._final_message = ChatMessage(content="tool-blocked", role="assistant")
        yield ToolCallStartEvent(
            tool_call_id="test-tool-1",
            tool_name="bash",
            title="Running bash command",
        )
        while not self._run_ctx.cancelled:
            await asyncio.sleep(0.01)
        return


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
async def test_cancel_then_new_prompt_full_flow(
    mock_pool: MagicMock,
) -> None:
    """End-to-end: cancel a running turn, then send a new prompt.

    Steps:
        1. Start a run with a slow mock agent (_BlockingTurn).
        2. Cancel via cancel_run_for_session().
        3. Send new prompt via receive_request().
        4. Verify new prompt processed (events published, no hang).
        5. Verify RunHandle is same instance (1:1 model) or new one (if old died).

    Uses asyncio.wait_for() with a 30s timeout to catch hangs.
    """
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    session_id = "sess-cancel-e2e"
    await session_pool.create_session(session_id, agent_name="test-agent")

    agent = _make_cancel_aware_agent()
    await _attach_agent(session_pool, session_id, agent)

    # Subscribe to events BEFORE sending the first prompt
    queue = await session_pool.event_bus.subscribe(session_id)

    # --- Step 1: Start a run with the blocking agent ---
    first_handle = await session_pool.receive_request(session_id, "first prompt")
    assert first_handle is not None, (
        "receive_request should return a RunHandle for idle session"
    )

    # Wait for the blocking turn to start
    await asyncio.sleep(0.1)

    # --- Step 2: Cancel the active run ---
    session_pool.sessions.cancel_run_for_session(session_id)

    # Wait for cancellation to propagate: the start() loop should
    # publish RunFailedEvent, set _turn_complete_event, clear the
    # message queue, and continue.
    await asyncio.sleep(0.2)

    # Drain events published so far (RunStartedEvent, RunFailedEvent,
    # and possibly RunStartedEvent + StreamCompleteEvent from the
    # automatic second turn).
    pre_events = await _drain_queue(queue)
    pre_event_types = [type(_unwrap_event(e)) for e in pre_events]

    # RunFailedEvent must have been published as a result of the cancel
    assert RunFailedEvent in pre_event_types, (
        f"Expected RunFailedEvent from cancelled turn, got: {pre_event_types}"
    )

    # --- Step 3: Send a new prompt via receive_request ---
    # Use asyncio.wait_for to catch hangs.
    second_handle = await asyncio.wait_for(
        session_pool.receive_request(session_id, "second prompt"),
        timeout=30.0,
    )

    # --- Step 4: Verify new prompt is processed (events published, no hang) ---
    # Collect events with a timeout. We expect at least RunStartedEvent
    # and StreamCompleteEvent from the new prompt's turn.
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

    # We should see RunStartedEvent for the new turn
    assert RunStartedEvent in post_event_types, (
        f"Expected RunStartedEvent for new prompt, got: {post_event_types}"
    )

    # We should see StreamCompleteEvent for the new turn
    assert StreamCompleteEvent in post_event_types, (
        f"Expected StreamCompleteEvent for new prompt, got: {post_event_types}"
    )

    # --- Step 5: Verify RunHandle identity ---
    # In the 1:1 model, receive_request steers the existing idle RunHandle
    # (returns None). If the old run died and a new one was created,
    # receive_request returns a new RunHandle.
    if second_handle is not None:
        # A new RunHandle was created — verify it's different from the first
        assert second_handle is not first_handle, (
            "New RunHandle should be a different instance if old one was cleaned up"
        )
    # If second_handle is None, the existing RunHandle was steered (1:1 model).

    # Verify the first handle is not stuck in a running state
    assert first_handle._status in (RunStatus.idle, RunStatus.done), (
        f"First RunHandle should be idle or done, got: {first_handle._status}"
    )

    # Cleanup: close the RunHandle first so the start() loop exits and
    # releases turn_lock. Otherwise close_session waits 30s for the lock.
    first_handle.close()
    await asyncio.sleep(0.1)
    await session_pool.shutdown()


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


async def _setup_pool_and_session(
    mock_pool: MagicMock,
    session_id: str,
) -> tuple[SessionPool, str]:
    """Create a SessionPool and an empty session for testing."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()
    await session_pool.create_session(session_id, agent_name="test-agent")
    return session_pool, session_id


def _make_tool_blocking_agent() -> MagicMock:
    """Create a mock agent whose first create_turn returns _ToolBlockingTurn.

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
            return _ToolBlockingTurn(run_ctx)
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


def _make_stub_then_die_agent() -> MagicMock:
    """Create a mock agent: first create_turn returns _StubTurn, second raises, rest _StubTurn."""
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
        if call_count == 2:
            msg = "Simulated unrecoverable error in create_turn"
            raise RuntimeError(msg)
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


async def _collect_events_until(
    queue: anyio.streams.memory.MemoryObjectReceiveStream,
    target_type: type,
    *,
    timeout: float = 30.0,
) -> list[Any]:
    """Collect events from a queue until a target event type is seen."""
    events: list[Any] = []
    try:
        async with asyncio.timeout(timeout):
            while True:
                try:
                    event = await asyncio.wait_for(queue.receive(), timeout=5.0)
                    events.append(event)
                    if isinstance(_unwrap_event(event), target_type):
                        break
                except asyncio.TimeoutError:
                    break
    except TimeoutError:
        pytest.fail(f"Timed out waiting for {target_type.__name__}")
    return events


@pytest.mark.anyio
async def test_double_cancel(mock_pool: MagicMock) -> None:
    """Call cancel() twice during active turn — idempotent, no errors.

    Given: a running turn with _BlockingTurn.
    When: cancel() is called twice.
    Then: no exceptions, RunHandle returns to idle/done, new prompt works.
    """
    session_pool = SessionPool(mock_pool)
    await session_pool.start()
    session_id = "sess-double-cancel"
    await session_pool.create_session(session_id, agent_name="test-agent")

    agent = _make_cancel_aware_agent()
    await _attach_agent(session_pool, session_id, agent)
    queue = await session_pool.event_bus.subscribe(session_id)

    # Start a blocking turn
    first_handle = await session_pool.receive_request(session_id, "first prompt")
    assert first_handle is not None
    await asyncio.sleep(0.1)

    # Cancel twice — second call is idempotent (cancelled already True)
    session_pool.sessions.cancel_run_for_session(session_id)
    session_pool.sessions.cancel_run_for_session(session_id)

    # Wait for cancellation to propagate
    await asyncio.sleep(0.2)
    pre_events = await _drain_queue(queue)
    pre_types = [type(_unwrap_event(e)) for e in pre_events]
    assert RunFailedEvent in pre_types, f"Expected RunFailedEvent, got: {pre_types}"

    # RunHandle should be idle or done (not running)
    assert first_handle._status in (RunStatus.idle, RunStatus.done), (
        f"RunHandle should be idle/done after double cancel, got: {first_handle._status}"
    )

    # Send a new prompt — should not hang
    second_handle = await asyncio.wait_for(
        session_pool.receive_request(session_id, "second prompt"),
        timeout=30.0,
    )

    # Collect events — should see StreamCompleteEvent from the new turn
    post_events = await _collect_events_until(queue, StreamCompleteEvent)
    post_types = [type(_unwrap_event(e)) for e in post_events]
    assert StreamCompleteEvent in post_types, f"Expected StreamCompleteEvent, got: {post_types}"

    first_handle.close()
    await asyncio.sleep(0.1)
    await session_pool.shutdown()


@pytest.mark.anyio
async def test_cancel_during_idle_then_new_prompt(mock_pool: MagicMock) -> None:
    """Cancel while idle (no active turn), then send new prompt.

    Given: a completed turn, RunHandle is idle.
    When: cancel() is called while idle, then a new prompt is sent.
    Then: cancelled flag is reset before new turn starts, prompt is processed.
    """
    session_pool = SessionPool(mock_pool)
    await session_pool.start()
    session_id = "sess-cancel-idle"
    await session_pool.create_session(session_id, agent_name="test-agent")

    # Agent: first turn is _StubTurn (completes immediately), rest are _StubTurn
    agent = _make_cancel_aware_agent()
    await _attach_agent(session_pool, session_id, agent)
    queue = await session_pool.event_bus.subscribe(session_id)

    # Start a turn that completes immediately (_StubTurn, not _BlockingTurn)
    # _make_cancel_aware_agent returns _BlockingTurn on first call, so we need
    # a different agent for this test.
    stub_agent = MagicMock()
    stub_agent.AGENT_TYPE = "native"
    stub_agent.create_turn = lambda prompts, run_ctx, message_history: _StubTurn(
        events=[
            StreamCompleteEvent(
                message=ChatMessage(content="response", role="assistant"),
            ),
        ],
        message_history=["msg"],
    )
    await _attach_agent(session_pool, session_id, stub_agent)

    # Start first turn — completes immediately
    first_handle = await session_pool.receive_request(session_id, "first prompt")
    assert first_handle is not None

    # Wait for first turn to complete and handle to go idle
    await _collect_events_until(queue, StreamCompleteEvent)
    await asyncio.sleep(0.1)

    # Cancel while idle (no active turn)
    session_pool.sessions.cancel_run_for_session(session_id)
    await asyncio.sleep(0.05)

    # cancelled flag should be True after cancel
    assert first_handle.run_ctx.cancelled is True, (
        "cancelled flag should be True after cancel while idle"
    )

    # Send a new prompt — cancelled flag should be reset before new turn
    second_handle = await asyncio.wait_for(
        session_pool.receive_request(session_id, "second prompt"),
        timeout=30.0,
    )

    # Collect events — should see StreamCompleteEvent from the new turn
    post_events = await _collect_events_until(queue, StreamCompleteEvent)
    post_types = [type(_unwrap_event(e)) for e in post_events]
    assert StreamCompleteEvent in post_types, (
        f"Expected StreamCompleteEvent from new turn, got: {post_types}"
    )

    # cancelled flag should be False after the new turn starts
    assert first_handle.run_ctx.cancelled is False, (
        "cancelled flag should be reset to False before new turn"
    )

    first_handle.close()
    await asyncio.sleep(0.1)
    await session_pool.shutdown()


@pytest.mark.anyio
async def test_cancel_then_steer_continues_turn(mock_pool: MagicMock) -> None:
    """Cancel then immediately steer() — cancel interrupts turn, steer queues for next.

    Given: a running turn with _BlockingTurn.
    When: cancel() is called, then steer() is called immediately.
    Then: cancel interrupts the current turn, steer message is queued,
          and a subsequent turn processes it.
    """
    session_pool = SessionPool(mock_pool)
    await session_pool.start()
    session_id = "sess-cancel-steer"
    await session_pool.create_session(session_id, agent_name="test-agent")

    agent = _make_cancel_aware_agent()
    await _attach_agent(session_pool, session_id, agent)
    queue = await session_pool.event_bus.subscribe(session_id)

    # Start a blocking turn
    first_handle = await session_pool.receive_request(session_id, "first prompt")
    assert first_handle is not None
    await asyncio.sleep(0.1)

    # Cancel then immediately steer
    session_pool.sessions.cancel_run_for_session(session_id)
    steer_result = first_handle.steer("steer message")
    assert steer_result is True, "steer() should return True (message delivered/queued)"

    # Wait for cancellation and subsequent turn to process
    post_events = await _collect_events_until(queue, StreamCompleteEvent, timeout=30.0)
    post_types = [type(_unwrap_event(e)) for e in post_events]

    # Cancel should have produced RunFailedEvent
    assert RunFailedEvent in post_types, (
        f"Expected RunFailedEvent from cancelled turn, got: {post_types}"
    )
    # Subsequent turn should produce StreamCompleteEvent
    assert StreamCompleteEvent in post_types, (
        f"Expected StreamCompleteEvent from subsequent turn, got: {post_types}"
    )

    first_handle.close()
    await asyncio.sleep(0.1)
    await session_pool.shutdown()


@pytest.mark.anyio
async def test_cancel_during_tool_execution(mock_pool: MagicMock) -> None:
    """Cancel during tool execution — run_ctx.cancelled is set, turn exits after tool.

    Given: a turn that yields ToolCallStartEvent then blocks.
    When: cancel() is called during the blocking period.
    Then: run_ctx.cancelled is set, turn exits, RunFailedEvent is published.
    """
    session_pool = SessionPool(mock_pool)
    await session_pool.start()
    session_id = "sess-cancel-tool"
    await session_pool.create_session(session_id, agent_name="test-agent")

    agent = _make_tool_blocking_agent()
    await _attach_agent(session_pool, session_id, agent)
    queue = await session_pool.event_bus.subscribe(session_id)

    # Start a turn that yields ToolCallStartEvent then blocks
    first_handle = await session_pool.receive_request(session_id, "first prompt")
    assert first_handle is not None
    await asyncio.sleep(0.1)

    # Cancel during tool execution (while _ToolBlockingTurn is blocking)
    session_pool.sessions.cancel_run_for_session(session_id)

    # Wait for cancellation to propagate
    await asyncio.sleep(0.2)
    events = await _drain_queue(queue)
    event_types = [type(_unwrap_event(e)) for e in events]

    # ToolCallStartEvent should have been published before cancel
    assert ToolCallStartEvent in event_types, (
        f"Expected ToolCallStartEvent before cancel, got: {event_types}"
    )
    # RunFailedEvent should have been published after cancel
    assert RunFailedEvent in event_types, (
        f"Expected RunFailedEvent after cancel, got: {event_types}"
    )

    # RunHandle should be idle or done
    assert first_handle._status in (RunStatus.idle, RunStatus.done), (
        f"RunHandle should be idle/done after cancel, got: {first_handle._status}"
    )

    first_handle.close()
    await asyncio.sleep(0.1)
    await session_pool.shutdown()


@pytest.mark.anyio
async def test_cancel_then_followup_next_turn(mock_pool: MagicMock) -> None:
    """Cancel then followup() — next turn processes the followup message.

    Given: a running turn with _BlockingTurn.
    When: cancel() is called, then after propagation, followup() is called.
    Then: the followup message is processed in a subsequent turn.
    """
    session_pool = SessionPool(mock_pool)
    await session_pool.start()
    session_id = "sess-cancel-followup"
    await session_pool.create_session(session_id, agent_name="test-agent")

    agent = _make_cancel_aware_agent()
    await _attach_agent(session_pool, session_id, agent)
    queue = await session_pool.event_bus.subscribe(session_id)

    # Start a blocking turn
    first_handle = await session_pool.receive_request(session_id, "first prompt")
    assert first_handle is not None
    await asyncio.sleep(0.1)

    # Cancel the active turn
    session_pool.sessions.cancel_run_for_session(session_id)

    # Wait for cancellation to propagate (RunFailedEvent published, queue cleared)
    await asyncio.sleep(0.3)

    # Drain events from the cancelled turn
    pre_events = await _drain_queue(queue)
    pre_types = [type(_unwrap_event(e)) for e in pre_events]
    assert RunFailedEvent in pre_types, (
        f"Expected RunFailedEvent from cancelled turn, got: {pre_types}"
    )

    # Now call followup — this should queue a message for the next turn
    followup_result = first_handle.followup("followup message")
    assert followup_result is True, "followup() should return True (message queued)"

    # Collect events — should see RunStartedEvent and StreamCompleteEvent
    # from the turn processing the followup
    post_events = await _collect_events_until(queue, StreamCompleteEvent, timeout=30.0)
    post_types = [type(_unwrap_event(e)) for e in post_events]
    assert RunStartedEvent in post_types, (
        f"Expected RunStartedEvent for followup turn, got: {post_types}"
    )
    assert StreamCompleteEvent in post_types, (
        f"Expected StreamCompleteEvent for followup turn, got: {post_types}"
    )

    first_handle.close()
    await asyncio.sleep(0.1)
    await session_pool.shutdown()


@pytest.mark.anyio
async def test_double_cancel_then_new_prompt(mock_pool: MagicMock) -> None:
    """Double cancel then new prompt — no hang, new prompt processed.

    Given: a running turn with _BlockingTurn.
    When: cancel() is called twice, then a new prompt is sent via receive_request().
    Then: no hang, new prompt is processed (StreamCompleteEvent published).
    """
    session_pool = SessionPool(mock_pool)
    await session_pool.start()
    session_id = "sess-double-cancel-prompt"
    await session_pool.create_session(session_id, agent_name="test-agent")

    agent = _make_cancel_aware_agent()
    await _attach_agent(session_pool, session_id, agent)
    queue = await session_pool.event_bus.subscribe(session_id)

    # Start a blocking turn
    first_handle = await session_pool.receive_request(session_id, "first prompt")
    assert first_handle is not None
    await asyncio.sleep(0.1)

    # Double cancel
    session_pool.sessions.cancel_run_for_session(session_id)
    session_pool.sessions.cancel_run_for_session(session_id)

    # Wait for cancellation to propagate
    await asyncio.sleep(0.2)

    # Drain events from cancelled turn
    pre_events = await _drain_queue(queue)
    pre_types = [type(_unwrap_event(e)) for e in pre_events]
    assert RunFailedEvent in pre_types, (
        f"Expected RunFailedEvent from cancelled turn, got: {pre_types}"
    )

    # Send a new prompt — should not hang
    second_handle = await asyncio.wait_for(
        session_pool.receive_request(session_id, "second prompt"),
        timeout=30.0,
    )

    # Collect events — should see StreamCompleteEvent from the new turn
    post_events = await _collect_events_until(queue, StreamCompleteEvent, timeout=30.0)
    post_types = [type(_unwrap_event(e)) for e in post_events]
    assert StreamCompleteEvent in post_types, (
        f"Expected StreamCompleteEvent from new prompt, got: {post_types}"
    )

    first_handle.close()
    await asyncio.sleep(0.1)
    await session_pool.shutdown()


@pytest.mark.anyio
async def test_runhandle_dies_in_idle_loop(mock_pool: MagicMock) -> None:
    """Simulate unrecoverable error in start() — finally block sets events, cleanup clears current_run_id.

    Given: an agent whose second create_turn call raises RuntimeError.
    When: the first turn completes, followup triggers the second create_turn which raises.
    Then: finally block sets complete_event, _cleanup_run clears current_run_id,
          next receive_request creates a new RunHandle and processes the prompt.
    """
    session_pool = SessionPool(mock_pool)
    await session_pool.start()
    session_id = "sess-dies-in-idle"
    await session_pool.create_session(session_id, agent_name="test-agent")

    agent = _make_stub_then_die_agent()
    await _attach_agent(session_pool, session_id, agent)
    queue = await session_pool.event_bus.subscribe(session_id)

    # Start first turn — _StubTurn completes immediately
    first_handle = await session_pool.receive_request(session_id, "first prompt")
    assert first_handle is not None

    # Wait for first turn to complete
    await _collect_events_until(queue, StreamCompleteEvent)
    await asyncio.sleep(0.1)

    # Trigger the second create_turn (which raises) via followup
    first_handle.followup("trigger error")

    # Wait for the error to propagate and cleanup to happen
    await asyncio.sleep(0.5)

    # Verify finally block set events
    assert first_handle.complete_event.is_set(), (
        "complete_event should be set by finally block after error"
    )
    assert first_handle._status == RunStatus.done, (
        f"RunHandle should be done after error, got: {first_handle._status}"
    )

    # Verify _cleanup_run cleared current_run_id
    session = session_pool.sessions.get_session(session_id)
    assert session is not None
    assert session.current_run_id is None, (
        "current_run_id should be cleared by _cleanup_run after error"
    )

    # Next receive_request should create a new RunHandle
    second_handle = await asyncio.wait_for(
        session_pool.receive_request(session_id, "new prompt after crash"),
        timeout=30.0,
    )
    assert second_handle is not None, (
        "receive_request should return a new RunHandle after cleanup"
    )
    assert second_handle is not first_handle, (
        "New RunHandle should be a different instance"
    )

    # Collect events — should see StreamCompleteEvent from the new turn
    post_events = await _collect_events_until(queue, StreamCompleteEvent, timeout=30.0)
    post_types = [type(_unwrap_event(e)) for e in post_events]
    assert StreamCompleteEvent in post_types, (
        f"Expected StreamCompleteEvent from new RunHandle, got: {post_types}"
    )

    second_handle.close()
    await asyncio.sleep(0.1)
    await session_pool.shutdown()
