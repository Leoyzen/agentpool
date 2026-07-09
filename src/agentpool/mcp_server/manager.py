"""MCP server management for AgentPool."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import AsyncExitStack
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self, cast
import warnings

import anyio

from agentpool.capabilities.combined_toolset import AbstractCapability, CombinedToolsetCapability
from agentpool.capabilities.mcp_capability import MCPCapability
from agentpool.log import get_logger
from agentpool.mcp_server.global_pool import GlobalConnectionPool
from agentpool_config.mcp_server import AcpMCPServerConfig, BaseMCPServerConfig


if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from fastmcp.client import ClientTransport
    from mcp import types
    from mcp.shared.context import RequestContext
    from mcp.types import ElicitRequestParams, SamplingMessage
    from pydantic_ai.capabilities import MCP

    from agentpool.mcp_server.config_snapshot import McpConfigSnapshot
    from agentpool.mcp_server.session_pool import SessionConnectionPool
    from agentpool.ui.base import InputProvider
    from agentpool_config.mcp_server import MCPServerConfig
    from agentpool_server.acp_server.acp_mcp_manager import AcpMcpConnectionManager


logger = get_logger(__name__)

# ContextVar for the current session's InputProvider, set by the run loop
# before agent execution.  Read by the PydanticAI MCP elicitation callback
# so that agent-level MCP servers can delegate to ACPInputProvider.
_current_input_provider: ContextVar[InputProvider | None] = ContextVar(
    "_current_input_provider", default=None
)


def set_current_input_provider(provider: InputProvider | None) -> None:
    """Set the InputProvider for the current async context."""
    _current_input_provider.set(provider)


def _make_elicitation_handler() -> Any:
    """Create a FastMCP elicitation handler for MCPToolset.

    The handler reads the current InputProvider from the ContextVar
    and delegates to ``InputProvider.get_elicitation()``.
    """
    from fastmcp.client.elicitation import ElicitResult

    async def _handler[T](
        message: str,
        response_type: type[T] | None,
        params: ElicitRequestParams,
        context: RequestContext[Any, Any],
    ) -> T | dict[str, Any] | ElicitResult:
        provider = _current_input_provider.get()
        if provider is None:
            logger.warning(
                "No InputProvider in context for MCP elicitation, declining",
            )
            return ElicitResult(action="decline")
        result = await provider.get_elicitation(params)
        return cast("T | dict[str, Any] | ElicitResult", result)

    return _handler


def _make_timeout_logger(
    server_name: str | None,
) -> Any:
    """Build a ``process_tool_call`` callback that logs MCP tool call timeouts.

    The callback wraps ``direct_call_tool`` and emits a ``WARNING``-level log
    when the underlying MCP request times out, so operators can distinguish
    timeouts from other tool errors.

    Args:
        server_name: Display name of the MCP server, included in the log message.

    Returns:
        A callable suitable for ``MCPToolset.process_tool_call``.
    """

    async def _process_tool_call(
        ctx: Any,
        direct_call_tool: Any,
        name: str,
        tool_args: dict[str, Any],
    ) -> Any:
        try:
            return await direct_call_tool(name, tool_args)
        except Exception as e:
            msg = str(e)
            if "Timed out" in msg or "timeout" in msg.lower():
                logger.warning(
                    "MCP tool call timed out (server=%s, tool=%s): %s",
                    server_name,
                    name,
                    msg,
                )
            raise

    return _process_tool_call


@dataclass
class _SessionContext:
    """Per-session MCP state container for the :class:`MCPManager`.

    Holds all session-scoped MCP resources so that each session has its own
    connection pool, toolset cache, config snapshot, and ACP connection
    tracking — isolated from other sessions.

    Attributes:
        connection_pool: Per-session transport pool; created lazily by
            ``get_or_create_session()`` (T2).
        toolset_cache: Session-scoped ``MCPToolset`` cache keyed by
            ``client_id``, mirroring the global ``_toolset_cache``.
        snapshot: Immutable MCP config snapshot for this session, or
            ``None`` if no snapshot has been built yet.
        acp_connection_ids: List of ``(client_id, connection_id)`` tuples
            for ACP MCP connections opened during this session, used for
            cleanup tracking.
        _cleanup_lock: Serializes concurrent cleanup calls for this session.
    """

    connection_pool: SessionConnectionPool | None = None
    toolset_cache: dict[str, Any] = field(default_factory=dict)
    snapshot: McpConfigSnapshot | None = None
    acp_connection_ids: list[tuple[str, int]] = field(default_factory=list)
    _cleanup_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class MCPManager:
    """Manages MCP server connections and distributes resource providers.

    .. deprecated::
        This class is deprecated and will be removed in v0.5.0.
        Use :meth:`get_capabilities()` instead.
    """

    def __init__(
        self,
        name: str = "mcp",
        owner: str | None = None,
        sampling_model: str = "openai:gpt-5-nano",
        servers: Sequence[MCPServerConfig | str] | None = None,
        accessible_roots: list[str] | None = None,
    ) -> None:
        self.name = name
        self.owner = owner
        self.servers: list[MCPServerConfig] = []
        for server in servers or []:
            self.add_server_config(server)
        self.providers: list[MCPCapability] = []
        self.sampling_model = sampling_model
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self.aggregating_provider = CombinedToolsetCapability(
                capabilities=cast(list[AbstractCapability], self.providers),
                name=f"{name}_aggregated",
            )
        self.exit_stack = AsyncExitStack()
        self._accessible_roots = accessible_roots
        self._global_pool = GlobalConnectionPool()
        self._toolset_cache: dict[str, Any] = {}
        self._session_contexts: dict[str, _SessionContext] = {}
        self._acp_mcp_manager: AcpMcpConnectionManager | None = None

    def add_server_config(self, cfg: MCPServerConfig | str) -> None:
        """Add a new MCP server to the manager."""
        resolved = BaseMCPServerConfig.from_string(cfg) if isinstance(cfg, str) else cfg
        self.servers.append(resolved)

    def get_or_create_session(self, session_id: str) -> _SessionContext:
        """Get or create the per-session MCP context for ``session_id``.

        If no context exists for ``session_id``, a new ``_SessionContext``
        is created with a fresh ``SessionConnectionPool``, empty toolset
        cache, no snapshot, and an empty ACP connection list.  Subsequent
        calls with the same ``session_id`` return the same object.

        Args:
            session_id: Unique identifier for the session.

        Returns:
            The ``_SessionContext`` for this session.
        """
        from agentpool.mcp_server.session_pool import SessionConnectionPool

        ctx = self._session_contexts.get(session_id)
        if ctx is None:
            ctx = _SessionContext(
                connection_pool=SessionConnectionPool(session_id=session_id),
            )
            self._session_contexts[session_id] = ctx
        return ctx

    def get_session_context(self, session_id: str) -> _SessionContext | None:
        """Get the session context for ``session_id`` without creating one.

        Returns ``None`` if no context exists for the session.
        """
        return self._session_contexts.get(session_id)

    def update_session_snapshot(
        self,
        session_id: str,
        snapshot: McpConfigSnapshot,
    ) -> None:
        """Update the config snapshot for a session.

        Ensures the session context exists (creating it if necessary),
        then sets ``ctx.snapshot`` to the provided snapshot.  Safe to call
        on an already-existing session — only the snapshot is replaced.

        Args:
            session_id: Unique identifier for the session.
            snapshot: Immutable MCP config snapshot to store.
        """
        ctx = self.get_or_create_session(session_id)
        ctx.snapshot = snapshot

    def __repr__(self) -> str:
        return f"MCPManager(name={self.name!r}, servers={len(self.servers)})"

    async def __aenter__(self) -> Self:
        try:
            if tasks := [self.setup_server(server) for server in self.servers]:
                await asyncio.gather(*tasks)
        except Exception as e:
            server_names = [s.display_name for s in self.servers]
            logger.warning(
                "MCP manager initialization failed (servers: %s): %s",
                server_names,
                e,
            )
            await self.__aexit__(type(e), e, e.__traceback__)
            raise RuntimeError(f"Failed to initialize MCP manager (servers: {server_names})") from e

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.cleanup()

    async def _sampling_callback(
        self,
        messages: list[SamplingMessage],
        params: types.CreateMessageRequestParams,
        context: RequestContext[Any, Any, Any],
    ) -> str:
        """Handle MCP sampling by creating a new agent with specified preferences."""
        from agentpool.agents import Agent
        from agentpool.mcp_server.conversions import sampling_messages_to_user_content

        prompts = sampling_messages_to_user_content(messages)
        model = self.sampling_model
        if (prefs := params.modelPreferences) and prefs.hints and prefs.hints[0].name:
            model = prefs.hints[0].name  # Extract model from preferences
        # Create usage limits from sampling parameters
        # limits = UsageLimits(output_tokens_limit=params.maxTokens, request_limit=1)
        # TODO: Re-add per-turn usage_limits once implemented for all agents
        # TODO: Apply temperature from params.temperature
        sys_prompt = params.systemPrompt or ""
        agent = Agent(name="sampling-agent", model=model, system_prompt=sys_prompt, session=False)
        try:
            async with agent:
                result = await agent.run(*prompts, store_history=False)
                return result.content

        except Exception as e:
            logger.exception("Sampling failed")
            return f"Sampling failed: {e!s}"

    async def setup_server(
        self, config: MCPServerConfig, *, add_to_config: bool = False
    ) -> MCPCapability | None:
        """Set up a single MCP server resource provider.

        Args:
            config: MCP server configuration
            add_to_config: If True, also add config to self.servers list and
                          raise ValueError if config is disabled

        Returns:
            The provider if created, None if config is disabled (only when add_to_config=False)

        Raises:
            ValueError: If add_to_config=True and config is disabled
        """
        if not config.enabled:
            if add_to_config:
                raise ValueError(f"Server config {config.client_id} is disabled")
            return None

        if add_to_config:
            self.add_server_config(config)

        # Deduplication: skip if a provider with the same client_id already exists
        if any(p.server.client_id == config.client_id for p in self.providers):
            logger.debug(
                "MCP server already registered, skipping",
                client_id=config.client_id,
            )
            return None

        provider = MCPCapability(
            server=config,
            name=f"{self.name}_{config.display_name}",
            owner=self.owner,
            source="pool" if self.owner == "pool" else "node",
            sampling_callback=self._sampling_callback,
            accessible_roots=self._accessible_roots,
        )
        provider = await self.exit_stack.enter_async_context(provider)
        self.providers.append(provider)
        return provider

    def get_mcp_providers(self) -> list[MCPCapability]:
        """Get all MCP resource providers managed by this manager."""
        return list(self.providers)

    def remove_provider(self, client_id: str) -> bool:
        """Remove a provider by its server config's client_id.

        Args:
            client_id: The client_id of the MCP server config to remove

        Returns:
            True if a provider was removed, False otherwise
        """
        for i, provider in enumerate(self.providers):
            if provider.server.client_id == client_id:
                # Note: We don't remove from exit_stack here because
                # the provider was entered into the stack; cleanup() handles that
                self.providers.pop(i)
                return True
        return False

    async def disconnect_all(self) -> None:
        """Disconnect all MCP providers without clearing the servers list."""
        # Close cached MCPToolset instances before clearing the cache.
        # MCPToolset has no aclose() — must use __aexit__ for cleanup.
        # Guard against toolsets that were constructed but never entered
        # (MCPToolset.__aexit__ raises ValueError if __aenter__ wasn't called).
        for toolset in self._toolset_cache.values():
            with contextlib.suppress(ValueError):
                await toolset.__aexit__(None, None, None)
        self._toolset_cache.clear()
        await self._global_pool.shutdown_all()
        await self.cleanup()
        self.exit_stack = AsyncExitStack()

    def get_aggregating_provider(self) -> CombinedToolsetCapability:
        """Get an aggregating provider containing only ACP providers.

        Non-ACP providers are excluded because they are handled separately
        by :meth:`get_capabilities()`.
        """
        acp_providers = [p for p in self.providers if isinstance(p.server, AcpMCPServerConfig)]
        return CombinedToolsetCapability(
            capabilities=cast(list[AbstractCapability], acp_providers),
            name=f"{self.name}_acp_aggregated",
        )

    async def get_capabilities(  # noqa: PLR0915
        self,
        session_id: str | None = None,
    ) -> list[MCP]:
        """Return pydantic-ai MCP capabilities for all configured servers.

        Each enabled server is converted to a pydantic-ai ``MCP`` capability.
        ``MCPToolset`` instances are cached by ``client_id`` so repeated calls
        reuse the same underlying connection.  Servers using ACP transport are
        skipped in global configs since pydantic-ai does not support ACP
        directly. Disabled servers are also skipped.

        When ``session_id`` is provided, the session's ``_SessionContext`` is
        looked up via ``get_or_create_session()``.  If a snapshot is stored
        on the context, configs are partitioned:

        - Global configs (pool + agent) use ``self._global_pool`` for
          transports and ``self._toolset_cache`` for toolset caching.
        - Session-scoped configs (session + skill) use the session's
          ``connection_pool`` for transports and ``ctx.toolset_cache`` for
          toolset caching.

        GAP-11: If the session context was popped by concurrent
        ``cleanup_session()`` between the initial state check and
        subsequent access, the ``.get()`` call returns ``None`` and
        the method falls back to global-only capabilities (with a
        warning).  This is a benign race: ``dict.get()`` is atomic,
        so no ``KeyError`` can occur.

        When ``session_id`` is None, the legacy path uses ``self.servers``
        with ``self._global_pool`` for transports and ``self._toolset_cache``
        for toolset caching.

        Args:
            session_id: Optional session identifier for per-session MCP
                config isolation. When None, only global configs from
                ``self.servers`` are processed.

        Returns:
            A list of ``pydantic_ai.capabilities.MCP`` instances, one per
            configured and enabled server with a supported transport.
        """
        from pydantic_ai.capabilities import MCP
        from pydantic_ai.mcp import MCPToolset

        from agentpool_config.mcp_server import (
            SSEMCPServerConfig,
            StdioMCPServerConfig,
            StreamableHTTPMCPServerConfig,
        )

        capabilities: list[MCP] = []

        def _make_kwargs(server: BaseMCPServerConfig) -> dict[str, Any]:
            """Build MCPToolset constructor kwargs (without client)."""
            kwargs: dict[str, Any] = {
                "id": server.name,
                "include_instructions": True,
                "process_tool_call": _make_timeout_logger(server.display_name),
                "init_timeout": server.timeout,
                "read_timeout": server.timeout,
                "elicitation_handler": _make_elicitation_handler(),
            }
            if (
                isinstance(server, (SSEMCPServerConfig, StreamableHTTPMCPServerConfig))
                and server.auth.oauth
            ):
                kwargs["auth"] = "oauth"
            return kwargs

        def _derive_url(server: BaseMCPServerConfig) -> str:
            """Derive the synthetic URL required by ``MCP.__init__``."""
            match server:
                case SSEMCPServerConfig():
                    return str(server.url)
                case StreamableHTTPMCPServerConfig():
                    return str(server.url)
                case StdioMCPServerConfig():
                    return f"mcp://stdio/{server.client_id}"
                case _:
                    return f"mcp://{server.type}/{server.client_id}"

        def _make_capability(
            server: BaseMCPServerConfig,
            transport: Any,
            toolset_cache: dict[str, Any],
        ) -> MCP:
            """Create or reuse an MCPToolset and wrap it in an MCP capability.

            On first call for a given ``client_id``, a new ``MCPToolset`` is
            constructed and stored in ``toolset_cache``.  Subsequent calls
            reuse the cached instance, ensuring one underlying connection per
            server config.  The ``MCP`` wrapper is always fresh.
            """
            client_id = server.client_id
            toolset = toolset_cache.get(client_id)
            if toolset is None:
                toolset = MCPToolset(client=transport, **_make_kwargs(server))
                toolset_cache[client_id] = toolset

            return MCP(
                url=_derive_url(server),
                local=toolset,
                native=False,
                id=server.name or server.client_id,
                allowed_tools=server.enabled_tools,
            )

        async def _process_global_configs(
            snap: McpConfigSnapshot,
            toolset_cache: dict[str, Any],
        ) -> None:
            """Process global configs (pool + agent) from the snapshot."""
            for entry in snap.global_configs:
                server = entry.server_config
                if not server.enabled or isinstance(server, AcpMCPServerConfig):
                    continue
                transport = await self._global_pool.get_transport(server)
                capabilities.append(_make_capability(server, transport, toolset_cache))

        async def _process_session_configs(
            snap: McpConfigSnapshot,
            toolset_cache: dict[str, Any],
            connection_pool: SessionConnectionPool,
        ) -> None:
            """Process session-scoped configs (session + skill) from the snapshot.

            ACP entries have pre-stored transports via ``add_transport()``;
            ``get_transport()`` returns them without trying to create new
            ones.  Inherited ACP configs (from parent session) that don't
            have a transport in this session's pool are skipped — they go
            through the ACP aggregating provider.
            """
            for entry in snap.session_scoped_configs:
                server = entry.server_config
                if not server.enabled:
                    continue
                if isinstance(server, AcpMCPServerConfig):
                    try:
                        transport = await connection_pool.get_transport(server, entry.skill_name)
                    except NotImplementedError:
                        continue
                else:
                    transport = await connection_pool.get_transport(server, entry.skill_name)
                capabilities.append(_make_capability(server, transport, toolset_cache))

        ctx = self._session_contexts.get(session_id) if session_id is not None else None

        if ctx is not None and ctx.snapshot is not None:
            await _process_global_configs(ctx.snapshot, self._toolset_cache)
            if ctx.connection_pool is not None:
                await _process_session_configs(
                    ctx.snapshot,
                    ctx.toolset_cache,
                    ctx.connection_pool,
                )
        else:
            if session_id is not None and ctx is None:
                logger.warning(
                    "Session %s context was removed during get_capabilities(); "
                    "falling back to global-only MCP capabilities.",
                    session_id,
                )
            for server in self.servers:
                if not server.enabled or isinstance(server, AcpMCPServerConfig):
                    continue
                transport = await self._global_pool.get_transport(server)
                capabilities.append(_make_capability(server, transport, self._toolset_cache))

        return capabilities

    async def cleanup(self) -> None:
        """Clean up all MCP connections and providers."""
        try:
            with anyio.CancelScope(shield=True):
                try:
                    with anyio.fail_after(5):
                        await self.exit_stack.aclose()
                except TimeoutError:
                    logger.warning("MCP cleanup timed out after 5s, forcing exit")

            self.providers.clear()

        except Exception as e:
            msg = "Error during MCP manager cleanup"
            logger.exception(msg, exc_info=e)
            raise RuntimeError(msg) from e

    async def add_acp_transport(
        self,
        session_id: str,
        client_id: str,
        transport: ClientTransport,
        connection_id: str,
        session_key: int,
    ) -> None:
        """Register an ACP MCP transport for a session.

        Adds a pre-created transport (e.g. ``AcpMcpTransport``) to the
        session's connection pool and tracks the ACP connection for
        cleanup.

        Idempotent: calling twice with the same ``connection_id`` and
        ``session_key`` does not create duplicate tracking entries.

        Args:
            session_id: Unique identifier for the session.
            client_id: Client identifier for the MCP server.
            transport: Pre-created fastmcp ``ClientTransport``.
            connection_id: ACP connection identifier.
            session_key: ACP session key for the connection.
        """
        ctx = self.get_or_create_session(session_id)
        pool = ctx.connection_pool
        if pool is not None:
            await pool.add_transport(client_id, transport)
        entry = (connection_id, session_key)
        if entry not in ctx.acp_connection_ids:
            ctx.acp_connection_ids.append(entry)

    async def cleanup_session(self, session_id: str) -> None:
        """Clean up all MCP resources for a single session.

        Clears the session-scoped toolset cache, shuts down the
        per-session connection pool, delegates ACP connection cleanup
        to :class:`AcpMcpConnectionManager` (if wired), and removes
        the session context from the registry.

        The per-session ``_cleanup_lock`` serializes concurrent calls
        for the same ``session_id``, making cleanup idempotent: a
        second caller blocks on the lock, then finds the context
        already popped in the ``finally`` block.

        Intermediate cleanup errors are logged but never re-raised
        so that the context is always removed.

        Args:
            session_id: Unique identifier for the session to clean up.
        """
        ctx = self._session_contexts.get(session_id)
        if ctx is None:
            return
        async with ctx._cleanup_lock:
            # Identity check: if the context in _session_contexts is no
            # longer this ctx, another caller already cleaned it up.
            if self._session_contexts.get(session_id) is not ctx:
                return
            try:
                # Close cached MCPToolset instances before clearing the cache.
                # MCPToolset has no aclose() — must use __aexit__ for cleanup.
                # Guard against toolsets that were constructed but never entered
                # (MCPToolset.__aexit__ raises ValueError if __aenter__ wasn't called).
                for toolset in ctx.toolset_cache.values():
                    with contextlib.suppress(ValueError):
                        await toolset.__aexit__(None, None, None)
                ctx.toolset_cache.clear()

                if ctx.connection_pool is not None:
                    try:
                        await ctx.connection_pool.cleanup()
                    except Exception:
                        logger.exception(
                            "Error cleaning up session connection pool",
                            session_id=session_id,
                        )

                if self._acp_mcp_manager is not None:
                    try:
                        await self._acp_mcp_manager.cleanup_session(session_id)
                    except Exception:
                        logger.exception(
                            "Error cleaning up ACP MCP connections",
                            session_id=session_id,
                        )
            finally:
                self._session_contexts.pop(session_id, None)
