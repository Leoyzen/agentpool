"""AcpMcpTransport: fastmcp ClientTransport for MCP-over-ACP.

Implements the async stream protocol by bridging MCP JSON-RPC messages
between fastmcp ClientSession and AcpMcpConnection streams.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import anyio
from fastmcp.client.transports import ClientTransport
from mcp import ClientSession
from typing_extensions import Unpack

if TYPE_CHECKING:
    from agentpool_server.acp_server.acp_mcp_manager import AcpMcpConnection


class AcpMcpTransport(ClientTransport):
    """fastmcp ClientTransport that routes MCP messages over ACP channel.

    Implements the async stream protocol by wrapping ACP mcp/message
    JSON-RPC requests. Maintains independent MCP JSON-RPC id space.
    """

    def __init__(self, connection: AcpMcpConnection) -> None:
        self._connection = connection
        self._forwarder_task: asyncio.Task | None = None

    @contextlib.asynccontextmanager
    async def connect_session(
        self, **session_kwargs: Unpack[Any]
    ) -> AsyncIterator[ClientSession]:
        """Establish a ClientSession backed by ACP channel streams.

        Creates async memory streams and starts a forwarder task that
        bridges messages from the connection's from_session_receive
        stream to the ACP client via send_to_client.

        Raises:
            RuntimeError: If the connection has not been opened.
        """
        if not self._connection._is_open:
            raise RuntimeError("Connection not opened")

        # Create streams for ClientSession
        read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
        write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

        # Start forwarder task: reads from connection's from_session_receive
        # and forwards to the ACP client via send_to_client
        self._forwarder_task = asyncio.create_task(self._forward_messages())

        # Start drainer task to prevent ClientSession from blocking on write
        drainer_task = asyncio.create_task(self._drain_write_stream(write_stream_reader))

        try:
            session = ClientSession(read_stream, write_stream, **session_kwargs)
            yield session
        finally:
            for task in (self._forwarder_task, drainer_task):
                if task and not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            self._forwarder_task = None
            await read_stream.aclose()
            await write_stream.aclose()

    async def _forward_messages(self) -> None:
        """Forward messages from the connection to the ACP client.

        Reads from connection.from_session_receive and calls
        connection._send_to_client for each message.
        """
        async for message in self._connection.from_session_receive:
            await self._connection._send_to_client(message)

    async def _drain_write_stream(
        self,
        reader: Any,
    ) -> None:
        """Drain the ClientSession write stream to prevent blocking."""
        async for _msg in reader:
            pass
