"""Integration tests for SSE replay buffer + sync() interaction.

Tests that the sync() endpoint clears the EventBus replay buffer so
reconnecting SSE subscribers don't receive duplicate PartUpdatedEvent
events that sync() already loaded from the DB.

This is the test coverage that was missing — the first user message
duplication bug occurred because:
1. User sends message → PartUpdatedEvent published to EventBus → enters replay buffer
2. TUI connects to SSE → replay buffer delivers PartUpdatedEvent
3. TUI calls sync() → loads same parts from DB
4. Both sources provide parts → duplicate rendering

The fix: sync() endpoint calls event_bus.clear_replay_buffer(session_id)
so that events published before sync() are not re-delivered.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from agentpool.agents.events.events import (
    UserMessageInsertedEvent,
)
from agentpool.orchestrator.core import EventBus
from agentpool_server.opencode_server.event_processor_context import (
    EventProcessorContext,
)
from agentpool_server.opencode_server.models import (
    MessagePath,
    MessageTime,
    MessageWithParts,
)
from agentpool_server.opencode_server.opencode_event_bridge import (
    OpenCodeEventBridgeMixin,
)


pytestmark = pytest.mark.integration


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


async def _async_iter(items: list[Any]) -> AsyncIterator[Any]:
    for item in items:
        yield item


class _FakeBridge(OpenCodeEventBridgeMixin):
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
        self._pending_message_metadata: dict[str, dict[str, str | None]] = {}
        self.set_session_context_data = self._resume_contexts.__setitem__
        self.get_session_context_data = lambda sid: self._resume_contexts.pop(sid, None)


def _make_ctx(session_id: str) -> EventProcessorContext:
    assistant_msg = MessageWithParts.assistant(
        message_id="msg-a1",
        session_id=session_id,
        time=MessageTime(created=1000),
        agent_name="test-agent",
        model_id="test-model",
        parent_id=session_id,
        provider_id="test-provider",
        path=MessagePath(cwd="/tmp", root="/tmp"),
        mode="test-agent",
    )
    return EventProcessorContext(
        session_id=session_id,
        assistant_msg_id="msg-a1",
        assistant_msg=assistant_msg,
        state=MagicMock(),
        working_dir="/tmp",
    )


def _setup_bridge_with_event_bus(
    session_id: str,
) -> tuple[_FakeBridge, EventBus, EventProcessorContext]:
    """Set up a _FakeBridge with a REAL EventBus (not mocked)."""
    event_bus = EventBus()
    bridge = _FakeBridge()
    bridge._event_bus = event_bus

    ctx = _make_ctx(session_id)
    bridge._contexts[session_id] = ctx
    bridge._message_registered[session_id] = False

    adapter_mock = MagicMock()
    adapter_mock.convert_event = lambda _e: _async_iter([])
    bridge._adapters[session_id] = adapter_mock

    async def fake_broadcast(event: Any) -> None:
        pass

    bridge.server_state.broadcast_event = fake_broadcast  # type: ignore[method-assign]
    bridge.server_state.working_dir = "/tmp"
    bridge.server_state.resolve_default_model_info = Mock(
        return_value=("test-model", "test-provider")
    )
    bridge.session_pool.sessions.get_session = Mock(return_value=None)

    return bridge, event_bus, ctx


def _patch_mocks():
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        with (
            __import__("unittest.mock").mock.patch(
                "agentpool_server.opencode_server.opencode_event_bridge.set_session_status",
                new_callable=AsyncMock,
            ),
            __import__("unittest.mock").mock.patch(
                "agentpool_server.opencode_server.opencode_event_bridge.append_message_to_session",
                new_callable=AsyncMock,
            ),
        ):
            yield

    return _ctx()


# =============================================================================
# Replay buffer + sync() interaction tests
# =============================================================================


@pytest.mark.anyio
async def test_replay_buffer_contains_events_after_publish() -> None:
    """EventBus replay buffer stores events after publish.

    Given: A real EventBus with replay buffer enabled.
    When: A UserMessageInsertedEvent is published.
    Then: The event is in the replay buffer for the session.
    And: A new subscriber receives it from the replay buffer.
    """
    session_id = "sess-rb-1"
    _bridge, event_bus, _ctx = _setup_bridge_with_event_bus(session_id)

    event = UserMessageInsertedEvent(
        content="hello",
        session_id=session_id,
        message_id="msg-u1",
        source="protocol",
    )

    await event_bus.publish(session_id, event)

    # Replay buffer should contain the event
    buffer = event_bus._replay_buffers.get(session_id)
    assert buffer is not None
    assert len(buffer) > 0, "Replay buffer should contain the published event"


@pytest.mark.anyio
async def test_new_subscriber_receives_replay_buffer_events() -> None:
    """New EventBus subscriber receives events from replay buffer.

    Given: An event was published before subscription.
    When: A new subscriber subscribes to the session.
    Then: The subscriber receives the replayed event.
    """
    session_id = "sess-rb-2"
    _bridge, event_bus, _ctx = _setup_bridge_with_event_bus(session_id)

    event = UserMessageInsertedEvent(
        content="hello",
        session_id=session_id,
        message_id="msg-u2",
        source="protocol",
    )
    await event_bus.publish(session_id, event)

    # Subscribe AFTER the event was published
    queue = await event_bus.subscribe(session_id, scope="session")

    # Drain replay buffer events (they're enqueued synchronously in subscribe)
    received_events: list[Any] = []
    try:
        while not queue.empty():
            envelope = queue.get_nowait()
            received_events.append(envelope.event)
    except asyncio.QueueEmpty:
        pass

    assert len(received_events) >= 1, "New subscriber should receive event from replay buffer"


@pytest.mark.anyio
async def test_clear_replay_buffer_prevents_redelivery() -> None:
    """clear_replay_buffer prevents re-delivery to new subscribers.

    Given: An event was published and is in the replay buffer.
    When: clear_replay_buffer is called (simulating sync() endpoint).
    Then: A new subscriber does NOT receive the old event.
    """
    session_id = "sess-rb-3"
    _bridge, event_bus, _ctx = _setup_bridge_with_event_bus(session_id)

    event = UserMessageInsertedEvent(
        content="hello",
        session_id=session_id,
        message_id="msg-u3",
        source="protocol",
    )
    await event_bus.publish(session_id, event)

    # Clear replay buffer (simulates sync() endpoint behavior)
    event_bus.clear_replay_buffer(session_id)

    # Subscribe AFTER clear — should NOT receive old events
    queue = await event_bus.subscribe(session_id, scope="session")

    received_events: list[Any] = []
    try:
        while not queue.empty():
            envelope = queue.get_nowait()
            received_events.append(envelope.event)
    except asyncio.QueueEmpty:
        pass

    assert len(received_events) == 0, (
        "New subscriber should NOT receive events after replay buffer was cleared"
    )


@pytest.mark.anyio
async def test_clear_replay_buffer_only_affects_target_session() -> None:
    """clear_replay_buffer only clears the specified session's buffer.

    Given: Events published for two sessions.
    When: clear_replay_buffer is called for session A only.
    Then: Session A's buffer is empty, session B's buffer is intact.
    """
    session_a = "sess-rb-a"
    session_b = "sess-rb-b"
    _bridge, event_bus, _ctx = _setup_bridge_with_event_bus(session_a)

    event_a = UserMessageInsertedEvent(
        content="a", session_id=session_a, message_id="msg-a", source="protocol"
    )
    event_b = UserMessageInsertedEvent(
        content="b", session_id=session_b, message_id="msg-b", source="protocol"
    )
    await event_bus.publish(session_a, event_a)
    await event_bus.publish(session_b, event_b)

    # Clear only session A
    event_bus.clear_replay_buffer(session_a)

    buffer_a = event_bus._replay_buffers.get(session_a)
    buffer_b = event_bus._replay_buffers.get(session_b)

    assert buffer_a is None or len(buffer_a) == 0, "Session A replay buffer should be empty"
    assert buffer_b is not None, "Session B replay buffer should not be None"
    assert len(buffer_b) > 0, "Session B replay buffer should be intact"


@pytest.mark.anyio
async def test_events_after_clear_are_still_delivered() -> None:
    """Events published AFTER clear_replay_buffer are delivered normally.

    Given: Replay buffer was cleared (sync() was called).
    When: A new event is published.
    Then: Live subscribers receive it.
    And: New subscribers receive it from the (repopulated) replay buffer.
    """
    session_id = "sess-rb-4"
    _bridge, event_bus, _ctx = _setup_bridge_with_event_bus(session_id)

    # Publish old event
    old_event = UserMessageInsertedEvent(
        content="old", session_id=session_id, message_id="msg-old", source="protocol"
    )
    await event_bus.publish(session_id, old_event)

    # Clear (sync() called)
    event_bus.clear_replay_buffer(session_id)

    # Publish new event AFTER clear
    new_event = UserMessageInsertedEvent(
        content="new", session_id=session_id, message_id="msg-new", source="protocol"
    )
    await event_bus.publish(session_id, new_event)

    # New subscriber should receive ONLY the new event, not the old one
    queue = await event_bus.subscribe(session_id, scope="session")

    received: list[Any] = []
    try:
        while not queue.empty():
            envelope = queue.get_nowait()
            received.append(envelope.event)
    except asyncio.QueueEmpty:
        pass

    assert len(received) == 1, f"Should receive exactly 1 event (the new one), got {len(received)}"
    assert isinstance(received[0], UserMessageInsertedEvent)
    assert received[0].message_id == "msg-new", "Should receive the NEW event, not the old one"
