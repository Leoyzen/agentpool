"""Unit tests for EnqueuedMessagesEvent → UserMessageInsertedEvent mapping.

Tests the ``handle_enqueued_messages`` method on :class:`EventMapper`,
which maps pydantic-ai's ``EnqueuedMessagesEvent`` to AgentPool's
:class:`UserMessageInsertedEvent` with delivery inference based on
the current node type.
"""

from __future__ import annotations

from pydantic_ai.messages import (
    EnqueuedMessagesEvent,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
import pytest

from agentpool.agents.events.events import UserMessageInsertedEvent
from agentpool.orchestrator.event_mapper import EventMapper


def _make_enqueued_event(content: str = "Hello, steer me!") -> EnqueuedMessagesEvent:
    """Build an EnqueuedMessagesEvent with a single ModelRequest containing a UserPromptPart."""
    return EnqueuedMessagesEvent(
        enqueue_id="eq-001",
        messages=(ModelRequest(parts=[UserPromptPart(content=content)]),),
    )


@pytest.mark.unit
def test_enqueued_messages_steer_delivery_for_model_request_node() -> None:
    """EnqueuedMessagesEvent during ModelRequestNode → delivery='steer'."""
    mapper = EventMapper(agent_name="test-agent", message_id="msg-001")
    event = _make_enqueued_event("Steer this conversation!")

    result = mapper.map_event(event, current_node_type="ModelRequestNode")

    assert result is not None
    assert isinstance(result, UserMessageInsertedEvent)
    assert result.delivery == "steer"
    assert result.source == "internal"
    assert result.content == "Steer this conversation!"
    assert result.message_id  # non-empty UUID string
    assert result.session_id == ""
    assert result.meta is None


@pytest.mark.unit
def test_enqueued_messages_followup_delivery_for_call_tools_node() -> None:
    """EnqueuedMessagesEvent during CallToolsNode → delivery='followup'."""
    mapper = EventMapper(agent_name="test-agent", message_id="msg-001")
    event = _make_enqueued_event("Followup message")

    result = mapper.map_event(event, current_node_type="CallToolsNode")

    assert result is not None
    assert isinstance(result, UserMessageInsertedEvent)
    assert result.delivery == "followup"
    assert result.source == "internal"
    assert result.content == "Followup message"


@pytest.mark.unit
def test_enqueued_messages_followup_delivery_for_end_node() -> None:
    """EnqueuedMessagesEvent during End node → delivery='followup'."""
    mapper = EventMapper(agent_name="test-agent", message_id="msg-001")
    event = _make_enqueued_event("After-turn message")

    result = mapper.map_event(event, current_node_type="End")

    assert result is not None
    assert isinstance(result, UserMessageInsertedEvent)
    assert result.delivery == "followup"
    assert result.content == "After-turn message"


@pytest.mark.unit
def test_enqueued_messages_unknown_node_type_defaults_to_steer() -> None:
    """EnqueuedMessagesEvent with unknown node type defaults to delivery='steer'."""
    mapper = EventMapper(agent_name="test-agent", message_id="msg-001")
    event = _make_enqueued_event("Unknown context")

    result = mapper.map_event(event, current_node_type="unknown")

    assert result is not None
    assert isinstance(result, UserMessageInsertedEvent)
    assert result.delivery == "steer"


@pytest.mark.unit
def test_enqueued_messages_empty_messages_returns_none() -> None:
    """EnqueuedMessagesEvent with empty messages tuple returns None."""
    mapper = EventMapper(agent_name="test-agent", message_id="msg-001")
    event = EnqueuedMessagesEvent(enqueue_id="eq-empty", messages=())

    result = mapper.map_event(event, current_node_type="ModelRequestNode")

    assert result is None


@pytest.mark.unit
def test_enqueued_messages_no_user_prompt_part_returns_none() -> None:
    """EnqueuedMessagesEvent containing only ModelResponse (no UserPromptPart) returns None."""
    mapper = EventMapper(agent_name="test-agent", message_id="msg-001")
    event = EnqueuedMessagesEvent(
        enqueue_id="eq-no-user",
        messages=(ModelResponse(parts=[TextPart(content="Assistant response")]),),
    )

    result = mapper.map_event(event, current_node_type="ModelRequestNode")

    assert result is None
