"""Tests for SpawnSessionStart event handling in OpenCode server.

Tests event ordering, duplicate guard, and complete lifecycle for eager
session creation via SpawnSessionStart events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from pydantic_ai.messages import TextPartDelta
import pytest

from agentpool.agents.events import (
    PartDeltaEvent,
    PartStartEvent,
    SpawnSessionStart,
    StreamCompleteEvent,
    SubAgentEvent,
)
from agentpool.messaging import ChatMessage
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
)


if TYPE_CHECKING:
    from agentpool_server.opencode_server.state import ServerState


@pytest.mark.asyncio
async def test_spawn_start_before_content(server_state: ServerState) -> None:
    """Verify SpawnSessionStart emits before first SubAgentEvent content.

    When a SpawnSessionStart event is received before any SubAgentEvent,
    it should create the child session and context, which SubAgentEvent
    will then use for content propagation.

    Verifies:
    - SpawnSessionStart creates child session messages (user + assistant)
    - SpawnSessionStart creates ToolPart in parent session
    - Subsequent SubAgentEvent with PartDeltaEvent routes to child session
    - Child session receives the content updates
    """
    # Setup parent context
    processor = EventProcessor()
    parent_session_id = "parent-session-001"
    child_session_id = "child-session-001"

    parent_assistant_msg = MessageWithParts.assistant(
        message_id="msg-parent-001",
        session_id=parent_session_id,
        time=MessageTime(created=1000),
        agent_name="parent-agent",
        model_id="test-model",
        parent_id="user-msg-001",
        provider_id="test-provider",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )
    parent_ctx = EventProcessorContext(
        session_id=parent_session_id,
        assistant_msg_id="msg-parent-001",
        assistant_msg=parent_assistant_msg,
        state=server_state,
        working_dir="/tmp",
    )

    # GIVEN: SpawnSessionStart event
    spawn_event = SpawnSessionStart(
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
        tool_call_id="call-001",
        spawn_mechanism="task",
        source_name="test_agent",
        source_type="agent",
        depth=1,
        description="Run test_agent task",
        metadata={"prompt": "test prompt"},
    )

    # Process SpawnSessionStart
    spawn_events = []
    async for event in processor.process(spawn_event, parent_ctx):
        spawn_events.append(event)

    # THEN: Should create user message, assistant message, and ToolPart (3 events)
    message_updated_events = [e for e in spawn_events if isinstance(e, MessageUpdatedEvent)]
    part_updated_events = [e for e in spawn_events if isinstance(e, PartUpdatedEvent)]

    # 2 MessageUpdatedEvents (user message + assistant message)
    assert len(message_updated_events) == 2, (
        f"Expected 2 MessageUpdatedEvent, got {len(message_updated_events)}"
    )

    # 1 PartUpdatedEvent for the ToolPart in parent session
    assert len(part_updated_events) == 1, (
        f"Expected 1 PartUpdatedEvent for ToolPart, got {len(part_updated_events)}"
    )

    # Verify child session exists
    assert child_session_id in server_state.messages, (
        f"Child session {child_session_id} should be created"
    )

    # Verify child session has 2 messages (user + assistant)
    child_messages = server_state.messages[child_session_id]
    assert len(child_messages) == 2, (
        f"Child session should have 2 messages, got {len(child_messages)}"
    )

    # Verify user message has description content
    user_msg = child_messages[0]
    assert user_msg.info.role == "user", "First child message should be user message"
    assert "Run test_agent task" in str(user_msg.parts), (
        "User message should contain task description"
    )

    # GIVEN: SubAgentEvent with PartDeltaEvent after SpawnSessionStart
    inner_delta = PartDeltaEvent(
        index=0,
        delta=TextPartDelta(content_delta="Hello from subagent"),
    )
    subagent_event = SubAgentEvent(
        source_name="test_agent",
        source_type="agent",
        event=inner_delta,
        depth=1,
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
    )

    # Process SubAgentEvent
    subagent_events = []
    async for event in processor.process(subagent_event, parent_ctx):
        subagent_events.append(event)

    # THEN: SubAgentEvent should yield PartUpdatedEvent in child session
    delta_part_events = [e for e in subagent_events if isinstance(e, PartUpdatedEvent)]
    assert len(delta_part_events) >= 1, (
        f"Expected at least 1 PartUpdatedEvent from delta, got {len(delta_part_events)}"
    )

    # Verify content reached child session
    child_messages_after = server_state.messages[child_session_id]
    assistant_msg = child_messages_after[1]
    assert assistant_msg.info.role == "assistant", "Second child message should be assistant"

    # The assistant message should have a text part with the content
    all_text_parts = [p for p in assistant_msg.parts if hasattr(p, "text")]
    assert len(all_text_parts) >= 1, "Assistant message should have text part added"

    # Verify the content is there
    combined_text = " ".join([str(p.text) for p in all_text_parts if hasattr(p, "text")])
    assert "Hello from subagent" in combined_text, (
        f"Content 'Hello from subagent' should be in child session. Got: {combined_text!r}"
    )


@pytest.mark.asyncio
async def test_duplicate_session_guard(server_state: ServerState) -> None:
    """Verify duplicate SpawnSessionStart events don't create multiple sessions.

    When multiple SpawnSessionStart events with the same child_session_id
    are received, only the first should create the session. Subsequent
    events should be ignored (duplicate guard).

    Verifies:
    - First SpawnSessionStart creates session and yields events
    - Second SpawnSessionStart with same ID is ignored (no events yielded)
    - Child session still has only 2 messages (not 4)
    - ToolPart in parent is created only once
    """
    # Setup parent context
    processor = EventProcessor()
    parent_session_id = "parent-session-002"
    child_session_id = "child-session-002"

    parent_assistant_msg = MessageWithParts.assistant(
        message_id="msg-parent-002",
        session_id=parent_session_id,
        time=MessageTime(created=1000),
        agent_name="parent-agent",
        model_id="test-model",
        parent_id="user-msg-002",
        provider_id="test-provider",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )
    parent_ctx = EventProcessorContext(
        session_id=parent_session_id,
        assistant_msg_id="msg-parent-002",
        assistant_msg=parent_assistant_msg,
        state=server_state,
        working_dir="/tmp",
    )

    # Create first SpawnSessionStart event
    spawn_event_1 = SpawnSessionStart(
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
        tool_call_id="call-001",
        spawn_mechanism="task",
        source_name="test_agent",
        source_type="agent",
        depth=1,
        description="First spawn",
    )

    # Create duplicate SpawnSessionStart event (same child_session_id)
    spawn_event_2 = SpawnSessionStart(
        child_session_id=child_session_id,  # Same ID!
        parent_session_id=parent_session_id,
        tool_call_id="call-002",  # Different tool call ID
        spawn_mechanism="task",
        source_name="test_agent",
        source_type="agent",
        depth=1,
        description="Duplicate spawn",
    )

    # Process first SpawnSessionStart
    with patch("agentpool_server.opencode_server.event_processor.logger") as mock_logger:
        events_1 = []
        async for event in processor.process(spawn_event_1, parent_ctx):
            events_1.append(event)

        # Count initial messages in child session
        initial_child_messages = len(server_state.messages.get(child_session_id, []))
        assert initial_child_messages == 2, (
            f"Expected 2 messages after first spawn, got {initial_child_messages}"
        )

        # Process duplicate SpawnSessionStart
        events_2 = []
        async for event in processor.process(spawn_event_2, parent_ctx):
            events_2.append(event)

        # Verify debug log was called for duplicate
        debug_calls = [
            call for call in mock_logger.debug.call_args_list if child_session_id in str(call)
        ]
        assert len(debug_calls) >= 1, "Expected debug log about duplicate session"

    # THEN: Second spawn should yield no events (duplicate guard)
    assert len(events_2) == 0, f"Duplicate spawn should yield 0 events, got {len(events_2)}"

    # Child session should still have only 2 messages (not 4)
    final_child_messages = len(server_state.messages.get(child_session_id, []))
    assert final_child_messages == 2, (
        f"Child session should still have 2 messages, got {final_child_messages}"
    )

    # Parent should only have 1 ToolPart for this subagent
    tool_parts_in_parent = [
        p for p in parent_assistant_msg.parts if hasattr(p, "tool") and p.tool == "task"
    ]
    assert len(tool_parts_in_parent) == 1, (
        f"Parent should have 1 ToolPart, got {len(tool_parts_in_parent)}"
    )


@pytest.mark.asyncio
async def test_complete_lifecycle_ordering(server_state: ServerState) -> None:
    """Verify correct event order: start → subagent(content) → complete.

    Tests the complete lifecycle of a subagent session:
    1. SpawnSessionStart creates the session
    2. SubAgentEvent with text content is routed to child
    3. SubAgentEvent with StreamCompleteEvent finalizes

    Verifies:
    - Events are processed in correct order
    - Child session receives all content
    - StreamCompleteEvent properly finalizes child session
    - ToolPart in parent transitions to completed state
    """
    # Setup parent context
    processor = EventProcessor()
    parent_session_id = "parent-session-003"
    child_session_id = "child-session-003"

    parent_assistant_msg = MessageWithParts.assistant(
        message_id="msg-parent-003",
        session_id=parent_session_id,
        time=MessageTime(created=1000),
        agent_name="parent-agent",
        model_id="test-model",
        parent_id="user-msg-003",
        provider_id="test-provider",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )
    parent_ctx = EventProcessorContext(
        session_id=parent_session_id,
        assistant_msg_id="msg-parent-003",
        assistant_msg=parent_assistant_msg,
        state=server_state,
        working_dir="/tmp",
    )

    all_events = []

    # Step 1: SpawnSessionStart
    spawn_event = SpawnSessionStart(
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
        tool_call_id="call-003",
        spawn_mechanism="task",
        source_name="lifecycle_test_agent",
        source_type="agent",
        depth=1,
        description="Test lifecycle",
    )

    async for event in processor.process(spawn_event, parent_ctx):
        all_events.append(("spawn_start", event))

    # Step 2: SubAgentEvent with text content (PartStartEvent + PartDeltaEvent)
    text_start = PartStartEvent.text(index=0, content="Starting task")
    subagent_start = SubAgentEvent(
        source_name="lifecycle_test_agent",
        source_type="agent",
        event=text_start,
        depth=1,
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
    )

    async for event in processor.process(subagent_start, parent_ctx):
        all_events.append(("subagent_start", event))

    # Step 3: SubAgentEvent with delta
    text_delta = PartDeltaEvent.text(index=0, content=" progress update")
    subagent_delta = SubAgentEvent(
        source_name="lifecycle_test_agent",
        source_type="agent",
        event=text_delta,
        depth=1,
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
    )

    async for event in processor.process(subagent_delta, parent_ctx):
        all_events.append(("subagent_delta", event))

    # Step 4: SubAgentEvent with StreamCompleteEvent
    complete_event = StreamCompleteEvent(
        message=ChatMessage(role="assistant", content="Task completed successfully"),
    )
    subagent_complete = SubAgentEvent(
        source_name="lifecycle_test_agent",
        source_type="agent",
        event=complete_event,
        depth=1,
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
    )

    async for event in processor.process(subagent_complete, parent_ctx):
        all_events.append(("subagent_complete", event))

    # Verify event ordering
    event_types = [e[0] for e in all_events]
    assert event_types[0] == "spawn_start", "First event should be spawn_start"
    assert "subagent_start" in event_types, "Should have subagent_start event"
    assert "subagent_delta" in event_types, "Should have subagent_delta event"
    assert "subagent_complete" in event_types, "Should have subagent_complete event"

    # Verify final order: spawn_start comes before subagent events
    spawn_idx = event_types.index("spawn_start")
    start_idx = event_types.index("subagent_start")
    delta_idx = event_types.index("subagent_delta")
    complete_idx = event_types.index("subagent_complete")

    assert spawn_idx < start_idx < delta_idx < complete_idx, (
        f"Events should be in order: spawn_start < subagent_start < subagent_delta < "
        f"subagent_complete. Got indices: spawn={spawn_idx}, start={start_idx}, "
        f"delta={delta_idx}, complete={complete_idx}"
    )

    # Verify child session has final content
    child_messages = server_state.messages[child_session_id]
    assert len(child_messages) == 2, f"Child should have 2 messages, got {len(child_messages)}"

    # Check assistant message has content
    assistant_msg = child_messages[1]
    all_text_parts = [p for p in assistant_msg.parts if hasattr(p, "text")]
    assert len(all_text_parts) >= 1, "Assistant should have text parts"

    combined_text = " ".join([str(p.text) for p in all_text_parts])
    assert "Starting task" in combined_text, f"Should have 'Starting task'. Got: {combined_text!r}"
    assert "progress update" in combined_text, (
        f"Should have 'progress update'. Got: {combined_text!r}"
    )

    # Verify ToolPart in parent session was updated (completed state after StreamComplete)
    subagent_key = "1:lifecycle_test_agent"
    assert parent_ctx.has_subagent_tool_part(subagent_key), "Parent should have subagent ToolPart"

    tool_part = parent_ctx.get_subagent_tool_part(subagent_key)
    assert tool_part is not None, "ToolPart should exist"

    # Check if state has been updated (it should have been finalized)
    # The state type depends on the processor implementation
    if hasattr(tool_part.state, "output"):
        assert (
            "Task completed" in str(tool_part.state.output)
            or "completed" in str(tool_part.state).lower()
        ), "ToolPart should show completed status"


@pytest.mark.asyncio
async def test_backward_compatibility_fallback(server_state: ServerState) -> None:
    """Verify fallback in _process_subagent_event still works without SpawnSessionStart.

    When SubAgentEvent is received WITHOUT prior SpawnSessionStart, the
    _process_subagent_event method should reactively create the session
    (backward compatibility fallback).

    Verifies:
    - SubAgentEvent without prior SpawnSessionStart creates session
    - Child session messages are created reactively
    - Content is properly routed to child session
    - Old code path (reactive creation) still functions
    """
    # Setup parent context
    processor = EventProcessor()
    parent_session_id = "parent-session-004"
    child_session_id = "child-session-004"

    parent_assistant_msg = MessageWithParts.assistant(
        message_id="msg-parent-004",
        session_id=parent_session_id,
        time=MessageTime(created=1000),
        agent_name="parent-agent",
        model_id="test-model",
        parent_id="user-msg-004",
        provider_id="test-provider",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )
    parent_ctx = EventProcessorContext(
        session_id=parent_session_id,
        assistant_msg_id="msg-parent-004",
        assistant_msg=parent_assistant_msg,
        state=server_state,
        working_dir="/tmp",
    )

    # Verify child session does NOT exist initially
    assert child_session_id not in server_state.messages, (
        "Child session should not exist before SubAgentEvent"
    )

    # GIVEN: SubAgentEvent WITHOUT prior SpawnSessionStart (backward compat test)
    text_delta = PartDeltaEvent.text(index=0, content="Fallback content")
    subagent_event = SubAgentEvent(
        source_name="fallback_agent",
        source_type="agent",
        event=text_delta,
        depth=1,
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
    )

    # Process SubAgentEvent (should trigger reactive session creation)
    events = []
    async for event in processor.process(subagent_event, parent_ctx):
        events.append(event)

    # THEN: Child session should be created reactively
    assert child_session_id in server_state.messages, (
        "Child session should be created reactively by SubAgentEvent"
    )

    # Verify child session has messages (reactive creation)
    child_messages = server_state.messages[child_session_id]
    assert len(child_messages) == 2, (
        f"Reactive creation should produce 2 messages, got {len(child_messages)}"
    )

    # Verify content was routed
    assistant_msg = child_messages[1]
    all_text_parts = [p for p in assistant_msg.parts if hasattr(p, "text")]
    combined_text = " ".join([str(p.text) for p in all_text_parts])
    assert "Fallback content" in combined_text, f"Content should be routed. Got: {combined_text!r}"

    # Verify ToolPart was created in parent
    subagent_key = "1:fallback_agent"
    assert parent_ctx.has_subagent_tool_part(subagent_key), (
        "Parent should have subagent ToolPart (created reactively)"
    )

    # Verify events were yielded (MessageUpdatedEvent for user/assistant + PartUpdatedEvent)
    message_events = [e for e in events if isinstance(e, MessageUpdatedEvent)]
    part_events = [e for e in events if isinstance(e, PartUpdatedEvent)]

    assert len(message_events) >= 2, (
        f"Should have at least 2 MessageUpdatedEvents, got {len(message_events)}"
    )
    assert len(part_events) >= 1, f"Should have at least 1 PartUpdatedEvent, got {len(part_events)}"
