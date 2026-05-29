"""Red flag test: after mcp/connect, tools/list is not initiated.

This test verifies that ClientSession.initialize() triggers the send_to_client
callback (which sends ACP mcp/message), proving the bidirectional stream
forwarding works. Without this, fastmcp cannot complete initialization and
can never send tools/list.
"""

from __future__ import annotations

from unittest.mock import Mock

import asyncio

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
def server_config() -> AcpMcpServer:
    """Create a test ACP MCP server configuration."""
    return AcpMcpServer(name="test-server", id="test-id")


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
def acp_agent(default_test_agent: Agent) -> AgentPoolACPAgent:
    """Create a mock ACP agent for testing."""
    mock_client = Mock()
    return AgentPoolACPAgent(client=mock_client, default_agent=default_test_agent)


async def test_initialize_triggers_mcp_message(
    acp_agent: AgentPoolACPAgent,
    server_config: AcpMcpServer,
) -> None:
    """Red flag: ClientSession.initialize() must trigger send_to_client.

    This test verifies the complete bidirectional flow that existing tests
    miss because they patch ClientSession.initialize():

    1. connect_acp_mcp_server() creates AcpMcpConnection with send_to_client
       callback that wraps messages and calls client.send_request("mcp/message")
    2. AcpMcpTransport.connect_session() creates ClientSession and starts
       _forward_to_client() background task
    3. Real session.initialize() (NOT patched) sends MCP initialize request
       to from_session_send
    4. _forward_to_client() reads from from_session_receive and calls
       connection.send_to_client(), which calls the callback
    5. Mock client captures the ACP mcp/message and sends back an initialize
       response via the connection's to_session_send
    6. ClientSession receives the response, initialize() completes

    If step 4 is broken (e.g., _forward_to_client() doesn't read from
    from_session_receive), initialize() will hang forever and send_to_client
    is never called. This is the root cause of "tools/list is not initiated".
    """
    received_mcp_messages: list[dict] = []

    async def mock_send_request(method: str, params: dict) -> dict:
        if method == "mcp/connect":
            return {"connectionId": "test-conn-init"}

        if method == "mcp/message":
            received_mcp_messages.append(params)

            # Extract the SessionMessage from the wrapped params
            session_msg = params.get("message")
            if (
                session_msg is not None
                and hasattr(session_msg, "message")
                and hasattr(session_msg.message, "root")
                and hasattr(session_msg.message.root, "method")
                and session_msg.message.root.method == "initialize"
            ):
                # Send initialize response back through the connection
                conn = acp_agent._mcp_manager.get_connection("test-conn-init")
                if conn is not None:
                    result = InitializeResult(
                        protocolVersion="2024-11-05",
                        capabilities=ServerCapabilities(),
                        serverInfo=Implementation(name="test", version="1.0"),
                    )
                    response = JSONRPCResponse(
                        jsonrpc="2.0",
                        id=session_msg.message.root.id,
                        result=result.model_dump(
                            by_alias=True, mode="json", exclude_none=True
                        ),
                    )
                    response_msg = SessionMessage(message=JSONRPCMessage(response))
                    # Directly send to to_session_send to feed the response
                    # back to ClientSession._receive_loop()
                    assert conn._to_session_send is not None
                    await conn._to_session_send.send(response_msg)

            return {}

        return {}

    acp_agent.client.send_request = mock_send_request  # type: ignore[method-assign]

    # Step 1: Establish connection via connect_acp_mcp_server
    connection_id = await acp_agent.connect_acp_mcp_server(server_config)
    assert connection_id == "test-conn-init"

    conn = acp_agent._mcp_manager.get_connection(connection_id)
    assert conn is not None

    # Step 2: Create transport and connect session
    transport = AcpMcpTransport(conn)

    # Step 3: Enter session context (starts _receive_loop) and call REAL initialize()
    async with transport.connect_session() as session:
        with anyio.fail_after(5):
            await session.initialize()

    # Step 4: Verify send_to_client was called with the initialize request
    assert len(received_mcp_messages) >= 1, (
        "send_to_client was never called - _forward_to_client() may be broken"
    )

    # Find the initialize request among captured messages
    initialize_found = False
    for msg in received_mcp_messages:
        inner = msg.get("message")
        if (
            inner is not None
            and hasattr(inner, "message")
            and hasattr(inner.message, "root")
            and hasattr(inner.message.root, "method")
            and inner.message.root.method == "initialize"
        ):
            initialize_found = True
            break

    assert initialize_found, (
        f"No initialize request found in {len(received_mcp_messages)} "
        f"received messages: {received_mcp_messages}"
    )


