"""Tests for ACP event converter.

These tests demonstrate how the converter pattern makes testing easy -
no mocks needed, just assert on the yielded ACP session updates.
"""

from __future__ import annotations

from pydantic_ai import (
    FunctionToolCallEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolCallPart,
)
import pytest

from acp.schema import AgentMessageChunk, ToolCallProgress, ToolCallStart
from agentpool.agents.events import SpawnSessionStart, StreamCompleteEvent, SubAgentEvent
from agentpool_server.acp_server.event_converter import ACPEventConverter, SubagentSessionInfo


async def collect_updates(converter: ACPEventConverter, event):
    """Helper to collect all updates from an event."""
    return [u async for u in converter.convert(event)]


class TestACPEventConverter:
    """Test the ACP event converter."""

    @pytest.mark.anyio
    async def test_text_part_start_yields_agent_message_chunk(self):
        """PartStartEvent with TextPart yields AgentMessageChunk."""
        converter = ACPEventConverter()
        event = PartStartEvent(part=TextPart(content="Hello, world!"), index=0)

        updates = await collect_updates(converter, event)

        assert len(updates) == 1
        assert isinstance(updates[0], AgentMessageChunk)

    @pytest.mark.anyio
    async def test_text_delta_yields_agent_message_chunk(self):
        """PartDeltaEvent with TextPartDelta yields AgentMessageChunk."""
        converter = ACPEventConverter()
        event = PartDeltaEvent(delta=TextPartDelta(content_delta="streaming..."), index=0)

        updates = await collect_updates(converter, event)

        assert len(updates) == 1
        assert isinstance(updates[0], AgentMessageChunk)

    @pytest.mark.anyio
    async def test_multiple_events_yield_multiple_updates(self):
        """Multiple text events yield multiple updates."""
        converter = ACPEventConverter()

        events = [
            PartStartEvent(part=TextPart(content="Hello"), index=0),
            PartDeltaEvent(delta=TextPartDelta(content_delta=", "), index=0),
            PartDeltaEvent(delta=TextPartDelta(content_delta="world!"), index=0),
        ]

        all_updates = []
        for event in events:
            all_updates.extend(await collect_updates(converter, event))

        assert len(all_updates) == 3
        assert all(isinstance(u, AgentMessageChunk) for u in all_updates)

    @pytest.mark.anyio
    async def test_converter_reset_clears_state(self):
        """reset() clears internal state."""
        converter = ACPEventConverter()

        # Add some state by processing events
        event = PartStartEvent(part=TextPart(content="test"), index=0)
        await collect_updates(converter, event)

        # Reset
        converter.reset()

        # State should be cleared
        assert len(converter._tool_states) == 0
        assert len(converter._subagent_headers) == 0
        assert len(converter._subagent_content) == 0

    @pytest.mark.anyio
    async def test_converter_is_stateless_for_text(self):
        """Text conversion doesn't accumulate state."""
        converter = ACPEventConverter()

        # Process multiple text events
        for _ in range(5):
            event = PartStartEvent(part=TextPart(content="text"), index=0)
            await collect_updates(converter, event)

        # No tool state should be accumulated for plain text
        assert len(converter._tool_states) == 0

    @pytest.mark.anyio
    async def test_cancel_pending_tools_sends_cancellation_for_active_tools(self):
        """cancel_pending_tools() sends cancellation for all pending tool calls."""
        converter = ACPEventConverter()

        # Start two tool calls
        tool_event_1 = FunctionToolCallEvent(
            part=ToolCallPart(
                tool_call_id="tool-1",
                tool_name="test_tool",
                args={"arg": "value"},
            ),
        )
        tool_event_2 = FunctionToolCallEvent(
            part=ToolCallPart(
                tool_call_id="tool-2",
                tool_name="another_tool",
                args={},
            ),
        )

        # Process tool call starts
        await collect_updates(converter, tool_event_1)
        await collect_updates(converter, tool_event_2)

        # Verify both tools are tracked
        assert len(converter._tool_states) == 2

        # Cancel pending tools
        cancellations = [u async for u in converter.cancel_pending_tools()]

        # Should get cancellation notifications for both tools (status="completed")
        assert len(cancellations) == 2
        assert all(isinstance(u, ToolCallProgress) for u in cancellations)
        assert all(u.status == "completed" for u in cancellations)
        tool_ids = {u.tool_call_id for u in cancellations}
        assert tool_ids == {"tool-1", "tool-2"}

        # State should be cleared after cancellation
        assert len(converter._tool_states) == 0

    @pytest.mark.anyio
    async def test_cancel_pending_tools_handles_empty_state(self):
        """cancel_pending_tools() works when no tools are active."""
        converter = ACPEventConverter()

        # Cancel with no active tools
        cancellations = [u async for u in converter.cancel_pending_tools()]

        # Should yield nothing
        assert len(cancellations) == 0
        assert len(converter._tool_states) == 0


