"""End-to-end red flag test for SessionPool + OpenCode event flow.

This test simulates the exact scenario described by the user:
- Model outputs reasoning
- Events should flow through SessionPool -> EventBus -> SSE
- But currently no events reach the frontend
"""

import asyncio

from pydantic_ai.messages import (
    PartStartEvent,
    ThinkingPart,
)
import pytest

from agentpool.orchestrator.core import EventBus
from agentpool_server.opencode_server.session_pool_integration import OpenCodeSessionPoolIntegration


class MockServerState:
    """Mock OpenCode ServerState for testing."""

    def __init__(self):
        self.messages = {}
        self.events = []
        self.working_dir = "/tmp"

    async def broadcast_event(self, event):
        self.events.append(event)


class MockSessionPool:
    """Mock SessionPool for testing."""

    def __init__(self):
        self.event_bus = EventBus()
        self.sessions = MockSessions()

    async def receive_request(
        self, session_id, content, priority="when_idle", input_provider=None, **kwargs
    ):
        return None


class MockSessions:
    """Mock Sessions manager."""

    def __init__(self):
        self._sessions = {}
        self._session_agents = {}

    async def get_or_create_session(self, session_id, agent_name=None, **metadata):
        if session_id not in self._sessions:
            from agentpool.orchestrator.core import SessionState

            state = SessionState(
                session_id=session_id,
                agent_name=agent_name or "default",
            )
            self._sessions[session_id] = state
            return state, True
        return self._sessions[session_id], False

    def get_session(self, session_id):
        return self._sessions.get(session_id)


@pytest.mark.asyncio
async def test_send_message_async_does_not_start_consumer():
    """Red-flag: send_message_async calls session_pool.receive_request directly
    without going through integration.route_message, so the event consumer
    is never started for new sessions.
    """
    server_state = MockServerState()
    session_pool = MockSessionPool()

    OpenCodeSessionPoolIntegration(
        session_pool=session_pool,
        server_state=server_state,
    )

    session_id = "test_session"

    # Simulate send_message_async behavior (direct call to receive_request)
    # WITHOUT calling integration.create_session or integration.route_message
    await session_pool.sessions.get_or_create_session(session_id, agent_name="default")
    # Note: we do NOT call integration.create_session() here

    # Publish a thinking event to the EventBus
    thinking_event = PartStartEvent(index=0, part=ThinkingPart(content="Let me think..."))
    await session_pool.event_bus.publish(session_id, thinking_event)

    # Give consumer loop time to process (if it exists)
    await asyncio.sleep(0.1)

    # Verify no events were broadcast to OpenCode
    assert len(server_state.events) == 0, (
        f"Expected NO OpenCode events (consumer not started), got: {server_state.events}"
    )


@pytest.mark.asyncio
async def test_integration_route_message_starts_consumer():
    """Verify that integration.route_message starts the event consumer
    and events are broadcast.
    """
    server_state = MockServerState()
    session_pool = MockSessionPool()

    integration = OpenCodeSessionPoolIntegration(
        session_pool=session_pool,
        server_state=server_state,
    )

    session_id = "test_session"

    # Call integration.route_message which should create session and start consumer
    # Note: route_message expects a real SessionPool, our mock is minimal
    # So we manually call create_session instead
    await integration.create_session(session_id, agent_name="default")

    # Publish a thinking event
    thinking_event = PartStartEvent(index=0, part=ThinkingPart(content="Let me think..."))
    await session_pool.event_bus.publish(session_id, thinking_event)

    # Give consumer loop time to process
    await asyncio.sleep(0.1)

    # Verify events WERE broadcast
    assert len(server_state.events) > 0, (
        f"Expected OpenCode events (consumer started), got: {server_state.events}"
    )

    # Stop consumer
    await integration._stop_event_consumer(session_id)


@pytest.mark.asyncio
async def test_integration_route_message_starts_consumer_for_existing_session():
    """Red-flag: route_message must start consumer even for pre-existing sessions.
    Sessions created via other paths (e.g. get_or_load_session) don't have
    the consumer started, which would leave EventBus events unconsumed.
    """
    server_state = MockServerState()
    session_pool = MockSessionPool()

    integration = OpenCodeSessionPoolIntegration(
        session_pool=session_pool,
        server_state=server_state,
    )

    session_id = "test_session"

    # Simulate session created via another path (e.g. get_or_load_session)
    # that does NOT start the event consumer
    await session_pool.sessions.get_or_create_session(session_id, agent_name="default")

    # Verify consumer is NOT running yet
    assert session_id not in integration._session_groups

    # Call route_message - should detect missing consumer and start it
    await integration.route_message(
        session_id=session_id,
        content="test prompt",
        priority="when_idle",
    )

    # Give consumer loop time to subscribe
    await asyncio.sleep(0)

    # Publish a thinking event
    thinking_event = PartStartEvent(index=0, part=ThinkingPart(content="Let me think..."))
    await session_pool.event_bus.publish(session_id, thinking_event)
    await asyncio.sleep(0.1)

    # Verify events WERE broadcast
    assert len(server_state.events) > 0, (
        f"Expected OpenCode events (consumer started), got: {server_state.events}"
    )

    # Stop consumer
    await integration._stop_event_consumer(session_id)


@pytest.mark.asyncio
async def test_consumer_restarted_after_crash():
    """Red-flag: If consumer loop crashes, _start_event_consumer should restart it
    by cleaning up the old task and starting a new one.
    """
    server_state = MockServerState()
    session_pool = MockSessionPool()

    integration = OpenCodeSessionPoolIntegration(
        session_pool=session_pool,
        server_state=server_state,
    )

    session_id = "test_session"

    # Start consumer
    await integration._start_event_consumer(session_id)

    # Verify it's running
    assert session_id in integration._session_groups
    assert session_id in integration._consumer_streams

    await integration.stop_event_consumer(session_id)

    await integration._start_event_consumer(session_id)

    assert session_id in integration._session_groups
    assert session_id in integration._consumer_streams

    # Yield control so the new consumer can finish subscribing to EventBus
    await asyncio.sleep(0)

    # Publish event - new consumer should process it
    thinking_event = PartStartEvent(index=0, part=ThinkingPart(content="Let me think..."))
    await session_pool.event_bus.publish(session_id, thinking_event)
    await asyncio.sleep(0.1)

    assert len(server_state.events) > 0, (
        f"Expected events after restart, got: {server_state.events}"
    )

    # Stop consumer
    await integration._stop_event_consumer(session_id)
