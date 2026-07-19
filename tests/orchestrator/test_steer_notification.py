"""Unit tests for ``RunHandle.steer()`` / ``followup()`` notification emission.

Verifies the ``emit_notification`` flag behavior, long-message truncation,
and the ``RuntimeError`` (no running loop) fallback for the
``asyncio.create_task()`` fire-and-forget notification scheduling
(tasks 4.5–4.7).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events.events import SystemNotificationEvent
from agentpool.lifecycle import RunState
from agentpool.orchestrator.run import RunHandle

from .test_run_handle import _StubTurn, _stream_complete_event


pytestmark = pytest.mark.unit


def _make_handle(
    *,
    run_ctx: AgentRunContext | None = None,
    event_bus: Any | None = None,
) -> RunHandle:
    """Create a RunHandle with mocked deps and a stub turn."""
    turn = _StubTurn(
        events=[_stream_complete_event()],
        message_history=[],
    )
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    if event_bus is None:
        event_bus = AsyncMock()
    session = MagicMock()
    session.turn_lock = asyncio.Lock()
    return RunHandle(
        run_id="test-run",
        session_id="test-session",
        agent_type="test",
        agent=agent,
        event_bus=event_bus,
        session=session,
        run_ctx=run_ctx or AgentRunContext(),
    )


async def _start_and_wait_idle(handle: RunHandle) -> asyncio.Task[None]:
    """Start the RunHandle generator and wait until it reaches IDLE.

    Returns the consumer task so the caller can await it after close().
    """
    gen = handle.start("hello")

    async def _consume() -> None:
        async for _ in gen:
            pass

    consumer = asyncio.create_task(_consume())
    # Wait for first turn to complete and handle to go idle.
    await asyncio.sleep(0.05)
    assert handle._run_state == RunState.IDLE
    return consumer


def _published_notifications(bus: AsyncMock) -> list[SystemNotificationEvent]:
    """Extract all SystemNotificationEvent instances from bus.publish calls."""
    result: list[SystemNotificationEvent] = []
    for call in bus.publish.call_args_list:
        event = call.args[1] if len(call.args) > 1 else call.kwargs.get("event")
        if isinstance(event, SystemNotificationEvent):
            result.append(event)
    return result


# ---------------------------------------------------------------------------
# steer() notification tests (4.5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_steer_default_emits_notification() -> None:
    """steer() with default emit_notification=True schedules a
    SystemNotificationEvent(source="steer") via create_task.
    """
    bus = AsyncMock()
    run_ctx = AgentRunContext(session_id="sess-1", event_bus=bus)
    handle = _make_handle(run_ctx=run_ctx, event_bus=bus)

    consumer = await _start_and_wait_idle(handle)

    handle.steer("steer me")
    # Yield to the event loop so the create_task fires.
    await asyncio.sleep(0.05)

    notifications = _published_notifications(bus)
    steer_notifs = [n for n in notifications if n.source == "steer"]
    assert len(steer_notifs) == 1
    assert steer_notifs[0].text == "System injected: steer me"

    handle.close()
    await asyncio.sleep(0.05)
    await consumer


@pytest.mark.unit
async def test_steer_emit_notification_false_does_not_emit() -> None:
    """steer(emit_notification=False) does NOT schedule a notification."""
    bus = AsyncMock()
    run_ctx = AgentRunContext(session_id="sess-1", event_bus=bus)
    handle = _make_handle(run_ctx=run_ctx, event_bus=bus)

    consumer = await _start_and_wait_idle(handle)

    handle.steer("steer me", emit_notification=False)
    await asyncio.sleep(0.05)

    notifications = _published_notifications(bus)
    steer_notifs = [n for n in notifications if n.source == "steer"]
    assert len(steer_notifs) == 0

    handle.close()
    await asyncio.sleep(0.05)
    await consumer


# ---------------------------------------------------------------------------
# followup() notification tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_followup_emit_notification_true_emits_notification() -> None:
    """followup(emit_notification=True) schedules a
    SystemNotificationEvent(source="followup").
    """
    bus = AsyncMock()
    run_ctx = AgentRunContext(session_id="sess-1", event_bus=bus)
    handle = _make_handle(run_ctx=run_ctx, event_bus=bus)

    consumer = await _start_and_wait_idle(handle)

    handle.followup("followup msg", emit_notification=True)
    await asyncio.sleep(0.05)

    notifications = _published_notifications(bus)
    followup_notifs = [n for n in notifications if n.source == "followup"]
    assert len(followup_notifs) == 1
    assert followup_notifs[0].text == "System queued: followup msg"

    handle.close()
    await asyncio.sleep(0.05)
    await consumer


@pytest.mark.unit
async def test_followup_default_does_not_emit_notification() -> None:
    """followup() with default emit_notification=False does NOT emit."""
    bus = AsyncMock()
    run_ctx = AgentRunContext(session_id="sess-1", event_bus=bus)
    handle = _make_handle(run_ctx=run_ctx, event_bus=bus)

    consumer = await _start_and_wait_idle(handle)

    handle.followup("followup msg")
    await asyncio.sleep(0.05)

    notifications = _published_notifications(bus)
    followup_notifs = [n for n in notifications if n.source == "followup"]
    assert len(followup_notifs) == 0

    handle.close()
    await asyncio.sleep(0.05)
    await consumer


# ---------------------------------------------------------------------------
# Long-message truncation (4.6)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_steer_long_message_truncation() -> None:
    """steer() with a message >80 chars truncates the notification text
    with a "..." suffix.
    """
    bus = AsyncMock()
    run_ctx = AgentRunContext(session_id="sess-1", event_bus=bus)
    handle = _make_handle(run_ctx=run_ctx, event_bus=bus)

    consumer = await _start_and_wait_idle(handle)

    long_msg = "x" * 100  # 100 chars, > 80
    handle.steer(long_msg)
    await asyncio.sleep(0.05)

    notifications = _published_notifications(bus)
    steer_notifs = [n for n in notifications if n.source == "steer"]
    assert len(steer_notifs) == 1
    # Text should be: "System injected: " + first 80 chars + "..."
    expected = f"System injected: {'x' * 80}..."
    assert steer_notifs[0].text == expected

    handle.close()
    await asyncio.sleep(0.05)
    await consumer


@pytest.mark.unit
async def test_steer_short_message_no_truncation() -> None:
    """steer() with a message <=80 chars does NOT truncate."""
    bus = AsyncMock()
    run_ctx = AgentRunContext(session_id="sess-1", event_bus=bus)
    handle = _make_handle(run_ctx=run_ctx, event_bus=bus)

    consumer = await _start_and_wait_idle(handle)

    short_msg = "y" * 80  # exactly 80 chars, no truncation
    handle.steer(short_msg)
    await asyncio.sleep(0.05)

    notifications = _published_notifications(bus)
    steer_notifs = [n for n in notifications if n.source == "steer"]
    assert len(steer_notifs) == 1
    expected = f"System injected: {'y' * 80}"
    assert steer_notifs[0].text == expected

    handle.close()
    await asyncio.sleep(0.05)
    await consumer


@pytest.mark.unit
async def test_followup_long_message_truncation() -> None:
    """followup(emit_notification=True) with >80 chars truncates with "..."."""
    bus = AsyncMock()
    run_ctx = AgentRunContext(session_id="sess-1", event_bus=bus)
    handle = _make_handle(run_ctx=run_ctx, event_bus=bus)

    consumer = await _start_and_wait_idle(handle)

    long_msg = "z" * 90
    handle.followup(long_msg, emit_notification=True)
    await asyncio.sleep(0.05)

    notifications = _published_notifications(bus)
    followup_notifs = [n for n in notifications if n.source == "followup"]
    assert len(followup_notifs) == 1
    expected = f"System queued: {'z' * 80}..."
    assert followup_notifs[0].text == expected

    handle.close()
    await asyncio.sleep(0.05)
    await consumer


# ---------------------------------------------------------------------------
# RuntimeError fallback (4.7) — no running event loop
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_steer_without_running_loop_skips_notification_silently() -> None:
    """When steer() is called outside a running event loop, the
    notification is silently skipped (RuntimeError caught), but steer()
    still returns the message_id normally.

    This test runs in a synchronous (no-async) context so
    ``asyncio.get_running_loop()`` raises ``RuntimeError``.
    """
    bus = AsyncMock()
    run_ctx = AgentRunContext(session_id="sess-1", event_bus=bus)
    handle = _make_handle(run_ctx=run_ctx, event_bus=bus)

    # No running loop here (sync test function). steer() still works
    # because it only uses the loop for the notification create_task.
    result = handle.steer("steer without loop")
    assert result is not None

    # No notification was published (create_task never fired).
    notifications = _published_notifications(bus)
    steer_notifs = [n for n in notifications if n.source == "steer"]
    assert len(steer_notifs) == 0


@pytest.mark.unit
def test_followup_without_running_loop_skips_notification_silently() -> None:
    """When followup(emit_notification=True) is called outside a running
    event loop, the notification is silently skipped but followup()
    returns normally.
    """
    bus = AsyncMock()
    run_ctx = AgentRunContext(session_id="sess-1", event_bus=bus)
    handle = _make_handle(run_ctx=run_ctx, event_bus=bus)

    result = handle.followup("followup without loop", emit_notification=True)
    assert result is not None

    notifications = _published_notifications(bus)
    followup_notifs = [n for n in notifications if n.source == "followup"]
    assert len(followup_notifs) == 0
