"""MCP-over-ACP connection manager.

Manages the lifecycle of MCP connections that are routed over the ACP channel.
Each connection maps to a unique connectionId and wraps an ACP-transport MCP server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import anyio

from agentpool.log import get_logger


if TYPE_CHECKING:
    from acp.schema.mcp import AcpMcpServer

logger = get_logger(__name__)


@dataclass
class AcpMcpConnection:
    """Represents a single active MCP-over-ACP connection."""

    connection_id: str
    """Unique identifier for this connection instance."""

    acp_id: str
    """The ACP server ID that this connection is bound to."""

    server: AcpMcpServer
    """The ACP MCP server configuration."""

    _send_to_client: Any = field(repr=False)
    """Async callable to send MCP messages to the ACP client."""

    _is_open: bool = field(default=False, repr=False)
    """Whether the connection streams have been opened."""

    _from_session_send: anyio.streams.memory.MemoryObjectSendStream | None = field(
        default=None, repr=False
    )
    """Stream to send messages from the MCP session side."""

    _from_session_receive: anyio.streams.memory.MemoryObjectReceiveStream | None = field(
        default=None, repr=False
    )
    """Stream to receive messages from the MCP session side."""

    _to_session_send: anyio.streams.memory.MemoryObjectSendStream | None = field(
        default=None, repr=False
    )
    """Stream to send messages to the MCP session side (from client)."""

    _to_session_receive: anyio.streams.memory.MemoryObjectReceiveStream | None = field(
        default=None, repr=False
    )
    """Stream to receive messages to the MCP session side (from client)."""

    async def open(self) -> None:
        """Open the connection by creating memory object streams."""
        self._from_session_send, self._from_session_receive = anyio.create_memory_object_stream(0)
        self._to_session_send, self._to_session_receive = anyio.create_memory_object_stream(0)
        self._is_open = True
        logger.debug("Opened ACP MCP connection streams", connection_id=self.connection_id)

    async def close(self) -> None:
        """Close the connection streams."""
        if self._from_session_send is not None:
            await self._from_session_send.aclose()
        if self._from_session_receive is not None:
            await self._from_session_receive.aclose()
        if self._to_session_send is not None:
            await self._to_session_send.aclose()
        if self._to_session_receive is not None:
            await self._to_session_receive.aclose()
        self._is_open = False
        logger.debug("Closed ACP MCP connection streams", connection_id=self.connection_id)

    @property
    def from_session_receive(self) -> anyio.streams.memory.MemoryObjectReceiveStream:
        """Return the receive stream for messages from the MCP session side."""
        if self._from_session_receive is None:
            raise RuntimeError("Connection not opened")
        return self._from_session_receive

    @property
    def to_session_send(self) -> anyio.streams.memory.MemoryObjectSendStream:
        """Return the send stream for messages to the MCP session side."""
        if self._to_session_send is None:
            raise RuntimeError("Connection not opened")
        return self._to_session_send

    async def handle_client_message(self, message: dict[str, Any]) -> None:
        """Handle an incoming MCP message from the client.

        Routes the message to the appropriate internal handler.
        """
        logger.debug(
            "Handling client message",
            connection_id=self.connection_id,
            message_method=message.get("method"),
        )


@dataclass
class AcpMcpConnectionManager:
    """Manages MCP-over-ACP connection lifecycle.

    Owned by AgentPoolACPAgent (per-ACP-connection), NOT per-session.
    """

    _by_acp_id: dict[str, list[str]] = field(default_factory=dict)
    """acpId -> connectionIds mapping."""

    _connections: dict[str, AcpMcpConnection] = field(default_factory=dict)
    """connectionId -> connection mapping."""

    def get_connection(self, connection_id: str) -> AcpMcpConnection | None:
        """Get an active connection by its ID."""
        return self._connections.get(connection_id)

    async def create_connection(
        self,
        connection_id: str,
        server: AcpMcpServer,
        send_to_client: Any,
    ) -> AcpMcpConnection:
        """Create a new MCP-over-ACP connection.

        Args:
            connection_id: Unique identifier for the new connection.
            server: The ACP MCP server configuration.
            send_to_client: Async callable to forward messages to the client.

        Returns:
            The newly created connection.
        """
        conn = AcpMcpConnection(
            connection_id=connection_id,
            acp_id=server.id,
            server=server,
            _send_to_client=send_to_client,
        )
        self._connections[connection_id] = conn

        if server.id not in self._by_acp_id:
            self._by_acp_id[server.id] = []
        self._by_acp_id[server.id].append(connection_id)

        logger.info(
            "Created ACP MCP connection",
            connection_id=connection_id,
            acp_id=server.id,
        )
        return conn

    async def remove_connection(self, connection_id: str) -> None:
        """Remove and clean up a single connection.

        Args:
            connection_id: The connection ID to remove.
        """
        conn = self._connections.pop(connection_id, None)
        if conn is None:
            logger.warning("Connection not found for removal", connection_id=connection_id)
            return

        # Remove from acp_id index
        acp_id = conn.acp_id
        if acp_id in self._by_acp_id:
            self._by_acp_id[acp_id] = [
                cid for cid in self._by_acp_id[acp_id] if cid != connection_id
            ]
            if not self._by_acp_id[acp_id]:
                del self._by_acp_id[acp_id]

        logger.info("Removed ACP MCP connection", connection_id=connection_id, acp_id=acp_id)

    async def close_all(self) -> None:
        """Clean up all active connections."""
        connection_ids = list(self._connections.keys())
        for connection_id in connection_ids:
            await self.remove_connection(connection_id)

        logger.info("Closed all ACP MCP connections")
