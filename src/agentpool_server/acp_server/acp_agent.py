"""ACP (Agent Client Protocol) Agent implementation."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import KW_ONLY, dataclass, field
from importlib.metadata import version as _version
import sys
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from acp import Agent as ACPAgent
from acp.schema import (
    ForkSessionResponse,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    ModelInfo as ACPModelInfo,
    NewSessionResponse,
    PromptResponse,
    ResumeSessionResponse,
    SessionConfigOption,
    SessionConfigSelectOption,
    SessionMode,
    SessionModelState,
    SessionModeState,
    SetSessionConfigOptionResponse,
    SetSessionModelRequest,
    SetSessionModelResponse,
    SetSessionModeRequest,
    SetSessionModeResponse,
    StopSessionResponse,
)
from agentpool.log import get_logger
from agentpool.utils.tasks import TaskManager
from agentpool_server.acp_server.commands.skill_commands import ACPSkillBridge
from agentpool_server.acp_server.converters import to_session_config_option, to_session_info
from agentpool_server.acp_server.provider_router import ProviderRouter
from agentpool_server.acp_server.session_manager import ACPSessionManager


if TYPE_CHECKING:
    from pydantic_ai import ModelMessage

    from acp import Client
    from acp.schema import (
        AuthenticateRequest,
        CancelNotification,
        ClientCapabilities,
        ForkSessionRequest,
        Implementation,
        InitializeRequest,
        ListSessionsRequest,
        LoadSessionRequest,
        NewSessionRequest,
        PromptRequest,
        ResumeSessionRequest,
        SetSessionConfigOptionRequest,
        SetSessionModelRequest,
        SetSessionModeRequest,
        StopSessionRequest,
    )
    from agentpool import AgentPool
    from agentpool.agents.base_agent import BaseAgent
    from agentpool.models.agents import NativeAgentConfig
    from agentpool.storage.manager import SessionMetadataGeneratedEvent
    from agentpool_server.acp_server.server import ACPServer

logger = get_logger(__name__)


async def get_session_model_state(
    agent: BaseAgent,
    provider_router: ProviderRouter | None = None,
) -> SessionModelState | None:
    """Get SessionModelState from an agent, including configured variants.

    Uses configured-first logic via build_model_state_for_acp(),
    falling back to tokonomics discovery if no configured variants exist.

    Args:
        agent: Any agent with get_available_models() method
        provider_router: Optional ProviderRouter for disable filtering

    Returns:
        SessionModelState with all available models, None if no models available
    """
    from agentpool_server.shared.model_utils import build_model_state_for_acp

    try:
        state = await build_model_state_for_acp(agent, provider_router)
    except Exception:
        logger.exception("Failed to build model state for ACP")
        return None

    if state is not None:
        return state

    # Final fallback: ensure current model is represented
    current_model = agent.model_name
    if current_model:
        desc = "Currently configured model"
        model_info = ACPModelInfo(model_id=current_model, name=current_model, description=desc)
        return SessionModelState(available_models=[model_info], current_model_id=current_model)

    return None


async def get_session_mode_state(agent: BaseAgent) -> SessionModeState | None:
    """Get SessionModeState from an agent using its get_modes() method.

    Converts agentpool ModeCategory to ACP SessionModeState format.
    Uses the first category that looks like permissions (not model).

    Args:
        agent: Any agent with get_modes() method

    Returns:
        SessionModeState from agent's modes, None if no modes available
    """
    try:
        mode_categories = await agent.get_modes()
    except Exception:
        logger.exception("Failed to get modes from agent")
        return None
    if not mode_categories:
        return None
    # Find the permissions category (not model)
    category = next((c for c in mode_categories if c.id == "mode"), None)
    if not category:
        return None
    acp_modes = [  # Convert ModeInfo to ACP SessionMode
        SessionMode(id=mode.id, name=mode.name, description=mode.description)
        for mode in category.available_modes
    ]
    return SessionModeState(available_modes=acp_modes, current_mode_id=category.current_mode_id)


async def get_session_config_options(agent: BaseAgent) -> list[SessionConfigOption]:
    """Get SessionConfigOptions from an agent using its get_modes() method."""
    try:
        mode_categories = await agent.get_modes()
    except Exception:
        logger.exception("Failed to get modes from agent")
        return []
    options = [to_session_config_option(category) for category in mode_categories]
    # Append agent_role config option if pool has multiple agents
    if agent_role_opt := get_agent_role_config_option(agent):
        options.append(agent_role_opt)
    return options


def get_agent_role_config_option(agent: BaseAgent[Any, Any]) -> SessionConfigOption | None:
    """Build agent_role config option if pool has more than one agent.

    Args:
        agent: The agent to check pool membership for.

    Returns:
        SessionConfigOption for agent_role, or None if pool has <= 1 agents.
    """
    pool = agent.agent_pool
    if pool is None or len(pool.all_agents) <= 1:
        return None

    choices = [
        SessionConfigSelectOption(
            value=a.name,
            name=a.display_name if isinstance(a.display_name, str) and a.display_name else a.name,
            description=f"Switch to {a.name} agent",
        )
        for a in pool.all_agents.values()
    ]
    return SessionConfigOption(
        id="agent_role",
        name="Agent Role",
        description="Switch between available agents",
        category="other",
        current_value=agent.name,
        options=choices,
    )


@dataclass
class AgentPoolACPAgent(ACPAgent):
    """Implementation of ACP Agent protocol interface for AgentPool.

    This class implements the external library's Agent protocol interface,
    bridging AgentPool with the standard ACP JSON-RPC protocol.
    """

    PROTOCOL_VERSION: ClassVar = 1

    client: Client
    """ACP connection for client communication."""

    default_agent: BaseAgent[Any, Any]
    """Default agent instance to use for new sessions.

    The agent carries its own pool reference via agent.agent_pool,
    which is used for pool-level operations and agent switching.
    """

    _: KW_ONLY

    debug_commands: bool = False
    """Whether to enable debug slash commands for testing."""

    load_skills: bool | None = None
    """Whether to load client-side skills from .claude/skills directory.

    If None (default), uses the manifest's skills.include_default setting.
    """

    server: ACPServer | None = field(default=None)
    """Reference to the ACPServer for pool hot-switching."""

    subagent_display_mode: Literal["inline", "tool_box"] = "tool_box"
    """Display mode for subagent outputs (inline or tool_box)."""

    _skill_bridge: ACPSkillBridge | None = field(init=False, default=None)
    """Bridge for exposing skill commands as ACP slash commands."""

    def __post_init__(self) -> None:
        """Initialize derived attributes and setup after field assignment."""
        self.client_capabilities: ClientCapabilities | None = None
        self.client_info: Implementation | None = None
        pool = self.agent_pool
        if pool is None:
            msg = "Default agent has no associated pool"
            raise RuntimeError(msg)
        self.session_manager = ACPSessionManager(pool=pool)
        self.tasks = TaskManager()
        self._initialized = False
        self._sessions_cache: ListSessionsResponse | None = None
        self._sessions_cache_time: float = 0.0
        # Connect to title generation signal to notify clients of session updates
        pool.storage.metadata_generated.connect(self._on_metadata_generated)
        # Setup skill command bridge if pool has skill commands configured
        self._setup_skill_bridge()

        # RFC-0034: Initialize provider router with None manifest (will be updated in initialize)
        self.provider_router = ProviderRouter(None)
        from agentpool.models.agents import NativeAgentConfig
        if (
            self.agent_pool
            and self.agent_pool.main_agent
            and self.agent_pool.main_agent.name in self.agent_pool.manifest.agents
        ):
            cfg = self.agent_pool.manifest.agents[self.agent_pool.main_agent.name]
            if isinstance(cfg, NativeAgentConfig):
                if cfg.name is None:
                    cfg = cfg.model_copy(update={"name": self.agent_pool.main_agent.name})
                self._agent_config = cfg

    # NEW: Per-session agent registry (RFC-0031)
    _agent_config: NativeAgentConfig | None = field(init=False, default=None)
    _session_agents: dict[str, BaseAgent[Any, Any]] = field(init=False, default_factory=dict)
    _session_agent_locks: dict[str, asyncio.Lock] = field(init=False, default_factory=dict)
    _swap_in_progress: bool = field(init=False, default=False)
    """Flag to prevent concurrent session creation during pool swap."""

    # RFC-0034: Provider router for ACP providers/* protocol
    provider_router: ProviderRouter = field(init=False)
    """Router for LLM provider metadata and override tracking."""

    def _setup_skill_bridge(self) -> None:
        """Initialize skill command bridge and subscribe to registry changes.

        Wire up the ACPSkillBridge to the pool's SkillCommandRegistry if available.
        This enables skill commands to be exposed as ACP slash commands.
        Gracefully handles cases where no skill commands are configured.
        """
        pool = self.agent_pool
        if pool is None:
            return

        # Check if pool has skill_commands registry
        skill_commands = getattr(pool, "skill_commands", None)
        if skill_commands is None:
            return

        self._skill_bridge = ACPSkillBridge()
        skill_commands.on_command_change(self._skill_bridge.handle_change)
        logger.debug(
            "Skill bridge setup complete",
            command_count=len(skill_commands),
        )

    def get_skill_commands(self) -> list[Any] | None:
        """Get available skill commands for ACP capabilities.

        Returns:
            List of AvailableCommand objects for skill commands,
            or None if no skill bridge is configured.
        """
        if self._skill_bridge is not None:
            return self._skill_bridge.get_available_commands()
        return None

    async def get_or_create_session_agent(
        self,
        session_id: str,
        input_provider: Any | None = None,
        agent_name: str | None = None,
    ) -> BaseAgent[Any, Any]:
        """Get or create a per-session agent instance.

        Uses double-checked locking for concurrent access.
        """
        # Reject session creation during pool swap to prevent stale config usage
        if self._swap_in_progress:
            msg = "Pool swap in progress - cannot create new session"
            raise RuntimeError(msg)

        # Fast path: already registered
        if session_id in self._session_agents:
            return self._session_agents[session_id]

        # Ensure a lock exists for this session
        if session_id not in self._session_agent_locks:
            self._session_agent_locks[session_id] = asyncio.Lock()

        async with self._session_agent_locks[session_id]:
            # Re-check after acquiring lock
            if session_id in self._session_agents:
                return self._session_agents[session_id]

            # Create agent and set config dir for path resolution
            # (ConfigContextManager from pool loading has exited,
            # so _config_dir_global and CONFIG_DIR are None, breaking runtime path resolution)
            from upathtools import UPath

            import agentpool_config.context as ctx

            config_path = self._resolve_agent_config_path(agent_name)
            previous_dir = ctx._config_dir_global
            config_token = None
            if config_path is not None:
                ctx._config_dir_global = UPath(config_path)
                config_token = ctx.CONFIG_DIR.set(UPath(config_path))
                logger.info(
                    "get_or_create_session_agent: set _config_dir_global=%s",
                    ctx._config_dir_global,
                )

            try:
                agent = self._create_session_agent(session_id, input_provider, agent_name)

                # Initialize the agent's async context (MCP subprocesses, tool providers, etc.)
                # Tool providers need _config_dir_global to resolve schema paths
                entered = False
                try:
                    await agent.__aenter__()
                    entered = True
                except Exception:
                    if entered:
                        with suppress(Exception):
                            await agent.__aexit__(*sys.exc_info())
                    raise
                else:
                    self._session_agents[session_id] = agent
                    return agent
            finally:
                ctx._config_dir_global = previous_dir
                if config_token is not None:
                    ctx.CONFIG_DIR.reset(config_token)
                logger.info(
                    "get_or_create_session_agent: restored _config_dir_global=%s",
                    ctx._config_dir_global,
                )

    def _create_session_agent(
        self,
        session_id: str,
        input_provider: Any | None = None,
        agent_name: str | None = None,
    ) -> BaseAgent[Any, Any]:
        """Create a new agent instance for a session.

        NOTE: _config_dir_global must be set by caller (get_or_create_session_agent)
        before calling this method, and restored after agent.__aenter__().
        """
        # Resolve config: use specified agent_name or fallback to main agent
        agent_config = None
        if (agent_name is not None and self.agent_pool
                and agent_name in self.agent_pool.manifest.agents):
            from agentpool.models.agents import NativeAgentConfig
            cfg = self.agent_pool.manifest.agents[agent_name]
            if isinstance(cfg, NativeAgentConfig):
                agent_config = cfg
        elif agent_name is None:
            agent_config = self._agent_config

        if agent_config is not None:
            agent = agent_config.get_agent(
                input_provider=input_provider,
                pool=self.agent_pool,
            )
            agent.session_id = session_id
            return agent

        # Fallback: use pool agent directly if available, otherwise shared default_agent
        if (agent_name is not None and self.agent_pool
                and agent_name in self.agent_pool.all_agents):
            return self.agent_pool.all_agents[agent_name]

        logger.warning(
            "Non-native agent type - falling back to shared default_agent",
            agent_type=type(self.default_agent).__name__,
        )
        return self.default_agent

    async def remove_session_agent(self, session_id: str) -> None:
        """Remove and clean up a session's dedicated agent."""
        if session_id not in self._session_agent_locks:
            self._session_agent_locks[session_id] = asyncio.Lock()
        async with self._session_agent_locks[session_id]:
            agent = self._session_agents.pop(session_id, None)
            if agent is not None:
                try:
                    await agent.__aexit__(None, None, None)
                except Exception:
                    logger.exception("Failed to clean up session agent", session_id=session_id)
        self._session_agent_locks.pop(session_id, None)

    async def cleanup_all_session_agents(self) -> None:
        """Clean up all per-session agents."""
        # Iterate over a snapshot to avoid mutation during iteration
        for session_id, agent in list(self._session_agents.items()):
            try:
                await agent.__aexit__(None, None, None)
            except Exception:
                logger.exception("Failed to clean up agent", session_id=session_id)
        self._session_agents.clear()
        self._session_agent_locks.clear()

    def _resolve_agent_config_path(self, agent_name: str | None = None) -> str | None:
        """Resolve the config file path for agent creation.

        Used to set _config_dir_global so that tool schemas and other
        config-relative paths resolve correctly during agent.__aenter__().

        Returns the parent directory of the config file, or None if no
        config file path can be resolved.
        """
        # Resolve config: use specified agent_name or fallback to main agent
        agent_config = None
        if (agent_name is not None and self.agent_pool
                and agent_name in self.agent_pool.manifest.agents):
            from agentpool.models.agents import NativeAgentConfig
            cfg = self.agent_pool.manifest.agents[agent_name]
            if isinstance(cfg, NativeAgentConfig):
                agent_config = cfg
        elif agent_name is None:
            agent_config = self._agent_config

        if agent_config is None:
            return None

        # Resolve config path: agent-level -> manifest-level -> None
        config_path = agent_config.config_file_path
        if config_path is None and self.agent_pool and self.agent_pool.manifest:
            config_path = self.agent_pool.manifest.config_file_path

        if config_path is not None:
            from upathtools import UPath
            return str(UPath(config_path).parent)

        return None

    async def _on_metadata_generated(self, event: SessionMetadataGeneratedEvent) -> None:
        """Handle metadata generation - notify active sessions of the update."""
        from acp.schema import SessionInfoUpdate, SessionNotification

        session = self.session_manager.get_session(event.session_id)
        if session is None:
            logger.debug("Metadata generated for inactive session", session_id=event.session_id)
            return

        # Send session info update to client
        update = SessionInfoUpdate(session_id=event.session_id, title=event.metadata.title)
        notification = SessionNotification(session_id=event.session_id, update=update)
        try:
            await session.client.session_update(notification)  # pyright: ignore[reportArgumentType]
            logger.info(
                "Sent session info update",
                session_id=event.session_id,
                title=event.metadata.title,
            )
        except Exception:
            logger.exception("Failed to send session info update", session_id=event.session_id)

    @property
    def agent_pool(self) -> AgentPool[Any] | None:
        """Get the agent pool from the default agent."""
        return self.default_agent.agent_pool

        # Note: Tool registration happens after initialize() when we know client caps

    async def initialize(self, params: InitializeRequest) -> InitializeResponse:
        """Initialize the agent and negotiate capabilities."""
        version = min(params.protocol_version, self.PROTOCOL_VERSION)
        self.client_capabilities = params.client_capabilities
        self.client_info = params.client_info
        logger.info("Client info", request=params.model_dump_json())
        self._initialized = True
        # Initialize provider router from current pool manifest
        pool = self.agent_pool
        manifest = pool.manifest if pool else None
        self.provider_router = ProviderRouter(manifest)
        return InitializeResponse.create(
            protocol_version=version,
            name="agentpool",
            title="AgentPool",
            version=_version("agentpool"),
            load_session=True,
            list_sessions=True,
            resume_session=True,
            stop_session=True,
            http_mcp_servers=True,
            sse_mcp_servers=True,
            audio_prompts=True,
            embedded_context_prompts=True,
            image_prompts=True,
            providers=True,
        )

    async def new_session(self, params: NewSessionRequest) -> NewSessionResponse:
        """Create a new session."""
        from agentpool.agents.acp_agent import ACPAgent as ACPAgentClient

        if not self._initialized:
            raise RuntimeError("Agent not initialized")

        logger.info("Creating new session", default_agent=self.default_agent.name)
        try:
            session_id = await self.session_manager.create_session(
                agent=self.default_agent,
                cwd=params.cwd,
                client=self.client,
                acp_agent=self,
                mcp_servers=params.mcp_servers,
                client_capabilities=self.client_capabilities,
                client_info=self.client_info,
                subagent_display_mode=self.subagent_display_mode,
            )
            state: SessionModeState | None = None
            models: SessionModelState | None = None
            config_options: list[SessionConfigOption] = []

            if session := self.session_manager.get_session(session_id):
                if isinstance(session.agent, ACPAgentClient):
                    # Nested ACP agent - pass through its state directly
                    if session.agent._state:
                        models = session.agent._state.models
                        state = session.agent._state.modes
                    # Also get config_options from nested agent
                    config_options = await get_session_config_options(session.agent)
                else:
                    # Use unified helpers for all other agents
                    models = await get_session_model_state(
                        session.agent, provider_router=self.provider_router
                    )
                    state = await get_session_mode_state(session.agent)
                    config_options = await get_session_config_options(session.agent)
        except Exception:
            logger.exception("Failed to create new session")
            raise
        else:
            # Schedule available commands update after session response is returned
            if session := self.session_manager.get_session(session_id):
                # Schedule task to run after response is sent
                self.tasks.create_task(session.send_available_commands_update())
                self.tasks.create_task(session.agent.load_rules(session.cwd))
                self.tasks.create_task(session._register_prompt_hub_commands())
                # Determine whether to load client skills
                # None means "use manifest's include_default setting"
                should_load_skills = self.load_skills
                if should_load_skills is None and self.agent_pool and self.agent_pool.manifest:
                    should_load_skills = self.agent_pool.manifest.skills.include_default
                elif should_load_skills is None:
                    should_load_skills = True  # Fallback default

                if should_load_skills:
                    coro_4 = session.init_client_skills()
                    self.tasks.create_task(coro_4, name=f"init_client_skills_{session_id}")
            logger.info("Created session", session_id=session_id)

            return NewSessionResponse(
                session_id=session_id,
                modes=state,
                models=models,
                config_options=config_options if config_options else None,
            )

    async def load_session(self, params: LoadSessionRequest) -> LoadSessionResponse:
        """Load an existing session from storage.

        Delegates to the agent's load_session method, which populates agent.conversation.
        Then replays the conversation to the client via ACP notifications.
        """
        from agentpool.agents.acp_agent import ACPAgent as ACPAgentClient

        if not self._initialized:
            raise RuntimeError("Agent not initialized")

        try:
            # Get or create an active session wrapper
            session = self.session_manager.get_session(params.session_id)
            if not session:
                session_id = await self.session_manager.create_session(
                    agent=self.default_agent,
                    cwd=params.cwd or "",
                    client=self.client,
                    acp_agent=self,
                    mcp_servers=params.mcp_servers,
                    client_capabilities=self.client_capabilities,
                    client_info=self.client_info,
                    session_id=params.session_id,
                    subagent_display_mode=self.subagent_display_mode,
                )
                session = self.session_manager.get_session(session_id)

            if not session:
                logger.error("Failed to create session")
                return LoadSessionResponse()

            # Load session via agent - this populates agent.conversation
            if not await session.agent.load_session(params.session_id):
                logger.warning("Agent failed to load session", session_id=params.session_id)
                return LoadSessionResponse()

            # Replay loaded conversation to client via ACP notifications
            if msgs := session.agent.conversation.chat_messages:
                model_messages: list[ModelMessage] = []
                for chat_msg in msgs:
                    if chat_msg.messages:
                        model_messages.extend(chat_msg.messages)
                await session.notifications.replay(model_messages)
                logger.info(
                    "Conversation replayed",
                    session_id=params.session_id,
                    message_count=len(model_messages),
                )
            mode_state: SessionModeState | None = None
            models: SessionModelState | None = None
            if isinstance(session.agent, ACPAgentClient) and session.agent._state:
                mode_state = session.agent._state.modes
                models = session.agent._state.models
            config_opts = await get_session_config_options(session.agent)
            # Schedule post-load tasks
            self.tasks.create_task(session.send_available_commands_update())
            self.tasks.create_task(session.agent.load_rules(session.cwd))
            logger.info("Session loaded", session_id=params.session_id)
            return LoadSessionResponse(models=models, modes=mode_state, config_options=config_opts)
        except Exception:
            logger.exception("Failed to load session", session_id=params.session_id)
            return LoadSessionResponse()

    async def list_sessions(self, params: ListSessionsRequest) -> ListSessionsResponse:
        """List available sessions.

        Delegates to the current agent's list_sessions method which handles
        fetching sessions from storage with proper titles.

        Uses a short TTL cache to avoid redundant expensive storage reads
        when clients request the list multiple times in quick succession.
        """
        import time

        if not self._initialized:
            raise RuntimeError("Agent not initialized")

        # Return cached result if fresh (within 10 seconds)
        cache_ttl = 10.0
        now = time.monotonic()
        if self._sessions_cache and (now - self._sessions_cache_time) < cache_ttl:
            logger.debug("Returning cached sessions list", count=len(self._sessions_cache.sessions))
            return self._sessions_cache

        # Get agent from first active session, or fall back to default
        first_session = next(iter(self.session_manager._active.values()), None)
        agent = first_session.agent if first_session else self.default_agent
        try:
            logger.info("Listing sessions for agent", agent_name=agent.name)
            agent_sessions = await agent.list_sessions()
            logger.info("Agent returned sessions", count=len(agent_sessions))
            sessions = [to_session_info(s) for s in agent_sessions]
            logger.info("Listed sessions", count=len(sessions))
            response = ListSessionsResponse(sessions=sessions)
        except Exception:
            logger.exception("Failed to list sessions")
            return ListSessionsResponse(sessions=[])
        else:
            # Cache the result
            self._sessions_cache = response
            self._sessions_cache_time = now
            return response

    async def fork_session(self, params: ForkSessionRequest) -> ForkSessionResponse:
        """Fork an existing session.

        Creates a new session with the same state as the original.
        UNSTABLE: This feature is not part of the spec yet.
        """
        if not self._initialized:
            raise RuntimeError("Agent not initialized")

        logger.info("Forking session", session_id=params.session_id)
        # For now, just create a new session - full fork implementation would copy state
        session_id = await self.session_manager.create_session(
            agent=self.default_agent,
            cwd=params.cwd,
            client=self.client,
            acp_agent=self,
            mcp_servers=params.mcp_servers,
            client_capabilities=self.client_capabilities,
            client_info=self.client_info,
            subagent_display_mode=self.subagent_display_mode,
        )
        return ForkSessionResponse(session_id=session_id)

    async def resume_session(self, params: ResumeSessionRequest) -> ResumeSessionResponse:
        """Resume an existing session without replaying history.

        Like load_session but doesn't send session/update notifications with
        previous messages. The agent restores its internal state so the
        conversation can continue.

        UNSTABLE: This feature is not part of the spec yet.
        """
        if not self._initialized:
            raise RuntimeError("Agent not initialized")

        try:
            # Get or create session wrapper
            session = self.session_manager.get_session(params.session_id)
            if not session:
                session_id = await self.session_manager.create_session(
                    agent=self.default_agent,
                    cwd=params.cwd or "",
                    client=self.client,
                    acp_agent=self,
                    mcp_servers=params.mcp_servers,
                    client_capabilities=self.client_capabilities,
                    client_info=self.client_info,
                    session_id=params.session_id,
                    subagent_display_mode=self.subagent_display_mode,
                )
                session = self.session_manager.get_session(session_id)

            if not session:
                logger.error("Failed to create session for resume")
                return ResumeSessionResponse()

            # Load session state in the agent (populates conversation) but don't replay to client
            # This restores the agent's internal state so it can continue the conversation
            if not await session.agent.load_session(params.session_id):
                logger.warning("Agent failed to load session state", session_id=params.session_id)
                # Continue anyway - session wrapper is created, agent may still work

            # Schedule post-resume tasks
            self.tasks.create_task(session.send_available_commands_update())
            self.tasks.create_task(session.agent.load_rules(session.cwd))
            logger.info("Session resumed", session_id=params.session_id)
            return ResumeSessionResponse()

        except Exception:
            logger.exception("Failed to resume session", session_id=params.session_id)
            return ResumeSessionResponse()

    async def authenticate(self, params: AuthenticateRequest) -> None:
        """Authenticate with the agent."""
        logger.info("Authentication requested", method_id=params.method_id)

    async def prompt(self, params: PromptRequest) -> PromptResponse:
        """Process a prompt request."""
        if not self._initialized:
            raise RuntimeError("Agent not initialized")

        logger.info("Processing prompt", session_id=params.session_id)
        session = self.session_manager.get_session(params.session_id)
        # Auto-recreate session if not found (e.g., after pool swap)
        if not session:
            logger.info("Session not found, recreating", session_id=params.session_id)
            # Try to get cwd from stored session data
            cwd = "."
            try:
                stored = (
                    await self.session_manager.session_store.load(params.session_id)
                    if self.session_manager.session_store
                    else None
                )
                if stored and stored.cwd:
                    cwd = stored.cwd
            except Exception:  # noqa: BLE001
                pass  # Use default cwd

            try:
                # Recreate session with same ID using default agent
                await self.session_manager.create_session(
                    agent=self.default_agent,
                    cwd=cwd,
                    client=self.client,
                    acp_agent=self,
                    session_id=params.session_id,
                    client_capabilities=self.client_capabilities,
                    client_info=self.client_info,
                    subagent_display_mode=self.subagent_display_mode,
                )
                if session := self.session_manager.get_session(params.session_id):
                    # Initialize session extras
                    self.tasks.create_task(session.send_available_commands_update())
                    self.tasks.create_task(session.agent.load_rules(session.cwd))
            except Exception:
                logger.exception("Failed to recreate session", session_id=params.session_id)
                return PromptResponse(stop_reason="end_turn", user_message_id=params.message_id)

        try:
            if not session:
                raise ValueError(f"Session {params.session_id} not found")  # noqa: TRY301
            stop_reason = await session.process_prompt(params.prompt)
            # Return the actual stop reason from the session
        except Exception as e:
            logger.exception("Failed to process prompt", session_id=params.session_id)
            msg = f"Error processing prompt: {e}"
            if session:
                # Send error notification asynchronously to avoid blocking response
                name = f"error_notification_{params.session_id}"
                self.tasks.create_task(session._send_error_notification(msg), name=name)

            return PromptResponse(stop_reason="end_turn", user_message_id=params.message_id)
        else:
            response = PromptResponse(
                stop_reason=stop_reason,
                user_message_id=params.message_id,
                usage=session.last_usage,
            )
            logger.info("Returning PromptResponse", stop_reason=stop_reason)
            return response

    async def stop_session(self, params: StopSessionRequest) -> StopSessionResponse:
        """Stop an active session and free its resources.

        Cancels any ongoing work (like session/cancel) and then
        closes the session and releases all associated resources.
        """
        logger.info("Stopping session", session_id=params.session_id)
        try:
            # Cancel ongoing work first
            if session := self.session_manager.get_session(params.session_id):
                await session.cancel()
            # Close and release session resources
            await self.session_manager.close_session(params.session_id)
            logger.info("Session stopped", session_id=params.session_id)
        except Exception:
            logger.exception("Failed to stop session", session_id=params.session_id)
        return StopSessionResponse()

    async def cancel(self, params: CancelNotification) -> None:
        """Cancel operations for a session."""
        logger.info("Cancelling session", session_id=params.session_id)
        try:
            # Get session and cancel it
            if session := self.session_manager.get_session(params.session_id):
                await session.cancel()
                logger.info("Cancelled operations", session_id=params.session_id)
            else:
                logger.warning("Session not found for cancellation", session_id=params.session_id)

        except Exception:
            logger.exception("Failed to cancel session", session_id=params.session_id)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Handle extension methods including MCP-over-ACP lifecycle messages.

        Args:
            method: The extension method name.
            params: Method parameters.

        Returns:
            Response dictionary.
        """
        result: dict[str, Any] = {}
        match method:
            case "providers/list":
                providers = self.provider_router.get_providers()
                result = {"providers": [p.model_dump(mode="json") for p in providers]}
            case "providers/set":
                await self._handle_providers_set(params)
                result = {"success": True}
            case "providers/disable":
                await self._handle_providers_disable(params)
                result = {"success": True}
            case _:
                result = {}
        return result

    async def _handle_providers_set(self, params: dict[str, Any]) -> None:
        """Handle providers/set extension method.

        Raises:
            RequestError: If provider_id is unknown.
        """
        from acp.exceptions import RequestError

        provider_id = params.get("provider_id") or params.get("id", "")
        base_url = params.get("base_url")
        api_key_id = params.get("api_key_id")
        try:
            await self.provider_router.set_provider_override(
                provider_id, base_url=base_url, api_key_id=api_key_id
            )
        except ValueError as e:
            raise RequestError.invalid_params({"provider_id": provider_id}) from e

    async def _handle_providers_disable(self, params: dict[str, Any]) -> None:
        """Handle providers/disable extension method.

        Raises:
            RequestError: If provider_id is unknown.
        """
        from acp.exceptions import RequestError

        provider_id = params.get("provider_id") or params.get("id", "")
        try:
            await self.provider_router.disable_provider(provider_id)
        except ValueError as e:
            raise RequestError.invalid_params({"provider_id": provider_id}) from e

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None

    async def close(self) -> None:
        """Close the agent and clean up all resources."""
        logger.info("Closing AgentPoolACPAgent")
        await self.cleanup_all_session_agents()

    async def set_session_mode(
        self, params: SetSessionModeRequest
    ) -> SetSessionModeResponse | None:
        """Set the session mode (change tool confirmation level).

        Calls set_mode directly on the agent with the mode_id, allowing the agent
        to handle mode-specific logic (e.g., acceptEdits auto-allowing edit tools).
        """
        from agentpool.agents.acp_agent import ACPAgent as ACPAgentClient

        session = self.session_manager.get_session(params.session_id)
        if not session:
            logger.warning("Session not found for mode switch", session_id=params.session_id)
            return None
        try:
            # Call set_mode directly - agent handles mode-specific logic
            await session.agent.set_mode(params.mode_id, category_id="mode")
            # Update stored mode state for ACPAgent
            if (
                isinstance(session.agent, ACPAgentClient)
                and session.agent._state
                and session.agent._state.modes
            ):
                session.agent._state.modes.current_mode_id = params.mode_id

            logger.info("Set mode", mode_id=params.mode_id, session_id=params.session_id)
            return SetSessionModeResponse()

        except Exception:
            logger.exception("Failed to set session mode", session_id=params.session_id)
            return None

    async def set_session_model(
        self, params: SetSessionModelRequest
    ) -> SetSessionModelResponse | None:
        """Set the session model.

        Changes the model for the active agent in the session.
        Validates that the requested model is available via agent.get_available_models()
        OR is a configured variant in the manifest.
        """
        session = self.session_manager.get_session(params.session_id)
        if not session:
            msg = "Session not found for model switch"
            logger.warning(msg, session_id=params.session_id)
            return None
        try:
            # Build list of valid model IDs from both tokonomics and configured variants
            valid_model_ids: set[str] = set()

            # Get tokonomics models
            if toko_models := await session.agent.get_available_models():
                valid_model_ids.update(
                    m.id_override if m.id_override else m.id for m in toko_models
                )

            # Get configured variants from manifest
            agent_pool = getattr(session.agent, "agent_pool", None)
            manifest = getattr(agent_pool, "manifest", None) if agent_pool else None
            if manifest and manifest.model_variants:
                valid_model_ids.update(manifest.model_variants.keys())

            # Validate the requested model
            if params.model_id not in valid_model_ids:
                logger.warning(
                    "Model not in available models",
                    model_id=params.model_id,
                    available=list(valid_model_ids),
                )
                return None

            # Set the model on the agent (all agents now have async set_model)
            await session.agent.set_model(params.model_id)
            logger.info("Set model", model_id=params.model_id, session_id=params.session_id)
            return SetSessionModelResponse()
        except (AttributeError, NotImplementedError) as e:
            logger.warning(
                "Agent does not support model switching",
                error=str(e),
                session_id=params.session_id,
            )
            return None
        except Exception:
            logger.exception("Failed to set session model", session_id=params.session_id)
            return None

    async def _swap_session_agent(self, session_id: str, new_agent_name: str) -> dict[str, bool]:
        """Swap the active agent for a session.

        Acquires the session agent lock to prevent concurrent swaps,
        then delegates to the session's switch_active_agent method.

        Args:
            session_id: The session to swap agent for.
            new_agent_name: Name of the agent to switch to.

        Returns:
            Dict with success flag.

        Raises:
            RequestError: If swap fails (session not found, agent unknown,
                or prompt is active).
        """
        from acp.exceptions import RequestError

        session = self.session_manager.get_session(session_id)
        if not session:
            msg = {"session_id": session_id, "reason": "Session not found"}
            raise RequestError.invalid_params(msg)

        # Block swap during active prompt
        if hasattr(session, "_task_lock") and session._task_lock.locked():
            msg = {"session_id": session_id, "reason": "Prompt active"}
            raise RequestError.invalid_params(msg)

        # Ensure lock exists for this session
        if session_id not in self._session_agent_locks:
            self._session_agent_locks[session_id] = asyncio.Lock()

        async with self._session_agent_locks[session_id]:
            await session.switch_active_agent(new_agent_name)
            # Update session agent registry so subsequent get_or_create calls
            # return the new agent instead of the cached old one.
            self._session_agents[session_id] = session.agent

        # TODO: Clean up _session_agent_locks when session ends.
        # Currently no session lifecycle hook exists to safely pop locks.
        # Memory impact is minimal (one Lock per active session).

        return {"success": True}

    async def set_session_config_option(
        self, params: SetSessionConfigOptionRequest
    ) -> SetSessionConfigOptionResponse | None:
        """Set a session config option.

        Forwards the config option change to the agent's set_mode method
        or handles agent_role swap, then returns the updated config options.
        """
        session = self.session_manager.get_session(params.session_id)
        if not session or not session.agent:
            msg = "Session not found for config option change"
            logger.warning(msg, session_id=params.session_id)
            return None
        logger.info(
            "Set config option",
            config_id=params.config_id,
            value=params.value,
            session_id=params.session_id,
        )
        try:
            if params.config_id == "agent_role":
                await self._set_agent_role(session, params.value)
            else:
                # Forward to agent's set_mode method
                # config_id maps to category_id, value maps to mode_id
                await session.agent.set_mode(params.value, category_id=params.config_id)
            # Return updated config options
            config_options = await get_session_config_options(session.agent)
            return SetSessionConfigOptionResponse(config_options=config_options)
        except Exception:
            logger.exception("Failed to set session config option", session_id=params.session_id)
            return None

    async def _set_agent_role(self, session: Any, agent_name: str) -> None:
        """Validate and swap to the requested agent role.

        Args:
            session: The active session.
            agent_name: Target agent name.

        Raises:
            RequestError: If agent name is not in pool.
        """
        from acp.exceptions import RequestError

        pool = session.agent.agent_pool
        if pool is None or agent_name not in pool.all_agents:
            msg = {"agent_role": agent_name, "reason": "Unknown agent"}
            raise RequestError.invalid_params(msg)
        await self._swap_session_agent(session.session_id, agent_name)

    async def swap_pool(self, config_path: str, agent_name: str | None = None) -> list[str]:
        """Swap the agent pool with a new one from configuration.

        This coordinates the full pool swap:
        1. Acquires session_manager._lock to serialize with create_session()
        2. Clears all active sessions while holding the lock
        3. Closes all sessions (outside the lock)
        4. Cleans up all per-session agents
        5. Delegates to server.swap_pool() for pool lifecycle
        6. Updates internal references and cached agent config

        Args:
            config_path: Path to the new agent configuration file
            agent_name: Optional specific agent name to use as default

        Returns:
            List of agent names in the new pool

        Raises:
            RuntimeError: If server reference is not set
            ValueError: If config is invalid or agent not found
        """
        if not self.server:
            msg = "Server reference not set - cannot swap pool"
            raise RuntimeError(msg)

        logger.info("Swapping pool", config_path=config_path, agent=agent_name)

        # Acquire session_manager._lock to serialize with create_session()
        async with self.session_manager._lock:
            # 1. Copy and clear all active sessions while holding the lock
            sessions = list(self.session_manager._active.values())
            self.session_manager._active.clear()
            # 2. Set swap flag to prevent new session creation during swap
            self._swap_in_progress = True

        # 3. Close all sessions (outside the lock - may take time)
        for session in sessions:
            try:
                await session.close()
            except Exception:
                logger.exception(
                    "Error closing session during pool swap",
                    session_id=session.session_id,
                )

        # 4. Clean up all per-session agents
        await self.cleanup_all_session_agents()

        try:
            # 5. Swap pool
            new_agent = await self.server.swap_pool(config_path, agent_name)

            # 6. Update cached agent config from new pool
            pool = new_agent.agent_pool
            if pool is None:
                msg = "New agent has no associated pool"
                raise RuntimeError(msg)

            # Re-resolve _agent_config from the new pool's manifest
            if pool.main_agent and pool.main_agent.name in pool.manifest.agents:
                cfg = pool.manifest.agents[pool.main_agent.name]
                from agentpool.models.agents import NativeAgentConfig
                if isinstance(cfg, NativeAgentConfig):
                    if cfg.name is None:
                        cfg = cfg.model_copy(update={"name": pool.main_agent.name})
                    self._agent_config = cfg
            elif pool.manifest.agents:
                cfg = next(iter(pool.manifest.agents.values()))
                from agentpool.models.agents import NativeAgentConfig
                if isinstance(cfg, NativeAgentConfig):
                    self._agent_config = cfg
            else:
                self._agent_config = None

            # 7. Update default_agent reference and pool
            self.default_agent = new_agent
            self.session_manager._pool = pool

            # 8. Invalidate sessions cache
            self._sessions_cache = None

            agent_names = list(pool.all_agents.keys())
            logger.info("Pool swap complete", agent_names=agent_names)
            return agent_names
        finally:
            # 9. Clear swap flag - new sessions can now be created
            # This MUST be in finally to prevent permanent blocking on error
            self._swap_in_progress = False
