"""Agent and command models."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agentpool_server.opencode_server.models.base import OpenCodeBaseModel
from agentpool_server.opencode_server.models.common import ModelRef  # noqa: TC001


class AgentPermission(OpenCodeBaseModel):
    """Agent permission settings."""

    edit: Literal["ask", "allow", "deny"] = "ask"
    bash: dict[str, Literal["ask", "allow", "deny"]] = Field(default_factory=dict)
    skill: dict[str, Literal["ask", "allow", "deny"]] = Field(default_factory=dict)
    webfetch: Literal["ask", "allow", "deny"] | None = None
    doom_loop: Literal["ask", "allow", "deny"] | None = None
    external_directory: Literal["ask", "allow", "deny"] | None = None


class Agent(OpenCodeBaseModel):
    """Agent information matching SDK type."""

    name: str
    description: str | None = None
    mode: Literal["subagent", "primary", "all"] = "primary"
    native: bool | None = None
    hidden: bool | None = None
    default: bool | None = None
    top_p: float | None = None
    temperature: float | None = None
    color: str | None = None
    permission: AgentPermission = Field(default_factory=AgentPermission)
    model: ModelRef | None = None
    prompt: str | None = None
    tools: dict[str, bool] = Field(default_factory=dict)
    options: dict[str, str] = Field(default_factory=dict)


class Command(OpenCodeBaseModel):
    """Slash command."""

    name: str
    description: str = ""


class SkillInfo(OpenCodeBaseModel):
    """Skill information."""

    name: str
    """Skill name."""

    description: str
    """Skill description."""

    location: str
    """File path where the skill is defined."""

    content: str
    """Skill content (e.g. SKILL.md body)."""


class ProviderAuthMethod(OpenCodeBaseModel):
    """Authentication method for a provider."""

    type: Literal["oauth", "api"]
    """Auth type."""

    label: str
    """Human-readable label for the auth method."""


class ProviderAuthAuthorization(OpenCodeBaseModel):
    """Response from starting a provider OAuth flow."""

    url: str
    """URL to open in browser for authorization."""

    method: Literal["auto", "code"]
    """Authorization method."""

    instructions: str
    """Instructions to display to the user."""


class WorktreeInfo(OpenCodeBaseModel):
    """Git worktree information."""

    name: str
    """Worktree name."""

    branch: str
    """Git branch name."""

    directory: str
    """Full path to the worktree directory."""


class WorktreeCreateRequest(OpenCodeBaseModel):
    """Request to create a new git worktree."""

    name: str | None = None
    """Optional worktree name. Auto-generated if not provided."""

    start_command: str | None = None
    """Optional startup script to run after creation."""


class WorktreeRemoveRequest(OpenCodeBaseModel):
    """Request to remove a git worktree."""

    directory: str
    """Worktree directory path to remove."""


class WorktreeResetRequest(OpenCodeBaseModel):
    """Request to reset a git worktree."""

    directory: str
    """Worktree directory path to reset."""


class AuthInfo(OpenCodeBaseModel):
    """Authentication credential info."""

    type: str = "api_key"
    """Auth type (e.g., 'api_key', 'oauth')."""

    token: str | None = None
    """API key or access token."""

    refresh: str | None = None
    """Refresh token (for OAuth)."""

    expires: int | None = None
    """Token expiry timestamp."""
