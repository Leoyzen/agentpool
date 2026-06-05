"""Parallel, unordered group of agents / nodes."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from anyenv.async_run import as_generated
import anyio

from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.events import SpawnSessionStart, SubAgentEvent
from agentpool.agents.exceptions import MAX_DELEGATION_DEPTH, DelegationDepthError
from agentpool.delegation.base_team import BaseTeam
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage, TeamResponse
from agentpool.messaging.messagenode import get_source_type
from agentpool.messaging.processing import finalize_message, prepare_prompts


logger = get_logger(__name__)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from toprompt import AnyPromptType

    from agentpool import MessageNode
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.common_types import PromptCompatible
    from agentpool.talk import Talk


class Team[TDeps = None](BaseTeam[TDeps, Any]):
    """Group of agents that can execute together."""

    _error_mode: Literal["fail_all", "collect_exceptions"] = "collect_exceptions"

    async def execute(self, *prompts: PromptCompatible | None, **kwargs: Any) -> TeamResponse:
        """Run all agents in parallel via pydantic-graph Fork + Join."""
        from agentpool.delegation.graph_team import _TeamGraphState, run_team_graph
        from agentpool.talk.talk import Talk

        self._team_talk.clear()
        all_nodes = list(self.nodes)
        # Create Talk connections for monitoring this execution
        execution_talks: list[Talk[Any]] = []
        for node in all_nodes:
            # No actual forwarding, just for tracking
            talk = Talk[Any](node, [], connection_type="run", queued=True, queue_strategy="latest")
            execution_talks.append(talk)
            self._team_talk.append(talk)  # Add to base class's TeamTalk

        state = _TeamGraphState(
            prompts=prompts,
            kwargs=kwargs,
            shared_prompt=self.shared_prompt,
            execution_talks=execution_talks,
            error_mode=self._error_mode,
        )

        return await run_team_graph(all_nodes, state)

    def __prompt__(self) -> str:
        """Format team info for prompts."""
        members = ", ".join(a.name for a in self.nodes)
        desc = f" - {self.description}" if self.description else ""
        return f"Parallel Team {self.name!r}{desc}\nMembers: {members}"

    async def run_iter(
        self,
        *prompts: AnyPromptType,
        **kwargs: Any,
    ) -> AsyncIterator[ChatMessage[Any]]:
        """Yield messages as they arrive from parallel execution."""
        queue: asyncio.Queue[ChatMessage[Any] | None] = asyncio.Queue()
        failures: dict[str, Exception] = {}

        async def _run(node: MessageNode[TDeps, Any]) -> None:
            try:
                message = await node.run(*prompts, **kwargs)
                await queue.put(message)
            except Exception as e:
                logger.exception("Error executing node", name=node.name)
                failures[node.name] = e
                # Put None to maintain queue count
                await queue.put(None)

        # Get nodes to run
        all_nodes = list(self.nodes)
        # Start all agents
        tasks = [asyncio.create_task(_run(n), name=f"run_{n.name}") for n in all_nodes]
        try:
            # Yield messages as they arrive
            for _ in all_nodes:
                if msg := await queue.get():
                    yield msg

            # If any failures occurred, raise error with details
            if failures:
                error_details = "\n".join(f"- {name}: {error}" for name, error in failures.items())
                error_msg = f"Some nodes failed to execute:\n{error_details}"
                raise RuntimeError(error_msg)

        finally:
            # Clean up any remaining tasks
            for task in tasks:
                if not task.done():
                    task.cancel()

    async def run(
        self,
        *prompts: PromptCompatible | None,
        wait_for_connections: bool | None = None,
        store_history: bool = False,
        **kwargs: Any,
    ) -> ChatMessage[list[Any]]:
        """Run all agents in parallel and return combined message."""
        # Prepare prompts and create user message
        user_msg, processed_prompts = await prepare_prompts(*prompts)
        await self.message_received.emit(user_msg)
        # Execute team logic
        result = await self.execute(*processed_prompts, **kwargs)
        message_id = str(uuid4())  # Always generate unique response ID
        message = ChatMessage(
            content=[r.message.content for r in result if r.message],
            messages=[m for r in result if r.message for m in r.message.messages],
            role="assistant",
            name=self.name,
            message_id=message_id,
            session_id=user_msg.session_id,
            parent_id=user_msg.message_id,
            metadata={
                "agent_names": [r.agent_name for r in result],
                "errors": {name: str(error) for name, error in result.errors.items()},
                "start_time": result.start_time.isoformat(),
            },
        )

        # Teams typically don't store history by default, but allow it
        if store_history:
            # Teams could implement their own history management here if needed
            pass

        # Finalize and route message
        return await finalize_message(
            message,
            user_msg,
            self,
            self.connections,
            wait_for_connections,
        )

    async def run_stream(
        self,
        *prompts: PromptCompatible,
        depth: int = 0,
        **kwargs: Any,
    ) -> AsyncIterator[RichAgentStreamEvent[Any]]:
        """Stream responses from all team members in parallel.

        Args:
            prompts: Input prompts to process in parallel
            depth: Current delegation depth (0 = top-level run)
            kwargs: Additional arguments passed to each agent.
                ``session_id`` and ``depth`` are popped before forwarding
                to prevent duplicate-keyword ``TypeError``.

        Yields:
            RichAgentStreamEvent, with member events wrapped in SubAgentEvent
        """
        from agentpool.common_types import SupportsRunStream
        from agentpool.utils.identifiers import generate_session_id

        # Pop session_id/depth/parent_session_id from kwargs to avoid duplicate
        # keyword errors when forwarding to members.  The explicit *depth*
        # parameter is the source of truth; the popped session_id and
        # parent_session_id determine the parent session for child-session
        # creation.
        session_id_kwarg: str | None = kwargs.pop("session_id", None)
        kwargs.pop("depth", None)
        parent_session_id_kwarg: str | None = kwargs.pop("parent_session_id", None)

        # Compute child depth and guard against excessive nesting
        child_depth = depth + 1
        if child_depth > MAX_DELEGATION_DEPTH:
            raise DelegationDepthError(child_depth)

        # Resolve the parent session id for this team execution.
        # The caller's parent_session_id takes priority, then session_id (for
        # backward compat).
        parent_sid: str | None = parent_session_id_kwarg or session_id_kwarg

        # Get nodes to run
        all_nodes = list(self.nodes)

        # Pre-create child sessions for each member so that SpawnSessionStart
        # can be emitted *before* the member's stream begins.
        # Use id(node) as key instead of node.name to avoid collisions
        # when multiple team members share the same name.
        child_session_ids: dict[int, str] = {}
        for node in all_nodes:
            if self.agent_pool and self.agent_pool.session_pool:
                if parent_sid:
                    child_state = await self.agent_pool.session_pool.create_session(
                        session_id=generate_session_id(),
                        parent_session_id=parent_sid,
                        agent_name=node.name,
                        agent_type=node.agent_type,
                    )
                    child_sid = child_state.session_id
                else:
                    child_sid = generate_session_id()
            else:
                child_sid = generate_session_id()
            child_session_ids[id(node)] = child_sid

        # Create list of streams — one per member, prefixed by SpawnSessionStart
        async def wrap_stream(
            node: MessageNode[Any, Any],
            child_sid: str,
        ) -> AsyncIterator[RichAgentStreamEvent[Any]]:
            """Wrap a node's stream events, prefixed with SpawnSessionStart."""
            source_type = get_source_type(node)

            # Emit SpawnSessionStart before the member's stream begins
            yield SpawnSessionStart(
                child_session_id=child_sid,
                parent_session_id=parent_sid or "",
                source_type=source_type,
                source_name=node.name,
                depth=child_depth,
                description=f"Spawning {node.name} as team member",
                spawn_mechanism="spawn",
            )

            if not isinstance(node, SupportsRunStream):
                return

            # Extract model_id from BaseAgent nodes
            node_model_id: str | None = None
            if isinstance(node, BaseAgent):
                node_model_id = node.model_name

            async for event in node.run_stream(
                *prompts,
                session_id=child_sid,
                parent_session_id=parent_sid,
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
                        model_id=event.model_id,
                        mode=event.mode,
                        child_session_id=event.child_session_id,
                        parent_session_id=event.parent_session_id,
                    )
                else:
                    yield SubAgentEvent(
                        source_name=node.name,
                        source_type=source_type,
                        event=event,
                        depth=child_depth,
                        model_id=node_model_id,
                        child_session_id=child_sid,
                        parent_session_id=parent_sid,
                    )

        streams = [wrap_stream(node, child_session_ids[id(node)]) for node in all_nodes]
        # Merge all streams
        async for event in as_generated(streams):
            yield event


if __name__ == "__main__":

    async def main() -> None:
        from agentpool import Agent, TeamRun
        from agentpool.agents.events import SubAgentEvent

        agent_a = Agent(name="A", model="test")
        agent_b = Agent(name="B", model="test")
        agent_c = Agent(name="C", model="test")
        # Test Team containing TeamRun (parallel containing sequential)
        inner_run = TeamRun([agent_a, agent_b], name="Sequential")
        outer_team = Team([inner_run, agent_c], name="Parallel")

        print("Testing Team containing TeamRun...")
        async for event in outer_team.run_stream("test"):
            if isinstance(event, SubAgentEvent):
                print(f"[depth={event.depth}] {event.source_name}: {type(event.event).__name__}")
            else:
                print(f"Event: {type(event).__name__}")

    anyio.run(main)
