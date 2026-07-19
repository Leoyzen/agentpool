"""Unit tests for ``AgentRunContext.emit_system_notification``.

Verifies the best-effort emission contract:
- (a) emission publishes a ``SystemNotificationEvent`` to ``event_bus``
- (b) empty ``text`` logs a warning and does not publish
- (c) ``event_bus`` failure logs a warning but does not raise
- (d) ``event_bus=None`` logs a warning and returns without raising
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events.events import SystemNotificationEvent


pytestmark = pytest.mark.unit


def _make_run_ctx(
    *,
    event_bus: object | None = AsyncMock(),
    session_id: str = "sess-test",
) -> AgentRunContext:
    """Build an ``AgentRunContext`` with the given event_bus and session_id."""
    return AgentRunContext(
        event_bus=event_bus,  # type: ignore[arg-type]
        session_id=session_id,
    )


@pytest.mark.asyncio
async def test_emit_system_notification_publishes_to_event_bus() -> None:
    """emit_system_notification constructs a SystemNotificationEvent and
    publishes it to event_bus with the run's session_id, including ref_label.
    """
    bus = AsyncMock()
    ctx = _make_run_ctx(event_bus=bus, session_id="sess-1")

    await ctx.emit_system_notification(
        level="warning",
        source="background_task",
        text="task completed",
        title="Done",
        ref_session_id="child-1",
        ref_label="member: researcher",
    )

    bus.publish.assert_awaited_once()
    call_args = bus.publish.await_args
    assert call_args.args[0] == "sess-1"
    event = call_args.args[1]
    assert isinstance(event, SystemNotificationEvent)
    assert event.session_id == "sess-1"
    assert event.level == "warning"
    assert event.source == "background_task"
    assert event.title == "Done"
    assert event.text == "task completed"
    assert event.ref_session_id == "child-1"
    assert event.ref_label == "member: researcher"


@pytest.mark.asyncio
async def test_emit_system_notification_empty_text_skips_publish(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When ``text`` is empty, emit_system_notification logs a warning and
    does NOT call ``event_bus.publish``.
    """
    bus = AsyncMock()
    ctx = _make_run_ctx(event_bus=bus)

    with caplog.at_level(logging.WARNING, logger="agentpool.agents.context"):
        await ctx.emit_system_notification(text="")

    bus.publish.assert_not_awaited()
    assert any(
        "empty text" in rec.message.lower() for rec in caplog.records
    ), f"Expected 'empty text' warning in logs, got: {caplog.records}"


@pytest.mark.asyncio
async def test_emit_system_notification_event_bus_failure_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When ``event_bus.publish`` raises, emit_system_notification logs a
    warning but does NOT propagate the exception.
    """
    bus = AsyncMock()
    bus.publish.side_effect = RuntimeError("bus down")
    ctx = _make_run_ctx(event_bus=bus)

    with caplog.at_level(logging.WARNING, logger="agentpool.agents.context"):
        # Should not raise.
        await ctx.emit_system_notification(text="hello")

    bus.publish.assert_awaited_once()
    assert any(
        "failed to publish" in rec.message.lower() for rec in caplog.records
    ), f"Expected 'failed to publish' warning in logs, got: {caplog.records}"


@pytest.mark.asyncio
async def test_emit_system_notification_no_event_bus_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When ``event_bus`` is None, emit_system_notification logs a warning
    and returns without raising.
    """
    ctx = _make_run_ctx(event_bus=None)

    with caplog.at_level(logging.WARNING, logger="agentpool.agents.context"):
        # Should not raise.
        await ctx.emit_system_notification(text="hello")

    assert any(
        "no event_bus" in rec.message.lower() for rec in caplog.records
    ), f"Expected 'no event_bus' warning in logs, got: {caplog.records}"
