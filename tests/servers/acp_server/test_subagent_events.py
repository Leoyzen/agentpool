"""Integration tests for ACP subagent event handling.

Tests that ACPProtocolHandler correctly converts agent stream events to ACP
session update notifications via ProtocolEventConsumerMixin and ACPEventConverter.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

from pydantic_ai import RequestUsage, TextPartDelta
import pytest

from acp.schema import ClientCapabilities
from acp.schema.notifications import SessionNotification
from agentpool.agents.events.events import (
    PartDeltaEvent,
    RunErrorEvent,
    SpawnSessionStart,
    StreamCompleteEvent,
    ToolCallStartEvent,
)
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import EventBus
from agentpool_server.acp_server.event_converter import ACPEventConverter
from agentpool_server.acp_server.handler import ACPProtocolHandler


pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture
def mock_event_bus() -> AsyncMock:
    """Return a mock EventBus with async subscribe/unsubscribe."""
    bus = AsyncMock(spec=EventBus)
    bus.subscribe = AsyncMock(return_value=asyncio.Queue())
    bus.unsubscribe = AsyncMock(return_value=None)
    return bus


@pytest.fixture
def mock_agent_pool(mock_event_bus: AsyncMock) -> Mock:
    """Return a mock AgentPool with session_pool and main_agent."""
    pool = Mock()
    pool.session_pool = Mock()
    pool.session_pool.event_bus = mock_event_bus
    pool.main_agent = Mock()
    pool.main_agent.metadata = {"use_session_pool": True}
    return pool


@pytest.fixture
def mock_client() -> AsyncMock:
    """Return a mock ACP client."""
    client = AsyncMock()
    client.session_update = AsyncMock(return_value=None)
    return client


@pytest.fixture
def acp_handler(
    mock_agent_pool: Mock,
    mock_client: AsyncMock,
) -> ACPProtocolHandler:
    """Return an ACPProtocolHandler with mocked dependencies."""
    session_manager = AsyncMock()
    event_converter = ACPEventConverter()
    handler = ACPProtocolHandler(
        agent_pool=mock_agent_pool,
        session_manager=session_manager,
        event_converter=event_converter,
        client=mock_client,
        client_capabilities=None,
    )
    # Prevent child consumer creation from SpawnSessionStart events
    # (existing tests were written before child consumer support was added)
    handler._on_spawn_session_start = AsyncMock()  # type: ignore[method-assign]
    return handler


async def test_acp_handler_converts_spawn_session_start(
    acp_handler: ACPProtocolHandler,
    mock_event_bus: AsyncMock,
    mock_client: AsyncMock,
) -> None:
    """SpawnSessionStart produces session/update with AgentMessageChunk containing subagent name."""
    queue = asyncio.Queue()
    mock_event_bus.subscribe = AsyncMock(return_value=queue)

    event = SpawnSessionStart(
        child_session_id="child-1",
        parent_session_id="sess-1",
        source_name="subagent-agent",
        source_type="agent",
        depth=1,
        description="test spawn",
        spawn_mechanism="spawn",
    )
    await queue.put(event)
    await queue.put(None)

    await acp_handler.start_event_consumer("sess-1")
    task = acp_handler._consumer_tasks["sess-1"]
    await asyncio.wait_for(task, timeout=0.5)

    mock_client.session_update.assert_awaited()
    notification: SessionNotification[Any] = mock_client.session_update.await_args.args[0]
    assert isinstance(notification, SessionNotification)
    assert notification.session_id == "sess-1"
    assert notification.update.session_update == "agent_message_chunk"
    assert "subagent-agent" in notification.update.content.text


async def test_acp_handler_converts_part_delta(
    acp_handler: ACPProtocolHandler,
    mock_event_bus: AsyncMock,
    mock_client: AsyncMock,
) -> None:
    """PartDeltaEvent from subagent is converted to AgentMessageChunk."""
    queue = asyncio.Queue()
    mock_event_bus.subscribe = AsyncMock(return_value=queue)

    event = PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="hello"))
    await queue.put(event)
    await queue.put(None)

    await acp_handler.start_event_consumer("sess-1")
    task = acp_handler._consumer_tasks["sess-1"]
    await asyncio.wait_for(task, timeout=0.5)

    mock_client.session_update.assert_awaited()
    notification: SessionNotification[Any] = mock_client.session_update.await_args.args[0]
    assert isinstance(notification, SessionNotification)
    assert notification.update.session_update == "agent_message_chunk"
    assert notification.update.content.text == "hello"


async def test_acp_handler_converts_tool_call(
    acp_handler: ACPProtocolHandler,
    mock_event_bus: AsyncMock,
    mock_client: AsyncMock,
) -> None:
    """ToolCallStartEvent from subagent produces ToolCallStart notification."""
    queue = asyncio.Queue()
    mock_event_bus.subscribe = AsyncMock(return_value=queue)

    event = ToolCallStartEvent(
        tool_call_id="tc-1",
        tool_name="bash",
        title="Run bash command",
        kind="execute",
    )
    await queue.put(event)
    await queue.put(None)

    await acp_handler.start_event_consumer("sess-1")
    task = acp_handler._consumer_tasks["sess-1"]
    await asyncio.wait_for(task, timeout=0.5)

    mock_client.session_update.assert_awaited()
    notification: SessionNotification[Any] = mock_client.session_update.await_args.args[0]
    assert isinstance(notification, SessionNotification)
    assert notification.update.session_update == "tool_call"
    assert notification.update.tool_call_id == "tc-1"
    assert notification.update.title == "Run bash command"
    assert notification.update.kind == "execute"


async def test_acp_handler_converts_stream_complete(
    mock_agent_pool: Mock,
    mock_event_bus: AsyncMock,
    mock_client: AsyncMock,
) -> None:
    """StreamCompleteEvent produces UsageUpdate (+ TurnCompleteUpdate if client supports it)."""
    queue = asyncio.Queue()
    mock_event_bus.subscribe = AsyncMock(return_value=queue)

    handler = ACPProtocolHandler(
        agent_pool=mock_agent_pool,
        session_manager=AsyncMock(),
        event_converter=ACPEventConverter(),
        client=mock_client,
        client_capabilities=ClientCapabilities(turn_complete=True),
    )

    message = ChatMessage(
        content="done",
        role="assistant",
        usage=RequestUsage(input_tokens=5, output_tokens=5),
    )
    event = StreamCompleteEvent(message=message, session_id="sess-1")
    await queue.put(event)
    await queue.put(None)

    await handler.start_event_consumer("sess-1")
    task = handler._consumer_tasks["sess-1"]
    await asyncio.wait_for(task, timeout=0.5)

    assert mock_client.session_update.await_count == 2
    calls = mock_client.session_update.await_args_list

    assert calls[0].args[0].update.session_update == "usage_update"
    assert calls[0].args[0].update.used == 10

    assert calls[1].args[0].update.session_update == "turn_complete"
    assert calls[1].args[0].update.stop_reason == "end_turn"


async def test_acp_handler_converts_run_error(
    acp_handler: ACPProtocolHandler,
    mock_event_bus: AsyncMock,
    mock_client: AsyncMock,
) -> None:
    """RunErrorEvent produces error-formatted AgentMessageChunk."""
    queue = asyncio.Queue()
    mock_event_bus.subscribe = AsyncMock(return_value=queue)

    event = RunErrorEvent(message="something broke", agent_name="test-agent")
    await queue.put(event)
    await queue.put(None)

    await acp_handler.start_event_consumer("sess-1")
    task = acp_handler._consumer_tasks["sess-1"]
    await asyncio.wait_for(task, timeout=0.5)

    mock_client.session_update.assert_awaited()
    notification: SessionNotification[Any] = mock_client.session_update.await_args.args[0]
    assert isinstance(notification, SessionNotification)
    assert notification.update.session_update == "agent_message_chunk"
    assert "something broke" in notification.update.content.text
    assert "test-agent" in notification.update.content.text


async def test_acp_handler_connection_error_stops_consumer(
    acp_handler: ACPProtocolHandler,
    mock_event_bus: AsyncMock,
    mock_client: AsyncMock,
) -> None:
    """ConnectionResetError during session_update triggers ConsumerShutdown."""
    queue = asyncio.Queue()
    mock_event_bus.subscribe = AsyncMock(return_value=queue)

    mock_client.session_update = AsyncMock(
        side_effect=ConnectionResetError("connection lost")
    )

    event = PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="hello"))
    await queue.put(event)

    await acp_handler.start_event_consumer("sess-1")
    task = acp_handler._consumer_tasks["sess-1"]
    await asyncio.wait_for(task, timeout=0.5)

    assert task.done()
    assert task.exception() is None
    assert "sess-1" not in acp_handler._consumer_tasks
    mock_event_bus.unsubscribe.assert_awaited()


async def test_acp_handler_converter_isolated_per_session(
    acp_handler: ACPProtocolHandler,
    mock_event_bus: AsyncMock,
    mock_client: AsyncMock,
) -> None:
    """Two sessions have separate converters, events don't cross."""
    queue1 = asyncio.Queue()
    queue2 = asyncio.Queue()
    mock_event_bus.subscribe = AsyncMock(side_effect=[queue1, queue2])

    # Capture converter instances before the loop cleans them up
    captured_converters: dict[str, ACPEventConverter] = {}
    original_before = acp_handler._before_consumer_loop

    async def _patched_before(session_id: str) -> None:
        await original_before(session_id)
        captured_converters[session_id] = acp_handler._converters[session_id]

    acp_handler._before_consumer_loop = _patched_before  # type: ignore[method-assign]

    event1 = SpawnSessionStart(
        child_session_id="child-1",
        parent_session_id="sess-1",
        source_name="agent-a",
        source_type="agent",
        depth=1,
        description="spawn a",
        spawn_mechanism="spawn",
    )
    event2 = SpawnSessionStart(
        child_session_id="child-2",
        parent_session_id="sess-2",
        source_name="agent-b",
        source_type="agent",
        depth=1,
        description="spawn b",
        spawn_mechanism="spawn",
    )
    await queue1.put(event1)
    await queue2.put(event2)
    await queue1.put(None)
    await queue2.put(None)

    await acp_handler.start_event_consumer("sess-1")
    await acp_handler.start_event_consumer("sess-2")

    task1 = acp_handler._consumer_tasks["sess-1"]
    task2 = acp_handler._consumer_tasks["sess-2"]
    await asyncio.wait_for(asyncio.gather(task1, task2), timeout=0.5)

    # Verify separate converter instances were created
    assert "sess-1" in captured_converters
    assert "sess-2" in captured_converters
    assert captured_converters["sess-1"] is not captured_converters["sess-2"]

    assert mock_client.session_update.await_count == 2
    calls = mock_client.session_update.await_args_list

    sess1_notifications = [c.args[0] for c in calls if c.args[0].session_id == "sess-1"]
    sess2_notifications = [c.args[0] for c in calls if c.args[0].session_id == "sess-2"]

    assert len(sess1_notifications) == 1
    assert len(sess2_notifications) == 1
    assert "agent-a" in sess1_notifications[0].update.content.text
    assert "agent-b" in sess2_notifications[0].update.content.text


async def test_acp_handler_no_child_consumers_created(
    acp_handler: ACPProtocolHandler,
    mock_event_bus: AsyncMock,
) -> None:
    """Verify _consumer_tasks only has parent session, no child consumers."""
    queue = asyncio.Queue()
    mock_event_bus.subscribe = AsyncMock(return_value=queue)

    event = SpawnSessionStart(
        child_session_id="child-1",
        parent_session_id="sess-1",
        source_name="subagent-agent",
        source_type="agent",
        depth=1,
        description="test spawn",
        spawn_mechanism="spawn",
    )
    await queue.put(event)

    await acp_handler.start_event_consumer("sess-1")

    # Allow consumer to process SpawnSessionStart
    await asyncio.sleep(0.05)

    assert len(acp_handler._consumer_tasks) == 1
    assert "sess-1" in acp_handler._consumer_tasks
    assert "child-1" not in acp_handler._consumer_tasks

    # Gracefully stop the consumer
    await queue.put(None)
    task = acp_handler._consumer_tasks["sess-1"]
    await asyncio.wait_for(task, timeout=0.5)
