"""Integration tests for ACP subagent event handling.

Tests that ACPProtocolHandler correctly converts agent stream events to ACP
session update notifications via ProtocolEventConsumerMixin and ACPEventConverter.
"""

from __future__ import annotations

import asyncio

import anyio
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
    from tests._helpers.mock_stream import EmptyReceiveStream

    bus.subscribe = AsyncMock(return_value=EmptyReceiveStream())
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
    _send, _recv = anyio.create_memory_object_stream(max_buffer_size=100)
    mock_event_bus.subscribe = AsyncMock(return_value=_recv)

    event = SpawnSessionStart(
        child_session_id="child-1",
        parent_session_id="sess-1",
        source_name="subagent-agent",
        source_type="agent",
        depth=1,
        description="test spawn",
        spawn_mechanism="spawn",
    )
    await _send.send(event)

    await acp_handler.start_event_consumer("sess-1")
    await asyncio.sleep(0.1)
    await _send.aclose()
    for _ in range(100):
        if len(acp_handler._converters) == 0 or "sess-1" not in acp_handler._consumer_streams:
            break
        await asyncio.sleep(0.01)

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
    _send, _recv = anyio.create_memory_object_stream(max_buffer_size=100)
    mock_event_bus.subscribe = AsyncMock(return_value=_recv)

    event = PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="hello"))
    await _send.send(event)

    await acp_handler.start_event_consumer("sess-1")
    await asyncio.sleep(0.1)
    await _send.aclose()
    for _ in range(100):
        if len(acp_handler._converters) == 0 or "sess-1" not in acp_handler._consumer_streams:
            break
        await asyncio.sleep(0.01)

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
    _send, _recv = anyio.create_memory_object_stream(max_buffer_size=100)
    mock_event_bus.subscribe = AsyncMock(return_value=_recv)

    event = ToolCallStartEvent(
        tool_call_id="tc-1",
        tool_name="bash",
        title="Run bash command",
        kind="execute",
    )
    await _send.send(event)

    await acp_handler.start_event_consumer("sess-1")
    await asyncio.sleep(0.1)
    await _send.aclose()
    for _ in range(100):
        if len(acp_handler._converters) == 0 or "sess-1" not in acp_handler._consumer_streams:
            break
        await asyncio.sleep(0.01)

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
    _send, _recv = anyio.create_memory_object_stream(max_buffer_size=100)
    mock_event_bus.subscribe = AsyncMock(return_value=_recv)

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
    await _send.send(event)

    await handler.start_event_consumer("sess-1")
    await asyncio.sleep(0.1)
    await _send.aclose()
    for _ in range(100):
        if len(handler._converters) == 0 or "sess-1" not in handler._consumer_streams:
            break
        await asyncio.sleep(0.01)

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
    _send, _recv = anyio.create_memory_object_stream(max_buffer_size=100)
    mock_event_bus.subscribe = AsyncMock(return_value=_recv)

    event = RunErrorEvent(message="something broke", agent_name="test-agent")
    await _send.send(event)

    await acp_handler.start_event_consumer("sess-1")
    await asyncio.sleep(0.1)
    await _send.aclose()
    for _ in range(100):
        if len(acp_handler._converters) == 0 or "sess-1" not in acp_handler._consumer_streams:
            break
        await asyncio.sleep(0.01)

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
    _send, _recv = anyio.create_memory_object_stream(max_buffer_size=100)
    mock_event_bus.subscribe = AsyncMock(return_value=_recv)

    mock_client.session_update = AsyncMock(
        side_effect=ConnectionResetError("connection lost")
    )

    event = PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="hello"))
    await _send.send(event)

    await acp_handler.start_event_consumer("sess-1")
    await asyncio.sleep(0.1)
    await _send.aclose()
    for _ in range(100):
        if len(acp_handler._converters) == 0 or "sess-1" not in acp_handler._consumer_streams:
            break
        await asyncio.sleep(0.01)


    assert "sess-1" not in acp_handler._session_groups
    mock_event_bus.unsubscribe.assert_awaited()


async def test_acp_handler_converter_isolated_per_session(
    acp_handler: ACPProtocolHandler,
    mock_event_bus: AsyncMock,
    mock_client: AsyncMock,
) -> None:
    """Two sessions have separate converters, events don't cross."""
    _send1, _recv1 = anyio.create_memory_object_stream(max_buffer_size=100)
    _send2, _recv2 = anyio.create_memory_object_stream(max_buffer_size=100)
    mock_event_bus.subscribe = AsyncMock(side_effect=[_recv1, _recv2])

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
    await _send1.send(event1)
    await _send2.send(event2)

    await acp_handler.start_event_consumer("sess-1")
    await acp_handler.start_event_consumer("sess-2")
    await asyncio.sleep(0.2)
    await _send1.aclose()
    await _send2.aclose()

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
    """Verify _session_groups only has parent session, no child consumers."""
    _send, _recv = anyio.create_memory_object_stream(max_buffer_size=100)
    mock_event_bus.subscribe = AsyncMock(return_value=_recv)

    event = SpawnSessionStart(
        child_session_id="child-1",
        parent_session_id="sess-1",
        source_name="subagent-agent",
        source_type="agent",
        depth=1,
        description="test spawn",
        spawn_mechanism="spawn",
    )
    await _send.send(event)

    await acp_handler.start_event_consumer("sess-1")

    await asyncio.sleep(0.05)

    assert len(acp_handler._session_groups) == 1
    assert "sess-1" in acp_handler._session_groups
    assert "child-1" not in acp_handler._session_groups

    await _send.aclose()
    for _ in range(100):
        if len(acp_handler._converters) == 0 or "sess-1" not in acp_handler._consumer_streams:
            break
        await asyncio.sleep(0.01)
