"""Tests for AcpMcpConnectionManager and AcpMcpConnection."""

from __future__ import annotations

from unittest.mock import AsyncMock

import anyio
import pytest

from acp.schema.mcp import AcpMcpServer
from agentpool_server.acp_server.acp_mcp_manager import (
    AcpMcpConnection,
    AcpMcpConnectionManager,
)


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def server_config() -> AcpMcpServer:
    """Create a test ACP MCP server configuration."""
    return AcpMcpServer(name="test-server", id="test-id")


@pytest.fixture
def send_to_client() -> AsyncMock:
    """Create an AsyncMock send_to_client callable."""
    return AsyncMock(return_value=None)


# AcpMcpConnectionManager tests


async def test_create_connection_basic(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """Creating a connection stores it and opens streams."""
    manager = AcpMcpConnectionManager()

    conn = await manager.create_connection("conn-1", server_config, send_to_client)

    assert conn.connection_id == "conn-1"
    assert conn.server_config == server_config
    assert manager.get_connection("conn-1") is conn
    assert "conn-1" in manager
    assert len(manager) == 1


async def test_create_connection_duplicate_raises(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """Creating a connection with duplicate ID raises ValueError."""
    manager = AcpMcpConnectionManager()
    await manager.create_connection("conn-1", server_config, send_to_client)

    with pytest.raises(ValueError, match="MCP connection 'conn-1' already exists"):
        await manager.create_connection("conn-1", server_config, send_to_client)


async def test_get_connection_existing(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """get_connection returns the connection for an existing ID."""
    manager = AcpMcpConnectionManager()
    created = await manager.create_connection("conn-1", server_config, send_to_client)

    result = manager.get_connection("conn-1")

    assert result is created


async def test_get_connection_missing() -> None:
    """get_connection returns None for a missing ID."""
    manager = AcpMcpConnectionManager()

    result = manager.get_connection("nonexistent")

    assert result is None


async def test_remove_connection(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """remove_connection removes the connection and closes it."""
    manager = AcpMcpConnectionManager()
    await manager.create_connection("conn-1", server_config, send_to_client)

    await manager.remove_connection("conn-1")

    assert manager.get_connection("conn-1") is None
    assert "conn-1" not in manager
    assert len(manager) == 0


async def test_remove_connection_missing_does_not_raise(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """remove_connection is a no-op for a missing ID."""
    manager = AcpMcpConnectionManager()
    await manager.create_connection("conn-1", server_config, send_to_client)

    await manager.remove_connection("missing")

    assert len(manager) == 1


async def test_close_all(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """close_all closes and removes all connections."""
    manager = AcpMcpConnectionManager()
    await manager.create_connection("conn-1", server_config, send_to_client)
    await manager.create_connection("conn-2", server_config, send_to_client)

    await manager.close_all()

    assert len(manager) == 0
    assert manager.get_connection("conn-1") is None
    assert manager.get_connection("conn-2") is None


# AcpMcpConnection tests


async def test_connection_open_creates_streams(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """Open creates memory streams for session communication."""
    conn = AcpMcpConnection("conn-1", server_config, send_to_client)

    await conn.open()

    assert conn._to_session_send is not None
    assert conn._to_session_receive is not None
    assert conn._from_session_send is not None
    assert conn._from_session_receive is not None


async def test_connection_close_closes_streams(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """Close closes all streams and marks connection closed."""
    conn = AcpMcpConnection("conn-1", server_config, send_to_client)
    await conn.open()

    await conn.close()

    assert conn._closed is True


async def test_connection_close_is_idempotent(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """Close can be called multiple times without error."""
    conn = AcpMcpConnection("conn-1", server_config, send_to_client)
    await conn.open()
    await conn.close()

    await conn.close()

    assert conn._closed is True


async def test_connection_handle_client_message_routes_to_session(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """handle_client_message sends the message to the session receive stream."""
    conn = AcpMcpConnection("conn-1", server_config, send_to_client)
    await conn.open()

    message = {"jsonrpc": "2.0", "method": "test", "id": 1}

    # Stream has capacity 0 (handoff semantics), so send and receive must be concurrent
    async with anyio.create_task_group() as tg:
        tg.start_soon(conn.handle_client_message, message)
        received = await conn.to_session.receive()

    assert received == message


async def test_connection_handle_client_message_not_opened_raises(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """handle_client_message raises RuntimeError when connection is not opened."""
    conn = AcpMcpConnection("conn-1", server_config, send_to_client)

    with pytest.raises(RuntimeError, match="Connection not opened"):
        await conn.handle_client_message({"jsonrpc": "2.0", "method": "test"})


async def test_connection_send_to_client_formats_message(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """send_to_client wraps the message with connectionId and forwards it."""
    conn = AcpMcpConnection("conn-1", server_config, send_to_client)
    message = {"jsonrpc": "2.0", "result": "ok", "id": 1}

    await conn.send_to_client(message)

    send_to_client.assert_awaited_once_with(
        {"connectionId": "conn-1", "message": message}
    )


async def test_connection_to_session_property(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """to_session property returns the receive stream after opening."""
    conn = AcpMcpConnection("conn-1", server_config, send_to_client)
    await conn.open()

    stream = conn.to_session

    assert stream is conn._to_session_receive


async def test_connection_to_session_not_opened_raises(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """to_session property raises RuntimeError when connection is not opened."""
    conn = AcpMcpConnection("conn-1", server_config, send_to_client)

    with pytest.raises(RuntimeError, match="Connection not opened"):
        _ = conn.to_session


async def test_connection_from_session_property(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """from_session property returns the send stream after opening."""
    conn = AcpMcpConnection("conn-1", server_config, send_to_client)
    await conn.open()

    stream = conn.from_session

    assert stream is conn._from_session_send


async def test_connection_from_session_not_opened_raises(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """from_session property raises RuntimeError when connection is not opened."""
    conn = AcpMcpConnection("conn-1", server_config, send_to_client)

    with pytest.raises(RuntimeError, match="Connection not opened"):
        _ = conn.from_session


async def test_connection_from_session_receive_property(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """from_session_receive property returns the receive stream after opening."""
    conn = AcpMcpConnection("conn-1", server_config, send_to_client)
    await conn.open()

    stream = conn.from_session_receive

    assert stream is conn._from_session_receive


async def test_connection_from_session_receive_not_opened_raises(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """from_session_receive property raises RuntimeError when not opened."""
    conn = AcpMcpConnection("conn-1", server_config, send_to_client)

    with pytest.raises(RuntimeError, match="Connection not opened"):
        _ = conn.from_session_receive
