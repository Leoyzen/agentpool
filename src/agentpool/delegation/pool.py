"""Agent pool management for collaboration."""

from __future__ import annotations

import asyncio
from asyncio import Lock
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass, field
import os
from typing import TYPE_CHECKING, Any, Self, overload

from anyenv import ProcessManager
import anyio
from upathtools import to_upath

from agentpool.common_types import NodeName, SupportsStructuredOutput
from agentpool.delegation.message_flow_tracker import MessageFlowTracker
from agentpool.log import get_logger
from agentpool.messaging import MessageNode
from agentpool.resource_providers.aggregating import AggregatingResourceProvider
from agentpool.resource_providers.local import LocalResourceProvider
from agentpool.skills.command_registry import SkillCommandRegistry
from agentpool.skills.uri_resolver import SkillURIResolver
from agentpool.talk import TeamTalk
from agentpool.talk.registry import ConnectionRegistry
from agentpool.tasks import TaskRegistry
from agentpool.utils.baseregistry import BaseRegistry
from agentpool.utils.inspection import get_fn_name


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from contextlib import AbstractAsyncContextManager
    from types import TracebackType
    from typing import Any

    from upathtools import JoinablePathLike, UPath

    from agentpool.agents import Agent
    from agentpool.agents.base_agent import BaseAgent
    from agentpool.common_types import AgentName, AnyEventHandlerType
    from agentpool.delegation.base_team import BaseTeam
    from agentpool.delegation.team import Team
    from agentpool.delegation.teamrun import TeamRun
    from agentpool.messaging.compaction import CompactionPipeline
    from agentpool.models.manifest import AgentsManifest
    from agentpool.orchestrator import SessionPool
    from agentpool.orchestrator.run import RunHandle
    from agentpool.resource_providers.base import ResourceProvider
    from agentpool.ui.base import InputProvider
    from agentpool_config.session_pool import SessionPoolConfig
    from agentpool_config.task import Job


logger = get_logger(__name__)


@dataclass
class _WorkflowGraphState:
    """Shared state for config-based workflow graph execution."""
    prompts: tuple[Any, ...] = field(default_factory=tuple)
    kwargs: dict[str, Any] = field(default_factory=dict)
    result: Any = None


