"""ToolsetFactory protocol — thin replacement for ResourceProvider hierarchy.

Phase 5 of the thin-wrapper refactor. Defines a structural protocol that
produces pydantic-ai capabilities (Toolset, Hooks, MCP) without the
heavyweight ResourceProvider base class.

Each factory is a lightweight callable that returns a pydantic-ai
``AbstractToolset`` or ``None`` (if no tools are available).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


if TYPE_CHECKING:
    from pydantic_ai.toolsets import AbstractToolset


@runtime_checkable
class ToolsetFactory(Protocol):
    """Produce a pydantic-ai toolset from configured tools.

    Implementations wrap tool sources (MCP servers, local skills,
    subagent delegation, static tool lists) and return a single
    ``AbstractToolset`` that pydantic-ai injects at agent run time.

    The factory is called once per agent run. Returning ``None`` means
    the factory has no tools to contribute for this run.
    """

    async def create_capability(self) -> AbstractToolset[Any] | None:
        """Build and return a pydantic-ai toolset, or ``None``."""
        ...


class StaticToolsetFactory:
    """Factory wrapping a pre-configured list of AgentPool Tool objects.

    Replaces ``StaticResourceProvider`` for the common case where tools
    are known at configuration time and do not change.
    """

    def __init__(self, tools: list[Any] | None = None, *, name: str = "static") -> None:
        self._tools = tools or []
        self._name = name

    async def create_capability(self) -> AbstractToolset[Any] | None:
        from pydantic_ai.toolsets import (
            ApprovalRequiredToolset,
            CombinedToolset,
            FunctionToolset,
        )

        from agentpool.tools.tool_wrapping import wrap_tool_for_pydantic_ai

        if not self._tools:
            return None

        normal_tools = [t for t in self._tools if not t.requires_confirmation]
        confirm_tools = [t for t in self._tools if t.requires_confirmation]

        toolsets: list[AbstractToolset[Any]] = []
        if normal_tools:
            pa_tools = [wrap_tool_for_pydantic_ai(tool) for tool in normal_tools]
            toolsets.append(FunctionToolset(pa_tools, id=self._name))
        if confirm_tools:
            pa_tools = [wrap_tool_for_pydantic_ai(tool) for tool in confirm_tools]
            toolsets.append(ApprovalRequiredToolset(FunctionToolset(pa_tools, id=self._name)))

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

    async def create_capability(self) -> AbstractToolset[Any] | None:
        from pydantic_ai.toolsets import AbstractToolset

        cap = self._provider.as_capability()
        if cap is None:
            return None
        if isinstance(cap, AbstractToolset):
            return cap
        return None

    @property
    def provider(self) -> Any:
        return self._provider
