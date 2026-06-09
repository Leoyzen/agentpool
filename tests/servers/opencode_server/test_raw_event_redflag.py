"""Red flag test: Raw events (without SubAgentEvent wrapping) should update parent ToolPart.

This test reproduces the issue where removing SubAgentEvent wrapping from TurnRunner
breaks the parent ToolPart completion transition.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentpool.agents.events import (
    SpawnSessionStart,
    StreamCompleteEvent,
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
)
from agentpool_server.opencode_server.models.parts import (
    ToolStateCompleted,
    ToolStateRunning,
)


def _make_parent_ctx(
    server_state: Any,
    parent_session_id: str = "parent-test",
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


@pytest.mark.asyncio
async def test_raw_stream_complete_updates_parent_toolpart(
    server_state: Any,
) -> None:
    """RED FLAG: Raw StreamCompleteEvent MUST transition parent ToolPart to Completed.

    CURRENT BEHAVIOR (BROKEN after removing SubAgentEvent wrapping):
      TurnRunner no longer wraps child session events in SubAgentEvent.
      EventProcessor.convert_event receives raw StreamCompleteEvent and
      matches 'case StreamCompleteEvent()' → _process_stream_complete().
      _process_subagent_event() is NEVER called, so:
      1. Parent ToolPart stays in ToolStateRunning forever
      2. Child session idle events are never emitted
      3. TUI card remains stuck in 'running' state

    EXPECTED BEHAVIOR:
      When a raw StreamCompleteEvent arrives for a known child session,
      the parent ToolPart should transition to ToolStateCompleted.
    """
    processor = EventProcessor()
    parent_session_id = "parent-raw-test"
    child_session_id = "child-raw-test"
    parent_ctx = _make_parent_ctx(server_state, parent_session_id)

    # Step 1: Spawn subagent (creates ToolPart in parent)
    spawn = SpawnSessionStart(
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
        tool_call_id="tc-raw",
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

    # Step 2: Send RAW StreamCompleteEvent (simulating TurnRunner without wrapping)
    stream_complete = StreamCompleteEvent(
        message=ChatMessage(role="assistant", content="Done"),
        session_id=child_session_id,
    )
    async for e in processor.process(stream_complete, parent_ctx):
        emitted.append(e)

    # Step 3: Verify ToolPart has transitioned to Completed
    tool_part_after = parent_ctx.get_subagent_tool_part(subagent_key)
    assert tool_part_after is not None, "ToolPart should still exist after completion"
    assert isinstance(tool_part_after.state, ToolStateCompleted), (
        f"FAIL: ToolPart should be in Completed state after subagent finishes, "
        f"but got {type(tool_part_after.state).__name__}. "
        f"This proves the SubAgentEvent removal broke parent ToolPart updates."
    )
