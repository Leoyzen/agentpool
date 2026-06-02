"""Tests for TeamRun.run_stream() with depth, session_id and child session support.

Covers RFC-0028 Task T12: Adapted streamed TeamRun sequential execution.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool import Agent, AgentPool
from agentpool.agents.events import SpawnSessionStart, StreamCompleteEvent, SubAgentEvent
from agentpool.agents.exceptions import DelegationDepthError, MAX_DELEGATION_DEPTH
from agentpool.delegation.teamrun import TeamRun
from agentpool.messaging import ChatMessage


async def _collect_events(team_run: TeamRun[Any, Any], *args: Any, **kwargs: Any) -> list[Any]:
    """Collect all events from run_stream into a list."""
    events: list[Any] = []
    async for event in team_run.run_stream(*args, **kwargs):
        events.append(event)
    return events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_echo_agent(name: str, response: str = "hello") -> Agent[Any, str]:
    """Create an Agent that echoes a fixed response via function_to_model."""
    from functools import partial

    from llmling_models import function_to_model

    async def _echo(_msg: str, *, _response: str = response) -> str:
        return _response

    model = function_to_model(partial(_echo, _response=response))
    return Agent(name=name, model=model)


# ---------------------------------------------------------------------------
# Tests: depth parameter
# ---------------------------------------------------------------------------


async def test_run_stream_accepts_depth_without_type_error():
    """TeamRun.run_stream(..., depth=1, require_all=False) must not raise TypeError."""
    agent1 = _make_echo_agent("a1", "first")
    agent2 = _make_echo_agent("a2", "second")
    team = TeamRun([agent1, agent2], name="seq")

    async with agent1, agent2:
        # The call itself must succeed — no TypeError about unexpected keyword
        events = await _collect_events(team, "prompt", depth=1, require_all=False)
        assert len(events) > 0


async def test_run_stream_default_depth_is_zero():
    """Without explicit depth, the default is 0 and child_depth should be 1."""
    agent1 = _make_echo_agent("a1", "first")
    team = TeamRun([agent1], name="seq")

    async with agent1:
        events = await _collect_events(team, "prompt")
        sub_events = [e for e in events if isinstance(e, SubAgentEvent)]
        # At depth=0 (default), child_depth=1
        for se in sub_events:
            assert se.depth == 1


async def test_run_stream_depth_propagates_to_sub_events():
    """Explicit depth=2 should produce SubAgentEvent with depth=3 (child_depth)."""
    agent1 = _make_echo_agent("a1", "result")
    team = TeamRun([agent1], name="seq")

    async with agent1:
        events = await _collect_events(team, "prompt", depth=2)
        sub_events = [e for e in events if isinstance(e, SubAgentEvent)]
        for se in sub_events:
            assert se.depth == 3


# ---------------------------------------------------------------------------
# Tests: child sessions
# ---------------------------------------------------------------------------


async def test_each_member_gets_own_child_session():
    """Each team member should get its own SpawnSessionStart + SubAgentEvent with unique child_session_id."""
    agent1 = _make_echo_agent("a1", "first")
    agent2 = _make_echo_agent("a2", "second")
    team = TeamRun([agent1, agent2], name="seq")

    async with agent1, agent2:
        events = await _collect_events(team, "prompt", session_id="parent-123")

        spawn_events = [e for e in events if isinstance(e, SpawnSessionStart)]
        assert len(spawn_events) == 2  # one per member

        # Each member should have a different child_session_id
        child_ids = {e.child_session_id for e in spawn_events}
        assert len(child_ids) == 2

        # All should reference the parent session
        for se in spawn_events:
            assert se.parent_session_id == "parent-123"
            assert se.source_name in {"a1", "a2"}


async def test_sub_events_carry_child_session_ids():
    """SubAgentEvent wrappers should carry child_session_id and parent_session_id."""
    agent1 = _make_echo_agent("a1", "first")
    team = TeamRun([agent1], name="seq")

    async with agent1:
        events = await _collect_events(team, "prompt", session_id="parent-456")
        sub_events = [e for e in events if isinstance(e, SubAgentEvent)]
        assert len(sub_events) > 0

        for se in sub_events:
            assert se.child_session_id is not None
            assert se.parent_session_id == "parent-456"


async def test_spawn_session_start_fields():
    """SpawnSessionStart events should have correct fields."""
    agent1 = _make_echo_agent("a1", "result")
    team = TeamRun([agent1], name="seq")

    async with agent1:
        events = await _collect_events(team, "prompt", session_id="parent-789")
        spawn_events = [e for e in events if isinstance(e, SpawnSessionStart)]
        assert len(spawn_events) == 1

        se = spawn_events[0]
        assert se.source_name == "a1"
        assert se.source_type == "agent"
        assert se.spawn_mechanism == "spawn"
        assert se.parent_session_id == "parent-789"
        assert se.depth == 1  # child_depth at default depth=0


async def test_child_session_uses_generate_session_id_when_no_pool():
    """Without a pool, child sessions should use generate_session_id() as fallback."""
    agent1 = _make_echo_agent("a1", "result")
    team = TeamRun([agent1], name="seq")
    # No pool → fallback to generate_session_id()

    async with agent1:
        events = await _collect_events(team, "prompt", session_id="parent-abc")
        spawn_events = [e for e in events if isinstance(e, SpawnSessionStart)]
        assert len(spawn_events) == 1
        # Should start with "ses_" prefix (from generate_session_id)
        assert spawn_events[0].child_session_id.startswith("ses_")


async def test_child_session_uses_pool_sessions_when_available():
    """With a pool, child sessions should be created via pool.sessions.create_child_session()."""
    agent1 = _make_echo_agent("a1", "result")
    team = TeamRun([agent1], name="seq")

    # Create mock pool with session_pool
    mock_pool = MagicMock(spec=AgentPool)
    mock_sessions = AsyncMock()
    mock_sessions.create_session = AsyncMock(
        return_value=MagicMock(session_id="child-via-pool")
    )
    mock_pool.session_pool = mock_sessions
    team.agent_pool = mock_pool

    async with agent1:
        events = await _collect_events(team, "prompt", session_id="parent-via-pool")
        spawn_events = [e for e in events if isinstance(e, SpawnSessionStart)]
        assert len(spawn_events) == 1
        assert spawn_events[0].child_session_id == "child-via-pool"

        # Verify create_session was called correctly
        from unittest.mock import ANY
        mock_sessions.create_session.assert_called_once_with(
            session_id=ANY,
            parent_session_id="parent-via-pool",
            agent_name="a1",
            agent_type="agent",
        )


# ---------------------------------------------------------------------------
# Tests: sequential handoff
# ---------------------------------------------------------------------------


async def test_sequential_handoff_uses_stream_complete_content():
    """The second agent should receive the first agent's StreamComplete content."""
    agent1 = _make_echo_agent("a1", "first output")
    agent2 = _make_echo_agent("a2", "second output")
    team = TeamRun([agent1, agent2], name="seq")

    received_prompts: list[tuple[str, ...]] = []

    # Intercept what agent2 receives by patching its run_stream
    original_run_stream = agent2.run_stream

    async def _capturing_run_stream(*prompts: Any, **kwargs: Any) -> Any:
        received_prompts.append(prompts)
        async for event in original_run_stream(*prompts, **kwargs):
            yield event

    agent2.run_stream = _capturing_run_stream  # type: ignore[assignment]

    async with agent1, agent2:
        await _collect_events(team, "initial prompt", session_id="parent-handoff")

    # Agent2 should have received the first agent's output as its prompt
    assert len(received_prompts) == 1
    assert received_prompts[0] == ("first output",)


