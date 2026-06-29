"""E2E test for subagent ToolPart state transitions through the full pipeline.

This test verifies that when a subagent spawns and completes, the parent
ToolPart transitions correctly from Running -> Completed, preventing the
UI from showing a stuck "running" state.

REGRESSION TEST: Previously, SpawnSessionStart was handled with 'continue'
in _event_consumer_loop before EventProcessor could register the ToolPart
in its subagent_tool_parts dict. When SubAgentEvent(StreamCompleteEvent)
arrived later, EventProcessor could not find the ToolPart to update it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic_ai.models.test import TestModel

from agentpool import AgentPool, AgentsManifest, NativeAgentConfig
from agentpool.agents.events import SpawnSessionStart, StreamCompleteEvent
from agentpool.messaging import ChatMessage
from agentpool_server.opencode_server.session_pool_integration import (
    OpenCodeSessionPoolIntegration,
)
from agentpool_server.opencode_server.models import PartUpdatedEvent
from agentpool_server.opencode_server.models.parts import (
    ToolPart,
    ToolStateCompleted,
    ToolStateRunning,
)
from agentpool_server.opencode_server.state import ServerState


class MockServerState:
    """Minimal mock of OpenCode ServerState for testing."""

    def __init__(self) -> None:
        self.messages: dict[str, list[Any]] = {}
        self.events: list[Any] = []
        self.working_dir = "/tmp"
        self.agent = None
        self.pool = None
        self.session_status: dict[str, Any] = {}
        self.sessions: dict[str, Any] = {}
        self.session_locks: dict[str, Any] = {}

    async def broadcast_event(self, event: Any) -> None:
        self.events.append(event)


def _get_last_assistant_message(state: MockServerState, session_id: str) -> Any | None:
    """Get the last assistant message for a session."""
    messages = state.messages.get(session_id, [])
    for msg in reversed(messages):
        if hasattr(msg, "info") and hasattr(msg.info, "role") and msg.info.role == "assistant":
            return msg
    return None


def _get_tool_part_for_child(msg: Any, child_session_id: str) -> ToolPart | None:
    """Find the ToolPart representing a child session."""
    for part in msg.parts:
        if (
            isinstance(part, ToolPart)
            and part.state is not None
            and hasattr(part.state, "metadata")
            and isinstance(part.state.metadata, dict)
            and part.state.metadata.get("sessionId") == child_session_id
        ):
            return part
    return None


@pytest.mark.integration
async def test_subagent_toolpart_transitions_running_to_completed() -> None:
    """Full lifecycle: SpawnSessionStart -> StreamCompleteEvent -> ToolPart Completed.

    This is an end-to-end test that exercises _event_consumer_loop, not just
    EventProcessor in isolation. The bug only appeared because _event_consumer_loop
    handled SpawnSessionStart with 'continue' before EventProcessor saw the event.
    """
    agent_config = NativeAgentConfig(
        name="test_agent",
        model="test",
        system_prompt="You are a test agent",
    )
    manifest = AgentsManifest(agents={"test_agent": agent_config})

    async with AgentPool(manifest) as pool:
        session_pool = pool.session_pool
        assert session_pool is not None
        await session_pool.start()

        server_state = MockServerState()
        server_state.pool = pool

        integration = OpenCodeSessionPoolIntegration(
            session_pool=session_pool,
            server_state=server_state,  # type: ignore[arg-type]
        )

        parent_session_id = "parent-e2e-test"
        child_session_id = "child-e2e-test"

        # Pre-create parent session so consumer has something to subscribe to
        await session_pool.create_session(parent_session_id, agent_name="test_agent")

        # Pre-create child session with parent relationship so EventBus
        # descendants scope routes child events to parent consumer.
        await session_pool.create_session(
            child_session_id,
            parent_session_id=parent_session_id,
            agent_name="worker",
        )

        # Start the event consumer for the parent session
        await integration._start_event_consumer(parent_session_id)

        # Give consumer time to subscribe
        await asyncio.sleep(0.05)

        # Phase 1: Publish SpawnSessionStart (simulating subagent spawn)
        spawn_event = SpawnSessionStart(
            child_session_id=child_session_id,
            parent_session_id=parent_session_id,
            tool_call_id="tc-1",
            spawn_mechanism="task",
            source_name="worker",
            source_type="agent",
            depth=1,
            description="Test subagent task",
            metadata={"prompt": "do something"},
            model_id="test-model",
        )
        await session_pool.event_bus.publish(parent_session_id, spawn_event)

        # Wait for consumer to process SpawnSessionStart and create ToolPart
        await asyncio.sleep(0.1)

        # ASSERTION 1: ToolPart should exist in Running state
        assistant_msg = _get_last_assistant_message(server_state, parent_session_id)
        assert assistant_msg is not None, "No assistant message found after SpawnSessionStart"

        tool_part = _get_tool_part_for_child(assistant_msg, child_session_id)
        assert tool_part is not None, (
            f"No ToolPart found for child session {child_session_id}. "
            "SpawnSessionStart handling may have failed to create it."
        )
        assert isinstance(tool_part.state, ToolStateRunning), (
            f"Expected ToolStateRunning, got {type(tool_part.state).__name__}"
        )
        assert tool_part.state.time.start is not None, "ToolPart should have start time"

        # Phase 2: Publish StreamCompleteEvent for child session
        # The parent consumer subscribes with scope="descendants", so it will
        # receive events published on child sessions too.
        complete_event = StreamCompleteEvent(
            message=ChatMessage(role="assistant", content="Task completed successfully"),
            session_id=child_session_id,
        )
        await session_pool.event_bus.publish(child_session_id, complete_event)

        # Wait for consumer to process completion and update ToolPart
        await asyncio.sleep(0.1)

        # ASSERTION 2: ToolPart should now be Completed
        assistant_msg = _get_last_assistant_message(server_state, parent_session_id)
        assert assistant_msg is not None

        tool_part = _get_tool_part_for_child(assistant_msg, child_session_id)
        assert tool_part is not None, (
            f"ToolPart for child {child_session_id} disappeared after StreamCompleteEvent"
        )
        assert isinstance(tool_part.state, ToolStateCompleted), (
            f"Expected ToolStateCompleted after subagent finished, "
            f"got {type(tool_part.state).__name__}. "
            f"The ToolPart is stuck in a non-completed state. "
            f"This usually means _event_consumer_loop or _update_parent_toolpart failed."
        )
        assert tool_part.state.time.end is not None, (
            "Completed ToolPart should have end time set"
        )
        assert tool_part.state.output == "Task completed successfully", (
            f"ToolPart output mismatch: {tool_part.state.output}"
        )

        # Cleanup
        await integration._stop_event_consumer(parent_session_id)
        await session_pool.shutdown()


@pytest.mark.integration
async def test_subagent_toolpart_handles_multiple_child_events() -> None:
    """Verify ToolPart transitions correctly even with intermediate child events."""
    agent_config = NativeAgentConfig(
        name="test_agent",
        model="test",
        system_prompt="You are a test agent",
    )
    manifest = AgentsManifest(agents={"test_agent": agent_config})

    async with AgentPool(manifest) as pool:
        session_pool = pool.session_pool
        assert session_pool is not None
        await session_pool.start()

        server_state = MockServerState()
        server_state.pool = pool

        integration = OpenCodeSessionPoolIntegration(
            session_pool=session_pool,
            server_state=server_state,  # type: ignore[arg-type]
        )

        parent_session_id = "parent-multi-test"
        child_session_id = "child-multi-test"

        await session_pool.create_session(parent_session_id, agent_name="test_agent")
        await session_pool.create_session(
            child_session_id,
            parent_session_id=parent_session_id,
            agent_name="analyzer",
        )
        await integration._start_event_consumer(parent_session_id)
        await asyncio.sleep(0.05)

        # Spawn subagent
        spawn_event = SpawnSessionStart(
            child_session_id=child_session_id,
            parent_session_id=parent_session_id,
            tool_call_id="tc-2",
            spawn_mechanism="task",
            source_name="analyzer",
            source_type="agent",
            depth=1,
            description="Analysis task",
            metadata={"prompt": "analyze this"},
        )
        await session_pool.event_bus.publish(parent_session_id, spawn_event)
        await asyncio.sleep(0.1)

        # Verify initial Running state
        assistant_msg = _get_last_assistant_message(server_state, parent_session_id)
        assert assistant_msg is not None
        tool_part = _get_tool_part_for_child(assistant_msg, child_session_id)
        assert tool_part is not None, "ToolPart should exist after SpawnSessionStart"
        assert isinstance(tool_part.state, ToolStateRunning)

        # Publish completion
        complete_event = StreamCompleteEvent(
            message=ChatMessage(role="assistant", content="Analysis done"),
            session_id=child_session_id,
        )
        await session_pool.event_bus.publish(child_session_id, complete_event)
        await asyncio.sleep(0.1)

        # Verify final Completed state
        assistant_msg = _get_last_assistant_message(server_state, parent_session_id)
        assert assistant_msg is not None
        tool_part = _get_tool_part_for_child(assistant_msg, child_session_id)
        assert tool_part is not None, "ToolPart should still exist after completion"
        assert isinstance(tool_part.state, ToolStateCompleted), (
            f"ToolPart stuck in {type(tool_part.state).__name__} after completion"
        )

        await integration._stop_event_consumer(parent_session_id)
        await session_pool.shutdown()
