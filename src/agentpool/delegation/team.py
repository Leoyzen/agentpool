"""Parallel, unordered group of agents / nodes."""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4
from xml.sax.saxutils import escape

from anyenv.async_run import as_generated
import anyio
from jinja2 import BaseLoader, Environment

from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.events import SpawnSessionStart, SubAgentEvent
from agentpool.agents.exceptions import MAX_DELEGATION_DEPTH, DelegationDepthError
from agentpool.delegation.base_team import BaseTeam
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage, TeamResponse
from agentpool.messaging.messagenode import get_source_type
from agentpool.messaging.processing import finalize_message, prepare_prompts


logger = get_logger(__name__)
_PROMPT_TEMPLATE_ENV = Environment(loader=BaseLoader(), autoescape=False)  # noqa: S701

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from toprompt import AnyPromptType

    from agentpool import MessageNode
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.common_types import PromptCompatible
    from agentpool.talk import Talk


async def _timeout_stream[T](
    stream: AsyncIterator[T],
    timeout: float | None,
    member_name: str,
    team_name: str,
) -> AsyncIterator[T]:
    """Wrap an async iterator with an overall deadline.

    Each ``__anext__`` is bounded by the remaining budget so a single hung
    call cannot block forever.  If ``timeout`` is ``None`` the stream is
    yielded through unchanged.
    """
    if timeout is None:
        async for item in stream:
            yield item
        return

    deadline = perf_counter() + timeout
    it = stream.__aiter__()
    while True:
        remaining = deadline - perf_counter()
        if remaining <= 0:
            logger.warning(
                "Team member stream timed out",
                member=member_name, team=team_name, timeout=timeout,
            )
            return
        try:
            item = await asyncio.wait_for(it.__anext__(), timeout=remaining)
        except StopAsyncIteration:
            return
        except TimeoutError:
            logger.warning(
                "Team member stream timed out",
                member=member_name, team=team_name, timeout=timeout,
            )
            return
        yield item


