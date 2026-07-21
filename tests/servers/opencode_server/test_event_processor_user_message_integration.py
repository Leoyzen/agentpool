"""L2 integration tests for UserMessageInsertedEvent handling in EventProcessor.

These tests use REAL ``EventProcessor`` and ``EventProcessorContext`` instances
(no mocking of ``process()``) to verify the full event → OpenCode SSE event
conversion pipeline. They exercise the real ``_process_user_message_inserted``
code path including dedup, content parsing, and session-state mutation.

See ``test_event_processor_user_message.py`` for the L1 unit tests.
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


pytestmark = [pytest.mark.integration, pytest.mark.anyio]


if TYPE_CHECKING:
    from agentpool_server.opencode_server.state import ServerState


def _make_ctx(server_state: ServerState) -> EventProcessorContext:
    """Create a minimal EventProcessorContext for integration testing."""
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
# Test 12: Real EventProcessor creates UserMessage from UserMessageInsertedEvent
# =============================================================================


@pytest.mark.asyncio
async def test_event_processor_creates_user_message(
    server_state: ServerState,
) -> None:
    """Real EventProcessor converts UserMessageInsertedEvent to SSE events.

    Given: A real ``EventProcessor`` with no dedup set and a
        ``UserMessageInsertedEvent`` with string content.
    When: ``processor.process(event, ctx)`` is called.
    Then: Yields ``MessageUpdatedEvent`` with a ``UserMessage`` (role="user",
        id="msg_1", content containing "hello world") and a
        ``PartUpdatedEvent`` with a ``TextPart``.
    """
    # GIVEN: Real EventProcessor with no dedup set
    processor = EventProcessor()
    ctx = _make_ctx(server_state)

    # WHEN: UserMessageInsertedEvent with text content
    event = UserMessageInsertedEvent(
        session_id="s1",
        message_id="msg_1",
        content="hello world",
        delivery="steer",
        source="background_task",
    )
    events = [e async for e in processor.process(event, ctx)]

    # THEN: MessageUpdatedEvent yielded with UserMessage info
    msg_events = [e for e in events if isinstance(e, MessageUpdatedEvent)]
    assert len(msg_events) == 1
    msg_info = msg_events[0].properties.info
    assert msg_info.id == "msg_1"
    assert msg_info.role == "user"

    # AND: PartUpdatedEvent yielded with TextPart containing "hello world"
    part_events = [e for e in events if isinstance(e, PartUpdatedEvent)]
    assert len(part_events) == 1
    part = part_events[0].properties.part
    assert isinstance(part, TextPart)
    assert part.text == "hello world"
    assert part.message_id == "msg_1"

    # AND: Message appended to session state
    messages = server_state.messages.get("test-session", [])
    assert len(messages) == 1
    assert messages[0].info.id == "msg_1"


# =============================================================================
# Test 13: Real EventProcessor dedup skips duplicate message_id
# =============================================================================


@pytest.mark.asyncio
async def test_event_processor_dedup_skips_duplicate(
    server_state: ServerState,
) -> None:
    """Real EventProcessor skips emission when message_id is in dedup set.

    Given: A real ``EventProcessor`` with ``displayed_message_ids`` pre-populated
        with ``"msg_dup"``.
    When: A ``UserMessageInsertedEvent`` with ``message_id="msg_dup"`` is processed.
    Then: The processor yields nothing (dedup skip).
    """
    # GIVEN: EventProcessor with dedup set pre-populated
    dedup_set: set[str] = {"msg_dup"}
    processor = EventProcessor(displayed_message_ids=dedup_set)
    ctx = _make_ctx(server_state)

    # WHEN: UserMessageInsertedEvent with duplicate message_id
    event = UserMessageInsertedEvent(
        session_id="s1",
        message_id="msg_dup",
        content="dup",
        delivery="steer",
        source="background_task",
    )
    events = [e async for e in processor.process(event, ctx)]

    # THEN: No events yielded (dedup skip)
    assert events == []


# =============================================================================
# Test 14: Real EventProcessor handles multimodal content list
# =============================================================================


@pytest.mark.asyncio
async def test_event_processor_content_list_multimodal(
    server_state: ServerState,
) -> None:
    """Real EventProcessor handles list content with text and non-text items.

    Given: A real ``EventProcessor`` and a ``UserMessageInsertedEvent`` with
        content as a list containing a text string and an image dict.
    When: ``processor.process(event, ctx)`` is called.
    Then: ``MessageUpdatedEvent`` is yielded, and a ``PartUpdatedEvent`` with
        ``TextPart`` is yielded for the text item. The image dict (no "text"
        key) is silently skipped by the current implementation.
    """
    # GIVEN: Real EventProcessor with no dedup set
    processor = EventProcessor()
    ctx = _make_ctx(server_state)

    # WHEN: UserMessageInsertedEvent with multimodal content list
    event = UserMessageInsertedEvent(
        session_id="s1",
        message_id="msg_multi",
        content=["text part", {"type": "image", "url": "http://example.com/img.png"}],
        delivery="steer",
        source="background_task",
    )
    events = [e async for e in processor.process(event, ctx)]

    # THEN: MessageUpdatedEvent yielded
    msg_events = [e for e in events if isinstance(e, MessageUpdatedEvent)]
    assert len(msg_events) == 1
    assert msg_events[0].properties.info.id == "msg_multi"
    assert msg_events[0].properties.info.role == "user"

    # AND: PartUpdatedEvent yielded for the text part only
    # (image dict without "text" key is skipped by _process_user_message_inserted)
    part_events = [e for e in events if isinstance(e, PartUpdatedEvent)]
    assert len(part_events) == 1
    part = part_events[0].properties.part
    assert isinstance(part, TextPart)
    assert part.text == "text part"

    # AND: Session state has the message with one text part
    messages = server_state.messages.get("test-session", [])
    assert len(messages) == 1
    assert len(messages[0].parts) == 1
