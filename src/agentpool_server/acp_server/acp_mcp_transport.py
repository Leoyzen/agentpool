"""fastmcp ClientTransport implementation for MCP-over-ACP.

Bridges fastmcp's ClientSession to the ACP connection manager.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any

import anyio
from mcp import ClientSession
from fastmcp.client.transports import ClientTransport

from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool_server.acp_server.acp_mcp_manager import AcpMcpConnection

logger = get_logger(__name__)


class AcpMcpTransport(ClientTransport):
    """fastmcp ClientTransport that tunnels MCP over ACP.

    This transport is used by fastmcp's Client to communicate
    with an MCP server over the existing ACP connection.
    """

    def __init__(self, connection: AcpMcpConnection) -> None:
        """Initialize the transport with an active ACP MCP connection.

        Args:
            connection: An active AcpMcpConnection with open streams.
        """
        self._connection = connection

    @property
    def connection_id(self) -> str:
        """Return the ACP connection ID managed by this transport."""
        return self._connection.connection_id

    @asynccontextmanager
    async def connect_session(
        self,
        **session_kwargs: Any,
    ) -> AsyncIterator[ClientSession]:
        """Create a fastmcp ClientSession over ACP.

        Uses the memory streams from the AcpMcpConnection to bridge
        MCP JSON-RPC messages to/from the ACP client.

        Args:
            **session_kwargs: Additional arguments passed to ClientSession.

        Yields:
            A connected fastmcp ClientSession.
        """

        # Create a task that reads from from_session stream and sends to client
        # This bridges MCP session -> ACP client
        async def _forward_to_client() -> None:
            try:
                async for message in self._connection.from_session_receive:
                    await self._connection.send_to_client(message)
            except anyio.EndOfStream:
                pass
            except Exception:
                logger.exception("Error in MCP-over-ACP forwarder task")
                await self._connection.close()
                raise

        session = ClientSession(
            self._connection.to_session,  # type: ignore[arg-type]
            self._connection.from_session,  # type: ignore[arg-type]
            **session_kwargs,
        )

        forwarder = asyncio.create_task(_forward_to_client())
        try:
            # Note: session.initialize() is NOT called here.
            # When this transport is used via fastmcp.Client (MCPClient),
            # the Client will call initialize() as part of its own connection
            # lifecycle. Calling it here would cause a double-initialize.
            yield session
        finally:
            forwarder.cancel()
            try:
                with suppress(asyncio.CancelledError):
                    await forwarder
            except Exception:
                logger.exception("Error in MCP-over-ACP forwarder task")
            self._forwarder_task = None
