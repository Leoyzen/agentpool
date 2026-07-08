"""MCP server integration for AgentPool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from agentpool.mcp_server.client import MCPClient
from agentpool.mcp_server.tool_bridge import ToolManagerBridge


if TYPE_CHECKING:
    from agentpool.agents.base_agent import BaseAgent


class ToolBridge(Protocol):
    """Abstract interface for an MCP tool bridge.

    Hides the concrete ToolManagerBridge implementation behind a protocol,
    allowing callers to avoid importing the concrete class directly.
    """

    @property
    def url(self) -> str:
        """Get the server URL."""
        ...

    @property
    def resolved_server_name(self) -> str:
        """Get the resolved server name."""
        ...

    async def start(self) -> None:
        """Start the bridge."""
        ...

    async def stop(self) -> None:
        """Stop the bridge."""
        ...


def create_tool_bridge(node: BaseAgent[Any, Any], *, server_name: str | None = None) -> ToolBridge:
    """Create a ToolBridge backed by ToolManagerBridge.

    Args:
        node: The agent node whose tools to expose.
        server_name: Optional name for the MCP server.

    Returns:
        A ToolBridge protocol instance (backed by ToolManagerBridge).
    """
    return ToolManagerBridge(node=node, server_name=server_name)


__all__ = ["MCPClient", "ToolBridge", "ToolManagerBridge", "create_tool_bridge"]
