from __future__ import annotations

from unittest.mock import MagicMock

from agentpool.agents.events import PartDeltaEvent, PartStartEvent
from agentpool_server.opencode_server.models import PartUpdatedEvent
from agentpool_server.opencode_server.models.events import PartUpdatedEventProperties
from agentpool_server.opencode_server.models.parts import ReasoningPart
from agentpool_server.opencode_server.stream_adapter import OpenCodeStreamAdapter


def test_thinking_events_create_reasoning_part():
    """Verify ThinkingPart/ThinkingPartDelta events create ReasoningPart."""
    # Create a mock MessageWithParts
    mock_msg = MagicMock()
    mock_msg.parts = []

    adapter = OpenCodeStreamAdapter(assistant_msg=mock_msg, working_dir=".")
    # Use the adapter's _handle_event method directly
    events = list(adapter._handle_event(PartStartEvent.thinking(index=0, content="Thinking...")))
    events.extend(list(adapter._handle_event(PartDeltaEvent.thinking(index=0, content=" more..."))))

    # Assert reasoning part was created
    # Based on models/events.py, PartUpdatedEvent has properties.part
    reasoning_events = []
    for e in events:
        match e:
            case PartUpdatedEvent(properties=PartUpdatedEventProperties(part=ReasoningPart())):
                reasoning_events.append(e)

    assert len(reasoning_events) >= 1, "ReasoningPart should be created from thinking events"
    first_part = reasoning_events[0].properties.part
    last_part = reasoning_events[-1].properties.part
    assert isinstance(first_part, ReasoningPart)
    assert isinstance(last_part, ReasoningPart)
    assert "Thinking..." in first_part.text
    assert " more..." in last_part.text
