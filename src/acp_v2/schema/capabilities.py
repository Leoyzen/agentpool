"""ACP v2 unified capabilities schema.

v2 changes from v1:
- Single ``capabilities`` field (replaces clientCapabilities/agentCapabilities)
- Object markers: {} = supported, omitted/null = unsupported
- Session-scoped capabilities nested under ``session``
- ``session`` is optional (NES-only agents can omit it)
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Supported(BaseModel):
    """Empty object = capability supported. Omitted/null = unsupported."""

    model_config = ConfigDict(extra="allow")


class PromptCapabilities(BaseModel):
    """Prompt content type support markers."""

    image: _Supported | None = None
    audio: _Supported | None = None
    embedded_context: _Supported | None = Field(
        default=None, alias="embeddedContext"
    )

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",
    )


class McpCapabilities(BaseModel):
    """MCP transport support markers."""

    stdio: _Supported | None = None
    http: _Supported | None = None

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",
    )


class AuthCapabilities(BaseModel):
    """Authentication-related capabilities."""

    terminal: _Supported | None = None

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",
    )


class SessionCapabilities(BaseModel):
    """Session-scoped capability group."""

    prompt: PromptCapabilities | None = None
    mcp: McpCapabilities | None = None
    load: _Supported | None = None
    delete: _Supported | None = None
    additional_directories: _Supported | None = Field(
        default=None, alias="additionalDirectories"
    )

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",
    )


class Capabilities(BaseModel):
    """Unified v2 capabilities for both client and agent.

    Replaces v1's separate clientCapabilities and agentCapabilities.
    Object markers: {} = supported, omitted/null = unsupported.
    """

    session: SessionCapabilities | None = None
    auth: AuthCapabilities | None = None
    providers: _Supported | None = None
    turn_complete: _Supported | None = Field(default=None, alias="turnComplete")

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",
    )

    @classmethod
    def empty(cls) -> Capabilities:
        """Return capabilities with no markers (everything unsupported)."""
        return cls()
