"""Tests for the session status synchronization bridge."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest

from agentpool.agents.events import RunFailedEvent, RunStartedEvent, StreamCompleteEvent
from agentpool.orchestrator.core import EventBus
from agentpool_server.opencode_server.models import SessionStatus, SessionStatusEvent
from agentpool_server.opencode_server.models.events import SessionErrorEvent
from agentpool_server.opencode_server.status_bridge import SessionStatusBridge


if TYPE_CHECKING:
    from agentpool_server.opencode_server.state import ServerState


@pytest.fixture
def event_bus() -> EventBus:
    """Create a fresh EventBus for testing."""
    return EventBus()


@pytest.fixture
def bridge(server_state: ServerState, event_bus: EventBus) -> SessionStatusBridge:
    """Create a status bridge wired to the test server state and event bus."""
    return SessionStatusBridge(
        server_state=server_state,
        session_id="test-session",
        event_bus=event_bus,
    )


@pytest.mark.anyio
async def test_bridge_start_stop(bridge: SessionStatusBridge) -> None:
    """Start and stop the bridge without errors."""
    await bridge.start()
    assert bridge._task is not None
    assert bridge._queue is not None

    await bridge.stop()
    assert bridge._task is None
    assert bridge._queue is None


@pytest.mark.anyio
async def test_run_started_broadcasts_busy(
    bridge: SessionStatusBridge,
    event_bus: EventBus,
    server_state: ServerState,
) -> None:
    """RunStartedEvent triggers a busy status broadcast."""
    await bridge.start()

    await event_bus.publish(
        "test-session",
        RunStartedEvent(session_id="test-session", run_id="run-1"),
    )

    # Give the consumer task a chance to process
    await asyncio.sleep(0.05)

    status = server_state.session_status.get("test-session")
    assert status is not None
    assert status.type == "busy"

    await bridge.stop()


@pytest.mark.anyio
async def test_stream_complete_broadcasts_idle(
    bridge: SessionStatusBridge,
    event_bus: EventBus,
    server_state: ServerState,
) -> None:
    """StreamCompleteEvent triggers an idle status broadcast."""
    await bridge.start()

    # Set initial busy state
    server_state.session_status["test-session"] = SessionStatus(type="busy")

    msg = Mock()
    msg.content = "done"
    await event_bus.publish(
        "test-session",
        StreamCompleteEvent(message=msg),
    )

    await asyncio.sleep(0.05)

    status = server_state.session_status.get("test-session")
    assert status is not None
    assert status.type == "idle"

    await bridge.stop()


@pytest.mark.anyio
async def test_run_failed_broadcasts_idle_and_error(
    bridge: SessionStatusBridge,
    event_bus: EventBus,
    server_state: ServerState,
    event_capture: Any,
) -> None:
    """RunFailedEvent triggers idle status and error event broadcast."""
    await bridge.start()

    # Set initial busy state
    server_state.session_status["test-session"] = SessionStatus(type="busy")

    exc = RuntimeError("something went wrong")
    await event_bus.publish(
        "test-session",
        RunFailedEvent(run_id="run-1", session_id="test-session", exception=exc),
    )

    await asyncio.sleep(0.05)

    status = server_state.session_status.get("test-session")
    assert status is not None
    assert status.type == "idle"

    # Verify error event was broadcast
    error_events = [e for e in event_capture.events if isinstance(e, SessionErrorEvent)]
    assert len(error_events) == 1
    assert error_events[0].properties.error is not None
    assert error_events[0].properties.error.name == "RuntimeError"

    await bridge.stop()


@pytest.mark.anyio
async def test_unknown_event_ignored(
    bridge: SessionStatusBridge,
    event_bus: EventBus,
    server_state: ServerState,
) -> None:
    """Unknown events do not change session status."""
    await bridge.start()

    server_state.session_status["test-session"] = SessionStatus(type="idle")

    class UnknownEvent:
        pass

    await event_bus.publish("test-session", UnknownEvent())
    await asyncio.sleep(0.05)

    status = server_state.session_status.get("test-session")
    assert status is not None
    assert status.type == "idle"

    await bridge.stop()


@pytest.mark.anyio
async def test_status_events_broadcast_to_sse(
    bridge: SessionStatusBridge, event_bus: EventBus, event_capture: Any
) -> None:
    """Status changes are broadcast as SessionStatusEvent."""
    await bridge.start()

    await event_bus.publish(
        "test-session",
        RunStartedEvent(session_id="test-session", run_id="run-1"),
    )
    await asyncio.sleep(0.05)

    status_events = [e for e in event_capture.events if isinstance(e, SessionStatusEvent)]
    assert len(status_events) == 1
    assert status_events[0].properties.status.type == "busy"

    msg = Mock()
    msg.content = "done"
    await event_bus.publish(
        "test-session",
        StreamCompleteEvent(message=msg),
    )
    await asyncio.sleep(0.05)

    status_events = [e for e in event_capture.events if isinstance(e, SessionStatusEvent)]
    assert len(status_events) == 2
    assert status_events[1].properties.status.type == "idle"

    await bridge.stop()
