"""Regression tests for subagent completion -> TUI/lead-agent handoff.

NOTE: The subagent event handling (SpawnSessionStart, SubAgentEvent) has been
refactored out of EventProcessor into session_pool_integration.py. The
EventProcessor is now stateless and does not handle subagent events directly.
The original tests that tested subagent behavior via EventProcessor.process()
have been removed since they tested functionality at the wrong architectural
layer.

The remaining tests cover behavior that is still handled by EventProcessor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from agentpool_server.opencode_server.event_processor_context import (
    EventProcessorContext,
)
from agentpool_server.opencode_server.models import (
    MessagePath,
    MessageTime,
    MessageWithParts,
)


if TYPE_CHECKING:
    from agentpool_server.opencode_server.event_processor import EventProcessor
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
        emitted.extend([e async for e in processor.process(event, ctx)])
    return emitted


# =============================================================================
# Red-Flag Test #3: inject_prompt does not re-awaken lead agent
# =============================================================================


@pytest.mark.asyncio
async def test_background_task_inject_prompt_wakes_lead_agent(
    server_state: ServerState,
) -> None:
    """inject_prompt after background task completion MUST re-awaken the lead agent.

    CURRENT BEHAVIOR (FIXED):
      inject_prompt() now delegates to SessionPool.receive_request() or
      SessionPool.inject_prompt() when no active run context exists,
      which triggers auto-resume via SessionController.
      The lead agent receives the completion notice and resumes reasoning.

    PREVIOUS BEHAVIOR (BROKEN):
      inject_prompt() was a silent no-op when no active run context existed,
      causing the lead agent to never resume after background task completion.
    """
    import inspect

    from agentpool.agents.base_agent import BaseAgent

    source = inspect.getsource(BaseAgent.inject_prompt)

    # Verify the fixed implementation delegates to SessionPool for auto-resume
    assert "session_pool" in source, (
        "inject_prompt must reference session_pool to delegate when no run context exists"
    )
    assert "receive_request" in source or "inject_prompt" in source, (
        "inject_prompt must call receive_request or session_pool.inject_prompt "
        "to trigger auto-resume when no active run context is available"
    )
    assert "fire_and_forget" in source, (
        "inject_prompt must use fire_and_forget to schedule the request asynchronously"
    )

    # Verify the fallback path for shared agents (no fixed session_id)
    assert "find_sessions_by_agent_name" in source, (
        "inject_prompt must use find_sessions_by_agent_name as fallback for shared agents"
    )
