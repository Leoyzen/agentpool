"""Integration tests for SystemNotificationEvent end-to-end flow.

Verifies that ``SystemNotificationEvent`` (and the lifecycle events mapped
to it) flow from emission through the EventBus into the OpenCode SSE
event stream as ``PartUpdatedEvent`` with ``ToolPart(tool="system")``.

Flow under test:
    emit_system_notification() / complete_background_task() / steer()
        → EventBus.publish(SystemNotificationEvent)
        → EventProcessor.process(event, ctx)
        → PartUpdatedEvent(ToolPart(tool="system"))
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events.events import (
    CompactionEvent,
    SystemNotificationEvent,
)
from agentpool.orchestrator.event_bus import EventBus
from agentpool.orchestrator.run import RunHandle
from agentpool_server.opencode_server.event_processor import EventProcessor
from agentpool_server.opencode_server.event_processor_context import (
    EventProcessorContext,
)
from agentpool_server.opencode_server.models import (
    MessagePath,
    MessageTime,
    MessageWithParts,
    PartUpdatedEvent,
    ToolPart,
)
from agentpool_server.opencode_server.models.parts import ToolStateCompleted


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(state: Any = None) -> EventProcessorContext:
    """Build a minimal EventProcessorContext for notification rendering."""
    if state is None:
        state = MagicMock()
    assistant_msg = MessageWithParts.assistant(
        message_id="msg-int",
        session_id="sess-int",
        time=MessageTime(created=0),
        agent_name="test-agent",
        model_id="test-model",
        parent_id="parent-1",
        provider_id="agentpool",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )
    return EventProcessorContext(
        session_id="sess-int",
        assistant_msg_id="msg-int",
        assistant_msg=assistant_msg,
        state=state,
        working_dir="/tmp",
    )


async def _drain_bus(
    bus: EventBus,
    session_id: str,
    expected_count: int,
    timeout_s: float = 2.0,
) -> list[Any]:
    """Subscribe to the bus and drain ``expected_count`` events."""
    subscriber = await bus.subscribe(session_id)
    events: list[Any] = []
    deadline = asyncio.get_event_loop().time() + timeout_s
    while len(events) < expected_count:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            envelope = await asyncio.wait_for(
                subscriber.get(),
                timeout=remaining,
            )
            events.append(envelope.event)
        except TimeoutError:
            break
    return events


async def _process_through_processor(
    event: Any,
    ctx: EventProcessorContext,
) -> list[Any]:
    """Run an event through EventProcessor.process() and collect output."""
    processor = EventProcessor()
    return [e async for e in processor.process(event, ctx)]


def _assert_system_tool_part(
    sse_events: list[Any],
    *,
    expected_output: str | None = None,
    expected_source: str | None = None,
) -> ToolPart:
    """Assert SSE events contain exactly one PartUpdatedEvent with a
    ToolPart(tool="system"), and return it.
    """
    part_updates = [e for e in sse_events if isinstance(e, PartUpdatedEvent)]
    assert len(part_updates) == 1, f"Expected 1 PartUpdatedEvent, got {len(part_updates)}"
    part = part_updates[0].properties.part
    assert isinstance(part, ToolPart)
    assert part.tool == "system"
    assert isinstance(part.state, ToolStateCompleted)
    assert part.metadata == {"system_notification": True}
    assert part.call_id.startswith("system-")
    if expected_output is not None:
        assert part.state.output == expected_output
    if expected_source is not None:
        # For SystemNotificationEvent, the source is embedded in the title.
        title = part.state.title
        assert expected_source in title
    return part


# ---------------------------------------------------------------------------
# 7.1: complete_background_task() → OpenCode SSE
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_complete_background_task_reaches_opencode_sse() -> None:
    """complete_background_task() emits a SystemNotificationEvent that
    flows through the EventBus and EventProcessor to produce a
    PartUpdatedEvent with ToolPart(tool="system").
    """
    bus = EventBus()
    run_ctx = AgentRunContext(session_id="sess-int", event_bus=bus)
    run_ctx.steer_callback = AsyncMock(return_value="mid-1")

    # Subscribe BEFORE emitting to capture the event.
    subscriber = await bus.subscribe("sess-int")

    await run_ctx.complete_background_task("child-sess", "task completed")

    # Drain the event from the bus.
    envelope = await asyncio.wait_for(subscriber.get(), timeout=2.0)
    event = envelope.event
    assert isinstance(event, SystemNotificationEvent)
    assert event.source == "background_task"

    # Process through EventProcessor.
    ctx = _make_ctx()
    sse_events = await _process_through_processor(event, ctx)

    _assert_system_tool_part(
        sse_events,
        expected_output="[info] task completed (session: child-sess)",
        expected_source="background_task",
    )


# ---------------------------------------------------------------------------
# 7.2: CompactionEvent → OpenCode SSE
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_compaction_event_reaches_opencode_sse() -> None:
    """CompactionEvent surfaces as a system notification in OpenCode SSE
    (not silently dropped).
    """
    bus = EventBus()

    subscriber = await bus.subscribe("sess-int")
    await bus.publish(
        "sess-int",
        CompactionEvent(session_id="sess-int", trigger="auto", phase="completed"),
    )

    envelope = await asyncio.wait_for(subscriber.get(), timeout=2.0)
    event = envelope.event
    assert isinstance(event, CompactionEvent)

    ctx = _make_ctx()
    sse_events = await _process_through_processor(event, ctx)

    _assert_system_tool_part(
        sse_events,
        expected_output="[info] Context compacted (auto, completed)",
    )


# ---------------------------------------------------------------------------
# 7.3: emit_system_notification() from a tool → OpenCode SSE
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_emit_system_notification_from_tool_reaches_opencode_sse() -> None:
    """emit_system_notification() called directly (as a tool would) reaches
    the OpenCode SSE output.
    """
    bus = EventBus()
    run_ctx = AgentRunContext(session_id="sess-int", event_bus=bus)

    subscriber = await bus.subscribe("sess-int")

    await run_ctx.emit_system_notification(
        level="warning",
        source="custom",
        text="custom tool notification",
        title="Tool Alert",
    )

    envelope = await asyncio.wait_for(subscriber.get(), timeout=2.0)
    event = envelope.event
    assert isinstance(event, SystemNotificationEvent)
    assert event.source == "custom"

    ctx = _make_ctx()
    sse_events = await _process_through_processor(event, ctx)

    _assert_system_tool_part(
        sse_events,
        expected_output="[warning] Tool Alert: custom tool notification",
    )
    # Source is embedded in the fallback title only when no title is set;
    # here the title is "Tool Alert", so we verify source from the event.
    assert event.source == "custom"


# ---------------------------------------------------------------------------
# 7.4: steer() with default emit_notification=True → OpenCode SSE
# ---------------------------------------------------------------------------


def _make_run_handle(
    *,
    run_ctx: AgentRunContext,
    event_bus: EventBus,
) -> RunHandle:
    """Create a RunHandle with a stub turn and real EventBus."""
    from tests.orchestrator.test_run_handle import _StubTurn, _stream_complete_event

    turn = _StubTurn(
        events=[_stream_complete_event()],
        message_history=[],
    )
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    session = MagicMock()
    session.turn_lock = asyncio.Lock()
    return RunHandle(
        run_id="test-run",
        session_id="sess-int",
        agent_type="test",
        agent=agent,
        event_bus=event_bus,
        session=session,
        run_ctx=run_ctx,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_steer_default_emits_notification_in_sse() -> None:
    """steer() with default emit_notification=True produces a
    SystemNotificationEvent(source="steer") that reaches OpenCode SSE.
    """
    bus = EventBus()
    run_ctx = AgentRunContext(session_id="sess-int", event_bus=bus)
    handle = _make_run_handle(run_ctx=run_ctx, event_bus=bus)

    # Subscribe to capture steer notification.
    subscriber = await bus.subscribe("sess-int")

    # Start the handle and wait for it to go idle.
    gen = handle.start("hello")

    async def _consume() -> None:
        async for _ in gen:
            pass

    consumer = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    # Steer with default emit_notification=True.
    handle.steer("steer into turn")
    # Yield to let create_task fire.
    await asyncio.sleep(0.05)

    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # Drain all events from the bus and find the steer notification.
    notifications: list[SystemNotificationEvent] = []
    while not subscriber.empty():
        envelope = subscriber.get_nowait()
        if isinstance(envelope.event, SystemNotificationEvent):
            notifications.append(envelope.event)

    steer_notifs = [n for n in notifications if n.source == "steer"]
    assert len(steer_notifs) == 1
    assert steer_notifs[0].text == "System injected: steer into turn"

    # Process through EventProcessor to verify SSE output.
    ctx = _make_ctx()
    sse_events = await _process_through_processor(steer_notifs[0], ctx)
    _assert_system_tool_part(
        sse_events,
        expected_output="[info] System injected: steer into turn",
        expected_source="steer",
    )


# ---------------------------------------------------------------------------
# 7.5: steer(emit_notification=False) → NO notification in SSE
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_steer_emit_notification_false_does_not_emit_in_sse() -> None:
    """steer(emit_notification=False) does NOT produce a
    SystemNotificationEvent(source="steer") in the SSE output.
    """
    bus = EventBus()
    run_ctx = AgentRunContext(session_id="sess-int", event_bus=bus)
    handle = _make_run_handle(run_ctx=run_ctx, event_bus=bus)

    subscriber = await bus.subscribe("sess-int")

    gen = handle.start("hello")

    async def _consume() -> None:
        async for _ in gen:
            pass

    consumer = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    handle.steer("silent steer", emit_notification=False)
    await asyncio.sleep(0.05)

    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # Drain all events — there should be NO SystemNotificationEvent with source="steer".
    all_events: list[Any] = []
    while not subscriber.empty():
        envelope = subscriber.get_nowait()
        all_events.append(envelope.event)

    steer_notifs = [
        e for e in all_events
        if isinstance(e, SystemNotificationEvent) and e.source == "steer"
    ]
    assert len(steer_notifs) == 0
