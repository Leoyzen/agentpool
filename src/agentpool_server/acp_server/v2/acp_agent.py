"""ACP v2 Agent implementation for AgentPool.

Implements the v2 Agent protocol interface with:
- PROTOCOL_VERSION = 2
- Unified capabilities + info in initialize
- prompt() returns immediately (delegates to ACPProtocolHandlerV2)
- auth_login/auth_logout (replaces authenticate/logout)
- No set_session_mode (removed in v2)
"""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass, field
from importlib.metadata import version as _version
from typing import TYPE_CHECKING, Any, ClassVar

from acp.schema.common import Implementation
from acp_v2.agent.protocol import Agent as ACPAgentV2
from acp_v2.schema.capabilities import Capabilities, SessionCapabilities
from acp_v2.schema.client_requests import (
    CancelNotification,
    CloseSessionRequest,
    InitializeRequest,
    ListSessionsRequest,
    LoadSessionRequest,
    LoginAuthRequest,
    LogoutAuthRequest,
    NewSessionRequest,
    PromptRequest,
    SetSessionConfigOptionRequest,
)
from acp_v2.schema.client_responses import (
    CloseSessionResponse,
    ForkSessionResponse,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    LoginAuthResponse,
    LogoutAuthResponse,
    NewSessionResponse,
    PromptResponse,
    ResumeSessionResponse,
    SetSessionConfigOptionResponse,
)
from agentpool.log import get_logger
from agentpool_server.acp_server.acp_mcp_manager import AcpMcpConnectionManager
from agentpool_server.acp_server.commands.skill_commands import ACPSkillBridge
from agentpool_server.acp_server.provider_router import ProviderRouter
from agentpool_server.acp_server.session_manager import ACPSessionManager


if TYPE_CHECKING:
    from acp import Client
    from agentpool import AgentPool
    from agentpool.agents.base_agent import BaseAgent
    from agentpool_server.acp_server.v2.handler import ACPProtocolHandlerV2

logger = get_logger(__name__)


