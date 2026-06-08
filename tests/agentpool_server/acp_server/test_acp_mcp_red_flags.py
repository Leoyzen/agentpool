"""Red-flag regression tests for MCP-over-ACP message forwarding.

These tests guard against critical bugs in the MCP-over-ACP bridging layer
that can silently break tool discovery and all MCP operations.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import anyio
import pytest

from acp.schema.mcp import AcpMcpServer
from agentpool import Agent
from agentpool.delegation import AgentPool
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
from agentpool_server.acp_server.acp_mcp_manager import AcpMcpConnection
from agentpool_server.acp_server.acp_mcp_transport import AcpMcpTransport


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


async def test_no_double_wrap_on_mcp_message_forwarding(
    acp_agent: AgentPoolACPAgent,
    server_config: AcpMcpServer,
) -> None:
    """Regression test: mcp/message must NOT be double-wrapped.

    Bug: connect_acp_mcp_server() created a send_to_client callback that
    wrapped messages in {"connectionId": conn_id, "message": msg}. But
    AcpMcpConnection.send_to_client() ALREADY wraps messages the same way.
    This caused double-wrapping:

        {"connectionId": "x", "message":
            {"connectionId": "x", "message":
                {"jsonrpc": "2.0", ...}}}

    Impact: When fastmcp ClientSession sends tools/list internally, the
    message gets double-wrapped. The client receives a malformed request
    and silently fails to return tools.

    Fix: The callback now passes through the already-wrapped message directly.

    This test simulates the real fastmcp flow:
    1. ClientSession writes to from_session
    2. Transport forwarder reads and calls connection.send_to_client()
    3. connection.send_to_client() wraps as {"connectionId": id, "message": msg}
    4. The callback from connect_acp_mcp_server() passes through directly
    5. client.send_request("mcp/message", wrapped) receives single-wrapped msg
    """
    # Setup: mock client returns connectionId on mcp/connect
    send_request_mock = AsyncMock(return_value={"connectionId": "conn-redflag-1"})
    acp_agent.client.send_request = send_request_mock  # type: ignore[method-assign]

    # Step 1: Establish connection (this creates the callback)
    connection_id = await acp_agent.connect_acp_mcp_server(server_config)
    assert connection_id == "conn-redflag-1"

    conn = acp_agent._mcp_manager.get_connection(connection_id)
    assert conn is not None

    # Step 2: Start transport session to activate the forwarder task
    transport = AcpMcpTransport(conn)
    raw_mcp_msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}

    with patch("mcp.client.session.ClientSession.initialize", new_callable=AsyncMock):
        async with transport.connect_session():
            # Step 3: Simulate fastmcp ClientSession writing to from_session
            await conn.from_session.send(raw_mcp_msg)

            # Step 4: Wait for the forwarder to process and deliver
            with anyio.fail_after(1):
                # Spin briefly to let the forwarder task run
                await anyio.sleep(0.05)

    # Step 5: Verify what the mock client received
    # Find the send_request call for "mcp/message"
    mcp_message_calls = [
        call for call in send_request_mock.call_args_list
        if call.args[0] == "mcp/message"
    ]
    assert len(mcp_message_calls) == 1, (
        f"Expected exactly one mcp/message call, got {len(mcp_message_calls)}"
    )

    _, params = mcp_message_calls[0].args

    # The params MUST be a single-wrapped object: {"connectionId": ..., "message": raw_msg}
    assert "connectionId" in params, "params must contain connectionId"
    assert "message" in params, "params must contain message"
    assert params["connectionId"] == connection_id

    inner_message = params["message"]

    # CRITICAL: inner_message must be the RAW MCP JSON-RPC message,
    # NOT another wrapped object. If double-wrapping occurred, this
    # would be {"connectionId": ..., "message": ...} instead.
    assert inner_message == raw_mcp_msg, (
        f"Double-wrapping bug detected! Expected raw message {raw_mcp_msg}, "
        f"got {inner_message}"
    )

    # Also verify it's not a dict with nested wrapping keys
    assert "connectionId" not in inner_message, (
        "inner_message contains 'connectionId' — double-wrapping bug!"
    )


async def test_elicitation_passthrough_returns_correct_result() -> None:
    """Regression test: response to pending request is returned directly.

    When a client-initiated request is pending, the response should be
    fulfilled via the correlation registry instead of being forwarded
    through _send_to_client.
    """
    mock_send = AsyncMock()
    server_cfg = AcpMcpServer(name="test", id="test-id")
    conn = AcpMcpConnection("conn-passthrough", server_cfg, mock_send)
    await conn.open()

    future = await conn.register_pending_request(3)
    response = {"jsonrpc": "2.0", "id": 3, "result": {"action": "accept"}}
    result = await conn.send_to_client(response)

    assert result == response
    assert future.done()
    assert future.result() == response
    mock_send.assert_not_awaited()

    await conn.close()


async def test_late_response_after_timeout_is_dropped() -> None:
    """Regression test: late response after timeout must be dropped.

    If a pending request times out (future is cancelled), any late
    response for that request should be dropped, not forwarded.
    """
    mock_send = AsyncMock()
    server_cfg = AcpMcpServer(name="test", id="test-id")
    conn = AcpMcpConnection("conn-timeout", server_cfg, mock_send)
    await conn.open()

    future = await conn.register_pending_request(1)
    future.cancel()  # Simulate timeout cancellation

    response = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
    await conn.send_to_client(response)

    mock_send.assert_not_awaited()

    await conn.close()


async def test_unmatched_response_is_dropped() -> None:
    """Regression test: unmatched response must be dropped with a warning.

    Responses for request IDs that were never registered should not
    be forwarded to the client.
    """
    mock_send = AsyncMock()
    server_cfg = AcpMcpServer(name="test", id="test-id")
    conn = AcpMcpConnection("conn-unmatched", server_cfg, mock_send)
    await conn.open()

    response = {"jsonrpc": "2.0", "id": 99, "result": {}}

    with patch("agentpool_server.acp_server.acp_mcp_manager.logger.warning") as mock_warn:
        await conn.send_to_client(response)
        mock_send.assert_not_awaited()
        mock_warn.assert_called_once()

    await conn.close()


async def test_response_fulfillment_prevents_fake_response_injection() -> None:
    """Regression test: fulfilled response must not reach _to_session_send.

    When a response is consumed by the correlation registry, it should
    NOT be injected back into the MCP session stream.
    """
    mock_send = AsyncMock()
    server_cfg = AcpMcpServer(name="test", id="test-id")
    conn = AcpMcpConnection("conn-no-inject", server_cfg, mock_send)
    await conn.open()

    await conn.register_pending_request(2)
    response = {"jsonrpc": "2.0", "id": 2, "result": {"status": "ok"}}
    await conn.send_to_client(response)

    mock_send.assert_not_awaited()
    # Verify _to_session_send received nothing
    with pytest.raises(anyio.WouldBlock):
        conn.to_session.receive_nowait()

    await conn.close()


async def test_duplicate_response_invalid_state_handled_gracefully() -> None:
    """Regression test: duplicate response must not crash or leak.

    If two responses arrive for the same request ID, the second must
    be dropped without raising exceptions or calling _send_to_client.
    """
    mock_send = AsyncMock()
    server_cfg = AcpMcpServer(name="test", id="test-id")
    conn = AcpMcpConnection("conn-duplicate", server_cfg, mock_send)
    await conn.open()

    future = await conn.register_pending_request(5)
    response1 = {"jsonrpc": "2.0", "id": 5, "result": {"first": True}}
    response2 = {"jsonrpc": "2.0", "id": 5, "result": {"second": True}}

    # First response fulfills the pending request
    await conn.send_to_client(response1)
    assert future.done()
    assert future.result() == response1

    # Second response should be dropped gracefully
    await conn.send_to_client(response2)

    mock_send.assert_not_awaited()

    await conn.close()
