from unittest.mock import AsyncMock, patch

import anyio
import pytest

from acp.schema.mcp import AcpMcpServer
from agentpool_server.acp_server.acp_mcp_manager import AcpMcpConnection
from agentpool_server.acp_server.acp_mcp_transport import AcpMcpTransport


@pytest.fixture
async def connection():
    """Create an opened AcpMcpConnection for transport tests."""
    server = AcpMcpServer(name="test-server", id="test-123")
    conn = AcpMcpConnection(
        connection_id="test-conn-1",
        server_config=server,
        send_to_client=AsyncMock(),
    )
    await conn.open()
    yield conn
    await conn.close()


class TestAcpMcpTransportInitialization:
    """Tests for AcpMcpTransport basic initialization."""

    @pytest.mark.anyio
    async def test_transport_initialization(self, connection):
        """Transport should store the connection reference."""
        transport = AcpMcpTransport(connection)
        assert transport._connection is connection

    @pytest.mark.anyio
    async def test_connect_session_yields_client_session(self, connection):
        """connect_session should yield a ClientSession instance."""
        from mcp.client.session import ClientSession

        transport = AcpMcpTransport(connection)

        with patch("mcp.client.session.ClientSession.initialize", new_callable=AsyncMock):
            async with transport.connect_session() as session:
                assert isinstance(session, ClientSession)


class TestAcpMcpTransportMessageForwarding:
    """Tests for bidirectional message forwarding through streams."""

    @pytest.mark.anyio
    async def test_message_forwarding_from_session_to_client(self, connection):
        """Messages from MCP server should be forwarded to the client."""
        transport = AcpMcpTransport(connection)
        msg = {"jsonrpc": "2.0", "id": 1, "result": {"status": "ok"}}

        with patch("mcp.client.session.ClientSession.initialize", new_callable=AsyncMock):
            async with transport.connect_session():
                # Simulate MCP server sending a response
                await connection.from_session.send(msg)

                # Should be forwarded to client via send_to_client
                connection._send_to_client.assert_awaited_once()
                call_args = connection._send_to_client.call_args[0][0]
                assert call_args["connectionId"] == connection.connection_id
                assert call_args["message"] == msg

    @pytest.mark.anyio
    async def test_multiple_messages_forwarded(self, connection):
        """Multiple messages should be forwarded in order."""
        transport = AcpMcpTransport(connection)
        messages = [
            {"jsonrpc": "2.0", "id": i, "result": {"data": i}}
            for i in range(3)
        ]

        with patch("mcp.client.session.ClientSession.initialize", new_callable=AsyncMock):
            async with transport.connect_session():
                for msg in messages:
                    await connection.from_session.send(msg)

                assert connection._send_to_client.await_count == len(messages)
                for i, expected in enumerate(messages):
                    call_args = connection._send_to_client.call_args_list[i][0][0]
                    assert call_args["connectionId"] == connection.connection_id
                    assert call_args["message"] == expected

    @pytest.mark.anyio
    async def test_forwarder_task_cleanup_on_session_exit(self, connection):
        """Forwarder task should be cancelled when session context exits."""
        transport = AcpMcpTransport(connection)

        with patch("mcp.client.session.ClientSession.initialize", new_callable=AsyncMock):
            async with transport.connect_session():
                pass  # Session exits here

        # After session exit, forwarder should be cancelled
        # Sending a message should block because nobody is reading from the stream
        with pytest.raises(TimeoutError):
            with anyio.fail_after(0.1):
                await connection.from_session.send({"jsonrpc": "2.0", "id": 99, "result": {}})


class TestAcpMcpTransportReusability:
    """Tests verifying transport can be used for multiple sessions."""

    @pytest.mark.anyio
    async def test_transport_reusable_across_sessions(self, connection):
        """Transport should support multiple connect_session calls."""
        transport = AcpMcpTransport(connection)

        msg1 = {"jsonrpc": "2.0", "id": 1, "result": {"a": 1}}
        with patch("mcp.client.session.ClientSession.initialize", new_callable=AsyncMock):
            async with transport.connect_session():
                await connection.from_session.send(msg1)
                call_args = connection._send_to_client.call_args[0][0]
                assert call_args["connectionId"] == connection.connection_id
                assert call_args["message"] == msg1

        # Connection streams remain open, transport is reusable
        msg2 = {"jsonrpc": "2.0", "id": 2, "result": {"b": 2}}
        with patch("mcp.client.session.ClientSession.initialize", new_callable=AsyncMock):
            async with transport.connect_session():
                await connection.from_session.send(msg2)
                call_args = connection._send_to_client.call_args[0][0]
                assert call_args["connectionId"] == connection.connection_id
                assert call_args["message"] == msg2

    @pytest.mark.anyio
    async def test_each_session_has_isolated_forwarder(self, connection):
        """Each session should get its own forwarder task."""
        transport = AcpMcpTransport(connection)

        msg1 = {"jsonrpc": "2.0", "id": 1, "result": {"session": 1}}
        with patch("mcp.client.session.ClientSession.initialize", new_callable=AsyncMock):
            async with transport.connect_session():
                await connection.from_session.send(msg1)
                call_args = connection._send_to_client.call_args[0][0]
                assert call_args["connectionId"] == connection.connection_id
                assert call_args["message"] == msg1

        msg2 = {"jsonrpc": "2.0", "id": 2, "result": {"session": 2}}
        with patch("mcp.client.session.ClientSession.initialize", new_callable=AsyncMock):
            async with transport.connect_session():
                await connection.from_session.send(msg2)
                call_args = connection._send_to_client.call_args[0][0]
                assert call_args["connectionId"] == connection.connection_id
                assert call_args["message"] == msg2


class TestAcpMcpTransportErrorHandling:
    """Tests for error conditions."""

    @pytest.mark.anyio
    async def test_message_after_forwarder_cancelled_not_delivered(self, connection):
        """Messages sent after forwarder cancellation should not be delivered."""
        transport = AcpMcpTransport(connection)

        with patch("mcp.client.session.ClientSession.initialize", new_callable=AsyncMock):
            async with transport.connect_session():
                # Send one message during active session
                msg = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
                await connection.from_session.send(msg)
                call_args = connection._send_to_client.call_args[0][0]
                assert call_args["message"]["id"] == 1

        # After session close, forwarder is cancelled
        # Sending a message should block because nobody is reading from the stream
        msg = {"jsonrpc": "2.0", "id": 2, "result": {"ok": False}}
        with pytest.raises(TimeoutError):
            with anyio.fail_after(0.1):
                await connection.from_session.send(msg)

        # _send_to_client should only have been called once (for the message during session)
        connection._send_to_client.assert_awaited_once()
