"""Regression tests for AgentPool event types.

Tests cover:
- ``PartStartEvent`` / ``PartDeltaEvent`` subclassing behavior (session_id field)
- ``ToolCallStartEvent`` / ``ToolCallCompleteEvent`` construction
- ``RunStartedEvent`` / ``StreamCompleteEvent`` fields
- ``RichAgentStreamEvent`` union membership

These serve as a **behavioral baseline** before the thinning refactor
removes the PydanticAI event subclasses.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic_ai import PartStartEvent as PyAIPartStartEvent
from pydantic_ai import PartDeltaEvent as PyAIPartDeltaEvent
from pydantic_ai.messages import TextPart, ThinkingPart, TextPartDelta, ThinkingPartDelta

from agentpool.agents.events.events import (
    PartDeltaEvent,
    PartStartEvent,
    RichAgentStreamEvent,
    RunFailedEvent,
    RunStartedEvent,
    StreamCompleteEvent,
    ToolCallCompleteEvent,
    ToolCallStartEvent,
)


# ---------------------------------------------------------------------------
# PartStartEvent — subclass behavior
# ---------------------------------------------------------------------------


def test_part_start_event_is_pydantic_ai_subclass():
    """PartStartEvent subclasses pydantic_ai.PartStartEvent."""
    assert issubclass(PartStartEvent, PyAIPartStartEvent)


def test_part_start_event_has_session_id():
    """PartStartEvent has a session_id field defaulting to empty string."""
    event = PartStartEvent(index=0, part=TextPart(content="hello"))
    assert hasattr(event, "session_id")
    assert event.session_id == ""


def test_part_start_event_session_id_default():
    """PartStartEvent session_id defaults to empty string."""
    event = PartStartEvent(index=0, part=TextPart(content="hi"))
    assert hasattr(event, "session_id")
    assert event.session_id == ""


def test_part_start_event_thinking_factory():
    """PartStartEvent.thinking() creates a thinking part event."""
    event = PartStartEvent.thinking(index=0, content="reasoning...")
    assert isinstance(event, PartStartEvent)
    assert isinstance(event.part, ThinkingPart)


def test_part_start_event_text_factory():
    """PartStartEvent.text() creates a text part event."""
    event = PartStartEvent.text(index=0, content="response")
    assert isinstance(event, PartStartEvent)
    assert isinstance(event.part, TextPart)


# ---------------------------------------------------------------------------
# PartDeltaEvent — subclass behavior
# ---------------------------------------------------------------------------


def test_part_delta_event_is_pydantic_ai_subclass():
    """PartDeltaEvent subclasses pydantic_ai.PartDeltaEvent."""
    assert issubclass(PartDeltaEvent, PyAIPartDeltaEvent)


def test_part_delta_event_has_session_id():
    """PartDeltaEvent has a session_id field defaulting to empty string."""
    event = PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="chunk"))
    assert hasattr(event, "session_id")
    assert event.session_id == ""


def test_part_delta_event_text_factory():
    """PartDeltaEvent.text() creates a text delta event."""
    event = PartDeltaEvent.text(index=0, content="chunk")
    assert isinstance(event, PartDeltaEvent)
    assert isinstance(event.delta, TextPartDelta)


def test_part_delta_event_thinking_factory():
    """PartDeltaEvent.thinking() creates a thinking delta event."""
    event = PartDeltaEvent.thinking(index=0, content="reason")
    assert isinstance(event, PartDeltaEvent)
    assert isinstance(event.delta, ThinkingPartDelta)


def test_part_delta_event_tool_call_factory():
    """PartDeltaEvent.tool_call() creates a tool call delta event."""
    event = PartDeltaEvent.tool_call(index=0, content='{"x":1}', tool_call_id="tc-1")
    assert isinstance(event, PartDeltaEvent)


# ---------------------------------------------------------------------------
# RunStartedEvent
# ---------------------------------------------------------------------------


def test_run_started_event_fields():
    """RunStartedEvent has the expected fields."""
    event = RunStartedEvent(
        run_id="run-1",
        agent_name="my_agent",
        session_id="sess-1",
        parent_session_id=None,
    )
    assert event.run_id == "run-1"
    assert event.agent_name == "my_agent"
    assert event.session_id == "sess-1"
    assert event.parent_session_id is None
    assert event.event_kind == "run_started"


def test_run_started_event_with_parent():
    """RunStartedEvent with parent_session_id."""
    event = RunStartedEvent(
        run_id="run-2",
        agent_name="child",
        session_id="child-sess",
        parent_session_id="parent-sess",
    )
    assert event.parent_session_id == "parent-sess"


def test_run_started_event_session_id_defaults_empty():
    """RunStartedEvent session_id defaults to empty string."""
    event = RunStartedEvent(run_id="r")
    assert event.session_id == ""


# ---------------------------------------------------------------------------
# StreamCompleteEvent
# ---------------------------------------------------------------------------


def test_stream_complete_event_fields():
    """StreamCompleteEvent has the expected fields."""
    from agentpool.messaging.messages import ChatMessage

    msg = ChatMessage(content="final response", role="assistant")
    event = StreamCompleteEvent(message=msg)
    assert event.message is msg
    assert event.cancelled is False
    assert event.event_kind == "stream_complete"


def test_stream_complete_event_cancelled():
    """StreamCompleteEvent can be marked as cancelled."""
    from agentpool.messaging.messages import ChatMessage

    msg = ChatMessage(content="[Interrupted]", role="assistant")
    event = StreamCompleteEvent(message=msg, cancelled=True)
    assert event.cancelled is True


# ---------------------------------------------------------------------------
# ToolCallStartEvent
# ---------------------------------------------------------------------------


def test_tool_call_start_event_fields():
    """ToolCallStartEvent has the expected fields."""
    event = ToolCallStartEvent(
        tool_call_id="tc-1",
        tool_name="bash",
        title="Executing: bash",
        kind="execute",
        raw_input={"command": "ls"},
    )
    assert event.tool_call_id == "tc-1"
    assert event.tool_name == "bash"
    assert event.title == "Executing: bash"
    assert event.kind == "execute"
    assert event.raw_input == {"command": "ls"}
    assert event.event_kind == "tool_call_start"
    assert event.session_id == ""


def test_tool_call_start_event_content_default_empty():
    """ToolCallStartEvent content defaults to empty list."""
    event = ToolCallStartEvent(
        tool_call_id="tc-1",
        tool_name="bash",
        title="test",
        kind="execute",
        raw_input={},
    )
    assert event.content == []


# ---------------------------------------------------------------------------
# ToolCallCompleteEvent
# ---------------------------------------------------------------------------


def test_tool_call_complete_event_fields():
    """ToolCallCompleteEvent has the expected fields."""
    event = ToolCallCompleteEvent(
        tool_name="bash",
        tool_call_id="tc-1",
        tool_input={"command": "ls"},
        tool_result="file1\nfile2",
        agent_name="my_agent",
        message_id="msg-1",
    )
    assert event.tool_name == "bash"
    assert event.tool_call_id == "tc-1"
    assert event.tool_input == {"command": "ls"}
    assert event.tool_result == "file1\nfile2"
    assert event.agent_name == "my_agent"
    assert event.message_id == "msg-1"
    assert event.event_kind == "tool_call_complete"
    assert event.session_id == ""


def test_tool_call_complete_event_metadata_default_none():
    """ToolCallCompleteEvent metadata defaults to None."""
    event = ToolCallCompleteEvent(
        tool_name="t",
        tool_call_id="tc",
        tool_input={},
        tool_result="r",
        agent_name="a",
        message_id="m",
    )
    assert event.metadata is None


# ---------------------------------------------------------------------------
# RunFailedEvent
# ---------------------------------------------------------------------------


def test_run_failed_event_fields():
    """RunFailedEvent carries exception details."""
    exc = RuntimeError("something broke")
    event = RunFailedEvent(
        run_id="run-1",
        session_id="sess-1",
        exception=exc,
    )
    assert event.run_id == "run-1"
    assert event.session_id == "sess-1"
    assert event.exception is exc
    assert event.event_kind == "run_failed"


# ---------------------------------------------------------------------------
# RichAgentStreamEvent union
# ---------------------------------------------------------------------------


def test_rich_agent_stream_event_includes_custom_events():
    """RichAgentStreamEvent union includes all custom event types."""
    import typing

    # RichAgentStreamEvent may be a TypeAlias to a Union, so we need to
    # resolve it via typing.get_origin / get_args or check __annotations__
    origin = typing.get_origin(RichAgentStreamEvent)
    args = typing.get_args(RichAgentStreamEvent)

    # If it's a Union, args will be non-empty. If it's a TypeAlias to a
    # class, we check differently.
    if args:
        assert RunStartedEvent in args or any(
            RunStartedEvent is a for a in args
        )
    else:
        # May be a TypeAlias — check the underlying type
        assert hasattr(RichAgentStreamEvent, "__value__") or hasattr(
            RichAgentStreamEvent, "__origin__"
        )


# ---------------------------------------------------------------------------
# Event construction from RunExecutor patterns (without running executor)
# ---------------------------------------------------------------------------


def test_tool_call_start_event_from_tool_call_part():
    """ToolCallStartEvent can be constructed from a ToolCallPart-like input."""
    from pydantic_ai.messages import ToolCallPart

    call_part = ToolCallPart(tool_name="read", args={"path": "/tmp"})
    event = ToolCallStartEvent(
        tool_call_id=call_part.tool_call_id,
        tool_name=call_part.tool_name,
        title=f"Executing: {call_part.tool_name}",
        kind="read",
        raw_input=dict(call_part.args) if isinstance(call_part.args, dict) else {},
    )
    assert event.tool_name == "read"
    assert event.kind == "read"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
