"""End-to-end integration tests for MCP-over-ACP message lifecycle.

Verifies the complete flow: mcp/connect -> mcp/message -> mcp/disconnect
through all layers of the ACP MCP integration.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import anyio
import pytest

from acp.exceptions import RequestError
from acp.schema.mcp import AcpMcpServer
from agentpool import Agent
from agentpool.delegation import AgentPool
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
from agentpool_server.acp_server.acp_mcp_manager import (
    AcpMcpConnection,
    AcpMcpConnectionManager,
)
from agentpool_server.acp_server.acp_mcp_transport import AcpMcpTransport


pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture
def server_config() -> AcpMcpServer:
    """Create a test ACP MCP server configuration."""
    return AcpMcpServer(name="test-server", id="test-id")


@pytest.fixture
def send_to_client() -> AsyncMock:
    """Create an AsyncMock send_to_client callable."""
    return AsyncMock(return_value=None)


@pytest.fixture
def mock_connection():
    """Create a mock ACP connection."""
    return Mock()


@pytest.fixture
def default_test_agent() -> Agent:
    """Create a simple test agent with a pool."""

    def simple_callback(message: str) -> str:
        return f"Test response: {message}"

    pool = AgentPool()
    agent = Agent.from_callback(
        name="test_agent", callback=simple_callback, agent_pool=pool
    )
    pool.register("test_agent", agent)
    return agent


@pytest.fixture
def acp_agent(mock_connection, default_test_agent: Agent) -> AgentPoolACPAgent:
    """Create a mock ACP agent for testing."""
    return AgentPoolACPAgent(client=mock_connection, default_agent=default_test_agent)


# Test 1: Full Connection Lifecycle


async def test_full_connection_lifecycle(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """Verify complete connection lifecycle from creation to removal."""
    # 1. Create an AcpMcpConnectionManager
    manager = AcpMcpConnectionManager()

    # 2. Create a connection with create_connection()
    conn = await manager.create_connection(
        connection_id="conn-1",
        server_config=server_config,
        send_to_client=send_to_client,
    )

    # 3. Verify connection is active via get_connection()
    assert manager.get_connection("conn-1") is conn
    assert "conn-1" in manager
    assert len(manager) == 1

    # 4. Streams are already opened by create_connection(); verify they work
    assert conn._to_session_send is not None
    assert conn._to_session_receive is not None
    assert conn._from_session_send is not None
    assert conn._from_session_receive is not None

    # 5. Create an AcpMcpTransport with the connection
    transport = AcpMcpTransport(conn)

    # 6. Use connect_session() to establish a ClientSession
    with patch("mcp.client.session.ClientSession.initialize", new_callable=AsyncMock):
        async with transport.connect_session():
            # 7. Send a message via connection.from_session.send()
            msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
            await conn.from_session.send(msg)

            # 8. Verify _send_to_client was called with the wrapped message format
            conn._send_to_client.assert_awaited()
            call_args = conn._send_to_client.call_args[0][0]
            assert call_args["connectionId"] == "conn-1"
            assert "message" in call_args
            assert call_args["message"] == msg

        # 9. Close the session (context manager exit)
        # Session is closed when exiting the async with block

    # 10. Remove connection via manager.remove_connection()
    await manager.remove_connection("conn-1")

    # 11. Verify connection is gone
    assert manager.get_connection("conn-1") is None
    assert "conn-1" not in manager
    assert len(manager) == 0


# Test 2: Multiple Messages Over Same Connection


async def test_multiple_messages_over_same_connection(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """Verify multiple MCP JSON-RPC messages are forwarded in order."""
    # 1. Create connection and open streams
    manager = AcpMcpConnectionManager()
    conn = await manager.create_connection(
        connection_id="conn-multi",
        server_config=server_config,
        send_to_client=send_to_client,
    )

    # 2. Create transport and establish session
    transport = AcpMcpTransport(conn)

    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "read_file", "arguments": {"path": "/tmp/test"}},
        },
        {
            "jsonrpc": "2.0",
            "method": "notifications/progress",
            "params": {"token": "test", "progress": 100},
        },
    ]

    with patch("mcp.client.session.ClientSession.initialize", new_callable=AsyncMock):
        async with transport.connect_session():
            # 3. Send multiple MCP JSON-RPC messages
            for msg in messages:
                await conn.from_session.send(msg)

            # 4. Verify each is forwarded to client in order
            assert conn._send_to_client.await_count == len(messages)

            # 5. Verify message format: {"connectionId": "...", "message": {...}}
            for i, expected in enumerate(messages):
                call_args = conn._send_to_client.call_args_list[i][0][0]
                assert call_args["connectionId"] == "conn-multi"
                assert "message" in call_args
                assert call_args["message"] == expected

    # Cleanup
    await manager.remove_connection("conn-multi")


# Test 3: Connection Cleanup on Disconnect


async def test_connection_cleanup_on_disconnect(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """Verify close_all closes and removes all connections."""
    # 1. Create multiple connections in the manager
    manager = AcpMcpConnectionManager()
    conn1 = await manager.create_connection(
        connection_id="conn-1",
        server_config=server_config,
        send_to_client=send_to_client,
    )
    conn2 = await manager.create_connection(
        connection_id="conn-2",
        server_config=server_config,
        send_to_client=send_to_client,
    )
    conn3 = await manager.create_connection(
        connection_id="conn-3",
        server_config=server_config,
        send_to_client=send_to_client,
    )

    assert len(manager) == 3
    assert manager.get_connection("conn-1") is conn1
    assert manager.get_connection("conn-2") is conn2
    assert manager.get_connection("conn-3") is conn3

    # 2. Call close_all()
    await manager.close_all()

    # 3. Verify all connections are closed and removed
    assert len(manager) == 0
    assert manager.get_connection("conn-1") is None
    assert manager.get_connection("conn-2") is None
    assert manager.get_connection("conn-3") is None
    assert conn1._closed is True
    assert conn2._closed is True
    assert conn3._closed is True


# Test 4: Error Handling - Closed Connection


async def test_error_handling_closed_connection(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """Verify handle_client_message handles closed streams gracefully."""
    # 1. Create connection, open it, then close it
    conn = AcpMcpConnection(
        connection_id="conn-closed",
        server_config=server_config,
        send_to_client=send_to_client,
    )
    await conn.open()
    await conn.close()

    assert conn._closed is True

    # 2. Verify handle_client_message handles closed streams gracefully
    # (does not raise, logs debug message instead)
    await conn.handle_client_message({"jsonrpc": "2.0", "method": "test"})


# Test 5: Concurrent Pending Requests Are Isolated


async def test_concurrent_pending_requests_are_isolated(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """Verify multiple pending requests can be fulfilled independently and in any order."""
    # 1. Create connection and open streams
    conn = AcpMcpConnection("conn-concurrent", server_config, send_to_client)
    await conn.open()

    # 2. Register two pending requests with different IDs
    future1 = await conn.register_pending_request(1)
    future2 = await conn.register_pending_request(2)

    # 3. Fulfill them in reverse order
    conn.fulfill_pending_request(2, {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}})
    conn.fulfill_pending_request(1, {"jsonrpc": "2.0", "id": 1, "result": {"items": []}})

    # 4. Verify each Future gets its correct response
    result1 = await future1
    result2 = await future2
    assert result1 == {"jsonrpc": "2.0", "id": 1, "result": {"items": []}}
    assert result2 == {"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}

    # Cleanup
    await conn.close()


# Test 6: Pending Request Timeout


async def test_pending_request_timeout(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """Verify pending request Future raises TimeoutError when not fulfilled."""
    # 1. Create connection with mock _send_to_client that never responds
    conn = AcpMcpConnection("conn-timeout", server_config, send_to_client)
    await conn.open()

    # 2. Register pending request
    future = await conn.register_pending_request(1)

    # 3. Await Future with a short timeout
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(future, timeout=0.1)

    # Cleanup
    conn.unregister_pending_request(1)
    await conn.close()


# Test 7: Duplicate Request ID Rejected


async def test_duplicate_request_id_rejected(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """Verify registering duplicate request ID raises RuntimeError."""
    # 1. Create connection
    conn = AcpMcpConnection("conn-dup", server_config, send_to_client)
    await conn.open()

    # 2. Register pending request id=1
    await conn.register_pending_request(1)

    # 3. Try to register id=1 again
    with pytest.raises(RuntimeError, match="Duplicate pending request ID: 1"):
        await conn.register_pending_request(1)

    # Cleanup
    conn.unregister_pending_request(1)
    await conn.close()


# Test 8: Agent-Initiated Request Path Unchanged


async def test_agent_initiated_request_path_unchanged(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """Verify agent-initiated requests are forwarded to client and response written back."""
    # 1. Create connection with mock _send_to_client
    conn = AcpMcpConnection("conn-agent", server_config, send_to_client)
    await conn.open()

    # 2. Mock _send_to_client to return an ACP response
    acp_response = {"jsonrpc": "2.0", "id": 10, "result": {"tools": []}}
    send_to_client.return_value = acp_response

    # 3. Send an agent-initiated REQUEST (has method, not a response)
    msg = {"jsonrpc": "2.0", "id": 10, "method": "tools/list"}
    # Run in background because send_to_client writes back to _to_session_send
    # which blocks until someone reads from to_session (buffer size 0)
    send_task = asyncio.create_task(conn.send_to_client(msg))

    # 4. Read from to_session to unblock send_to_client, then let it finish
    received = await conn.to_session.receive()
    await send_task

    # 5. Verify _send_to_client IS called (because it's not a response)
    send_to_client.assert_awaited_once()
    call_args = send_to_client.call_args[0][0]
    assert call_args["connectionId"] == "conn-agent"
    assert call_args["message"] == msg

    # 6. Verify the ACP response is written back to _to_session_send
    # Stream is typed as dict but actually carries SessionMessage objects
    received_dict = received.message.model_dump(  # type: ignore[union-attr]
        by_alias=True, mode="json", exclude_none=True
    )
    assert received_dict == acp_response

    # Cleanup
    await conn.close()


# Test 9: Notification Fire and Forget


async def test_notification_fire_and_forget(
    acp_agent: AgentPoolACPAgent,
    server_config: AcpMcpServer,
) -> None:
    """Verify notifications return {} immediately without blocking."""

    async def mock_send(message: dict) -> None:
        return None

    # 1. Create a connection in the manager
    conn = await acp_agent._mcp_manager.create_connection(
        "conn-notif", server_config, mock_send
    )

    # 2. Call ext_method with a notification (no "id" field)
    params = {
        "connectionId": "conn-notif",
        "message": {"jsonrpc": "2.0", "method": "notifications/initialized"},
    }

    # 3. Verify it returns {} immediately without blocking
    result = await acp_agent.ext_method("mcp/message", params)
    assert result == {}

    # Cleanup
    await acp_agent._mcp_manager.remove_connection("conn-notif")


# Test 10: Pending Request MCP Error Mapped to RequestError


async def test_pending_request_mcp_error_mapped_to_request_error(
    acp_agent: AgentPoolACPAgent,
    server_config: AcpMcpServer,
) -> None:
    """Verify MCP error responses are mapped to RequestError."""

    async def mock_send(message: dict) -> None:
        return None

    # 1. Create a connection in the manager
    conn = await acp_agent._mcp_manager.create_connection(
        "conn-err", server_config, mock_send
    )

    # 2. Start a background task to drain the to_session stream
    async def drain() -> None:
        try:
            async for _ in conn.to_session:
                pass
        except anyio.ClosedResourceError:
            pass

    drain_task = asyncio.create_task(drain())

    # 3. Call ext_method with a client-initiated request
    params = {
        "connectionId": "conn-err",
        "message": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    }
    ext_task = asyncio.create_task(acp_agent.ext_method("mcp/message", params))

    # 4. Give ext_method time to register the pending request
    await asyncio.sleep(0.05)

    # 5. Fulfill with an error response
    conn.fulfill_pending_request(
        1,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32601, "message": "Method not found"},
        },
    )

    # 6. Verify ext_method raises RequestError with correct code/message
    with pytest.raises(RequestError) as exc_info:
        await ext_task

    assert exc_info.value.code == -32601
    assert "Method not found" in str(exc_info.value)

    # Cleanup
    drain_task.cancel()
    try:
        await drain_task
    except asyncio.CancelledError:
        pass
    await acp_agent._mcp_manager.remove_connection("conn-err")


# Test 11: Notification with Null ID is Fire and Forget


async def test_notification_with_null_id_is_fire_and_forget(
    acp_agent: AgentPoolACPAgent,
    server_config: AcpMcpServer,
) -> None:
    """Verify message with id=null is treated as notification and returns {}."""

    async def mock_send(message: dict) -> None:
        return None

    # 1. Create a connection in the manager
    conn = await acp_agent._mcp_manager.create_connection(
        "conn-null", server_config, mock_send
    )

    # 2. Call ext_method with a message that has id=null
    params = {
        "connectionId": "conn-null",
        "message": {
            "jsonrpc": "2.0",
            "id": None,
            "method": "notifications/initialized",
        },
    }

    # 3. Verify it returns {} (fire-and-forget)
    result = await acp_agent.ext_method("mcp/message", params)
    assert result == {}

    # Cleanup
    await acp_agent._mcp_manager.remove_connection("conn-null")


# Test 12: Non-Dict Message Handled Gracefully


async def test_non_dict_message_handled_gracefully(
    acp_agent: AgentPoolACPAgent,
    server_config: AcpMcpServer,
) -> None:
    """Verify non-dict message doesn't crash ext_method and returns {}."""

    async def mock_send(message: dict) -> None:
        return None

    # 1. Create a connection in the manager
    conn = await acp_agent._mcp_manager.create_connection(
        "conn-nondict", server_config, mock_send
    )

    # 2. Call ext_method with a non-dict message
    params = {
        "connectionId": "conn-nondict",
        "message": "not a dict",
    }

    # 3. Verify it doesn't crash and returns {}
    result = await acp_agent.ext_method("mcp/message", params)
    assert result == {}

    # Cleanup
    await acp_agent._mcp_manager.remove_connection("conn-nondict")


# Test 13: Connection Closed While Pending


async def test_connection_closed_while_pending(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """Verify pending requests are cancelled when connection closes."""
    # 1. Create connection and open streams
    conn = AcpMcpConnection("conn-close", server_config, send_to_client)
    await conn.open()

    # 2. Register pending request
    future = await conn.register_pending_request(1)
    assert not future.done()

    # 3. Close connection
    await conn.close()

    # 4. Verify Future is cancelled and registry is cleared
    assert future.cancelled()
    assert len(conn._pending_client_requests) == 0
