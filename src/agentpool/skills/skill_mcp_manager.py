"""Per-skill MCP server connection manager.

Manages the lifecycle of MCP server connections that are scoped to skill
activations. Unlike the pool-level ``MCPManager`` which maintains persistent
connections, ``SkillMcpManager`` lazily connects on first use and disconnects
on session cleanup, with idle timeout support.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from agentpool.mcp_server.config_snapshot import McpConfigEntry
    # MCPCapability removed - use MCPCapability from agentpool.capabilities.mcp_capability
    from agentpool.tools.base import Tool
    from agentpool_config.skills import SkillMcpServerConfig

logger = logging.getLogger(__name__)

DEFAULT_IDLE_TIMEOUT = 300  # 5 minutes in seconds
DEFAULT_MAX_RETRIES = 3
RETRY_BASE_DELAY = 1  # seconds


class SkillMcpManager:
    """Manages per-skill MCP server connections with activation-scoped lifecycle.

    Each skill activation (tied to a session) gets its own MCP server
    connection. Connections are lazily established on first tool access
    and automatically disconnected when the session ends or when idle
    timeout is reached.

    Thread-safe: concurrent ``connect()`` calls for the same server name
    are serialized via per-server ``asyncio.Lock``.
    """

    def __init__(self, idle_timeout: float = DEFAULT_IDLE_TIMEOUT) -> None:
        """Initialize the skill MCP manager.

        Args:
            idle_timeout: Seconds of inactivity before a connection is
                considered idle and eligible for reconnection on next access.
                Defaults to 300 (5 minutes).
        """
        self._idle_timeout = idle_timeout

        # Registered server configs: server_name → SkillMcpServerConfig
        self._configs: dict[str, SkillMcpServerConfig] = {}

        # Active connections: session_id → server_name → MCPCapability
        self._providers: dict[str, dict[str, MCPCapability]] = {}

        # Per-server locks for thread-safe connect serialization
        self._locks: dict[str, asyncio.Lock] = {}

        # Last activity timestamps: (session_id, server_name) → float (monotonic)
        self._last_activity: dict[tuple[str, str], float] = {}

    # ---- Registration ----

    def prepare(self, server_name: str, config: SkillMcpServerConfig) -> None:
        """Register a server config for lazy connection.

        Does not start any subprocess. The actual connection is
        established lazily on the first ``connect()`` or ``get_tools()`` call.

        Args:
            server_name: Unique name for this MCP server within the manager.
            config: Connection configuration (command+args or url+headers).
        """
        self._configs[server_name] = config
        logger.debug("Prepared skill MCP server config", extra={"server_name": server_name})

    def build_config_entries(self, skill_name: str) -> tuple[McpConfigEntry, ...]:
        """Build ``McpConfigEntry`` entries from all registered configs.

        Converts each registered ``SkillMcpServerConfig`` to a standard
        ``MCPServerConfig`` via ``to_mcp_server_config()``, then wraps it
        in a ``McpConfigEntry`` tagged with ``source="skill"`` and the
        given ``skill_name``.

        Args:
            skill_name: Name of the skill that owns these MCP servers.

        Returns:
            A tuple of ``McpConfigEntry`` instances, one per registered server.
        """
        from agentpool.mcp_server.config_snapshot import McpConfigEntry

        entries: list[McpConfigEntry] = []
        for server_name, config in self._configs.items():
            mcp_config = config.to_mcp_server_config(name=server_name)
            entries.append(
                McpConfigEntry(
                    server_config=mcp_config,
                    source="skill",
                    skill_name=skill_name,
                )
            )
        return tuple(entries)

    # ---- Connection ----

    async def connect(self, server_name: str, session_id: str) -> MCPCapability:
        """Lazily connect to a skill MCP server, returning its provider.

        On first call for a ``(session_id, server_name)`` pair, creates an
        ``MCPCapability`` and establishes the connection. Subsequent
        calls return the existing provider.

        If the existing connection has been idle beyond the configured
        timeout, it is disconnected and reconnected.

        Connection failures are retried with exponential backoff (up to
        ``DEFAULT_MAX_RETRIES`` attempts).

        Args:
            server_name: Name of the server to connect to.
            session_id: Session identifier for connection scoping.

        Returns:
            The connected ``MCPCapability``.

        Raises:
            ValueError: If ``server_name`` was not registered via ``prepare()``.
            RuntimeError: If connection fails after all retries.
        """
        if server_name not in self._configs:
            raise ValueError(f"Unknown skill MCP server: {server_name!r}. Call prepare() first.")

        # Fast path: already connected and not idle
        existing = self._get_provider(server_name, session_id)
        if existing is not None and not self._is_idle(session_id, server_name):
            self._touch(session_id, server_name)
            return existing

        # Serialize concurrent connect() calls for the same server name
        lock = self._get_lock(server_name)
        async with lock:
            # Double-check after acquiring lock
            existing = self._get_provider(server_name, session_id)
            if existing is not None and not self._is_idle(session_id, server_name):
                self._touch(session_id, server_name)
                return existing

            # Disconnect idle connection before reconnecting
            if existing is not None:
                await self.disconnect(server_name, session_id)

            # Retry with exponential backoff
            config = self._configs[server_name]
            last_error: Exception | None = None
            for attempt in range(1, DEFAULT_MAX_RETRIES + 1):
                try:
                    provider = await self._create_and_connect(config, server_name)
                except (OSError, TimeoutError, RuntimeError) as e:
                    last_error = e
                    if attempt < DEFAULT_MAX_RETRIES:
                        delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                        logger.warning(
                            "Skill MCP server connection failed, retrying",
                            extra={
                                "server_name": server_name,
                                "session_id": session_id,
                                "attempt": attempt,
                                "delay": delay,
                                "error": str(e),
                            },
                        )
                        await asyncio.sleep(delay)
                else:
                    self._store_provider(server_name, session_id, provider)
                    self._touch(session_id, server_name)
                    logger.info(
                        "Connected skill MCP server",
                        extra={
                            "server_name": server_name,
                            "session_id": session_id,
                            "attempt": attempt,
                        },
                    )
                    return provider

            raise RuntimeError(
                f"Failed to connect skill MCP server {server_name!r} "
                f"after {DEFAULT_MAX_RETRIES} attempts"
            ) from last_error

    async def get_tools(self, server_name: str, session_id: str) -> list[Tool]:
        """Get tools from a connected skill MCP server.

        Ensures the server is connected (lazy connect on first call),
        then returns the cached tool list.

        Args:
            server_name: Name of the server.
            session_id: Session identifier.

        Returns:
            List of tools from the MCP server.
        """
        provider = await self.connect(server_name, session_id)
        tools = await provider.get_tools()
        self._touch(session_id, server_name)
        return list(tools)

    # ---- Disconnection ----

    async def disconnect(self, server_name: str, session_id: str) -> None:
        """Disconnect a specific server for a session.

        No-op if the server is not connected for this session.

        Args:
            server_name: Name of the server to disconnect.
            session_id: Session identifier.
        """
        provider = self._pop_provider(server_name, session_id)
        if provider is None:
            return

        try:
            await provider.__aexit__(None, None, None)
        except Exception:
            logger.exception(
                "Error disconnecting skill MCP server",
                extra={"server_name": server_name, "session_id": session_id},
            )

        self._last_activity.pop((session_id, server_name), None)
        logger.info(
            "Disconnected skill MCP server",
            extra={"server_name": server_name, "session_id": session_id},
        )

    async def cleanup(self, session_id: str) -> None:
        """Disconnect ALL servers for a session.

        Called when a run ends. Disconnects every server that was
        connected during this session.

        Args:
            session_id: Session identifier to clean up.
        """
        session_providers = self._providers.get(session_id)
        if not session_providers:
            return

        server_names = list(session_providers.keys())
        for server_name in server_names:
            await self.disconnect(server_name, session_id)

        self._providers.pop(session_id, None)
        logger.info(
            "Cleaned up all skill MCP servers for session",
            extra={"session_id": session_id},
        )

    async def cleanup_all(self) -> None:
        """Disconnect all servers for all sessions.

        Called on pool shutdown.
        """
        session_ids = list(self._providers.keys())
        for session_id in session_ids:
            await self.cleanup(session_id)
        logger.info("Cleaned up all skill MCP servers (all sessions)")

    async def cleanup_idle(self) -> None:
        """Disconnect all connections that have been idle beyond the timeout.

        Useful for periodic background cleanup. Checks every active
        connection and disconnects any that exceed the idle timeout.

        Returns:
            Number of idle connections that were disconnected.
        """
        idle_keys: list[tuple[str, str]] = []
        for (session_id, server_name), last_time in self._last_activity.items():
            elapsed = time.monotonic() - last_time
            if elapsed > self._idle_timeout:
                idle_keys.append((session_id, server_name))

        for session_id, server_name in idle_keys:
            await self.disconnect(server_name, session_id)

        if idle_keys:
            logger.info(
                "Disconnected idle skill MCP servers",
                extra={"count": len(idle_keys)},
            )

    # ---- Internal helpers ----

    def _get_lock(self, server_name: str) -> asyncio.Lock:
        """Get or create the asyncio.Lock for a server name."""
        if server_name not in self._locks:
            self._locks[server_name] = asyncio.Lock()
        return self._locks[server_name]

    def _get_provider(self, server_name: str, session_id: str) -> MCPCapability | None:
        """Get existing provider for a session+server pair, or None."""
        session_providers = self._providers.get(session_id, {})
        return session_providers.get(server_name)

    def _store_provider(
        self,
        server_name: str,
        session_id: str,
        provider: MCPCapability,
    ) -> None:
        """Store a provider for a session+server pair."""
        self._providers.setdefault(session_id, {})[server_name] = provider

    def _pop_provider(self, server_name: str, session_id: str) -> MCPCapability | None:
        """Remove and return a provider for a session+server pair."""
        session_providers = self._providers.get(session_id, {})
        return session_providers.pop(server_name, None)

    def _touch(self, session_id: str, server_name: str) -> None:
        """Update the last activity timestamp for a connection."""
        self._last_activity[(session_id, server_name)] = time.monotonic()

    def _is_idle(self, session_id: str, server_name: str) -> bool:
        """Check if a connection has been idle beyond the timeout."""
        key = (session_id, server_name)
        last = self._last_activity.get(key)
        if last is None:
            return False
        return (time.monotonic() - last) > self._idle_timeout

    async def _create_and_connect(
        self, config: SkillMcpServerConfig, server_name: str
    ) -> MCPCapability:
        """Create an MCPCapability from config and connect it.

        Converts ``SkillMcpServerConfig`` to the appropriate
        ``MCPServerConfig`` subclass based on whether ``command`` or
        ``url`` is specified.

        Args:
            config: Skill MCP server configuration.
            server_name: Name for the provider.

        Returns:
            A connected ``MCPCapability``.

        Raises:
            ValueError: If neither ``command`` nor ``url`` is specified.
        """
        # MCPCapability removed - use MCPCapability from agentpool.capabilities.mcp_capability
        from agentpool_config.mcp_server import (
            BaseMCPServerConfig,
            StdioMCPServerConfig,
            StreamableHTTPMCPServerConfig,
        )

        mcp_config: BaseMCPServerConfig
        if config.command:
            mcp_config = StdioMCPServerConfig(
                name=server_name,
                command=config.command,
                args=config.args,
                env=config.env if config.env else None,
            )
        elif config.url:
            from pydantic import HttpUrl

            mcp_config = StreamableHTTPMCPServerConfig(
                name=server_name,
                url=HttpUrl(config.url),
                headers=config.headers if config.headers else None,
                env=config.env if config.env else None,
            )
        else:
            raise ValueError(
                f"SkillMcpServerConfig for {server_name!r} must specify either 'command' or 'url'"
            )

        provider = MCPCapability(
            server=mcp_config,
            name=f"skill_mcp_{server_name}",
            source="node",
        )
        await provider.__aenter__()
        return provider
