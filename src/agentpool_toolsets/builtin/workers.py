"""Provider for worker agent tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.agents.events import (
    SpawnSessionStart,
    StreamCompleteEvent,
    SubAgentEvent,
)
from agentpool.agents.exceptions import MAX_DELEGATION_DEPTH, DelegationDepthError
from agentpool.log import get_logger
from agentpool.resource_providers import ResourceProvider
from agentpool.tools.exceptions import ToolError


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.tools.base import Tool
    from agentpool_config.workers import WorkerConfig

logger = get_logger(__name__)


class WorkersTools(ResourceProvider):
    """Provider for worker agent tools.

    Creates tools for each configured worker that delegate to agents/teams in the pool.
    Tools are created lazily when get_tools() is called, using AgentContext to access
    the pool at call time.
    """

    def __init__(self, workers: list[WorkerConfig], name: str = "workers") -> None:
        """Initialize workers toolset.

        Args:
            workers: List of worker configurations
            name: Provider name
        """
        super().__init__(name=name)
        self.workers = workers

    async def get_tools(self) -> Sequence[Tool]:
        """Get tools for all configured workers."""
        return [self._create_worker_tool(i) for i in self.workers]

    def _create_worker_tool(self, worker_config: WorkerConfig) -> Tool:
        """Create a tool for a single worker configuration."""
        from agentpool_config.workers import AgentWorkerConfig

        worker_name = worker_config.name
        # Regular agents get history management
        if isinstance(worker_config, AgentWorkerConfig):
            return self._create_agent_tool(
                worker_name,
                reset_history_on_run=worker_config.reset_history_on_run,
                pass_message_history=worker_config.pass_message_history,
            )
        # Teams, ACP agents, AGUI agents - all handled uniformly
        return self._create_node_tool(worker_name)

    def _create_agent_tool(
        self,
        agent_name: str,
        *,
        reset_history_on_run: bool = True,
        pass_message_history: bool = False,
    ) -> Tool:
        """Create tool for a regular agent worker with history management."""

        async def run(ctx: AgentContext, prompt: str) -> Any:
            from agentpool import Team, TeamRun
            from agentpool.agents.base_agent import BaseAgent
            from agentpool.common_types import SupportsRunStream

            if ctx.pool is None:
                msg = "No agent pool available"
                raise ToolError(msg)

            # Look for agent in both agents and teams
            worker = None
            agents = ctx.pool.get_agents()
            if agent_name in agents:
                worker = agents[agent_name]
            elif agent_name in ctx.pool.teams:
                worker = ctx.pool.teams[agent_name]

            if worker is None:
                available = list(agents.keys()) + list(ctx.pool.teams.keys())
                msg = f"Agent {agent_name!r} not found in pool. Available: {available}"
                raise ToolError(msg)

            # Compute delegation depth from current run context
            current_depth: int = ctx.run_ctx.depth if ctx.run_ctx is not None else 0
            child_depth = current_depth + 1
            if child_depth > MAX_DELEGATION_DEPTH:
                raise DelegationDepthError(child_depth)

            # Handle conversation history only for agents (not teams)
            old_history = None
            if isinstance(worker, BaseAgent):
                if pass_message_history:
                    old_history = worker.conversation.get_history()
                    worker.conversation.set_history(ctx.agent.conversation.get_history())
                elif reset_history_on_run:
                    await worker.conversation.clear()

            # Create child session via AgentContext (RFC-0028)
            from agentpool.utils.identifiers import generate_session_id

            parent_session_id = getattr(ctx.node, "session_id", None) or generate_session_id()
            child_session_id = await ctx.create_child_session(
                agent_name=agent_name,
                agent_type=worker.agent_type,
                parent_session_id=parent_session_id,
            )

            # Determine source type for events
            source_type: Literal["agent", "team_parallel", "team_sequential"] = "agent"
            if isinstance(worker, Team):
                source_type = "team_parallel"
            elif isinstance(worker, TeamRun):
                source_type = "team_sequential"
            elif isinstance(worker, BaseAgent):
                source_type = "agent"

            if not isinstance(worker, SupportsRunStream):
                msg = f"Agent {agent_name} does not support streaming"
                raise ToolError(msg)

            # Emit SpawnSessionStart before streaming begins
            spawn_event = SpawnSessionStart(
                child_session_id=child_session_id,
                parent_session_id=parent_session_id,
                tool_call_id=ctx.tool_call_id,
                spawn_mechanism="task",
                source_name=agent_name,
                source_type=source_type,
                depth=child_depth,
                description=f"Run {agent_name} worker",
                metadata={"prompt": prompt[:200]} if prompt else {},
            )
            await ctx.events.emit_event(spawn_event)

            try:
                # Use run_stream instead of run for consistent event handling
                stream = worker.run_stream(
                    prompt,
                    session_id=child_session_id,
                    parent_session_id=parent_session_id,
                    depth=child_depth,
                )

                final_content = ""
                async for event in stream:
                    # Wrap the event in SubAgentEvent
                    subagent_event = SubAgentEvent(
                        source_name=agent_name,
                        source_type=source_type,
                        event=event,
                        depth=child_depth,
                        child_session_id=child_session_id,
                        parent_session_id=parent_session_id,
                        tool_call_id=ctx.tool_call_id,
                    )
                    await ctx.events.emit_event(subagent_event)

                    # Extract final content from StreamCompleteEvent
                    if isinstance(event, StreamCompleteEvent):
                        content = event.message.content
                        final_content = str(content) if content else ""

                return final_content
            finally:
                if old_history is not None and isinstance(worker, BaseAgent):
                    worker.conversation.set_history(old_history)

        normalized_name = agent_name.replace("_", " ").title()
        run.__name__ = f"ask_{agent_name}"
        run.__doc__ = f"Get expert answer from specialized agent: {normalized_name}"
        return self.create_tool(run)

    def _create_node_tool(self, node_name: str) -> Tool:
        """Create tool for non-agent nodes (teams, ACP agents, AGUI agents)."""
        from agentpool import Team, TeamRun
        from agentpool.agents.base_agent import BaseAgent
        from agentpool.common_types import SupportsRunStream

        async def run(ctx: AgentContext, prompt: str) -> str:
            if ctx.pool is None:
                msg = "No agent pool available"
                raise ToolError(msg)

            # Look for worker in both nodes and teams
            worker = None
            if node_name in ctx.pool.nodes:
                worker = ctx.pool.nodes[node_name]
            elif node_name in ctx.pool.teams:
                worker = ctx.pool.teams[node_name]

            if worker is None:
                available = list(ctx.pool.nodes.keys()) + list(ctx.pool.teams.keys())
                msg = f"Worker {node_name!r} not found in pool. Available: {available}"
                raise ToolError(msg)

            # Compute delegation depth from current run context
            current_depth: int = ctx.run_ctx.depth if ctx.run_ctx is not None else 0
            child_depth = current_depth + 1
            if child_depth > MAX_DELEGATION_DEPTH:
                raise DelegationDepthError(child_depth)

            # Create child session via AgentContext (RFC-0028)
            from agentpool.utils.identifiers import generate_session_id

            parent_session_id = getattr(ctx.node, "session_id", None) or generate_session_id()
            child_session_id = await ctx.create_child_session(
                agent_name=node_name,
                agent_type=worker.agent_type,
                parent_session_id=parent_session_id,
            )

            # Determine source type for events
            source_type: Literal["agent", "team_parallel", "team_sequential"] = "agent"
            if isinstance(worker, Team):
                source_type = "team_parallel"
            elif isinstance(worker, TeamRun):
                source_type = "team_sequential"
            elif isinstance(worker, BaseAgent):
                source_type = "agent"

            if not isinstance(worker, SupportsRunStream):
                msg = f"Node {node_name} does not support streaming"
                raise ToolError(msg)

            # Emit SpawnSessionStart before streaming begins
            spawn_event = SpawnSessionStart(
                child_session_id=child_session_id,
                parent_session_id=parent_session_id,
                tool_call_id=ctx.tool_call_id,
                spawn_mechanism="task",
                source_name=node_name,
                source_type=source_type,
                depth=child_depth,
                description=f"Run {node_name} worker",
                metadata={"prompt": prompt[:200]} if prompt else {},
            )
            await ctx.events.emit_event(spawn_event)

            # Use run_stream for consistent event handling
            stream = worker.run_stream(
                prompt,
                session_id=child_session_id,
                parent_session_id=parent_session_id,
                depth=child_depth,
            )

            final_content = ""
            async for event in stream:
                # Wrap the event in SubAgentEvent
                subagent_event = SubAgentEvent(
                    source_name=node_name,
                    source_type=source_type,
                    event=event,
                    depth=child_depth,
                    child_session_id=child_session_id,
                    parent_session_id=parent_session_id,
                    tool_call_id=ctx.tool_call_id,
                )
                await ctx.events.emit_event(subagent_event)

                # Extract final content from StreamCompleteEvent
                if isinstance(event, StreamCompleteEvent):
                    content = event.message.content
                    final_content = str(content) if content else ""

            return final_content

        normalized_name = node_name.replace("_", " ").title()
        run.__name__ = f"ask_{node_name}"
        run.__doc__ = f"Delegate task to worker: {normalized_name}"
        return self.create_tool(run)
