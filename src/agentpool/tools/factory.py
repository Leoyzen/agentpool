"""ToolsetFactory protocol — thin replacement for ResourceProvider hierarchy.

Phase 5 of the thin-wrapper refactor. Defines a structural protocol that
produces pydantic-ai capabilities (Toolset, Hooks, MCP) without the
heavyweight ResourceProvider base class.

Each factory is a lightweight callable that returns a pydantic-ai
``AbstractCapability`` or ``None`` (if no tools are available).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


if TYPE_CHECKING:
    from pydantic_ai.capabilities import AbstractCapability


@runtime_checkable
class ToolsetFactory(Protocol):
    """Produce a pydantic-ai capability from configured tools.

    Implementations wrap tool sources (MCP servers, local skills,
    subagent delegation, static tool lists) and return a single
    ``AbstractCapability`` that pydantic-ai injects at agent run time.

    The factory is called once per agent run. Returning ``None`` means
    the factory has no tools to contribute for this run.
    """

    async def create_capability(self) -> AbstractCapability | None:
        """Build and return a pydantic-ai capability, or ``None``."""
        ...


class StaticToolsetFactory:
    """Factory wrapping a pre-configured list of AgentPool Tool objects.

    Replaces ``StaticResourceProvider`` for the common case where tools
    are known at configuration time and do not change.
    """

    def __init__(self, tools: list[Any] | None = None, *, name: str = "static") -> None:
        self._tools = tools or []
        self._name = name

    async def create_capability(self) -> AbstractCapability | None:
        from pydantic_ai.toolsets import (
            ApprovalRequiredToolset,
            CombinedToolset,
            FunctionToolset,
        )

        from agentpool.resource_providers.base import ResourceProvider

        if not self._tools:
            return None

        normal_tools = [t for t in self._tools if not t.requires_confirmation]
        confirm_tools = [t for t in self._tools if t.requires_confirmation]

        toolsets: list[Any] = []
        if normal_tools:
            pa_tools = [ResourceProvider._wrap_for_pydantic_ai(tool) for tool in normal_tools]
            toolsets.append(FunctionToolset(pa_tools, id=self._name))
        if confirm_tools:
            pa_tools = [ResourceProvider._wrap_for_pydantic_ai(tool) for tool in confirm_tools]
            toolsets.append(ApprovalRequiredToolset(FunctionToolset(pa_tools, id=self._name)))

        if not toolsets:
            return None
        if len(toolsets) == 1:
            return toolsets[0]
        return CombinedToolset(toolsets)

    @property
    def tools(self) -> list[Any]:
        return self._tools


class AdapterToolsetFactory:
    """Adapter wrapping an existing ResourceProvider.

    Allows incremental migration: callers can switch from
    ``provider.as_capability()`` to ``AdapterToolsetFactory(provider).create_capability()``
    without changing the provider itself.
    """

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def create_capability(self) -> AbstractCapability | None:
        cap = self._provider.as_capability()
        if cap is None:
            return None
        return cap

    @property
    def provider(self) -> Any:
        return self._provider
