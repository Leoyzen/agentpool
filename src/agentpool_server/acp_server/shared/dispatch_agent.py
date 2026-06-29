"""ACP dispatch agent for automatic v1/v2 version routing.

DispatchAgent implements the v1 ``Agent`` interface as a shell. On
``initialize()``, it inspects ``protocolVersion`` and creates either
``AgentPoolACPAgent`` (v1) or ``AgentPoolACPAgentV2`` (v2) as its
internal delegate. All subsequent method calls are forwarded.

When SessionPool is unavailable, v2 requests degrade to v1 behavior
with ``protocolVersion=2`` and ``_meta.fallback=true``.
"""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass, field
from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger
from agentpool_server.acp_server.shared.version_negotiator import (
    VersionNegotiator,
)


if TYPE_CHECKING:
    from acp import Client
    from acp.schema import (
        AuthenticateRequest,
        AuthenticateResponse,
        CancelNotification,
        CloseSessionRequest,
        CloseSessionResponse,
        ForkSessionRequest,
        ForkSessionResponse,
        InitializeRequest,
        InitializeResponse,
        ListSessionsRequest,
        ListSessionsResponse,
        LoadSessionRequest,
        LoadSessionResponse,
        LogoutRequest,
        NewSessionRequest,
        NewSessionResponse,
        PromptRequest,
        PromptResponse,
        ResumeSessionRequest,
        ResumeSessionResponse,
        SetSessionConfigOptionRequest,
        SetSessionConfigOptionResponse,
        SetSessionModelRequest,
        SetSessionModelResponse,
        SetSessionModeRequest,
        SetSessionModeResponse,
    )
    from agentpool.agents.base_agent import BaseAgent

logger = get_logger(__name__)


@dataclass
class DispatchAgent:
    """Version-routing agent that delegates to v1 or v2 based on initialize.

    Created by ``server.py`` as the agent factory for ``serve()``.
    At ``initialize()`` time, reads ``protocolVersion`` from the client
    request and creates the appropriate delegate agent.

    Attributes:
        client: ACP client connection.
        default_agent: The AgentPool agent to use for sessions.
        debug_commands: Enable debug slash commands.
        load_skills: Whether to load client-side skills.
        server: Reference to the ACPServer instance.
        subagent_display_mode: Display mode for subagent output.
    """

    client: Client
    default_agent: BaseAgent[Any, Any]
    _: KW_ONLY
    debug_commands: bool = False
    load_skills: bool | None = None
    server: Any = None
    subagent_display_mode: str = "legacy"

    _delegate: Any = field(init=False, default=None)
    _negotiated_version: int = field(init=False, default=0)
    _fallback: bool = field(init=False, default=False)

    @property
    def _pool(self) -> Any:
        return self.default_agent.agent_pool

    @property
    def _session_pool_enabled(self) -> bool:
        pool = self._pool
        if pool is None:
            return False
        return bool(
            pool.manifest.acp and pool.manifest.acp.use_session_pool
        )

    def _create_v1_delegate(self) -> Any:
        from agentpool_server.acp_server.v1.acp_agent import (
            AgentPoolACPAgent,
        )

        return AgentPoolACPAgent(
            client=self.client,
            default_agent=self.default_agent,
            debug_commands=self.debug_commands,
            load_skills=self.load_skills,
            server=self.server,
            subagent_display_mode=self.subagent_display_mode,  # type: ignore[arg-type]
        )

    def _create_v2_delegate(self) -> Any:
        from agentpool_server.acp_server.v2.acp_agent import (
            AgentPoolACPAgentV2,
        )

        return AgentPoolACPAgentV2(
            client=self.client,
            default_agent=self.default_agent,
            debug_commands=self.debug_commands,
            load_skills=self.load_skills,
            server=self.server,
            subagent_display_mode=self.subagent_display_mode,
        )

    async def initialize(self, params: InitializeRequest) -> InitializeResponse:
        """Negotiate version and create the delegate agent."""
        requested = params.protocol_version
        version = VersionNegotiator.negotiate(requested)
        self._negotiated_version = version

        if version >= 2 and self._session_pool_enabled:
            self._delegate = self._create_v2_delegate()
            logger.info("DispatchAgent: routed to v2 agent")
        elif version >= 2 and not self._session_pool_enabled:
            self._delegate = self._create_v1_delegate()
            self._fallback = True
            logger.info(
                "DispatchAgent: v2 requested, degraded to v1 (SessionPool disabled)"
            )
        else:
            self._delegate = self._create_v1_delegate()
            logger.info("DispatchAgent: routed to v1 agent")

        response = await self._delegate.initialize(params)

        if self._fallback:
            response.protocol_version = 2
            if response.field_meta is None:
                response.field_meta = {}
            response.field_meta["fallback"] = True

        return response

    async def new_session(self, params: NewSessionRequest) -> NewSessionResponse:
        return await self._delegate.new_session(params)

    async def load_session(self, params: LoadSessionRequest) -> LoadSessionResponse:
        return await self._delegate.load_session(params)

    async def list_sessions(
        self, params: ListSessionsRequest
    ) -> ListSessionsResponse:
        return await self._delegate.list_sessions(params)

    async def prompt(self, params: PromptRequest) -> PromptResponse:
        return await self._delegate.prompt(params)

    async def cancel(self, params: CancelNotification) -> None:
        await self._delegate.cancel(params)

    async def close_session(
        self, params: CloseSessionRequest
    ) -> CloseSessionResponse:
        return await self._delegate.close_session(params)

    async def fork_session(self, params: ForkSessionRequest) -> ForkSessionResponse:
        return await self._delegate.fork_session(params)

    async def resume_session(
        self, params: ResumeSessionRequest
    ) -> ResumeSessionResponse:
        return await self._delegate.resume_session(params)

    async def authenticate(
        self, params: AuthenticateRequest
    ) -> AuthenticateResponse | None:
        if hasattr(self._delegate, "authenticate"):
            return await self._delegate.authenticate(params)
        return None

    async def logout(self, params: LogoutRequest) -> None:
        if hasattr(self._delegate, "logout"):
            await self._delegate.logout(params)

    async def auth_login(self, params: Any) -> Any:
        if hasattr(self._delegate, "auth_login"):
            return await self._delegate.auth_login(params)
        return None

    async def auth_logout(self, params: Any) -> Any:
        if hasattr(self._delegate, "auth_logout"):
            return await self._delegate.auth_logout(params)
        return None

    async def set_session_mode(
        self, params: SetSessionModeRequest
    ) -> SetSessionModeResponse | None:
        if hasattr(self._delegate, "set_session_mode"):
            return await self._delegate.set_session_mode(params)
        return None

    async def set_session_model(
        self, params: SetSessionModelRequest
    ) -> SetSessionModelResponse | None:
        if hasattr(self._delegate, "set_session_model"):
            return await self._delegate.set_session_model(params)
        return None

    async def set_session_config_option(
        self, params: SetSessionConfigOptionRequest
    ) -> SetSessionConfigOptionResponse | None:
        return await self._delegate.set_session_config_option(params)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self._delegate.ext_method(method, params)

    async def ext_notification(
        self, method: str, params: dict[str, Any]
    ) -> None:
        await self._delegate.ext_notification(method, params)

    async def close(self) -> None:
        await self._delegate.close()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if self._delegate is None:
            raise AttributeError(
                f"DispatchAgent has no attribute '{name}' "
                "(delegate not yet created — initialize required)"
            )
        return getattr(self._delegate, name)
