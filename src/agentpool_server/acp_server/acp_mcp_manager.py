"""MCP-over-ACP connection manager.

Manages bidirectional MCP connections tunnelled over the ACP protocol.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import anyio

from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import Callable

    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

    from acp.schema.mcp import AcpMcpServer

logger = get_logger(__name__)


class AcpMcpConnection:
    """Represents a single active MCP-over-ACP connection.

    Bridges MCP JSON-RPC messages between fastmcp's ClientSession
    and the ACP connection via mcp/message ext_method calls.
    """

    def __init__(
        self,
        connection_id: str,
        server_config: AcpMcpServer,
        send_to_client: Callable[[dict[str, Any]], Any],
    ) -> None:
        """Initialize an MCP-over-ACP connection.

        Args:
            connection_id: Unique identifier for this connection.
            server_config: The ACP MCP server configuration.
            send_to_client: Callable to send mcp/message to the client.
        """
        self.connection_id = connection_id
        self.server_config = server_config
        self._send_to_client = send_to_client
        self._to_session_send: MemoryObjectSendStream[dict[str, Any]] | None = None
        self._to_session_receive: MemoryObjectReceiveStream[dict[str, Any]] | None = None
        self._from_session_send: MemoryObjectSendStream[dict[str, Any]] | None = None
        self._from_session_receive: MemoryObjectReceiveStream[dict[str, Any]] | None = None
        self._closed = False

    async def open(self) -> None:
        """Open the memory streams for the MCP session."""
        self._to_session_send, self._to_session_receive = anyio.create_memory_object_stream[
            dict[str, Any]
        ](0)
        self._from_session_send, self._from_session_receive = anyio.create_memory_object_stream[
            dict[str, Any]
        ](0)
        logger.info("MCP-over-ACP connection opened", connection_id=self.connection_id)

    async def close(self) -> None:
        """Close the connection and clean up streams."""
        if self._closed:
            return
        self._closed = True
        for stream in [
            self._to_session_send,
            self._to_session_receive,
            self._from_session_send,
            self._from_session_receive,
        ]:
            if stream is not None:
                await stream.aclose()
        logger.info("MCP-over-ACP connection closed", connection_id=self.connection_id)

    async def handle_client_message(self, message: dict[str, Any]) -> None:
        """Handle an incoming mcp/message from the client.

        Routes the message to the MCP session's receive stream.
        """
        if self._to_session_send is None:
            raise RuntimeError("Connection not opened")
        try:
            await self._to_session_send.send(message)
        except (anyio.ClosedResourceError, anyio.EndOfStream):
            logger.debug(
                "Failed to route message: connection already closed",
                connection_id=self.connection_id,
            )

    async def send_to_client(self, message: dict[str, Any]) -> Any:
        """Send an mcp/message to the client.

        Args:
            message: MCP JSON-RPC message dict.

        Returns:
            Response from client (for requests) or None (for notifications).
        """
        return await self._send_to_client(
            {"connectionId": self.connection_id, "message": message}
        )

    @property
    def to_session(self) -> MemoryObjectReceiveStream[dict[str, Any]]:
        """Stream for receiving messages FROM the ACP client INTO the MCP session."""
        if self._to_session_receive is None:
            raise RuntimeError("Connection not opened")
        return self._to_session_receive

    @property
    def from_session(self) -> MemoryObjectSendStream[dict[str, Any]]:
        """Stream for sending messages FROM the MCP session TO the ACP client."""
        if self._from_session_send is None:
            raise RuntimeError("Connection not opened")
        return self._from_session_send

    @property
    def from_session_receive(self) -> MemoryObjectReceiveStream[dict[str, Any]]:
        """Stream for reading messages written by the MCP session."""
        if self._from_session_receive is None:
            raise RuntimeError("Connection not opened")
        return self._from_session_receive


class AcpMcpConnectionManager:
    """Manages multiple MCP-over-ACP connections.

    Maps connection IDs to active AcpMcpConnection instances.
    """

    def __init__(self) -> None:
        """Initialize the connection manager."""
        self._connections: dict[str, AcpMcpConnection] = {}
        self._lock = asyncio.Lock()

    async def create_connection(
        self,
        connection_id: str,
        server_config: AcpMcpServer,
        send_to_client: Callable[[dict[str, Any]], Any],
    ) -> AcpMcpConnection:
        """Create and register a new MCP-over-ACP connection.

        Args:
            connection_id: Unique identifier for this connection.
            server_config: The ACP MCP server configuration.
            send_to_client: Callable to send mcp/message to the client.

        Returns:
            The newly created connection.

        Raises:
            ValueError: If a connection with the same ID already exists.
        """
        async with self._lock:
            if not connection_id:
                raise ValueError("connection_id cannot be empty")
            if connection_id in self._connections:
                raise ValueError(f"MCP connection '{connection_id}' already exists")
            conn = AcpMcpConnection(connection_id, server_config, send_to_client)
            await conn.open()
            self._connections[connection_id] = conn
            logger.info("MCP connection created", connection_id=connection_id)
            return conn

    async def remove_connection(self, connection_id: str) -> None:
        """Remove and close an MCP-over-ACP connection.

        Args:
            connection_id: The connection ID to remove.
        """
        async with self._lock:
            conn = self._connections.pop(connection_id, None)
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                logger.exception("Failed to close MCP connection", connection_id=connection_id)
            logger.info("MCP connection removed", connection_id=connection_id)

    def get_connection(self, connection_id: str) -> AcpMcpConnection | None:
        """Get an active connection by ID.

        Args:
            connection_id: The connection ID to look up.

        Returns:
            The connection if found, None otherwise.
        """
        return self._connections.get(connection_id)

    async def close_all(self) -> None:
        """Close all active connections."""
        async with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
        for conn in connections:
            try:
                await conn.close()
            except Exception:
                logger.exception("Failed to close MCP connection", connection_id=conn.connection_id)
        logger.info("All MCP connections closed")

    def __contains__(self, connection_id: str) -> bool:
        """Check if a connection ID is active."""
        return connection_id in self._connections

    def __len__(self) -> int:
        """Return the number of active connections."""
        return len(self._connections)
