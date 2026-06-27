"""Tests for BaseAgent.create_run() and create_run_stream() v2 methods."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai.models.test import TestModel
import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunStartedEvent, StreamCompleteEvent
from agentpool.agents.native_agent.agent import Agent
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.run import RunHandle, RunStatus
from agentpool.orchestrator.turn import Turn


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubTurn(Turn):
    """Minimal Turn implementation that yields a fixed event sequence."""

    def __init__(
        self,
        *,
        events: list[Any] | None = None,
        message_history: list[Any] | None = None,
    ) -> None:
        self._events = events or []
        self._history = message_history or []

    async def execute(self):  # type: ignore[override]
        # Set history before yielding so it's available even if
        # the consumer breaks on StreamCompleteEvent.
        self._message_history = self._history
        self._final_message = ChatMessage(content="done", role="assistant")
        for event in self._events:
            yield event


def _stream_complete_event() -> StreamCompleteEvent[Any]:
    return StreamCompleteEvent(message=ChatMessage(content="done", role="assistant"))


def _make_session() -> Any:
    """Create a mock SessionState with a real turn_lock."""
    session = MagicMock()
    session.turn_lock = asyncio.Lock()
    return session


def _make_agent_with_stub_turn(
    events: list[Any],
    history: list[Any] | None = None,
) -> Agent:
    """Create a real Agent whose create_turn returns a _StubTurn.

    This lets us test create_run/create_run_stream without running
    the actual pydantic-ai agent loop.
    """
    agent = Agent(model=TestModel(), name="test_agent")
    stub = _StubTurn(events=events, message_history=history or [])
    agent.create_turn = MagicMock(return_value=stub)  # type: ignore[method-assign]
    return agent


# ---------------------------------------------------------------------------
# create_run() tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_create_run_returns_run_handle_without_executing() -> None:
    """Given an Agent, create_run() returns a RunHandle in idle status.

    No execution should happen — the handle is ready to be started
    via start() but has not begun any turn.
    """
    agent = Agent(model=TestModel(), name="test_agent")
    run_ctx = AgentRunContext(session_id="sess-1", run_id="run-1")
    event_bus = AsyncMock()
    session = _make_session()

    handle = agent.create_run(
        prompt="Hello",
        run_ctx=run_ctx,
        message_history=[],
        event_bus=event_bus,
        session=session,
    )

    assert isinstance(handle, RunHandle)
    assert handle._status == RunStatus.idle
    assert handle._closing is False


@pytest.mark.unit
async def test_create_run_handle_fields_correctly_set() -> None:
    """Given an Agent with specific run_ctx, create_run() wires all fields."""
    agent = Agent(model=TestModel(), name="test_agent")
    run_ctx = AgentRunContext(session_id="sess-42", run_id="run-42")
    event_bus = AsyncMock()
    session = _make_session()
    history: list[Any] = ["msg1", "msg2"]

    handle = agent.create_run(
        prompt="Hello",
        run_ctx=run_ctx,
        message_history=history,
        event_bus=event_bus,
        session=session,
    )

    assert handle.agent is agent
    assert handle.event_bus is event_bus
    assert handle.session is session
    assert handle.run_ctx is run_ctx
    assert handle.run_id == "run-42"
    assert handle.session_id == "sess-42"
    assert handle.agent_type == "native"
    assert handle._message_history == ["msg1", "msg2"]


@pytest.mark.unit
async def test_create_run_does_not_call_create_turn() -> None:
    """Given create_run() is called, create_turn() is never invoked.

    This verifies that construction does not trigger execution.
    """
    agent = Agent(model=TestModel(), name="test_agent")
    create_turn_mock = MagicMock(return_value=_StubTurn(events=[]))
    agent.create_turn = create_turn_mock  # type: ignore[method-assign]

    run_ctx = AgentRunContext()
    event_bus = AsyncMock()
    session = _make_session()

    agent.create_run(
        prompt="Hello",
        run_ctx=run_ctx,
        message_history=[],
        event_bus=event_bus,
        session=session,
    )

    create_turn_mock.assert_not_called()


# ---------------------------------------------------------------------------
# create_run_stream() tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_create_run_stream_yields_events_and_closes() -> None:
    """Given a stubbed agent, create_run_stream() yields all events.

    The stream should yield RunStartedEvent followed by
    StreamCompleteEvent, then terminate.
    """
    events = [
        RunStartedEvent(run_id="r1", session_id="s1", agent_name="test"),
        _stream_complete_event(),
    ]
    agent = _make_agent_with_stub_turn(events=events, history=["m1"])

    run_ctx = AgentRunContext(session_id="s1", run_id="r1")
    event_bus = AsyncMock()
    session = _make_session()

    yielded = [
        event
        async for event in agent.create_run_stream(
            prompt="Hello",
            run_ctx=run_ctx,
            message_history=[],
            event_bus=event_bus,
            session=session,
        )
    ]

    assert len(yielded) == 2
    assert isinstance(yielded[0], RunStartedEvent)
    assert isinstance(yielded[1], StreamCompleteEvent)


@pytest.mark.unit
async def test_create_run_stream_closes_handle_after_completion() -> None:
    """Given create_run_stream() completes, the RunHandle is closed.

    The close() call on StreamCompleteEvent sets _closing=True.
    """
    events = [_stream_complete_event()]
    agent = _make_agent_with_stub_turn(events=events, history=["m1"])

    # Capture the RunHandle by wrapping create_run
    captured: list[RunHandle] = []
    original_create_run = agent.create_run

    def _capturing_create_run(*args: Any, **kwargs: Any) -> RunHandle:
        handle = original_create_run(*args, **kwargs)
        captured.append(handle)
        return handle

    agent.create_run = _capturing_create_run  # type: ignore[method-assign]

    run_ctx = AgentRunContext(session_id="s1", run_id="r1")
    event_bus = AsyncMock()
    session = _make_session()

    async for _event in agent.create_run_stream(
        prompt="Hello",
        run_ctx=run_ctx,
        message_history=[],
        event_bus=event_bus,
        session=session,
    ):
        pass

    assert len(captured) == 1
    assert captured[0]._closing is True
