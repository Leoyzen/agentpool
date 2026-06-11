"""Session pool configuration models."""

from __future__ import annotations

import os

from pydantic import ConfigDict, Field
from schemez import Schema


def _env_flag(var_name: str) -> bool:
    """Read a boolean env-var defaulting to True.

    Returns True unless the env var is explicitly set to ``0``, ``false``, or ``no``.
    """
    return os.environ.get(var_name, "true").lower() not in ("0", "false", "no")


class SessionPoolConfig(Schema):
    """Configuration for the SessionPool orchestration layer.

    Controls session lifecycle management, turn execution, event routing,
    and auto-resume capabilities for agent sessions.
    """

    enable_auto_resume: bool = Field(default=True, title="Enable auto-resume")
    """Whether to enable the auto-resume loop for post-turn work."""

    enable_event_bus: bool = Field(default=True, title="Enable event bus")
    """Whether to enable cross-turn event routing via the event bus."""

    session_ttl_seconds: float = Field(
        default=3600.0, gt=0, title="Session TTL seconds"
    )
    """Time-to-live for sessions in seconds. Expired sessions are cleaned up."""

    max_auto_resume: int = Field(default=10, ge=0, title="Max auto-resume")
    """Maximum number of auto-resume iterations per turn loop."""

    max_queue_size: int = Field(default=1000, ge=1, title="Max queue size")
    """Maximum size for event bus subscriber queues."""

    mcp_max_processes: int = Field(default=100, ge=1, title="MCP max processes")
    """Maximum number of MCP processes for per-session agents."""

    model_config = ConfigDict(frozen=True)


class ACPConfig(Schema):
    """ACP protocol-specific configuration."""

    use_session_pool: bool = Field(default=True, title="Use session pool")
    """Whether to use the SessionPool for ACP protocol session management.

    Defaults to True as SessionPool is the mandatory execution entry point
    per the sessionpool-only-execution spec. Setting to False is deprecated.
    """

    model_config = ConfigDict(frozen=True)


class OpenCodeConfig(Schema):
    """OpenCode protocol-specific configuration."""

    use_session_pool: bool = Field(default=True, title="Use session pool")
    """Whether to use the SessionPool for OpenCode protocol session management.

    Defaults to True as SessionPool is the mandatory execution entry point
    per the sessionpool-only-execution spec. Setting to False is deprecated.
    """

    use_session_pool_for_commands: bool = Field(
        default_factory=lambda: _env_flag("AGENTPOOL_USE_SESSION_POOL_FOR_COMMANDS"),
        title="Use session pool for commands",
    )
    """Whether to route command execution through the SessionPool.

    Defaults to True. Set to False to fall back to direct agent invocation
    for emergency rollback only.
    """

    use_session_pool_for_skills: bool = Field(
        default_factory=lambda: _env_flag("AGENTPOOL_USE_SESSION_POOL_FOR_SKILLS"),
        title="Use session pool for skills",
    )
    """Whether to route skill invocation through the SessionPool.

    Defaults to True. Set to False to fall back to direct agent invocation
    for emergency rollback only.
    """

    use_session_pool_for_init: bool = Field(
        default_factory=lambda: _env_flag("AGENTPOOL_USE_SESSION_POOL_FOR_INIT"),
        title="Use session pool for init",
    )
    """Whether to use SessionPool during agent initialization.

    Defaults to True. Set to False to fall back to direct agent invocation
    for emergency rollback only.
    """

    use_session_pool_for_summarize: bool = Field(
        default_factory=lambda: _env_flag("AGENTPOOL_USE_SESSION_POOL_FOR_SUMMARIZE"),
        title="Use session pool for summarize",
    )
    """Whether to route summarization through the SessionPool.

    Defaults to True. Set to False to fall back to direct agent invocation
    for emergency rollback only.
    """

    use_session_pool_for_mcp: bool = Field(
        default_factory=lambda: _env_flag("AGENTPOOL_USE_SESSION_POOL_FOR_MCP"),
        title="Use session pool for MCP",
    )
    """Whether to route MCP tool calls through the SessionPool.

    Defaults to True. Set to False to fall back to direct agent invocation
    for emergency rollback only.
    """

    use_session_pool_for_messages: bool = Field(
        default_factory=lambda: _env_flag("AGENTPOOL_USE_SESSION_POOL_FOR_MESSAGES"),
        title="Use session pool for messages",
    )
    """Whether to use SessionPool as the exclusive source of truth for message history.

    Defaults to True. Set to False to fall back to ServerState in-memory dictionaries
    for emergency rollback only.
    """

    use_session_pool_for_status: bool = Field(
        default_factory=lambda: _env_flag("AGENTPOOL_USE_SESSION_POOL_FOR_STATUS"),
        title="Use session pool for status",
    )
    """Whether to use SessionController/SessionStatusBridge as the exclusive source
    of truth for session status.

    Defaults to True. Set to False to fall back to ServerState in-memory dictionaries
    for emergency rollback only.
    """

    eventbus_replay_buffer_size: int = Field(
        default=100, ge=1, title="EventBus replay buffer size"
    )
    """Maximum number of events retained per session for EventBus replay."""

    def should_use_session_pool_for(self, category: str) -> bool:
        """Check if SessionPool should be used for a specific category.

        The global `use_session_pool` master switch must be True for any
        category flag to be evaluated. If the global switch is False,
        this always returns False regardless of category settings.

        Args:
            category: The category to check. Supported values are
                "commands", "skills", "init", "summarize", "mcp".

        Returns:
            True if SessionPool should be used for the given category.
        """
        if not self.use_session_pool:
            return False
        return getattr(self, f"use_session_pool_for_{category}", False)

    model_config = ConfigDict(frozen=True)
