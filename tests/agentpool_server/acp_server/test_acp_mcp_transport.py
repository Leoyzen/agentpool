"""Tests for AcpMcpTransport stream simulation."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

from mcp import ClientSession
import pytest

from acp.schema.mcp import AcpMcpServer
from agentpool_server.acp_server.acp_mcp_manager import AcpMcpConnection
from agentpool_server.acp_server.acp_mcp_transport import AcpMcpTransport


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def server_config() -> AcpMcpServer:
    """Create a test ACP MCP server configuration."""
    return AcpMcpServer(name="test-server", id="test-id")


@pytest.fixture
def send_to_client() -> AsyncMock:
    """Create an AsyncMock send_to_client callable."""
    return AsyncMock(return_value=None)


@pytest.fixture
async def opened_connection(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> AsyncGenerator[AcpMcpConnection]:
    """Create and open an AcpMcpConnection with active streams."""
    conn = AcpMcpConnection(
        connection_id="conn-1",
        acp_id=server_config.id,
        server=server_config,
        _send_to_client=send_to_client,
    )
    await conn.open()
    yield conn
    await conn.close()


@pytest.fixture
def closed_connection(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> AcpMcpConnection:
    """Create an AcpMcpConnection that has NOT been opened."""
    return AcpMcpConnection(
        connection_id="conn-2",
        acp_id=server_config.id,
        server=server_config,
        _send_to_client=send_to_client,
    )


# AcpMcpTransport tests


async def test_transport_init(opened_connection: AcpMcpConnection) -> None:
    """Transport initializes with an AcpMcpConnection."""
    transport = AcpMcpTransport(connection=opened_connection)

    assert transport._connection is opened_connection
    assert transport._forwarder_task is None


async def test_connect_session_yields_client_session(
    opened_connection: AcpMcpConnection,
) -> None:
    """connect_session yields a ClientSession within the async context."""
    transport = AcpMcpTransport(connection=opened_connection)

    async with transport.connect_session() as session:
        assert isinstance(session, ClientSession)


async def test_stream_forwarding(
    opened_connection: AcpMcpConnection,
) -> None:
    """Messages written to from_session_send are forwarded to send_to_client."""
    transport = AcpMcpTransport(connection=opened_connection)

    message = {"jsonrpc": "2.0", "method": "tools/call", "id": 1}

    async with transport.connect_session():
        # Write message to the connection's send end.
        # With buffer size 0, this blocks until the forwarder receives it.
        await opened_connection._from_session_send.send(message)  # type: ignore[attr-defined]

    # After context exit, send_to_client should have been called.
    opened_connection._send_to_client.assert_awaited_once_with(message)


async def test_multiple_messages_forwarded(
    opened_connection: AcpMcpConnection,
) -> None:
    """Multiple messages are forwarded sequentially by the forwarder task."""
    transport = AcpMcpTransport(connection=opened_connection)

    messages = [
        {"jsonrpc": "2.0", "method": "tools/call", "id": 1},
        {"jsonrpc": "2.0", "method": "tools/call", "id": 2},
        {"jsonrpc": "2.0", "method": "tools/list", "id": 3},
    ]

    async with transport.connect_session():
        for msg in messages:
            await opened_connection._from_session_send.send(msg)  # type: ignore[attr-defined]

    assert opened_connection._send_to_client.await_count == len(messages)
    for msg in messages:
        opened_connection._send_to_client.assert_any_await(msg)


async def test_forwarder_task_cleanup_on_session_exit(
    opened_connection: AcpMcpConnection,
) -> None:
    """Forwarder task is created during session and cancelled on exit."""
    transport = AcpMcpTransport(connection=opened_connection)

    async with transport.connect_session():
        assert transport._forwarder_task is not None
        assert not transport._forwarder_task.done()

    # After exiting the context, the forwarder task should be cleaned up.
    assert transport._forwarder_task is None


async def test_connection_not_opened_raises(
    closed_connection: AcpMcpConnection,
) -> None:
    """connect_session raises RuntimeError when connection is not opened."""
    transport = AcpMcpTransport(connection=closed_connection)

    with pytest.raises(RuntimeError, match="Connection not opened"):
        async with transport.connect_session():
            pass  # pragma: no cover


async def test_connection_streams_not_opened_property(
    closed_connection: AcpMcpConnection,
) -> None:
    """Accessing from_session_receive on unopened connection raises RuntimeError."""
    with pytest.raises(RuntimeError, match="Connection not opened"):
        _ = closed_connection.from_session_receive


async def test_connection_open_creates_streams(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """open() creates the memory object streams on the connection."""
    conn = AcpMcpConnection(
        connection_id="conn-3",
        acp_id=server_config.id,
        server=server_config,
        _send_to_client=send_to_client,
    )

    assert not conn._is_open
    assert conn._from_session_send is None
    assert conn._from_session_receive is None

    await conn.open()

    assert conn._is_open
    assert conn._from_session_send is not None
    assert conn._from_session_receive is not None
    assert conn._to_session_send is not None
    assert conn._to_session_receive is not None

    await conn.close()


async def test_connection_close_cleans_streams(
    opened_connection: AcpMcpConnection,
) -> None:
    """close() closes all streams and resets the open flag."""
    await opened_connection.close()

    assert not opened_connection._is_open


async def test_transport_reusable_across_sessions(
    opened_connection: AcpMcpConnection,
) -> None:
    """Transport can be used for multiple connect_session calls."""
    transport = AcpMcpTransport(connection=opened_connection)

    for _ in range(2):
        async with transport.connect_session() as session:
            assert isinstance(session, ClientSession)


async def test_forwarder_cleanup_when_no_messages(
    opened_connection: AcpMcpConnection,
) -> None:
    """Forwarder task is cancelled even when no messages were sent."""
    transport = AcpMcpTransport(connection=opened_connection)

    async with transport.connect_session():
        pass

    assert transport._forwarder_task is None
