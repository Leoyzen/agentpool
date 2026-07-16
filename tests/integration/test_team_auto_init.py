"""Integration tests for team_create with config default members.

These tests exercise the team_create flow with auto_init config providing
default members when the LLM passes an empty members list. Uses real
FileTeamState on tmp_path, with mocked SessionPool and DelegationService.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.capabilities.team_comm_capability import TeamCommCapability
from agentpool_config.team_mode import AutoInitConfig, MemberSpec, TeamModeConfig


def _make_auto_init_config(base_dir: str) -> TeamModeConfig:
    """Create an enabled TeamModeConfig with auto_init for testing."""
    return TeamModeConfig(
        enabled=True,
        member_eligible=["translator", "reviewer"],
        lead_eligible=["coordinator"],
        base_dir=base_dir,
        auto_init=AutoInitConfig(
            team_name="auto_integration_team",
            members=[
                MemberSpec(name="translator", agent="translator"),
                MemberSpec(name="reviewer", agent="reviewer"),
            ],
        ),
    )


def _make_run_context(
    metadata: dict[str, Any],
    session_pool: MagicMock,
    config: TeamModeConfig,
    agent_registry: MagicMock,
    delegation: MagicMock,
    session_id: str = "lead_session_001",
) -> MagicMock:
    """Create a mock RunContext with AgentContext deps for integration tests."""
    from agentpool.capabilities.agent_context import AgentContext

    agent_ctx = MagicMock(spec=AgentContext)
    agent_ctx.session.metadata = metadata
    agent_ctx.host.session_pool = session_pool
    agent_ctx.team_mode_config = config
    agent_ctx.agent_registry = agent_registry
    agent_ctx.session.session_id = session_id
    agent_ctx.delegation = delegation

    ctx = MagicMock()
    ctx.deps = agent_ctx
    return ctx


@pytest.mark.integration
async def test_team_create_with_config_default_members(tmp_path: Any) -> None:
    """Given: TeamCommCapability with auto_init config, lead role.

    When: team_create is called with empty members.
    Then: uses auto_init.members to create the team with child sessions.
    """
    from agentpool.capabilities.file_team_state import FileTeamState

    config = _make_auto_init_config(str(tmp_path))

    mock_pool = MagicMock()
    mock_pool.send_message = AsyncMock(return_value="msg_id")
    mock_pool.close_session = AsyncMock()

    mock_registry = MagicMock()
    mock_registry.exists = MagicMock(return_value=True)

    child_ids = iter(["child_translator", "child_reviewer"])
    mock_delegation = MagicMock()
    mock_delegation.create_child_session = AsyncMock(
        side_effect=lambda *a, **kw: next(child_ids),
    )

    lead_metadata: dict[str, Any] = {
        "team_role": "lead",
        "team_member_name": "coordinator",
    }
    ctx = _make_run_context(lead_metadata, mock_pool, config, mock_registry, mock_delegation)
    cap = TeamCommCapability(config, "coordinator", lead_metadata)

    result = await cap.team_create(ctx, "my_team", [])

    assert "Team 'my_team' created with 2 members" in result
    assert "team_id=" in result
    team_id = result.split("team_id=")[1].strip()

    # FileTeamState should have the team on disk.
    team_state = FileTeamState(str(tmp_path))
    state_path = team_state._state_path(team_id)
    assert state_path.exists()

    state = team_state._read_json(state_path)
    assert state["team_name"] == "my_team"
    assert "translator" in state["members"]
    assert "reviewer" in state["members"]

    # Delegation and session pool should have been called for each member.
    assert mock_delegation.create_child_session.await_count == 2
    assert mock_pool.send_message.await_count == 2


@pytest.mark.integration
async def test_team_create_config_default_members_graceful_degradation(
    tmp_path: Any,
) -> None:
    """Given: auto_init config, but delegation.create_child_session raises.

    When: team_create is called with empty members.
    Then: error message returned, no crash, team state cleaned up.
    """
    config = _make_auto_init_config(str(tmp_path))

    mock_pool = MagicMock()
    mock_pool.send_message = AsyncMock(return_value="msg_id")
    mock_pool.close_session = AsyncMock()

    mock_registry = MagicMock()
    mock_registry.exists = MagicMock(return_value=True)

    mock_delegation = MagicMock()
    mock_delegation.create_child_session = AsyncMock(
        side_effect=RuntimeError("Session creation failed"),
    )

    lead_metadata: dict[str, Any] = {
        "team_role": "lead",
        "team_member_name": "coordinator",
    }
    ctx = _make_run_context(lead_metadata, mock_pool, config, mock_registry, mock_delegation)
    cap = TeamCommCapability(config, "coordinator", lead_metadata)

    result = await cap.team_create(ctx, "my_team", [])

    assert "Failed to create team" in result
    assert "Session creation failed" in result


@pytest.mark.integration
async def test_team_create_config_default_members_then_delete(tmp_path: Any) -> None:
    """Given: team_create with config default members creates a team.

    When: team_delete is called afterwards.
    Then: team is successfully deleted.
    """
    from agentpool.capabilities.file_team_state import FileTeamState

    config = _make_auto_init_config(str(tmp_path))

    mock_pool = MagicMock()
    mock_pool.send_message = AsyncMock(return_value="msg_id")
    mock_pool.close_session = AsyncMock()

    mock_registry = MagicMock()
    mock_registry.exists = MagicMock(return_value=True)

    child_ids = iter(["child_translator", "child_reviewer"])
    mock_delegation = MagicMock()
    mock_delegation.create_child_session = AsyncMock(
        side_effect=lambda *a, **kw: next(child_ids),
    )

    lead_metadata: dict[str, Any] = {
        "team_role": "lead",
        "team_member_name": "coordinator",
    }
    ctx = _make_run_context(lead_metadata, mock_pool, config, mock_registry, mock_delegation)
    cap = TeamCommCapability(config, "coordinator", lead_metadata)

    # Create the team with empty members (uses auto_init config).
    create_result = await cap.team_create(ctx, "my_team", [])
    assert "Team 'my_team' created with 2 members" in create_result
    team_id = create_result.split("team_id=")[1].strip()

    # Verify team exists on disk.
    team_state = FileTeamState(str(tmp_path))
    assert team_state._state_path(team_id).exists()

    # Write team_id into metadata so team_delete can find it.
    lead_metadata["team_id"] = team_id
    lead_metadata["team_name"] = "my_team"

    # Now delete the team.
    result = await cap.team_delete(ctx)

    assert result == "Team deleted"
    assert not team_state._state_path(team_id).exists()
    assert mock_pool.close_session.await_count == 2
