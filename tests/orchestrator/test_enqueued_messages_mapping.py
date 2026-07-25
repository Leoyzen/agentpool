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


# ---------------------------------------------------------------------------
# FIFO message_id reuse tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_enqueued_messages_reuses_message_id_from_fifo_queue() -> None:
    """handle_enqueued_messages() reuses message_id from _enqueue_message_ids
    FIFO queue instead of generating a new UUID.
    """  # noqa: D205
    fifo: list[str] = ["steer-msg-123"]
    mapper = EventMapper(
        agent_name="test-agent",
        message_id="msg-001",
        _enqueue_message_ids=fifo,
    )
    event = _make_enqueued_event("Steer content")

    result = mapper.map_event(event, current_node_type="ModelRequestNode")

    assert result is not None
    assert isinstance(result, UserMessageInsertedEvent)
    assert result.message_id == "steer-msg-123"
    # FIFO should be drained.
    assert len(fifo) == 0


@pytest.mark.unit
def test_enqueued_messages_generates_uuid_when_fifo_empty() -> None:
    """handle_enqueued_messages() generates a new UUID when the FIFO queue
    is empty (no steer/followup preceded the enqueue).
    """  # noqa: D205
    mapper = EventMapper(
        agent_name="test-agent",
        message_id="msg-001",
        _enqueue_message_ids=[],
    )
    event = _make_enqueued_event("Spontaneous enqueue")

    result = mapper.map_event(event, current_node_type="ModelRequestNode")

    assert result is not None
    assert isinstance(result, UserMessageInsertedEvent)
    assert result.message_id  # non-empty
    assert result.message_id != "msg-001"  # not the mapper's internal ID


@pytest.mark.unit
def test_enqueued_messages_fifo_pop_order() -> None:
    """Multiple message_ids in FIFO are popped in FIFO order (first in, first out)."""
    fifo: list[str] = ["msg-a", "msg-b", "msg-c"]
    mapper = EventMapper(
        agent_name="test-agent",
        message_id="msg-001",
        _enqueue_message_ids=fifo,
    )

    result1 = mapper.map_event(_make_enqueued_event("first"), current_node_type="ModelRequestNode")
    result2 = mapper.map_event(_make_enqueued_event("second"), current_node_type="ModelRequestNode")
    result3 = mapper.map_event(_make_enqueued_event("third"), current_node_type="ModelRequestNode")

    assert result1 is not None
    assert result2 is not None
    assert result3 is not None
    assert result1.message_id == "msg-a"
    assert result2.message_id == "msg-b"
    assert result3.message_id == "msg-c"
    assert len(fifo) == 0
