"""MCP server management for AgentPool."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Self, cast

import anyio

from agentpool.log import get_logger
from agentpool.mcp_server.global_pool import GlobalConnectionPool
from agentpool.resource_providers import AggregatingResourceProvider, ResourceProvider
from agentpool.resource_providers.mcp_provider import MCPResourceProvider
from agentpool_config.mcp_server import AcpMCPServerConfig, BaseMCPServerConfig


if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from mcp import types
    from mcp.shared.context import RequestContext
    from mcp.types import ElicitRequestParams, SamplingMessage
    from pydantic_ai.capabilities import MCP

    from agentpool.mcp_server.config_snapshot import McpConfigSnapshot
    from agentpool.mcp_server.session_pool import SessionConnectionPool
    from agentpool.ui.base import InputProvider
    from agentpool_config.mcp_server import MCPServerConfig


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


class MCPManager:
    """Manages MCP server connections and distributes resource providers.

    .. deprecated::
        This class is deprecated and will be removed in v0.5.0.
        Use :meth:`as_capability()` instead.
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
        self.providers: list[MCPResourceProvider] = []
        self.sampling_model = sampling_model
        self.aggregating_provider = AggregatingResourceProvider(
            providers=cast(list[ResourceProvider], self.providers),
            name=f"{name}_aggregated",
        )
        self.exit_stack = AsyncExitStack()
        self._accessible_roots = accessible_roots
        self._global_pool = GlobalConnectionPool()

    def add_server_config(self, cfg: MCPServerConfig | str) -> None:
        """Add a new MCP server to the manager."""
        resolved = BaseMCPServerConfig.from_string(cfg) if isinstance(cfg, str) else cfg
        self.servers.append(resolved)

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
    ) -> MCPResourceProvider | None:
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

        provider = MCPResourceProvider(
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

    def get_mcp_providers(self) -> list[MCPResourceProvider]:
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
        await self._global_pool.shutdown_all()
        await self.cleanup()
        self.exit_stack = AsyncExitStack()

    def get_aggregating_provider(self) -> AggregatingResourceProvider:
        """Get an aggregating provider containing only ACP providers.

        Non-ACP providers are excluded because they are handled separately
        by :meth:`as_capability()`.
        """
        acp_providers = [
            p for p in self.providers if isinstance(p.server, AcpMCPServerConfig)
        ]
        return AggregatingResourceProvider(
            providers=cast(list[ResourceProvider], acp_providers),
            name=f"{self.name}_acp_aggregated",
        )

    async def as_capability(
        self,
        snapshot: McpConfigSnapshot | None = None,
        session_pool: SessionConnectionPool | None = None,
    ) -> list[MCP]:
        """Return pydantic-ai MCP capabilities for all configured servers.

        Each enabled server is converted to a pydantic-ai ``MCP`` capability.
        A new ``MCPToolset`` instance is created on every call — no caching.
        Servers using ACP transport are skipped in global configs since
        pydantic-ai does not support ACP directly. Disabled servers are
        also skipped.

        When ``snapshot`` is provided, configs are read from the snapshot
        and transports are obtained from the appropriate connection pool:

        - Global configs (pool + agent) use ``self._global_pool``
        - Session-scoped configs (session + skill) use ``session_pool``

        When ``snapshot`` is None, the legacy path uses ``self.servers``
        with ``self._global_pool`` for transports.

        Args:
            snapshot: Optional immutable snapshot of MCP configs partitioned
                by lifecycle scope.
            session_pool: Optional per-session connection pool for transport
                lifecycle isolation. Required when snapshot contains
                session-scoped configs.

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
            if isinstance(
                server, (SSEMCPServerConfig, StreamableHTTPMCPServerConfig)
            ) and server.auth.oauth:
                kwargs["auth"] = "oauth"
            return kwargs

        def _make_capability(
            server: BaseMCPServerConfig, transport: Any
        ) -> MCP:
            """Create a fresh MCPToolset and wrap it in an MCP capability."""
            toolset = MCPToolset(client=transport, **_make_kwargs(server))

            match server:
                case SSEMCPServerConfig():
                    url = str(server.url)
                case StreamableHTTPMCPServerConfig():
                    url = str(server.url)
                case StdioMCPServerConfig():
                    url = f"mcp://stdio/{server.client_id}"
                case _:
                    url = f"mcp://{server.type}/{server.client_id}"

            return MCP(
                url=url,
                local=toolset,
                native=False,
                id=server.name or server.client_id,
                allowed_tools=server.enabled_tools,
            )

        if snapshot is not None:
            # Global configs (pool + agent) — borrow from GlobalConnectionPool
            for entry in snapshot.global_configs:
                server = entry.server_config
                if not server.enabled:
                    continue
                if isinstance(server, AcpMCPServerConfig):
                    continue
                transport = await self._global_pool.get_transport(server)
                capabilities.append(_make_capability(server, transport))

            # Session-scoped configs (session + skill) — borrow from
            # SessionConnectionPool.  ACP entries have pre-stored transports
            # via add_transport(); get_transport() returns them without
            # trying to create new ones.  Inherited ACP configs (from parent
            # session) that don't have a transport in this session's pool
            # are skipped — they go through the ACP aggregating provider.
            if session_pool is not None:
                for entry in snapshot.session_scoped_configs:
                    server = entry.server_config
                    if not server.enabled:
                        continue
                    if isinstance(server, AcpMCPServerConfig):
                        # ACP transports are pre-stored via add_transport().
                        # If not found, skip — the ACP aggregating provider
                        # handles ACP MCP tool exposure.
                        try:
                            transport = await session_pool.get_transport(
                                server, entry.skill_name
                            )
                        except NotImplementedError:
                            continue
                    else:
                        transport = await session_pool.get_transport(
                            server, entry.skill_name
                        )
                    capabilities.append(_make_capability(server, transport))
        else:
            # Legacy path: pool servers only, no snapshot
            for server in self.servers:
                if not server.enabled:
                    continue
                if isinstance(server, AcpMCPServerConfig):
                    continue
                transport = await self._global_pool.get_transport(server)
                capabilities.append(_make_capability(server, transport))

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



