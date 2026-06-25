"""Command for running agents as an MCP server."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import os
from typing import TYPE_CHECKING, Annotated, Any, Literal

import anyio
import typer as t

from agentpool_cli import log


# Duplicated from agentpool_config.pool_server to avoid heavy imports at CLI startup
TransportType = Literal["stdio", "sse", "streamable-http"]

if TYPE_CHECKING:
    from agentpool import ChatMessage


logger = log.get_logger(__name__)


def serve_command(
    config: Annotated[str, t.Argument(help="Path to agent configuration")],
    transport: Annotated[TransportType, t.Option(help="Transport type")] = "stdio",
    host: Annotated[
        str, t.Option(help="Host to bind server to (sse/streamable-http only)")
    ] = "localhost",
    port: Annotated[int, t.Option(help="Port to listen on (sse/streamable-http only)")] = 3001,
    zed_mode: Annotated[bool, t.Option(help="Enable Zed editor compatibility")] = False,
    show_messages: Annotated[
        bool, t.Option("--show-messages", help="Show message activity")
    ] = False,
) -> None:
    """Run agents as an MCP server.

    This makes agents available as tools to other applications, regardless of
    whether pool_server is configured in the manifest.
    """

    def on_message(message: ChatMessage[Any]) -> None:
        print(message.format(style="simple"))

    async def run_server() -> None:

        from agentpool import AgentPool, AgentsManifest
        from agentpool_config.context import ConfigContextManager
        from agentpool_config.pool_server import MCPPoolServerConfig
        from agentpool_server.mcp_server.server import MCPServer

        logger.info("Server PID", pid=os.getpid())
        # Load manifest and create pool (without server config)
        with ConfigContextManager(config):
            manifest = AgentsManifest.from_file(config)
        pool = AgentPool(manifest)
        # Create server config and server externally
        server_config = MCPPoolServerConfig(
            enabled=True,
            transport=transport,
            host=host,
            port=port,
            zed_mode=zed_mode,
        )
        server = MCPServer(pool, server_config)
        async with pool, server:
            _consumer_task: asyncio.Task[None] | None = None
            if show_messages:
                from agentpool.agents.events import StreamCompleteEvent

                _event_bus = pool.session_pool.event_bus if pool.session_pool is not None else None
                if _event_bus is not None:

                    async def _consume_stream_complete() -> None:
                        """Subscribe to EventBus and print completed messages."""
                        stream: Any = None
                        try:
                            stream = await _event_bus.subscribe("_mcp_messages", scope="all")
                            async with stream:
                                async for envelope in stream:
                                    if isinstance(envelope.event, StreamCompleteEvent):
                                        on_message(envelope.event.message)
                        except anyio.EndOfStream:
                            pass
                        except asyncio.CancelledError:
                            pass
                        finally:
                            if stream is not None:
                                with suppress(Exception):
                                    await _event_bus.unsubscribe("_mcp_messages", stream)

                    _consumer_task = asyncio.create_task(_consume_stream_complete())

            try:
                await server.start()  # Blocks until server stops
            except KeyboardInterrupt:
                logger.info("Server shutdown requested")
            finally:
                if _consumer_task is not None:
                    _consumer_task.cancel()
                    with suppress(BaseException):
                        await _consumer_task

    asyncio.run(run_server())
