"""SkillCapability — wraps a Skill as a pydantic-ai capability.

Exposes skill instructions, tools (Python and MCP), and tool filtering
as a composable :class:`~pydantic_ai.capabilities.AbstractCapability`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic_ai._instructions import AgentInstructions  # noqa: TC002
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
    from agentpool.agents.context import AgentContext
    from agentpool.mcp_server.config_snapshot import McpConfigEntry
    from agentpool.skills.skill_mcp_manager import SkillMcpManager
    from agentpool.skills.skill_tool_manager import SkillToolManager

logger = logging.getLogger(__name__)


class SkillCapability(AbstractCapability[AgentDepsT]):
    """Wraps a single :class:`~agentpool.skills.skill.Skill` as a pydantic-ai capability.

    Provides:
    - Instructions via :meth:`get_instructions` (raw skill content, no XML wrapper)
    - Tools via :meth:`get_toolset` (Python tools eagerly imported, MCP tools lazily connected)
    - Tool filtering via :meth:`get_wrapper_toolset` (based on ``allowed_tools``)
    - Ordering via :meth:`get_ordering` (wrapped by ``ProcessHistory`` and ``NativeTool``)
    - MCP config entries via :meth:`build_config_entries` (for snapshot registration)
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
        self._python_tools: list = []
        if skill.tools and self._tool_manager is not None:
            self._python_tools = self._tool_manager.import_tools(skill.tools)

        # Pre-register MCP server configs at construction time.
        if skill.mcp_servers and self._mcp_manager is not None:
            for server_name, config in skill.mcp_servers.items():
                self._mcp_manager.prepare(server_name, config)

    # ---- Instructions ----

    def get_instructions(self) -> AgentInstructions[AgentDepsT] | None:
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

    # ---- Config entries ----

    def build_config_entries(self) -> tuple[McpConfigEntry, ...]:
        """Build ``McpConfigEntry`` entries for this skill's MCP servers.

        Delegates to :meth:`SkillMcpManager.build_config_entries` when the
        skill has MCP servers and a manager is configured. Returns an empty
        tuple otherwise.

        Returns:
            Tuple of ``McpConfigEntry`` instances tagged with
            ``source="skill"`` and ``skill_name`` set to this skill's name.
        """
        if self._mcp_manager is None or not self._skill.mcp_servers:
            return ()
        return self._mcp_manager.build_config_entries(self._skill.name)

    # ---- Toolset ----

    def get_toolset(self) -> AgentToolset[AgentDepsT] | None:
        """Return the skill's toolset, or None if no tools are configured.

        - Python tools: eagerly imported at construction, wrapped in
          ``PrefixedToolset(prefix="{name}__tool__")``.
        - MCP tools: lazily connected per-run via a ``ToolsetFunc``.
          When the agent has a ``SessionConnectionPool`` and ``McpConfigSnapshot``
          with skill configs, ``MCPToolset`` instances are created from pooled
          transports. Otherwise, falls back to the legacy
          ``SkillMcpManager.get_tools()`` path.
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
                mcp_toolsets = await self._build_mcp_toolsets(ctx)
                if mcp_toolsets:
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

    async def _build_mcp_toolsets(
        self,
        ctx: RunContext[AgentDepsT],
    ) -> list[AbstractToolset[AgentDepsT]]:
        """Build MCP toolsets for this skill's servers.

        Prefers the ``SessionConnectionPool`` + ``McpConfigSnapshot`` path
        when available (snapshot-aware transport pooling). Falls back to
        the legacy ``SkillMcpManager.get_tools()`` path otherwise.

        Args:
            ctx: The pydantic-ai run context.

        Returns:
            List of ``AbstractToolset`` instances for the skill's MCP servers.
        """
        from agentpool.agents.context import AgentContext

        if isinstance(ctx.deps, AgentContext):
            return await self._build_mcp_toolsets_from_pool(ctx.deps)
        return await self._build_mcp_toolsets_legacy(ctx)

    async def _build_mcp_toolsets_from_pool(
        self,
        deps: AgentContext[AgentDepsT],
    ) -> list[AbstractToolset[AgentDepsT]]:
        """Build MCP toolsets from ``SessionConnectionPool`` transports.

        Reads skill-scoped config entries from the agent's snapshot and
        creates ``MCPToolset`` instances from pooled transports.

        Args:
            deps: The agent context providing access to the agent and
                its session connection pool.

        Returns:
            List of ``MCPToolset`` instances wrapped in ``PrefixedToolset``.
        """
        from pydantic_ai.mcp import MCPToolset

        agent = deps.native_agent
        session_pool = agent._session_connection_pool
        snapshot = agent._mcp_snapshot

        if session_pool is None or snapshot is None:
            # Snapshot or pool not configured — fall back to legacy path.
            run_ctx = deps.run_ctx
            session_id = run_ctx.session_id if run_ctx is not None else "default"
            return await self._build_mcp_toolsets_legacy_session(session_id)

        # Filter skill configs from the snapshot for this skill.
        skill_entries = [
            entry
            for entry in snapshot.skill_configs
            if entry.skill_name == self._skill.name
        ]

        if not skill_entries:
            # No skill configs registered in snapshot — fall back.
            run_ctx = deps.run_ctx
            session_id = run_ctx.session_id if run_ctx is not None else "default"
            return await self._build_mcp_toolsets_legacy_session(session_id)

        toolsets: list[AbstractToolset[AgentDepsT]] = []
        for entry in skill_entries:
            server_config = entry.server_config
            if not server_config.enabled:
                continue
            try:
                transport = await session_pool.get_transport(
                    server_config,
                    skill_name=self._skill.name,
                )
            except Exception:
                logger.exception(
                    "Failed to get MCP transport for skill %r, server %r",
                    self._skill.name,
                    server_config.name,
                )
                continue
            toolset = MCPToolset(
                client=transport,
                id=server_config.name,
                include_instructions=True,
                init_timeout=server_config.timeout,
                read_timeout=server_config.timeout,
            )
            toolsets.append(toolset)
        return toolsets

    async def _build_mcp_toolsets_legacy(
        self,
        ctx: RunContext[AgentDepsT],
    ) -> list[AbstractToolset[AgentDepsT]]:
        """Build MCP toolsets using the legacy ``SkillMcpManager`` path.

        Used when ``ctx.deps`` is not an ``AgentContext`` (e.g. in tests
        with mock deps). Uses ``"default"`` as the session_id since we
        cannot extract it from untyped deps.

        Args:
            ctx: The pydantic-ai run context.

        Returns:
            List of ``FunctionToolset`` instances built from MCP tools.
        """
        _ = ctx  # ctx.deps is untyped; session_id defaults to "default"
        return await self._build_mcp_toolsets_legacy_session("default")

    async def _build_mcp_toolsets_legacy_session(
        self,
        session_id: str,
    ) -> list[AbstractToolset[AgentDepsT]]:
        """Build MCP toolsets from ``SkillMcpManager`` for a session.

        Args:
            session_id: Session identifier for MCP connection scoping.

        Returns:
            List of ``FunctionToolset`` instances built from MCP tools.
        """
        assert self._mcp_manager is not None
        assert self._skill.mcp_servers is not None

        toolsets: list[AbstractToolset[AgentDepsT]] = []
        for server_name in self._skill.mcp_servers:
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
            toolsets.append(FunctionToolset(pa_tools))
        return toolsets

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
        all MCP servers that were connected during this session via the
        legacy path. Servers connected via ``SessionConnectionPool`` are
        cleaned up separately by the session lifecycle.
        """
        if self._mcp_manager is None:
            return
        from agentpool.agents.context import AgentContext

        session_id: str | None = None
        if isinstance(ctx.deps, AgentContext):
            run_ctx = ctx.deps.run_ctx
            if run_ctx is not None:
                session_id = run_ctx.session_id
        if session_id is None:
            return
        await self._mcp_manager.cleanup(session_id)
