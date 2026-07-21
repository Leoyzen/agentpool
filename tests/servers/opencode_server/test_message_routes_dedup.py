"""Tests for dedup integration between REST handler and EventProcessor.

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
    """Set up a real dedup set on the mock session_controller."""
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


@pytest.mark.asyncio
async def test_no_double_display_when_rest_and_event_bus_fire(
    server_state: ServerState,
) -> None:
    """REST handler registers message_id → EventProcessor skips EventBus event.

    Given: A REST handler registers a message_id in the dedup set.
    When: EventBus-derived UserMessageInsertedEvent arrives with the same
        message_id.
    Then: EventProcessor skips the event (finds message_id in dedup set).
    """
    from agentpool_server.opencode_server.routes.message_routes import _register_dedup

    dedup_store = _setup_dedup_mock(server_state)
    session_id = "test-session-dedup"
    message_id = "user-msg-integration"

    _register_dedup(server_state, session_id, message_id)
    dedup_set = dedup_store[session_id]

    processor = EventProcessor(displayed_message_ids=dedup_set)
    ctx = _make_ctx(server_state)

    event = UserMessageInsertedEvent(
        session_id=session_id,
        message_id=message_id,
        content="Message already displayed by REST handler",
        delivery="initial",
        source="protocol",
    )

    events = [e async for e in processor.process(event, ctx)]

    assert events == [], "EventBus event with REST-registered message_id should be skipped"


@pytest.mark.asyncio
async def test_dedup_does_not_affect_different_message_ids(
    server_state: ServerState,
) -> None:
    """Dedup for message_id "A" does not block message_id "B".

    Given: A dedup set containing message_id "A".
    When: UserMessageInsertedEvent arrives with message_id "B".
    Then: EventProcessor emits events for "B" (not deduped).
    """
    from agentpool_server.opencode_server.routes.message_routes import _register_dedup

    dedup_store = _setup_dedup_mock(server_state)
    session_id = "test-session-dedup"
    msg_a = "user-msg-a"
    msg_b = "user-msg-b"

    _register_dedup(server_state, session_id, msg_a)
    dedup_set = dedup_store[session_id]

    processor = EventProcessor(displayed_message_ids=dedup_set)
    ctx = _make_ctx(server_state)

    event_b = UserMessageInsertedEvent(
        session_id=session_id,
        message_id=msg_b,
        content="Different message — should be emitted",
        delivery="steer",
        source="protocol",
    )

    events = [e async for e in processor.process(event_b, ctx)]

    assert len(events) >= 2
    assert msg_b in dedup_set
