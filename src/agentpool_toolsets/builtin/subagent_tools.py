"""Provider for subagent/task tools with streaming support.

Business-layer event routing is intentionally minimal. All agent stream events
flow through the SessionPool's TurnRunner, which publishes them to the EventBus.
The protocol layer (OpenCode, ACP, etc.) subscribes to the parent session with
``scope="descendants"`` and receives child session events automatically — no
manual forwarding from the business layer is required.
"""

from __future__ import annotations

import asyncio
import datetime
import re
from typing import Any, Literal

from pydantic_ai import ModelRetry

from agentpool.agents.context import AgentContext  # noqa: TC001
from agentpool.agents.events import (
    SpawnSessionStart,
    StreamCompleteEvent,
)
from agentpool.agents.exceptions import MAX_DELEGATION_DEPTH, DelegationDepthError
from agentpool.log import get_logger
from agentpool.resource_providers import StaticResourceProvider
from agentpool.tools.exceptions import ToolError


logger = get_logger(__name__)

# Set to hold references to background tasks, preventing GC while running
_background_tasks: set[asyncio.Task[Any]] = set()


def _serialize_content(content: Any) -> str:
    """Serialize subagent output content to a string."""
    if not content:
        return ""
    if isinstance(content, str):
        return content

    from pydantic import BaseModel

    if isinstance(content, BaseModel):
        return content.model_dump_json()
    return str(content)


def _generate_task_id(description: str) -> str:
    """Generate a unique, sortable task ID from timestamp and description.

    Args:
        description: Short task description to include in the ID

    Returns:
        Task ID in format: YYYYMMDD-HHMMSS-description
    """
    timestamp = datetime.datetime.now(tz=datetime.UTC).strftime("%Y%m%d-%H%M%S")
    # Sanitize description: lowercase, replace spaces/special chars with dashes
    slug = re.sub(r"[^a-z0-9]+", "-", description.lower()).strip("-")[:30]
    return f"{timestamp}-{slug}"


