"""Tests for Team.run_stream() session hierarchy and depth adaptation (RFC-0028 T11)."""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool import Agent, AgentPool, Team
from agentpool.agents.events import SpawnSessionStart, SubAgentEvent
from agentpool.agents.exceptions import DelegationDepthError, MAX_DELEGATION_DEPTH

pytestmark = pytest.mark.filterwarnings(
    "ignore::DeprecationWarning:agentpool.agents.base_agent"
)


# ---------------------------------------------------------------------------
# Signature tests
# ---------------------------------------------------------------------------


def test_team_run_stream_accepts_depth_param() -> None:
    """Team.run_stream() should accept depth parameter with default 0."""
    sig = inspect.signature(Team.run_stream)
    assert "depth" in sig.parameters
    assert sig.parameters["depth"].default == 0


# ---------------------------------------------------------------------------
# Depth guard tests
# ---------------------------------------------------------------------------


async def test_team_run_stream_depth_guard() -> None:
    """Team.run_stream() should raise DelegationDepthError when depth exceeds maximum."""
    async with AgentPool() as pool:
        agent_a = Agent(name="a", model="test")
        await pool.add_agent(agent_a)
        agent_b = Agent(name="b", model="test")
        await pool.add_agent(agent_b)
        team = Team([agent_a, agent_b])

        with pytest.raises(DelegationDepthError) as exc_info:
            # depth=MAX_DELEGATION_DEPTH means child_depth = MAX + 1 > MAX
            async for _ in team.run_stream("prompt", depth=MAX_DELEGATION_DEPTH):
                pass

        assert exc_info.value.current_depth == MAX_DELEGATION_DEPTH + 1


async def test_team_run_stream_depth_at_limit_ok() -> None:
    """Team.run_stream() should NOT raise at depth = MAX - 1 (child = MAX, still ok)."""
    from llmling_models import function_to_model

    async def echo(msg: str) -> str:
        return msg

    model = function_to_model(echo)
    async with AgentPool() as pool:
        agent_a = Agent(name="a", model=model)
        await pool.add_agent(agent_a)
        team = Team([agent_a])

        # depth = MAX - 1 → child_depth = MAX → should not raise
        events: list[Any] = []
        async for event in team.run_stream("hi", depth=MAX_DELEGATION_DEPTH - 1):
            events.append(event)
        # Should have at least SpawnSessionStart + SubAgentEvent events
        assert len(events) > 0


# ---------------------------------------------------------------------------
# SpawnSessionStart emission
# ---------------------------------------------------------------------------


async def test_team_run_stream_emits_spawn_session_start() -> None:
    """Each member should emit SpawnSessionStart before SubAgentEvent content."""
    from llmling_models import function_to_model

    async def echo(msg: str) -> str:
        return msg

    model = function_to_model(echo)
    async with AgentPool() as pool:
        agent_a = Agent(name="alpha", model=model)
        await pool.add_agent(agent_a)
        agent_b = Agent(name="beta", model=model)
        await pool.add_agent(agent_b)
        team = Team([agent_a, agent_b])

        events: list[Any] = []
        async for event in team.run_stream("test"):
            events.append(event)

        spawn_events = [e for e in events if isinstance(e, SpawnSessionStart)]
        sub_events = [e for e in events if isinstance(e, SubAgentEvent)]

        # Should have one SpawnSessionStart per member
        assert len(spawn_events) == 2
        spawn_names = {e.source_name for e in spawn_events}
        assert spawn_names == {"alpha", "beta"}

        # SpawnSessionStart depth should be 1 (child_depth when depth=0)
        for sp in spawn_events:
            assert sp.depth == 1
            assert sp.spawn_mechanism == "spawn"
            assert sp.source_type == "agent"

        # SubAgentEvents should also be present
        assert len(sub_events) >= 2


