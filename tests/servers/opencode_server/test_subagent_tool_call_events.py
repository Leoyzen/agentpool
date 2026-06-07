"""Test subagent tool call event emission through EventProcessor."""

import pytest
from pydantic_ai import FunctionToolCallEvent
from pydantic_ai.messages import ToolCallPart

from agentpool.agents.events import SpawnSessionStart, SubAgentEvent, ToolCallCompleteEvent
from agentpool_server.opencode_server.event_processor import EventProcessor
from agentpool_server.opencode_server.models import MessagePath, MessageTime, MessageWithParts
from agentpool_server.opencode_server.models.parts import ToolPart


@pytest.mark.asyncio
async def test_subagent_function_tool_call_creates_child_tool_part(server_state):
    """Verify that SubAgentEvent wrapping FunctionToolCallEvent creates ToolPart in child session."""
    processor = EventProcessor()
    
    parent_assistant_msg = MessageWithParts.assistant(
        message_id="parent-msg-1",
        session_id="parent-session",
        time=MessageTime(created=0),
        agent_name="parent-agent",
        model_id="test-model",
        parent_id="parent-user-1",
        provider_id="agentpool",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )
    parent_ctx = processor.main_context = None  # Not used directly
    
    # We need to create the context manually
    from agentpool_server.opencode_server.event_processor_context import EventProcessorContext
    parent_ctx = EventProcessorContext(
        session_id="parent-session",
        assistant_msg_id="parent-msg-1",
        assistant_msg=parent_assistant_msg,
        state=server_state,
        working_dir="/tmp",
    )
    
    # Step 1: SpawnSessionStart
    spawn = SpawnSessionStart(
        child_session_id="child-session",
        parent_session_id="parent-session",
        spawn_mechanism="task",
        source_name="subagent",
        source_type="agent",
        depth=1,
        description="Run subagent task",
    )
    async for _ in processor.process(spawn, parent_ctx):
        pass
    
    # Step 2: SubAgentEvent wrapping FunctionToolCallEvent
    tc_part = ToolCallPart(tool_call_id="tc-123", tool_name="bash", args="{\"command\":\"ls\"}")
    ftce = FunctionToolCallEvent(part=tc_part)
    subagent_event = SubAgentEvent(
        source_name="subagent",
        source_type="agent",
        event=ftce,
        depth=1,
        child_session_id="child-session",
        parent_session_id="parent-session",
    )
    events = []
    async for e in processor.process(subagent_event, parent_ctx):
        events.append(e)
    
    # Should yield PartUpdatedEvent for the ToolPart
    assert len(events) > 0, "FunctionToolCallEvent wrapped in SubAgentEvent should yield events"
    
    # Check child session has ToolPart
    child_messages = server_state.messages.get("child-session", [])
    assert len(child_messages) >= 1, "Child session should have messages"
    
    assistant_msgs = [m for m in child_messages if getattr(m.info, 'role', None) == 'assistant']
    assert len(assistant_msgs) >= 1, "Child session should have assistant message"
    
    tool_parts = [p for m in assistant_msgs for p in m.parts if isinstance(p, ToolPart)]
    assert len(tool_parts) >= 1, f"Child assistant message should have ToolPart, got parts: {[type(p).__name__ for m in assistant_msgs for p in m.parts]}"
    
    tool_part = tool_parts[0]
    assert tool_part.tool == "bash", f"Tool should be 'bash', got '{tool_part.tool}'"
    
    print("SUCCESS: Subagent FunctionToolCallEvent creates ToolPart in child session")


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
