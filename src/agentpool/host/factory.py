"""AgentFactory — creates per-session agent instances from config.

Extracts the three agent creation paths (native child, native main,
non-native) from ``SessionController.get_or_create_session_agent()``
into a standalone factory. The factory calls ``__aenter__`` only —
``__aexit__`` is the caller's responsibility. Locking, caching, and
config resolution are NOT handled here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger


if TYPE_CHECKING:
    from agentpool.agents.base_agent import BaseAgent
    from agentpool.agents.native_agent import Agent as NativeAgent
    from agentpool.delegation.pool import AgentPool
    from agentpool.host.context import HostContext
    from agentpool.host.registry import AgentRegistry
    from agentpool.models.agents import NativeAgentConfig
    from agentpool.models.manifest import AgentsManifest, AnyAgentConfig
    from agentpool.orchestrator.session_controller import SessionState


logger = get_logger(__name__)


class AgentFactory:
    """Creates per-session agent instances from agent config.

    The factory is constructed with a reference to the ``AgentPool`` and
    provides two methods:

    - ``compile()``: Returns an empty ``AgentRegistry``. Agents are
      created lazily per-session, not upfront.
    - ``create_session_agent()``: Creates a single agent instance for a
      session, handling native child, native main, and non-native paths.

    !!! warning "Lifecycle contract"
        The factory calls ``agent.__aenter__()`` but NEVER calls
        ``agent.__aexit__()``. The caller is responsible for cleanup.
        The factory also does NOT acquire locks or handle caching.
    """

    def __init__(self, pool: AgentPool[Any]) -> None:
        """Initialize the factory with a pool reference.

        Args:
            pool: The AgentPool instance that owns this factory.
        """
        self._pool = pool

    @property
    def pool(self) -> AgentPool[Any]:
        """Return the pool this factory belongs to."""
        return self._pool

    def compile(
        self,
        manifest: AgentsManifest,
        host_context: HostContext,
    ) -> AgentRegistry:
        """Compile agents from manifest into a registry.

        Returns an empty ``AgentRegistry`` because agents are created
        lazily per-session (via ``create_session_agent()``), not upfront.

        Args:
            manifest: The agents manifest to compile from.
            host_context: The host context providing shared services.

        Returns:
            An empty AgentRegistry.
        """
        from agentpool.host.registry import AgentRegistry

        _ = manifest, host_context  # accepted for future use
        return AgentRegistry()

    async def create_session_agent(
        self,
        agent_name: str,
        session_id: str,
        host_context: HostContext,
        session: SessionState,
        cfg: AnyAgentConfig,
        input_provider: Any | None = None,
        parent_agent: BaseAgent[Any, Any] | None = None,
    ) -> BaseAgent[Any, Any]:
        """Create a per-session agent from config.

        Extracts the three creation paths from
        ``SessionController.get_or_create_session_agent()``:

        - **Path A** (native child): ``cfg`` is ``NativeAgentConfig`` and
          ``session.parent_session_id`` is set. Inherits env, filesystem,
          MCP snapshot, and ACP transports from ``parent_agent``.
        - **Path B** (native main): ``cfg`` is ``NativeAgentConfig`` and
          no parent session. Builds MCP snapshot from agent's own configs.
        - **Path C** (non-native): ``cfg`` is not ``NativeAgentConfig``.
          Builds MCP snapshot manually from pool and agent configs.

        The lifecycle config from ``cfg.lifecycle`` is passed to the
        agent via ``agent._lifecycle_config`` so the agent's RunLoop
        can use durable dimensions when configured.

        Args:
            agent_name: Name of the agent to create.
            session_id: Unique session identifier.
            host_context: Host context with shared services.
            session: Session state for this session.
            cfg: The resolved agent config.
            input_provider: Optional input provider for elicitation.
            parent_agent: Parent agent for child sessions.

        Returns:
            The created agent instance (already entered via __aenter__).
        """
        from agentpool.models.agents import NativeAgentConfig

        # Fix config name if missing.
        if cfg.name is None:
            cfg = cfg.model_copy(update={"name": agent_name})

        if isinstance(cfg, NativeAgentConfig):
            if session.parent_session_id:
                agent = await self._create_native_child(
                    agent_name=agent_name,
                    session_id=session_id,
                    host_context=host_context,
                    session=session,
                    cfg=cfg,
                    input_provider=input_provider,
                    parent_agent=parent_agent,
                )
            else:
                agent = await self._create_native_main(
                    agent_name=agent_name,
                    session_id=session_id,
                    host_context=host_context,
                    session=session,
                    cfg=cfg,
                    input_provider=input_provider,
                )
        else:
            agent = await self._create_non_native(
                agent_name=agent_name,
                session_id=session_id,
                host_context=host_context,
                session=session,
                cfg=cfg,
                input_provider=input_provider,
            )

        # Pass lifecycle config from agent config to the agent instance
        # so the RunLoop can create durable dimensions when configured.
        agent._lifecycle_config = cfg.lifecycle
        return agent

    async def _create_native_child(
        self,
        *,
        agent_name: str,
        session_id: str,
        host_context: HostContext,
        session: SessionState,
        cfg: NativeAgentConfig,
        input_provider: Any | None,
        parent_agent: BaseAgent[Any, Any] | None,
    ) -> BaseAgent[Any, Any]:
        """Create a native child session agent inheriting from parent.

        Path A: Inherits env, filesystem, MCP snapshot, and ACP
        transports from the parent agent. Model is NOT inherited.
        """
        from agentpool_config.context import ConfigContextManager

        with ConfigContextManager(host_context.config_file_path):
            agent: NativeAgent[Any, Any] = cfg.get_agent(
                input_provider=input_provider,
                pool=host_context.pool,
            )

        # Preserve runtime resources from parent agent.
        # Model is NOT inherited — each agent uses its own configured model.
        if parent_agent is not None:
            if parent_agent.env is not None:
                agent.env = parent_agent.env
            agent._internal_fs = parent_agent._internal_fs

        await agent.__aenter__()

        # MCP snapshot strategy A: INHERIT from parent.
        from agentpool.agents.native_agent import Agent as _NativeAgent
        from agentpool.mcp_server.config_snapshot import (
            McpConfigSnapshot,
        )

        parent_snapshot: McpConfigSnapshot | None = None
        if (
            parent_agent is not None
            and isinstance(parent_agent, _NativeAgent)
            and session.parent_session_id
        ):
            parent_ctx = parent_agent.mcp._session_contexts.get(
                session.parent_session_id,
            )
            parent_snapshot = parent_ctx.snapshot if parent_ctx is not None else None

        snapshot = McpConfigSnapshot(
            pool_configs=(parent_snapshot.pool_configs if parent_snapshot is not None else ()),
            agent_configs=agent._build_agent_configs(),
            session_configs=(
                parent_snapshot.session_configs if parent_snapshot is not None else ()
            ),
            skill_configs=(),
        )
        child_ctx = agent.mcp.get_or_create_session(session_id)
        agent.mcp.update_session_snapshot(session_id, snapshot)

        # Share pre-created ACP transports from parent.
        if (
            parent_agent is not None
            and isinstance(parent_agent, _NativeAgent)
            and session.parent_session_id
            and child_ctx.connection_pool is not None
        ):
            parent_ctx = parent_agent.mcp._session_contexts.get(
                session.parent_session_id,
            )
            if parent_ctx is not None and parent_ctx.connection_pool is not None:
                await child_ctx.connection_pool.copy_pre_created_transports(
                    parent_ctx.connection_pool,
                )

        # Wire ACP MCP manager from parent.
        if (
            parent_agent is not None
            and isinstance(parent_agent, _NativeAgent)
            and parent_agent.mcp._acp_mcp_manager is not None
        ):
            agent.mcp._acp_mcp_manager = parent_agent.mcp._acp_mcp_manager

        # Add pool-level providers (child path includes aggregating_provider).
        _add_pool_providers(agent, host_context, include_aggregating=True)

        _ = agent_name  # accepted for future logging
        return agent

    async def _create_native_main(
        self,
        *,
        agent_name: str,
        session_id: str,
        host_context: HostContext,
        session: SessionState,
        cfg: NativeAgentConfig,
        input_provider: Any | None,
    ) -> BaseAgent[Any, Any]:
        """Create a native main session agent (no parent).

        Path B: Builds MCP snapshot from the agent's own pool and agent
        configs. Loads conversation history from storage.
        """
        from agentpool_config.context import ConfigContextManager

        with ConfigContextManager(host_context.config_file_path):
            agent: NativeAgent[Any, Any] = cfg.get_agent(
                input_provider=input_provider,
                pool=host_context.pool,
            )

        await agent.__aenter__()

        # Load conversation history from storage.
        try:
            await agent.load_session(session_id)
        except Exception:
            logger.exception(
                "Failed to load session for per-session agent: %s",
                session_id,
            )

        # MCP snapshot strategy B: BUILD from agent.
        from agentpool.mcp_server.config_snapshot import (
            McpConfigSnapshot,
        )

        snapshot = McpConfigSnapshot(
            pool_configs=agent._build_pool_configs(),
            agent_configs=agent._build_agent_configs(),
            session_configs=(),
            skill_configs=(),
        )
        agent.mcp.get_or_create_session(session_id)
        agent.mcp.update_session_snapshot(session_id, snapshot)

        # Add pool-level providers (main path: no aggregating_provider).
        _add_pool_providers(agent, host_context, include_aggregating=False)

        _ = agent_name, session  # accepted for future logging
        return agent

    async def _create_non_native(
        self,
        *,
        agent_name: str,
        session_id: str,
        host_context: HostContext,
        session: SessionState,
        cfg: AnyAgentConfig,
        input_provider: Any | None,
    ) -> BaseAgent[Any, Any]:
        """Create a non-native (ACP, etc.) per-session agent.

        Path C: Builds MCP snapshot manually from pool MCPManager and
        agent config's ``get_mcp_servers()``.
        """
        from agentpool.mcp_server.config_snapshot import (
            McpConfigEntry,
            McpConfigSnapshot,
        )
        from agentpool_config.context import ConfigContextManager

        with ConfigContextManager(host_context.config_file_path):
            agent: BaseAgent[Any, Any] = cfg.get_agent(
                input_provider=input_provider,
                pool=host_context.pool,
            )

        await agent.__aenter__()

        # MCP snapshot strategy C: MANUAL from pool.
        pool_configs: tuple[McpConfigEntry, ...] = ()
        if host_context.pool is not None:
            pool_configs = tuple(
                McpConfigEntry(server_config=s, source="pool")
                for s in host_context.mcp.servers
                if s.enabled
            )
        agent_configs: tuple[McpConfigEntry, ...] = tuple(
            McpConfigEntry(server_config=s, source="agent")
            for s in cfg.get_mcp_servers()
            if s.enabled
        )
        snapshot = McpConfigSnapshot(
            pool_configs=pool_configs,
            agent_configs=agent_configs,
            session_configs=(),
            skill_configs=(),
        )
        agent.mcp.get_or_create_session(session_id)
        agent.mcp.update_session_snapshot(session_id, snapshot)

        # Add pool-level providers (non-native: no aggregating_provider).
        _add_pool_providers(agent, host_context, include_aggregating=False)

        _ = agent_name, session  # accepted for future logging
        return agent


def _add_pool_providers(
    agent: BaseAgent[Any, Any],
    host_context: HostContext,
    *,
    include_aggregating: bool,
) -> None:
    """Add pool-level resource providers to an agent.

    Always adds skills_instruction_provider (if present) and
    skills_tools_provider. When ``include_aggregating`` is True, also
    adds the MCP aggregating provider (child session path only).
    """
    pool = host_context.pool
    if pool is None:
        return
    if host_context.skills_instruction_provider is not None:
        agent.tools.add_provider(host_context.skills_instruction_provider)
    if host_context.skills_tools_provider is not None:
        agent.tools.add_provider(host_context.skills_tools_provider)
    if include_aggregating:
        agent.tools.add_provider(host_context.mcp.get_aggregating_provider())
