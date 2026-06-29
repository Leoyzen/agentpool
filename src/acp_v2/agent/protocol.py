"""ACP v2 agent-side protocol interface."""

from __future__ import annotations

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


class Agent:  # noqa: PLR0904
    """ACP v2 Agent protocol interface.

    Agents implement this interface to handle client requests.
    v2 differences from v1:
    - ``prompt()`` returns empty ``PromptResponse`` immediately
    - ``auth_login()`` / ``auth_logout()`` replace authenticate/logout
    - No ``set_session_mode`` (removed in v2)
    """

    async def initialize(self, params: InitializeRequest) -> InitializeResponse:
        """Negotiate protocol version and exchange capabilities."""
        raise NotImplementedError

    async def new_session(self, params: NewSessionRequest) -> NewSessionResponse:
        """Create a new conversation session."""
        raise NotImplementedError

    async def load_session(self, params: LoadSessionRequest) -> LoadSessionResponse:
        """Load an existing session from storage."""
        raise NotImplementedError

    async def list_sessions(self, params: ListSessionsRequest) -> ListSessionsResponse:
        """List available sessions."""
        raise NotImplementedError

    async def prompt(self, params: PromptRequest) -> PromptResponse:
        """Accept a prompt. Returns immediately with empty result.

        Turn completion is communicated via ``state_update`` notification.
        """
        raise NotImplementedError

    async def cancel(self, params: CancelNotification) -> None:
        """Cancel ongoing operations for a session."""
        raise NotImplementedError

    async def close_session(self, params: CloseSessionRequest) -> CloseSessionResponse:
        """Close a session and release resources."""
        raise NotImplementedError

    async def fork_session(self, params: NewSessionRequest) -> ForkSessionResponse:
        """Fork an existing session."""
        raise NotImplementedError

    async def resume_session(
        self, params: LoadSessionRequest
    ) -> ResumeSessionResponse:
        """Resume a paused session."""
        raise NotImplementedError

    async def auth_login(self, params: LoginAuthRequest) -> LoginAuthResponse:
        """Authenticate (v2 auth/login, replaces v1 authenticate)."""
        raise NotImplementedError

    async def auth_logout(self, params: LogoutAuthRequest) -> LogoutAuthResponse:
        """Logout (v2 auth/logout, now required for all agents)."""
        raise NotImplementedError

    async def set_session_config_option(
        self, params: SetSessionConfigOptionRequest
    ) -> SetSessionConfigOptionResponse:
        """Set a session config option (replaces set_mode/set_model)."""
        raise NotImplementedError

    async def ext_method(self, method: str, params: dict) -> dict:
        """Handle extension methods."""
        raise NotImplementedError

    async def ext_notification(self, method: str, params: dict) -> None:
        """Handle extension notifications."""
        raise NotImplementedError

    async def close(self) -> None:
        """Close the agent and clean up resources."""
        raise NotImplementedError
