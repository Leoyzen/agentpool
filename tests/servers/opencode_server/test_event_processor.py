"""Tests for the EventProcessor in OpenCode server.

Tests text handling and tool processing.
SubAgentEvent handling was moved to session_pool_integration in commit 4be3dd70b.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.messages import (
    PartDeltaEvent as PydanticPartDeltaEvent,
    PartStartEvent,
    TextPart as PydanticTextPart,
    TextPartDelta,
)
import pytest

from agentpool_server.opencode_server.event_processor import EventProcessor
from agentpool_server.opencode_server.event_processor_context import (
    EventProcessorContext,
)
from agentpool_server.opencode_server.models import (
    MessagePath,
    MessageTime,
    MessageWithParts,
    PartDeltaEvent,
    PartUpdatedEvent,
    TextPart,
)


if TYPE_CHECKING:
    from agentpool_server.opencode_server.state import ServerState


# =============================================================================
# Text Handling Tests
# =============================================================================


@pytest.mark.asyncio
async def test_process_text_start_creates_text_part(server_state: ServerState) -> None:
    """Test that PartStartEvent with PydanticTextPart creates a text part.

    Verifies:
    - EventProcessor yields PartUpdatedEvent
    - context.text_part is set
    - text is in assistant_msg.parts
    """
    # GIVEN: empty context with assistant message
    processor = EventProcessor()
    assistant_msg = MessageWithParts.assistant(
        message_id="msg-1",
        session_id="test-session",
        time=MessageTime(created=0),
        agent_name="test-agent",
        model_id="test-model",
        parent_id="parent-1",
        provider_id="agentpool",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )
    ctx = EventProcessorContext(
        session_id="test-session",
        assistant_msg_id="msg-1",
        assistant_msg=assistant_msg,
        state=server_state,
        working_dir="/tmp",
    )

    # WHEN: PartStartEvent with PydanticTextPart received
    event = PartStartEvent(index=0, part=PydanticTextPart(content="Hello, world!"))
    events = [e async for e in processor.process(event, ctx)]

    # THEN: PartUpdatedEvent is yielded
    assert len(events) == 1
    assert isinstance(events[0], PartUpdatedEvent)

    # AND: context.text_part is set
    assert ctx.text_part is not None
    assert ctx.text_part.text == "Hello, world!"

    # AND: text is in assistant_msg.parts
    assert len(assistant_msg.parts) == 1
    first_part = assistant_msg.parts[0]
    assert isinstance(first_part, TextPart)
    assert first_part.text == "Hello, world!"

    # AND: response_text is accumulated
    assert ctx.response_text == "Hello, world!"


@pytest.mark.asyncio
async def test_process_text_delta_accumulates_text(server_state: ServerState) -> None:
    """Test that PartDeltaEvent accumulates text onto existing text part.

    Verifies:
    - context.response_text accumulates the delta
    - PartUpdatedEvent is yielded
    - text_part is updated with accumulated text
    """
    # GIVEN: text has been started
    processor = EventProcessor()
    assistant_msg = MessageWithParts.assistant(
        message_id="msg-1",
        session_id="test-session",
        time=MessageTime(created=0),
        agent_name="test-agent",
        model_id="test-model",
        parent_id="parent-1",
        provider_id="agentpool",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )
    ctx = EventProcessorContext(
        session_id="test-session",
        assistant_msg_id="msg-1",
        assistant_msg=assistant_msg,
        state=server_state,
        working_dir="/tmp",
    )

    # Start with initial text
    start_event = PartStartEvent(index=0, part=PydanticTextPart(content="Hello, "))
    async for _ in processor.process(start_event, ctx):
        pass

    # WHEN: PartDeltaEvent with TextPartDelta received
    delta_event = PydanticPartDeltaEvent(index=0, delta=TextPartDelta(content_delta="world!"))
    events = [e async for e in processor.process(delta_event, ctx)]

    # THEN: PartDeltaEvent is yielded (not PartUpdatedEvent for deltas)
    assert len(events) == 1
    assert isinstance(events[0], PartDeltaEvent)

    # AND: context.response_text accumulated the delta
    assert ctx.response_text == "Hello, world!"

    # AND: text_part is updated with accumulated text
    assert ctx.text_part is not None
    assert ctx.text_part.text == "Hello, world!"

    # AND: assistant_msg.parts is updated
    assert len(assistant_msg.parts) == 1
    first_part = assistant_msg.parts[0]
    assert isinstance(first_part, TextPart)
    assert first_part.text == "Hello, world!"


@pytest.mark.asyncio
async def test_process_text_delta_without_start(server_state: ServerState) -> None:
    """Test that PartDeltaEvent without prior PartStartEvent creates text part.

    This tests the fallback behavior when delta arrives before start.
    """
    # GIVEN: no text part started yet
    processor = EventProcessor()
    assistant_msg = MessageWithParts.assistant(
        message_id="msg-1",
        session_id="test-session",
        time=MessageTime(created=0),
        agent_name="test-agent",
        model_id="test-model",
        parent_id="parent-1",
        provider_id="agentpool",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )
    ctx = EventProcessorContext(
        session_id="test-session",
        assistant_msg_id="msg-1",
        assistant_msg=assistant_msg,
        state=server_state,
        working_dir="/tmp",
    )

    # WHEN: PartDeltaEvent without prior PartStartEvent
    delta_event = PydanticPartDeltaEvent(index=0, delta=TextPartDelta(content_delta="Some text"))
    events = [e async for e in processor.process(delta_event, ctx)]

    # THEN: PartUpdatedEvent is yielded
    assert len(events) == 1
    assert isinstance(events[0], PartUpdatedEvent)

    # AND: text_part is created with accumulated text
    assert ctx.text_part is not None
    assert ctx.text_part.text == "Some text"

    # AND: assistant_msg.parts contains the text part
    assert len(assistant_msg.parts) == 1
    first_part = assistant_msg.parts[0]
    assert isinstance(first_part, TextPart)
    assert first_part.text == "Some text"
