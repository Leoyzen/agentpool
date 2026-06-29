"""MCP connection pooling for AgentPool.

Provides :class:`MCPConnectionPool` that shares MCP subprocess connections
across sessions, replacing the pool-agent MCP fallback pattern.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
import time
from typing import TYPE_CHECKING

import anyio

from agentpool.log import get_logger
from agentpool.resource_providers.aggregating import AggregatingResourceProvider
from agentpool.resource_providers.mcp_provider import MCPResourceProvider


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool_config.mcp_server import MCPServerConfig


logger = get_logger(__name__)

DEFAULT_IDLE_TIMEOUT_SECONDS: float = 300.0
DEFAULT_MAX_PROCESSES: int = 100


@dataclass
class _PooledConnection:
    """Internal tracking for a cached MCP connection."""

    provider: MCPResourceProvider
    """The MCP resource provider wrapping the subprocess connection."""
    active_sessions: int = 0
    """Number of active sessions referencing this connection."""
    last_used: float = field(default_factory=time.monotonic)
    """Timestamp of the most recent get/release operation."""


class MCPConnectionPool:
    """Pool of MCP subprocess connections shared across sessions.

    Caches :class:`MCPResourceProvider` instances keyed by server config
    ``client_id``.  The first request for a given config spawns the
    subprocess; subsequent requests return the cached connection.

    Tracks active session count per connection so idle connections can
    be cleaned up after ``idle_timeout_seconds``.

    Provides an :class:`AggregatingResourceProvider` that dynamically
    includes all currently cached providers for drop-in replacement of
    the ``MCPManager.get_aggregating_provider()`` pattern.
    """

    def __init__(
        self,
        servers: Sequence[MCPServerConfig] | None = None,
        *,
        max_processes: int = DEFAULT_MAX_PROCESSES,
        idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the connection pool.

        Args:
            servers: Initial MCP server configurations (can be empty;
                connections are created lazily via :meth:`get_connection`).
            max_processes: Maximum number of distinct subprocess connections
                to cache.  When the limit is reached, new ``get_connection``
                calls reuse the least-recently-used idle connection.
            idle_timeout_seconds: Idle connections (active_sessions == 0)
                are terminated after this many seconds.
        """
        self._servers: list[MCPServerConfig] = (
            list(servers) if isinstance(servers, (list, tuple)) else []
        )
        self._connections: dict[str, _PooledConnection] = {}
        self._max_processes = max_processes
        self._idle_timeout = idle_timeout_seconds
        self._lock = asyncio.Lock()
        self._exit_stack = AsyncExitStack()
        self._aggregating_provider = AggregatingResourceProvider(providers=[], name="mcp_pool")
        self._cleanup_task: asyncio.Task[None] | None = None
        self._shutting_down = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_connection(self, server_config: MCPServerConfig) -> MCPResourceProvider:
        """Get or create a cached MCP connection for *server_config*.

        If a connection for this config's ``client_id`` already exists,
        increments its active session count and returns it.  Otherwise
        spawns a new subprocess (subject to ``max_processes``).

        Args:
            server_config: MCP server configuration.

        Returns:
            The cached or newly-created :class:`MCPResourceProvider`.
        """
        key = server_config.client_id

        async with self._lock:
            if key in self._connections:
                conn = self._connections[key]
                conn.active_sessions += 1
                conn.last_used = time.monotonic()
                logger.debug(
                    "Reusing cached MCP connection",
                    client_id=key,
                    active_sessions=conn.active_sessions,
                )
                return conn.provider

            # Enforce max_processes: if at capacity, recycle the
            # least-recently-used idle connection.
            if len(self._connections) >= self._max_processes:
                recycled = await self._recycle_lru_idle()
                if recycled is None:
                    logger.warning(
                        "MCP connection pool at capacity (%d) with no idle "
                        "connections to recycle — request for '%s' denied",
                        self._max_processes,
                        key,
                    )
                    raise RuntimeError(f"MCP connection pool at capacity ({self._max_processes})")

            # Spawn new connection
            provider = MCPResourceProvider(
                server=server_config,
                name=f"mcp_pool_{key}",
                source="pool",
            )
            try:
                await self._exit_stack.enter_async_context(provider)
            except Exception:
                logger.exception("Failed to spawn MCP subprocess for '%s'", key)
                raise

            self._connections[key] = _PooledConnection(
                provider=provider,
                active_sessions=1,
            )
            self._aggregating_provider.add_provider(provider)
            logger.info(
                "Spawned new MCP connection",
                client_id=key,
                total_connections=len(self._connections),
            )
            return provider

    def release_connection(self, server_config: MCPServerConfig) -> None:
        """Release a previously acquired connection.

        Decrements the active session count.  When the count reaches
        zero the connection becomes eligible for idle-timeout cleanup.

        Args:
            server_config: MCP server configuration whose connection
                should be released.
        """
        key = server_config.client_id
        conn = self._connections.get(key)
        if conn is None:
            logger.debug("release_connection called for unknown config", client_id=key)
            return
        conn.active_sessions = max(0, conn.active_sessions - 1)
        conn.last_used = time.monotonic()
        logger.debug(
            "Released MCP connection",
            client_id=key,
            active_sessions=conn.active_sessions,
        )

    def get_aggregating_provider(self) -> AggregatingResourceProvider:
        """Return the aggregating provider for all cached connections.

        The returned provider dynamically includes any connections
        added after this call, so it is safe to obtain once and reuse.

        Returns:
            The :class:`AggregatingResourceProvider` that wraps all
            currently cached (and future) MCP providers.
        """
        return self._aggregating_provider

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_cleanup_task(self) -> None:
        """Start the background idle-connection cleanup loop."""
        if self._cleanup_task is None and not self._shutting_down:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.debug("MCP connection pool cleanup task started")

    async def stop_cleanup_task(self) -> None:
        """Stop the background cleanup loop."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            with __import__("contextlib").suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None
            logger.debug("MCP connection pool cleanup task stopped")

    async def shutdown(self) -> None:
        """Terminate all subprocess connections and clear the cache.

        Safe to call multiple times.
        """
        if self._shutting_down:
            return
        self._shutting_down = True

        await self.stop_cleanup_task()

        async with self._lock:
            providers_to_close = [conn.provider for conn in self._connections.values()]
            self._connections.clear()
            # Reset aggregating provider to empty list so it no longer
            # references closed providers.
            self._aggregating_provider.providers = []

        # Close providers outside the lock to avoid deadlocks
        for provider in providers_to_close:
            try:
                await provider.__aexit__(None, None, None)
            except Exception:
                logger.exception(
                    "Error closing MCP provider during shutdown",
                    provider=repr(provider),
                )

        try:
            with anyio.CancelScope(shield=True):
                try:
                    with anyio.fail_after(5):
                        await self._exit_stack.aclose()
                except TimeoutError:
                    logger.warning("MCP connection pool shutdown timed out after 5s")
        except Exception:
            logger.exception("Error during MCP connection pool shutdown")

        logger.info("MCP connection pool shut down")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _recycle_lru_idle(self) -> str | None:
        """Find and close the least-recently-used idle connection.

        Returns the ``client_id`` of the recycled connection, or
        ``None`` if no idle connection exists.

        Must be called while holding ``self._lock``.
        """
        idle_connections = [
            (key, conn) for key, conn in self._connections.items() if conn.active_sessions == 0
        ]
        if not idle_connections:
            return None

        # Sort by last_used ascending (oldest first)
        idle_connections.sort(key=lambda item: item[1].last_used)
        lru_key, lru_conn = idle_connections[0]

        logger.info(
            "Recycling idle MCP connection (pool at capacity)",
            client_id=lru_key,
            idle_seconds=time.monotonic() - lru_conn.last_used,
        )

        # Remove from tracking
        del self._connections[lru_key]
        self._aggregating_provider.remove_provider(lru_conn.provider)

        # Close the provider (best-effort, don't block)
        try:
            await lru_conn.provider.__aexit__(None, None, None)
        except Exception:
            logger.exception(
                "Error closing recycled MCP provider",
                client_id=lru_key,
            )

        return lru_key

    async def _cleanup_loop(self) -> None:
        """Background task that periodically terminates idle connections."""
        sleep_interval = max(10.0, self._idle_timeout / 4)
        while not self._shutting_down:
            try:
                await asyncio.sleep(sleep_interval)
                await self._cleanup_idle()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("MCP connection pool cleanup iteration failed")

    async def _cleanup_idle(self) -> None:
        """Terminate connections that have been idle beyond the timeout."""
        now = time.monotonic()
        to_close: list[tuple[str, MCPResourceProvider]] = []

        async with self._lock:
            for key, conn in list(self._connections.items()):
                if conn.active_sessions == 0 and (now - conn.last_used) > self._idle_timeout:
                    to_close.append((key, conn.provider))
                    del self._connections[key]
                    self._aggregating_provider.remove_provider(conn.provider)

        for key, provider in to_close:
            logger.info(
                "Terminating idle MCP connection",
                client_id=key,
                idle_timeout=self._idle_timeout,
            )
            try:
                await provider.__aexit__(None, None, None)
            except Exception:
                logger.exception("Error closing idle MCP provider", client_id=key)
