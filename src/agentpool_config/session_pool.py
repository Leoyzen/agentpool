"""Session pool configuration models."""

from __future__ import annotations

from pydantic import ConfigDict, Field
from schemez import Schema


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

    use_session_pool: bool = Field(default=False, title="Use session pool")
    """Whether to use the SessionPool for ACP protocol session management."""

    model_config = ConfigDict(frozen=True)


class OpenCodeConfig(Schema):
    """OpenCode protocol-specific configuration."""

    use_session_pool: bool = Field(default=False, title="Use session pool")
    """Whether to use the SessionPool for OpenCode protocol session management."""

    model_config = ConfigDict(frozen=True)
