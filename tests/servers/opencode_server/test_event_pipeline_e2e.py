"""E2E tests for the OpenCode event pipeline.

Simulates a full agent streaming session from agent event emission through
EventBus → session-scoped consumer → event_bridge → EventBus → SSE subscriber.
Verifies every OpenCode event type produced by the pipeline reaches the scope="all"
subscriber (representing the SSE frontend).

Covers the regression where PartUpdatedEvent (which has session_id at
properties.part.session_id, not properties.session_id) was silently dropped
by event_bridge._extract_session_id().
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, Mock

from pydantic_ai.messages import (
    PartDeltaEvent as PydanticPartDeltaEvent,
    PartStartEvent,
    TextPartDelta,
)
import pytest

from agentpool.agents.events import (
    RunStartedEvent,
    StreamCompleteEvent,
    ToolCallCompleteEvent,
    ToolCallStartEvent,
)
from agentpool.messaging import ChatMessage
from agentpool_server.opencode_server.models import PartDeltaEvent, PartUpdatedEvent
from agentpool_server.opencode_server.models.events import (
    SessionErrorEvent,
    SessionStatusEvent,
)
from agentpool_server.opencode_server.models.parts import (
    StepFinishPart,
    TextPart,
    ToolPart,
    ToolStateCompleted,
    ToolStateRunning,
)
from agentpool_server.opencode_server.session_pool_integration import (
    OpenCodeSessionPoolIntegration,
)
from agentpool_server.opencode_server.state import ServerState


pytestmark = pytest.mark.integration


if TYPE_CHECKING:
    from agentpool.orchestrator.core import SessionPool


# =============================================================================
# Helpers
# =============================================================================


def _stream_empty(queue: asyncio.Queue[Any]) -> bool:
    """Check if a subscriber queue has no buffered items."""
    return queue.empty()


def _extract_opencode_events(sse_queue: Any) -> list[Any]:
    """Extract OpenCode events from the SSE subscriber queue.

    The SSE subscriber receives EventEnvelope objects where the ``.event``
    field can be a CustomEvent wrapper (from event_bridge) or a raw agent
    event.  This helper filters for CustomEvent wrappers and extracts the
    underlying OpenCode event from ``.event_data``.
    """
    from agentpool.agents.events.events import CustomEvent
    from agentpool.orchestrator.core import EventEnvelope

    result: list[Any] = []
    while not _stream_empty(sse_queue):
        envelope = sse_queue.get_nowait()
        if isinstance(envelope, EventEnvelope):
            inner = envelope.event
            if isinstance(inner, CustomEvent) and inner.event_data is not None:
                result.append(inner.event_data)
    return result


async def _async_wait(seconds: float = 0.1) -> None:
    """Await briefly for async event propagation."""
    await asyncio.sleep(seconds)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def server_state(tmp_path: Any) -> ServerState:
    """Create a minimal ServerState with a mock agent for testing."""
    agent = Mock()
    agent.model_name = None  # resolve_default_model_info() fallback
    agent.name = "test-agent"
    agent.storage = Mock()
    agent.host_context = Mock()
    agent._agent_pool = agent.host_context  # state.py resolves _pool via agent._agent_pool
    agent.host_context.session_pool = Mock()
    agent.host_context.session_pool.event_bus = Mock()  # will be overridden
    state = ServerState(working_dir=str(tmp_path), agent=agent)
    # Required for SessionPoolIntegration consumer to function
    state.messages = {}
    return state


@pytest.fixture
async def session_pool(server_state: ServerState):  # type: ignore[no-untyped-def]
    """Create a real SessionPool with a real EventBus."""
    from agentpool.orchestrator.core import SessionPool

    pool_mock = Mock()
    pool_mock.main_agent = Mock()
    pool_mock.main_agent.name = "test-agent"
    pool_mock.manifest = Mock()
    pool_mock.manifest.agents = {}
    pool_mock._config_file_path = None

    store_mock = Mock()
    store_mock.save_session = AsyncMock(return_value=None)
    store_mock.delete_session = AsyncMock(return_value=None)
    store_mock.load_session = AsyncMock(return_value=None)
    store_mock.list_session_ids = AsyncMock(return_value=[])

    sp = SessionPool(
        pool=pool_mock,
        store=store_mock,
        enable_auto_resume=False,
        enable_event_bus=True,
    )
    await sp.start()

    # Wire the EventBus into server_state so event_bridge can discover it
    server_state._pool = pool_mock
    pool_mock.session_pool = sp
    # Re-initialize event_bridge now that event_bus is available
    from agentpool_server.opencode_server.event_bridge import OpenCodeEventBridge

    server_state.event_bridge = OpenCodeEventBridge(server_state, sp.event_bus)

    yield sp
    await sp.shutdown()


# =============================================================================
# Tests
# =============================================================================


class TestEventPipelineE2E:
    """Full pipeline: agent event → EventBus → consumer → event_bridge → EventBus → SSE."""

    @pytest.mark.asyncio
    async def test_text_streaming_full_pipeline(
        self,
        session_pool: SessionPool,
        server_state: ServerState,
    ) -> None:
        """PartStartEvent → PartUpdatedEvent(text) → PartDeltaEvent → SSE subscriber.

        Simulates: agent emits PartStartEvent(text), followed by delta events,
        followed by StreamComplete. Verifies that PartUpdatedEvent (with nested
        session_id at properties.part.session_id) correctly reaches the scope="all"
        subscriber.
        """
        integration = OpenCodeSessionPoolIntegration(
            session_pool=session_pool,
            server_state=server_state,
        )
        session_id = "e2e-text-stream"

        # Create session and start consumer
        await integration.create_session(session_id=session_id, agent_name="test-agent")
        await _async_wait(0.1)

        # Subscribe a scope="all" subscriber to mimic SSE frontend
        sse_queue = await session_pool.event_bus.subscribe("__global_sse__", scope="all")

        # Simulate agent emitting PartStartEvent (text)
        text_start = PartStartEvent(index=0, part=pydantic_text_part("Hello"))
        await session_pool.event_bus.publish(session_id, text_start)
        await _async_wait(0.15)

        # Simulate agent emitting PartDeltaEvent
        text_delta = PydanticPartDeltaEvent(index=0, delta=TextPartDelta(content_delta=" world"))
        await session_pool.event_bus.publish(session_id, text_delta)
        await _async_wait(0.15)

        # Simulate agent emitting StreamCompleteEvent
        complete_msg = ChatMessage(content="Hello world", role="assistant")
        complete = StreamCompleteEvent[Any](message=complete_msg)  # type: ignore[arg-type]
        await session_pool.event_bus.publish(session_id, complete)
        await _async_wait(0.15)

        # Collect OpenCode events from SSE subscriber (filters for wrapped events)
        oc_events = _extract_opencode_events(sse_queue)

        # Extract PartUpdatedEvents and PartDeltaEvents
        part_updated_events = [e for e in oc_events if isinstance(e, PartUpdatedEvent)]
        part_delta_events = [e for e in oc_events if isinstance(e, PartDeltaEvent)]

        # Assert: Text PartUpdatedEvent reached SSE (regression test for nested session_id)
        assert len(part_updated_events) >= 1, (
            "PartUpdatedEvent should reach scope='all' subscriber; "
            "event_bridge._extract_session_id must traverse properties.part.session_id"
        )

        # The first PartUpdatedEvent should contain a TextPart
        text_part_events = [
            e for e in part_updated_events if isinstance(e.properties.part, TextPart)
        ]
        assert len(text_part_events) >= 1, (
            "At least one PartUpdatedEvent should contain a TextPart "
            "(from PartStartEvent or StreamCompleteEvent handling)"
        )

        # Assert: PartDeltaEvents reached SSE
        assert len(part_delta_events) >= 1, "PartDeltaEvent should reach SST subscriber"

        # Assert: StreamComplete produces StepFinishPart as PartUpdatedEvent
        step_finish_events = [
            e for e in part_updated_events if isinstance(e.properties.part, StepFinishPart)
        ]
        assert len(step_finish_events) >= 1, (
            "StreamCompleteEvent should produce StepFinishPart PartUpdatedEvent"
        )

        # Cleanup
        await integration._stop_event_consumer(session_id)

    @pytest.mark.asyncio
    async def test_tool_call_full_pipeline(
        self,
        session_pool: SessionPool,
        server_state: ServerState,
    ) -> None:
        """ToolCallStartEvent → ToolPart (running) → ToolCallComplete → ToolPart (completed).

        Verifies tool call events produce ToolPart PartUpdatedEvents that reach SSE.
        """
        integration = OpenCodeSessionPoolIntegration(
            session_pool=session_pool,
            server_state=server_state,
        )
        session_id = "e2e-tool-call"

        await integration.create_session(session_id=session_id, agent_name="test-agent")
        await _async_wait(0.1)

        sse_queue = await session_pool.event_bus.subscribe("__global_sse__", scope="all")

        # Simulate agent emitting ToolCallStartEvent
        tool_start = ToolCallStartEvent(
            tool_call_id="call-e2e-001",
            tool_name="bash",
            raw_input={"command": "ls"},
            title="Running ls",
        )
        await session_pool.event_bus.publish(session_id, tool_start)
        await _async_wait(0.15)

        # Simulate agent emitting ToolCallCompleteEvent
        tool_complete = ToolCallCompleteEvent(
            tool_call_id="call-e2e-001",
            tool_name="bash",
            tool_input={"command": "ls"},
            tool_result="file1.txt\nfile2.txt",
            agent_name="test-agent",
            message_id="msg-e2e-tool",
        )
        await session_pool.event_bus.publish(session_id, tool_complete)
        await _async_wait(0.15)

        # Collect SSE events with ToolPart
        oc_events = _extract_opencode_events(sse_queue)

        tool_part_events = [
            e
            for e in oc_events
            if isinstance(e, PartUpdatedEvent) and isinstance(e.properties.part, ToolPart)
        ]

        assert len(tool_part_events) >= 2, (
            f"Expected at least 2 ToolPart events (start + complete), got {len(tool_part_events)}"
        )

        # First should be running state
        running_states = [
            t
            for t in tool_part_events
            if isinstance(t.properties.part.state, ToolStateRunning)  # type: ignore[union-attr]
        ]
        assert len(running_states) >= 1, (
            "ToolCallStart should produce ToolPart with ToolStateRunning"
        )

        # Last should be completed state
        completed_states = [
            t
            for t in tool_part_events
            if isinstance(t.properties.part.state, ToolStateCompleted)  # type: ignore[union-attr]
        ]
        assert len(completed_states) >= 1, (
            "ToolCallComplete should produce ToolPart with ToolStateCompleted"
        )

        await integration._stop_event_consumer(session_id)

    @pytest.mark.asyncio
    async def test_run_started_produces_session_status(
        self,
        session_pool: SessionPool,
        server_state: ServerState,
    ) -> None:
        """RunStartedEvent → SessionStatusEvent (busy).

        Verifies lifecycle events pass through event_bridge correctly.
        """
        integration = OpenCodeSessionPoolIntegration(
            session_pool=session_pool,
            server_state=server_state,
        )
        session_id = "e2e-run-started"

        await integration.create_session(session_id=session_id, agent_name="test-agent")
        await _async_wait(0.1)

        sse_queue = await session_pool.event_bus.subscribe("__global_sse__", scope="all")

        run_started = RunStartedEvent(session_id=session_id, run_id="run-e2e-001")
        await session_pool.event_bus.publish(session_id, run_started)
        await _async_wait(0.15)

        oc_events = _extract_opencode_events(sse_queue)

        status_events = [e for e in oc_events if isinstance(e, SessionStatusEvent)]

        assert len(status_events) >= 1, "RunStartedEvent should produce SessionStatusEvent"
        assert status_events[0].properties.status.type == "busy"

        await integration._stop_event_consumer(session_id)

    @pytest.mark.asyncio
    async def test_run_error_produces_session_error(
        self,
        session_pool: SessionPool,
        server_state: ServerState,
    ) -> None:
        """RunErrorEvent → SessionErrorEvent.

        Verifies error events pass through event_bridge correctly.
        """
        from agentpool.agents.events import RunErrorEvent

        integration = OpenCodeSessionPoolIntegration(
            session_pool=session_pool,
            server_state=server_state,
        )
        session_id = "e2e-run-error"

        await integration.create_session(session_id=session_id, agent_name="test-agent")
        await _async_wait(0.1)

        sse_queue = await session_pool.event_bus.subscribe("__global_sse__", scope="all")

        run_error = RunErrorEvent(
            code="TestError",
            message="Something went wrong",
            run_id="run-e2e-err",
        )
        await session_pool.event_bus.publish(session_id, run_error)
        await _async_wait(0.15)

        oc_events = _extract_opencode_events(sse_queue)

        error_events = [e for e in oc_events if isinstance(e, SessionErrorEvent)]

        assert len(error_events) >= 1, "RunErrorEvent should produce SessionErrorEvent"

        await integration._stop_event_consumer(session_id)

    @pytest.mark.asyncio
    async def test_event_bridge_extracts_nested_session_id(
        self,
        session_pool: SessionPool,
        server_state: ServerState,
    ) -> None:
        """Regression test: _extract_session_id handles PartUpdatedEvent's nested session_id.

        PartUpdatedEventProperties has session_id at properties.part.session_id,
        not at properties.session_id. The extractor must traverse this nested path.
        """
        # Verify event_bridge is set up
        assert server_state.event_bridge is not None

        # Create a PartUpdatedEvent with a TextPart (nested session_id)
        text_part = TextPart(
            id="part-e2e-nested",
            message_id="msg-e2e-nested",
            session_id="sess-e2e-nested",
            text="hello",
        )
        part_updated = PartUpdatedEvent.create(text_part)

        # Extract session_id — must succeed with the fix
        session_id = server_state.event_bridge._extract_session_id(part_updated)
        assert session_id == "sess-e2e-nested", (
            f"_extract_session_id should return 'sess-e2e-nested' for PartUpdatedEvent "
            f"with TextPart, got {session_id!r}"
        )

    @pytest.mark.asyncio
    async def test_session_scoped_consumer_does_not_loopback(
        self,
        session_pool: SessionPool,
        server_state: ServerState,
    ) -> None:
        """Verify the consumer doesn't process the OpenCode events it publishes back.

        When _handle_event converts an agent event to an OpenCode event and
        broadcasts it via event_bridge, the event_bridge publishes back to the
        SAME EventBus. The consumer should NOT try to convert these OpenCode
        events again (they're not RichAgentStreamEvent instances).
        """
        integration = OpenCodeSessionPoolIntegration(
            session_pool=session_pool,
            server_state=server_state,
        )
        session_id = "e2e-no-loopback"

        await integration.create_session(session_id=session_id, agent_name="test-agent")
        await _async_wait(0.1)

        sse_queue = await session_pool.event_bus.subscribe("__global_sse__", scope="all")

        # Publish a sequence of agent events
        text_start = PartStartEvent(index=0, part=pydantic_text_part("Hello"))
        await session_pool.event_bus.publish(session_id, text_start)
        await _async_wait(0.15)

        # Collect events and count PartUpdatedEvents with TextPart
        oc_events = _extract_opencode_events(sse_queue)
        part_updated_count = sum(
            1
            for e in oc_events
            if isinstance(e, PartUpdatedEvent) and isinstance(e.properties.part, TextPart)
        )

        # Should be exactly 1 (the converted PartStartEvent), not 2+ (loopback)
        assert part_updated_count == 1, (
            f"Expected exactly 1 PartUpdatedEvent with TextPart, got {part_updated_count}. "
            "Loopback conversion of OpenCode events should not produce duplicates."
        )

        await integration._stop_event_consumer(session_id)


# =============================================================================
# Test helpers
# =============================================================================


def pydantic_text_part(content: str) -> Any:
    """Create a pydantic TextPart for use in PartStartEvent."""
    from pydantic_ai.messages import TextPart as PydanticTextPart

    return PydanticTextPart(content=content)