async def test_spawn_session_start_precedes_subagent_for_member() -> None:
    """For each member, SpawnSessionStart should appear before any SubAgentEvent from that member."""
    from llmling_models import function_to_model

    async def echo(msg: str) -> str:
        return msg

    model = function_to_model(echo)
    async with AgentPool() as pool:
        agent_a = Agent(name="alpha", model=model)
        await pool.add_agent(agent_a)
        team = Team([agent_a])

        events: list[Any] = []
        async for event in team.run_stream("test"):
            events.append(event)

        # Find indices of SpawnSessionStart and SubAgentEvent for alpha
        spawn_idx = None
        sub_idx = None
        for i, e in enumerate(events):
            if isinstance(e, SpawnSessionStart) and e.source_name == "alpha":
                spawn_idx = i
            if isinstance(e, SubAgentEvent) and e.source_name == "alpha":
                if sub_idx is None:  # first occurrence
                    sub_idx = i

        assert spawn_idx is not None, "No SpawnSessionStart for alpha"
        assert sub_idx is not None, "No SubAgentEvent for alpha"
        assert spawn_idx < sub_idx, "SpawnSessionStart must precede SubAgentEvent"


# ---------------------------------------------------------------------------
# Child session IDs and SubAgentEvent fields
# ---------------------------------------------------------------------------


async def test_subagent_event_preserves_session_ids() -> None:
    """SubAgentEvent should carry child_session_id and parent_session_id."""
    from llmling_models import function_to_model

    async def echo(msg: str) -> str:
        return msg

    model = function_to_model(echo)
    async with AgentPool() as pool:
        agent_a = Agent(name="alpha", model=model)
        await pool.add_agent(agent_a)
        team = Team([agent_a])

        events: list[Any] = []
        async for event in team.run_stream("test", session_id="parent_ses_123", depth=2):
            events.append(event)

        sub_events = [e for e in events if isinstance(e, SubAgentEvent)]
        assert len(sub_events) >= 1

        for se in sub_events:
            # child_session_id should be set (generated)
            assert se.child_session_id is not None
            assert se.child_session_id.startswith("ses_")
            # parent_session_id should match what we passed
            assert se.parent_session_id == "parent_ses_123"
            # depth should be child_depth = 2 + 1 = 3
            assert se.depth == 3


async def test_spawn_session_start_carries_session_ids() -> None:
    """SpawnSessionStart should carry child_session_id and parent_session_id."""
    from llmling_models import function_to_model

    async def echo(msg: str) -> str:
        return msg

    model = function_to_model(echo)
    async with AgentPool() as pool:
        agent_a = Agent(name="alpha", model=model)
        await pool.add_agent(agent_a)
        team = Team([agent_a])

        events: list[Any] = []
        # session_id is the caller's session — becomes parent for children
        async for event in team.run_stream("test", session_id="ses_parent_abc"):
            events.append(event)

        spawn_events = [e for e in events if isinstance(e, SpawnSessionStart)]
        assert len(spawn_events) == 1
        sp = spawn_events[0]
        assert sp.child_session_id.startswith("ses_")
        assert sp.parent_session_id == "ses_parent_abc"


# ---------------------------------------------------------------------------
# Out-of-pool Team (no persistence)
# ---------------------------------------------------------------------------


async def test_out_of_pool_team_generates_session_ids() -> None:
    """Team without pool should generate session IDs via generate_session_id() and not crash."""
    from llmling_models import function_to_model

    async def echo(msg: str) -> str:
        return msg

    model = function_to_model(echo)
    agent_a = Agent(name="alpha", model=model)
    agent_b = Agent(name="beta", model=model)
    # No AgentPool — team is standalone
    team = Team([agent_a, agent_b])

    events: list[Any] = []
    async for event in team.run_stream("hello"):
        events.append(event)

    # Should produce events without crashing
    spawn_events = [e for e in events if isinstance(e, SpawnSessionStart)]
    sub_events = [e for e in events if isinstance(e, SubAgentEvent)]

    assert len(spawn_events) == 2
    assert len(sub_events) >= 2

    # All child_session_ids should be generated (ses_ prefix)
    for sp in spawn_events:
        assert sp.child_session_id.startswith("ses_")
        # parent_session_id should be empty string (no parent available)
        assert sp.parent_session_id == ""

    # SubAgentEvent child_session_ids should also be set
    for se in sub_events:
        assert se.child_session_id is not None
        assert se.child_session_id.startswith("ses_")


# ---------------------------------------------------------------------------
# Pool-backed Team (with SessionManager)
# ---------------------------------------------------------------------------


