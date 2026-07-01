"""Graph-based team execution using pydantic-graph Fork + Join.

This module provides an alternative implementation of :meth:`Team.execute`
that uses :class:`pydantic_graph.GraphBuilder` with ``Fork`` and ``Join``
nodes to run team members in parallel and collect their outputs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import perf_counter
from typing import TYPE_CHECKING, Any, Literal, NoReturn, cast

from pydantic_graph import GraphBuilder, StepContext, reduce_list_append

from agentpool.log import get_logger
from agentpool.messaging import AgentResponse, TeamResponse


if TYPE_CHECKING:
    from agentpool.messaging.messagenode import MessageNode
    from agentpool.talk.talk import Talk


logger = get_logger(__name__)


def _raise_no_message(node_name: str) -> NoReturn:
    """Raise RuntimeError for a member that returned no message."""
    msg = f"Member {node_name!r} returned no message"
    raise RuntimeError(msg)


@dataclass
class _MemberOutput:
    """Result from a single team member execution within the graph."""

    agent_name: str
    """Name of the agent that produced this result."""

    response: AgentResponse[Any] | None = None
    """Successful response, if any."""

    exception: Exception | None = None
    """Exception raised during execution, if any."""


@dataclass
class _TeamGraphState:
    """Shared state passed through the pydantic-graph execution."""

    prompts: tuple[Any, ...] = field(default_factory=tuple)
    """Input prompts for this execution."""

    kwargs: dict[str, Any] = field(default_factory=dict)
    """Additional keyword arguments passed to member ``run()``."""

    shared_prompt: str | None = None
    """Optional prompt prepended to all member inputs."""

    member_prompts: dict[str, list[Any]] = field(default_factory=dict)
    """Resolved prompt list per member name."""

    child_session_ids: dict[str, str] = field(default_factory=dict)
    """Session id allocated for each member in this team run."""

    parent_session_id: str | None = None
    """Parent session id for scoped team member runs."""

    member_timeout: float | None = None
    """Maximum seconds a member may run before being cancelled."""

    member_retry_attempts: int = 0
    """Additional attempts for non-timeout runtime member failures."""

    member_retry_delay: float = 0.0
    """Seconds to wait before retrying a failed member."""

    execution_talks: list[Talk[Any]] = field(default_factory=list)
    """Talk connections for tracking execution stats."""

    error_mode: Literal["fail_all", "collect_exceptions"] = "collect_exceptions"
    """How to handle member failures:
    - ``fail_all``: raise immediately on first failure
    - ``collect_exceptions``: catch all failures and return them in errors dict
    """


def _make_member_step(
    node: MessageNode[Any, Any],
) -> Any:
    """Create a pydantic-graph step function for a team member.

    The returned step runs ``node.run()`` with the prompts and kwargs stored
    in :attr:`_TeamGraphState`, records timing, updates the corresponding
    :class:`Talk` stats, and returns a :class:`_MemberOutput`.

    Args:
        node: The team member node to wrap.

    Returns:
        An async callable compatible with :meth:`GraphBuilder.step`.
    """

    async def _step(
        ctx: StepContext,
    ) -> _MemberOutput:
        state = cast(_TeamGraphState, ctx.state)
        final_prompt = state.member_prompts.get(node.name)
        if final_prompt is None:
            final_prompt = list(state.prompts)
        if state.shared_prompt and node.name not in state.member_prompts:
            final_prompt.insert(0, state.shared_prompt)

        try:
            start = perf_counter()
            run_kwargs = dict(state.kwargs)
            if child_session_id := state.child_session_ids.get(node.name):
                run_kwargs["session_id"] = child_session_id
            if state.parent_session_id:
                run_kwargs["parent_session_id"] = state.parent_session_id
            attempts = max(1, state.member_retry_attempts + 1)
            message = None
            for attempt_index in range(attempts):
                try:
                    coro = node.run(*final_prompt, **run_kwargs)
                    message = (
                        await asyncio.wait_for(coro, timeout=state.member_timeout)
                        if state.member_timeout is not None
                        else await coro
                    )
                    break
                except TimeoutError:
                    raise
                except RuntimeError as exc:
                    if attempt_index >= attempts - 1:
                        raise
                    logger.warning(
                        "Team member failed; retrying",
                        member=node.name,
                        attempt=attempt_index + 1,
                        max_attempts=attempts,
                        error=str(exc),
                    )
                    if state.member_retry_delay > 0:
                        await asyncio.sleep(state.member_retry_delay)
            if message is None:
                _raise_no_message(node.name)
            timing = perf_counter() - start
            response = AgentResponse(agent_name=node.name, message=message, timing=timing)

            # Update talk stats for this agent
            talk = next(
                (t for t in state.execution_talks if t.source == node),
                None,
            )
            if talk is not None:
                talk._stats.messages.append(message)

            return _MemberOutput(agent_name=node.name, response=response)

        except TimeoutError as exc:
            logger.warning(
                "Team member timed out",
                member=node.name,
                timeout=state.member_timeout,
            )
            if state.error_mode == "fail_all":
                raise
            timeout = state.member_timeout
            error = TimeoutError(f"Member {node.name!r} exceeded {timeout}s deadline")
            error.__cause__ = exc
            return _MemberOutput(agent_name=node.name, exception=error)

        except Exception as exc:
            if state.error_mode == "fail_all":
                raise
            return _MemberOutput(agent_name=node.name, exception=exc)

    return _step


def build_team_graph(
    nodes: list[MessageNode[Any, Any]],
) -> GraphBuilder:
    r"""Build a pydantic-graph that forks to all members and joins results.

    Graph topology::

        start_node
            |
           Fork  <-- broadcasts input to all members
          / | \
        m1 m2 m3  <-- member steps (parallel)
           \\ | /
           Join  <-- reduce_list_append collects outputs
            |
        end_node

    Args:
        nodes: Team members to execute in parallel.

    Returns:
        A :class:`GraphBuilder` ready to be built and run.
    """
    builder = GraphBuilder(
        state_type=_TeamGraphState,
        output_type=list[_MemberOutput],
    )

    # Create a step for each team member.
    # Use a positional suffix to ensure unique node IDs when the same
    # agent appears in multiple steps (e.g. parallel teams with duplicates).
    member_steps = []
    for index, node in enumerate(nodes):
        step_fn = _make_member_step(node)
        step = builder.step(call=step_fn, node_id=f"{node.name}_{index}")
        member_steps.append(step)

    # Join that collects all member outputs into a list
    collect = builder.join(
        reduce_list_append,
        initial_factory=list[_MemberOutput],
        node_id="team_join",
    )

    # Wire: start -> fork -> members -> join -> end
    builder.add(
        builder.edge_from(builder.start_node).to(*member_steps),
        builder.edge_from(*member_steps).to(collect),
        builder.edge_from(collect).to(builder.end_node),
    )

    return builder


async def run_team_graph(
    nodes: list[MessageNode[Any, Any]],
    state: _TeamGraphState,
) -> TeamResponse:
    """Execute a team via pydantic-graph and return a :class:`TeamResponse`.

    Args:
        nodes: Team members to execute.
        state: Shared graph state carrying prompts, kwargs, and tracking data.

    Returns:
        A :class:`TeamResponse` with successful responses and any errors.
    """
    from agentpool.utils.time_utils import get_now

    start_time = get_now()
    graph = build_team_graph(nodes).build()
    results: list[_MemberOutput] = await graph.run(state=state)

    responses: list[AgentResponse[Any]] = []
    errors: dict[str, Exception] = {}
    for output in results:
        if output.exception is not None:
            errors[output.agent_name] = output.exception
        elif output.response is not None:
            responses.append(output.response)

    return TeamResponse(
        responses=responses,
        start_time=start_time,
        errors=errors,
        child_session_ids=state.child_session_ids,
    )