# ============================================================================
# RFC-0027: _meta filling in zed mode
# ============================================================================


@pytest.mark.anyio
async def test_spawn_session_start_emits_tool_call_start_with_meta_in_zed_mode():
    """In zed mode, SpawnSessionStart emits ToolCallStart with field_meta containing subagent_session_info."""
    converter = ACPEventConverter()
    converter._display_mode = "zed"  # type: ignore[assignment]

    event = SpawnSessionStart(
        child_session_id="child-123",
        parent_session_id="parent-456",
        source_name="test_agent",
        source_type="agent",
        description="Test spawn",
        spawn_mechanism="task",
    )

    updates = await collect_updates(converter, event)

    tool_starts = [u for u in updates if isinstance(u, ToolCallStart)]
    assert len(tool_starts) == 1
    assert tool_starts[0].field_meta is not None
    assert "subagent_session_info" in tool_starts[0].field_meta
    session_info = tool_starts[0].field_meta["subagent_session_info"]
    assert isinstance(session_info, dict)
    assert session_info["session_id"] == "child-123"


@pytest.mark.anyio
async def test_subagent_event_emits_tool_call_progress_with_meta_in_zed_mode():
    """In zed mode, SubAgentEvent emits ToolCallProgress with field_meta."""
    converter = ACPEventConverter()
    converter._display_mode = "zed"  # type: ignore[assignment]

    inner_event = PartStartEvent(part=TextPart(content="Hello"), index=0)
    event = SubAgentEvent(
        source_name="test_agent",
        source_type="agent",
        event=inner_event,
        depth=1,
        child_session_id="child-123",
    )

    updates = await collect_updates(converter, event)

    progresses = [u for u in updates if isinstance(u, ToolCallProgress)]
    assert len(progresses) >= 1
    assert progresses[0].field_meta is not None
    assert "subagent_session_info" in progresses[0].field_meta


@pytest.mark.parametrize("mode", ["legacy", "inline", "tool_box"])
@pytest.mark.anyio
async def test_meta_never_present_in_non_zed_modes(mode: str):
    """field_meta is never present in non-zed display modes."""
    converter = ACPEventConverter()
    converter._display_mode = mode  # type: ignore[assignment]

    spawn_event = SpawnSessionStart(
        child_session_id="child-123",
        parent_session_id="parent-456",
        source_name="test_agent",
        source_type="agent",
        description="Test spawn",
        spawn_mechanism="task",
    )

    inner_event = PartStartEvent(part=TextPart(content="Hello"), index=0)
    subagent_event = SubAgentEvent(
        source_name="test_agent",
        source_type="agent",
        event=inner_event,
        depth=1,
        child_session_id="child-123",
    )

    spawn_updates = await collect_updates(converter, spawn_event)
    subagent_updates = await collect_updates(converter, subagent_event)
    all_updates = spawn_updates + subagent_updates

    for update in all_updates:
        if hasattr(update, "field_meta"):
            assert update.field_meta is None


