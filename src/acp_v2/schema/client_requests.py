"""ACP v2 client request schemas.

v2 changes from v1:
- Unified ``capabilities`` field (no clientCapabilities)
- Unified ``info`` field (no clientInfo)
- auth/login replaces authenticate
- auth/logout replaces logout
"""

from __future__ import annotations

from pydantic import Field

from acp.schema.base import Request
from acp.schema.common import Implementation
from acp.schema.content_blocks import ContentBlock  # noqa: TC001
from acp.schema.mcp import McpServer  # noqa: TC001
from acp_v2.schema.capabilities import Capabilities


class InitializeRequest(Request):
    """v2 initialize request with unified capabilities and info."""

    protocol_version: int = Field(alias="protocolVersion")
    capabilities: Capabilities | None = None
    info: Implementation | None = None


class NewSessionRequest(Request):
    """Create a new v2 session. No modes field."""

    cwd: str
    mcp_servers: list[McpServer] | None = Field(default=None, alias="mcpServers")
    additional_directories: list[str] | None = Field(
        default=None, alias="additionalDirectories"
    )


class LoadSessionRequest(Request):
    """Load an existing v2 session."""

    session_id: str = Field(alias="sessionId")
    cwd: str
    mcp_servers: list[McpServer] | None = Field(default=None, alias="mcpServers")
    additional_directories: list[str] | None = Field(
        default=None, alias="additionalDirectories"
    )


class PromptRequest(Request):
    """v2 prompt request. Response returns immediately with empty result."""

    session_id: str = Field(alias="sessionId")
    prompt: list[ContentBlock]


class CancelNotification(Request):
    """Cancel ongoing operations in a session."""

    session_id: str = Field(alias="sessionId")


class LoginAuthRequest(Request):
    """v2 auth/login request (replaces v1 authenticate)."""

    method_id: str = Field(alias="methodId")


class LogoutAuthRequest(Request):
    """v2 auth/logout request (replaces v1 logout, now required)."""


class ListSessionsRequest(Request):
    """List available sessions."""

    cwd: str | None = None
    cursor: str | None = None


class CloseSessionRequest(Request):
    """Close a session."""

    session_id: str = Field(alias="sessionId")


class SetSessionConfigOptionRequest(Request):
    """Set a session config option (replaces set_mode/set_model)."""

    session_id: str = Field(alias="sessionId")
    config_id: str = Field(alias="configId")
    value: str


ClientRequest = (
    InitializeRequest
    | NewSessionRequest
    | LoadSessionRequest
    | PromptRequest
    | CancelNotification
    | LoginAuthRequest
    | LogoutAuthRequest
    | ListSessionsRequest
    | CloseSessionRequest
    | SetSessionConfigOptionRequest
)
