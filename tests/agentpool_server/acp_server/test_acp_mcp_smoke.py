"""Smoke test for MCP-over-ACP with real JSON serialization round-trip.

Reproduces and verifies the fix for the bug where SessionMessage was not
converted to/from a JSON-serializable dict, causing _receive_loop() to
crash with an AttributeError and initialize() to hang/timeout.
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import anyio
from mcp.types import (
    Implementation,
    InitializeResult,
    ListToolsResult,
    ServerCapabilities,
    Tool,
)
import pytest

from acp.schema.mcp import AcpMcpServer
from agentpool import Agent
from agentpool.delegation import AgentPool
from agentpool.models.agents import NativeAgentConfig
from agentpool.models.manifest import AgentsManifest
from agentpool.resource_providers.mcp_provider import MCPResourceProvider
from agentpool_config.mcp_server import AcpMCPServerConfig
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
from agentpool_server.acp_server.acp_mcp_transport import AcpMcpTransport


pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture
def mock_connection():
    """Create a mock ACP connection."""
    return Mock()


@pytest.fixture
def default_test_agent() -> Agent:
    """Create a simple test agent with a pool backed by manifest config."""

    def simple_callback(message: str) -> str:
        return f"Test response: {message}"

    manifest = AgentsManifest(agents={"test_agent": NativeAgentConfig(model="test")})
    pool = AgentPool(manifest)
    return Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=pool)


@pytest.fixture
def acp_agent(mock_connection, default_test_agent: Agent) -> AgentPoolACPAgent:
    """Create a mock ACP agent for testing."""
    return AgentPoolACPAgent(client=mock_connection, default_agent=default_test_agent)


@pytest.fixture
def server_config() -> AcpMcpServer:
    """Create a test ACP MCP server configuration."""
    return AcpMcpServer(name="test-server", id="test-id")


async def test_initialize_and_get_tools_with_json_round_trip(
    acp_agent: AgentPoolACPAgent,
    server_config: AcpMcpServer,
) -> None:
    """Full smoke test: simulate real JSON serialization and verify no hang.

    This test reproduces the exact bug path:

    1. ClientSession._send() writes a SessionMessage to from_session.
    2. _forward_to_client() reads it and calls send_to_client(SessionMessage).
    3. send_to_client() must convert SessionMessage -> dict (the fix).
    4. The dict travels over the wire (simulated by json.dumps/json.loads).
    5. The remote ACP client receives a plain dict and replies with a dict.
    6. handle_client_message() receives the dict and must convert it back
       to SessionMessage (the fix) before writing to to_session.
    7. _receive_loop() reads a SessionMessage and can access .message.root.
    8. initialize() completes successfully instead of hanging.
    9. get_tools() also completes successfully.

    Before the fix, step 6 sent a raw dict to to_session, causing
    _receive_loop() to crash on ``message.message.root`` (AttributeError),
    which triggered CONNECTION_CLOSED for pending requests, making
    initialize() raise McpError / hang until timeout.
    """
    received_mcp_messages: list[dict] = []

    async def mock_send_request(method: str, params: dict) -> dict:
        if method == "mcp/connect":
            return {"connectionId": "smoke-conn-1"}

        if method == "mcp/message":
            received_mcp_messages.append(params)
            req_method = params.get("method")

            # Verify the fix: params must be in flattened ACP format
            assert "connectionId" in params, "params must contain connectionId"
            assert "method" in params, "params must contain method"
            assert "message" not in params, "old nested 'message' key must not be present"

            # Simulate real JSON serialization / deserialization round-trip
            serialized = json.dumps(params)
            deserialized: dict = json.loads(serialized)

            conn = acp_agent._mcp_manager.get_connection("smoke-conn-1")
            if conn is None:
                return {}

            req_id = deserialized.get("params", {}).get("protocolVersion")

            if req_method == "initialize":
                result = InitializeResult(
                    protocolVersion="2024-11-05",
                    capabilities=ServerCapabilities(),
                    serverInfo=Implementation(name="test", version="1.0"),
                )
                return result.model_dump(by_alias=True, mode="json", exclude_none=True)

            if req_method == "tools/list":
                result = ListToolsResult(
                    tools=[
                        Tool(
                            name="smoke_tool",
                            description="A smoke test tool",
                            inputSchema={"type": "object", "properties": {}},
                        )
                    ]
                )
                return result.model_dump(by_alias=True, mode="json", exclude_none=True)

            return {}

        return {}

    acp_agent.client.send_request = mock_send_request  # type: ignore[method-assign]

    connection_id = await acp_agent.connect_acp_mcp_server(server_config)
    assert connection_id == "smoke-conn-1"

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

    # Step 1: initialize() must complete without hanging
    with anyio.fail_after(5):
        async with provider:
            pass

    # Step 2: get_tools() must return tools after successful init
    with anyio.fail_after(5):
        async with provider:
            tools = await provider.get_tools()

    assert len(tools) >= 1, "Expected at least one tool from get_tools()"
    assert any(t.name.endswith("smoke_tool") for t in tools), (
        f"Expected a tool ending with 'smoke_tool' in {[t.name for t in tools]}"
    )

    # Verify that send_to_client produced plain dicts in flattened format
    assert len(received_mcp_messages) >= 2, (
        f"Expected at least 2 mcp/message calls, got {len(received_mcp_messages)}"
    )

    methods = [
        m.get("method")
        for m in received_mcp_messages
        if isinstance(m.get("method"), str)
    ]
    assert "initialize" in methods, f"Expected initialize in {methods}"
    assert "tools/list" in methods, f"Expected tools/list in {methods}"
