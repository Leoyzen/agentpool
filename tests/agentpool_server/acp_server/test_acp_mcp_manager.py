"""Tests for AcpMcpConnectionManager and AcpMcpConnection."""

from __future__ import annotations

from unittest.mock import AsyncMock

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
    """Creating a connection stores it and indexes by acp_id."""
    manager = AcpMcpConnectionManager()

    conn = await manager.create_connection("conn-1", server_config, send_to_client)

    assert conn.connection_id == "conn-1"
    assert conn.acp_id == server_config.id
    assert conn.server == server_config
    assert manager.get_connection("conn-1") is conn
    assert manager._by_acp_id[server_config.id] == ["conn-1"]


async def test_create_connection_duplicate_overwrites(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """Creating a connection with duplicate ID overwrites the existing one."""
    manager = AcpMcpConnectionManager()
    first = await manager.create_connection("conn-1", server_config, send_to_client)
    second = await manager.create_connection("conn-1", server_config, send_to_client)

    assert manager.get_connection("conn-1") is second
    assert manager.get_connection("conn-1") is not first
    assert manager._by_acp_id[server_config.id] == ["conn-1", "conn-1"]


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
    """remove_connection removes the connection and cleans up the acp_id index."""
    manager = AcpMcpConnectionManager()
    await manager.create_connection("conn-1", server_config, send_to_client)

    await manager.remove_connection("conn-1")

    assert manager.get_connection("conn-1") is None
    assert server_config.id not in manager._by_acp_id


async def test_remove_connection_missing_is_no_op(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """remove_connection is a no-op for a missing ID."""
    manager = AcpMcpConnectionManager()
    await manager.create_connection("conn-1", server_config, send_to_client)

    await manager.remove_connection("missing")

    assert manager.get_connection("conn-1") is not None


async def test_remove_connection_cleans_partial_acp_index(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """remove_connection removes only the relevant entry from the acp_id index."""
    manager = AcpMcpConnectionManager()
    await manager.create_connection("conn-1", server_config, send_to_client)
    await manager.create_connection("conn-2", server_config, send_to_client)

    await manager.remove_connection("conn-1")

    assert manager.get_connection("conn-2") is not None
    assert manager._by_acp_id[server_config.id] == ["conn-2"]


async def test_close_all(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """close_all removes all connections and cleans up indexes."""
    manager = AcpMcpConnectionManager()
    await manager.create_connection("conn-1", server_config, send_to_client)
    await manager.create_connection("conn-2", server_config, send_to_client)

    await manager.close_all()

    assert manager.get_connection("conn-1") is None
    assert manager.get_connection("conn-2") is None
    assert len(manager._connections) == 0
    assert len(manager._by_acp_id) == 0


async def test_close_all_empty_manager() -> None:
    """close_all on an empty manager is a no-op."""
    manager = AcpMcpConnectionManager()

    await manager.close_all()

    assert len(manager._connections) == 0
    assert len(manager._by_acp_id) == 0


# AcpMcpConnection tests


async def test_connection_attributes(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """AcpMcpConnection stores the expected attributes."""
    conn = AcpMcpConnection(
        connection_id="conn-1",
        acp_id=server_config.id,
        server=server_config,
        _send_to_client=send_to_client,
    )

    assert conn.connection_id == "conn-1"
    assert conn.acp_id == "test-id"
    assert conn.server == server_config


async def test_connection_handle_client_message_does_not_raise(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """handle_client_message accepts a message dict without raising."""
    conn = AcpMcpConnection(
        connection_id="conn-1",
        acp_id=server_config.id,
        server=server_config,
        _send_to_client=send_to_client,
    )

    message = {"jsonrpc": "2.0", "method": "test", "id": 1}
    await conn.handle_client_message(message)


async def test_connection_send_to_client_callable_stored(
    server_config: AcpMcpServer,
    send_to_client: AsyncMock,
) -> None:
    """The _send_to_client callable is stored on the connection."""
    conn = AcpMcpConnection(
        connection_id="conn-1",
        acp_id=server_config.id,
        server=server_config,
        _send_to_client=send_to_client,
    )

    assert conn._send_to_client is send_to_client