class AgentPool[TPoolDeps = None](BaseRegistry[NodeName, MessageNode[Any, Any]]):
    """Pool managing message processing nodes (agents and teams).

    Acts as a unified registry for all nodes, providing:
    - Centralized node management and lookup
    - Shared dependency injection
    - Connection management
    - Resource coordination

    Nodes can be accessed through:
    - nodes: All registered nodes (agents and teams)
    - agents: Only Agent instances
    - teams: Only Team instances
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
        """Initialize agent pool with immediate agent creation.

        Args:
            manifest: Agent configuration manifest
            shared_deps_type: Dependencies to share across all nodes
            connect_nodes: Whether to set up forwarding connections
            input_provider: Input provider for tool / step confirmations / HumanAgents
            parallel_load: Whether to load nodes in parallel (async)
            event_handlers: Event handlers to pass through to all agents
            main_agent_name: Name of the main agent (overrides manifest.default_agent)
            session_pool_config: Optional override for SessionPool configuration

        Raises:
            ValueError: If manifest contains invalid node configurations
            RuntimeError: If node initialization fails
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

        super().__init__()

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
            self.prompt_manager = PromptManager(self.manifest.prompts)
            # Main agent name: explicit param > manifest.default_agent > None (will use first)
            self._main_agent_name = main_agent_name or self.manifest.default_agent
            # Register tasks from manifest
            for name, task in self.manifest.jobs.items():
                self._tasks.register(name, task)
            self.process_manager = ProcessManager()
            self.file_ops = FileOpsTracker()
            self.todos = TodoTracker()
            # Create all agents from unified manifest.agents dict
            for name, config in self.manifest.agents.items():
                # Ensure name is set on config
                cfg = config.model_copy(update={"name": name}) if config.name is None else config
                agent: BaseAgent[TPoolDeps] = cfg.get_agent(
                    event_handlers=self.event_handlers,
                    input_provider=self._input_provider,
                    pool=self,
                    deps_type=shared_deps_type,
                )
                self.register(name, agent)

            logger.debug("AgentPool: registered %d agents: %s", len(self.all_agents), list(self.all_agents.keys()))
            self._create_teams()
            if connect_nodes:
                self._connect_nodes()
            self.pool_talk = TeamTalk[Any].from_nodes(list(self.nodes.values()))
            self._enter_lock = Lock()  # Initialize async safety fields
            self._running_count = 0
            if "enable_session_pool" in kwargs:
                import warnings

                warnings.warn(
                    "enable_session_pool is deprecated and ignored. "
                    "SessionPool is always enabled.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                kwargs.pop("enable_session_pool")
            self._session_pool_config = session_pool_config or self.manifest.session_pool
            self._session_pool: SessionPool | None = None
            # Graph topology: lazily-built pydantic-graph from registered nodes
            self._graph: Any | None = None
            self._graph_dirty = True
            self._node_id_mapping: dict[Any, MessageNode[Any, Any]] = {}
            self._talk_mapping: dict[tuple[Any, Any], Any] = {}
            # Config-based workflow graph (loaded from YAML graph: section)
            self._graph_config: Any | None = None
            self._load_graph_config(path_for_loading)
            # Invalidate graph when registry changes
            self._items.events.added.connect(self._on_registry_changed)
            self._items.events.removed.connect(self._on_registry_changed)

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
                aggregating_provider = self.mcp.get_aggregating_provider()
                agents = list(self.all_agents.values())
                teams = list(self.teams.values())
                if self.skills_instruction_provider:
                    await self.exit_stack.enter_async_context(self.skills_instruction_provider)
                for agent in agents:
                    if getattr(agent, "inherit_pool_mcp_servers", True):
                        agent.tools.add_provider(aggregating_provider)
                    if self.skills_instruction_provider:
                        agent.tools.add_provider(self.skills_instruction_provider)
                    agent.tools.add_provider(self.skills_tools_provider)
                # Initialize storage and sessions sequentially (they share the same DB)
                await self.exit_stack.enter_async_context(self.storage)
                if self._session_store is not None:
                    await self.exit_stack.enter_async_context(self._session_store)
                # Initialize agents and teams (can be parallel)
                comps: list[AbstractAsyncContextManager[Any]] = [*agents, *teams]
                node_inits = [self.exit_stack.enter_async_context(c) for c in comps]
                if self.parallel_load:
                    await asyncio.gather(*node_inits)
                else:
                    for init in node_inits:
                        await init
                # Build config-based graph if present
                if self._graph_config is not None:
                    try:
                        self._graph = self._build_graph_from_config()
                        self._graph_dirty = False
                    except Exception as exc:
                        config_path_str = (
                            str(self._config_file_path)
                            if self._config_file_path
                            else "programmatic config"
                        )
                        raise RuntimeError(
                            f"Failed to build graph from config at {config_path_str}: {exc}"
                        ) from exc
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
                self._session_pool.turns.event_bus._max_queue_size = cfg.max_queue_size
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
                # Shutdown SessionPool
                assert self._session_pool is not None
                await self._session_pool.shutdown()
                self._session_pool = None
                # Remove MCP aggregating provider from all agents
                aggregating_provider = self.mcp.get_aggregating_provider()
                for agent in self.get_agents().values():
                    if getattr(agent, "inherit_pool_mcp_servers", True):
                        agent.tools.remove_provider(aggregating_provider.name)
                    if self.skills_instruction_provider:
                        agent.tools.remove_provider(self.skills_instruction_provider.name)
                    agent.tools.remove_provider(self.skills_tools_provider.name)
                # Clean up skill provider and resolver
                if self._skill_provider is not None:
                    self._skill_provider.skills_changed.disconnect(self._on_skills_changed)
                    self._skill_provider = None
                self._skill_resolver = None
                await self.cleanup()

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
        return self._session_pool  # type: ignore[return-value]

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

    async def _on_skills_changed(self, event: Any) -> None:
        """Handle skills changed events from the skill provider.

        This method is called when the skill provider detects changes to skills
        from any source (local filesystem or MCP servers). The event is already
        handled by SkillCommandRegistry which listens to _skill_provider directly.

        Args:
            event: The resource change event from the provider.
        """
        # Skill changes are handled by SkillCommandRegistry which subscribes
        # directly to _skill_provider.skills_changed. No additional forwarding
        # needed here to avoid potential event loops.

    async def cleanup(self) -> None:
        """Clean up all agents."""
        # Clean up background processes
        await self.process_manager.cleanup()
        await self.exit_stack.aclose()
        self.clear()

    @overload
    def create_team_run[TDeps, TResult](
        self,
        agents: Sequence[MessageNode[TDeps, Any]],
        validator: MessageNode[Any, TResult] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        shared_prompt: str | None = None,
    ) -> TeamRun[TDeps, TResult]: ...

    @overload
    def create_team_run[TResult](
        self,
        agents: Sequence[MessageNode[Any, Any]],
        validator: MessageNode[Any, TResult] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        shared_prompt: str | None = None,
    ) -> TeamRun[Any, TResult]: ...

    def create_team_run[TResult](
        self,
        agents: Sequence[MessageNode[Any, Any]],
        validator: MessageNode[Any, TResult] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        shared_prompt: str | None = None,
    ) -> TeamRun[Any, TResult]:
        """Create a a sequential TeamRun from a list of Agents.

        Args:
            agents: List of agent names or team/agent instances (all if None)
            validator: Node to validate the results of the TeamRun
            name: Optional name for the team
            description: Optional description for the team
            shared_prompt: Optional prompt for all agents
        """
        from agentpool.delegation.teamrun import TeamRun

        team = TeamRun(
            agents,
            name=name,
            description=description,
            validator=validator,
            shared_prompt=shared_prompt,
        )
        if name:
            self[name] = team
        return team

    @overload
    def create_team[TDeps](
        self,
        agents: Sequence[MessageNode[TDeps, Any]],
        *,
        name: str | None = None,
        description: str | None = None,
        shared_prompt: str | None = None,
        member_timeout: float | None = None,
    ) -> Team[TDeps]: ...

    @overload
    def create_team(
        self,
        agents: Sequence[MessageNode[Any, Any]],
        *,
        name: str | None = None,
        description: str | None = None,
        shared_prompt: str | None = None,
        member_timeout: float | None = None,
    ) -> Team[Any]: ...

    def create_team(
        self,
        agents: Sequence[MessageNode[Any, Any]],
        *,
        name: str | None = None,
        description: str | None = None,
        shared_prompt: str | None = None,
        member_timeout: float | None = None,
    ) -> Team[Any]:
        """Create a group from agent names or instances.

        Args:
            agents: List of agent names or instances (all if None)
            name: Optional name for the team
            description: Optional description for the team
            shared_prompt: Optional prompt for all agents
            member_timeout: Per-member timeout in seconds (``None`` = no limit)
        """
        from agentpool.delegation.team import Team

        team = Team(
            agents, name=name, description=description,
            shared_prompt=shared_prompt, member_timeout=member_timeout,
        )
        if name:
            self[name] = team
        return team

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
        print("Active nodes: ", ", ".join(list(self.nodes.keys())))
        print("Press Ctrl+C to stop")

        shutdown_event = anyio.Event()
        with suppress(KeyboardInterrupt):
            await shutdown_event.wait()

    @overload
    def get_agents(self) -> dict[str, BaseAgent[Any, Any]]: ...

    @overload
    def get_agents[TAgent: BaseAgent[Any, Any]](
        self, agent_type: type[TAgent]
    ) -> dict[str, TAgent]: ...

    def get_agents[TAgent: BaseAgent[Any, Any]](
        self,
        agent_type: type[TAgent] | None = None,
    ) -> dict[str, TAgent] | dict[str, BaseAgent[Any, Any]]:
        """Get agents filtered by type.

        Args:
            agent_type: Optional agent type to filter by. If None, returns all agents.

        Returns:
            Dictionary mapping agent names to agent instances.
        """
        from agentpool.agents.base_agent import BaseAgent

        filter_type = agent_type or BaseAgent
        return {i.name: i for i in self._items.values() if isinstance(i, filter_type)}

    @property
    def all_agents(self) -> dict[str, BaseAgent[Any, Any]]:
        """Get all agents (regular, ACP, and AG-UI)."""
        return self.get_agents()

    @property
    def main_agent(self) -> BaseAgent[Any, Any]:
        """Get the main agent.

        Returns the agent specified by main_agent_name constructor param,
        manifest.default_agent, or falls back to the first agent.

        Raises:
            RuntimeError: If no agents are available.
            ValueError: If the specified main agent doesn't exist.
        """
        agents = self.all_agents
        if not agents:
            msg = "No agents available in pool"
            raise RuntimeError(msg)

        if self._main_agent_name:
            if self._main_agent_name not in agents:
                available = list(agents.keys())
                msg = f"Main agent {self._main_agent_name!r} not found. Available: {available}"
                raise ValueError(msg)
            return agents[self._main_agent_name]

        # Fallback to first agent
        return next(iter(agents.values()))

    @property
    def teams(self) -> dict[str, BaseTeam[Any, Any]]:
        """Get agents dict (backward compatibility)."""
        from agentpool.delegation.base_team import BaseTeam

        return {i.name: i for i in self._items.values() if isinstance(i, BaseTeam)}

    @property
    def nodes(self) -> dict[str, MessageNode[Any, Any]]:
        """Get agents dict (backward compatibility)."""
        return {i.name: i for i in self._items.values()}

    @property
    def compaction_pipeline(self) -> CompactionPipeline | None:
        """Get the configured compaction pipeline or None if not configured."""
        return self.manifest.get_compaction_pipeline()

    def _validate_item(self, item: MessageNode[Any, Any] | Any) -> MessageNode[Any, Any]:
        """Validate and convert items before registration.

        Raises:
            AgentPoolError: If item is not a valid node
        """
        if not isinstance(item, MessageNode):
            raise self._error_class(f"Item must be Agent or Team, got {type(item)}")
        item.agent_pool = self
        return item

    def _create_teams(self) -> None:
        """Create all teams in two phases to allow nesting."""
        # Phase 1: Create empty teams
        empty_teams: dict[str, BaseTeam[Any, Any]] = {}
        for name, config in self.manifest.teams.items():
            empty_teams[name] = config.get_team([], name=name)
        # Phase 2: Resolve members (supports both str and TeamMemberConfig)
        for name, config in self.manifest.teams.items():
            team = empty_teams[name]
            members: list[MessageNode[Any, Any]] = []
            agents = self.all_agents
            for member in config.members:
                member_name = config.get_member_name(member)
                if member_name in agents:
                    members.append(agents[member_name])
                elif member_name in empty_teams:
                    members.append(empty_teams[member_name])
                else:
                    raise ValueError(f"Unknown team member: {member_name}")
            team.nodes.extend(members)
            self[name] = team

    def _connect_nodes(self) -> None:
        """Set up connections defined in manifest."""
        # Merge agent and team configs into one dict of nodes with connections
        for name, config in self.manifest.nodes.items():
            source = self[name]
            for target in config.connections or []:
                target.connect_nodes(source, list(self.all_agents.values()), name)
        # Connections changed -> graph topology changed
        self._invalidate_graph()

    def _on_registry_changed(self, key: Any, value: Any) -> None:
        """Invalidate cached graph when registry changes."""
        self._invalidate_graph()

    def _invalidate_graph(self) -> None:
        """Mark the runtime graph as dirty so it will be rebuilt."""
        self._graph_dirty = True

    def _load_graph_config(self, path_for_loading: Any | None) -> None:
        """Load graph configuration from raw YAML or manifest extras."""
        from agentpool_config.graph_translation import GraphConfig, translate_config

        if path_for_loading is not None:
            import yamling
            try:
                raw_data = yamling.load_yaml_file(path_for_loading, resolve_inherit=True)
            except (OSError, ValueError):
                return
            try:
                self._graph_config = translate_config(raw_data)
            except Exception as exc:
                config_str = str(path_for_loading)
                raise ValueError(
                    f"Failed to build graph config from {config_str}: {exc}"
                ) from exc
        else:
            extra = getattr(self.manifest, "model_extra", None) or {}
            if "graph" in extra:
                graph_data = extra["graph"]
                if isinstance(graph_data, dict):
                    self._graph_config = GraphConfig.model_validate(graph_data)
                elif hasattr(graph_data, "model_dump"):
                    self._graph_config = graph_data

    def _build_graph_from_config(self) -> Any:  # noqa: PLR0915
        """Build a pydantic-graph from the stored YAML graph configuration."""
        if self._graph_config is None:
            raise ValueError("No graph config loaded")
        config_path_str = (
            str(self._config_file_path)
            if self._config_file_path
            else "programmatic config"
        )
        try:
            from pydantic_graph import GraphBuilder, StepContext
            from pydantic_graph.id_types import NodeID

            builder = GraphBuilder(state_type=_WorkflowGraphState, output_type=Any)

            step_ids = [s.id for s in self._graph_config.steps]
            seen: set[str] = set()
            duplicates: set[str] = set()
            for sid in step_ids:
                if sid in seen:
                    duplicates.add(sid)
                seen.add(sid)
            if duplicates:
                raise ValueError(f"Duplicate step IDs in graph: {sorted(duplicates)}")  # noqa: TRY301

            step_map: dict[str, Any] = {}
            for step_cfg in self._graph_config.steps:
                agent = self.all_agents.get(step_cfg.agent)
                if agent is None:
                    available = list(self.all_agents.keys())
                    raise ValueError(  # noqa: TRY301
                        f"Graph step '{step_cfg.id}' references unknown agent "
                        f"'{step_cfg.agent}'. Available agents: {available}"
                    )

                async def _execute(
                    ctx: StepContext[_WorkflowGraphState, Any, Any],
                    node: MessageNode[Any, Any] = agent,
                ) -> Any:
                    if ctx.inputs is None:
                        result = await node.run(*ctx.state.prompts, **ctx.state.kwargs)
                    else:
                        result = await node.run_message(ctx.inputs)
                    ctx.state.result = result
                    return result

                step = builder.step(call=_execute, node_id=NodeID(step_cfg.id))
                step_map[step_cfg.id] = step

            for edge_cfg in self._graph_config.edges:
                from_ref = edge_cfg.from_
                to_ref = edge_cfg.to
                from_refs = [from_ref] if isinstance(from_ref, str) else from_ref
                to_refs = [to_ref] if isinstance(to_ref, str) else to_ref
                from_steps = [
                    self._resolve_graph_step_ref(ref, step_map, builder)
                    for ref in from_refs
                ]
                to_steps = [
                    self._resolve_graph_step_ref(ref, step_map, builder)
                    for ref in to_refs
                ]
                for from_step in from_steps:
                    path = builder.edge_from(from_step)
                    if edge_cfg.label:
                        path = path.label(edge_cfg.label)
                    if edge_cfg.transform:
                        path = path.transform(edge_cfg.transform)
                    if len(to_steps) == 1:
                        builder.add(path.to(to_steps[0]))
                    else:
                        builder.add(path.to(*to_steps))

            has_incoming: set[str] = set()
            has_outgoing: set[str] = set()
            for edge_cfg in self._graph_config.edges:
                to_refs = [edge_cfg.to] if isinstance(edge_cfg.to, str) else edge_cfg.to
                from_refs = (
                    [edge_cfg.from_]
                    if isinstance(edge_cfg.from_, str)
                    else edge_cfg.from_
                )
                for ref in to_refs:
                    if ref not in ("start", "end"):
                        has_incoming.add(ref)
                for ref in from_refs:
                    if ref not in ("start", "end"):
                        has_outgoing.add(ref)

            for step_id, step in step_map.items():
                if step_id not in has_incoming:
                    builder.add(builder.edge_from(builder.start_node).to(step))
                if step_id not in has_outgoing:
                    builder.add(builder.edge_from(step).to(builder.end_node))

            return builder.build()
        except Exception as exc:
            raise ValueError(
                f"Failed to build graph from config at {config_path_str}: {exc}"
            ) from exc

    def _resolve_graph_step_ref(
        self, ref: str, step_map: dict[str, Any], builder: Any
    ) -> Any:
        """Resolve a step reference string to a pydantic-graph node object."""
        if ref == "start":
            return builder.start_node
        if ref == "end":
            return builder.end_node
        if ref not in step_map:
            available = ["start", "end", *step_map.keys()]
            raise ValueError(
                f"Graph edge references unknown step '{ref}'. "
                f"Available steps: {available}"
            )
        return step_map[ref]

    def _build_graph(self) -> Any:
        """Build pydantic-graph from current pool nodes and their Talk connections."""
        from pydantic_graph import GraphBuilder, StepContext
        from pydantic_graph.id_types import NodeID

        builder = GraphBuilder(state_type=Any, output_type=Any)
        step_map: dict[str, Any] = {}
        for node in self.nodes.values():
            async def _step(
                ctx: StepContext[Any, Any, Any],
                node: MessageNode[Any, Any] = node,
            ) -> Any:
                if ctx.inputs is None:
                    result = await node.run(*ctx.state.args, **ctx.state.kwargs)
                else:
                    result = await node.run_message(ctx.inputs)
                return result

            step = builder.step(call=_step, node_id=NodeID(node.name))
            step_map[node.name] = step
        for node in self.nodes.values():
            for talk in node.connections.get_connections():
                source_step = step_map[talk.source.name]
                for target in talk.targets:
                    target_step = step_map[target.name]
                    path = builder.edge_from(source_step)
                    if talk.queued:
                        path = path.label(talk.queue_strategy or "queued")
                    builder.add(path.to(target_step))
        return builder.build(validate_graph_structure=False)

    @property
    def graph(self) -> Any:
        """The pool's pydantic-graph topology.

        When the manifest contains a ``graph:`` section (native syntax) or
        legacy ``teams:`` / ``connections:`` that were translated to a graph,
        the graph is built from the YAML config during :meth:`__aenter__`.

        Otherwise the graph is built lazily on first access from the runtime
        pool topology (all registered nodes and their Talk connections) and
        is rebuilt automatically when the registry changes.

        Returns:
            An immutable pydantic-graph.

        Raises:
            RuntimeError: If a config-based graph has not yet been built
                (pool context not entered).
        """
        if self._graph_config is not None:
            if self._graph is None:
                raise RuntimeError(
                    "Config-based graph not yet initialized. "
                    "Enter the AgentPool async context first."
                )
            return self._graph
        has_connections = any(
            node.connections.get_connections() for node in self.nodes.values()
        )
        if not has_connections:
            return None
        if self._graph is None or self._graph_dirty:
            self._graph = self._build_graph()
            self._graph_dirty = False
        return self._graph

    @overload
    def get_agent[TResult = str](
        self,
        agent: AgentName | Agent[Any, str],
        *,
        output_type: type[TResult] = str,  # type: ignore[assignment]
    ) -> BaseAgent[TPoolDeps, TResult]: ...

    @overload
    def get_agent[TCustomDeps, TResult = str](
        self,
        agent: AgentName | Agent[Any, str],
        *,
        deps_type: type[TCustomDeps],
        output_type: type[TResult] = str,  # type: ignore[assignment]
    ) -> BaseAgent[TCustomDeps, TResult]: ...

    def get_agent(
        self,
        agent: AgentName | Agent[Any, str],
        *,
        deps_type: Any | None = None,
        output_type: Any = str,
    ) -> BaseAgent[Any, Any]:
        """Get or configure an agent from the pool.

        This method provides flexible agent configuration with dependency injection:
        - Without deps: Agent uses pool's shared dependencies
        - With deps: Agent uses provided custom dependencies

        Args:
            agent: Either agent name or instance
            deps_type: Optional custom dependencies type (overrides shared deps)
            output_type: Optional type for structured responses

        Returns:
            Either:
            - Agent[TPoolDeps] when using pool's shared deps
            - Agent[TCustomDeps] when custom deps provided

        Raises:
            KeyError: If agent name not found
            ValueError: If configuration is invalid
        """
        from agentpool.agents.base_agent import BaseAgent

        base = agent if isinstance(agent, BaseAgent) else self.get_agents()[agent]
        # Use custom deps if provided, otherwise use shared deps
        # base.context.data = deps if deps is not None else self.shared_deps
        base.deps_type = deps_type
        base.agent_pool = self
        if isinstance(base, SupportsStructuredOutput):
            base.to_structured(output_type)
        return base

    def get_job(self, name: str) -> Job[Any, Any]:
        return self._tasks[name]

    def register_task(self, name: str, task: Job[Any, Any]) -> None:
        self._tasks.register(name, task)

    async def add_agent(self, agent: BaseAgent[Any, Any]) -> None:
        """Add a new permanent agent to the pool."""
        from agentpool.agents.events import resolve_event_handlers

        if agent.agent_pool is not None:
            raise ValueError("Agent is already part of a pool")
        for handler in resolve_event_handlers(self.event_handlers):
            agent.event_handler.add_handler(handler)
        # Add MCP aggregating provider from manager
        agent.tools.add_provider(self.mcp.get_aggregating_provider())
        if self.skills_instruction_provider:
            agent.tools.add_provider(self.skills_instruction_provider)
        agent = await self.exit_stack.enter_async_context(agent)
        self.register(agent.name, agent)

    def get_mermaid_diagram(self, include_details: bool = True) -> str:
        """Generate mermaid flowchart of all agents and their connections.

        Args:
            include_details: Whether to show connection details (types, queues, etc)
        """
        declare_lines = []
        connect_lines = []
        # Add all connections as edges
        for agent in self.all_agents.values():
            declare_lines.append(f"    {agent.name}[{agent.display_name}]")
            for talk in agent.connections.get_connections():
                source = talk.source.name
                for target in talk.targets:
                    if include_details:
                        details: list[str] = []
                        details.append(talk.connection_type)
                        if talk.queued:
                            details.append(f"queued({talk.queue_strategy})")
                        if fn := talk.filter_condition:
                            details.append(f"filter:{get_fn_name(fn)}")
                        if fn := talk.stop_condition:
                            details.append(f"stop:{get_fn_name(fn)}")
                        if fn := talk.exit_condition:
                            details.append(f"exit:{get_fn_name(fn)}")

                        label = f"|{' '.join(details)}|" if details else ""
                        connect_lines.append(f"    {source}--{label}-->{target.name}")
                    else:
                        connect_lines.append(f"    {source}-->{target.name}")
        all_lines = ["flowchart LR", *declare_lines, *connect_lines]
        return "\n".join(all_lines)


if __name__ == "__main__":

    async def main() -> None:
        path = "src/agentpool/config_resources/agents.yml"
        async with AgentPool(path) as pool:
            agent = pool.get_agent("overseer")
            print(agent)

    anyio.run(main)