class SubagentTools(StaticResourceProvider):
    """Provider for task delegation tools with streaming progress."""

    def __init__(
        self,
        name: str = "subagent_tools",
    ) -> None:
        super().__init__(name=name)
        for tool in [
            self.create_tool(
                self.list_available_nodes, category="search", read_only=True, idempotent=True
            ),
            self.create_tool(self.task, category="other"),
        ]:
            self.add_tool(tool)

    async def list_available_nodes(  # noqa: D417
        self,
        ctx: AgentContext,
        node_type: Literal["all", "agent", "team"] = "all",
        only_idle: bool = False,
    ) -> str:
        """List available agents and/or teams in the current pool.

        Args:
            node_type: Filter by node type - "all", "agent", or "team"
            only_idle: If True, only returns nodes that aren't currently busy

        Returns:
            List of node names that can be used with the task tool
        """
        if ctx.pool is None:
            msg = "No agent pool available"
            raise ToolError(msg)
        lines: list[str] = []
        if node_type in ("all", "agent"):
            for ag_name, ag_cfg in ctx.pool.manifest.agents.items():
                lines.extend([
                    f"name: {ag_name}",
                    "type: agent",
                    f"description: {ag_cfg.description or 'No description'}",
                    "---",
                ])

        if node_type in ("all", "team"):  # List teams
            for tm_name, tm_cfg in ctx.pool.manifest.teams.items():
                lines.extend([
                    f"name: {tm_name}",
                    "type: team",
                    f"description: {tm_cfg.description or 'No description'}",
                    "---",
                ])

        return "\n".join(lines) if lines else "No nodes available"

    async def task(  # noqa: D417
        self,
        ctx: AgentContext,
        agent_or_team: str,
        prompt: str,
        description: str,
        async_mode: bool = False,
    ) -> dict[str, Any]:
        """Execute a task on an agent or team.

        Launch a task to be executed by a specialized agent or team.

        In synchronous mode (default), the task runs with streaming progress events
        and returns the result when complete.

        In async mode, the task starts in the background and returns immediately
        with a task ID. The output is written to /tasks/{task_id}/output.md in
        the internal filesystem after the run completes.

        Args:
            agent_or_team: The agent or team to execute the task
            prompt: The task instructions for the agent or team
            description: A short (3-5 words) description of the task
            async_mode: If True, run in background and return task ID immediately

        Returns:
            Structured output containing result and metadata
        """
        from agentpool.common_types import SupportsRunStream

        _ = description  # Used for logging/tracking in future

        if ctx.pool is None:
            msg = "Agent needs to be in a pool to execute tasks"
            raise ToolError(msg)

        session_pool = ctx.pool.session_pool
        if session_pool is None:
            msg = "SessionPool is required for subagent task execution"
            raise ToolError(msg)

        # Existence check against manifest configs
        agent_cfg = ctx.pool.manifest.agents.get(agent_or_team)
        team_cfg = ctx.pool.manifest.teams.get(agent_or_team)
        if agent_cfg is None and team_cfg is None:
            available = list(ctx.pool.manifest.agents.keys()) + list(ctx.pool.manifest.teams.keys())
            msg = (
                f"No agent or team found with name: {agent_or_team}. "
                f"Available nodes: {', '.join(available)}"
            )
            raise ModelRetry(msg)

        # Determine source_type and agent_type from config
        if agent_cfg is not None:
            source_type: Literal["team_parallel", "team_sequential", "agent"] = "agent"
            agent_type_str: str = agent_cfg.type
        else:
            assert team_cfg is not None
            agent_type_str = "team"
            source_type = "team_parallel" if team_cfg.mode == "parallel" else "team_sequential"

        logger.info(
            "Executing task",
            agent_or_team=agent_or_team,
            description=description,
            async_mode=async_mode,
        )

        # Compute current delegation depth
        current_depth = ctx.run_ctx.depth if ctx.run_ctx is not None else 0
        child_depth = current_depth + 1

        # Guard against excessive nesting before creating any resources
        if child_depth > MAX_DELEGATION_DEPTH:
            raise DelegationDepthError(child_depth)

        parent_session_id = getattr(ctx.node, "session_id", None) or (
            ctx.run_ctx.session_id if ctx.run_ctx else ""
        )

        # Extract model_id from agent config if possible
        node_model_id: str | None = None
        if agent_cfg is not None:
            from agentpool.models.agents import NativeAgentConfig
            if isinstance(agent_cfg, NativeAgentConfig):
                raw_model = agent_cfg.model
                node_model_id = str(raw_model) if raw_model else None

        # Create child session with metadata for TurnRunner event wrapping
        child_session_id = await ctx.create_child_session(
            agent_name=agent_or_team,
            agent_type=agent_type_str,
            parent_session_id=parent_session_id,
            source_name=agent_or_team,
            source_type=source_type,
            depth=child_depth,
            tool_call_id=ctx.tool_call_id,
            model_id=node_model_id,
        )

        # Resolve the actual node via SessionPool
        is_team_node = team_cfg is not None
        node: SupportsRunStream[Any]
        if not is_team_node:
            # Agent: bind to the child session
            node = await session_pool.sessions.get_or_create_session_agent(
                child_session_id, agent_name=agent_or_team,
            )
            if not isinstance(node, SupportsRunStream):
                msg = f"Agent {agent_or_team} does not support streaming"
                raise ToolError(msg)
        else:
            # Team: create team from config
            assert team_cfg is not None
            node = await session_pool.create_team_from_config(agent_or_team, team_cfg)
            if not isinstance(node, SupportsRunStream):
                msg = f"Team {agent_or_team} does not support streaming"
                raise ToolError(msg)

        # Emit exactly one SpawnSessionStart for both sync and async modes
        # Emit SpawnSessionStart so the protocol layer can detect child session
        # creation. All other stream events flow through TurnRunner → EventBus
        # and reach the frontend via protocol-layer ``scope="descendants"``
        # subscription — no manual business-layer forwarding is required.
        spawn_event = SpawnSessionStart(
            child_session_id=child_session_id,
            parent_session_id=parent_session_id,
            tool_call_id=ctx.tool_call_id,
            spawn_mechanism="task",
            source_name=agent_or_team,
            source_type=source_type,
            depth=child_depth,
            description=f"Run {agent_or_team} task",
            metadata={"prompt": prompt[:200]} if prompt else {},
            model_id=node_model_id,
        )
        await ctx.events.emit_event(spawn_event)

        try:
            input_provider = ctx.get_input_provider()
        except RuntimeError:
            input_provider = None

        if async_mode:
            # Generate task ID and start background task
            task_id = _generate_task_id(description)
            output_path = f"/tasks/{task_id}/output.md"
            fs = ctx.internal_fs
            fs.mkdirs(f"/tasks/{task_id}", exist_ok=True)

            from agentpool.messaging.message_history import MessageHistory

            async def _background_run() -> None:
                """Run task through SessionPool and write final result to filesystem."""
                final_content = ""
                try:
                    if is_team_node:
                        # Teams run directly (SessionPool does not support team sessions)
                        result = await node.run(prompt, message_history=MessageHistory())
                        final_content = _serialize_content(result.content)
                    else:
                        async for event in session_pool.run_stream(
                            child_session_id,
                            prompt,
                            input_provider=input_provider,
                            message_history=MessageHistory(),
                        ):
                            if isinstance(event, StreamCompleteEvent):
                                content = event.message.content
                                final_content = _serialize_content(content)
                except Exception:
                    logger.exception("Async task failed", task_id=task_id, agent=agent_or_team)
                    error_content = (
                        f"# Task Failed\n\nTask {task_id} ({agent_or_team}) failed with an error."
                    )
                    fs.pipe(output_path, error_content.encode("utf-8"))
                else:
                    fs.pipe(output_path, final_content.encode("utf-8"))
                    logger.info(
                        "Async task completed",
                        task_id=task_id,
                        agent=agent_or_team,
                        output_path=output_path,
                    )

            task = asyncio.create_task(_background_run(), name=f"async_task_{task_id}")
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

            return {
                "output": (
                    f"Task started in background.\n"
                    f"Task ID: {task_id}\n"
                    f"Output will be written to: {output_path}\n"
                    f"Use the read tool to check the output file for results."
                ),
                "metadata": {
                    "taskId": task_id,
                    "sessionId": child_session_id,
                    "outputFile": output_path,
                },
            }

        # Synchronous mode — block until completion and return final result
        from agentpool.messaging.message_history import MessageHistory

        final_content = ""
        if is_team_node:
            # Teams run directly (SessionPool does not support team sessions)
            result = await node.run(prompt, message_history=MessageHistory())
            final_content = _serialize_content(result.content)
        else:
            async for event in session_pool.run_stream(
                child_session_id,
                prompt,
                input_provider=input_provider,
                message_history=MessageHistory(),
            ):
                if isinstance(event, StreamCompleteEvent):
                    content = event.message.content
                    final_content = _serialize_content(content)

        return {
            "output": final_content,
            "metadata": {
                "sessionId": child_session_id,
            },
        }
