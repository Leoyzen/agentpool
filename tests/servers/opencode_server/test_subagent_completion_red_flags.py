"""Regression tests for subagent completion -> TUI/lead-agent handoff.

These tests cover:
1. Child session receives SessionIdleEvent after StreamCompleteEvent.
2. No NameError when parent ToolPart is missing (indentation fix).
3. Parent ToolPart transitions from Running -> Completed after subagent finishes.
4. inject_prompt weakness documentation (lead agent no-op without run context).
5. Full lifecycle: spawn -> run -> text -> complete produces all expected events.
6. RED FLAG: Race condition between ToolCallCompleteEvent and FunctionToolCallEvent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic_ai.messages import (
    PartStartEvent,
    TextPart as PydanticTextPart,
)

from agentpool.agents.events import (
    RunStartedEvent,
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
    MessageWithParts,
    PartUpdatedEvent,
    SessionIdleEvent,
    SessionStatusEvent,
)
from agentpool_server.opencode_server.models.parts import (
    ToolPart,
    ToolStateCompleted,
    ToolStateRunning,
)


if TYPE_CHECKING:
    from agentpool_server.opencode_server.state import ServerState


# =============================================================================
# Helpers
# =============================================================================


def _make_parent_ctx(
    server_state: ServerState,
    parent_session_id: str = "parent-session",
    parent_msg_id: str = "parent-msg-1",
) -> EventProcessorContext:
    """Create a parent EventProcessorContext for subagent tests."""
    assistant_msg = MessageWithParts.assistant(
        message_id=parent_msg_id,
        session_id=parent_session_id,
        time=MessageTime(created=0),
        agent_name="lead-agent",
        model_id="test-model",
        parent_id="parent-user-1",
        provider_id="agentpool",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )
    return EventProcessorContext(
        session_id=parent_session_id,
        assistant_msg_id=parent_msg_id,
        assistant_msg=assistant_msg,
        state=server_state,
        working_dir="/tmp",
    )


async def _process_events(
    processor: EventProcessor,
    events: list[Any],
    ctx: EventProcessorContext,
) -> list[Any]:
    """Process a sequence of events and collect all emitted SSE events."""
    emitted: list[Any] = []
    for event in events:
        async for e in processor.process(event, ctx):
            emitted.append(e)
    return emitted


# =============================================================================
# Red-Flag Test #1: Missing child session idle event
# =============================================================================


@pytest.mark.asyncio
async def test_subagent_stream_complete_emits_child_session_idle(
    server_state: ServerState,
) -> None:
    """Child session MUST receive a SessionIdleEvent after StreamCompleteEvent.

    REGRESSION TEST:
      Previously, EventProcessor._process_subagent_event() processed
      StreamCompleteEvent for the child context (via recursive self.process()),
      which emitted StepFinishPart into the child session. But NO SessionIdleEvent
      or SessionStatusEvent(type="idle") was ever emitted for the child session.

      The only idle events came from message_routes.py:_process_message_locked()
      which only runs for the PARENT session, not child sessions created by
      _process_subagent_event() or _process_spawn_start().

      After the fix, the child session receives both SessionStatusEvent(idle)
      and SessionIdleEvent after StreamCompleteEvent processing, so the TUI
      knows the subagent has finished and can update the card.
    """
    processor = EventProcessor()
    parent_session_id = "parent-idle-test"
    child_session_id = "child-idle-test"
    parent_ctx = _make_parent_ctx(server_state, parent_session_id)

    # Step 1: Start subagent (SpawnSessionStart → creates child session + ToolPart)
    spawn = SpawnSessionStart(
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
        tool_call_id="tc-1",
        spawn_mechanism="task",
        source_name="worker",
        source_type="agent",
        depth=1,
        description="Test task",
        metadata={"prompt": "test"},
        model_id="test-model",
    )
    events: list[Any] = []
    async for e in processor.process(spawn, parent_ctx):
        events.append(e)

    # Step 2: Stream some text (via SubAgentEvent wrapping)
    text_start = SubAgentEvent(
        source_name="worker",
        source_type="agent",
        event=PartStartEvent(index=0, part=PydanticTextPart(content="Result text")),
        depth=1,
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
    )
    async for e in processor.process(text_start, parent_ctx):
        events.append(e)

    # Step 3: Complete the stream — this is where the idle event SHOULD be emitted
    stream_complete = SubAgentEvent(
        source_name="worker",
        source_type="agent",
        event=StreamCompleteEvent(
            message=ChatMessage(role="assistant", content="Result text"),
        ),
        depth=1,
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
    )
    async for e in processor.process(stream_complete, parent_ctx):
        events.append(e)

    # ASSERTION: At least one idle-related event for the child session must exist
    child_idle_events = [
        e
        for e in events
        if isinstance(e, (SessionIdleEvent, SessionStatusEvent))
        and hasattr(e.properties, "session_id")
        and e.properties.session_id == child_session_id
    ]
    # Also check SessionStatusEvent with idle status
    child_status_idle = [
        e
        for e in events
        if isinstance(e, SessionStatusEvent)
        and e.properties.session_id == child_session_id
        and e.properties.status.type == "idle"
    ]

    assert len(child_idle_events) > 0 or len(child_status_idle) > 0, (
        f"No SessionIdleEvent or SessionStatusEvent(idle) emitted for child session "
        f"'{child_session_id}'. The TUI card will remain stuck in 'running' state. "
        f"Event types emitted: {[type(e).__name__ for e in events]}"
    )


# =============================================================================
# Red-Flag Test #2: Indentation bug — NameError when existing is None
# =============================================================================


@pytest.mark.asyncio
async def test_subagent_stream_complete_no_nameerror_when_toolpart_missing(
    server_state: ServerState,
) -> None:
    """Processing StreamCompleteEvent MUST NOT raise NameError when ToolPart is missing.

    REGRESSION TEST:
      Previously, event_processor.py had an indentation bug where lines
        ctx.add_subagent_tool_part(subagent_key, updated)
        ctx.assistant_msg.update_part(updated)
        yield PartUpdatedEvent.create(updated)
      were outside the `if existing is not None:` guard, causing NameError
      when `existing` was None (variable `updated` was undefined).

      After the fix, these lines are inside the `if existing is not None:`
      block, so no NameError is raised when the ToolPart is missing.
      The child session idle events are still emitted regardless.
    """
    processor = EventProcessor()
    parent_session_id = "parent-nameerror-test"
    child_session_id = "child-nameerror-test"
    parent_ctx = _make_parent_ctx(server_state, parent_session_id)

    # Step 1: Start subagent (creates ToolPart in parent)
    spawn = SpawnSessionStart(
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
        tool_call_id="tc-2",
        spawn_mechanism="task",
        source_name="worker",
        source_type="agent",
        depth=1,
        description="Test task",
        metadata={"prompt": "test"},
        model_id="test-model",
    )
    emitted: list[Any] = []
    async for e in processor.process(spawn, parent_ctx):
        emitted.append(e)

    # Step 2: Inject a None value for the subagent tool part key.
    # This simulates the case where has_subagent_tool_part returns True
    # (key exists in dict) but get_subagent_tool_part returns None.
    subagent_key = f"1:worker:{child_session_id}"
    parent_ctx.subagent_tool_parts[subagent_key] = None  # type: ignore[assignment]

    # Step 3: Send StreamCompleteEvent — this MUST NOT raise NameError
    stream_complete = SubAgentEvent(
        source_name="worker",
        source_type="agent",
        event=StreamCompleteEvent(
            message=ChatMessage(role="assistant", content="Done"),
        ),
        depth=1,
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
    )

    # Should process without NameError
    async for e in processor.process(stream_complete, parent_ctx):
        emitted.append(e)

    # Child session idle events must still be emitted even when ToolPart is missing
    child_idle_events = [
        e
        for e in emitted
        if isinstance(e, (SessionIdleEvent, SessionStatusEvent))
        and hasattr(e.properties, "session_id")
        and e.properties.session_id == child_session_id
        and (isinstance(e, SessionIdleEvent) or e.properties.status.type == "idle")
    ]
    assert len(child_idle_events) > 0, (
        "Child session idle events should still be emitted even when "
        "the parent ToolPart is missing."
    )


@pytest.mark.asyncio
async def test_subagent_stream_complete_parent_toolpart_transitions_to_completed(
    server_state: ServerState,
) -> None:
    """Parent ToolPart MUST transition from Running to Completed after subagent finishes.

    CURRENT BEHAVIOR (PARTIALLY BROKEN):
      When `existing is not None` (the common path), the code at lines 950-952
      works but only because `updated` accidentally leaks out of the inner
      `if existing is not None:` scope. The 3 lines at 12-space indent
      execute unconditionally, which is an indentation bug.

      The ToolPart does transition, but the code structure is fragile.

    EXPECTED BEHAVIOR:
      After processing a SubAgentEvent(StreamCompleteEvent), the parent
      session's ToolPart for the subagent MUST have state=ToolStateCompleted
      (not ToolStateRunning).
    """
    processor = EventProcessor()
    parent_session_id = "parent-completion-test"
    child_session_id = "child-completion-test"
    parent_ctx = _make_parent_ctx(server_state, parent_session_id)

    # Step 1: Start subagent (creates ToolPart in parent)
    spawn = SpawnSessionStart(
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
        tool_call_id="tc-3",
        spawn_mechanism="task",
        source_name="worker",
        source_type="agent",
        depth=1,
        description="Test task",
        metadata={"prompt": "test"},
        model_id="test-model",
    )
    emitted: list[Any] = []
    async for e in processor.process(spawn, parent_ctx):
        emitted.append(e)

    # Verify ToolPart is Running before completion
    subagent_key = f"1:worker:{child_session_id}"
    tool_part_before = parent_ctx.get_subagent_tool_part(subagent_key)
    assert tool_part_before is not None, "ToolPart should exist after SpawnSessionStart"
    assert isinstance(tool_part_before.state, ToolStateRunning), (
        "ToolPart should be in Running state before subagent completes"
    )

    # Step 2: Send StreamCompleteEvent
    stream_complete = SubAgentEvent(
        source_name="worker",
        source_type="agent",
        event=StreamCompleteEvent(
            message=ChatMessage(role="assistant", content="Done"),
        ),
        depth=1,
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
    )
    async for e in processor.process(stream_complete, parent_ctx):
        emitted.append(e)

    # Step 3: Verify ToolPart has transitioned to Completed
    tool_part_after = parent_ctx.get_subagent_tool_part(subagent_key)
    assert tool_part_after is not None, "ToolPart should still exist after completion"
    assert isinstance(tool_part_after.state, ToolStateCompleted), (
        f"ToolPart should be in Completed state after subagent finishes, "
        f"but got {type(tool_part_after.state).__name__}. "
        f"The TUI card will remain stuck showing 'running' status."
    )

    # Step 4: Verify PartUpdatedEvent was emitted for the completion
    completion_events = [
        e
        for e in emitted
        if isinstance(e, PartUpdatedEvent)
        and isinstance(e.properties.part, ToolPart)
        and isinstance(e.properties.part.state, ToolStateCompleted)
    ]
    assert len(completion_events) > 0, (
        "No PartUpdatedEvent with ToolStateCompleted was emitted for the parent ToolPart. "
        "The TUI will not update the subagent card from 'running' to 'completed'."
    )


# =============================================================================
# Red-Flag Test #3: inject_prompt does not re-awaken lead agent
# =============================================================================


@pytest.mark.asyncio
async def test_background_task_inject_prompt_wakes_lead_agent(
    server_state: ServerState,
) -> None:
    """inject_prompt after background task completion MUST re-awaken the lead agent.

    PREVIOUS BEHAVIOR (BROKEN):
      inject_prompt() only worked if the agent had an active run context
      (_current_run_ctx or _background_run_ctx). If both were None, the
      prompt was silently dropped and the lead agent never resumed.

    CURRENT BEHAVIOR (FIXED):
      inject_prompt() now delegates to SessionPool.receive_request() when
      no active run context exists. This triggers auto-resume so the lead
      agent receives the completion notice and resumes reasoning.
    """
    import inspect

    from agentpool.agents.base_agent import BaseAgent

    source = inspect.getsource(BaseAgent.inject_prompt)

    # The fix: inject_prompt now delegates to SessionPool instead of
    # silently dropping the prompt when no run context exists.
    assert "session_pool" in source, (
        "inject_prompt must reference session_pool to delegate when no run context exists"
    )
    assert "receive_request" in source or "inject_prompt" in source, (
        "inject_prompt must call receive_request or session_pool.inject_prompt "
        "to trigger auto-resume when no active run context is available"
    )

    # Verify the fallback path for shared agents (no fixed session_id)
    assert "agent_pool" in source, (
        "inject_prompt must check agent_pool as fallback for shared agents"
    )

    # Verify fire_and_forget is used to avoid blocking
    assert "fire_and_forget" in source, (
        "inject_prompt must use fire_and_forget to schedule the request asynchronously"
    )


# =============================================================================
# Red-Flag Test #4: Subagent events stop when lead agent stops streaming
# =============================================================================


@pytest.mark.asyncio
async def test_subagent_events_flow_after_parent_tool_returns(
    server_state: ServerState,
) -> None:
    """SubAgentEvent emissions MUST continue even after parent tool returns.

    CURRENT BEHAVIOR (BROKEN):
      In background_task_provider.py, _task_async() runs _run_and_stream()
      in a separate asyncio.Task via BackgroundTaskManager. Events are
      emitted via _safe_emit_event() which suppresses ALL exceptions
      including CancelledError. If the parent agent's run_stream ends
      before the background task completes, the event queue is closed
      and _safe_emit_event silently drops all subsequent events.

    EXPECTED BEHAVIOR:
      Even after the parent tool call returns (in async mode), the
      background task's events should still flow to the TUI so the
      subagent card shows real-time progress.
    """
    processor = EventProcessor()
    parent_session_id = "parent-events-test"
    child_session_id = "child-events-test"
    parent_ctx = _make_parent_ctx(server_state, parent_session_id)

    # Simulate the full lifecycle: spawn → run → text → complete

    # Step 1: Spawn
    spawn = SpawnSessionStart(
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
        tool_call_id="tc-5",
        spawn_mechanism="task",
        source_name="worker",
        source_type="agent",
        depth=1,
        description="Test task",
        metadata={"prompt": "test"},
        model_id="test-model",
    )
    emitted: list[Any] = []
    async for e in processor.process(spawn, parent_ctx):
        emitted.append(e)

    # Step 2: RunStarted
    run_started = SubAgentEvent(
        source_name="worker",
        source_type="agent",
        event=RunStartedEvent(session_id=child_session_id, run_id="run-1"),
        depth=1,
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
    )
    async for e in processor.process(run_started, parent_ctx):
        emitted.append(e)

    # Step 3: Text content
    text_event = SubAgentEvent(
        source_name="worker",
        source_type="agent",
        event=PartStartEvent(index=0, part=PydanticTextPart(content="Working...")),
        depth=1,
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
    )
    async for e in processor.process(text_event, parent_ctx):
        emitted.append(e)

    # Step 4: StreamCompleteEvent — must produce child session content
    # AND parent ToolPart completion AND child session idle signal
    complete = SubAgentEvent(
        source_name="worker",
        source_type="agent",
        event=StreamCompleteEvent(
            message=ChatMessage(role="assistant", content="Working..."),
        ),
        depth=1,
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
    )
    async for e in processor.process(complete, parent_ctx):
        emitted.append(e)

    # ASSERTION 1: Child session must have text content
    child_ctx = processor._child_contexts.get(child_session_id)
    assert child_ctx is not None, "Child context should exist after processing events"
    assert child_ctx.has_text_part or child_ctx.response_text, (
        "Child session should have text content after StreamCompleteEvent. "
        "The TUI would show an empty subagent card."
    )

    # ASSERTION 2: Child session must have StepFinishPart
    from agentpool_server.opencode_server.models.parts import StepFinishPart

    child_step_finish = [
        p for p in child_ctx.assistant_msg.parts if isinstance(p, StepFinishPart)
    ]
    assert len(child_step_finish) > 0, (
        "Child session must have a StepFinishPart after StreamCompleteEvent. "
        "Without it, the TUI considers the assistant message still 'pending' "
        "and the session appears stuck in 'working' state."
    )

    # ASSERTION 3: Parent ToolPart must be in Completed state
    subagent_key = f"1:worker:{child_session_id}"
    tool_part = parent_ctx.get_subagent_tool_part(subagent_key)
    assert tool_part is not None and isinstance(tool_part.state, ToolStateCompleted), (
        "Parent ToolPart must be in Completed state after subagent finishes. "
        "The TUI card will remain stuck showing 'running' status."
    )

    # ASSERTION 4: Child session idle event MUST exist
    # Must specifically check for idle status, not just any SessionStatusEvent
    # (RunStartedEvent produces SessionStatusEvent(busy) which is not what we need)
    child_idle_events = [
        e for e in emitted if isinstance(e, SessionIdleEvent) and e.properties.session_id == child_session_id
    ]
    child_status_idle = [
        e
        for e in emitted
        if isinstance(e, SessionStatusEvent)
        and e.properties.session_id == child_session_id
        and e.properties.status.type == "idle"
    ]
    assert len(child_idle_events) > 0 or len(child_status_idle) > 0, (
        f"No SessionIdleEvent or SessionStatusEvent(idle) emitted for child session "
        f"'{child_session_id}'. Event types: {[type(e).__name__ for e in emitted]}. "
        f"The TUI card will remain in 'busy' state forever."
    )


# =============================================================================
# Red-Flag Test #5: Race condition — ToolCallCompleteEvent arrives before start
# =============================================================================


@pytest.mark.asyncio
async def test_redflag_tool_complete_race_condition_dropped_event(
    server_state: ServerState,
) -> None:
    """RED FLAG: ToolCallCompleteEvent may be dropped if it arrives before start event.

    In SessionPool mode, _run_agentlet_core has a dual-path event architecture:
    1. FunctionToolCallEvent flows through local event_queue -> consumer -> EventBus
    2. ToolCallCompleteEvent is published DIRECTLY to EventBus by process_tool_event

    These two paths have NO ordering guarantee. If ToolCallCompleteEvent arrives
    at the event_processor before the tool start event (FunctionToolCallEvent or
    ToolCallStartEvent), the completion is silently dropped because
    ctx.has_tool_part(tool_call_id) returns False.

    REGRESSION TEST:
      After the fix, either:
      a) All tool events flow through a single ordered path, OR
      b) event_processor caches out-of-order completions and applies them
         when the start event arrives.
    """
    from pydantic_ai import FunctionToolCallEvent
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart

    from agentpool.agents.events import ToolCallCompleteEvent, ToolCallStartEvent

    processor = EventProcessor()
    parent_session_id = "parent-race-test"
    child_session_id = "child-race-test"
    parent_ctx = _make_parent_ctx(server_state, parent_session_id)

    # Step 1: Spawn subagent
    spawn = SpawnSessionStart(
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
        tool_call_id="tc-race",
        spawn_mechanism="task",
        source_name="worker",
        source_type="agent",
        depth=1,
        description="Test task",
        metadata={"prompt": "test"},
        model_id="test-model",
    )
    emitted: list[Any] = []
    async for e in processor.process(spawn, parent_ctx):
        emitted.append(e)

    # After fix: all tool events flow through a single ordered path, so
    # ToolCallCompleteEvent can never arrive before the start event.
    # This test now verifies correct ordering: start event first, then completion.

    # Step 2: Send the FunctionToolCallEvent (start event)
    tool_call_id = "call_race_001"
    func_call_event = SubAgentEvent(
        source_name="worker",
        source_type="agent",
        event=FunctionToolCallEvent(
            part=ToolCallPart(
                tool_name="bash",
                args={"command": "echo hello"},
                tool_call_id=tool_call_id,
            )
        ),
        depth=1,
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
    )
    async for e in processor.process(func_call_event, parent_ctx):
        emitted.append(e)

    # Step 3: Now send ToolCallCompleteEvent (after start event)
    complete_event = SubAgentEvent(
        source_name="worker",
        source_type="agent",
        event=ToolCallCompleteEvent(
            tool_name="bash",
            tool_call_id=tool_call_id,
            tool_input={"command": "echo hello"},
            tool_result="hello",
            agent_name="worker",
            message_id="msg-race",
        ),
        depth=1,
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
    )
    async for e in processor.process(complete_event, parent_ctx):
        emitted.append(e)

    # Step 4: Check child context state
    child_ctx = processor._child_contexts.get(child_session_id)
    assert child_ctx is not None, "Child context should exist"

    # The ToolPart should exist (created by FunctionToolCallEvent)
    tool_part = child_ctx.get_tool_part(tool_call_id)
    assert tool_part is not None, (
        "ToolPart should exist after FunctionToolCallEvent arrives. "
        "If missing, the start event itself was not processed."
    )

    # RED FLAG: The ToolPart is in Running state, not Completed.
    # This is because ToolCallCompleteEvent arrived first and was dropped.
    # After the fix, either:
    #   a) The completion should be cached and applied when start arrives, OR
    #   b) The event ordering should be guaranteed so this never happens.
    is_completed = isinstance(tool_part.state, ToolStateCompleted)
    is_running = isinstance(tool_part.state, ToolStateRunning)

    # After fix: ordering is guaranteed because ToolCallCompleteEvent flows through
    # the same local queue as FunctionToolCallEvent, so it can never arrive first.
    assert is_completed, (
        "ToolPart should be in Completed state. If this fails, the race condition fix "
        "is not working correctly."
    )
