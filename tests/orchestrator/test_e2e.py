"""End-to-end integration tests covering full user session lifecycles (Group 6.13-6.15).

Tests full session lifecycle, multi-agent concurrent handling, and cross-protocol
event passing through the EventBus.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import (
    PartDeltaEvent,
    RunStartedEvent,
    StreamCompleteEvent,
    ToolCallCompleteEvent,
    ToolCallStartEvent,
)
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import EventBus, SessionPool
from pydantic_ai import TextPartDelta


pytestmark = [pytest.mark.integration, pytest.mark.anyio]


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
def mock_agent_full_lifecycle() -> MagicMock:
    """Return a mocked BaseAgent that yields a complete event lifecycle."""
    agent = MagicMock()

    async def _stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[
        RunStartedEvent
        | PartDeltaEvent
        | ToolCallStartEvent
        | ToolCallCompleteEvent
        | StreamCompleteEvent[Any]
    ]:
        session_id = kwargs.get("session_id", "default")
        yield RunStartedEvent(session_id=session_id, run_id="run-1")
        yield PartDeltaEvent.text(index=0, content="Hello")
        yield ToolCallStartEvent(
            tool_call_id="tc-1",
            tool_name="bash",
            title="Running bash command",
        )
        yield ToolCallCompleteEvent(
            tool_name="bash",
            tool_call_id="tc-1",
            tool_input={"command": "echo hi"},
            tool_result="hi",
            agent_name="test-agent",
            message_id="msg-1",
        )
        yield StreamCompleteEvent(
            message=ChatMessage(content="Done", role="assistant"),
        )

    agent._run_stream_once = _stream
    return agent


@pytest.fixture
def mock_agent_with_text(text: str = "response") -> MagicMock:
    """Return a mocked BaseAgent that yields text and completes."""
    agent = MagicMock()

    async def _stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[
        RunStartedEvent | PartDeltaEvent | StreamCompleteEvent[Any]
    ]:
        session_id = kwargs.get("session_id", "default")
        yield RunStartedEvent(session_id=session_id, run_id="run-1")
        yield PartDeltaEvent.text(index=0, content=text)
        yield StreamCompleteEvent(
            message=ChatMessage(content=text, role="assistant"),
        )

    agent._run_stream_once = _stream
    return agent


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


# ---------------------------------------------------------------------------
# 6.13: Full user session lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_full_session_lifecycle_create_prompt_events_close(
    mock_pool: MagicMock,
    mock_agent_full_lifecycle: MagicMock,
) -> None:
    """6.13: create_session → process_prompt → verify events → close_session.

    Verifies that the SessionPool tracks state correctly throughout the
    entire lifecycle and that all expected event types are delivered in
    order via the EventBus.
    """
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    # Create session
    state = await session_pool.create_session("sess-lifecycle", agent_name="agent-a")
    assert state.session_id == "sess-lifecycle"
    assert session_pool.sessions.get_session("sess-lifecycle") is state

    # Attach mock agent so turn can run
    await _attach_agent(session_pool, "sess-lifecycle", mock_agent_full_lifecycle)

    # Subscribe to events before processing
    queue = await session_pool.event_bus.subscribe("sess-lifecycle")

    # Process prompt
    await session_pool.process_prompt("sess-lifecycle", "hello")

    # Collect all events
    events: list[Any] = []
    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=0.5)
            if event is None:
                break
            events.append(event)
        except asyncio.TimeoutError:
            break

    # Verify event ordering and types
    assert len(events) == 5
    assert isinstance(events[0], RunStartedEvent)
    assert events[0].session_id == "sess-lifecycle"
    assert isinstance(events[1], PartDeltaEvent)
    assert isinstance(events[2], ToolCallStartEvent)
    assert events[2].tool_name == "bash"
    assert isinstance(events[3], ToolCallCompleteEvent)
    assert events[3].tool_result == "hi"
    assert isinstance(events[4], StreamCompleteEvent)
    assert events[4].message.content == "Done"

    # Close session
    await session_pool.close_session("sess-lifecycle")
    assert session_pool.sessions.get_session("sess-lifecycle") is None

    # Sentinel should have been sent
    sentinel = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert sentinel is None

    await session_pool.shutdown()


@pytest.mark.anyio
async def test_full_lifecycle_session_state_transitions(
    mock_pool: MagicMock,
    mock_agent_full_lifecycle: MagicMock,
) -> None:
    """6.13: Verify SessionPool state transitions during lifecycle.

    Ensures that session state moves correctly from active to closing to
    closed, and that turn timing metrics are recorded.
    """
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    await session_pool.create_session("sess-state", agent_name="agent-b")
    await _attach_agent(session_pool, "sess-state", mock_agent_full_lifecycle)

    # Pre-run: session exists and is not closing
    pre_state = session_pool.sessions.get_session("sess-state")
    assert pre_state is not None
    assert pre_state.is_closing is False
    assert pre_state.closed_at is None

    # Run turn
    await session_pool.process_prompt("sess-state", "hello")

    # Turn timing should be recorded
    assert len(session_pool.turns._turn_timings) == 1
    start, end = session_pool.turns._turn_timings[0]
    assert end >= start

    # Close session
    await session_pool.close_session("sess-state")

    # Post-close: session removed
    post_state = session_pool.sessions.get_session("sess-state")
    assert post_state is None

    # Turn state cleaned up
    assert "sess-state" not in session_pool.turns._post_turn_injections
    assert "sess-state" not in session_pool.turns._post_turn_prompts

    await session_pool.shutdown()


# ---------------------------------------------------------------------------
# 6.14: Multi-agent concurrent session handling
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_multi_agent_concurrent_sessions_no_contamination(
    mock_pool: MagicMock,
) -> None:
    """6.14: Create 2+ sessions with different agents and process concurrently.

    Verifies that events from different sessions do not cross over and that
    each session receives only its own events.
    """
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    # Create two agents with distinct response text
    agent_a = MagicMock()

    async def _stream_a(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent | PartDeltaEvent | StreamCompleteEvent[Any]]:
        session_id = kwargs.get("session_id", "default")
        yield RunStartedEvent(session_id=session_id, run_id="run-a")
        yield PartDeltaEvent.text(index=0, content="response-from-agent-a")
        yield StreamCompleteEvent(
            message=ChatMessage(content="response-from-agent-a", role="assistant"),
        )

    agent_a._run_stream_once = _stream_a

    agent_b = MagicMock()

    async def _stream_b(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent | PartDeltaEvent | StreamCompleteEvent[Any]]:
        session_id = kwargs.get("session_id", "default")
        yield RunStartedEvent(session_id=session_id, run_id="run-b")
        yield PartDeltaEvent.text(index=0, content="response-from-agent-b")
        yield StreamCompleteEvent(
            message=ChatMessage(content="response-from-agent-b", role="assistant"),
        )

    agent_b._run_stream_once = _stream_b

    # Create sessions and attach different agents
    await session_pool.create_session("sess-a", agent_name="agent-a")
    await session_pool.create_session("sess-b", agent_name="agent-b")
    await _attach_agent(session_pool, "sess-a", agent_a)
    await _attach_agent(session_pool, "sess-b", agent_b)

    # Subscribe to both sessions
    queue_a = await session_pool.event_bus.subscribe("sess-a")
    queue_b = await session_pool.event_bus.subscribe("sess-b")

    # Process prompts concurrently
    await asyncio.gather(
        session_pool.process_prompt("sess-a", "prompt-a"),
        session_pool.process_prompt("sess-b", "prompt-b"),
    )

    # Collect events for session A
    events_a: list[Any] = []
    while not queue_a.empty():
        event = queue_a.get_nowait()
        if event is not None:
            events_a.append(event)

    # Collect events for session B
    events_b: list[Any] = []
    while not queue_b.empty():
        event = queue_b.get_nowait()
        if event is not None:
            events_b.append(event)

    # Verify session A only has agent-a events
    assert len(events_a) == 3
    assert all(
        isinstance(e, (RunStartedEvent, PartDeltaEvent, StreamCompleteEvent))
        for e in events_a
    )
    part_delta_a = events_a[1]
    assert isinstance(part_delta_a, PartDeltaEvent)
    assert isinstance(part_delta_a.delta, TextPartDelta)
    assert part_delta_a.delta.content_delta == "response-from-agent-a"

    # Verify session B only has agent-b events
    assert len(events_b) == 3
    assert all(
        isinstance(e, (RunStartedEvent, PartDeltaEvent, StreamCompleteEvent))
        for e in events_b
    )
    part_delta_b = events_b[1]
    assert isinstance(part_delta_b, PartDeltaEvent)
    assert isinstance(part_delta_b.delta, TextPartDelta)
    assert part_delta_b.delta.content_delta == "response-from-agent-b"

    # Verify no cross-session contamination in SessionController
    assert session_pool.sessions.get_session("sess-a") is not None
    assert session_pool.sessions.get_session("sess-b") is not None

    # Cleanup
    await session_pool.close_session("sess-a")
    await session_pool.close_session("sess-b")
    await session_pool.shutdown()


@pytest.mark.anyio
async def test_concurrent_sessions_turn_serialization_per_session(
    mock_pool: MagicMock,
) -> None:
    """6.14: Turns for the same session serialize; different sessions run concurrently.

    Verifies that per-session turn_lock ensures only one turn per session
    at a time, while different sessions can process in parallel.
    """
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    agent = MagicMock()

    turn_starts: dict[str, list[float]] = {"sess-1": [], "sess-2": []}
    turn_ends: dict[str, list[float]] = {"sess-1": [], "sess-2": []}

    async def _stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        session_id = kwargs.get("session_id", "default")
        start = asyncio.get_event_loop().time()
        turn_starts[session_id].append(start)
        await asyncio.sleep(0.03)
        end = asyncio.get_event_loop().time()
        turn_ends[session_id].append(end)
        yield RunStartedEvent(session_id=session_id, run_id="run-1")

    agent._run_stream_once = _stream

    await session_pool.create_session("sess-1")
    await session_pool.create_session("sess-2")
    await _attach_agent(session_pool, "sess-1", agent)
    await _attach_agent(session_pool, "sess-2", agent)

    # Fire two turns for each session concurrently
    await asyncio.gather(
        session_pool.process_prompt("sess-1", "prompt-1a"),
        session_pool.process_prompt("sess-1", "prompt-1b"),
        session_pool.process_prompt("sess-2", "prompt-2a"),
        session_pool.process_prompt("sess-2", "prompt-2b"),
    )

    # Each session should have exactly 2 turns
    assert len(turn_starts["sess-1"]) == 2
    assert len(turn_starts["sess-2"]) == 2

    # Within each session, turns must not overlap (serialized)
    for sess in ("sess-1", "sess-2"):
        for i in range(len(turn_starts[sess]) - 1):
            assert turn_ends[sess][i] <= turn_starts[sess][i + 1]

    # Across sessions, turns should overlap (concurrent)
    # The first turn of sess-1 and sess-2 should have started near the same time
    assert abs(turn_starts["sess-1"][0] - turn_starts["sess-2"][0]) < 0.02

    await session_pool.close_session("sess-1")
    await session_pool.close_session("sess-2")
    await session_pool.shutdown()


@pytest.mark.anyio
async def test_concurrent_sessions_event_bus_isolation(
    mock_pool: MagicMock,
) -> None:
    """6.14: Per-session event isolation holds under concurrent load.

    Subscribes multiple queues per session and verifies that each
    subscriber receives only events for its own session.
    """
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    agent = MagicMock()

    async def _stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        session_id = kwargs.get("session_id", "default")
        yield RunStartedEvent(session_id=session_id, run_id="run-1")

    agent._run_stream_once = _stream

    await session_pool.create_session("sess-x")
    await session_pool.create_session("sess-y")
    await _attach_agent(session_pool, "sess-x", agent)
    await _attach_agent(session_pool, "sess-y", agent)

    # Multiple subscribers per session
    qx1 = await session_pool.event_bus.subscribe("sess-x")
    qx2 = await session_pool.event_bus.subscribe("sess-x")
    qy1 = await session_pool.event_bus.subscribe("sess-y")
    qy2 = await session_pool.event_bus.subscribe("sess-y")

    # Concurrent processing
    await asyncio.gather(
        session_pool.process_prompt("sess-x", "prompt-x"),
        session_pool.process_prompt("sess-y", "prompt-y"),
    )

    # All subscribers for sess-x should have exactly 1 event
    for q in (qx1, qx2):
        ev = await asyncio.wait_for(q.get(), timeout=0.5)
        assert ev is not None
        assert isinstance(ev, RunStartedEvent)
        assert ev.session_id == "sess-x"
        assert q.empty()

    # All subscribers for sess-y should have exactly 1 event
    for q in (qy1, qy2):
        ev = await asyncio.wait_for(q.get(), timeout=0.5)
        assert ev is not None
        assert isinstance(ev, RunStartedEvent)
        assert ev.session_id == "sess-y"
        assert q.empty()

    await session_pool.close_session("sess-x")
    await session_pool.close_session("sess-y")
    await session_pool.shutdown()


# ---------------------------------------------------------------------------
# 6.15: Cross-protocol event passing via EventBus
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cross_protocol_event_publishing_and_subscribing() -> None:
    """6.15: Simulate ACP handler publishing events; OpenCode handler receives them.

    Creates an EventBus, simulates an ACP protocol handler publishing events,
    and verifies that an OpenCode protocol handler subscribing to the same
    session receives identical events in the correct order.
    """
    event_bus = EventBus(max_queue_size=10)

    # Simulate protocol handlers subscribing
    acp_queue = await event_bus.subscribe("sess-cross")
    opencode_queue = await event_bus.subscribe("sess-cross")

    # Simulate ACP handler publishing events
    events_to_publish: list[Any] = [
        RunStartedEvent(session_id="sess-cross", run_id="run-1"),
        PartDeltaEvent.text(index=0, content="Cross-protocol text"),
        ToolCallStartEvent(
            tool_call_id="tc-cross",
            tool_name="read",
            title="Reading file",
        ),
        ToolCallCompleteEvent(
            tool_name="read",
            tool_call_id="tc-cross",
            tool_input={"path": "/tmp/test.txt"},
            tool_result="file contents",
            agent_name="cross-agent",
            message_id="msg-cross",
        ),
        StreamCompleteEvent(
            message=ChatMessage(content="Cross complete", role="assistant"),
        ),
    ]

    for event in events_to_publish:
        await event_bus.publish("sess-cross", event)

    # Verify ACP subscriber receives all events in order
    acp_received: list[Any] = []
    while not acp_queue.empty():
        acp_received.append(acp_queue.get_nowait())

    # Verify OpenCode subscriber receives all events in order
    opencode_received: list[Any] = []
    while not opencode_queue.empty():
        opencode_received.append(opencode_queue.get_nowait())

    assert len(acp_received) == len(events_to_publish)
    assert len(opencode_received) == len(events_to_publish)

    # Verify event ordering is preserved for both subscribers
    for i, expected in enumerate(events_to_publish):
        assert type(acp_received[i]) is type(expected)
        assert type(opencode_received[i]) is type(expected)

    # Verify shallow copy independence (events are distinct objects)
    for i in range(len(acp_received)):
        assert acp_received[i] is not opencode_received[i]

    await event_bus.close_session("sess-cross")


@pytest.mark.anyio
async def test_cross_protocol_multiple_subscribers_different_protocols() -> None:
    """6.15: Multiple subscribers from different protocols receive events.

    Simulates three protocol handlers (ACP, OpenCode, AG-UI) subscribing to
    the same session and verifies all receive the same events.
    """
    event_bus = EventBus(max_queue_size=10)

    # Simulate three different protocol handlers
    acp_queue = await event_bus.subscribe("sess-multi")
    opencode_queue = await event_bus.subscribe("sess-multi")
    agui_queue = await event_bus.subscribe("sess-multi")

    # Publish a sequence of events
    events_to_publish: list[Any] = [
        RunStartedEvent(session_id="sess-multi", run_id="run-multi"),
        PartDeltaEvent.text(index=0, content="Multi-protocol message"),
        StreamCompleteEvent(
            message=ChatMessage(content="Done", role="assistant"),
        ),
    ]

    for event in events_to_publish:
        await event_bus.publish("sess-multi", event)

    # Verify all three subscribers receive the events
    acp_received: list[Any] = []
    opencode_received: list[Any] = []
    agui_received: list[Any] = []

    while not acp_queue.empty():
        acp_received.append(acp_queue.get_nowait())
    while not opencode_queue.empty():
        opencode_received.append(opencode_queue.get_nowait())
    while not agui_queue.empty():
        agui_received.append(agui_queue.get_nowait())

    assert len(acp_received) == 3
    assert len(opencode_received) == 3
    assert len(agui_received) == 3

    # Verify all subscribers see the same event types in order
    for i, expected in enumerate(events_to_publish):
        assert type(acp_received[i]) is type(expected)
        assert type(opencode_received[i]) is type(expected)
        assert type(agui_received[i]) is type(expected)

    # All received events should be independent shallow copies
    all_events = acp_received + opencode_received + agui_received
    for i in range(len(all_events)):
        for j in range(i + 1, len(all_events)):
            assert all_events[i] is not all_events[j]

    await event_bus.close_session("sess-multi")


@pytest.mark.anyio
async def test_cross_protocol_event_ordering_preserved_under_load() -> None:
    """6.15: Event ordering is preserved when publishing many events rapidly.

    Publishes a large number of events in a known order and verifies that
    all subscribers receive them in the exact same sequence.
    """
    event_bus = EventBus(max_queue_size=100)

    protocol_a = await event_bus.subscribe("sess-order")
    protocol_b = await event_bus.subscribe("sess-order")

    # Publish 20 ordered events
    event_count = 20
    for i in range(event_count):
        await event_bus.publish(
            "sess-order",
            PartDeltaEvent.text(index=i, content=f"msg-{i}"),
        )

    # Collect events for both subscribers
    received_a: list[Any] = []
    received_b: list[Any] = []
    while not protocol_a.empty():
        received_a.append(protocol_a.get_nowait())
    while not protocol_b.empty():
        received_b.append(protocol_b.get_nowait())

    assert len(received_a) == event_count
    assert len(received_b) == event_count

    # Verify strict ordering
    for i in range(event_count):
        ev_a = received_a[i]
        ev_b = received_b[i]
        assert isinstance(ev_a, PartDeltaEvent)
        assert isinstance(ev_b, PartDeltaEvent)
        assert isinstance(ev_a.delta, TextPartDelta)
        assert isinstance(ev_b.delta, TextPartDelta)
        assert ev_a.delta.content_delta == f"msg-{i}"
        assert ev_b.delta.content_delta == f"msg-{i}"

    await event_bus.close_session("sess-order")


@pytest.mark.anyio
async def test_cross_protocol_with_session_pool_integration(
    mock_pool: MagicMock,
    mock_agent_full_lifecycle: MagicMock,
) -> None:
    """6.15: Full integration test: SessionPool + EventBus with protocol subscribers.

    Simulates ACP and OpenCode handlers subscribing to a SessionPool's
    EventBus, runs a full turn, and verifies both protocols receive the
    complete event stream in order.
    """
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    await session_pool.create_session("sess-integrated", agent_name="agent-a")
    await _attach_agent(session_pool, "sess-integrated", mock_agent_full_lifecycle)

    # Simulate two protocol handlers subscribing
    acp_queue = await session_pool.event_bus.subscribe("sess-integrated")
    opencode_queue = await session_pool.event_bus.subscribe("sess-integrated")

    # Run a full turn
    await session_pool.process_prompt("sess-integrated", "integrated prompt")

    # Collect events from both protocol perspectives
    acp_events: list[Any] = []
    opencode_events: list[Any] = []

    while not acp_queue.empty():
        event = acp_queue.get_nowait()
        if event is not None:
            acp_events.append(event)
    while not opencode_queue.empty():
        event = opencode_queue.get_nowait()
        if event is not None:
            opencode_events.append(event)

    # Both should see the full lifecycle
    expected_types = [
        RunStartedEvent,
        PartDeltaEvent,
        ToolCallStartEvent,
        ToolCallCompleteEvent,
        StreamCompleteEvent,
    ]

    assert len(acp_events) == len(expected_types)
    assert len(opencode_events) == len(expected_types)

    for i, expected_type in enumerate(expected_types):
        assert type(acp_events[i]) is expected_type
        assert type(opencode_events[i]) is expected_type

    # Verify specific event data
    assert acp_events[2].tool_name == "bash"
    assert opencode_events[2].tool_name == "bash"
    assert acp_events[3].tool_result == "hi"
    assert opencode_events[3].tool_result == "hi"

    # Cleanup
    await session_pool.close_session("sess-integrated")

    # Both should receive sentinel
    assert await asyncio.wait_for(acp_queue.get(), timeout=0.5) is None
    assert await asyncio.wait_for(opencode_queue.get(), timeout=0.5) is None

    await session_pool.shutdown()
