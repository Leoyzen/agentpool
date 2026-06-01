"""Integration tests for MCP-over-ACP at the AgentPoolACPAgent layer.

Tests the high-level agent methods: connect_acp_mcp_server,
disconnect_acp_mcp_server, ext_method("mcp/message", ...), and close().
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

from typing import Any

import anyio
import pytest

from acp.schema.mcp import AcpMcpServer
from agentpool import Agent
from agentpool.delegation import AgentPool
from agentpool.resource_providers.mcp_provider import MCPResourceProvider
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
from agentpool_server.acp_server.acp_mcp_transport import AcpMcpTransport
from agentpool_config.mcp_server import AcpMCPServerConfig
from mcp.shared.message import SessionMessage
from mcp.types import (
    JSONRPCMessage,
    JSONRPCResponse,
    Implementation,
    InitializeResult,
    ListToolsResult,
    ServerCapabilities,
    Tool,
)


pytestmark = [pytest.mark.unit, pytest.mark.anyio]


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
    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=pool)
    pool.register("test_agent", agent)
    return agent


@pytest.fixture
def acp_agent(mock_connection, default_test_agent: Agent) -> AgentPoolACPAgent:
    """Create a mock ACP agent for testing."""
    return AgentPoolACPAgent(client=mock_connection, default_agent=default_test_agent)


@pytest.fixture
def server_config() -> AcpMcpServer:
    """Create a test ACP MCP server configuration."""
    return AcpMcpServer(name="test-server", id="test-id")


# Test 1: connect_acp_mcp_server returns connectionId and registers connection


async def test_connect_acp_mcp_server_success(
    acp_agent: AgentPoolACPAgent,
    server_config: AcpMcpServer,
) -> None:
    """Verify connect_acp_mcp_server returns connectionId and registers in manager."""
    send_request_mock = AsyncMock(return_value={"connectionId": "conn-123"})
    acp_agent.client.send_request = send_request_mock  # type: ignore[method-assign]

    result = await acp_agent.connect_acp_mcp_server(server_config)

    assert result == "conn-123"
    assert acp_agent._mcp_manager.get_connection("conn-123") is not None
    assert "conn-123" in acp_agent._mcp_manager
    send_request_mock.assert_awaited_once_with(
        "mcp/connect",
        {
            "server": server_config.model_dump(by_alias=True, exclude_none=True),
            "acpId": server_config.id,
        },
    )


# Test 2: connect_acp_mcp_server without connectionId raises ValueError


async def test_connect_acp_mcp_server_missing_connection_id(
    acp_agent: AgentPoolACPAgent,
    server_config: AcpMcpServer,
) -> None:
    """Verify connect_acp_mcp_server raises ValueError when client omits connectionId."""
    send_request_mock = AsyncMock(return_value={})
    acp_agent.client.send_request = send_request_mock  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="connectionId"):
        await acp_agent.connect_acp_mcp_server(server_config)


# Test 3: connect_acp_mcp_server raises TimeoutError when client hangs


async def test_connect_acp_mcp_server_timeout(
    acp_agent: AgentPoolACPAgent,
    server_config: AcpMcpServer,
) -> None:
    """Verify connect_acp_mcp_server raises TimeoutError when send_request hangs."""

    async def hang_forever(*args, **kwargs):
        await asyncio.Event().wait()

    acp_agent.client.send_request = hang_forever  # type: ignore[method-assign]

    with pytest.raises(TimeoutError):
        await acp_agent.connect_acp_mcp_server(server_config)


# Test 4: disconnect_acp_mcp_server sends mcp/disconnect and removes connection


async def test_disconnect_acp_mcp_server(
    acp_agent: AgentPoolACPAgent,
    server_config: AcpMcpServer,
) -> None:
    """Verify disconnect_acp_mcp_server notifies client and removes from manager."""
    # Setup: connect first
    connect_mock = AsyncMock(return_value={"connectionId": "conn-456"})
    acp_agent.client.send_request = connect_mock  # type: ignore[method-assign]
    await acp_agent.connect_acp_mcp_server(server_config)
    assert acp_agent._mcp_manager.get_connection("conn-456") is not None

    # Reset mock to track disconnect call
    disconnect_mock = AsyncMock(return_value=None)
    acp_agent.client.send_request = disconnect_mock  # type: ignore[method-assign]

    await acp_agent.disconnect_acp_mcp_server("conn-456")

    disconnect_mock.assert_awaited_once_with(
        "mcp/disconnect", {"connectionId": "conn-456"}
    )
    assert acp_agent._mcp_manager.get_connection("conn-456") is None
    assert "conn-456" not in acp_agent._mcp_manager


# Test 4: ext_method routes mcp/message to correct connection


async def test_ext_method_routes_message(
    acp_agent: AgentPoolACPAgent,
    server_config: AcpMcpServer,
) -> None:
    """Verify ext_method("mcp/message", ...) routes to the correct connection."""
    send_request_mock = AsyncMock(return_value={"connectionId": "conn-789"})
    acp_agent.client.send_request = send_request_mock  # type: ignore[method-assign]
    await acp_agent.connect_acp_mcp_server(server_config)

    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    await acp_agent.ext_method("mcp/message", {"connectionId": "conn-789", "message": msg})

    # Give the async task a chance to run, then receive with timeout
    conn = acp_agent._mcp_manager.get_connection("conn-789")
    assert conn is not None
    with anyio.fail_after(1):
        received = await conn.to_session.receive()

    from mcp.shared.message import SessionMessage

    assert isinstance(received, SessionMessage)
    assert received.message.root.method == "tools/list"


# Test 5: ext_method with unknown connectionId logs warning and does not crash


async def test_ext_method_unknown_connection_id(
    acp_agent: AgentPoolACPAgent,
) -> None:
    """Verify ext_method handles unknown connectionId gracefully without crashing."""
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}

    result = await acp_agent.ext_method(
        "mcp/message", {"connectionId": "unknown-conn", "message": msg}
    )

    assert result == {}


# Test 6: Concurrent messages on different connectionIds


async def test_ext_method_concurrent_messages(
    acp_agent: AgentPoolACPAgent,
    server_config: AcpMcpServer,
) -> None:
    """Verify concurrent messages are routed to correct connections."""
    # Setup two connections
    side_effect = [
        {"connectionId": "conn-a"},
        {"connectionId": "conn-b"},
    ]
    send_request_mock = AsyncMock(side_effect=side_effect)
    acp_agent.client.send_request = send_request_mock  # type: ignore[method-assign]

    await acp_agent.connect_acp_mcp_server(server_config)
    await acp_agent.connect_acp_mcp_server(
        AcpMcpServer(name="test-server-2", id="test-id-2")
    )

    msg_a = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    msg_b = {"jsonrpc": "2.0", "id": 2, "method": "tools/call"}

    await asyncio.gather(
        acp_agent.ext_method("mcp/message", {"connectionId": "conn-a", "message": msg_a}),
        acp_agent.ext_method("mcp/message", {"connectionId": "conn-b", "message": msg_b}),
    )

    conn_a = acp_agent._mcp_manager.get_connection("conn-a")
    conn_b = acp_agent._mcp_manager.get_connection("conn-b")
    assert conn_a is not None
    assert conn_b is not None

    with anyio.fail_after(1):
        received_a = await conn_a.to_session.receive()
        received_b = await conn_b.to_session.receive()

    from mcp.shared.message import SessionMessage

    assert isinstance(received_a, SessionMessage)
    assert isinstance(received_b, SessionMessage)
    assert received_a.message.root.method == "tools/list"
    assert received_b.message.root.method == "tools/call"


# Test 7: close disconnects all ACP MCP servers and cleans up


async def test_close_disconnects_all_servers(
    acp_agent: AgentPoolACPAgent,
    server_config: AcpMcpServer,
) -> None:
    """Verify close disconnects all connections and cleans up the manager."""
    # Setup: connect three servers
    side_effect = [
        {"connectionId": "conn-1"},
        {"connectionId": "conn-2"},
        {"connectionId": "conn-3"},
    ]
    connect_mock = AsyncMock(side_effect=side_effect)
    acp_agent.client.send_request = connect_mock  # type: ignore[method-assign]

    await acp_agent.connect_acp_mcp_server(server_config)
    await acp_agent.connect_acp_mcp_server(AcpMcpServer(name="srv-2", id="id-2"))
    await acp_agent.connect_acp_mcp_server(AcpMcpServer(name="srv-3", id="id-3"))

    assert len(acp_agent._mcp_manager) == 3

    # Track disconnect calls
    disconnect_mock = AsyncMock(return_value=None)
    acp_agent.client.send_request = disconnect_mock  # type: ignore[method-assign]

    await acp_agent.close()

    # Verify all disconnect calls were made
    call_methods = [call.args[0] for call in disconnect_mock.call_args_list]
    assert call_methods.count("mcp/disconnect") == 3

    # Verify manager is empty
    assert len(acp_agent._mcp_manager) == 0


# Test 8: session.initialize() triggers the ACP mcp/message send_to_client callback


async def test_session_initialize_triggers_mcp_message(
    acp_agent: AgentPoolACPAgent,
    server_config: AcpMcpServer,
) -> None:
    """ClientSession.initialize() must trigger send_to_client via _forward_to_client.

    This verifies the bidirectional flow that existing tests miss because
    they patch ClientSession.initialize():

    1. connect_acp_mcp_server() creates AcpMcpConnection with send_to_client
    2. AcpMcpTransport.connect_session() creates ClientSession + _forward_to_client
    3. Real session.initialize() sends MCP initialize request
    4. _forward_to_client() reads it and calls connection.send_to_client()
    5. Mock client captures it and sends back response
    6. ClientSession receives response, initialize() completes

    If _forward_to_client() is broken (e.g. doesn't read from
    from_session_receive), initialize() hangs forever.
    """
    received_mcp_messages: list[dict] = []

    async def mock_send_request(method: str, params: dict) -> dict:
        if method == "mcp/connect":
            return {"connectionId": "test-conn-init"}

        if method == "mcp/message":
            received_mcp_messages.append(params)
            session_msg = params.get("message")
            if isinstance(session_msg, dict) and session_msg.get("method") == "initialize":
                conn = acp_agent._mcp_manager.get_connection("test-conn-init")
                if conn is not None:
                    req_id = session_msg.get("id")
                    result = InitializeResult(
                        protocolVersion="2024-11-05",
                        capabilities=ServerCapabilities(),
                        serverInfo=Implementation(name="test", version="1.0"),
                    )
                    response = JSONRPCResponse(
                        jsonrpc="2.0",
                        id=req_id,
                        result=result.model_dump(
                            by_alias=True, mode="json", exclude_none=True
                        ),
                    )
                    response_msg = SessionMessage(message=JSONRPCMessage(response))
                    assert conn._to_session_send is not None
                    await conn._to_session_send.send(response_msg)
            return {}

        return {}

    acp_agent.client.send_request = mock_send_request  # type: ignore[method-assign]

    connection_id = await acp_agent.connect_acp_mcp_server(server_config)
    assert connection_id == "test-conn-init"

    conn = acp_agent._mcp_manager.get_connection(connection_id)
    assert conn is not None

    transport = AcpMcpTransport(conn)
    async with transport.connect_session() as session:
        with anyio.fail_after(5):
            await session.initialize()

    assert len(received_mcp_messages) >= 1, (
        "send_to_client was never called - _forward_to_client() may be broken"
    )

    initialize_found = False
    for msg in received_mcp_messages:
        inner = msg.get("message")
        if isinstance(inner, dict) and inner.get("method") == "initialize":
            initialize_found = True
            break

    assert initialize_found, (
        f"No initialize request found in {len(received_mcp_messages)} messages"
    )


# Test 9: get_tools() sends tools/list through the ACP channel


async def test_get_tools_sends_tools_list_via_acp(
    acp_agent: AgentPoolACPAgent,
    server_config: AcpMcpServer,
) -> None:
    """MCPResourceProvider.get_tools() must trigger tools/list via ACP mcp/message.

    This verifies the complete end-to-end flow:

    1. connect_acp_mcp_server() creates connection
    2. MCPResourceProvider with AcpMcpTransport enters context
    3. Real initialize() completes (via _forward_to_client + mock response)
    4. provider.get_tools() calls refresh_tools_cache() -> list_tools()
    5. session.list_tools() sends MCP tools/list request
    6. _forward_to_client() reads it and calls send_to_client()
    7. Mock client captures it and sends back response with tools
    8. get_tools() returns tools

    If step 6 is broken, list_tools() hangs and no tools are returned.
    """
    received_mcp_messages: list[dict] = []

    async def mock_send_request(method: str, params: dict) -> dict:
        if method == "mcp/connect":
            return {"connectionId": "test-conn-tools"}

        if method == "mcp/message":
            received_mcp_messages.append(params)
            session_msg = params.get("message")
            if isinstance(session_msg, dict):
                conn = acp_agent._mcp_manager.get_connection("test-conn-tools")
                if conn is not None:
                    req_method = session_msg.get("method")
                    req_id = session_msg.get("id")

                    if req_method == "initialize" and req_id is not None:
                        return {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "serverInfo": {"name": "test", "version": "1.0"},
                        }

                    elif req_method == "tools/list":
                        return {
                            "tools": [
                                {
                                    "name": "test_tool",
                                    "description": "A test tool",
                                    "inputSchema": {
                                        "type": "object",
                                        "properties": {},
                                    },
                                }
                            ]
                        }
            return {}

        return {}

    acp_agent.client.send_request = mock_send_request  # type: ignore[method-assign]

    connection_id = await acp_agent.connect_acp_mcp_server(server_config)
    assert connection_id == "test-conn-tools"

    conn = acp_agent._mcp_manager.get_connection(connection_id)
    assert conn is not None

    transport = AcpMcpTransport(conn)
    acp_server_config = AcpMCPServerConfig(
        acp_id=server_config.id,
        name=server_config.name,
        timeout=10.0,
    )
    provider = MCPResourceProvider(
        server=acp_server_config, transport=transport
    )

    with anyio.fail_after(5):
        async with provider:
            tools = await provider.get_tools()

    assert len(tools) >= 1, "Expected at least one tool from get_tools()"

    tools_list_found = False
    for msg in received_mcp_messages:
        inner = msg.get("message")
        if isinstance(inner, dict) and inner.get("method") == "tools/list":
            tools_list_found = True
            break

    assert tools_list_found, (
        f"No tools/list request found in {len(received_mcp_messages)} messages"
    )
