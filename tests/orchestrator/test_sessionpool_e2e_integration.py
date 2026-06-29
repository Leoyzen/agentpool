"""E2E integration test: AgentPool -> SessionPool -> EventBus -> OpenCode SSE.

This test verifies the complete event flow from agent execution through
the SessionPool orchestration layer to OpenCode SSE events, ensuring
reasoning/text events reach the frontend.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest
from pydantic_ai.models.test import TestModel

from agentpool import AgentPool, AgentsManifest, NativeAgentConfig
from agentpool.orchestrator.core import SessionPool
from agentpool_server.opencode_server.session_pool_integration import (
    OpenCodeSessionPoolIntegration,
)


class MockServerState:
    """Minimal mock of OpenCode ServerState for testing."""

    def __init__(self) -> None:
        self.messages: dict[str, list[Any]] = {}
        self.events: list[Any] = []
        self.working_dir = "/tmp"
        self.agent = None
        self.pool = None
        self.session_status: dict[str, Any] = {}

    async def broadcast_event(self, event: Any) -> None:
        self.events.append(event)


@pytest.mark.integration
async def test_e2e_reasoning_events_through_sessionpool() -> None:
    """End-to-end: AgentPool -> SessionPool -> OpenCode events.

    Verifies that when a model produces reasoning output, the events flow
    through the entire pipeline and reach the SSE broadcast layer.
    """
    # Create a real AgentPool with a TestModel that produces text
    agent_config = NativeAgentConfig(
        name="test_agent",
        model="test",
        system_prompt="You are a test agent",
    )
    manifest = AgentsManifest(agents={"test_agent": agent_config})

    async with AgentPool(manifest) as pool:
        session_pool = pool.session_pool
        assert session_pool is not None

        server_state = MockServerState()
        server_state.pool = pool

        integration = OpenCodeSessionPoolIntegration(
            session_pool=session_pool,
            server_state=server_state,
        )

        session_id = "test-session"

        # Route message through integration (this should start consumer)
        run_handle = await integration.route_message(
            session_id=session_id,
            content="hello",
            priority="when_idle",
        )

        if run_handle is not None:
            # Wait for run to complete
            await run_handle.complete_event.wait()

            # Give consumer time to process events
            await asyncio.sleep(0.2)

        # Stop consumer
        await integration._stop_event_consumer(session_id)

        # Verify events were broadcast
        assert len(server_state.events) > 0, (
            f"Expected SSE events to be broadcast, got {len(server_state.events)}. "
            "Event consumer may not have been started."
        )

        # Verify at least some events are message-related (not just session created)
        event_types = [type(e).__name__ for e in server_state.events]
        print(f"Broadcast events: {event_types}")

        # Should have PartUpdatedEvent or ReasoningPart events
        from agentpool_server.opencode_server.models import PartUpdatedEvent

        part_events = [e for e in server_state.events if isinstance(e, PartUpdatedEvent)]
        assert len(part_events) > 0, (
            f"Expected PartUpdatedEvent in broadcast, got: {event_types}. "
            "Events may not be flowing through EventProcessor."
        )


@pytest.mark.integration
@pytest.mark.slow
async def test_e2e_pre_existing_session_consumer_started() -> None:
    """Consumer must start even when session already exists in SessionPool."""
    agent_config = NativeAgentConfig(
        name="test_agent",
        model="test",
        system_prompt="You are a test agent",
    )
    manifest = AgentsManifest(agents={"test_agent": agent_config})

    async with AgentPool(manifest) as pool:
        session_pool = pool.session_pool
        assert session_pool is not None

        server_state = MockServerState()
        server_state.pool = pool

        integration = OpenCodeSessionPoolIntegration(
            session_pool=session_pool,
            server_state=server_state,
        )

        session_id = "pre-existing-session"

        # Pre-create session directly in SessionPool (simulates get_or_load_session)
        await session_pool.create_session(session_id, agent_name="test_agent")

        # Now route message - consumer should still start
        run_handle = await integration.route_message(
            session_id=session_id,
            content="hello",
            priority="when_idle",
        )

        if run_handle is not None:
            await run_handle.complete_event.wait()
            await asyncio.sleep(0.2)

        await integration._stop_event_consumer(session_id)

        # Verify events were broadcast
        assert len(server_state.events) > 0, (
            f"Expected SSE events for pre-existing session, got {len(server_state.events)}"
        )
