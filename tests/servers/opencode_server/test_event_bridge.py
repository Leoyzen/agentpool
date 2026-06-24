"""Tests for OpenCodeEventBridge behavior parity.

Validates that the event bridge correctly dual-publishes events to both
legacy SSE subscribers and the SessionPool EventBus, while preserving
backward compatibility for the legacy path.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest

from agentpool.agents.events.events import CustomEvent
from agentpool.orchestrator.core import EventBus, EventEnvelope
from agentpool_server.opencode_server.event_bridge import OpenCodeEventBridge
from agentpool_server.opencode_server.models import (
    SessionIdleEvent,
    SessionStatus,
    SessionStatusEvent,
)
from agentpool_server.opencode_server.models.events import ServerConnectedEvent
from agentpool_server.opencode_server.state import ServerState
import anyio



if TYPE_CHECKING:
    from pathlib import Path

    from agentpool_server.opencode_server.models.events import Event


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def bridged_state(tmp_project_dir: Path, mock_agent: Mock) -> ServerState:
    """Create a ServerState with an active OpenCodeEventBridge."""
    from agentpool.orchestrator.core import EventBus

    # Wire a real EventBus into the mock pool so __post_init__ can discover it
    mock_agent.agent_pool.session_pool.event_bus = EventBus()

    state = ServerState(
        working_dir=str(tmp_project_dir),
        agent=mock_agent,
        session_controller=Mock(),  # non-None triggers bridge instantiation
    )
    return state


@pytest.fixture
def event_bus(bridged_state: ServerState) -> EventBus:
    """Return the EventBus attached to the bridged state."""
    assert bridged_state.event_bridge is not None
    return bridged_state.event_bridge._event_bus


# =============================================================================
# Legacy path tests (no session_controller)
# =============================================================================


@pytest.mark.anyio
async def test_legacy_path_broadcasts_to_sse_only(
    tmp_project_dir: Path,
    mock_agent: Mock,
) -> None:
    """Without a session_controller, events flow only to SSE subscribers."""
    state = ServerState(
        working_dir=str(tmp_project_dir),
        agent=mock_agent,
        session_controller=None,
    )
    queue = asyncio.Queue()
    state.event_subscribers.append(queue)

    event = SessionStatusEvent.create("sess-legacy", SessionStatus(type="busy"))
    await state.broadcast_event(event)

    assert queue.qsize() == 1
    assert queue.get_nowait() is event


@pytest.mark.anyio
async def test_legacy_path_no_bridge_created(
    tmp_project_dir: Path,
    mock_agent: Mock,
) -> None:
    """ServerState without session_controller has no event_bridge."""
    state = ServerState(
        working_dir=str(tmp_project_dir),
        agent=mock_agent,
        session_controller=None,
    )
    assert state.event_bridge is None


# =============================================================================
# SessionPool path tests (bridge active)
# =============================================================================


@pytest.mark.anyio
async def test_session_pool_path_broadcasts_to_sse(
    bridged_state: ServerState,
) -> None:
    """With the bridge active, events still reach SSE subscribers."""
    queue = asyncio.Queue()
    bridged_state.event_subscribers.append(queue)

    event = SessionStatusEvent.create("sess-pool", SessionStatus(type="busy"))
    await bridged_state.broadcast_event(event)

    assert queue.qsize() == 1
    assert queue.get_nowait() is event


@pytest.mark.anyio
async def test_bridge_republishes_to_event_bus(
    bridged_state: ServerState,
    event_bus: EventBus,
) -> None:
    """Events are republished to the EventBus as CustomEvent wrappers."""
    subscriber = await event_bus.subscribe("sess-pool")

    event = SessionStatusEvent.create("sess-pool", SessionStatus(type="busy"))
    await bridged_state.broadcast_event(event)

    # Allow the async publish to propagate
    await asyncio.sleep(0.05)

    assert subscriber.qsize() == 1
    envelope = subscriber.receive_nowait()
    assert isinstance(envelope, EventEnvelope)
    wrapped = envelope.event
    assert isinstance(wrapped, CustomEvent)
    assert wrapped.event_data is event
    assert wrapped.event_type == "opencode:session.status"


@pytest.mark.anyio
async def test_bridge_wraps_different_event_types(
    bridged_state: ServerState,
    event_bus: EventBus,
) -> None:
    """Various OpenCode event types are correctly wrapped."""
    subscriber = await event_bus.subscribe("sess-mixed")

    events: list[Event] = [
        SessionStatusEvent.create("sess-mixed", SessionStatus(type="busy")),
        SessionIdleEvent.create("sess-mixed"),
    ]

    for evt in events:
        await bridged_state.broadcast_event(evt)

    await asyncio.sleep(0.05)

    assert subscriber.qsize() == 2
    for i, evt in enumerate(events):
        envelope = subscriber.receive_nowait()
        assert isinstance(envelope, EventEnvelope)
        wrapped = envelope.event
        assert isinstance(wrapped, CustomEvent)
        assert wrapped.event_data is evt
        expected_type = f"opencode:{evt.type}"
        assert wrapped.event_type == expected_type


@pytest.mark.anyio
async def test_global_event_not_republished_to_event_bus(
    bridged_state: ServerState,
    event_bus: EventBus,
) -> None:
    """Global events without session_id are NOT republished to EventBus."""
    # Use a dummy session just to have a subscriber queue; the event itself
    # has no session_id so it should not be published there.
    subscriber = await event_bus.subscribe("global-session")

    event = ServerConnectedEvent()
    await bridged_state.broadcast_event(event)

    await asyncio.sleep(0.05)

    # EventBus should receive nothing because the event has no session_id
    assert subscriber.qsize() == 0

    # But SSE subscribers should still receive it
    queue = asyncio.Queue()
    bridged_state.event_subscribers.append(queue)
    await bridged_state.broadcast_event(event)
    assert queue.qsize() == 1


# =============================================================================
# Bridge unit tests
# =============================================================================


@pytest.mark.anyio
async def test_bridge_publish_calls_original_broadcast(
    bridged_state: ServerState,
) -> None:
    """Bridge.publish invokes the original SSE broadcast implementation."""
    queue = asyncio.Queue()
    bridged_state.event_subscribers.append(queue)

    event = SessionStatusEvent.create("sess-unit", SessionStatus(type="idle"))
    assert bridged_state.event_bridge is not None
    await bridged_state.event_bridge.publish(event)

    assert queue.qsize() == 1
    assert queue.get_nowait() is event


@pytest.mark.anyio
async def test_bridge_extract_session_id_variations(
    bridged_state: ServerState,
) -> None:
    """_extract_session_id handles events with and without session_id."""
    bridge = bridged_state.event_bridge
    assert bridge is not None

    # Event with session_id
    status_event = SessionStatusEvent.create("sess-1", SessionStatus(type="busy"))
    assert bridge._extract_session_id(status_event) == "sess-1"

    # Event without session_id
    connected_event = ServerConnectedEvent()
    assert bridge._extract_session_id(connected_event) is None

    # Edge case: object with no properties attribute
    class NoProperties:
        pass

    assert bridge._extract_session_id(NoProperties()) is None  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_bridge_wrap_event_format(
    bridged_state: ServerState,
) -> None:
    """_wrap_event produces a correctly formatted CustomEvent."""
    bridge = bridged_state.event_bridge
    assert bridge is not None

    event = SessionIdleEvent.create("sess-wrap")
    wrapped = bridge._wrap_event(event)

    assert isinstance(wrapped, CustomEvent)
    assert wrapped.event_data is event
    assert wrapped.event_type == "opencode:session.idle"
    assert wrapped.source == "opencode_event_bridge"


@pytest.mark.anyio
async def test_bridge_isolation_between_sessions(
    bridged_state: ServerState,
    event_bus: EventBus,
) -> None:
    """Events for session A do not leak into session B's EventBus subscription."""
    sub_a = await event_bus.subscribe("sess-a")
    sub_b = await event_bus.subscribe("sess-b")

    await bridged_state.broadcast_event(
        SessionStatusEvent.create("sess-a", SessionStatus(type="busy"))
    )
    await asyncio.sleep(0.05)

    assert sub_a.qsize() == 1
    assert sub_b.qsize() == 0

    envelope = sub_a.receive_nowait()
    assert isinstance(envelope, EventEnvelope)
    wrapped = envelope.event
    assert isinstance(wrapped, CustomEvent)
    assert wrapped.event_data.properties.session_id == "sess-a"
