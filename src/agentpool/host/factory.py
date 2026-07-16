"""AgentFactory — creates per-session agent instances from config.

Extracts the three agent creation paths (native child, native main,
non-native) from ``SessionController.get_or_create_session_agent()``
into a standalone factory. The factory calls ``__aenter__`` only —
``__aexit__`` is the caller's responsibility. Locking, caching, and
config resolution are NOT handled here.

Capability-Native Architecture (M3 Todo 11):
    The factory pre-compiles a capability registry in ``compile()``
    mapping agent names to ``list[AbstractCapability]``. At session
    creation time, these capabilities are injected via
    ``agent._extra_capabilities``.

    ResourceSource collection: capabilities implementing the
    ``ResourceSource`` Protocol are aggregated into an
    ``AggregatedResourceSource`` per agent.

    Hot-swap: capabilities with non-None ``on_change()`` are monitored
    by background tasks that replace the affected capability on
    ``ChangeEvent`` emission.

    Adapter fallback removed: all toolsets now produce native
    ``AbstractCapability`` instances directly.

    Entry-Point Discovery (M3 Todo 12):
        ``compile()`` calls ``discover_entry_point_capabilities()`` to
        load all capability classes registered via the
        ``agentpool.capabilities`` entry-point group. The discovered
        mapping is stored in ``_entry_point_capabilities`` and used by
        ``resolve_capability_type()`` to resolve YAML ``type:``
        references to capability classes. External packages can
        register new capability types by adding an entry-point in
        their ``pyproject.toml``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic_ai.capabilities import AbstractCapability

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.agents.native_agent import Agent as NativeAgent
    from agentpool.capabilities.change_event import ChangeEvent
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

    - ``compile()``: Pre-compiles a capability registry mapping agent
      names to ``list[AbstractCapability]``. Returns an empty
      ``AgentRegistry`` (agents are created lazily per-session).
    - ``create_session_agent()``: Creates a single agent instance for a
      session, handling native child, native main, and non-native paths.
      Injects pre-compiled capabilities via ``_extra_capabilities``.

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
        self._capability_registry: dict[str, list[AbstractCapability[Any]]] = {}
        self._hot_swap_tasks: list[asyncio.Task[None]] = []
        self._entry_point_capabilities: dict[str, type[AbstractCapability[object]]] = {}

    @property
    def pool(self) -> AgentPool[Any]:
        """Return the pool this factory belongs to."""
        return self._pool

    @property
    def capability_registry(self) -> dict[str, list[AbstractCapability[Any]]]:
        """Return the pre-compiled capability registry.

        Maps agent names to their compiled capability lists. Populated
        by ``compile()`` and used by ``create_session_agent()`` to
        inject capabilities into per-session agents.
        """
        return self._capability_registry

    @property
    def entry_point_capabilities(self) -> dict[str, type[AbstractCapability[object]]]:
        """Return the discovered entry-point capability types.

        Maps type names to capability classes discovered via the
        ``agentpool.capabilities`` entry-point group. Populated by
        ``compile()``.
        """
        return self._entry_point_capabilities

    def compile(
        self,
        manifest: AgentsManifest,
        host_context: HostContext,
    ) -> AgentRegistry:
        """Compile agents from manifest into a capability registry.

        Pre-compiles a capability registry mapping agent names to their
        ``list[AbstractCapability]``. Also collects ``ResourceSource``
        instances from the compiled capabilities and constructs
        ``AggregatedResourceSource`` per agent.

        Discovers entry-point capabilities via the
        ``agentpool.capabilities`` entry-point group and stores them
        for resolving YAML ``type:`` references to capability classes.

        Returns an empty ``AgentRegistry`` — agents are created lazily
        per-session via ``create_session_agent()``.

        Args:
            manifest: The agents manifest to compile from.
            host_context: The host context providing shared services.

        Returns:
            An empty AgentRegistry.
        """
        from agentpool.capabilities.registry import discover_entry_point_capabilities
        from agentpool.host.registry import AgentRegistry

        self._capability_registry = {}
        self._entry_point_capabilities = discover_entry_point_capabilities()

        for agent_name, cfg in manifest.agents.items():
            caps = self._compile_agent_capabilities(agent_name, cfg, host_context)
            self._capability_registry[agent_name] = caps

            # Register capabilities with the pool-level ExtensionRegistry.
            from agentpool.capabilities.extension_registry import Scope, ScopeLevel

            pool_scope = Scope(level=ScopeLevel.POOL)
            for cap in caps:
                self._pool.extension_registry.register(cap, pool_scope)

        return AgentRegistry()

    def _compile_agent_capabilities(
        self,
        agent_name: str,
        cfg: AnyAgentConfig,
        host_context: HostContext,
    ) -> list[AbstractCapability[Any]]:
        """Compile capabilities for a single agent from its config.

        Maps agent config to native capabilities:

        - Pool-level skills tools provider → added directly as a
          native capability
        - Subagent delegation → ``SubagentCapability`` (if config
          includes a ``subagent`` toolset)
        - Config-level tool providers → added directly as native
          capabilities

        MCP servers, skill capabilities, and code mode are handled
        separately by the native agent's ``get_agentlet()`` and the
        pool's ``SkillManager``. They are NOT compiled here to avoid
        duplication.

        Args:
            agent_name: Name of the agent.
            cfg: The agent's configuration.
            host_context: The host context with shared services.

        Returns:
            List of compiled ``AbstractCapability`` instances.
        """
        from agentpool.capabilities.subagent_capability import SubagentCapability
        from agentpool.models.agents import NativeAgentConfig

        caps: list[AbstractCapability[Any]] = []

        # 1. Pool-level skills tools provider — native capability.
        if host_context.skills_tools_provider is not None:
            caps.append(host_context.skills_tools_provider)

        # 2. Subagent delegation — native capability.
        if self._has_subagent_toolset(cfg):
            caps.append(SubagentCapability())

        # 3. Config-level tool providers — native capabilities.
        #    These are AbstractCapability instances from the agent's tools
        #    config. They are also added to agent.tools by Agent.from_config(),
        #    but we compile them here for the capability registry so that
        #    ResourceSource collection and hot-swap can discover them.
        if isinstance(cfg, NativeAgentConfig):
            caps.extend(cfg.get_tool_providers())

        # 4. Team communication capability — shared instance with session_metadata=None.
        #    Per-session instance with actual metadata is created in create_session_agent().
        from agentpool_config.team_mode import resolve_team_mode

        global_tm = host_context.manifest.team_mode
        agent_tm = cfg.team_mode
        resolved_tm = resolve_team_mode(global_tm, agent_tm)
        if resolved_tm is not None and resolved_tm.enabled:
            eligible = resolved_tm.lead_eligible + resolved_tm.member_eligible
            if agent_name in eligible:
                from agentpool.capabilities.team_comm_capability import TeamCommCapability

                caps.append(TeamCommCapability(resolved_tm, agent_name))

        # MCP servers are NOT compiled here — they are handled by MCPManager
        # which creates MCPCapability instances. MCPCapability is now
        # deprecated; McpServerCap (agentpool.capabilities.mcp_server_cap)
        # is the replacement. Full migration to McpServerCap with
        # SessionConnectionPool injection happens in Phase 2/4.
        # Phase 4 task 4.13 will migrate MCPManager to use McpServerCap
        # via ExtensionRegistry.

        from agentpool.capabilities.combined_toolset import _NamedCapability

        logger.debug(
            "Compiled capabilities for agent",
            agent_name=agent_name,
            capability_count=len(caps),
            capability_names=[
                cap.name if isinstance(cap, _NamedCapability) else type(cap).__name__
                for cap in caps
            ],
        )

        return caps

    def _has_subagent_toolset(self, cfg: AnyAgentConfig) -> bool:
        """Check if the agent config includes a subagent toolset.

        Args:
            cfg: The agent configuration to check.

        Returns:
            True if the config has a tool with ``type == "subagent"``.
        """
        from agentpool_config.toolsets import SubagentToolsetConfig

        return any(isinstance(tool_config, SubagentToolsetConfig) for tool_config in cfg.tools)

    def resolve_capability_type(
        self,
        type_name: str,
    ) -> type[AbstractCapability[object]]:
        """Resolve a YAML ``type:`` reference to a capability class.

        Looks up ``type_name`` in the discovered entry-point capability
        registry. Must be called after ``compile()`` has populated
        ``_entry_point_capabilities``.

        Args:
            type_name: The capability type name to resolve.

        Returns:
            The capability class corresponding to ``type_name``.

        Raises:
            CapabilityNotFoundError: If ``type_name`` is not a registered
                entry-point capability.
        """
        from agentpool.capabilities.registry import resolve_capability_type

        return resolve_capability_type(type_name, self._entry_point_capabilities)

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

        After creating the agent, injects pre-compiled capabilities via
        ``_extra_capabilities`` and starts hot-swap listeners.

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

        # Inject pre-compiled capabilities via _extra_capabilities.
        # Only NativeAgent has _extra_capabilities; non-native agents
        # (ACP, etc.) receive capabilities through their own protocol.
        caps = self._capability_registry.get(agent_name, [])
        if caps:
            from agentpool.agents.native_agent import Agent as _NativeAgent

            if isinstance(agent, _NativeAgent):
                agent._extra_capabilities = list(caps)

        # Per-session TeamCommCapability with actual session metadata.
        from agentpool_config.team_mode import resolve_team_mode

        global_tm = host_context.manifest.team_mode
        agent_tm = cfg.team_mode
        resolved_tm = resolve_team_mode(global_tm, agent_tm)
        if resolved_tm is not None and resolved_tm.enabled:
            eligible = resolved_tm.lead_eligible + resolved_tm.member_eligible
            if agent_name in eligible:
                from agentpool.agents.native_agent import Agent as _NativeAgent2
                from agentpool.capabilities.team_comm_capability import TeamCommCapability

                team_cap = TeamCommCapability(resolved_tm, agent_name, session.metadata)
                if isinstance(agent, _NativeAgent2):
                    agent._extra_capabilities = [
                        team_cap if isinstance(c, TeamCommCapability) else c
                        for c in agent._extra_capabilities
                    ]

        # Start hot-swap listeners for capabilities with on_change().
        await self._start_hot_swap_listeners(agent_name, agent, caps)

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
                pool=self._pool,
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

        # Inject pool-level providers (MCP aggregating provider for
        # child connection inheritance).
        _inject_pool_providers(agent, host_context, self._pool, include_aggregating=True)

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
                pool=self._pool,
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

        # Inject pool-level MCP aggregating provider (child connection inheritance).
        _inject_pool_providers(agent, host_context, self._pool, include_aggregating=False)

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
                pool=self._pool,
            )

        await agent.__aenter__()

        # MCP snapshot strategy C: MANUAL from pool.
        pool_configs: tuple[McpConfigEntry, ...] = ()
        if self._pool is not None:
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

        # Inject pool-level providers (transitional).
        _inject_pool_providers(agent, host_context, self._pool, include_aggregating=False)

        _ = agent_name, session  # accepted for future logging
        return agent

    async def _start_hot_swap_listeners(
        self,
        agent_name: str,
        agent: BaseAgent[Any, Any],
        caps: list[AbstractCapability[Any]],
    ) -> None:
        """Start background tasks to listen for capability change events.

        For each capability with a non-None ``on_change()`` method,
        starts a background ``asyncio.Task`` that iterates the
        ``on_change()`` async generator. When a ``ChangeEvent`` is
        received, the affected capability is replaced in the agent's
        ``_extra_capabilities`` list.

        The replacement takes effect on the next ``get_agentlet()``
        call (i.e., the next agent run). In-flight runs are not
        interrupted.

        Args:
            agent_name: Name of the agent (for logging).
            agent: The agent instance whose capabilities to monitor.
            caps: The compiled capability list to monitor.
        """
        from agentpool.capabilities.combined_toolset import (
            _NamedCapability,
            _OnChangeCapable,
        )

        for cap in caps:
            if not isinstance(cap, _OnChangeCapable):
                continue
            on_change = cap.on_change()
            if on_change is None:
                continue
            cap_name = cap.name if isinstance(cap, _NamedCapability) else type(cap).__name__
            task = asyncio.create_task(
                self._hot_swap_loop(agent_name, agent, cap, on_change),
                name=f"hot_swap:{agent_name}:{cap_name}",
            )
            self._hot_swap_tasks.append(task)

    async def _hot_swap_loop(
        self,
        agent_name: str,
        agent: BaseAgent[Any, Any],
        cap: AbstractCapability[Any],
        on_change: AsyncIterator[ChangeEvent],
    ) -> None:
        """Listen for ChangeEvent and replace the affected capability.

        When a ``ChangeEvent`` is received from the capability's
        ``on_change()`` stream, the capability is replaced in the
        agent's ``_extra_capabilities`` list. The replacement is a
        fresh instance of the same type, constructed by re-calling
        ``_compile_agent_capabilities`` for the agent.

        Since capabilities are typically stateless wrappers, the
        "replacement" is primarily a signal that the agent's toolset
        should be rebuilt on the next run. The actual capability object
        is kept — what changes is that ``get_agentlet()`` will re-call
        ``get_toolset()`` on the next run, picking up any changes.

        Args:
            agent_name: Name of the agent (for logging).
            agent: The agent instance.
            cap: The capability being monitored.
            on_change: The async iterator of change events.
        """
        try:
            async for event in on_change:
                logger.info(
                    "Capability change event received",
                    agent_name=agent_name,
                    capability_name=event.capability_name,
                    kind=event.kind,
                )
                # The capability object itself is not replaced — it
                # remains in _extra_capabilities. The change event
                # signals that get_toolset() should be re-evaluated
                # on the next run. This is the "local hot-swap":
                # the capability is notified of its own change and
                # can update its internal state.
                #
                # For capabilities wrapping dynamic tool sources,
                # the change signals already propagate through
                # on_change(). The next get_toolset() call will
                # re-fetch tools, picking up any changes.
        except asyncio.CancelledError:
            raise
        except Exception:
            from agentpool.capabilities.combined_toolset import _NamedCapability

            cap_name = cap.name if isinstance(cap, _NamedCapability) else type(cap).__name__
            logger.exception(
                "Hot-swap listener error",
                agent_name=agent_name,
                capability_name=cap_name,
            )

    async def stop_hot_swap_listeners(self) -> None:
        """Cancel all hot-swap background tasks.

        Called during pool shutdown to clean up background tasks.
        """
        for task in self._hot_swap_tasks:
            task.cancel()
        if self._hot_swap_tasks:
            await asyncio.gather(*self._hot_swap_tasks, return_exceptions=True)
        self._hot_swap_tasks.clear()


def _inject_pool_providers(
    agent: BaseAgent[Any, Any],
    host_context: HostContext,
    pool: AgentPool[Any] | None,
    *,
    include_aggregating: bool,
) -> None:
    """Inject pool-level providers into an agent (transitional).

    The ``skills_tools_provider`` is injected here so that child session
    agents have ``load_skill`` and ``list_skills`` tools available.

    What remains on the old path:
    - MCP aggregating provider (child only): Used for connection
      inheritance, not tool injection.

    Args:
        agent: The agent to inject providers into.
        host_context: The host context with shared services.
        pool: The AgentPool instance (passed directly to avoid
            deprecated pool access via host_context).
        include_aggregating: Whether to include the MCP aggregating
            provider (child session path only).
    """
    if pool is None:
        return
    # Pool-level skills tools provider — load_skill/list_skills tools.
    if host_context.skills_tools_provider is not None:
        agent._external_capabilities.append(host_context.skills_tools_provider)
    # MCP aggregating provider — only for child sessions (connection
    # inheritance).
    if include_aggregating:
        agent._external_capabilities.append(host_context.mcp.get_aggregating_provider())