@dataclass
class AgentPoolACPAgentV2(ACPAgentV2):
    """ACP v2 Agent implementation for AgentPool.

    Bridges AgentPool with the v2 ACP JSON-RPC protocol.
    Key v2 differences:
    - initialize returns unified capabilities + info
    - prompt returns empty result immediately
    - auth_login/auth_logout replace authenticate/logout
    - No set_session_mode (use config options)
    """

    PROTOCOL_VERSION: ClassVar = 2

    client: Client
    default_agent: BaseAgent[Any, Any]
    _: KW_ONLY
    debug_commands: bool = False
    load_skills: bool | None = None
    server: Any = field(default=None)
    subagent_display_mode: str = "legacy"

    _skill_bridge: ACPSkillBridge | None = field(init=False, default=None)
    _mcp_manager: AcpMcpConnectionManager = field(init=False)
    _protocol_handler: ACPProtocolHandlerV2 | None = field(init=False, default=None)
    _initialized: bool = field(init=False, default=False)
    _agent_config: Any = field(init=False, default=None)
    provider_router: ProviderRouter = field(init=False)

    def __post_init__(self) -> None:
        pool = self.agent_pool
        if pool is None:
            msg = "Default agent has no associated pool"
            raise RuntimeError(msg)
        self.session_manager = ACPSessionManager(pool=pool)
        self._mcp_manager = AcpMcpConnectionManager()
        self.provider_router = ProviderRouter(pool.manifest)
        self._setup_skill_bridge()
        if pool.manifest.acp and pool.manifest.acp.use_session_pool:
            from agentpool_server.acp_server.v2.handler import ACPProtocolHandlerV2

            self._protocol_handler = ACPProtocolHandlerV2(
                agent_pool=pool,
                session_manager=self.session_manager,
                client=self.client,
            )
            logger.info("ACPProtocolHandlerV2 initialized for v2 SessionPool mode")

    @property
    def agent_pool(self) -> AgentPool[Any] | None:
        return self.default_agent.agent_pool

    def _setup_skill_bridge(self) -> None:
        pool = self.agent_pool
        if pool is None:
            return
        skill_commands = getattr(pool, "skill_commands", None)
        if skill_commands is None:
            return
        self._skill_bridge = ACPSkillBridge()
        skill_commands.on_command_change(self._skill_bridge.handle_change)

    async def initialize(self, params: InitializeRequest) -> InitializeResponse:
        """Negotiate v2 protocol and exchange unified capabilities."""
        self._initialized = True
        pool = self.agent_pool
        manifest = pool.manifest if pool else None
        self.provider_router = ProviderRouter(manifest)
        return InitializeResponse(
            protocol_version=self.PROTOCOL_VERSION,
            capabilities=Capabilities(
                session=SessionCapabilities(),
            ),
            info=Implementation(
                name="agentpool",
                title="AgentPool",
                version=_version("agentpool"),
            ),
        )

    async def new_session(self, params: NewSessionRequest) -> NewSessionResponse:
        if not self._initialized:
            raise RuntimeError("Agent not initialized")
        session_id = await self.session_manager.create_session(
            agent=self.default_agent,
            cwd=params.cwd,
            client=self.client,
            acp_agent=self,
            mcp_servers=params.mcp_servers,
        )
        return NewSessionResponse(session_id=session_id)

    async def load_session(self, params: LoadSessionRequest) -> LoadSessionResponse:
        if not self._initialized:
            raise RuntimeError("Agent not initialized")
        session = await self.session_manager.resume_session(
            session_id=params.session_id,
            client=self.client,
            acp_agent=self,
            mcp_servers=params.mcp_servers,
        )
        if not session:
            return LoadSessionResponse()
        return LoadSessionResponse()

    async def list_sessions(self, params: ListSessionsRequest) -> ListSessionsResponse:
        if not self._initialized:
            raise RuntimeError("Agent not initialized")
        first_session = next(iter(self.session_manager._acp_sessions.values()), None)
        agent = first_session.agent if first_session else self.default_agent
        try:
            agent_sessions = await agent.list_sessions()
            from agentpool_server.acp_server.converters import to_session_info
            sessions = [to_session_info(s) for s in agent_sessions]
            return ListSessionsResponse(sessions=sessions)
        except Exception:
            logger.exception("Failed to list sessions")
            return ListSessionsResponse(sessions=[])

    async def prompt(self, params: PromptRequest) -> PromptResponse:
        """v2 prompt — returns immediately, agent runs async."""
        if not self._initialized:
            raise RuntimeError("Agent not initialized")
        if self._protocol_handler is not None:
            return await self._protocol_handler.handle_prompt(
                params.session_id, params.prompt
            )
        raise RuntimeError("No v2 protocol handler configured")

    async def cancel(self, params: CancelNotification) -> None:
        if self._protocol_handler is not None:
            await self._protocol_handler.cancel_session(params.session_id)
        elif session := self.session_manager.get_session(params.session_id):
            await session.cancel()

    async def close_session(self, params: CloseSessionRequest) -> CloseSessionResponse:
        if self._protocol_handler is not None:
            await self._protocol_handler.close_session(params.session_id)
            return CloseSessionResponse()
        await self.session_manager.close_session(params.session_id)
        return CloseSessionResponse()

    async def fork_session(self, params: NewSessionRequest) -> ForkSessionResponse:
        session_id = await self.session_manager.create_session(
            agent=self.default_agent,
            cwd=params.cwd,
            client=self.client,
            acp_agent=self,
            mcp_servers=params.mcp_servers,
        )
        return ForkSessionResponse(session_id=session_id)

    async def resume_session(self, params: LoadSessionRequest) -> ResumeSessionResponse:
        session = await self.session_manager.resume_session(
            session_id=params.session_id,
            client=self.client,
            acp_agent=self,
            mcp_servers=params.mcp_servers,
        )
        if not session:
            return ResumeSessionResponse()
        return ResumeSessionResponse()

    async def auth_login(self, params: LoginAuthRequest) -> LoginAuthResponse:
        logger.info("v2 auth/login requested", method_id=params.method_id)
        return LoginAuthResponse()

    async def auth_logout(self, params: LogoutAuthRequest) -> LogoutAuthResponse:
        logger.info("v2 auth/logout requested")
        return LogoutAuthResponse()

    async def set_session_config_option(
        self, params: SetSessionConfigOptionRequest
    ) -> SetSessionConfigOptionResponse:
        session = self.session_manager.get_session(params.session_id)
        if not session or not session.agent:
            logger.warning("Session not found for config option", session_id=params.session_id)
            return SetSessionConfigOptionResponse()
        if params.config_id == "agent_role":
            from acp.exceptions import RequestError
            pool = session.agent.agent_pool
            if pool is None or params.value not in pool.all_agents:
                raise RequestError.invalid_params({"agent_role": params.value})
            await session.switch_active_agent(params.value)
        else:
            await session.agent.set_mode(params.value, category_id=params.config_id)
        from agentpool_server.acp_server.v1.acp_agent import get_session_config_options
        config_options = await get_session_config_options(session.agent)
        return SetSessionConfigOptionResponse(config_options=config_options)

    async def ext_method(self, method: str, params: dict) -> dict:
        match method:
            case "mcp/message":
                connection_id = params.get("connectionId", "")
                conn = self._mcp_manager.get_connection(connection_id)
                if conn is not None:
                    self.session_manager._pool.tasks.create_task(
                        conn.handle_client_message(params)
                    )
                return {}
            case _:
                return {}

    async def ext_notification(self, method: str, params: dict) -> None:
        pass

    async def close(self) -> None:
        logger.info("Closing AgentPoolACPAgentV2")
        try:
            await self._mcp_manager.close_all()
        except Exception:
            logger.exception("Failed to close MCP connections")
