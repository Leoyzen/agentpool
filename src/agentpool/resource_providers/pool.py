"""Resource provider exposing agents and teams as config-based delegation tools.

The provider iterates over agent and team *configs* (not runtime instances)
and creates delegation tools that lazily create session-level agents via
``SessionPool`` when invoked.  This eliminates the dependency on pool-level
agent instances and supports the eliminate-pool-level-agents migration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider
from agentpool.tools.base import FunctionTool


if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.capabilities import AbstractCapability

    from agentpool import AgentPool
    from agentpool.agents.context import AgentContext
    from agentpool.orchestrator.core import SessionPool
    from agentpool.prompts.prompts import BasePrompt
    from agentpool.resource_providers.resource_info import ResourceInfo
    from agentpool_config.teams import TeamConfig

logger = get_logger(__name__)


class PoolResourceProvider(ResourceProvider):
    """Provider that exposes an AgentPool's agents and teams as delegation tools.

    Tools are created lazily from config metadata instead of from pre-existing
    agent/team instances.  On invocation each tool creates a session-level agent
    via ``SessionPool.get_or_create_session_agent()`` and executes it.

    If ``session_pool`` is not provided at construction time, the provider
    falls back to ``ctx.pool.session_pool`` at invocation time (requires
    ``AgentContext``).
    """

    kind = "tools"

    def __init__(
        self,
        pool: AgentPool[Any],
        name: str | None = None,
        zed_mode: bool = False,
        include_team_members: bool = False,
        session_pool: SessionPool | None = None,
    ) -> None:
        """Initialize provider with agent pool.

        Args:
            pool: Agent pool whose manifest configs are exposed as tools.
            name: Optional name override (defaults to pool name).
            zed_mode: Whether to enable Zed mode.
            include_team_members: Whether to also expose delegation tools for
                agents that belong to teams (default *False* — those agents
                are only accessible through their team tool).
            session_pool: Optional ``SessionPool`` for creating session-level
                agents at tool invocation time.  When *None*, the provider
                falls back to ``ctx.pool.session_pool`` at runtime.
        """
        super().__init__(name=name or repr(pool))
        self.pool = pool
        self.zed_mode = zed_mode
        self.include_team_members = include_team_members
        self._session_pool: SessionPool | None = session_pool

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_tools(self) -> Sequence[FunctionTool]:
        """Get delegation tools from all agents and teams in pool manifest.

        Iterates ``pool.manifest.agents`` and ``pool.manifest.teams`` (pure
        config models) instead of pool-level agent/team instances.  Each tool
        stores the target name and creates a session-level agent on demand.
        """
        tools: list[FunctionTool] = []
        team_configs: dict[str, TeamConfig] = self.pool.manifest.teams

        # Team delegation tools from config
        for team_name, team_config in team_configs.items():
            tools.append(self._make_team_delegation_tool(team_name, team_config))

        # Agent delegation tools from config
        agent_configs = self.pool.manifest.agents

        if self.include_team_members:
            tools.extend(self._make_agent_delegation_tool(name) for name in agent_configs)
        else:
            # Collect all team member names so we can exclude them
            team_member_names: set[str] = set()
            for team_config in team_configs.values():
                for member in team_config.members:
                    member_name = team_config.get_member_name(member)
                    team_member_names.add(member_name)

            tools.extend(
                self._make_agent_delegation_tool(name)
                for name in agent_configs
                if name not in team_member_names
            )

        return tools

    async def get_prompts(self) -> list[BasePrompt]:
        """Get prompts from pool's manifest."""
        prompts: list[Any] = []
        return prompts

    async def get_resources(self) -> list[ResourceInfo]:
        """Get resources from pool's manifest."""
        return []

    def as_capability(self) -> AbstractCapability | None:
        """No capability — tools are injected directly via ``get_tools()``."""
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_session_pool(self, ctx: AgentContext | None = None) -> SessionPool:
        """Resolve the ``SessionPool`` from the stored reference or runtime context.

        Args:
            ctx: Optional ``AgentContext`` to fall back to ``ctx.pool.session_pool``.

        Returns:
            The ``SessionPool`` instance.

        Raises:
            RuntimeError: If no ``SessionPool`` is available from any source.
        """
        if self._session_pool is not None:
            return self._session_pool
        if ctx is not None and ctx.pool is not None and ctx.pool.session_pool is not None:
            return ctx.pool.session_pool
        msg = (
            "PoolResourceProvider requires a SessionPool for delegation tool execution. "
            "Pass session_pool to __init__() or ensure the pool has one configured."
        )
        raise RuntimeError(msg)

    def _make_agent_delegation_tool(self, agent_name: str) -> FunctionTool:
        """Create a delegation tool for an agent from its config name.

        Args:
            agent_name: Name of the agent in the manifest.

        Returns:
            A ``FunctionTool`` that creates a session agent and delegates to it.
        """
        agent_config = self.pool.manifest.agents.get(agent_name)
        display_name = (agent_config.display_name or agent_name) if agent_config else agent_name
        agent_desc = agent_config.description if agent_config else None

        async def _delegate_to_agent(ctx: AgentContext, prompt: str) -> Any:
            """Delegate a task to the {display_name} specialist agent.

            Use this tool to get expert assistance from the {display_name}
            agent.
            """
            session_pool = self._get_session_pool(ctx)
            child_session_id = await ctx.create_child_session(
                agent_name=agent_name,
                agent_type="native",
                description=f"Run {agent_name}",
                tool_call_id=ctx.tool_call_id,
            )
            agent = await session_pool.sessions.get_or_create_session_agent(
                child_session_id,
                agent_name,
            )
            result = await agent.run(prompt)
            return result.content

        tool_name = f"ask_{agent_name}"
        docstring = f"Get expert answer from specialized agent: {display_name}"
        if agent_desc:
            docstring = f"{docstring}\n\n{agent_desc}"
        _delegate_to_agent.__doc__ = docstring
        _delegate_to_agent.__name__ = tool_name
        return FunctionTool.from_callable(_delegate_to_agent, source="pool")

    def _make_team_delegation_tool(self, team_name: str, team_config: TeamConfig) -> FunctionTool:
        """Create a delegation tool for a team from its config.

        On invocation, creates session-level agents for each team member,
        assembles them into a ``BaseTeam``, and runs the team with the
        provided prompt.

        Args:
            team_name: Name of the team in the manifest.
            team_config: Team configuration model.

        Returns:
            A ``FunctionTool`` that delegates to the team.
        """
        display_name = team_config.display_name or team_name

        async def _delegate_to_team(ctx: AgentContext, prompt: str) -> Any:
            """Delegate a task to the {display_name} team.

            Use this tool to get the {display_name} team to collectively
            process a task.
            """
            session_pool = self._get_session_pool(ctx)

            # Create session-level agents for each team member
            member_nodes: list[Any] = []
            for member in team_config.members:
                member_name = team_config.get_member_name(member)
                child_session_id = await ctx.create_child_session(
                    agent_name=member_name,
                    agent_type="native",
                    description=f"Run {member_name}",
                    tool_call_id=ctx.tool_call_id,
                )
                member_agent = await session_pool.sessions.get_or_create_session_agent(
                    child_session_id,
                    member_name,
                )
                member_nodes.append(member_agent)

            # Build and run the team
            from agentpool.orchestrator.session_pool import _build_team_from_config

            team = _build_team_from_config(team_name, team_config, member_nodes)
            result = await team.run(prompt)
            return result.content

        tool_name = f"ask_{team_name}"
        docstring = f"Get expert answer from team: {display_name}"
        if team_config.description:
            docstring = f"{docstring}\n\n{team_config.description}"
        _delegate_to_team.__doc__ = docstring
        _delegate_to_team.__name__ = tool_name
        return FunctionTool.from_callable(_delegate_to_team, source="pool")
