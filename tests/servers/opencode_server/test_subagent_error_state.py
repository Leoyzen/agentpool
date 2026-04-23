"""Tests for subagent error state transitions in OpenCode server.

When a subagent fails (emits RunErrorEvent), the parent session's ToolPart
must transition from ToolStateRunning to ToolStateError (not stay running
forever). This test file verifies that error path using TDD.

Bug: _process_subagent_event() only handles StreamCompleteEvent (happy path),
transitioning ToolPart to ToolStateCompleted. RunErrorEvent is silently dropped
because EventProcessor.process() has no match arm for it. The ToolPart stays
in ToolStateRunning forever, making the UI show the subagent as perpetually
"running" even after it has crashed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from agentpool.agents.events import (
    RunErrorEvent,
    SpawnSessionStart,
    StreamCompleteEvent,
    SubAgentEvent,
)
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
    SessionErrorEvent,
)
from agentpool_server.opencode_server.models.parts import (
    ToolStateError,
    ToolStateRunning,
)


if TYPE_CHECKING:
    from agentpool_server.opencode_server.state import ServerState


def _make_parent_ctx(server_state: ServerState) -> EventProcessorContext:
    """Create a minimal parent context for testing."""
    parent_session_id = "parent-session-err-001"
    parent_assistant_msg = MessageWithParts.assistant(
        message_id="msg-parent-err-001",
        session_id=parent_session_id,
        time=MessageTime(created=1000),
        agent_name="parent-agent",
        model_id="test-model",
        parent_id="user-msg-err-001",
        provider_id="test-provider",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )
    return EventProcessorContext(
        session_id=parent_session_id,
        assistant_msg_id="msg-parent-err-001",
        assistant_msg=parent_assistant_msg,
        state=server_state,
        working_dir="/tmp",
    )


async def _collect_events(aiter) -> list:
    """Collect all items from an async iterator into a list."""
    result = []
    async for item in aiter:
        result.append(item)
    return result


@pytest.mark.asyncio
async def test_subagent_run_error_transitions_toolpart_to_error(
    server_state: ServerState,
) -> None:
    """Verify RunErrorEvent inside SubAgentEvent transitions ToolPart to ToolStateError.

    When a subagent emits RunErrorEvent, the parent's ToolPart must transition
    from ToolStateRunning to ToolStateError so the UI shows the failure instead
    of a perpetually "running" spinner.

    Verifies:
    - SubAgentEvent wrapping RunErrorEvent is processed (not silently dropped)
    - Parent's ToolPart transitions to ToolStateError (not ToolStateRunning)
    - ToolStateError.error contains the RunErrorEvent.message
    - A SessionErrorEvent is emitted for the child session
    """
    processor = EventProcessor()
    parent_ctx = _make_parent_ctx(server_state)
    child_session_id = "child-session-err-001"

    # GIVEN: A SpawnSessionStart has already created the child context + ToolPart
    spawn_event = SpawnSessionStart(
        child_session_id=child_session_id,
        parent_session_id=parent_ctx.session_id,
        source_name="explore",
        source_type="agent",
        spawn_mechanism="task",
        description="Test explore agent",
        depth=1,
    )
    await _collect_events(processor.process(spawn_event, parent_ctx))

    # Verify ToolPart is in Running state after spawn
    subagent_key = f"1:explore:{child_session_id}"
    assert parent_ctx.has_subagent_tool_part(subagent_key)
    tool_part = parent_ctx.get_subagent_tool_part(subagent_key)
    assert tool_part is not None
    assert isinstance(tool_part.state, ToolStateRunning)

    # WHEN: SubAgentEvent wraps a RunErrorEvent (subagent failed)
    error_event = SubAgentEvent(
        source_name="explore",
        source_type="agent",
        event=RunErrorEvent(
            message="Agent failed: connection timeout",
            code="TIMEOUT",
            run_id="run-err-001",
            agent_name="explore",
        ),
        depth=1,
        child_session_id=child_session_id,
        parent_session_id=parent_ctx.session_id,
    )
    events = await _collect_events(processor.process(error_event, parent_ctx))

    # THEN: ToolPart should transition to ToolStateError
    tool_part_after = parent_ctx.get_subagent_tool_part(subagent_key)
    assert tool_part_after is not None
    assert isinstance(
        tool_part_after.state, ToolStateError
    ), f"Expected ToolStateError, got {type(tool_part_after.state).__name__}"

    # THEN: Error message should contain the RunErrorEvent.message
    assert "connection timeout" in tool_part_after.state.error

    # THEN: A PartUpdatedEvent should be yielded for the ToolPart transition
    part_updated_events = [e for e in events if isinstance(e, PartUpdatedEvent)]
    assert len(part_updated_events) >= 1, "Expected at least one PartUpdatedEvent"

    # THEN: A SessionErrorEvent should be emitted for the child session
    session_error_events = [e for e in events if isinstance(e, SessionErrorEvent)]
    assert len(session_error_events) >= 1, "Expected at least one SessionErrorEvent"
    assert session_error_events[0].properties.session_id == child_session_id


@pytest.mark.asyncio
async def test_subagent_run_error_without_prior_spawn(
    server_state: ServerState,
) -> None:
    """Verify RunErrorEvent creates context if no prior SpawnSessionStart.

    In some error scenarios, RunErrorEvent may arrive before any
    SpawnSessionStart or content event. The processor should still create
    the child context and transition the ToolPart to error state.

    Verifies:
    - Child session is created even when RunErrorEvent is the first event
    - ToolPart is in ToolStateError state (not stuck running)
    - Error message is preserved
    """
    processor = EventProcessor()
    parent_ctx = _make_parent_ctx(server_state)
    child_session_id = "child-session-err-002"

    # WHEN: RunErrorEvent arrives as the first (and only) subagent event
    error_event = SubAgentEvent(
        source_name="explore",
        source_type="agent",
        event=RunErrorEvent(
            message="Agent failed: initialization error",
            code="INIT_ERROR",
            run_id="run-err-002",
            agent_name="explore",
        ),
        depth=1,
        child_session_id=child_session_id,
        parent_session_id=parent_ctx.session_id,
    )
    events = await _collect_events(processor.process(error_event, parent_ctx))

    # THEN: ToolPart should be in ToolStateError state
    subagent_key = f"1:explore:{child_session_id}"
    tool_part = parent_ctx.get_subagent_tool_part(subagent_key)
    assert tool_part is not None, "ToolPart should be created for the error"
    assert isinstance(
        tool_part.state, ToolStateError
    ), f"Expected ToolStateError, got {type(tool_part.state).__name__}"
    assert "initialization error" in tool_part.state.error


@pytest.mark.asyncio
async def test_subagent_stream_complete_after_run_error_stays_error(
    server_state: ServerState,
) -> None:
    """Verify StreamCompleteEvent after RunErrorEvent doesn't override error state.

    If a RunErrorEvent is followed by a StreamCompleteEvent (race condition
    or cleanup), the ToolPart should remain in ToolStateError, not transition
    to ToolStateCompleted. The error state should be terminal.

    Verifies:
    - ToolPart stays in ToolStateError after subsequent StreamCompleteEvent
    """
    processor = EventProcessor()
    parent_ctx = _make_parent_ctx(server_state)
    child_session_id = "child-session-err-003"

    # GIVEN: SpawnSessionStart + RunErrorEvent have already set ToolPart to error
    spawn_event = SpawnSessionStart(
        child_session_id=child_session_id,
        parent_session_id=parent_ctx.session_id,
        source_name="explore",
        source_type="agent",
        spawn_mechanism="task",
        description="Test explore agent",
        depth=1,
    )
    await _collect_events(processor.process(spawn_event, parent_ctx))

    error_event = SubAgentEvent(
        source_name="explore",
        source_type="agent",
        event=RunErrorEvent(
            message="Agent failed: API error",
            code="API_ERROR",
            run_id="run-err-003",
            agent_name="explore",
        ),
        depth=1,
        child_session_id=child_session_id,
        parent_session_id=parent_ctx.session_id,
    )
    await _collect_events(processor.process(error_event, parent_ctx))

    subagent_key = f"1:explore:{child_session_id}"
    tool_part = parent_ctx.get_subagent_tool_part(subagent_key)
    assert tool_part is not None
    assert isinstance(tool_part.state, ToolStateError)

    # WHEN: A late StreamCompleteEvent arrives
    from agentpool.messaging import ChatMessage

    complete_msg = ChatMessage(
        role="assistant",
        content="Partial output before error",
    )
    complete_event = SubAgentEvent(
        source_name="explore",
        source_type="agent",
        event=StreamCompleteEvent(message=complete_msg),
        depth=1,
        child_session_id=child_session_id,
        parent_session_id=parent_ctx.session_id,
    )
    await _collect_events(processor.process(complete_event, parent_ctx))

    # THEN: ToolPart should still be in ToolStateError (not overridden to Completed)
    tool_part_after = parent_ctx.get_subagent_tool_part(subagent_key)
    assert tool_part_after is not None
    assert isinstance(
        tool_part_after.state, ToolStateError
    ), "ToolPart should remain in error state after late StreamCompleteEvent"
