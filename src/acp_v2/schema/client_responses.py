"""ACP v2 client response schemas.

v2 changes from v1:
- Unified ``capabilities`` + ``info`` (no agentCapabilities/agentInfo)
- PromptResponse returns empty {} (no stopReason)
- NewSessionResponse has no ``modes`` field
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from acp.schema.base import Response
from acp.schema.common import Implementation
from acp.schema.session_state import SessionConfigOption  # noqa: TC001
from acp_v2.schema.capabilities import Capabilities


StopReason = Literal[
    "end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"
]


class InitializeResponse(Response):
    """v2 initialize response with unified capabilities and info."""

    protocol_version: int = Field(alias="protocolVersion")
    capabilities: Capabilities | None = None
    info: Implementation | None = None
    auth_methods: list[Any] | None = Field(default=None, alias="authMethods")


class NewSessionResponse(Response):
    """v2 new session response. No modes field."""

    session_id: str = Field(alias="sessionId")
    config_options: list[SessionConfigOption] | None = None


class LoadSessionResponse(Response):
    """v2 load session response. No modes field."""

    config_options: list[SessionConfigOption] | None = None


class PromptResponse(Response):
    """v2 prompt response — empty result, turn completes via state_update."""


class LoginAuthResponse(Response):
    """v2 auth/login response."""


class LogoutAuthResponse(Response):
    """v2 auth/logout response."""


class ListSessionsResponse(Response):
    """List of available sessions."""

    sessions: list[Any] = Field(default_factory=list)


class CloseSessionResponse(Response):
    """Close session response."""


class ResumeSessionResponse(Response):
    """v2 resume session response. No modes field."""

    config_options: list[SessionConfigOption] | None = None


class ForkSessionResponse(Response):
    """Fork session response."""

    session_id: str = Field(alias="sessionId")


class SetSessionConfigOptionResponse(Response):
    """Set config option response with updated options."""

    config_options: list[SessionConfigOption] | None = None
