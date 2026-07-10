"""Tests for SessionController & SessionPool migration to RunLoop API.

Covers M2 Task 10: SessionController and protocol server migration.
Verifies that receive_request creates RunHandle with ProtocolTrigger
and ProtocolChannel dimensions, steer/followup deliver Feedback to
ProtocolChannel, close_session calls RunLoop.close(), and event
delivery flows through ProtocolChannel → EventBus →
ProtocolEventConsumerMixin.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import (
    RunStartedEvent,
    StreamCompleteEvent,
)
from agentpool.lifecycle import (
    DirectChannel,
    Feedback,
    MemoryJournal,
    ProtocolChannel,
    ProtocolTrigger,
)
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.event_bus import EventBus
from agentpool.orchestrator.run import RunHandle
from agentpool.orchestrator.session_controller import (
    SessionController,
    SessionState,
)
from agentpool.orchestrator.session_pool import SessionPool
from agentpool.orchestrator.turn import Turn


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubTurn(Turn):
    """Minimal Turn implementation for testing."""

    def __init__(
        self,
        *,
        events: list[Any] | None = None,
        message_history: list[Any] | None = None,
        raise_exc: BaseException | None = None,
    ) -> None:
        self._events = events or []
        self._history = message_history or []
        self._raise = raise_exc
        self._final_message = ChatMessage(content="done", role="assistant")

    async def execute(self):  # type: ignore[override]
        if self._raise is not None:
            raise self._raise
        self._message_history = self._history
        for event in self._events:
            yield event


def _stream_complete_event() -> StreamCompleteEvent[Any]:
    return StreamCompleteEvent(message=ChatMessage(content="done", role="assistant"))


def _make_mock_agent() -> MagicMock:
    """Create a mock agent with a stub turn."""
    agent = MagicMock()
    agent.AGENT_TYPE = "native"
    agent.name = "test_agent"
    agent.conversation = MagicMock()
    agent.conversation.get_history.return_value = []
    agent.conversation.add_chat_messages = MagicMock()
    agent.create_turn = MagicMock(return_value=_StubTurn(events=[_stream_complete_event()]))
    agent._interrupt = MagicMock(return_value=None)
    return agent


def _make_mock_pool(agent: MagicMock | None = None) -> MagicMock:
    """Create a mock AgentPool for SessionController."""
    pool = MagicMock()
    pool.main_agent_name = "test_agent"
    pool.manifest = MagicMock()
    pool.manifest.agents = {}
    pool.get_context = MagicMock(return_value=None)
    pool._factory = MagicMock()
    if agent is not None:
        pool._factory.create_session_agent = AsyncMock(return_value=agent)
    return pool


# ---------------------------------------------------------------------------
# SessionController._start_run_handle: ProtocolTrigger & ProtocolChannel
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_start_run_handle_creates_protocol_trigger() -> None:
    """_start_run_handle creates a RunHandle with ProtocolTrigger."""
    agent = _make_mock_agent()
    pool = _make_mock_pool(agent)
    controller = SessionController(pool)
    event_bus = EventBus()
    controller._event_bus = event_bus

    session = SessionState(session_id="s1", agent_name="test_agent")
    controller._sessions["s1"] = session
    controller._session_agents["s1"] = agent

    run_handle = controller._start_run_handle(session, agent, "s1", "hello")

    assert isinstance(run_handle._trigger_source, ProtocolTrigger)
    assert run_handle._trigger_source is not None

    # Cleanup
    run_handle.close()
    controller._runs.clear()


@pytest.mark.unit
async def test_start_run_handle_creates_protocol_channel() -> None:
    """_start_run_handle creates a RunHandle with ProtocolChannel."""
    agent = _make_mock_agent()
    pool = _make_mock_pool(agent)
    controller = SessionController(pool)
    event_bus = EventBus()
    controller._event_bus = event_bus

    session = SessionState(session_id="s1", agent_name="test_agent")
    controller._sessions["s1"] = session
    controller._session_agents["s1"] = agent

    run_handle = controller._start_run_handle(session, agent, "s1", "hello")

    assert isinstance(run_handle._comm_channel, ProtocolChannel)
    assert run_handle._comm_channel is not None
    assert run_handle._comm_channel._session_id == "s1"
    assert run_handle._comm_channel._event_bus is event_bus

    # Cleanup
    run_handle.close()
    controller._runs.clear()


@pytest.mark.unit
async def test_start_run_handle_protocol_channel_has_journal() -> None:
    """The ProtocolChannel has a MemoryJournal injected."""
    agent = _make_mock_agent()
    pool = _make_mock_pool(agent)
    controller = SessionController(pool)
    event_bus = EventBus()
    controller._event_bus = event_bus

    session = SessionState(session_id="s1", agent_name="test_agent")
    controller._sessions["s1"] = session
    controller._session_agents["s1"] = agent

    run_handle = controller._start_run_handle(session, agent, "s1", "hello")

    assert isinstance(run_handle._comm_channel, ProtocolChannel)
    assert isinstance(run_handle._comm_channel._journal, MemoryJournal)

    # Cleanup
    run_handle.close()
    controller._runs.clear()


# ---------------------------------------------------------------------------
# Steer / followup via ProtocolChannel
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_steer_delivers_feedback_to_protocol_channel() -> None:
    """steer() delivers Feedback(is_steer=True) to ProtocolChannel."""
    agent = _make_mock_agent()
    pool = _make_mock_pool(agent)
    controller = SessionController(pool)
    event_bus = EventBus()
    controller._event_bus = event_bus

    session = SessionState(session_id="s1", agent_name="test_agent")
    controller._sessions["s1"] = session
    controller._session_agents["s1"] = agent

    run_handle = controller._start_run_handle(session, agent, "s1", "hello")

    # Steer should deliver feedback to the ProtocolChannel.
    result = run_handle.steer("change direction")
    assert result is True

    # Verify feedback was enqueued.
    assert isinstance(run_handle._comm_channel, ProtocolChannel)
    feedback = run_handle._comm_channel.recv()
    assert feedback is not None
    assert isinstance(feedback, Feedback)
    assert feedback.content == "change direction"
    assert feedback.is_steer is True

    # Cleanup
    run_handle.close()
    controller._runs.clear()


@pytest.mark.unit
async def test_followup_delivers_feedback_to_protocol_channel() -> None:
    """followup() delivers Feedback(is_steer=False) to ProtocolChannel."""
    agent = _make_mock_agent()
    pool = _make_mock_pool(agent)
    controller = SessionController(pool)
    event_bus = EventBus()
    controller._event_bus = event_bus

    session = SessionState(session_id="s1", agent_name="test_agent")
    controller._sessions["s1"] = session
    controller._session_agents["s1"] = agent

    run_handle = controller._start_run_handle(session, agent, "s1", "hello")

    result = run_handle.followup("next question")
    assert result is True

    assert isinstance(run_handle._comm_channel, ProtocolChannel)
    feedback = run_handle._comm_channel.recv()
    assert feedback is not None
    assert isinstance(feedback, Feedback)
    assert feedback.content == "next question"
    assert feedback.is_steer is False

    # Cleanup
    run_handle.close()
    controller._runs.clear()


# ---------------------------------------------------------------------------
# close_session calls RunLoop.close()
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_close_session_calls_run_handle_close() -> None:
    """close_session() signals RunHandle.close() via the run-turn path."""
    agent = _make_mock_agent()
    pool = _make_mock_pool(agent)
    controller = SessionController(pool)
    event_bus = EventBus()
    controller._event_bus = event_bus

    session = SessionState(session_id="s1", agent_name="test_agent")
    controller._sessions["s1"] = session
    controller._session_agents["s1"] = agent
    controller._session_scopes["s1"] = anyio.CancelScope()

    # Create a run handle but don't start its background task.
    run_handle = controller._start_run_handle(session, agent, "s1", "hello")

    # Give the background task a moment to start.
    await asyncio.sleep(0.01)

    # Close the session.
    await controller.close_session("s1")

    # The run handle should have been signaled to close (either
    # _closing=True if still running, or _closed=True if the start()
    # loop already finished and the finally block ran).
    assert run_handle._closing is True or run_handle._closed is True


# ---------------------------------------------------------------------------
# Event delivery: ProtocolChannel → EventBus
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_protocol_channel_publishes_to_event_bus() -> None:
    """ProtocolChannel.publish() delivers events to EventBus."""
    event_bus = EventBus()
    journal = MemoryJournal()
    channel = ProtocolChannel(journal=journal, event_bus=event_bus, session_id="s1")

    # Subscribe to the EventBus.
    queue = await event_bus.subscribe("s1", scope="session")

    # Publish an event via ProtocolChannel.
    event = RunStartedEvent(
        run_id="r1",
        session_id="s1",
        agent_name="test_agent",
    )
    await channel.publish(event)

    # The event should arrive on the EventBus subscription.
    envelope = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert envelope.event is event

    await event_bus.unsubscribe("s1", queue)
    channel.close()


# ---------------------------------------------------------------------------
# No double-publish when ProtocolChannel is used
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_no_double_publish_with_protocol_channel() -> None:
    """When ProtocolChannel is the comm_channel, events are not double-published to EventBus.

    The _channel_publishes_to_event_bus property returns True for
    ProtocolChannel, so start() skips the direct event_bus.publish()
    call and lets ProtocolChannel.publish() handle EventBus delivery.
    """
    event_bus = EventBus()
    journal = MemoryJournal()
    channel = ProtocolChannel(journal=journal, event_bus=event_bus, session_id="s1")

    # Create a RunHandle with ProtocolChannel.
    agent = _make_mock_agent()
    session = MagicMock()
    session.turn_lock = asyncio.Lock()
    session.parent_session_id = None
    session.input_provider = None

    run_handle = RunHandle(
        run_id="r1",
        session_id="s1",
        agent_type="native",
        agent=agent,
        event_bus=event_bus,
        session=session,
        run_ctx=AgentRunContext(session_id="s1", event_bus=event_bus),
        _comm_channel=channel,
        _trigger_source=ProtocolTrigger(),
    )

    # The property should return True.
    assert run_handle._channel_publishes_to_event_bus is True

    # Cleanup
    run_handle.close()


@pytest.mark.unit
async def test_double_publish_with_direct_channel() -> None:
    """With DirectChannel, _channel_publishes_to_event_bus is False.

    DirectChannel does not publish to EventBus, so start() must
    call event_bus.publish() directly.
    """
    event_bus = EventBus()
    journal = MemoryJournal()
    channel = DirectChannel(journal=journal)

    agent = _make_mock_agent()
    session = MagicMock()
    session.turn_lock = asyncio.Lock()
    session.parent_session_id = None
    session.input_provider = None

    run_handle = RunHandle(
        run_id="r1",
        session_id="s1",
        agent_type="native",
        agent=agent,
        event_bus=event_bus,
        session=session,
        run_ctx=AgentRunContext(session_id="s1", event_bus=event_bus),
        _comm_channel=channel,
    )

    # The property should return False.
    assert run_handle._channel_publishes_to_event_bus is False

    # Cleanup
    run_handle.close()


# ---------------------------------------------------------------------------
# receive_request on closed session
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_receive_request_closed_session_returns_none() -> None:
    """receive_request on a closing session returns None."""
    agent = _make_mock_agent()
    pool = _make_mock_pool(agent)
    controller = SessionController(pool)
    event_bus = EventBus()
    controller._event_bus = event_bus

    session = SessionState(session_id="s1", agent_name="test_agent")
    session.is_closing = True
    controller._sessions["s1"] = session
    controller._session_agents["s1"] = agent

    result = await controller.receive_request("s1", "hello")
    assert result is None


# ---------------------------------------------------------------------------
# SessionPool._create_run_handle with ProtocolTrigger/ProtocolChannel
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_session_pool_create_run_handle_creates_protocol_dimensions() -> None:
    """SessionPool._create_run_handle creates RunHandle with ProtocolTrigger/ProtocolChannel."""
    agent = _make_mock_agent()
    pool = _make_mock_pool(agent)

    # Create SessionPool with minimal setup.
    session_pool = SessionPool(pool, enable_event_bus=True)
    session_pool.sessions._event_bus = session_pool._event_bus

    session = SessionState(session_id="s1", agent_name="test_agent")
    session_pool.sessions._sessions["s1"] = session
    session_pool.sessions._session_agents["s1"] = agent

    run_handle = session_pool._create_run_handle(session, agent, "s1")

    assert isinstance(run_handle._trigger_source, ProtocolTrigger)
    assert isinstance(run_handle._comm_channel, ProtocolChannel)
    assert run_handle._comm_channel._session_id == "s1"
    assert run_handle._comm_channel._event_bus is session_pool._event_bus

    # Cleanup
    run_handle.close()
    session_pool.sessions._runs.clear()


# ---------------------------------------------------------------------------
# Event delivery end-to-end through ProtocolChannel → EventBus
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_event_delivery_through_protocol_channel_to_event_bus() -> None:
    """Events published via ProtocolChannel arrive on EventBus subscriptions.

    This verifies the event delivery path that protocol servers rely on:
    ProtocolChannel.publish() → EventBus → ProtocolEventConsumerMixin.
    """
    event_bus = EventBus()

    # Subscribe BEFORE publishing to avoid race conditions.
    queue = await event_bus.subscribe("s1", scope="session")

    journal = MemoryJournal()
    channel = ProtocolChannel(journal=journal, event_bus=event_bus, session_id="s1")

    # Publish multiple events. StateUpdate is intentionally excluded
    # because ProtocolChannel filters StateUpdate from EventBus
    # (they are internal lifecycle signals, not turn events).
    events = [
        RunStartedEvent(run_id="r1", session_id="s1", agent_name="test"),
        StreamCompleteEvent(message=ChatMessage(content="done", role="assistant")),
    ]

    for event in events:
        await channel.publish(event)

    # Verify all events arrive in order.
    received: list[Any] = []
    for _ in range(len(events)):
        envelope = await asyncio.wait_for(queue.get(), timeout=1.0)
        received.append(envelope.event)

    assert len(received) == len(events)
    assert isinstance(received[0], RunStartedEvent)
    assert isinstance(received[1], StreamCompleteEvent)

    await event_bus.unsubscribe("s1", queue)
    channel.close()


# ---------------------------------------------------------------------------
# Steer on closed RunHandle raises RuntimeError
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_steer_after_close_raises_runtime_error() -> None:
    """steer() after start() has finished (closed) raises RuntimeError."""
    event_bus = EventBus()
    journal = MemoryJournal()
    channel = ProtocolChannel(journal=journal, event_bus=event_bus, session_id="s1")

    agent = _make_mock_agent()
    session = MagicMock()
    session.turn_lock = asyncio.Lock()
    session.parent_session_id = None
    session.input_provider = None

    run_handle = RunHandle(
        run_id="r1",
        session_id="s1",
        agent_type="native",
        agent=agent,
        event_bus=event_bus,
        session=session,
        run_ctx=AgentRunContext(session_id="s1", event_bus=event_bus),
        _comm_channel=channel,
        _trigger_source=ProtocolTrigger(),
    )

    # Simulate closed state.
    run_handle._closed = True

    with pytest.raises(RuntimeError, match="Cannot steer after close"):
        run_handle.steer("message")


# ---------------------------------------------------------------------------
# close_session with active run waits then completes
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_close_session_with_active_run_completes() -> None:
    """close_session with an active run handle completes gracefully."""
    agent = _make_mock_agent()
    pool = _make_mock_pool(agent)
    controller = SessionController(pool)
    event_bus = EventBus()
    controller._event_bus = event_bus

    session = SessionState(session_id="s1", agent_name="test_agent")
    controller._sessions["s1"] = session
    controller._session_agents["s1"] = agent
    controller._session_scopes["s1"] = anyio.CancelScope()

    # Create run handle.
    _run_handle = controller._start_run_handle(session, agent, "s1", "hello")

    # Give background task a moment.
    await asyncio.sleep(0.01)

    # Close session — should not hang.
    await asyncio.wait_for(
        controller.close_session("s1"),
        timeout=15.0,
    )

    # Session should be removed.
    assert controller.get_session("s1") is None
