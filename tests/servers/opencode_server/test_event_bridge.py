"""Tests for OpenCodeEventBridge behavior parity.

Validates that the event bridge correctly dual-publishes events to both
legacy SSE subscribers and the SessionPool EventBus, while preserving
backward compatibility for the legacy path.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, Mock

import pytest

from agentpool.agents.events.events import CustomEvent
from agentpool.orchestrator.core import EventBus, EventEnvelope
from agentpool_server.opencode_server.models import (
    SessionIdleEvent,
    SessionStatus,
    SessionStatusEvent,
)
from agentpool_server.opencode_server.models.events import ServerConnectedEvent
from agentpool_server.opencode_server.opencode_event_bridge import (
    OpenCodeEventBridgeMixin,
)
from agentpool_server.opencode_server.state import ServerState


pytestmark = pytest.mark.integration


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
    mock_agent.host_context.session_pool.event_bus = EventBus()

    return ServerState(
        working_dir=str(tmp_project_dir),
        agent=mock_agent,
        session_controller=Mock(),  # non-None triggers bridge instantiation
    )


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
    queue: asyncio.Queue[Any] = asyncio.Queue()
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
    queue: asyncio.Queue[Any] = asyncio.Queue()
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

    envelope = subscriber.get_nowait()
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

    for _i, evt in enumerate(events):
        envelope = subscriber.get_nowait()
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
    with pytest.raises(asyncio.QueueEmpty):
        subscriber.get_nowait()

    # But SSE subscribers should still receive it
    queue: asyncio.Queue[Any] = asyncio.Queue()
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
    queue: asyncio.Queue[Any] = asyncio.Queue()
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

    envelope = sub_a.get_nowait()
    assert isinstance(envelope, EventEnvelope)
    wrapped = envelope.event
    assert wrapped.event_data.properties.session_id == "sess-a"

    with pytest.raises(asyncio.QueueEmpty):
        sub_b.get_nowait()


# =============================================================================
# --- Merged from test_event_bridge_review.py ---
# =============================================================================

"""Tests for stop_event_consumer exception handling (2nd round review).

Verifies that when one child's stop_event_consumer raises an exception,
the remaining children are still stopped (the loop doesn't break).
"""

pytestmark = pytest.mark.unit


class _FakeBridge(OpenCodeEventBridgeMixin):
    """Minimal concrete subclass for testing the mixin."""

    def __init__(self) -> None:
        self.session_pool = MagicMock()
        self.server_state = MagicMock()
        self._contexts: dict[str, Any] = {}
        self._adapters: dict[str, Any] = {}
        self._message_registered: dict[str, bool] = {}
        self._child_to_parent: dict[str, str] = {}
        self._child_spawns: dict[str, Any] = {}
        self._children_of: dict[str, set[str]] = {}
        self._resume_contexts: dict[str, dict[str, Any]] = {}
        self._pending_message_ids: dict[str, str] = {}


@pytest.mark.anyio
@pytest.mark.unit
async def test_stop_event_consumer_exception_does_not_break_loop() -> None:
    """Stop one child's consumer must not break the loop.

    When stop_event_consumer raises for one child, remaining children
    must still be stopped.
    """
    bridge = _FakeBridge()

    child1 = "child-1"
    child2 = "child-2"
    parent = "parent-session"
    bridge._children_of[parent] = {child1, child2}

    attempted: list[str] = []

    async def fake_stop(child_id: str) -> None:
        attempted.append(child_id)
        if child_id == child1:
            raise RuntimeError("simulated failure for child-1")

    bridge.stop_event_consumer = fake_stop  # type: ignore[method-assign]

    await bridge._after_consumer_loop(parent)

    assert len(attempted) == 2, f"Expected both children to be attempted, but only got {attempted}"
    assert child1 in attempted, "child-1 was not attempted"
    assert child2 in attempted, "child-2 was not attempted"
    assert parent not in bridge._children_of


@pytest.mark.anyio
@pytest.mark.unit
async def test_stop_event_consumer_all_succeed() -> None:
    """Normal case: all children stopped successfully."""
    bridge = _FakeBridge()

    child1 = "child-1"
    child2 = "child-2"
    parent = "parent-session"
    bridge._children_of[parent] = {child1, child2}

    attempted: list[str] = []

    async def fake_stop(child_id: str) -> None:
        attempted.append(child_id)

    bridge.stop_event_consumer = fake_stop  # type: ignore[method-assign]

    await bridge._after_consumer_loop(parent)

    assert len(attempted) == 2
    assert parent not in bridge._children_of


@pytest.mark.anyio
@pytest.mark.unit
async def test_stop_event_consumer_no_children() -> None:
    """When there are no children, _after_consumer_loop runs cleanly."""
    bridge = _FakeBridge()
    parent = "parent-session"

    attempted: list[str] = []

    async def fake_stop(child_id: str) -> None:
        attempted.append(child_id)

    bridge.stop_event_consumer = fake_stop  # type: ignore[method-assign]

    await bridge._after_consumer_loop(parent)

    assert attempted == []
