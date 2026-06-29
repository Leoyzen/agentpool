"""Agent pool management for collaboration."""

from __future__ import annotations

from asyncio import Lock
from contextlib import AsyncExitStack, asynccontextmanager, suppress
import os
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Self

from anyenv import ProcessManager
import anyio
from upathtools import to_upath

from agentpool.delegation.message_flow_tracker import MessageFlowTracker
from agentpool.log import get_logger
from agentpool.resource_providers.aggregating import AggregatingResourceProvider
from agentpool.resource_providers.local import LocalResourceProvider
from agentpool.skills.command_registry import SkillCommandRegistry
from agentpool.skills.uri_resolver import SkillURIResolver
from agentpool.talk.registry import ConnectionRegistry
from agentpool.tasks import TaskRegistry


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import TracebackType

    from upathtools import JoinablePathLike, UPath

    from agentpool.common_types import AnyEventHandlerType
    from agentpool.messaging.compaction import CompactionPipeline
    from agentpool.models.manifest import AgentsManifest, AnyAgentConfig
    from agentpool.orchestrator import SessionPool
    from agentpool.orchestrator.run import RunHandle
    from agentpool.resource_providers.base import ResourceProvider
    from agentpool.ui.base import InputProvider
    from agentpool_config.session_pool import SessionPoolConfig
    from agentpool_config.task import Job


logger = get_logger(__name__)


