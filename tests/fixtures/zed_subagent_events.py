"""Mock zed-mode subagent events for ACP event converter snapshot tests.

These fixtures extend the base subagent events with SpawnSessionStart
events, which are emitted before subagent streams begin in zed mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_ai import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolReturnPart,
)

from agentpool.agents.events import (
    PartDeltaEvent,
    PartStartEvent,
    SpawnSessionStart,
    StreamCompleteEvent,
    SubAgentEvent,
)


if TYPE_CHECKING:
    from collections.abc import Callable


def _get_text_start_event(text: str, index: int = 0) -> PartStartEvent:
    """Create a text part start event."""
    return PartStartEvent(index=index, part=TextPart(content=text))


def _get_text_delta_event(delta: str, index: int = 0) -> PartDeltaEvent:
    """Create a text part delta event."""
    return PartDeltaEvent(index=index, delta=TextPartDelta(content_delta=delta))


def _get_thinking_start_event(thinking: str, index: int = 0) -> PartStartEvent:
    """Create a thinking part start event."""
    return PartStartEvent(index=index, part=ThinkingPart(content=thinking))


def _get_thinking_delta_event(delta: str, index: int = 0) -> PartDeltaEvent:
    """Create a thinking part delta event."""
    return PartDeltaEvent(index=index, delta=ThinkingPartDelta(content_delta=delta))


def _get_tool_call_start_event(
    tool_name: str,
    tool_call_id: str,
    args: dict[str, Any],
) -> FunctionToolCallEvent:
    """Create a function tool call start event."""
    part = ToolCallPart(
        tool_name=tool_name,
        args=args,
        tool_call_id=tool_call_id,
    )
    return FunctionToolCallEvent(part=part)


def _get_tool_result_event(
    tool_name: str,
    tool_call_id: str,
    result: str,
) -> FunctionToolResultEvent:
    """Create a function tool result event."""
    part = ToolReturnPart(content=result, tool_name=tool_name)
    return FunctionToolResultEvent(result=part)


def _get_stream_complete_event() -> StreamCompleteEvent[Any]:
    """Create a stream complete event."""
    from agentpool.messaging import ChatMessage

    message = ChatMessage(
        role="assistant",
        content="Test complete",
        model_name="test-model",
    )
    return StreamCompleteEvent(message=message)


def _get_subagent_event(
    source_name: str,
    inner_event: Any,
    source_type: str = "agent",
    depth: int = 1,
) -> SubAgentEvent:
    """Wrap an event in a SubAgentEvent."""
    return SubAgentEvent(
        source_name=source_name,
        source_type=source_type,  # type: ignore[arg-type]
        event=inner_event,
        depth=depth,
    )


def _get_spawn_session_start_event(
    source_name: str = "assistant",
    source_type: str = "agent",
    depth: int = 1,
    child_session_id: str = "child_sess_001",
    parent_session_id: str = "parent_sess_001",
    description: str = "Spawning subagent",
    spawn_mechanism: str = "task",
) -> SpawnSessionStart:
    """Create a SpawnSessionStart event."""
    return SpawnSessionStart(
        source_name=source_name,
        source_type=source_type,  # type: ignore[arg-type]
        depth=depth,
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
        description=description,
        spawn_mechanism=spawn_mechanism,  # type: ignore[arg-type]
    )


def zed_text_stream_events(
    source_name: str = "assistant",
) -> list[SubAgentEvent | SpawnSessionStart]:
    """Zed text stream with SpawnSessionStart prefix.

    Events:
    1. SpawnSessionStart: "Starting assistant subagent"
    2. Text start: "Hello"
    3. Text delta: " world"
    4. Text delta: "!"
    5. Stream complete
    """
    return [
        _get_spawn_session_start_event(
            source_name=source_name,
            description=f"Starting {source_name} subagent",
        ),
        _get_subagent_event(
            source_name=source_name,
            inner_event=_get_text_start_event("Hello"),
        ),
        _get_subagent_event(
            source_name=source_name,
            inner_event=_get_text_delta_event(" world"),
        ),
        _get_subagent_event(
            source_name=source_name,
            inner_event=_get_text_delta_event("!"),
        ),
        _get_subagent_event(
            source_name=source_name,
            inner_event=_get_stream_complete_event(),
        ),
    ]


def zed_thinking_stream_events(
    source_name: str = "researcher",
) -> list[SubAgentEvent | SpawnSessionStart]:
    """Zed thinking stream with SpawnSessionStart prefix.

    Events:
    1. SpawnSessionStart: "Starting researcher subagent"
    2. Thinking start: "Analyzing"
    3. Thinking delta: " the"
    4. Thinking delta: " problem"
    5. Stream complete
    """
    return [
        _get_spawn_session_start_event(
            source_name=source_name,
            description=f"Starting {source_name} subagent",
        ),
        _get_subagent_event(
            source_name=source_name,
            inner_event=_get_thinking_start_event("Analyzing"),
        ),
        _get_subagent_event(
            source_name=source_name,
            inner_event=_get_thinking_delta_event(" the"),
        ),
        _get_subagent_event(
            source_name=source_name,
            inner_event=_get_thinking_delta_event(" problem"),
        ),
        _get_subagent_event(
            source_name=source_name,
            inner_event=_get_stream_complete_event(),
        ),
    ]


def zed_tool_call_events(source_name: str = "coder") -> list[SubAgentEvent | SpawnSessionStart]:
    """Zed tool call stream with SpawnSessionStart prefix.

    Events:
    1. SpawnSessionStart: "Starting coder subagent"
    2. Text start: "I'll search for files"
    3. Tool call start: "search" with args={"pattern": "*.py"}
    4. Tool result: "Found 3 files"
    5. Stream complete
    """
    return [
        _get_spawn_session_start_event(
            source_name=source_name,
            description=f"Starting {source_name} subagent",
        ),
        _get_subagent_event(
            source_name=source_name,
            inner_event=_get_text_start_event("I'll search for files"),
        ),
        _get_subagent_event(
            source_name=source_name,
            inner_event=_get_tool_call_start_event(
                tool_name="search",
                tool_call_id="call_001",
                args={"pattern": "*.py"},
            ),
        ),
        _get_subagent_event(
            source_name=source_name,
            inner_event=_get_tool_result_event(
                tool_name="search",
                tool_call_id="call_001",
                result="Found 3 files",
            ),
        ),
        _get_subagent_event(
            source_name=source_name,
            inner_event=_get_stream_complete_event(),
        ),
    ]


def zed_mixed_events(source_name: str = "analyzer") -> list[SubAgentEvent | SpawnSessionStart]:
    """Zed mixed events with SpawnSessionStart prefix.

    Events:
    1. SpawnSessionStart: "Starting analyzer subagent"
    2. Thinking start: "Need to analyze"
    3. Text start: "Let me check"
    4. Tool call start: "grep" with args={"pattern": "error"}
    5. Tool result: "No errors found"
    6. Text delta: " - all good!"
    7. Stream complete
    """
    return [
        _get_spawn_session_start_event(
            source_name=source_name,
            description=f"Starting {source_name} subagent",
        ),
        _get_subagent_event(
            source_name=source_name,
            inner_event=_get_thinking_start_event("Need to analyze"),
        ),
        _get_subagent_event(
            source_name=source_name,
            inner_event=_get_text_start_event("Let me check"),
        ),
        _get_subagent_event(
            source_name=source_name,
            inner_event=_get_tool_call_start_event(
                tool_name="grep",
                tool_call_id="call_002",
                args={"pattern": "error"},
            ),
        ),
        _get_subagent_event(
            source_name=source_name,
            inner_event=_get_tool_result_event(
                tool_name="grep",
                tool_call_id="call_002",
                result="No errors found",
            ),
        ),
        _get_subagent_event(
            source_name=source_name,
            inner_event=_get_text_delta_event(" - all good!"),
        ),
        _get_subagent_event(
            source_name=source_name,
            inner_event=_get_stream_complete_event(),
        ),
    ]


# Zed-mode event sequences keyed by test name
ZED_TEST_EVENT_SEQUENCES: dict[str, Callable[..., list[SubAgentEvent | SpawnSessionStart]]] = {
    "zed_text_stream": zed_text_stream_events,
    "zed_thinking_stream": zed_thinking_stream_events,
    "zed_tool_call": zed_tool_call_events,
    "zed_mixed_events": zed_mixed_events,
}
