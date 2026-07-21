"""Tests for user message dedup wiring at OpenCode UserMessage creation sites.

Verifies that when a user message is created by the REST handler, the
message_id is registered in the shared dedup set. When the EventBus-derived
``UserMessageInsertedEvent`` arrives at ``EventProcessor``, it finds the
message_id in the dedup set and skips (no double display).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from agentpool.agents.events import UserMessageInsertedEvent
from agentpool_server.opencode_server.event_processor import EventProcessor
from agentpool_server.opencode_server.event_processor_context import (
    EventProcessorContext,
)
from agentpool_server.opencode_server.models import (
    MessagePath,
    MessageTime,
    MessageWithParts,
)


pytestmark = pytest.mark.unit


if TYPE_CHECKING:
    from agentpool_server.opencode_server.state import ServerState


def _make_ctx(server_state: ServerState) -> EventProcessorContext:
    """Create a minimal EventProcessorContext for testing."""
    assistant_msg = MessageWithParts.assistant(
        message_id="msg-assistant-dedup",
        session_id="test-session-dedup",
        time=MessageTime(created=0),
        agent_name="test-agent",
        model_id="test-model",
        parent_id="parent-dedup",
        provider_id="agentpool",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )
    return EventProcessorContext(
        session_id="test-session-dedup",
        assistant_msg_id="msg-assistant-dedup",
        assistant_msg=assistant_msg,
        state=server_state,
        working_dir="/tmp",
    )


def _setup_dedup_mock(server_state: ServerState) -> dict[str, set[str]]:
    """Set up a real dedup set on the mock session_controller.

    The conftest ``server_state`` fixture uses a Mock session_controller
    that doesn't have a real ``_get_dedup_set``. This helper replaces it
    with a real implementation backed by a dict of sets.
    """
    dedup_store: dict[str, set[str]] = {}

    def _get_dedup_set(session_id: str) -> set[str]:
        return dedup_store.setdefault(session_id, set())

    controller = server_state.session_controller
    if controller is None:
        controller = Mock()
        server_state.session_controller = controller
    controller._get_dedup_set = _get_dedup_set
    controller._displayed_message_ids = dedup_store
    return dedup_store


async def _collect_events(
    processor: EventProcessor,
    ctx: EventProcessorContext,
    event: UserMessageInsertedEvent,
) -> list:
    """Collect all events from processor.process() into a list."""
    return [e async for e in processor.process(event, ctx)]


# =============================================================================
# _register_dedup Helper Tests
# =============================================================================


def test_register_dedup_adds_message_id_to_dedup_set(
    server_state: ServerState,
) -> None:
    """Test that _register_dedup adds the message_id to the dedup set.

    Given: A server state with a session_controller that has a dedup set.
    When: _register_dedup is called with a session_id and message_id.
    Then: The message_id appears in the session's dedup set.
    """
    from agentpool_server.opencode_server.routes.message_routes import _register_dedup

    dedup_store = _setup_dedup_mock(server_state)
    session_id = "test-session-dedup"
    message_id = "user-msg-dedup-1"

    _register_dedup(server_state, session_id, message_id)

    assert message_id in dedup_store[session_id]


def test_register_dedup_noop_without_session_controller(
    server_state: ServerState,
) -> None:
    """Test that _register_dedup is a no-op when session_controller is None.

    Given: A server state with session_controller set to None.
    When: _register_dedup is called.
    Then: No exception is raised and nothing is registered.
    """
    from agentpool_server.opencode_server.routes.message_routes import _register_dedup

    server_state.session_controller = None
    session_id = "test-session-no-controller"
    message_id = "user-msg-no-controller"

    # Should not raise
    _register_dedup(server_state, session_id, message_id)


# =============================================================================
# EventProcessor Dedup Tests
# =============================================================================


@pytest.mark.asyncio
async def test_event_processor_skips_deduped_message(
    server_state: ServerState,
) -> None:
    """Test EventProcessor skips UserMessageInsertedEvent when message_id is in dedup set.

    Given: An EventProcessor with a dedup set containing a message_id.
    When: UserMessageInsertedEvent arrives with the same message_id.
    Then: No events are yielded (dedup skip).
    """
    dedup_store = _setup_dedup_mock(server_state)
    session_id = "test-session-dedup"
    message_id = "user-msg-dedup-skip"

    # Register message_id in dedup set (simulating REST handler)
    dedup_set = dedup_store.setdefault(session_id, set())
    dedup_set.add(message_id)

    # Create EventProcessor with the same dedup set
    processor = EventProcessor(displayed_message_ids=dedup_set)
    ctx = _make_ctx(server_state)

    # WHEN: UserMessageInsertedEvent with the same message_id
    event = UserMessageInsertedEvent(
        session_id=session_id,
        message_id=message_id,
        content="This should be skipped",
        delivery="initial",
        source="protocol",
    )

    events = await _collect_events(processor, ctx, event)

    # THEN: No events yielded (dedup skip)
    assert events == []


@pytest.mark.asyncio
async def test_event_processor_emits_non_deduped_message(
    server_state: ServerState,
) -> None:
    """Test EventProcessor emits events when message_id is NOT in dedup set.

    Given: An EventProcessor with an empty dedup set.
    When: UserMessageInsertedEvent arrives with a new message_id.
    Then: MessageUpdatedEvent and PartUpdatedEvent are yielded, and the
        message_id is added to the dedup set.
    """
    dedup_store = _setup_dedup_mock(server_state)
    session_id = "test-session-dedup"
    message_id = "user-msg-dedup-emit"

    dedup_set = dedup_store.setdefault(session_id, set())

    # Create EventProcessor with the dedup set (empty for this message_id)
    processor = EventProcessor(displayed_message_ids=dedup_set)
    ctx = _make_ctx(server_state)

    event = UserMessageInsertedEvent(
        session_id=session_id,
        message_id=message_id,
        content="This should be emitted",
        delivery="initial",
        source="protocol",
    )

    events = await _collect_events(processor, ctx, event)

    # THEN: Events yielded (not deduped)
    assert len(events) >= 2  # MessageUpdatedEvent + PartUpdatedEvent
    # AND: message_id added to dedup set
    assert message_id in dedup_set


# =============================================================================
# Integration: REST handler registration + EventProcessor dedup
# =============================================================================


@pytest.mark.asyncio
async def test_no_double_display_when_rest_and_event_bus_fire(
    server_state: ServerState,
) -> None:
    """Test no double display when both REST handler and EventBus event fire with same message_id.

    Given: A REST handler registers a message_id in the dedup set.
    When: EventBus-derived UserMessageInsertedEvent arrives with the same
        message_id.
    Then: EventProcessor skips the event (finds message_id in dedup set).
    """
    from agentpool_server.opencode_server.routes.message_routes import _register_dedup

    dedup_store = _setup_dedup_mock(server_state)
    session_id = "test-session-dedup"
    message_id = "user-msg-integration"

    # Step 1: REST handler creates user message and registers in dedup set
    _register_dedup(server_state, session_id, message_id)

    # Verify registration
    assert message_id in dedup_store[session_id]
    dedup_set = dedup_store[session_id]

    # Step 2: EventBus-derived UserMessageInsertedEvent arrives
    # The EventProcessor uses the same dedup set (wired via event bridge)
    processor = EventProcessor(displayed_message_ids=dedup_set)
    ctx = _make_ctx(server_state)

    event = UserMessageInsertedEvent(
        session_id=session_id,
        message_id=message_id,
        content="Message already displayed by REST handler",
        delivery="initial",
        source="protocol",
    )

    events = await _collect_events(processor, ctx, event)

    # THEN: No events yielded — dedup prevents double display
    assert events == []


@pytest.mark.asyncio
async def test_dedup_does_not_affect_different_message_ids(
    server_state: ServerState,
) -> None:
    """Test that dedup for one message_id does not block a different one.

    Given: A dedup set containing message_id "A".
    When: UserMessageInsertedEvent arrives with message_id "B".
    Then: EventProcessor emits events for "B" (not deduped).
    """
    from agentpool_server.opencode_server.routes.message_routes import _register_dedup

    dedup_store = _setup_dedup_mock(server_state)
    session_id = "test-session-dedup"
    msg_a = "user-msg-a"
    msg_b = "user-msg-b"

    # Register only msg_a
    _register_dedup(server_state, session_id, msg_a)

    dedup_set = dedup_store[session_id]

    # EventProcessor with the dedup set
    processor = EventProcessor(displayed_message_ids=dedup_set)
    ctx = _make_ctx(server_state)

    # Event for msg_b should NOT be deduped
    event_b = UserMessageInsertedEvent(
        session_id=session_id,
        message_id=msg_b,
        content="Different message — should be emitted",
        delivery="steer",
        source="protocol",
    )

    events = await _collect_events(processor, ctx, event_b)

    # THEN: Events yielded for msg_b
    assert len(events) >= 2
    # AND: msg_b now in dedup set
    assert msg_b in dedup_set