async def test_pool_backed_team_creates_child_sessions() -> None:
    """Team with pool.sessions should call create_child_session for each member."""
    from llmling_models import function_to_model

    async def echo(msg: str) -> str:
        return msg

    model = function_to_model(echo)
    agent_a = Agent(name="alpha", model=model)
    agent_b = Agent(name="beta", model=model)
    team = Team([agent_a, agent_b])

    # Create a mock pool with session_pool
    mock_pool = AsyncMock()
    mock_sessions = AsyncMock()
    mock_sessions.create_session = AsyncMock(
        side_effect=[
            MagicMock(session_id="ses_child_alpha"),
            MagicMock(session_id="ses_child_beta"),
        ]
    )
    mock_pool.session_pool = mock_sessions

    # Set pool on team only (members should not delegate to mock session_pool)
    team.agent_pool = mock_pool

    events: list[Any] = []
    async for event in team.run_stream("test", session_id="ses_parent"):
        events.append(event)

    # create_session should have been called for each member
    assert mock_sessions.create_session.call_count == 2

    # Verify SpawnSessionStart events use the child session IDs from pool
    spawn_events = [e for e in events if isinstance(e, SpawnSessionStart)]
    spawn_sids = {e.child_session_id for e in spawn_events}
    assert spawn_sids == {"ses_child_alpha", "ses_child_beta"}

    # SubAgentEvents should also carry those IDs
    sub_events = [e for e in events if isinstance(e, SubAgentEvent)]
    for se in sub_events:
        assert se.child_session_id in {"ses_child_alpha", "ses_child_beta"}
        assert se.parent_session_id == "ses_parent"


# ---------------------------------------------------------------------------
# Kwargs popping (no duplicate keyword errors)
# ---------------------------------------------------------------------------


async def test_kwargs_session_id_depth_popped() -> None:
    """Passing session_id/depth in kwargs should not cause duplicate keyword errors."""
    from llmling_models import function_to_model

    async def echo(msg: str) -> str:
        return msg

    model = function_to_model(echo)
    async with AgentPool() as pool:
        agent_a = Agent(name="alpha", model=model)
        await pool.add_agent(agent_a)
        team = Team([agent_a])

        # This should NOT raise TypeError for duplicate keyword argument
        events: list[Any] = []
        async for event in team.run_stream(
            "test",
            session_id="ses_from_kwargs",  # passed via kwargs (will be popped)
            depth=5,  # also via kwargs
        ):
            events.append(event)

        # Verify events are produced normally
        spawn_events = [e for e in events if isinstance(e, SpawnSessionStart)]
        sub_events = [e for e in events if isinstance(e, SubAgentEvent)]
        assert len(spawn_events) >= 1
        assert len(sub_events) >= 1


# ---------------------------------------------------------------------------
# Team.run() unchanged
# ---------------------------------------------------------------------------


async def test_team_run_unchanged() -> None:
    """Team.run() should not be affected by run_stream() changes."""
    from llmling_models import function_to_model

    async def echo(msg: str) -> str:
        return msg

    model = function_to_model(echo)
    async with AgentPool() as pool:
        agent_a = Agent(name="alpha", model=model)
        await pool.add_agent(agent_a)
        agent_b = Agent(name="beta", model=model)
        await pool.add_agent(agent_b)
        team = Team([agent_a, agent_b])

        result = await team.run("test")
        # run() should still return a ChatMessage
        assert result is not None
        assert result.role == "assistant"


# ---------------------------------------------------------------------------
# Nested SubAgentEvent session IDs preserved
# ---------------------------------------------------------------------------


async def test_nested_subagent_event_session_ids_preserved() -> None:
    """When a Team member is itself a Team, nested SubAgentEvent IDs should be preserved."""
    from llmling_models import function_to_model

    async def echo(msg: str) -> str:
        return msg

    model = function_to_model(echo)
    async with AgentPool() as pool:
        inner_a = Agent(name="inner_a", model=model)
        await pool.add_agent(inner_a)
        inner_b = Agent(name="inner_b", model=model)
        await pool.add_agent(inner_b)
        inner_team = Team([inner_a, inner_b], name="inner_team")

        outer_agent = Agent(name="outer_agent", model=model)
        await pool.add_agent(outer_agent)
        outer_team = Team([inner_team, outer_agent], name="outer_team")

        events: list[Any] = []
        async for event in outer_team.run_stream("test"):
            events.append(event)

        # Verify nested SubAgentEvents preserve child_session_id and parent_session_id
        # from the inner team's stream
        nested_sub = [e for e in events if isinstance(e, SubAgentEvent) and e.depth > 1]
        for se in nested_sub:
            assert se.child_session_id is not None
            assert se.parent_session_id is not None