def test_subagent_session_info_is_json_object_not_string():
    """SubagentSessionInfo.model_dump() returns a dict, not a string."""
    info = SubagentSessionInfo(session_id="sess_123")
    dumped = info.model_dump()
    assert isinstance(dumped, dict)
    assert dumped["session_id"] == "sess_123"
    assert "message_start_index" in dumped
    assert "message_end_index" in dumped


@pytest.mark.anyio
async def test_tool_name_is_task_in_spawn_session_start_meta():
    """SpawnSessionStart-derived ToolCallStart has tool_name='task' in field_meta."""
    converter = ACPEventConverter()
    converter._display_mode = "zed"  # type: ignore[assignment]

    event = SpawnSessionStart(
        child_session_id="child-123",
        parent_session_id="parent-456",
        source_name="test_agent",
        source_type="agent",
        description="Test spawn",
        spawn_mechanism="task",
    )

    updates = await collect_updates(converter, event)

    tool_starts = [u for u in updates if isinstance(u, ToolCallStart)]
    assert len(tool_starts) == 1
    assert tool_starts[0].field_meta is not None
    assert tool_starts[0].field_meta.get("tool_name") == "task"


# ============================================================================
# Bug regression tests for event_converter.py
# ============================================================================


def test_no_duplicate_field_declarations():
    """_current_message_id field must be declared exactly once in ACPEventConverter.

    Regression: duplicate field declarations caused confusion and potential
    dataclass initialization issues.
    """
    import inspect

    source_file = inspect.getfile(ACPEventConverter)
    with open(source_file) as f:
        source = f.read()

    field_decl = "_current_message_id: str = field(default_factory=lambda: str(uuid.uuid4()))"
    count = source.count(field_decl)
    assert count == 1, f"Expected exactly 1 _current_message_id field declaration, found {count}"


def test_reset_body_not_duplicated():
    """reset() method body must not contain duplicate lines.

    Regression: the entire reset body was accidentally duplicated,
    causing redundant state clearing operations.
    """
    import inspect

    source = inspect.getsource(ACPEventConverter.reset)
    lines = source.splitlines()
    body_lines: list[str] = []
    in_body = False
    docstring_delim: str | None = None

    for line in lines:
        stripped = line.strip()
        if not in_body:
            if stripped.startswith("def reset("):
                in_body = True
            continue
        # Skip docstring
        if docstring_delim is None:
            if stripped.startswith('"""') or stripped.startswith("'''"):
                docstring_delim = stripped[:3]
                if stripped.endswith(docstring_delim) and len(stripped) > 3:
                    docstring_delim = None
                continue
        else:
            if stripped.endswith(docstring_delim):
                docstring_delim = None
            continue
        # Skip empty lines and comments-only lines
        if not stripped or stripped.startswith("#"):
            continue
        body_lines.append(stripped)

    seen: set[str] = set()
    for line in body_lines:
        assert line not in seen, f"Duplicate line in reset() body: {line}"
        seen.add(line)


@pytest.mark.anyio
async def test_reset_called_only_once_on_stream_complete():
    """reset() must be called exactly once when handling StreamCompleteEvent.

    Regression: StreamCompleteEvent handler contained a duplicate self.reset()
    call after the cleanup comments, causing reset to execute twice.
    """
    from unittest.mock import patch

    from pydantic_ai.usage import RequestUsage

    from agentpool.messaging.messages import ChatMessage

    converter = ACPEventConverter()
    message = ChatMessage(
        content="test",
        role="assistant",
        usage=RequestUsage(
            input_tokens=5,
            output_tokens=5,
        ),
    )
    event = StreamCompleteEvent(message=message)

    with patch.object(converter, "reset") as mock_reset:
        updates = await collect_updates(converter, event)
        # Consume the generator to drive execution
        list(updates)
        mock_reset.assert_called_once()
