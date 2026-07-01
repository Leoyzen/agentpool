"""Global connection pool for sharing MCP connections across sessions.

For HTTP/SSE servers: creates a fresh transport per ``get_transport()``
call. HTTP connections are cheap and stateless — no need to share.

For stdio servers: uses the owner-task pattern to keep the subprocess
alive. ``get_transport()`` returns a ``_SharedSessionTransport`` wrapper
whose ``connect_session()`` yields a shared ``ClientSession`` managed
by the owner task. This avoids multiple ``connect_session()`` calls
on the underlying transport.
"""  # allow: SIZE_OK — single cohesive class with tightly coupled private state

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator
import contextlib
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import logging
import threading
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from fastmcp.client.transports import ClientTransport

    from agentpool_config.mcp_server import BaseMCPServerConfig


logger = logging.getLogger(__name__)


class _SharedSessionTransport:
    """Transport wrapper that yields a shared ClientSession.

    Used for stdio connections where the owner task manages the
    underlying ``connect_session()`` context. Each call to this
    wrapper's ``connect_session()`` yields the same shared session
    without calling the underlying transport again.

    Uses reference counting: the session stays alive until all
    callers exit their ``connect_session()`` context.
    """

    def __init__(self, session: Any, ready_event: asyncio.Event) -> None:
        self._session = session
        self._ready_event = ready_event
        self._ref_count = 0
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def connect_session(self, **kwargs: Any) -> AsyncIterator[Any]:
        """Yield the shared session, incrementing the ref count."""
        await self._ready_event.wait()
        async with self._lock:
            self._ref_count += 1
        try:
            yield self._session
        finally:
            async with self._lock:
                self._ref_count -= 1


@dataclass
class _PooledConnection:
    """Internal state for a pooled MCP connection.

    For stdio connections, the owner_task manages the transport lifecycle
    and a _SharedSessionTransport wrapper is returned to callers.
    For HTTP/SSE connections, no pooling — fresh transport per call.
    """

    transport: ClientTransport
    owner_task: asyncio.Task[None] | None = None
    ref_count: int = 0
    ready_event: asyncio.Event = field(default_factory=asyncio.Event)
    close_event: asyncio.Event = field(default_factory=asyncio.Event)
    done_event: asyncio.Event = field(default_factory=asyncio.Event)
    is_stdio: bool = False
    shared_session_transport: _SharedSessionTransport | None = None


