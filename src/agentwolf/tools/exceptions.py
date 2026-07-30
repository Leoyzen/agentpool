"""Tool-related exceptions."""

from __future__ import annotations

from agentwolf.utils.baseregistry import AgentPoolError


class ToolError(AgentPoolError):
    """Tool-related errors."""