# ---------------------------------------------------------------------------
# Tests: depth guard
# ---------------------------------------------------------------------------


async def test_depth_guard_raises_delegation_depth_error():
    """Exceeding MAX_DELEGATION_DEPTH should raise DelegationDepthError."""
    agent1 = _make_echo_agent("a1", "result")
    team = TeamRun([agent1], name="seq")

    async with agent1:
        with pytest.raises(DelegationDepthError):
            async for _ in team.run_stream("prompt", depth=MAX_DELEGATION_DEPTH):
                pass


async def test_depth_guard_at_boundary():
    """depth = MAX_DELEGATION_DEPTH - 1 should still work (child_depth = MAX_DELEGATION_DEPTH)."""
    agent1 = _make_echo_agent("a1", "result")
    team = TeamRun([agent1], name="seq")

    async with agent1:
        # child_depth = MAX_DELEGATION_DEPTH, which equals the limit but doesn't exceed it
        events = await _collect_events(team, "prompt", depth=MAX_DELEGATION_DEPTH - 1)
        sub_events = [e for e in events if isinstance(e, SubAgentEvent)]
        assert len(sub_events) > 0
        assert sub_events[0].depth == MAX_DELEGATION_DEPTH


# ---------------------------------------------------------------------------
# Tests: nested SubAgentEvent depth preservation
# ---------------------------------------------------------------------------


