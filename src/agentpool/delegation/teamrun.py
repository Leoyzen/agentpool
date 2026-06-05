"""Sequential, ordered group of agents / nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise
from time import perf_counter
from typing import TYPE_CHECKING, Any, Literal, overload
from uuid import uuid4

import anyio

from agentpool.common_types import SupportsRunStream
from agentpool.delegation.base_team import BaseTeam
from agentpool.log import get_logger
from agentpool.messaging import AgentResponse, ChatMessage, TeamResponse
from agentpool.messaging.processing import finalize_message, prepare_prompts
from agentpool.talk.talk import Talk, TeamTalk
from agentpool.utils.time_utils import get_now

from pydantic_graph import GraphBuilder, Step, StepContext
from pydantic_graph.id_types import NodeID


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from datetime import datetime

    from agentpool import MessageNode
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.common_types import PromptCompatible
    from agentpool.delegation import AgentPool
    from agentpool_config.mcp_server import MCPServerConfig


logger = get_logger(__name__)

ResultMode = Literal["last", "concat"]


@dataclass
class _TeamRunGraphState:
    """Shared state for TeamRun graph execution."""

    prompts: tuple[Any, ...] = field(default_factory=tuple)
    """Input prompts for this execution."""

    kwargs: dict[str, Any] = field(default_factory=dict)
    """Additional keyword arguments passed to member ``run()``."""

    connections: list[Talk[Any]] = field(default_factory=list)
    """Talk connections for tracking execution stats."""

    responses: list[AgentResponse[Any]] = field(default_factory=list)
    """Collected responses from completed steps."""


def _make_sequential_step(
    node: MessageNode[Any, Any],
    node_index: int,
) -> Any:
    """Create a pydantic-graph step for a sequential team member.

    Args:
        node: The team member node to wrap.
        node_index: Index of the node in the pipeline (0 = first).

    Returns:
        An async callable compatible with :meth:`GraphBuilder.step`.
    """

    async def _step(
        ctx: StepContext[_TeamRunGraphState, Any, Any],
    ) -> ChatMessage[Any]:
        start = perf_counter()
        if node_index == 0:
            result = await node.run(*ctx.state.prompts, **ctx.state.kwargs)
        else:
            result = await node.run_message(ctx.inputs)
        timing = perf_counter() - start
        response = AgentResponse(agent_name=node.name, message=result, timing=timing)
        ctx.state.responses.append(response)

        # Update talk stats for the edge leaving this node (if any)
        if node_index < len(ctx.state.connections):
            talk = ctx.state.connections[node_index]
            if result:
                talk._stats.messages.append(result)

        return result

    return _step


@dataclass(frozen=True, kw_only=True)
class ExtendedTeamTalk(TeamTalk):
    """TeamTalk that also provides TeamRunStats interface."""

    errors: list[tuple[str, str, datetime]] = field(default_factory=list)

    def clear(self) -> None:
        """Reset all tracking data."""
        super().clear()  # Clear base TeamTalk
        self.errors.clear()

    def add_error(self, agent: str, error: str) -> None:
        """Track errors from AgentResponses."""
        self.errors.append((agent, error, get_now()))


class TeamRun[TDeps, TResult](BaseTeam[TDeps, TResult]):
    """Handles team operations with monitoring."""

    @overload  # validator set: it defines the output
    def __init__(
        self,
        agents: Sequence[MessageNode[TDeps, Any]],
        *,
        name: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        shared_prompt: str | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        validator: MessageNode[Any, TResult],
        agent_pool: AgentPool | None = None,
    ) -> None: ...

    @overload
    def __init__(  # no validator, but all nodes same output type.
        self,
        agents: Sequence[MessageNode[TDeps, TResult]],
        *,
        name: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        shared_prompt: str | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        validator: None = None,
        agent_pool: AgentPool | None = None,
    ) -> None: ...

    @overload
    def __init__(
        self,
        agents: Sequence[MessageNode[TDeps, Any]],
        *,
        name: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        shared_prompt: str | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        validator: MessageNode[Any, TResult] | None = None,
        agent_pool: AgentPool | None = None,
    ) -> None: ...

    def __init__(
        self,
        agents: Sequence[MessageNode[TDeps, Any]],
        *,
        name: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        shared_prompt: str | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        validator: MessageNode[Any, TResult] | None = None,
        agent_pool: AgentPool | None = None,
    ) -> None:
        super().__init__(
            agents,
            name=name,
            description=description,
            display_name=display_name,
            shared_prompt=shared_prompt,
            mcp_servers=mcp_servers,
            agent_pool=agent_pool,
        )
        self.validator = validator
        self.result_mode = "last"

    def __prompt__(self) -> str:
        """Format team info for prompts."""
        members = " -> ".join(a.name for a in self.nodes)
        desc = f" - {self.description}" if self.description else ""
        return f"Sequential Team {self.name!r}{desc}\nPipeline: {members}"

    async def run(
        self,
        *prompts: PromptCompatible | None,
        wait_for_connections: bool | None = None,
        store_history: bool = False,
        **kwargs: Any,
    ) -> ChatMessage[TResult]:
        """Run agents sequentially and return combined message."""
        # Prepare prompts and create user message
        user_msg, processed_prompts = await prepare_prompts(*prompts)
        await self.message_received.emit(user_msg)
        # Execute sequential logic
        message_id = str(uuid4())  # Always generate unique response ID
        result = await self.execute(*processed_prompts, **kwargs)
        all_messages = [r.message for r in result if r.message]
        assert all_messages, "Error during execution, returned None for TeamRun"
        # Determine content based on mode
        match self.result_mode:
            case "last":
                content = all_messages[-1].content
            case "concat":
                content = "\n".join(str(msg.content) for msg in all_messages)
            case _:
                raise ValueError(f"Invalid result mode: {self.result_mode}")

        message = ChatMessage(
            content=content,
            messages=[m for chat_message in all_messages for m in chat_message.messages],
            role="assistant",
            name=self.name,
            associated_messages=all_messages,
            message_id=message_id,
            session_id=user_msg.session_id,
            parent_id=user_msg.message_id,
            metadata={
                "execution_order": [r.agent_name for r in result],
                "start_time": result.start_time.isoformat(),
                "errors": {name: str(error) for name, error in result.errors.items()},
            },
        )

        if store_history:
            pass  # Teams could implement their own history management here if needed
        return await finalize_message(  # Finalize and route message
            message,
            user_msg,
            self,
            self.connections,
            wait_for_connections,
        )

    async def execute(
        self,
        *prompts: PromptCompatible | None,
        **kwargs: Any,
    ) -> TeamResponse[TResult]:
        """Start execution with optional monitoring."""
        self._team_talk.clear()
        start_time = get_now()
        prompts_ = list(prompts)
        if self.shared_prompt:
            prompts_.insert(0, self.shared_prompt)
        responses = [i async for i in self.execute_iter(*prompts_) if isinstance(i, AgentResponse)]
        return TeamResponse(responses, start_time)

    async def run_iter(
        self,
        *prompts: PromptCompatible,
        **kwargs: Any,
    ) -> AsyncIterator[ChatMessage[Any]]:
        """Yield messages from the execution chain."""
        async for item in self.execute_iter(*prompts, **kwargs):
            match item:
                case AgentResponse(message=message) if message:
                    yield message
                case Talk():
                    pass

    async def execute_iter(
        self,
        *prompt: PromptCompatible,
        **kwargs: Any,
    ) -> AsyncIterator[Talk[Any] | AgentResponse[Any]]:
        all_nodes = list(self.nodes)
        if self.validator:
            all_nodes.append(self.validator)

        # Create Talk objects for edges (not registered with ConnectionManager)
        connections: list[Talk[Any]] = []
        for source, target in pairwise(all_nodes):
            talk = Talk[Any](
                source=source,
                targets=[target],
                connection_type="run",
                queued=True,
            )
            connections.append(talk)
            self._team_talk.append(talk)

        # Build graph state
        state = _TeamRunGraphState(
            prompts=prompt,
            kwargs=kwargs,
            connections=connections,
        )

        # Build steps
        steps: list[Any] = []
        for i, node in enumerate(all_nodes):
            step_fn = _make_sequential_step(node, i)
            step = Step(
                id=NodeID(node.name),
                call=step_fn,
                label=node.description or node.name,
            )
            steps.append(step)

        # Build graph: start -> step1 -> step2 -> ... -> end
        builder = GraphBuilder(
            state_type=_TeamRunGraphState,
            input_type=Any,
            output_type=ChatMessage[Any],
        )
        builder.add_edge(builder.start_node, steps[0])
        for s, t in pairwise(steps):
            builder.add_edge(s, t)
        builder.add_edge(steps[-1], builder.end_node)

        graph = builder.build()

        try:
            await graph.run(state=state, deps=self._get_deps(), inputs=None)
        except Exception:
            # Yield responses collected so far, then re-raise
            for i, response in enumerate(state.responses):
                yield response
                if i < len(connections):
                    yield connections[i]
            raise

        # Add last_talk for the final node if all steps completed and pipeline
        # has more than one node (preserves legacy behaviour)
        if len(state.responses) == len(all_nodes) and len(all_nodes) > 1:
            last_response = state.responses[-1]
            last_talk = Talk[Any](all_nodes[-1], [], connection_type="run")
            if last_response.message:
                last_talk._stats.messages.append(last_response.message)
            self._team_talk.append(last_talk)

        # Yield results in order: AgentResponse, Talk, AgentResponse, Talk, ...
        for i, response in enumerate(state.responses):
            yield response
            if i < len(connections):
                yield connections[i]

    async def run_stream(
        self,
        *prompts: PromptCompatible,
        require_all: bool = True,
        depth: int = 0,
        **kwargs: Any,
    ) -> AsyncIterator[RichAgentStreamEvent[Any]]:
        """Stream responses through the chain of team members.

        Args:
            prompts: Input prompts to process through the chain
            require_all: If True, fail if any agent fails. If False,
                         continue with remaining agents.
            depth: Current delegation nesting depth (0 = top-level).
            kwargs: Additional arguments passed to each agent

        Yields:
            RichAgentStreamEvent, with member events wrapped in SubAgentEvent
        """
        from agentpool.agents.base_agent import BaseAgent
        from agentpool.agents.events import SpawnSessionStart, StreamCompleteEvent, SubAgentEvent
        from agentpool.agents.exceptions import MAX_DELEGATION_DEPTH, DelegationDepthError
        from agentpool.messaging.messagenode import get_source_type
        from agentpool.utils.identifiers import generate_session_id

        # Pop session_id, depth, and parent_session_id from kwargs to avoid
        # duplicate keyword args when forwarding to child nodes.  The explicit
        # ``depth`` parameter takes precedence over any ``depth`` key in
        # **kwargs; the popped session_id / parent_session_id are used for
        # child-session creation.
        session_id_kwarg: str | None = kwargs.pop("session_id", None)
        kwargs.pop("depth", None)  # explicit parameter wins
        parent_session_id_kwarg: str | None = kwargs.pop("parent_session_id", None)

        # Resolve the parent session id for this team execution.
        # The caller's parent_session_id takes priority, then session_id (for
        # backward compat).
        parent_session_id: str | None = (
            parent_session_id_kwarg or session_id_kwarg
        )

        child_depth = depth + 1
        if child_depth > MAX_DELEGATION_DEPTH:
            raise DelegationDepthError(child_depth)

        current_message = prompts
        for node in self.nodes:
            source_type = get_source_type(node)

            try:
                if not isinstance(node, SupportsRunStream):
                    raise TypeError(f"Node {node.name} does not support streaming")  # noqa: TRY301

                # Create child session for this member
                pool = self.agent_pool
                if pool is not None and pool.session_pool is not None and parent_session_id is not None:
                    child_state = await pool.session_pool.create_session(
                        session_id=generate_session_id(),
                        parent_session_id=parent_session_id,
                        agent_name=node.name,
                        agent_type=node.agent_type,
                    )
                    child_sid = child_state.session_id
                else:
                    child_sid = generate_session_id()

                # Emit spawn lifecycle event
                yield SpawnSessionStart(
                    child_session_id=child_sid,
                    parent_session_id=parent_session_id or "",
                    spawn_mechanism="spawn",
                    source_type=source_type,
                    source_name=node.name,
                    depth=child_depth,
                    description=f"Sequential team member {node.name!r}",
                )

                # Extract model_id from BaseAgent nodes
                node_model_id: str | None = None
                if isinstance(node, BaseAgent):
                    node_model_id = node.model_name

                async for event in node.run_stream(
                    *current_message,
                    session_id=child_sid,
                    parent_session_id=parent_session_id,
                    depth=child_depth,
                    **kwargs,
                ):
                    # Handle already-wrapped SubAgentEvents (nested teams)
                    if isinstance(event, SubAgentEvent):
                        yield SubAgentEvent(
                            source_name=event.source_name,
                            source_type=event.source_type,
                            event=event.event,
                            depth=event.depth + 1,
                            child_session_id=event.child_session_id,
                            parent_session_id=event.parent_session_id,
                            model_id=event.model_id,
                            mode=event.mode,
                        )
                    else:
                        yield SubAgentEvent(
                            source_name=node.name,
                            source_type=source_type,
                            event=event,
                            depth=child_depth,
                            child_session_id=child_sid,
                            parent_session_id=parent_session_id,
                            model_id=node_model_id,
                        )

                        # Extract content for next agent in chain
                        if isinstance(event, StreamCompleteEvent):
                            current_message = (event.message.content,)

            except Exception as e:
                if require_all:
                    msg = f"Chain broken at {node.name}: {e}"
                    logger.exception(msg)
                    raise ValueError(msg) from e
                logger.warning("Chain handler failed", name=node.name, error=e)


if __name__ == "__main__":

    async def main() -> None:
        from agentpool import Agent, Team
        from agentpool.agents.events import SubAgentEvent

        agent1 = Agent(name="Agent1", model="test")
        agent2 = Agent(name="Agent2", model="test")
        agent3 = Agent(name="Agent3", model="test")
        inner_team = Team([agent1, agent2], name="Parallel")
        outer_run = TeamRun([inner_team, agent3], name="Sequential")
        print("Testing TeamRun containing Team...")
        try:
            async for event in outer_run.run_stream("test"):
                if isinstance(event, SubAgentEvent):
                    print(
                        f"[depth={event.depth}] {event.source_name}: {type(event.event).__name__}"
                    )
                else:
                    print(f"Event: {type(event).__name__}")
        except Exception as e:  # noqa: BLE001
            print(f"Error: {e}")

    anyio.run(main)
