"""Tests for reasoning/thinking part behavior in OpenCode stream adapter."""

from typing import cast
from unittest.mock import MagicMock

from pydantic_ai.messages import (
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)
from agentpool_server.opencode_server.models import PartUpdatedEvent
from agentpool_server.opencode_server.models.events import PartUpdatedEventProperties
from agentpool_server.opencode_server.models.parts import (
    ReasoningPart,
    TextPart as OpenCodeTextPart,
)
from agentpool_server.opencode_server.stream_adapter import OpenCodeStreamAdapter


def test_thinking_events_create_reasoning_part():
    """Verify ThinkingPart/ThinkingPartDelta events create ReasoningPart."""
    # Create a mock MessageWithParts
    mock_msg = MagicMock()
    mock_msg.parts = []

    adapter = OpenCodeStreamAdapter(
        session_id="test-session",
        assistant_msg_id="msg-1",
        assistant_msg=mock_msg,
        working_dir=".",
    )

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
    # Cast to narrow type since we've already checked it's a ReasoningPart
    first_part = cast(ReasoningPart, reasoning_events[0].properties.part)
    last_part = cast(ReasoningPart, reasoning_events[-1].properties.part)
    assert "Thinking..." in first_part.text
    assert " more..." in last_part.text


def test_multi_turn_thinking_creates_separate_parts():
    """Verify that multiple thinking phases create separate ReasoningParts.

    This tests the fix for: "Multi-turn conversation thinking displayed in single block"
    Each thinking phase should be its own Part with its own ID.
    """
    # Create a mock MessageWithParts
    mock_msg = MagicMock()
    mock_msg.parts = []

    adapter = OpenCodeStreamAdapter(
        session_id="test-session",
        assistant_msg_id="msg-1",
        assistant_msg=mock_msg,
        working_dir=".",
    )

    events = []

    # Simulate multi-turn conversation with thinking in each turn:
    # Turn 1: Thinking -> Text
    events.extend(
        list(
            adapter._handle_event(
                PartStartEvent(index=0, part=ThinkingPart(content="First thinking..."))
            )
        )
    )
    events.extend(
        list(
            adapter._handle_event(
                PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta=" more thinking"))
            )
        )
    )
    # End of thinking - text response starts
    events.extend(
        list(
            adapter._handle_event(PartStartEvent(index=1, part=TextPart(content="First response")))
        )
    )

    # Turn 2: Thinking -> Text (new turn, should be separate Part)
    events.extend(
        list(
            adapter._handle_event(
                PartStartEvent(index=2, part=ThinkingPart(content="Second turn thinking..."))
            )
        )
    )
    events.extend(
        list(
            adapter._handle_event(
                PartDeltaEvent(index=2, delta=ThinkingPartDelta(content_delta=" more"))
            )
        )
    )
    # End of thinking - text response starts
    events.extend(
        list(
            adapter._handle_event(
                PartDeltaEvent(index=3, delta=TextPartDelta(content_delta="Second response"))
            )
        )
    )

    # Turn 3: Thinking (should be third separate Part)
    events.extend(
        list(
            adapter._handle_event(
                PartStartEvent(index=4, part=ThinkingPart(content="Third turn thinking..."))
            )
        )
    )

    # Extract ReasoningParts from events
    reasoning_parts = []
    for e in events:
        if isinstance(e, PartUpdatedEvent):
            props = e.properties
            if isinstance(props, PartUpdatedEventProperties) and isinstance(
                props.part, ReasoningPart
            ):
                reasoning_parts.append(props.part)

    # Extract TextParts to verify they were created correctly
    text_parts = []
    for e in events:
        if isinstance(e, PartUpdatedEvent):
            props = e.properties
            if isinstance(props, PartUpdatedEventProperties) and isinstance(
                props.part, OpenCodeTextPart
            ):
                text_parts.append(props.part)

    # Assertions
    # We need to check that there are 3 unique reasoning phases (unique Part IDs)
    # Each thinking start creates a new Part, and deltas update the same Part
    unique_reasoning_parts = {}
    for p in reasoning_parts:
        unique_reasoning_parts[p.id] = p

    # We should have 3 separate ReasoningParts (one for each thinking phase)
    assert len(unique_reasoning_parts) >= 3, (
        f"Expected at least 3 unique ReasoningParts (one per thinking phase), "
        f"got {len(unique_reasoning_parts)} unique IDs from {len(reasoning_parts)} events"
    )

    # Get the unique parts (one per thinking phase) sorted by creation order
    unique_parts_list = list(unique_reasoning_parts.values())

    # Verify the content is not accumulated across turns
    # Each unique part should represent one thinking phase
    first_thinking = unique_parts_list[0].text
    second_thinking = unique_parts_list[1].text if len(unique_parts_list) > 1 else ""
    third_thinking = unique_parts_list[2].text if len(unique_parts_list) > 2 else ""

    # Each thinking should only have that turn's content
    assert "First thinking..." in first_thinking, (
        f"First thinking content missing: {first_thinking}"
    )
    assert "Second turn thinking..." in second_thinking, (
        f"Second thinking content missing: {second_thinking}"
    )
    assert "Third turn thinking..." in third_thinking, (
        f"Third thinking content missing: {third_thinking}"
    )

    # Verify no cross-contamination - second thinking shouldn't have first thinking's content
    assert "First thinking" not in second_thinking, (
        f"Second thinking has first turn's content: {second_thinking}"
    )
    assert "First thinking" not in third_thinking, (
        f"Third thinking has first turn's content: {third_thinking}"
    )

    # Verify text parts were created correctly
    assert len(text_parts) >= 1, f"Expected at least 1 TextPart, got {len(text_parts)}"


def test_single_thinking_phase_accumulates_correctly():
    """Verify that a single thinking phase still accumulates correctly."""
    mock_msg = MagicMock()
    mock_msg.parts = []

    adapter = OpenCodeStreamAdapter(
        session_id="test-session",
        assistant_msg_id="msg-1",
        assistant_msg=mock_msg,
        working_dir=".",
    )

    events = []

    # Single thinking phase with multiple deltas
    events.extend(
        list(adapter._handle_event(PartStartEvent(index=0, part=ThinkingPart(content="Start "))))
    )
    events.extend(
        list(
            adapter._handle_event(
                PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta="middle "))
            )
        )
    )
    events.extend(
        list(
            adapter._handle_event(
                PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta="end"))
            )
        )
    )

    # Extract ReasoningParts
    reasoning_parts = []
    for e in events:
        if isinstance(e, PartUpdatedEvent):
            props = e.properties
            if isinstance(props, PartUpdatedEventProperties) and isinstance(
                props.part, ReasoningPart
            ):
                reasoning_parts.append(props.part)

    # Should have 1 part that accumulated all content
    assert len(reasoning_parts) >= 1, (
        f"Expected at least 1 ReasoningPart, got {len(reasoning_parts)}"
    )

    # The content should be accumulated
    final_content = reasoning_parts[-1].text
    expected = "Start middle end"
    assert final_content == expected, f"Expected '{expected}', got '{final_content}'"