async def test_nested_subagent_depth_incremented():
    """When a member yields a SubAgentEvent, the depth should be incremented by 1."""
    agent1 = _make_echo_agent("a1", "result")
    # Manually create a nested SubAgentEvent to simulate a nested team member
    inner_complete = StreamCompleteEvent(
        message=ChatMessage(role="assistant", content="inner result"),
    )
    inner_sub = SubAgentEvent(
        source_name="inner_agent",
        source_type="agent",
        event=inner_complete,
        depth=2,
        child_session_id="inner-child-123",
        parent_session_id="inner-parent-456",
    )

    # Patch agent1.run_stream to yield our nested SubAgentEvent
    original_run_stream = agent1.run_stream

    async def _nested_run_stream(*prompts: Any, **kwargs: Any) -> Any:
        # First yield a text event as normal
        async for event in original_run_stream(*prompts, **kwargs):
            yield event
        # Then yield the nested SubAgentEvent
        yield inner_sub

    agent1.run_stream = _nested_run_stream  # type: ignore[assignment]

    team = TeamRun([agent1], name="seq")
    async with agent1:
        events = await _collect_events(team, "prompt", depth=1, session_id="parent-nested")

        for e in events:
            if isinstance(e, SubAgentEvent) and e.source_name == "inner_agent":
                assert e.depth == 3  # 2 + 1
                assert e.child_session_id == "inner-child-123"
                assert e.parent_session_id == "inner-parent-456"


# ---------------------------------------------------------------------------
# Tests: kwargs pop semantics
# ---------------------------------------------------------------------------


async def test_session_id_popped_from_kwargs():
    """session_id in kwargs should be popped and not forwarded as duplicate."""
    agent1 = _make_echo_agent("a1", "result")
    team = TeamRun([agent1], name="seq")

    async with agent1:
        # This should NOT raise TypeError about duplicate keyword argument
        events = await _collect_events(team, "prompt", session_id="ses-123")
        assert len(events) > 0


async def test_depth_popped_from_kwargs():
    """depth in kwargs should be popped; explicit parameter takes precedence."""
    agent1 = _make_echo_agent("a1", "result")
    team = TeamRun([agent1], name="seq")

    async with agent1:
        # depth=5 in explicit param should win over any kwargs depth
        events = await _collect_events(team, "prompt", depth=5, session_id="ses-depth")
        sub_events = [e for e in events if isinstance(e, SubAgentEvent)]
        for se in sub_events:
            assert se.depth == 6  # child_depth = 5 + 1


# ---------------------------------------------------------------------------
# Tests: require_all preserved
# ---------------------------------------------------------------------------


async def test_require_all_still_propagates_errors():
    """require_all=True should still raise on member failure."""
    failing_agent = _make_echo_agent("fail", "nope")

    async def _failing_stream(*_prompts: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("Agent failed")
        yield  # noqa: UNREACHABLE

    failing_agent.run_stream = _failing_stream  # type: ignore[assignment]

    team = TeamRun([failing_agent], name="seq")
    async with failing_agent:
        with pytest.raises(ValueError, match="Chain broken"):
            await _collect_events(team, "prompt", require_all=True)


async def test_require_all_false_continues_on_error():
    """require_all=False should continue when a member fails."""
    failing_agent = _make_echo_agent("fail", "nope")

    async def _failing_stream(*_prompts: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("Agent failed")
        yield  # noqa: UNREACHABLE

    failing_agent.run_stream = _failing_stream  # type: ignore[assignment]

    good_agent = _make_echo_agent("good", "I survived")
    team = TeamRun([failing_agent, good_agent], name="seq")

    async with failing_agent, good_agent:
        events = await _collect_events(team, "prompt", require_all=False)
        # Should have events from the good agent
        sub_events = [e for e in events if isinstance(e, SubAgentEvent) and e.source_name == "good"]
        assert len(sub_events) > 0