class AgentPool[TPoolDeps = None]:
    """Configuration store and service manager for agent orchestration.

    Manages agent configurations, shared dependencies, MCP servers,
    skills, storage, and session orchestration. This is a pure config
    store — no agent instances are created at the pool level. Agents
    are defined in YAML config and instantiated on a per-session basis
    by ``SessionPool``, which is the exclusive execution path.

    Config metadata APIs:
    - ``main_agent_name``: Resolved main agent name from config
    - ``main_agent_config``: Main agent's ``AnyAgentConfig``
    - ``agent_configs``: All agent configs from the manifest
    - ``get_agent_display_name()``: Display name for a configured agent
    """

    def __init__(  # noqa: PLR0915
        self,
        manifest: JoinablePathLike | AgentsManifest | None = None,
        *,
        shared_deps_type: type[TPoolDeps] | None = None,
        connect_nodes: bool = True,
        input_provider: InputProvider | None = None,
        parallel_load: bool = True,
        event_handlers: list[AnyEventHandlerType] | None = None,
        main_agent_name: str | None = None,
        session_pool_config: SessionPoolConfig | None = None,
        **kwargs: Any,
    ):
        """Initialize agent pool with configuration loading.

        Args:
            manifest: Agent configuration manifest
            shared_deps_type: Dependencies to share across all nodes
            connect_nodes: Whether to set up forwarding connections
            input_provider: Input provider for tool / step confirmations / HumanAgents
            parallel_load: Whether to load nodes in parallel (async)
            event_handlers: Event handlers to pass through to all agents
            main_agent_name: Name of the main agent (overrides manifest.default_agent)
            session_pool_config: Optional override for SessionPool configuration
            **kwargs: Additional keyword arguments (e.g., deprecated options).

        Raises:
            ValueError: If manifest contains invalid configurations
        """
        from agentpool.mcp_server.manager import MCPManager
        from agentpool.models.manifest import AgentsManifest
        from agentpool.observability import registry
        from agentpool.prompts.manager import PromptManager
        from agentpool.resource_providers.skills_instruction import SkillsInstructionProvider
        from agentpool.skills.manager import SkillsManager
        from agentpool.storage import StorageManager
        from agentpool.utils.streams import FileOpsTracker
        from agentpool.utils.todos import TodoTracker
        from agentpool.vfs_registry import VFSRegistry
        from agentpool_config.context import ConfigContextManager
        from agentpool_toolsets.builtin.debug import install_memory_handler

        # Determine config path first, then load everything with context
        config_path: UPath | None = None
        manifest_obj: AgentsManifest | None = None
        path_for_loading: UPath | None = None

        match manifest:
            case None:
                manifest_obj = AgentsManifest()
            case str() | os.PathLike() as path:
                config_path = to_upath(path)
                path_for_loading = config_path
            case AgentsManifest():
                manifest_obj = manifest
                if manifest_obj.config_file_path is not None:
                    config_path = to_upath(manifest_obj.config_file_path)
            case _:
                raise ValueError(f"Invalid config type: {type(manifest)}")

        # Set up context manager if we have a config file path
        # This enables config-relative path resolution during manifest loading
        logger.debug(
            "AgentPool.__init__: config_path=%s, creating ConfigContextManager", config_path
        )
        with ConfigContextManager(config_path):
            if manifest_obj is None:
                manifest_obj = AgentsManifest.from_file(path_for_loading)  # type: ignore[arg-type]
            logger.debug(
                "AgentPool.__init__: after manifest load, agents=%s",
                list(manifest_obj.agents.keys()),
            )
            for name, cfg in manifest_obj.agents.items():
                logger.debug(
                    "AgentPool.__init__: agent %s config_file_path=%s",
                    name,
                    getattr(cfg, "config_file_path", "N/A"),
                )

            self._config_file_path = config_path
            self.manifest = manifest_obj

            # Validate forward target references before any runtime work.
            # Previously handled by _connect_nodes which was removed along
            # with pool-level agent creation; this lightweight check preserves
            # the "Forward target .* not found" ValueError contract.
            agent_names = set(manifest_obj.agents.keys())
            for agent_name, agent_cfg in manifest_obj.agents.items():
                for conn in agent_cfg.connections:
                    if hasattr(conn, "name") and conn.name not in agent_names:
                        raise ValueError(f"Forward target {conn.name} not found")

            registry.configure_observability(self.manifest.observability)
            self._memory_log_handler = install_memory_handler()
            self.shared_deps_type = shared_deps_type
            self.connect_nodes = connect_nodes
            self._input_provider = input_provider
            self.exit_stack = AsyncExitStack()
            self.parallel_load = parallel_load
            self.storage = StorageManager(self.manifest.storage)
            self.storage._model_variants = self.manifest.model_variants
            self.vfs_registry = VFSRegistry()
            for name, resource_config in self.manifest.resources.items():
                self.vfs_registry.register_from_config(name, resource_config)
            session_store = self.manifest.storage.get_session_store()
            self._session_store = session_store
            self.event_handlers = event_handlers or []
            self.connection_registry = ConnectionRegistry()
            servers = self.manifest.get_mcp_servers()
            self.mcp = MCPManager(name="pool_mcp", servers=servers, owner="pool", _warn=False)
            self.skills = SkillsManager(
                name="local",
                owner="pool",
                config=self.manifest.skills,
                config_file_path=self._config_file_path,
            )
            self.skills_instruction_provider = SkillsInstructionProvider(
                skills_registry=self.skills.registry,
                injection_mode=self.manifest.skills.instruction.mode,
                max_skills=self.manifest.skills.instruction.max_skills,
                owner="pool",
            )
            from agentpool_toolsets.builtin.skills import SkillsTools

            self.skills_tools_provider = SkillsTools(
                injection_mode=self.manifest.skills.instruction.mode,
                max_skills=self.manifest.skills.instruction.max_skills,
            )
            self._tasks = TaskRegistry()
            self._skill_commands: SkillCommandRegistry | None = None
            self._skill_resolver: SkillURIResolver | None = None
            self._skill_provider: AggregatingResourceProvider | None = None
            self._skill_mcp_manager: Any | None = None  # SkillMcpManager — pool-scoped
            self._skill_tool_manager: Any | None = None  # SkillToolManager — pool-scoped
            self._skill_capabilities: list[Any] = []  # SkillCapability instances
            skill_scopes = getattr(self.manifest, "model_extra", None) or {}
            raw_skill_scopes = skill_scopes.get("_skill_scopes", {})
            self._default_skill_scope = str(raw_skill_scopes.get("default_scope", "host"))
            self._node_skill_scopes = {
                str(name): str(scope) for name, scope in raw_skill_scopes.get("nodes", {}).items()
            }
            self._skill_scope_paths = tuple(
                (str(item.get("scope", self._default_skill_scope)), str(item.get("path", "")))
                for item in raw_skill_scopes.get("paths", [])
                if isinstance(item, dict) and item.get("path")
            )
            self.prompt_manager = PromptManager(self.manifest.prompts)
            # Main agent name: explicit param > manifest.default_agent > None (will use first)
            self._main_agent_name = main_agent_name or self.manifest.default_agent
            # Register tasks from manifest
            for name, task in self.manifest.jobs.items():
                self._tasks.register(name, task)
            self.process_manager = ProcessManager()
            self.file_ops = FileOpsTracker()
            self.todos = TodoTracker()
            self._enter_lock = Lock()  # Initialize async safety fields
            self._running_count = 0
            if "enable_session_pool" in kwargs:
                import warnings

                warnings.warn(
                    "enable_session_pool is deprecated and ignored. SessionPool is always enabled.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                kwargs.pop("enable_session_pool")
            self._session_pool_config = session_pool_config or self.manifest.session_pool
            self._session_pool: SessionPool | None = None
            self._protocol_servers: list[Any] = []
            # Graph topology attributes preserved for future re-implementation
            self._graph: Any | None = None
            self._graph_config: Any | None = None

    async def __aenter__(self) -> Self:
        """Enter async context and initialize all agents."""
        if self._running_count > 0:
            self._running_count += 1
            return self
        async with self._enter_lock:
            try:
                # Initialize MCP manager first, then add aggregating provider
                await self.exit_stack.enter_async_context(self.mcp)
                await self.exit_stack.enter_async_context(self.skills)
                # Initialize skill provider and resolver BEFORE skill command registry
                # so that skill_provider is available when syncing commands
                await self._setup_skills_provider()
                # Link skill provider to instruction provider so MCP skills are included
                self.skills_instruction_provider.skill_provider = self._skill_provider
                # Initialize skill command registry after skill provider is set up
                self._skill_commands = SkillCommandRegistry(
                    skills_registry=self.skills.registry,
                    skill_provider=self._skill_provider,
                )
                await self._skill_commands.initialize()
                # Create pool-scoped SkillCapability instances for all discovered skills
                await self._rebuild_skill_capabilities()
                if self.skills_instruction_provider:
                    await self.exit_stack.enter_async_context(self.skills_instruction_provider)
                # Initialize storage and sessions sequentially (they share the same DB)
                await self.exit_stack.enter_async_context(self.storage)
                if self._session_store is not None:
                    await self.exit_stack.enter_async_context(self._session_store)
                # Initialize SessionPool
                from agentpool.orchestrator import SessionPool

                cfg = self._session_pool_config
                self._session_pool = SessionPool(
                    pool=self,
                    store=self._session_store,
                    enable_auto_resume=cfg.enable_auto_resume,
                    enable_event_bus=cfg.enable_event_bus,
                    max_auto_resume=cfg.max_auto_resume,
                )
                # Configure additional SessionPool settings
                self._session_pool.sessions._session_ttl_seconds = cfg.session_ttl_seconds
                self._session_pool.sessions._mcp_max_processes = cfg.mcp_max_processes
                self._session_pool.event_bus._max_queue_size = cfg.max_queue_size
                await self._session_pool.start()

            except Exception as e:
                await self.cleanup()
                msg = "Failed to initialize agent pool"
                logger.exception(msg, exc_info=e)
                raise RuntimeError(msg) from e
        self._running_count += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context."""
        if self._running_count == 0:
            raise ValueError("AgentPool.__aexit__ called more times than __aenter__")
        async with self._enter_lock:
            self._running_count -= 1
            if self._running_count == 0:
                # Stop all protocol server event consumers first
                await self._stop_all_consumers()
                # Shutdown SessionPool
                assert self._session_pool is not None
                await self._session_pool.shutdown()
                # Await any in-flight checkpoint operations before cleanup
                await self._session_pool._await_inflight_checkpoints()
                self._session_pool = None
                # Clean up skill provider and resolver
                if self._skill_provider is not None:
                    self._skill_provider.skills_changed.disconnect(self._on_skills_changed)
                    self._skill_provider = None
                self._skill_resolver = None
                await self.cleanup()

    def add_server(self, server: Any) -> None:
        """Register a protocol server for consumer lifecycle management.

        Args:
            server: A protocol server instance (e.g. ACPServer, OpenCodeServer).
        """
        self._protocol_servers.append(server)

    async def _stop_all_consumers(self) -> None:
        """Stop all event consumers from registered protocol servers.

        Called during AgentPool.__aexit__ before SessionPool shutdown
        to ensure graceful consumer teardown.
        """
        for server in self._protocol_servers:
            stop_fn = getattr(server, "stop_event_consumers", None)
            if stop_fn is not None:
                await stop_fn()
        self._protocol_servers.clear()

    @property
    def is_running(self) -> bool:
        """Check if the agent pool is running."""
        return bool(self._running_count)

    @property
    def session_pool(self) -> SessionPool | None:
        """Get the active SessionPool.

        Returns the SessionPool instance when the pool is running,
        or None if not yet entered.
        """
        return self._session_pool

    @property
    def sessions(self) -> SessionPool | Any:
        """Deprecated: use session_pool instead.

        Returns the SessionPool instance when available.
        """
        return self._session_pool

    @sessions.setter
    def sessions(self, value: Any) -> None:
        """Setter for test compatibility."""
        from agentpool.orchestrator import SessionPool

        if isinstance(value, SessionPool):
            self._session_pool = value

    async def create_session(
        self,
        session_id: str,
        agent_name: str | None = None,
        **metadata: Any,
    ) -> Any:
        """Create or get a session through the SessionPool.

        Convenience method that delegates to the SessionPool's
        create_session method.

        Args:
            session_id: Unique identifier for the session.
            agent_name: Name of the agent to associate with the session.
            **metadata: Arbitrary metadata to attach to the session.

        Returns:
            The session state from the SessionPool.
        """
        assert self._session_pool is not None
        return await self._session_pool.create_session(session_id, agent_name, **metadata)

    def list_active_runs(self) -> list[RunHandle]:
        """List all currently active runs.

        Returns:
            List of active run handles, or empty list if no session pool.
        """
        if self._session_pool is None:
            return []
        return self._session_pool.active_runs

    def cancel_run(self, run_id: str) -> None:
        """Cancel an active run by its ID.

        Args:
            run_id: The run identifier to cancel.

        Raises:
            RuntimeError: If no session pool is available.
            ValueError: If no active run with the given ID exists.
        """
        if self._session_pool is None:
            raise RuntimeError("No session pool available")
        self._session_pool.cancel_run(run_id)

    def get_run(self, run_id: str) -> RunHandle | None:
        """Get a handle for an active run by its ID.

        Args:
            run_id: The run identifier to look up.

        Returns:
            The run handle if found and still active, otherwise None.
        """
        if self._session_pool is None:
            return None
        return self._session_pool.get_run(run_id)

    @property
    def skill_commands(self) -> SkillCommandRegistry | None:
        """Get the skill command registry.

        Returns the SkillCommandRegistry when skills are configured,
        or None if no skills are available.
        """
        return self._skill_commands

    @property
    def skill_resolver(self) -> SkillURIResolver | None:
        """Get the skill URI resolver.

        Returns the SkillURIResolver for resolving skill:// URIs,
        or None if skills provider is not initialized.
        """
        return self._skill_resolver

    @property
    def skill_provider(self) -> AggregatingResourceProvider | None:
        """Get the aggregating skill resource provider.

        Returns the AggregatingResourceProvider that combines all skill sources
        (local filesystem and MCP servers), or None if not initialized.
        """
        return self._skill_provider

    def register_skill_provider(self, provider: ResourceProvider) -> None:
        """Register a skill provider dynamically.

        Adds the provider to the aggregator and URI resolver so that its skills
        become visible to SkillsInstructionProvider and load_skill.
        If called before _setup_skills_provider(), the provider is buffered
        and added when the aggregator is created.

        Args:
            provider: The resource provider to register
        """
        if self._skill_provider is not None:
            self._skill_provider.add_provider(provider)
        else:
            if not hasattr(self, "_pending_skill_providers"):
                self._pending_skill_providers: list[ResourceProvider] = []
            self._pending_skill_providers.append(provider)

        if self._skill_resolver is not None:
            self._skill_resolver.register_provider(provider.name, provider)

    def unregister_skill_provider(self, provider: ResourceProvider) -> None:
        """Unregister a previously registered skill provider.

        Removes the provider from the aggregator and URI resolver.

        Args:
            provider: The resource provider to unregister
        """
        if self._skill_provider is not None:
            self._skill_provider.remove_provider(provider)

        if self._skill_resolver is not None:
            self._skill_resolver.unregister_provider(provider.name)

        pending: list[ResourceProvider] = getattr(self, "_pending_skill_providers", [])
        if pending:
            with suppress(ValueError):
                pending.remove(provider)

    def skill_scope_for_node(self, node_name: str | None) -> str:
        """Return the package-level skill scope for a node."""
        if node_name is None:
            return self._default_skill_scope
        return self._node_skill_scopes.get(node_name, self._default_skill_scope)

    def skill_scope_for_skill(self, skill: Any) -> str:
        """Return the package-level skill scope for a skill."""
        skill_path = getattr(skill, "skill_path", None)
        if skill_path is None or type(skill_path) is PurePosixPath:
            return self._default_skill_scope

        normalized_skill_path = self._normalize_skill_scope_path(skill_path)
        for scope, base_path in self._skill_scope_paths:
            normalized_base_path = self._normalize_skill_scope_path(base_path)
            if normalized_skill_path == normalized_base_path or normalized_skill_path.startswith(
                f"{normalized_base_path}/"
            ):
                return scope
        return self._default_skill_scope

    def is_skill_visible_to_node(self, skill: Any, node_name: str | None) -> bool:
        """Return whether a skill is visible to a node's package scope."""
        return self.skill_scope_for_skill(skill) == self.skill_scope_for_node(node_name)

    async def get_skill_instructions_for_node(self, skill_name: str, node_name: str) -> str:
        """Load skill instructions using a target node's package scope."""
        from agentpool.skills.exceptions import SkillNotFoundError

        if self._skill_resolver is not None:
            skill = await self._skill_resolver.resolve(skill_name)
            if not self.is_skill_visible_to_node(skill, node_name):
                raise SkillNotFoundError(skill_name)
            if type(skill.skill_path) is PurePosixPath:
                if self._skill_provider is None:
                    raise SkillNotFoundError(skill_name)
                return await self._skill_provider.get_skill_instructions(skill.name)
            return skill.load_instructions()

        if self._skill_provider is None:
            raise SkillNotFoundError(skill_name)

        for skill in await self._skill_provider.get_skills():
            if skill.name == skill_name and self.is_skill_visible_to_node(skill, node_name):
                return await self._skill_provider.get_skill_instructions(skill.name)
        raise SkillNotFoundError(skill_name)

    @staticmethod
    def _normalize_skill_scope_path(path: Any) -> str:
        try:
            raw_path = os.fspath(path)
        except TypeError:
            raw_path = str(path)
        return os.path.normcase(str(Path(raw_path).resolve())).replace("\\", "/").rstrip("/")

    async def _setup_skills_provider(self) -> None:
        """Initialize the skill provider and resolver.

        Creates an AggregatingResourceProvider that combines:
        - LocalResourceProvider for filesystem skills (from SkillsManager)
        - MCPResourceProvider for each MCP server in the pool

        Also creates a SkillURIResolver and registers all providers.
        Connects the skills_changed signal to _on_skills_changed callback.
        """
        providers: list[ResourceProvider] = []

        # Add LocalResourceProvider for filesystem skills if SkillsManager exists
        # Use the already-initialized resource_provider from SkillsManager
        if self.skills and self.skills.registry.skills_dirs:
            try:
                local_provider = self.skills.resource_provider
                providers.append(local_provider)
            except RuntimeError:
                # Fallback: create and initialize a new provider if resource_provider not available
                local_provider = LocalResourceProvider(
                    name="local",
                    skills_dirs=list(self.skills.registry.skills_dirs),
                )
                await local_provider.__aenter__()
                providers.append(local_provider)

        # Add MCPResourceProvider for each MCP server
        for mcp_provider in self.mcp.providers:
            providers.append(mcp_provider)

        # Create aggregating provider
        self._skill_provider = AggregatingResourceProvider(
            providers=providers,
            name="skills",
        )

        # Create skill URI resolver and register providers
        self._skill_resolver = SkillURIResolver()
        for provider in providers:
            self._skill_resolver.register_provider(provider.name, provider)

        # Connect skills_changed signal to callback
        self._skill_provider.skills_changed.connect(self._on_skills_changed)

        # Drain any pending skill providers that were registered before setup
        pending: list[ResourceProvider] = getattr(self, "_pending_skill_providers", [])
        if pending:
            for provider in pending:
                self._skill_provider.add_provider(provider)
                self._skill_resolver.register_provider(provider.name, provider)
            self._pending_skill_providers.clear()

    async def _rebuild_skill_capabilities(self) -> None:
        """Rebuild SkillCapability instances from currently discovered skills.

        Creates pool-scoped SkillMcpManager and SkillToolManager on first call,
        then creates a SkillCapability for each skill from SkillsManager.
        Skills with ``disable_model_invocation=True`` are skipped.

        This method is called:
        - During ``__aenter__`` after skill discovery completes.
        - Whenever ``_skill_provider.skills_changed`` fires (dynamic registration).
        """
        from agentpool.skills.capability import SkillCapability
        from agentpool.skills.skill_mcp_manager import SkillMcpManager
        from agentpool.skills.skill_tool_manager import SkillToolManager

        # Create pool-scoped managers on first call
        if self._skill_mcp_manager is None:
            self._skill_mcp_manager = SkillMcpManager()
        if self._skill_tool_manager is None:
            self._skill_tool_manager = SkillToolManager()

        # Build capabilities from SkillsManager (local filesystem skills only —
        # MCP-provided skills are handled separately by MCP capability system)
        capabilities: list[Any] = []
        if self.skills is not None:
            for skill in self.skills.list_skills():
                if skill.disable_model_invocation:
                    continue
                cap = SkillCapability(
                    skill,
                    self._skill_mcp_manager,
                    self._skill_tool_manager,
                )
                capabilities.append(cap)

        self._skill_capabilities = capabilities
        logger.debug(
            "Rebuilt skill capabilities",
            count=len(capabilities),
            skill_names=[c._skill.name for c in capabilities],
        )

    @property
    def skill_capabilities(self) -> list[Any]:
        """Get pool-scoped SkillCapability instances.

        These are created once in ``__aenter__`` and rebuilt on
        dynamic skill registration/unregistration.
        """
        return self._skill_capabilities

    async def _on_skills_changed(self, event: Any) -> None:
        """Handle skills changed events from the skill provider.

        This method is called when the skill provider detects changes to skills
        from any source (local filesystem or MCP servers). Skill command
        registry changes are handled by SkillCommandRegistry which listens to
        ``_skill_provider`` directly. We rebuild skill capabilities to keep
        them in sync with the latest skill list.

        Args:
            event: The resource change event from the provider.
        """
        await self._rebuild_skill_capabilities()

    async def cleanup(self) -> None:
        """Clean up pool resources."""
        # Clean up background processes
        await self.process_manager.cleanup()
        await self.exit_stack.aclose()

    # create_team_run and create_team removed as part of eliminating
    # pool-level agent creation. Teams are now defined in YAML config
    # via the ``graph:`` section instead of being created programmatically.

    @asynccontextmanager
    async def track_message_flow(self) -> AsyncIterator[MessageFlowTracker]:
        """Track message flow during a context."""
        tracker = MessageFlowTracker()
        self.connection_registry.message_flow.connect(tracker.track)
        try:
            yield tracker
        finally:
            self.connection_registry.message_flow.disconnect(tracker.track)

    async def run_event_loop(self) -> None:
        """Run pool in event-watching mode until interrupted."""
        print("Starting event watch mode...")
        print("Press Ctrl+C to stop")

        shutdown_event = anyio.Event()
        with suppress(KeyboardInterrupt):
            await shutdown_event.wait()

    # Runtime agent APIs (get_agents, all_agents, main_agent, teams, nodes, get_agent,
    # add_agent, get_mermaid_diagram, _build_graph, _build_graph_from_config,
    # _resolve_graph_step_ref) removed as part of eliminating pool-level agent creation.
    # Config-only APIs (main_agent_name, main_agent_config, agent_configs,
    # get_agent_display_name) are preserved.

    @property
    def main_agent_name(self) -> str:
        """Get the main agent name.

        Returns the name specified by the ``main_agent_name`` constructor
        parameter, ``manifest.default_agent``, or falls back to the first
        agent name from the manifest.

        This property works without calling ``__aenter__()`` — it only
        reads config data, not runtime agent instances.

        Raises:
            RuntimeError: If no agents are configured.
        """
        if self._main_agent_name:
            return self._main_agent_name
        if self.manifest.agents:
            return next(iter(self.manifest.agents))
        msg = "No agents configured in manifest"
        raise RuntimeError(msg)

    @property
    def main_agent_config(self) -> AnyAgentConfig:
        """Get the main agent configuration model.

        Resolves :meth:`main_agent_name` and returns its config from
        ``self.manifest.agents``.

        This property works without calling ``__aenter__()`` — it only
        reads config data, not runtime agent instances.

        Raises:
            RuntimeError: If no agents are configured.
        """
        name = self.main_agent_name
        config = self.manifest.agents.get(name)
        if config is None:
            available = list(self.manifest.agents.keys())
            msg = f"Main agent {name!r} not found in config. Available: {available}"
            raise RuntimeError(msg)
        return config

    # teams, nodes, get_agent, add_agent, get_mermaid_diagram, _build_graph,
    # _build_graph_from_config, _resolve_graph_step_ref, _load_graph_config removed
    # as part of eliminating pool-level agent creation.

    @property
    def compaction_pipeline(self) -> CompactionPipeline | None:
        """Get the configured compaction pipeline or None if not configured."""
        return self.manifest.get_compaction_pipeline()

    @property
    def agent_configs(self) -> dict[str, AnyAgentConfig]:
        """Get all agent configurations from the manifest.

        Returns a direct reference to the manifest's agents dict, providing
        typed access to configuration metadata (display_name, description,
        model settings, etc.) without needing to know the manifest structure.

        Use ``"agent_name" in pool.agent_configs`` for existence checks.

        Returns:
            Dictionary mapping agent names to their ``AnyAgentConfig``.
        """
        return self.manifest.agents

    def get_agent_display_name(self, name: str) -> str:
        """Get the display name for a configured agent.

        Returns the ``display_name`` from the agent's config if set,
        otherwise falls back to the agent name.

        Args:
            name: The agent name to look up.

        Returns:
            The display name, or the agent name if no display name is configured.

        Raises:
            KeyError: If no agent with the given name exists in the manifest.
        """
        config = self.manifest.agents[name]
        return config.display_name or name

    # Graph-related methods removed as part of eliminating pool-level agent creation.
    # _validate_item, _create_teams, _connect_nodes, _on_registry_changed, _invalidate_graph
    # were all dependent on the BaseRegistry pattern and runtime agent instances.

    # _load_graph_config, _build_graph_from_config, _resolve_graph_step_ref,
    # _build_graph removed as part of eliminating pool-level agent creation.
    # The config-based graph (graph: YAML section) cannot function without
    # runtime agent instances to look up.

    @property
    def graph(self) -> Any:
        """The pool's pydantic-graph topology.

        Graph building was removed as part of eliminating pool-level agent
        creation. Config-based graphs (``graph:`` YAML section) and runtime
        graphs (built from Talk connections) both required runtime agent
        instances to look up.

        Returns:
            Always None in the current implementation.
        """
        return None

    def get_job(self, name: str) -> Job[Any, Any]:
        return self._tasks[name]

    def register_task(self, name: str, task: Job[Any, Any]) -> None:
        self._tasks.register(name, task)

    # get_agent, add_agent, get_mermaid_diagram removed as part of eliminating
    # pool-level agent creation. These methods depended on runtime agent
    # instances (self._items, self.all_agents, self.register).


if __name__ == "__main__":

    async def main() -> None:
        path = "src/agentpool/config_resources/agents.yml"
        async with AgentPool(path) as pool:
            print(f"AgentPool loaded with agents: {list(pool.agent_configs.keys())}")

    anyio.run(main)