class Team[TDeps = None](BaseTeam[TDeps, Any]):
    """Group of agents that can execute together."""

    _error_mode: Literal["fail_all", "collect_exceptions"] = "collect_exceptions"

    @staticmethod
    def _active_parent_session_id() -> str | None:
        """Return the active SessionPool session id when running inside a turn."""
        from agentpool.agents.base_agent import _current_run_ctx_var

        run_ctx = _current_run_ctx_var.get()
        session_id = getattr(run_ctx, "session_id", None)
        return str(session_id) if session_id else None

    async def _resolve_scoped_team_nodes(
        self,
        nodes: list[MessageNode[Any, Any]],
        parent_session_id: str | None,
        team_run_id: str,
    ) -> tuple[list[MessageNode[Any, Any]], dict[str, str]]:
        """Resolve team members to child-session agents for a parent session."""
        if not parent_session_id or self.agent_pool is None or self.agent_pool.session_pool is None:
            return nodes, {}

        from agentpool.agents.base_agent import BaseAgent
        from agentpool.utils.identifiers import generate_session_id

        session_pool = self.agent_pool.session_pool
        pool_agents = self.agent_pool.all_agents
        pool_teams = getattr(self.agent_pool, "teams", {})
        scoped_nodes: list[MessageNode[Any, Any]] = []
        child_session_ids: dict[str, str] = {}

        await self._save_scoped_storage_session(parent_session_id)
        for node in nodes:
            if node.name not in pool_agents and node.name not in pool_teams:
                scoped_nodes.append(node)
                continue
            child_state = await session_pool.create_session(
                session_id=generate_session_id(),
                parent_session_id=parent_session_id,
                lifecycle_policy="cascade",
                agent_name=node.name,
                agent_type=getattr(node, "agent_type", type(node).__name__),
                team_name=self.name,
                team_run_id=team_run_id,
                generate_title=False,
            )
            child_session_id = child_state.session_id
            if isinstance(node, BaseAgent) and node.name in pool_agents:
                child_node = await session_pool.sessions.get_or_create_session_agent(
                    child_session_id,
                    agent_name=node.name,
                )
            else:
                child_node = node
            scoped_nodes.append(child_node)
            child_session_ids[node.name] = child_session_id
            await self._save_scoped_storage_session(child_session_id)

        return scoped_nodes, child_session_ids

    async def _save_scoped_storage_session(self, session_id: str | None) -> None:
        """Persist SessionPool session data to protocol storage for lineage checks."""
        if not session_id or self.agent_pool is None or self.agent_pool.session_pool is None:
            return
        storage = getattr(self.agent_pool, "storage", None)
        if storage is None:
            return
        session_controller = self.agent_pool.session_pool.sessions
        session = session_controller.get_session(session_id)
        if session is None:
            return
        await storage.save_session(session_controller._state_to_data(session))

    async def _close_scoped_team_nodes(self, child_session_ids: dict[str, str]) -> None:
        """Close and delete round-scoped child sessions created for a team run."""
        if not child_session_ids or self.agent_pool is None or self.agent_pool.session_pool is None:
            return
        for session_id in reversed(list(child_session_ids.values())):
            await self.agent_pool.session_pool.close_session(session_id)
            await self._delete_scoped_storage_session(session_id)

    async def _delete_scoped_storage_session(self, session_id: str) -> None:
        """Remove protocol storage written for a round-scoped child session."""
        if self.agent_pool is None:
            return
        storage = getattr(self.agent_pool, "storage", None)
        if storage is None:
            return
        await storage.delete_session_messages(session_id)
        await storage.delete_session(session_id)

    async def execute(self, *prompts: PromptCompatible | None, **kwargs: Any) -> TeamResponse:
        """Run all agents in parallel via pydantic-graph Fork + Join.

        Keyword Args:
            template_vars: Extra variables available as ``{{ extra.<key> }}``
                inside per-member ``prompt_template`` Jinja2 strings.
        """
        from agentpool.delegation.graph_team import _TeamGraphState, run_team_graph
        from agentpool.talk.talk import Talk

        self._team_talk.clear()
        default_prompt = list(prompts)
        if self.shared_prompt:
            default_prompt.insert(0, self.shared_prompt)

        session_id_kwarg = str(kwargs.pop("session_id", "") or "")
        parent_session_id_kwarg = str(kwargs.pop("parent_session_id", "") or "")
        if session_id_kwarg:
            kwargs["session_id"] = session_id_kwarg
        if parent_session_id_kwarg:
            kwargs["parent_session_id"] = parent_session_id_kwarg
        parent_session_id = (
            parent_session_id_kwarg
            or session_id_kwarg
            or self._active_parent_session_id()
        )
        team_run_id = uuid4().hex
        template_vars = kwargs.pop("template_vars", {})
        if not isinstance(template_vars, dict):
            template_vars = {}
        timeout = self.member_timeout
        member_retry_attempts = max(0, int(kwargs.pop("member_retry_attempts", 0) or 0))
        member_retry_delay = max(0.0, float(kwargs.pop("member_retry_delay", 0.0) or 0.0))
        member_skills = self._normalize_member_skills(kwargs.pop("member_skills", {}))
        member_skill_instructions = await self._load_member_skill_instructions(member_skills)

        base_nodes = list(self.nodes)
        all_nodes, child_session_ids = await self._resolve_scoped_team_nodes(
            base_nodes,
            parent_session_id,
            team_run_id,
        )
        execution_talks: list[Talk[Any]] = []
        member_prompts: dict[str, list[PromptCompatible | None]] = {}
        for node in all_nodes:
            talk = Talk[Any](node, [], connection_type="run", queued=True, queue_strategy="latest")
            execution_talks.append(talk)
            self._team_talk.append(talk)
            resolved = self._resolve_member_prompt(
                node.name,
                default_prompt,
                prompts,
                template_vars,
            )
            resolved = self._inject_member_skill_instructions(
                node.name,
                resolved,
                member_skill_instructions,
            )
            member_prompts[node.name] = resolved

        state = _TeamGraphState(
            prompts=prompts,
            member_prompts=member_prompts,
            kwargs=kwargs,
            shared_prompt=self.shared_prompt,
            child_session_ids=child_session_ids,
            parent_session_id=parent_session_id,
            member_timeout=timeout,
            member_retry_attempts=member_retry_attempts,
            member_retry_delay=member_retry_delay,
            execution_talks=execution_talks,
            error_mode=self._error_mode,
        )

        try:
            return await run_team_graph(all_nodes, state)
        finally:
            await self._close_scoped_team_nodes(child_session_ids)

    @staticmethod
    def _normalize_member_skills(value: Any) -> dict[str, list[str]]:
        if not isinstance(value, dict):
            return {}
        result: dict[str, list[str]] = {}
        for member_name, raw_names in value.items():
            name = str(member_name).strip()
            if not name:
                continue
            raw_list = raw_names if isinstance(raw_names, list) else [raw_names]
            names = [
                str(item).strip()
                for item in raw_list
                if str(item).strip()
            ]
            if names:
                result[name] = list(dict.fromkeys(names))
        return result

    async def _load_member_skill_instructions(
        self,
        member_skills: dict[str, list[str]],
    ) -> dict[str, str]:
        if not member_skills or self.agent_pool is None:
            return {}

        unique_skill_names = list(dict.fromkeys(
            skill_name
            for names in member_skills.values()
            for skill_name in names
        ))
        loaded: dict[str, str] = {}
        for skill_name in unique_skill_names:
            instructions = await self._load_skill_instructions(skill_name)
            if instructions:
                loaded[skill_name] = instructions
        return {
            member_name: "\n\n".join(
                self._format_skill_instruction(skill_name, loaded[skill_name])
                for skill_name in skill_names
                if skill_name in loaded
            )
            for member_name, skill_names in member_skills.items()
        }

    async def _load_skill_instructions(self, skill_name: str) -> str:
        if self.agent_pool is None or self.agent_pool.skill_provider is None:
            from agentpool.skills.exceptions import SkillNotFoundError

            raise SkillNotFoundError(skill_name)

        return await self.agent_pool.skill_provider.get_skill_instructions(skill_name)

    @staticmethod
    def _format_skill_instruction(skill_name: str, instructions: str) -> str:
        return (
            f'<skill-instruction name="{escape(skill_name)}">\n'
            f"{instructions.strip()}\n"
            "</skill-instruction>"
        )

    @staticmethod
    def _inject_member_skill_instructions(
        member_name: str,
        prompts: list[PromptCompatible | None],
        member_skill_instructions: dict[str, str],
    ) -> list[PromptCompatible | None]:
        instructions = member_skill_instructions.get(member_name, "").strip()
        if not instructions:
            return prompts
        prompt_text = "\n\n".join(str(prompt) for prompt in prompts if prompt is not None).strip()
        combined = (
            f"{instructions}\n\n{prompt_text}"
            if prompt_text
            else instructions
        )
        return [combined]

    def _resolve_member_prompt(
        self,
        member_name: str,
        default_prompt: list[PromptCompatible | None],
        raw_prompts: tuple[PromptCompatible | None, ...],
        template_vars: dict[str, Any],
    ) -> list[PromptCompatible | None]:
        """Return per-member prompt if a template is configured, else the default."""
        cfg = self.member_prompt_templates.get(member_name)
        if cfg is None or cfg.prompt_template is None:
            return default_prompt

        prompt_str = " ".join(str(p) for p in raw_prompts if p is not None)
        rendered = _PROMPT_TEMPLATE_ENV.from_string(cfg.prompt_template).render(
            prompt=prompt_str,
            shared_prompt=self.shared_prompt or "",
            extra=template_vars,
        )
        return [rendered]

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
        timeout = self.member_timeout

        async def _run(node: MessageNode[TDeps, Any]) -> None:
            try:
                coro = node.run(*prompts, **kwargs)
                message = (
                    await asyncio.wait_for(coro, timeout=timeout)
                    if timeout is not None
                    else await coro
                )
                await queue.put(message)
            except TimeoutError:
                logger.warning(
                    "Team member timed out",
                    member=node.name,
                    team=self.name,
                    timeout=timeout,
                )
                failures[node.name] = TimeoutError(
                    f"Member {node.name!r} exceeded {timeout}s deadline"
                )
                await queue.put(None)
            except Exception as e:
                logger.exception("Error executing node", name=node.name)
                failures[node.name] = e
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
                "child_session_ids": result.child_session_ids,
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
        base_nodes = list(self.nodes)
        all_nodes, child_session_ids = await self._resolve_scoped_team_nodes(
            base_nodes,
            parent_sid,
            uuid4().hex,
        )
        timeout = self.member_timeout

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

            from agentpool.common_types import SupportsRunStream

            if not isinstance(node, SupportsRunStream):
                return

            # Extract model_id from BaseAgent nodes
            node_model_id: str | None = None
            if isinstance(node, BaseAgent):
                node_model_id = node.model_name

            stream = node.run_stream(
                *prompts,
                session_id=child_sid,
                parent_session_id=parent_sid,
                depth=child_depth,
                **kwargs,
            )
            async for event in _timeout_stream(stream, timeout, node.name, self.name):
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

        streams = [
            wrap_stream(node, child_session_ids.get(node.name, generate_session_id()))
            for node in all_nodes
        ]
        # Merge all streams
        try:
            async for event in as_generated(streams):
                yield event
        finally:
            await self._close_scoped_team_nodes(child_session_ids)


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
