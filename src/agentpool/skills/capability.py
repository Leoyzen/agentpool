"""SkillCapability — wraps a Skill as a pydantic-ai capability.

Exposes skill instructions, tools (Python and MCP), and tool filtering
as a composable :class:`~pydantic_ai.capabilities.AbstractCapability`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic_ai.capabilities import (
    AbstractCapability,
    CapabilityOrdering,
    NativeTool,
    ProcessHistory,
)
from pydantic_ai.tools import AgentDepsT, RunContext, ToolDefinition
from pydantic_ai.toolsets import (
    AbstractToolset,
    AgentToolset,
    CombinedToolset,
    FilteredToolset,
    FunctionToolset,
    PrefixedToolset,
)

from agentpool.skills.skill import Skill  # noqa: TC001


if TYPE_CHECKING:
    from agentpool.skills.skill_mcp_manager import SkillMcpManager
    from agentpool.skills.skill_tool_manager import SkillToolManager
    from agentpool.tools.base import Tool

logger = logging.getLogger(__name__)


class SkillCapability(AbstractCapability[AgentDepsT]):
    """Wraps a single :class:`~agentpool.skills.skill.Skill` as a pydantic-ai capability.

    Provides:
    - Instructions via :meth:`get_instructions` (raw skill content, no XML wrapper)
    - Tools via :meth:`get_toolset` (Python tools eagerly imported, MCP tools lazily connected)
    - Tool filtering via :meth:`get_wrapper_toolset` (based on ``allowed_tools``)
    - Ordering via :meth:`get_ordering` (wrapped by ``ProcessHistory`` and ``NativeTool``)
    - MCP cleanup via :meth:`on_run_ended`
    """

    def __init__(
        self,
        skill: Skill,
        mcp_manager: SkillMcpManager | None = None,
        tool_manager: SkillToolManager | None = None,
    ) -> None:
        """Initialize the skill capability.

        Args:
            skill: The skill to wrap.
            mcp_manager: Manager for per-session MCP server connections.
                If provided and the skill has ``mcp_servers``, each server
                config is prepared at construction time.
            tool_manager: Manager for dynamic Python tool imports.
                If provided and the skill has ``tools``, all tools are
                imported eagerly at construction time.
        """
        self._skill = skill
        self._mcp_manager = mcp_manager
        self._tool_manager = tool_manager

        # Pre-register Python tools at construction time (eager import).
        self._python_tools: list[Tool] = []
        if skill.tools and self._tool_manager is not None:
            self._python_tools = self._tool_manager.import_tools(skill.tools)

        # Pre-register MCP server configs at construction time.
        if skill.mcp_servers and self._mcp_manager is not None:
            for server_name, config in skill.mcp_servers.items():
                self._mcp_manager.prepare(server_name, config)

    # ---- Instructions ----

    def get_instructions(self) -> str | None:
        """Return raw skill instruction content.

        No XML wrapper — ``SkillsInstructionProvider`` owns the XML wrapper.
        Note: pydantic-ai will inject this into the system prompt, and
        ``SkillsInstructionProvider`` may also inject skill content via the
        ``<available-skills>`` XML block (depending on injection mode).
        """
        raw = self._skill.load_instructions()
        if not raw:
            return None
        return raw

    # ---- Toolset ----

    def get_toolset(self) -> AgentToolset[AgentDepsT] | None:
        """Return the skill's toolset, or None if no tools are configured.

        - Python tools: eagerly imported at construction, wrapped in
          ``PrefixedToolset(prefix="{name}__tool__")``.
        - MCP tools: lazily connected per-run via a ``ToolsetFunc``,
          wrapped in ``PrefixedToolset(prefix="{name}__mcp__")``.
        - Both: ``CombinedToolset`` merging both prefixed toolsets.
        """
        has_python = bool(self._python_tools)
        has_mcp = bool(self._skill.mcp_servers) and self._mcp_manager is not None

        if not has_python and not has_mcp:
            return None

        # Python-only: concrete toolset returned eagerly.
        if has_python and not has_mcp:
            pa_tools = [t.to_pydantic_ai() for t in self._python_tools]
            return PrefixedToolset(
                prefix=f"{self._skill.name}__tool__",
                wrapped=FunctionToolset(pa_tools),
            )

        # MCP-only or Both: ToolsetFunc that builds lazily per-run.
        async def _build_toolset(
            ctx: RunContext[AgentDepsT],
        ) -> AbstractToolset[AgentDepsT]:
            toolsets: list[AbstractToolset[AgentDepsT]] = []

            if has_python:
                pa_tools = [t.to_pydantic_ai() for t in self._python_tools]
                toolsets.append(
                    PrefixedToolset(
                        prefix=f"{self._skill.name}__tool__",
                        wrapped=FunctionToolset(pa_tools),
                    )
                )

            if has_mcp:
                assert self._mcp_manager is not None
                session_id = getattr(ctx.deps, "session_id", "default")
                mcp_toolsets: list[AbstractToolset[AgentDepsT]] = []
                for server_name in self._skill.mcp_servers:  # type: ignore[union-attr]
                    try:
                        tools = await self._mcp_manager.get_tools(server_name, session_id)
                    except Exception:
                        logger.exception(
                            "Failed to get MCP tools for skill %r, server %r",
                            self._skill.name,
                            server_name,
                        )
                        continue
                    pa_tools = [t.to_pydantic_ai() for t in tools]
                    mcp_toolsets.append(FunctionToolset(pa_tools))
                toolsets.append(
                    PrefixedToolset(
                        prefix=f"{self._skill.name}__mcp__",
                        wrapped=CombinedToolset(toolsets=mcp_toolsets),
                    )
                )

            if len(toolsets) == 1:
                return toolsets[0]
            return CombinedToolset(toolsets=toolsets)

        return _build_toolset

    # ---- Wrapper toolset (filtering) ----

    def get_wrapper_toolset(
        self,
        toolset: AbstractToolset[AgentDepsT],
    ) -> AbstractToolset[AgentDepsT] | None:
        """Filter the assembled toolset based on ``allowed_tools``.

        Args:
            toolset: The agent's combined non-output toolset.

        Returns:
            A ``FilteredToolset`` if ``allowed_tools`` is configured,
            otherwise the toolset unchanged.
        """
        allowed = self._skill.parsed_allowed_tools()
        if not allowed:
            return toolset

        allowed_set = set(allowed)

        async def _filter(
            ctx: RunContext[AgentDepsT],
            tool_def: ToolDefinition,
        ) -> bool:
            name = tool_def.name
            prefix = f"{self._skill.name}__"
            if not name.startswith(prefix):
                return True  # Not our tool, pass through
            # Extract bare tool name from e.g. "metaskill__tool__Read" → "Read"
            bare_name = name.rsplit("__", 1)[-1]
            return bare_name in allowed_set

        return FilteredToolset(wrapped=toolset, filter_func=_filter)

    # ---- Ordering ----

    def get_ordering(self) -> CapabilityOrdering | None:
        """Declare that this capability should be wrapped by ProcessHistory and NativeTool."""
        return CapabilityOrdering(wrapped_by=[ProcessHistory, NativeTool])

    # ---- Run lifecycle ----

    async def on_run_ended(self, ctx: RunContext[AgentDepsT]) -> None:
        """Clean up MCP connections when a run ends.

        Triggers ``SkillMcpManager.cleanup(session_id)`` to disconnect
        all MCP servers that were connected during this session.
        """
        if self._mcp_manager is None:
            return
        session_id = getattr(ctx.deps, "session_id", None)
        if session_id is None:
            return
        await self._mcp_manager.cleanup(session_id)