async def test_get_tools_triggers_tools_list(
    acp_agent: AgentPoolACPAgent,
    server_config: AcpMcpServer,
) -> None:
    """Red flag: get_tools() must trigger send_to_client with tools/list.

    This test verifies the complete bidirectional flow from
    MCPResourceProvider.get_tools() through the ACP channel:

    1. connect_acp_mcp_server() creates AcpMcpConnection with send_to_client
       callback
    2. MCPResourceProvider with AcpMcpTransport enters context, triggering
       real initialize()
    3. Real session.initialize() sends MCP initialize request to
       from_session_send
    4. _forward_to_client() reads from from_session_receive and calls
       connection.send_to_client()
    5. Mock client captures initialize, sends back response
    6. initialize() completes
    7. provider.get_tools() calls refresh_tools_cache() -> client.list_tools()
       -> session.list_tools()
    8. session.list_tools() sends MCP tools/list request to from_session_send
    9. _forward_to_client() reads from from_session_receive and calls
       connection.send_to_client()
    10. Mock client captures tools/list, sends back response with tools
    11. session.list_tools() receives response, returns tools
    12. get_tools() returns tools

    If step 9 is broken (e.g., _forward_to_client() doesn't read from
    from_session_receive), list_tools() hangs and send_to_client is never
    called with tools/list.
    """
    received_mcp_messages: list[dict] = []

    async def mock_send_request(method: str, params: dict) -> dict:
        if method == "mcp/connect":
            return {"connectionId": "test-conn-tools"}

        if method == "mcp/message":
            received_mcp_messages.append(params)

            session_msg = params.get("message")
            if (
                session_msg is not None
                and hasattr(session_msg, "message")
                and hasattr(session_msg.message, "root")
                and hasattr(session_msg.message.root, "method")
            ):
                conn = acp_agent._mcp_manager.get_connection("test-conn-tools")
                if conn is not None:
                    req_method = session_msg.message.root.method
                    # Notifications (e.g., "initialized") don't have an id
                    req_id = getattr(session_msg.message.root, "id", None)

                    if req_method == "initialize" and req_id is not None:
                        result = InitializeResult(
                            protocolVersion="2024-11-05",
                            capabilities=ServerCapabilities(),
                            serverInfo=Implementation(
                                name="test", version="1.0"
                            ),
                        )
                        response = JSONRPCResponse(
                            jsonrpc="2.0",
                            id=req_id,
                            result=result.model_dump(
                                by_alias=True, mode="json", exclude_none=True
                            ),
                        )
                        response_msg = SessionMessage(
                            message=JSONRPCMessage(response)
                        )
                        # Send response asynchronously to avoid blocking the
                        # callback when _receive_loop is not running
                        assert conn._to_session_send is not None
                        asyncio.create_task(
                            conn._to_session_send.send(response_msg)
                        )

                    elif req_method == "tools/list":
                        result = ListToolsResult(
                            tools=[
                                Tool(
                                    name="test_tool",
                                    description="A test tool",
                                    inputSchema={
                                        "type": "object",
                                        "properties": {},
                                    },
                                )
                            ]
                        )
                        response = JSONRPCResponse(
                            jsonrpc="2.0",
                            id=req_id,
                            result=result.model_dump(
                                by_alias=True, mode="json", exclude_none=True
                            ),
                        )
                        response_msg = SessionMessage(
                            message=JSONRPCMessage(response)
                        )
                        assert conn._to_session_send is not None
                        asyncio.create_task(
                            conn._to_session_send.send(response_msg)
                        )

            return {}

        return {}

    acp_agent.client.send_request = mock_send_request  # type: ignore[method-assign]

    # Step 1: Establish connection via connect_acp_mcp_server
    connection_id = await acp_agent.connect_acp_mcp_server(server_config)
    assert connection_id == "test-conn-tools"

    conn = acp_agent._mcp_manager.get_connection(connection_id)
    assert conn is not None

    # Step 2: Create transport and provider
    transport = AcpMcpTransport(conn)
    acp_server_config = AcpMCPServerConfig(
        acp_id=server_config.id,
        name=server_config.name,
        timeout=10.0,
    )
    provider = MCPResourceProvider(
        server=acp_server_config, transport=transport
    )

    # Step 3: Enter provider context (triggers real initialize()) and get tools
    with anyio.fail_after(5):
        async with provider:
            tools = await provider.get_tools()

    # Step 4: Verify we got tools back
    assert len(tools) >= 1, "Expected at least one tool from get_tools()"

    # Step 5: Verify send_to_client was called with the tools/list request
    assert len(received_mcp_messages) >= 2, (
        "send_to_client was not called enough times - "
        "_forward_to_client() may be broken"
    )

    tools_list_found = False
    for msg in received_mcp_messages:
        inner = msg.get("message")
        if (
            inner is not None
            and hasattr(inner, "message")
            and hasattr(inner.message, "root")
            and hasattr(inner.message.root, "method")
            and inner.message.root.method == "tools/list"
        ):
            tools_list_found = True
            break

    assert tools_list_found, (
        f"No tools/list request found in {len(received_mcp_messages)} "
        f"received messages: {received_mcp_messages}"
    )
