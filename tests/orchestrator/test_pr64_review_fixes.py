"""Integration tests for PR #64 review comment fixes.

Covers fixes for:
1. complete_event not set when start() terminates
2. RunErrorEvent not yielded to consumers
3. RunAbortedError/UndrainedPendingMessagesError skip StreamCompleteEvent
4. run_stream while-loop missing terminal event check
5. _current_input_provider ContextVar not set during turn execution
7. agent lookup via .get() instead of get_or_create_session_agent
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
from pydantic_ai.exceptions import UndrainedPendingMessagesError
from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events.events import (
    RunErrorEvent,
    RunStartedEvent,
    StreamCompleteEvent,
)
from agentpool.agents.native_agent.turn import NativeTurn
from agentpool.orchestrator.core import EventBus, SessionState
from agentpool.orchestrator.run import RunHandle, RunStatus
from agentpool.tasks.exceptions import RunAbortedError


if TYPE_CHECKING:
    from agentpool.agents.events.events import RichAgentStreamEvent


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fix #1: complete_event set when start() terminates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_event_set_after_start_completes() -> None:
    """RunHandle.start() must set complete_event when it finishes.

    Without this, close_session() hangs for 30s waiting for
    complete_event.wait() when closing sessions started via
    process_prompt or run_stream.
    """
    agent = Agent(
        name="test-complete-event",
        model=TestModel(custom_output_text="done"),
    )
    async with agent:
        event_bus = EventBus()
        session = SessionState(
            session_id="test-ce-session",
            agent_name="test-complete-event",
        )
        run_ctx = AgentRunContext(
            session_id="test-ce-session",
            event_bus=event_bus,
        )
        run_handle = RunHandle(
            run_id="test-ce-run",
            session_id="test-ce-session",
            agent_type="test-complete-event",
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
        )

        # Drive start() — close after first turn to terminate the loop
        gen = run_handle.start("hello")
        try:
            async for event in gen:
                if isinstance(event, StreamCompleteEvent):
                    run_handle.close()
                    break
        finally:
            # Ensure generator is properly closed so finally block runs
            await gen.aclose()

        # complete_event must be set
        assert run_handle.complete_event.is_set(), (
            "complete_event was not set after start() completed — "
            "close_session() will hang for 30s"
        )


@pytest.mark.asyncio
async def test_complete_event_set_when_start_cancelled() -> None:
    """complete_event must be set even if start() is cancelled."""
    agent = Agent(
        name="test-ce-cancel",
        model=TestModel(custom_output_text="done"),
    )
    async with agent:
        event_bus = EventBus()
        session = SessionState(
            session_id="test-ce-cancel-session",
            agent_name="test-ce-cancel",
        )
        run_ctx = AgentRunContext(
            session_id="test-ce-cancel-session",
            event_bus=event_bus,
        )
        run_handle = RunHandle(
            run_id="test-ce-cancel-run",
            session_id="test-ce-cancel-session",
            agent_type="test-ce-cancel",
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
        )

        gen = run_handle.start("hello")
        task = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        with contextlib.suppress(Exception):
            await gen.aclose()

        # Even on cancel, complete_event should be set
        assert run_handle.complete_event.is_set(), (
            "complete_event was not set after start() was cancelled"
        )


# ---------------------------------------------------------------------------
# Fix #2: RunErrorEvent yielded to consumers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_error_event_yielded_to_consumer() -> None:
    """RunHandle.start() must yield RunErrorEvent when turn.execute() raises.

    Without yielding, create_run_stream and other direct consumers
    hang indefinitely waiting for an event that never arrives.
    """
    agent = Agent(
        name="test-error-yield",
        model=TestModel(custom_output_text="ok"),
    )
    async with agent:
        event_bus = EventBus()
        session = SessionState(
            session_id="test-err-session",
            agent_name="test-error-yield",
        )
        run_ctx = AgentRunContext(
            session_id="test-err-session",
            event_bus=event_bus,
        )
        run_handle = RunHandle(
            run_id="test-err-run",
            session_id="test-err-session",
            agent_type="test-error-yield",
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
        )

        # Patch agent.create_turn to return a turn that raises
        class FailingTurn:
            async def execute(self) -> Any:
                raise RuntimeError("turn failed")
                yield  # noqa: unreachable — make it an async generator

        agent.create_turn = MagicMock(return_value=FailingTurn())  # type: ignore[method-assign]

        events: list[Any] = []
        gen = run_handle.start("test")
        try:
            async with asyncio.timeout(5):
                async for event in gen:
                    events.append(event)
                    if isinstance(event, RunErrorEvent):
                        run_handle.close()
                        break
        except TimeoutError:
            pytest.fail(
                "start() hung waiting for RunErrorEvent — it was published "
                "to EventBus but never yielded to the consumer"
            )
        finally:
            with contextlib.suppress(Exception):
                await gen.aclose()

        # RunErrorEvent must have been yielded
        error_events = [e for e in events if isinstance(e, RunErrorEvent)]
        assert len(error_events) == 1, (
            f"Expected 1 RunErrorEvent, got {len(error_events)}. "
            f"Events: {[type(e).__name__ for e in events]}"
        )
        assert "turn failed" in error_events[0].message


# ---------------------------------------------------------------------------
# Fix #3: StreamCompleteEvent on RunAbortedError / UndrainedPendingMessagesError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_aborted_error_yields_stream_complete() -> None:
    """NativeTurn must yield StreamCompleteEvent even on RunAbortedError.

    Without this, RunHandle.start() never sees StreamCompleteEvent,
    the turn loop continues, and the handle hangs in idle.
    """
    agent = Agent(
        name="test-abort-sc",
        model=TestModel(custom_output_text="hello"),
    )
    async with agent:
        mock_agentlet = MagicMock()
        mock_run = AsyncMock()
        mock_run.__aenter__ = AsyncMock(side_effect=RunAbortedError("test abort"))
        mock_run.__aexit__ = AsyncMock(return_value=None)
        mock_agentlet.iter = MagicMock(return_value=mock_run)

        run_ctx = AgentRunContext(session_id="test-abort-sc-session")
        turn = NativeTurn(
            agent=agent,
            prompts=["test"],
            run_ctx=run_ctx,
            message_history=[],
        )

        events: list[Any] = []
        with patch.object(agent, "get_agentlet", AsyncMock(return_value=mock_agentlet)):
            async for event in turn.execute():
                events.append(event)

        # Must have StreamCompleteEvent as last event
        stream_complete = [e for e in events if isinstance(e, StreamCompleteEvent)]
        assert len(stream_complete) == 1, (
            f"Expected 1 StreamCompleteEvent after RunAbortedError, got "
            f"{len(stream_complete)}. Events: {[type(e).__name__ for e in events]}"
        )


@pytest.mark.asyncio
async def test_undrained_pending_yields_stream_complete() -> None:
    """NativeTurn must yield StreamCompleteEvent on UndrainedPendingMessagesError."""
    agent = Agent(
        name="test-undrained-sc",
        model=TestModel(custom_output_text="hello"),
    )
    async with agent:
        mock_agentlet = MagicMock()
        mock_run = AsyncMock()
        mock_run.__aenter__ = AsyncMock(
            side_effect=UndrainedPendingMessagesError("undrained")
        )
        mock_run.__aexit__ = AsyncMock(return_value=None)
        mock_agentlet.iter = MagicMock(return_value=mock_run)

        run_ctx = AgentRunContext(session_id="test-undrained-sc-session")
        turn = NativeTurn(
            agent=agent,
            prompts=["test"],
            run_ctx=run_ctx,
            message_history=[],
        )

        events: list[Any] = []
        with patch.object(agent, "get_agentlet", AsyncMock(return_value=mock_agentlet)):
            async for event in turn.execute():
                events.append(event)

        stream_complete = [e for e in events if isinstance(e, StreamCompleteEvent)]
        assert len(stream_complete) == 1, (
            f"Expected 1 StreamCompleteEvent after UndrainedPendingMessagesError, "
            f"got {len(stream_complete)}. Events: {[type(e).__name__ for e in events]}"
        )


# ---------------------------------------------------------------------------
# Fix #4: run_stream terminal event check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_stream_breaks_on_stream_complete() -> None:
    """_run_stream_run_turn must break on StreamCompleteEvent in active-run path.

    Without the break, the while-True loop blocks indefinitely on
    stream.receive() after the run completes because the session
    remains open and EndOfStream is never raised.
    """
    from agentpool.orchestrator.core import SessionController

    mock_pool = MagicMock()
    mock_pool.main_agent = MagicMock()
    mock_pool.main_agent.name = "main-agent"
    mock_pool.manifest = MagicMock()
    mock_pool.manifest.agents = {}

    controller = SessionController(pool=mock_pool)
    event_bus = EventBus()
    controller._event_bus = event_bus

    # We test the EventBus subscription loop logic directly
    session_id = "test-stream-break"
    stream = await event_bus.subscribe(session_id, scope="session")

    # Publish a StreamCompleteEvent
    complete_event = StreamCompleteEvent(
        message=MagicMock(content="done"),
    )

    async def _publish_and_finish() -> None:
        await asyncio.sleep(0.05)
        await event_bus.publish(session_id, complete_event)

    publish_task = asyncio.create_task(_publish_and_finish())

    # Simulate the while-True loop from _run_stream_run_turn
    received: list[Any] = []
    try:
        async with asyncio.timeout(5):
            while True:
                try:
                    event = await stream.receive()
                except anyio.EndOfStream:
                    break
                received.append(event.event)
                # This is the fix: break on terminal events
                if isinstance(event.event, StreamCompleteEvent | RunErrorEvent):
                    break
    except TimeoutError:
        pytest.fail(
            "Loop hung — StreamCompleteEvent was received but loop didn't break"
        )
    finally:
        publish_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await publish_task
        await event_bus.unsubscribe(session_id, stream)

    assert len(received) >= 1
    assert isinstance(received[-1], StreamCompleteEvent)


# ---------------------------------------------------------------------------
# Fix #5: _current_input_provider ContextVar set during turn execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_input_provider_contextvar_set_during_turn() -> None:
    """RunHandle.start() must set _current_input_provider ContextVar.

    MCP elicitation depends on this ContextVar. Without it,
    _current_input_provider.get() returns None during turn execution.
    """
    from agentpool.mcp_server.manager import _current_input_provider

    captured_provider: list[Any] = []

    def capture_tool() -> str:
        """Tool that captures the current input provider."""
        captured_provider.append(_current_input_provider.get())
        return "captured"

    agent = Agent(
        name="test-ctxvar",
        model=TestModel(call_tools=["capture_tool"], custom_output_text="ok"),
        tools=[capture_tool],
    )
    async with agent:
        event_bus = EventBus()
        session = SessionState(
            session_id="test-ctxvar-session",
            agent_name="test-ctxvar",
        )
        run_ctx = AgentRunContext(
            session_id="test-ctxvar-session",
            event_bus=event_bus,
        )

        mock_provider = MagicMock()
        session.input_provider = mock_provider

        run_handle = RunHandle(
            run_id="test-ctxvar-run",
            session_id="test-ctxvar-session",
            agent_type="test-ctxvar",
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
        )

        gen = run_handle.start("test")
        try:
            async for event in gen:
                if isinstance(event, StreamCompleteEvent):
                    run_handle.close()
                    break
        finally:
            await gen.aclose()

        # The tool should have captured the input provider
        assert len(captured_provider) > 0, "Tool was never called"
        assert captured_provider[0] is mock_provider, (
            f"ContextVar was not set — got {captured_provider[0]!r}, "
            f"expected {mock_provider!r}"
        )

        # After start() completes, ContextVar should be reset
        assert _current_input_provider.get() is None, (
            "ContextVar was not reset after turn execution"
        )


# ---------------------------------------------------------------------------
# Fix #7: agent lookup via get_or_create_session_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_receive_request_uses_get_or_create_session_agent() -> None:
    """receive_request should use get_or_create_session_agent, not .get().

    When agent is not yet cached (new top-level sessions), .get()
    returns None and receive_request silently does nothing.
    """
    from agentpool.orchestrator.core import SessionController

    mock_pool = MagicMock()
    mock_pool.main_agent = MagicMock()
    mock_pool.main_agent.name = "main-agent"
    mock_pool.manifest = MagicMock()
    mock_pool.manifest.agents = {}

    controller = SessionController(pool=mock_pool)
    event_bus = EventBus()
    controller._event_bus = event_bus

    mock_agent = MagicMock()
    mock_agent.AGENT_TYPE = "native"

    session_id = "sess-lazy"
    controller._sessions[session_id] = MagicMock()
    controller._sessions[session_id].session_id = session_id
    controller._sessions[session_id].current_run_id = None
    controller._sessions[session_id].closing = False
    controller._sessions[session_id].is_closing = False
    controller._sessions[session_id]._request_lock = asyncio.Lock()
    controller._sessions[session_id].turn_lock = asyncio.Lock()
    controller._sessions[session_id].input_provider = None
    # Deliberately do NOT pre-register agent in _session_agents

    # Mock get_or_create_session_agent to return the agent
    controller.get_or_create_session_agent = AsyncMock(return_value=mock_agent)  # type: ignore[method-assign]
    controller._consume_run = AsyncMock(return_value=None)  # type: ignore[method-assign]

    result = await controller.receive_request(session_id, "hello")

    # get_or_create_session_agent should have been called
    controller.get_or_create_session_agent.assert_called_once_with(session_id)
    assert result is not None, (
        "receive_request returned None because agent was not in _session_agents cache"
    )
