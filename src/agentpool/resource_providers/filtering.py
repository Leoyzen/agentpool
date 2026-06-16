"""Filtering resource provider implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentpool.resource_providers import ResourceProvider


if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.capabilities import AbstractCapability

    from agentpool.tools.base import Tool


class FilteringResourceProvider(ResourceProvider):
    """Wrapper that filters tools from a ResourceProvider based on tool name filter."""

    def __init__(self, provider: ResourceProvider, tool_filter: dict[str, bool]) -> None:
        """Initialize filtering wrapper.

        Args:
            provider: The provider to wrap
            tool_filter: Dict mapping tool names to enabled state (defaults to True)
        """
        self._provider = provider
        self._tool_filter = tool_filter

    def as_capability(self) -> AbstractCapability | None:
        """Return a pydantic-ai capability for this provider.

        Returns:
            A pydantic-ai AbstractCapability instance, or None.
        """
        return None

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access to wrapped provider."""
        return getattr(self._provider, name)

    async def get_tools(self) -> Sequence[Tool]:
        """Get filtered tools from wrapped provider.

        Returns only tools where the filter value is True. Tools not in the filter
        default to enabled (True).
        """
        tools = await self._provider.get_tools()
        return [t for t in tools if self._tool_filter.get(t.name, True)]
