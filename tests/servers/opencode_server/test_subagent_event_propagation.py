"""Tests demonstrating that subagent event propagation is broken.

This test file demonstrates the bug where PartDeltaEvent and TextPartDelta
from subagents are lost and not propagated to the child session.

The _on_subagent() method in OpenCodeStreamAdapter handles:
- RunStartedEvent - creates ToolPart
- StreamCompleteEvent - creates messages in child session
- ToolCallCompleteEvent - handles tool results

But it does NOT handle PartDeltaEvent, so streaming text from subagents is lost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic_ai.messages import PartDeltaEvent, TextPartDelta

from agentpool.agents.events import SubAgentEvent
from agentpool_server.opencode_server.models import MessagePath, MessageTime, MessageWithParts
from agentpool_server.opencode_server.models.parts import TextPart as OpenCodeTextPart
from agentpool_server.opencode_server.stream_adapter import OpenCodeStreamAdapter


if TYPE_CHECKING:
    from agentpool_server.opencode_server.state import ServerState


@pytest.mark.asyncio
async def test_part_delta_currently_lost(server_state: ServerState) -> None:
    """Demonstrate that PartDeltaEvent from subagents is lost (BUG).

    This test shows that when a SubAgentEvent wraps a PartDeltaEvent with
    TextPartDelta, the text content is NOT propagated to the child session.

    The current implementation only handles RunStartedEvent, StreamCompleteEvent,
    and ToolCallCompleteEvent in _on_subagent(), but NOT PartDeltaEvent.

    Expected behavior: The text delta "Hello from subagent" should appear
    in the child session's messages (currently it doesn't - this is the bug).
    """
    # Setup
    session_id = "parent-session"
    child_session_id = "child-session"

    # Create assistant message via factory
    assistant_msg = MessageWithParts.assistant(
        message_id="msg-1",
        session_id=session_id,
        time=MessageTime(created=1000),
        agent_name="test-agent",
        model_id="test-model",
        parent_id="user-msg-1",
        provider_id="test-provider",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )

    adapter = OpenCodeStreamAdapter(
        state=server_state,
        session_id=session_id,
        assistant_msg_id="msg-1",
        assistant_msg=assistant_msg,
        working_dir="/tmp",
    )

    # Create a stream with a SubAgentEvent wrapping a PartDeltaEvent
    async def event_stream():
        # PartDeltaEvent with TextPartDelta - this is the streaming content
        # from the subagent that should be propagated to the child session
        inner_delta_event = PartDeltaEvent(
            index=0,
            delta=TextPartDelta(content_delta="Hello from subagent"),
        )

        yield SubAgentEvent(
            source_name="subagent",
            source_type="agent",
            event=inner_delta_event,
            depth=0,
            child_session_id=child_session_id,
            parent_session_id=session_id,
        )

    # Run process_stream and collect events
    events = []
    async for event in adapter.process_stream(event_stream()):
        events.append(event)

    # Verify that child session has been created
    assert child_session_id in server_state.sessions, "Child session should exist"

    # The BUG: PartDeltaEvent content should be in child session's messages
    # but currently it is NOT because _on_subagent doesn't handle PartDeltaEvent

    # Check if messages exist in child session
    child_messages = server_state.messages.get(child_session_id, [])

    # Collect all text content from child session messages
    all_text_content = []
    for msg in child_messages:
        for part in msg.parts:
            match part:
                case OpenCodeTextPart(text=text):
                    all_text_content.append(text)

    # This assertion demonstrates the bug:
    # The text "Hello from subagent" SHOULD appear in child session messages
    # but currently it doesn't because PartDeltaEvent is not handled
    combined_text = " ".join(all_text_content)

    # This assertion will FAIL initially, demonstrating the bug
    # After the bug is fixed, this should pass
    assert "Hello from subagent" in combined_text, (
        f"BUG: PartDeltaEvent content 'Hello from subagent' was not propagated "
        f"to child session. Child session messages contain: {combined_text!r}"
    )
