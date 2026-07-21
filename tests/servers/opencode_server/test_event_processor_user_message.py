"""Tests for UserMessageInsertedEvent handling in EventProcessor.

Tests that steer/followup user messages are correctly converted to
OpenCode UserMessage objects and broadcast as SSE events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from agentpool.agents.events import UserMessageInsertedEvent
from agentpool_server.opencode_server.event_processor import EventProcessor
from agentpool_server.opencode_server.event_processor_context import (
    EventProcessorContext,
)
from agentpool_server.opencode_server.models import (
    MessagePath,
    MessageTime,
    MessageUpdatedEvent,
    MessageWithParts,
    PartUpdatedEvent,
    TextPart,
)


pytestmark = pytest.mark.unit


if TYPE_CHECKING:
    from agentpool_server.opencode_server.state import ServerState


def _make_ctx(server_state: ServerState) -> EventProcessorContext:
    """Create a minimal EventProcessorContext for testing."""
    assistant_msg = MessageWithParts.assistant(
        message_id="msg-assistant-1",
        session_id="test-session",
        time=MessageTime(created=0),
        agent_name="test-agent",
        model_id="test-model",
        parent_id="parent-1",
        provider_id="agentpool",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )
    return EventProcessorContext(
        session_id="test-session",
        assistant_msg_id="msg-assistant-1",
        assistant_msg=assistant_msg,
        state=server_state,
        working_dir="/tmp",
    )


# =============================================================================
# UserMessageInsertedEvent Tests
# =============================================================================


@pytest.mark.asyncio
async def test_user_message_inserted_creates_user_message(
    server_state: ServerState,
) -> None:
    """Test that UserMessageInsertedEvent creates a UserMessage and SSE events.

    Verifies:
    - MessageUpdatedEvent is yielded with UserMessage info
    - PartUpdatedEvent is yielded for the text part
    - Message is appended to session state
    """
    # GIVEN: EventProcessor with no dedup set
    processor = EventProcessor()
    ctx = _make_ctx(server_state)

    # WHEN: UserMessageInsertedEvent with text content
    event = UserMessageInsertedEvent(
        session_id="test-session",
        message_id="user-msg-1",
        content="Steer this conversation",
        delivery="steer",
        source="protocol",
        timestamp=1700000000.0,
    )
    events = [e async for e in processor.process(event, ctx)]

    # THEN: Two events yielded — MessageUpdatedEvent + PartUpdatedEvent
    assert len(events) == 2
    assert isinstance(events[0], MessageUpdatedEvent)
    assert isinstance(events[1], PartUpdatedEvent)

    # AND: MessageUpdatedEvent contains UserMessage with correct fields
    msg_info = events[0].properties.info
    assert msg_info.id == "user-msg-1"
    assert msg_info.session_id == "test-session"
    assert msg_info.role == "user"
    assert msg_info.time.created == 1700000000000  # epoch seconds → milliseconds

    # AND: PartUpdatedEvent contains TextPart with the content
    part = events[1].properties.part
    assert isinstance(part, TextPart)
    assert part.text == "Steer this conversation"
    assert part.message_id == "user-msg-1"

    # AND: Message appended to session state
    messages = server_state.messages.get("test-session", [])
    assert len(messages) == 1
    assert messages[0].info.id == "user-msg-1"


@pytest.mark.asyncio
async def test_user_message_inserted_dedup_skips_duplicate(
    server_state: ServerState,
) -> None:
    """Test that duplicate message_id is skipped via dedup set.

    Verifies:
    - First emission yields events and adds message_id to dedup set
    - Second emission with same message_id yields nothing
    """
    # GIVEN: EventProcessor with dedup set
    dedup_set: set[str] = set()
    processor = EventProcessor(displayed_message_ids=dedup_set)
    ctx = _make_ctx(server_state)

    # WHEN: First UserMessageInsertedEvent
    event = UserMessageInsertedEvent(
        session_id="test-session",
        message_id="user-msg-dedup",
        content="First message",
        delivery="steer",
        timestamp=1700000000.0,
    )
    first_events = [e async for e in processor.process(event, ctx)]

    # THEN: Events yielded and message_id added to dedup set
    assert len(first_events) == 2
    assert "user-msg-dedup" in dedup_set

    # WHEN: Second UserMessageInsertedEvent with same message_id
    second_events = [e async for e in processor.process(event, ctx)]

    # THEN: No events yielded (dedup skip)
    assert len(second_events) == 0


@pytest.mark.asyncio
async def test_user_message_inserted_multimodal_content(
    server_state: ServerState,
) -> None:
    """Test that list content with text blocks creates multiple text parts.

    Verifies:
    - Each text item in the list creates a separate TextPart
    - All parts are yielded as PartUpdatedEvent
    - Dict items with "text" key are also converted
    """
    # GIVEN: EventProcessor with no dedup set
    processor = EventProcessor()
    ctx = _make_ctx(server_state)

    # WHEN: UserMessageInsertedEvent with multi-modal content (list)
    event = UserMessageInsertedEvent(
        session_id="test-session",
        message_id="user-msg-multi",
        content=["First part", {"text": "Second part"}, "Third part"],
        delivery="followup",
        timestamp=1700000001.0,
    )
    events = [e async for e in processor.process(event, ctx)]

    # THEN: 1 MessageUpdatedEvent + 3 PartUpdatedEvents
    assert len(events) == 4
    assert isinstance(events[0], MessageUpdatedEvent)

    # AND: Three text parts with correct content
    text_parts = [e for e in events[1:] if isinstance(e, PartUpdatedEvent)]
    assert len(text_parts) == 3
    assert text_parts[0].properties.part.text == "First part"
    assert text_parts[1].properties.part.text == "Second part"
    assert text_parts[2].properties.part.text == "Third part"


@pytest.mark.asyncio
async def test_user_message_inserted_empty_string_no_parts(
    server_state: ServerState,
) -> None:
    """Test that empty string content creates no text parts.

    Verifies:
    - MessageUpdatedEvent is still yielded
    - No PartUpdatedEvent for empty content
    """
    # GIVEN: EventProcessor with no dedup set
    processor = EventProcessor()
    ctx = _make_ctx(server_state)

    # WHEN: UserMessageInsertedEvent with empty string content
    event = UserMessageInsertedEvent(
        session_id="test-session",
        message_id="user-msg-empty",
        content="",
        delivery="initial",
        timestamp=1700000002.0,
    )
    events = [e async for e in processor.process(event, ctx)]

    # THEN: Only MessageUpdatedEvent, no PartUpdatedEvent
    assert len(events) == 1
    assert isinstance(events[0], MessageUpdatedEvent)


@pytest.mark.asyncio
async def test_user_message_inserted_no_dedup_set_when_none(
    server_state: ServerState,
) -> None:
    """Test that processor without dedup set processes all messages.

    Verifies:
    - When displayed_message_ids is None, no deduplication occurs
    - Same message_id processed twice yields events both times
    """
    # GIVEN: EventProcessor with no dedup set (None)
    processor = EventProcessor(displayed_message_ids=None)
    ctx = _make_ctx(server_state)

    # WHEN: Same event processed twice
    event = UserMessageInsertedEvent(
        session_id="test-session",
        message_id="user-msg-no-dedup",
        content="Repeated message",
        delivery="steer",
        timestamp=1700000003.0,
    )
    first_events = [e async for e in processor.process(event, ctx)]
    second_events = [e async for e in processor.process(event, ctx)]

    # THEN: Both times events are yielded (no dedup)
    assert len(first_events) == 2
    assert len(second_events) == 2
