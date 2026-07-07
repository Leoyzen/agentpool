"""Transport abstractions for ACP agents.

This module provides transport configuration classes and utilities for running
ACP agents over different transports (stdio, WebSocket, etc.).
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
import os
import subprocess
from typing import TYPE_CHECKING, Any, Literal, Protocol, assert_never
import uuid

import anyio
from anyio.abc import ByteReceiveStream, ByteSendStream


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
    from pathlib import Path

    from anyio.abc import Process
    from websockets.asyncio.server import ServerConnection

    from acp.agent.connection import AgentSideConnection
    from acp.agent.protocol import Agent

logger = logging.getLogger(__name__)

DEFAULT_WEBSOCKET_PING_INTERVAL = 60.0
DEFAULT_WEBSOCKET_PONG_TIMEOUT = 30.0
DEFAULT_WEBSOCKET_MAX_MISSED_PONGS = 3


# =============================================================================
# Transport Configuration Classes
# =============================================================================


@dataclass
class StdioTransport:
    """Configuration for stdio transport.

    This is the default transport for ACP agents, communicating over
    stdin/stdout streams.
    """


@dataclass
class WebSocketTransport:
    """Configuration for WebSocket server transport.

    Runs an ACP agent as a WebSocket server that accepts client connections.
    Each client connection gets its own agent instance.

    Attributes:
        host: Host to bind the WebSocket server to.
        port: Port for the WebSocket server.
        ping_interval: Seconds between server-initiated WebSocket ping frames.
            Set to None to disable AgentPool's heartbeat.
        pong_timeout: Seconds to wait for a pong response to each ping.
        max_missed_pongs: Consecutive missed pongs before closing the connection.
    """

    host: str = "localhost"
    port: int = 8765
    ping_interval: float | None = DEFAULT_WEBSOCKET_PING_INTERVAL
    pong_timeout: float = DEFAULT_WEBSOCKET_PONG_TIMEOUT
    max_missed_pongs: int = DEFAULT_WEBSOCKET_MAX_MISSED_PONGS

    def __post_init__(self) -> None:
        if self.ping_interval is not None and self.ping_interval <= 0:
            msg = "ping_interval must be positive or None"
            raise ValueError(msg)
        _validate_websocket_heartbeat(self.pong_timeout, self.max_missed_pongs)


class _HeartbeatWebSocket(Protocol):
    async def ping(self) -> Awaitable[float]: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


def _validate_websocket_heartbeat(pong_timeout: float, max_missed_pongs: int) -> None:
    if pong_timeout <= 0:
        msg = "pong_timeout must be positive"
        raise ValueError(msg)
    if max_missed_pongs <= 0:
        msg = "max_missed_pongs must be positive"
        raise ValueError(msg)


def _effective_websocket_pong_timeout(
    ping_interval: float | None,
    pong_timeout: float,
    max_missed_pongs: int,
) -> float | None:
    """Return a single keepalive timeout matching the multi-miss tolerance window."""
    if ping_interval is None:
        return None
    return pong_timeout * max_missed_pongs + ping_interval * (max_missed_pongs - 1)


@dataclass
class StreamTransport:
    """Configuration for custom stream transport.

    Allows passing pre-created streams for the agent to use.

    Attributes:
        reader: Stream to read incoming messages from.
        writer: Stream to write outgoing messages to.
    """

    reader: ByteReceiveStream
    writer: ByteSendStream


@dataclass
class ACPWebSocketTransport:
    """Configuration for ACP streamable HTTP WebSocket transport.

    Runs an ACP agent as a Starlette-based WebSocket server at /acp
    that accepts client connections. Each client connection gets its
    own agent instance with a unique Acp-Connection-Id header.

    Attributes:
        host: Host to bind the WebSocket server to.
        port: Port for the WebSocket server.
        ping_interval: Seconds between server-initiated WebSocket ping frames.
            Set to None to disable server-side heartbeat.
        pong_timeout: Seconds to wait for each expected pong.
        max_missed_pongs: Consecutive missed pongs to tolerate before disconnecting.
    """

    host: str = "localhost"
    port: int = 8080
    ping_interval: float | None = DEFAULT_WEBSOCKET_PING_INTERVAL
    pong_timeout: float = DEFAULT_WEBSOCKET_PONG_TIMEOUT
    max_missed_pongs: int = DEFAULT_WEBSOCKET_MAX_MISSED_PONGS

    def __post_init__(self) -> None:
        if self.ping_interval is not None and self.ping_interval <= 0:
            msg = "ping_interval must be positive or None"
            raise ValueError(msg)
        _validate_websocket_heartbeat(self.pong_timeout, self.max_missed_pongs)


# Type alias for all supported transports
Transport = (
    StdioTransport
    | WebSocketTransport
    | StreamTransport
    | ACPWebSocketTransport
    | Literal["stdio", "streamable-http"]
)


# =============================================================================
# Transport Runner
# =============================================================================


async def serve(
    agent: Agent | Callable[[AgentSideConnection], Agent],
    transport: Transport = "stdio",
    *,
    shutdown_event: asyncio.Event | None = None,
    debug_file: str | None = None,
    **kwargs: Any,
) -> None:
    """Run an ACP agent with the specified transport.

    This is the main entry point for running ACP agents. It handles transport
    setup and lifecycle management automatically.

    Args:
        agent: An Agent implementation or a factory callable that takes
            an AgentSideConnection and returns an Agent.
        transport: Transport configuration. Can be:
            - "stdio" or StdioTransport(): Use stdin/stdout
                       - StreamTransport(...): Use custom streams
        shutdown_event: Optional event to signal shutdown. If not provided,
            runs until cancelled.
        debug_file: Optional file path for debug message logging.
        **kwargs: Additional keyword arguments passed to AgentSideConnection.

    Example:
        ```python
        # Stdio (default)
        await serve(MyAgent())

        # WebSocket server
        await serve(MyAgent(), WebSocketTransport(host="0.0.0.0", port=9000))

        # With shutdown control
        shutdown = asyncio.Event()
        task = asyncio.create_task(serve(MyAgent(), shutdown_event=shutdown))
        # ... later ...
        shutdown.set()
        await task
        ```
    """
    # Normalize string shortcuts to config objects
    match transport:
        case "stdio":
            transport = StdioTransport()

        case "streamable-http":
            transport = ACPWebSocketTransport()

    # Dispatch to appropriate runner
    match transport:
        case StdioTransport():
            await _serve_stdio(agent, shutdown_event, debug_file, **kwargs)
        case WebSocketTransport(
            host=host,
            port=port,
            ping_interval=ping_interval,
            pong_timeout=pong_timeout,
            max_missed_pongs=max_missed_pongs,
        ):
            await _serve_websocket(
                agent,
                host,
                port,
                shutdown_event,
                debug_file,
                ping_interval=ping_interval,
                pong_timeout=pong_timeout,
                max_missed_pongs=max_missed_pongs,
                **kwargs,
            )
        case ACPWebSocketTransport(
            host=host,
            port=port,
            ping_interval=ping_interval,
            pong_timeout=pong_timeout,
            max_missed_pongs=max_missed_pongs,
        ):
            await _serve_streamable_http(
                agent,
                host,
                port,
                shutdown_event,
                debug_file,
                ping_interval=ping_interval,
                pong_timeout=pong_timeout,
                max_missed_pongs=max_missed_pongs,
                **kwargs,
            )
        case StreamTransport(reader=reader, writer=writer):
            await _serve_streams(agent, reader, writer, shutdown_event, debug_file, **kwargs)
        case _ as unreachable:
            assert_never(unreachable)


async def _serve_stdio(
    agent: Agent | Callable[[AgentSideConnection], Agent],
    shutdown_event: asyncio.Event | None,
    debug_file: str | None,
    **kwargs: Any,
) -> None:
    """Run agent over stdio."""
    from acp.agent.connection import AgentSideConnection
    from acp.stdio import stdio_streams

    agent_factory = _ensure_factory(agent)
    reader, writer = await stdio_streams()

    conn = AgentSideConnection(agent_factory, writer, reader, debug_file=debug_file, **kwargs)
    try:
        if shutdown_event:
            await shutdown_event.wait()
        else:
            await asyncio.Event().wait()  # Wait forever
    except asyncio.CancelledError:
        pass
    finally:
        await conn.close()


async def _serve_streams(
    agent: Agent | Callable[[AgentSideConnection], Agent],
    reader: ByteReceiveStream,
    writer: ByteSendStream,
    shutdown_event: asyncio.Event | None,
    debug_file: str | None,
    **kwargs: Any,
) -> None:
    """Run agent over custom streams."""
    from acp.agent.connection import AgentSideConnection

    agent_factory = _ensure_factory(agent)
    conn = AgentSideConnection(agent_factory, writer, reader, debug_file=debug_file, **kwargs)
    try:
        if shutdown_event:
            await shutdown_event.wait()
        else:
            await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        await conn.close()


async def _serve_websocket(
    agent: Agent | Callable[[AgentSideConnection], Agent],
    host: str,
    port: int,
    shutdown_event: asyncio.Event | None,
    debug_file: str | None,
    *,
    ping_interval: float | None = DEFAULT_WEBSOCKET_PING_INTERVAL,
    pong_timeout: float = DEFAULT_WEBSOCKET_PONG_TIMEOUT,
    max_missed_pongs: int = DEFAULT_WEBSOCKET_MAX_MISSED_PONGS,
    **kwargs: Any,
) -> None:
    """Run agent as WebSocket server."""
    import websockets

    agent_factory = _ensure_factory(agent)
    shutdown = shutdown_event or asyncio.Event()
    connections: list[AgentSideConnection] = []

    async def handle_client(websocket: ServerConnection) -> None:
        """Handle a single WebSocket client connection."""
        await _handle_websocket_client(
            websocket,
            agent_factory,
            shutdown,
            connections,
            debug_file,
            ping_interval,
            pong_timeout,
            max_missed_pongs,
            kwargs,
        )

    logger.info("Starting WebSocket server on ws://%s:%d", host, port)
    async with websockets.serve(handle_client, host, port, ping_interval=None):
        logger.info("WebSocket server running on ws://%s:%d", host, port)
        await shutdown.wait()

    # Clean up remaining connections
    for conn in connections:
        await conn.close()


async def _handle_websocket_client(
    websocket: ServerConnection,
    agent_factory: Callable[[AgentSideConnection], Agent],
    shutdown: asyncio.Event,
    connections: list[AgentSideConnection],
    debug_file: str | None,
    ping_interval: float | None,
    pong_timeout: float,
    max_missed_pongs: int,
    kwargs: dict[str, Any],
    on_disconnect: Callable[[AgentSideConnection], Awaitable[None]] | None = None,
) -> None:
    """Handle a single WebSocket client connection lifecycle.

    Args:
        websocket: The WebSocket server connection for this client.
        agent_factory: Factory that creates an Agent from an AgentSideConnection.
        shutdown: Event signalling server-wide shutdown.
        connections: List tracking all active connections for cleanup.
        debug_file: Optional path for debug message logging.
        ping_interval: Seconds between heartbeat pings, or None to disable.
        pong_timeout: Seconds to wait for each pong response.
        max_missed_pongs: Consecutive missed pongs before closing.
        kwargs: Additional keyword arguments passed to AgentSideConnection.
        on_disconnect: Optional callback invoked when the client disconnects
            unexpectedly (ConnectionClosed). Receives the AgentSideConnection
            so the caller can read ``connection_id`` and perform session cleanup.
            Called BEFORE ``conn.close()`` in the finally block.
    """
    import websockets

    from acp.agent.connection import AgentSideConnection

    logger.info("WebSocket client connected")

    ws_reader = _WebSocketReadStream(websocket)
    ws_writer = _WebSocketWriteStream(websocket)

    conn = AgentSideConnection(agent_factory, ws_writer, ws_reader, debug_file=debug_file, **kwargs)
    conn.connection_id = uuid.uuid4().hex  # type: ignore[attr-defined]
    connections.append(conn)

    heartbeat_task: asyncio.Task[None] | None = None
    if ping_interval is not None:
        logger.info(
            "Starting WebSocket heartbeat with interval=%s, timeout=%s, max_missed=%s",
            ping_interval,
            pong_timeout,
            max_missed_pongs,
        )
        heartbeat_task = asyncio.create_task(
            _websocket_heartbeat(
                websocket,
                ping_interval=ping_interval,
                pong_timeout=pong_timeout,
                max_missed_pongs=max_missed_pongs,
            )
        )

    try:
        _recv_conn = getattr(conn, "_conn", None)
        recv_task = getattr(_recv_conn, "_recv_task", None) if _recv_conn else None
        waitables: list[asyncio.Future[Any]] = [asyncio.create_task(shutdown.wait())]
        if isinstance(recv_task, asyncio.Task):
            waitables.append(recv_task)
        if heartbeat_task is not None:
            waitables.append(heartbeat_task)

        done, _ = await asyncio.wait(waitables, return_when=asyncio.FIRST_COMPLETED)

        for w in waitables:
            if isinstance(w, asyncio.Task) and w not in done:
                w.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await w
    except websockets.exceptions.ConnectionClosed:
        logger.info("WebSocket client disconnected")
        if on_disconnect is not None:
            await on_disconnect(conn)
    finally:
        if heartbeat_task is not None and not heartbeat_task.done():
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Unexpected error during heartbeat task cleanup")
        with contextlib.suppress(ValueError):
            connections.remove(conn)
        try:
            await conn.close()
        except Exception:
            logger.exception("Unexpected error closing WebSocket connection")


async def _websocket_heartbeat(
    websocket: _HeartbeatWebSocket,
    *,
    ping_interval: float,
    pong_timeout: float,
    max_missed_pongs: int,
) -> None:
    """Close a WebSocket only after several consecutive missed pong responses."""
    import websockets

    missed_pongs = 0
    ping_count = 0
    logger.info("WebSocket heartbeat started")
    while True:
        await asyncio.sleep(ping_interval)
        ping_count += 1
        logger.debug("Sending WebSocket ping #%d", ping_count)
        try:
            pong_waiter: Awaitable[float] = await websocket.ping()
            await asyncio.wait_for(pong_waiter, timeout=pong_timeout)
            logger.debug("Pong #%d received in time", ping_count)
            if missed_pongs:
                logger.info(
                    "WebSocket heartbeat recovered after %d missed pong(s)",
                    missed_pongs,
                )
                missed_pongs = 0
        except TimeoutError:
            missed_pongs += 1
            logger.warning(
                "WebSocket heartbeat missed pong %d/%d",
                missed_pongs,
                max_missed_pongs,
            )
            if missed_pongs >= max_missed_pongs:
                logger.warning(
                    "Closing WebSocket after %d consecutive missed pong(s)",
                    missed_pongs,
                )
                await websocket.close(code=1011, reason="pong timeout")
                return
        except websockets.exceptions.ConnectionClosed:
            return
        except Exception:
            logger.exception("WebSocket heartbeat failed")
            with contextlib.suppress(Exception):
                await websocket.close(code=1011, reason="heartbeat failed")
            return


async def _serve_streamable_http(
    agent: Agent | Callable[[AgentSideConnection], Agent],
    host: str,
    port: int,
    shutdown_event: asyncio.Event | None,
    debug_file: str | None,
    *,
    ping_interval: float | None = DEFAULT_WEBSOCKET_PING_INTERVAL,
    pong_timeout: float = DEFAULT_WEBSOCKET_PONG_TIMEOUT,
    max_missed_pongs: int = DEFAULT_WEBSOCKET_MAX_MISSED_PONGS,
    **kwargs: Any,
) -> None:
    """Run agent as a streamable HTTP WebSocket server (Starlette-based)."""
    from starlette.applications import Starlette
    from starlette.routing import WebSocketRoute
    import uvicorn

    from acp.agent.connection import AgentSideConnection

    agent_factory = _ensure_factory(agent)
    shutdown = shutdown_event or asyncio.Event()
    active_connections: set[AgentSideConnection] = set()

    async def handle_acp(websocket: Any) -> None:
        """Handle a single ACP WebSocket client connection."""
        connection_id = uuid.uuid4().hex
        await websocket.accept(
            subprotocol=None,
            headers=[(b"Acp-Connection-Id", connection_id.encode())],
        )
        logger.info("ACP WebSocket client connected (id=%s)", connection_id)

        # Create stream adapters for WebSocket
        ws_reader = _StarletteWebSocketReadStream(websocket)
        ws_writer = _StarletteWebSocketWriteStream(websocket)

        conn = AgentSideConnection(
            agent_factory, ws_writer, ws_reader, debug_file=debug_file, **kwargs
        )
        active_connections.add(conn)

        try:
            # Wait for shutdown or for the receive loop to end (client disconnect)
            _recv_conn = getattr(conn, "_conn", None)
            recv_task = getattr(_recv_conn, "_recv_task", None) if _recv_conn else None
            waitables: list[asyncio.Future[Any]] = [asyncio.create_task(shutdown.wait())]
            if isinstance(recv_task, asyncio.Task):
                waitables.append(recv_task)
            done, _pending = await asyncio.wait(waitables, return_when=asyncio.FIRST_COMPLETED)
            # Cancel any remaining tasks
            for w in waitables:
                if isinstance(w, asyncio.Task) and w not in done:
                    w.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await w
        finally:
            active_connections.discard(conn)
            await conn.close()

    app = Starlette(routes=[WebSocketRoute("/acp", handle_acp)])
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        ws_ping_interval=ping_interval,
        ws_ping_timeout=_effective_websocket_pong_timeout(
            ping_interval,
            pong_timeout,
            max_missed_pongs,
        ),
    )
    server = uvicorn.Server(config)

    async def shutdown_watcher() -> None:
        await shutdown.wait()
        server.should_exit = True

    watcher_task = asyncio.create_task(shutdown_watcher())

    logger.info("Starting streamable HTTP server on http://%s:%d", host, port)
    try:
        await server.serve()
    finally:
        watcher_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher_task
        # Clean up remaining connections
        for conn in list(active_connections):
            await conn.close()


class _StarletteWebSocketReadStream(ByteReceiveStream):
    """Adapter to read from Starlette WebSocket as a ByteReceiveStream."""

    def __init__(self, websocket: Any) -> None:
        self._websocket = websocket
        self._buffer = b""

    async def receive(self, max_bytes: int = 65536) -> bytes:
        if self._buffer:
            data = self._buffer[:max_bytes]
            self._buffer = self._buffer[max_bytes:]
            return data

        try:
            message: str = await self._websocket.receive_text()
        except Exception as e:
            raise anyio.EndOfStream from e

        data = message.encode()
        # Append trailing newline for JSON-RPC line protocol compatibility
        if not data.endswith(b"\n"):
            data += b"\n"

        if len(data) > max_bytes:
            self._buffer = data[max_bytes:]
            return data[:max_bytes]

        return data

    async def aclose(self) -> None:
        pass


class _StarletteWebSocketWriteStream(ByteSendStream):
    """Adapter to write to Starlette WebSocket as a ByteSendStream."""

    def __init__(self, websocket: Any) -> None:
        self._websocket = websocket

    async def send(self, item: bytes) -> None:
        # Strip trailing newline, send complete text message via send_text()
        message = item.rstrip(b"\n").decode()
        if message:
            await self._websocket.send_text(message)

    async def aclose(self) -> None:
        pass


class _WebSocketReadStream(ByteReceiveStream):
    """Adapter to read from WebSocket as a ByteReceiveStream."""

    def __init__(self, websocket: Any) -> None:
        self._websocket = websocket
        self._buffer = b""

    async def receive(self, max_bytes: int = 65536) -> bytes:
        # If we have buffered data, return it
        if self._buffer:
            data = self._buffer[:max_bytes]
            self._buffer = self._buffer[max_bytes:]
            return data

        # Read from WebSocket
        try:
            message = await self._websocket.recv()
            if isinstance(message, str):
                message = message.encode()
            # Add newline for JSON-RPC line protocol
            if not message.endswith(b"\n"):
                message += b"\n"
            self._buffer = message[max_bytes:]
            return message[:max_bytes]  # type: ignore[no-any-return]
        except Exception as e:
            raise anyio.EndOfStream from e

    async def aclose(self) -> None:
        pass


class _WebSocketWriteStream(ByteSendStream):
    """Adapter to write to WebSocket as a ByteSendStream."""

    def __init__(self, websocket: Any) -> None:
        self._websocket = websocket

    async def send(self, item: bytes) -> None:
        # WebSocket sends complete messages, strip newline if present
        message = item.decode().strip()
        if message:
            await self._websocket.send(message)

    async def aclose(self) -> None:
        pass


def _ensure_factory(
    agent: Agent | Callable[[AgentSideConnection], Agent],
) -> Callable[[AgentSideConnection], Agent]:
    """Ensure agent is wrapped in a factory function."""
    if callable(agent) and not hasattr(agent, "initialize"):
        return agent  # Already a factory

    # Wrap instance in factory
    def factory(connection: AgentSideConnection) -> Agent:
        return agent  # type: ignore[return-value]

    return factory


# =============================================================================
# Subprocess Transport Utilities (for spawning agents)
# =============================================================================


DEFAULT_INHERITED_ENV_VARS = (
    [
        "APPDATA",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "USERNAME",
        "USERPROFILE",
    ]
    if os.name == "nt"
    else ["HOME", "LOGNAME", "PATH", "SHELL", "TERM", "USER"]
)


def default_environment() -> dict[str, str]:
    """Return a trimmed environment based on MCP best practices."""
    env: dict[str, str] = {}
    for key in DEFAULT_INHERITED_ENV_VARS:
        value = os.environ.get(key)
        if value is None:
            continue
        # Skip function-style env vars on some shells (see MCP reference)
        if value.startswith("()"):
            continue
        env[key] = value
    return env


async def _drain_stderr_to_log(
    process: Process,
    command: str,
    log_level: int = logging.WARNING,
) -> None:
    """Read stderr from a process and log each line.

    Args:
        process: The subprocess to read stderr from.
        command: Command name for log messages.
        log_level: Log level for stderr output (default WARNING).
    """
    if process.stderr is None:
        return

    try:
        async for line_bytes in process.stderr:
            line = line_bytes.decode(errors="replace").rstrip()
            if line:
                logger.log(log_level, "[%s stderr] %s", command, line)
    except anyio.EndOfStream:
        pass
    except Exception:  # noqa: BLE001
        logger.debug("Error reading stderr from %s", command, exc_info=True)


@asynccontextmanager
async def spawn_stdio_transport(
    command: str,
    *args: str,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
    stderr: int | None = subprocess.PIPE,
    shutdown_timeout: float = 2.0,
    log_stderr: bool = False,
    stderr_log_level: int = logging.WARNING,
) -> AsyncIterator[tuple[ByteReceiveStream, ByteSendStream, Process]]:
    """Launch a subprocess and expose its stdio streams as anyio streams.

    This mirrors the defensive shutdown behaviour used by the MCP Python SDK:
    close stdin first, wait for graceful exit, then escalate to terminate/kill.

    Args:
        command: The command to execute.
        *args: Arguments for the command.
        env: Environment variables (merged with defaults).
        cwd: Working directory for the subprocess.
        stderr: How to handle stderr (default: subprocess.PIPE).
        shutdown_timeout: Timeout for graceful shutdown.
        log_stderr: If True, read stderr in background and log each line.
        stderr_log_level: Log level for stderr output (default WARNING).
    """
    merged_env = default_environment()
    if env:
        merged_env.update(env)

    process = await anyio.open_process(
        [command, *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=stderr,
        env=merged_env,
        cwd=str(cwd) if cwd is not None else None,
    )

    if process.stdout is None or process.stdin is None:
        process.kill()
        await process.wait()
        raise RuntimeError("spawn_stdio_transport requires stdout/stdin pipes")

    stderr_task: asyncio.Task[None] | None = None
    if log_stderr and process.stderr is not None:
        stderr_task = asyncio.create_task(_drain_stderr_to_log(process, command, stderr_log_level))

    try:
        yield process.stdout, process.stdin, process
    finally:
        # Cancel stderr logging task
        if stderr_task is not None:
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task

        # Attempt graceful stdin shutdown first
        if process.stdin is not None:
            with contextlib.suppress(Exception):
                await process.stdin.aclose()

        try:
            with anyio.fail_after(shutdown_timeout):
                await process.wait()
        except TimeoutError:
            process.terminate()
            try:
                with anyio.fail_after(shutdown_timeout):
                    await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()