class GlobalConnectionPool:
    """Pool-level singleton for sharing MCP connections across sessions.

    Manages connections for pool-level and agent-level MCP servers.
    Assumes servers are stateless (safe to share across sessions/users).

    Uses the owner-task pattern (deer-flow) for stdio servers:
    - A dedicated asyncio.Task enters and exits the transport context manager.
    - Callers signal via Events; the owner task handles lifecycle.
    - This eliminates cross-task CancelScope errors.

    HTTP/SSE servers use direct transports (no cancel scope issues).
    """

    MAX_SESSIONS: int = 256

    def __init__(self) -> None:
        self._connections: OrderedDict[str, _PooledConnection] = OrderedDict()
        self._lock = threading.Lock()

    async def get_transport(
        self,
        config: BaseMCPServerConfig,
    ) -> ClientTransport:
        """Get or create a shared transport for the given config.

        For stdio: uses owner-task pattern (dedicated asyncio.Task).
        For HTTP/SSE: creates direct transport (no pooling overhead,
                     but still cached by client_id for reuse).
        For ACP: raises NotImplementedError (handled by SessionConnectionPool).

        Args:
            config: MCP server configuration.

        Returns:
            A ClientTransport that can be used to construct MCPToolset.

        Raises:
            NotImplementedError: For ACP transport servers.
            RuntimeError: If the owner task fails to initialize the connection.
            TimeoutError: If the owner task does not become ready in time.
        """
        client_id = config.client_id

        transport: ClientTransport
        is_stdio: bool
        ready_event: asyncio.Event | None
        owner_task: asyncio.Task[None] | None

        with self._lock:
            existing = self._connections.get(client_id)
            if existing is not None and existing.is_stdio:
                # Reuse stdio connection (managed by owner task)
                existing.ref_count += 1
                self._connections.move_to_end(client_id)
                transport = existing.transport
                is_stdio = existing.is_stdio
                ready_event = existing.ready_event
                owner_task = existing.owner_task
            elif existing is not None and not existing.is_stdio:
                # HTTP/SSE: don't reuse — create fresh transport per call
                self._connections.move_to_end(client_id)
                transport = config.to_transport()
                is_stdio = False
                ready_event = None
                owner_task = None
            else:
                self._evict_if_needed()
                transport, is_stdio, ready_event, owner_task = self._create_connection_locked(
                    config, client_id
                )

        if is_stdio and owner_task is not None and ready_event is not None:
            try:
                await asyncio.shield(asyncio.wait_for(ready_event.wait(), timeout=30.0))
            except TimeoutError:
                msg = f"Owner task for {client_id} did not become ready in 30s"
                logger.error(msg)  # noqa: TRY400
                raise TimeoutError(msg) from None

            if owner_task.done() and owner_task.exception() is not None:
                exc = owner_task.exception()
                msg = f"Owner task for {client_id} failed: {exc}"
                logger.error(msg)
                raise RuntimeError(msg) from exc

            # Return the shared session transport wrapper, not the raw
            # transport. This prevents callers from calling
            # connect_session() on the underlying transport again.
            existing = self._connections.get(client_id)
            if existing is not None and existing.shared_session_transport is not None:
                return existing.shared_session_transport

        return transport

    def _create_connection_locked(
        self,
        config: BaseMCPServerConfig,
        client_id: str,
    ) -> tuple[ClientTransport, bool, asyncio.Event | None, asyncio.Task[None] | None]:
        """Create a new pooled connection while holding the lock.

        Must be called with self._lock held.

        Args:
            config: MCP server configuration.
            client_id: Cache key for the connection.

        Returns:
            Tuple of (transport, is_stdio, ready_event, owner_task).
            For HTTP/SSE: ready_event and owner_task are None.
            For stdio: all four values are populated.

        Raises:
            NotImplementedError: For ACP transport servers.
        """
        from agentpool_config.mcp_server import AcpMCPServerConfig

        if isinstance(config, AcpMCPServerConfig):
            raise NotImplementedError(
                "ACP transport is handled by SessionConnectionPool, not GlobalConnectionPool"
            )

        transport = config.to_transport()

        from agentpool_config.mcp_server import StdioMCPServerConfig

        is_stdio = isinstance(config, StdioMCPServerConfig)

        if is_stdio:
            conn = _PooledConnection(
                transport=transport,
                ref_count=1,
                is_stdio=True,
            )
            conn.owner_task = asyncio.create_task(
                self._run_session(conn, client_id),
                name=f"mcp-owner-{client_id}",
            )
            self._connections[client_id] = conn
            return transport, True, conn.ready_event, conn.owner_task

        # HTTP/SSE: direct transport, no owner task needed
        conn = _PooledConnection(
            transport=transport,
            ref_count=1,
            is_stdio=False,
        )
        conn.ready_event.set()
        self._connections[client_id] = conn
        return transport, False, None, None

    def _evict_if_needed(self) -> None:
        """Evict least-recently-used stdio connections when at capacity.

        Must be called with self._lock held.
        Only evicts connections with ref_count == 0.
        """
        while len(self._connections) >= self.MAX_SESSIONS:
            evicted = False
            for cid, conn in list(self._connections.items()):
                if conn.ref_count == 0:
                    logger.info("Evicting idle MCP connection: %s", cid)
                    self._signal_shutdown_locked(cid, conn)
                    self._connections.pop(cid, None)
                    evicted = True
                    break
            if not evicted:
                logger.warning(
                    "MCP connection pool at capacity (%d) with no idle connections to evict",
                    self.MAX_SESSIONS,
                )
                break

    async def _run_session(
        self,
        conn: _PooledConnection,
        client_id: str,
    ) -> None:
        """Owner-task body for stdio connections.

        Enters the transport context manager, captures the session,
        creates a _SharedSessionTransport wrapper, signals readiness,
        waits for close signal, then exits.

        Args:
            conn: The pooled connection state.
            client_id: Cache key for logging.
        """
        logger.debug("Owner task starting for %s", client_id)
        try:
            async with conn.transport.connect_session() as session:
                conn.shared_session_transport = _SharedSessionTransport(
                    session=session,
                    ready_event=conn.ready_event,
                )
                conn.ready_event.set()
                logger.debug("Owner task ready for %s", client_id)
                await conn.close_event.wait()
                logger.debug("Owner task received close signal for %s", client_id)
        except Exception:
            logger.exception("Owner task error for %s", client_id)
            conn.ready_event.set()
            raise
        finally:
            conn.done_event.set()
            logger.debug("Owner task done for %s", client_id)

    async def release(self, client_id: str) -> None:
        """Decrement ref count. When 0, signal owner-task to shut down.

        Args:
            client_id: Cache key returned by get_transport.
        """
        with self._lock:
            conn = self._connections.get(client_id)
            if conn is None:
                logger.warning("release() called for unknown client_id: %s", client_id)
                return

            conn.ref_count -= 1
            self._connections.move_to_end(client_id)

            if conn.ref_count <= 0:
                self._signal_shutdown_locked(client_id, conn)

        if conn.is_stdio and conn.ref_count <= 0:
            try:
                await asyncio.shield(asyncio.wait_for(conn.done_event.wait(), timeout=10.0))
            except TimeoutError:
                logger.warning("Owner task for %s did not shut down in 10s, cancelling", client_id)
                if conn.owner_task is not None:
                    conn.owner_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await conn.owner_task

            with self._lock:
                self._connections.pop(client_id, None)

    def _signal_shutdown_locked(
        self,
        client_id: str,
        conn: _PooledConnection,
    ) -> None:
        """Signal the owner task to shut down and remove from cache.

        Must be called with self._lock held.

        Args:
            client_id: Cache key for logging.
            conn: The connection to shut down.
        """
        if conn.is_stdio:
            conn.close_event.set()
        else:
            # HTTP/SSE: no owner task, just remove
            self._connections.pop(client_id, None)

    async def shutdown_all(self, timeout: float = 10.0) -> None:
        """Clean shutdown of all connections. Called on pool shutdown.

        Signals all owner tasks to shut down and waits for them to
        complete within the timeout.

        Args:
            timeout: Maximum time to wait for all connections to shut down.
        """
        with self._lock:
            items = list(self._connections.items())
            for cid, conn in items:
                if conn.is_stdio:
                    conn.close_event.set()
                else:
                    self._connections.pop(cid, None)

        stdio_tasks: list[asyncio.Task[None]] = []
        for cid, conn in items:
            if conn.is_stdio and conn.owner_task is not None:
                stdio_tasks.append(conn.owner_task)
                logger.debug("Signaled shutdown for %s", cid)

        if not stdio_tasks:
            return

        _done, pending = await asyncio.wait(
            stdio_tasks,
            timeout=timeout,
            return_when=asyncio.ALL_COMPLETED,
        )

        for task in pending:
            logger.warning("Cancelling owner task that did not shut down: %s", task.get_name())
            task.cancel()

        for task in pending:
            with contextlib.suppress(asyncio.CancelledError):
                await task
            if not task.cancelled() and task.exception() is not None:
                logger.exception("Error during shutdown of owner task: %s", task.get_name())

        with self._lock:
            self._connections.clear()

        logger.info("GlobalConnectionPool shutdown complete")
