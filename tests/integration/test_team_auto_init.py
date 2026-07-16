"""Integration tests for TeamCommCapability auto_init (T10).

These tests exercise the full auto_init flow end-to-end using real
FileTeamState on tmp_path, with a mocked SessionPool for async calls.
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

    ctx = MagicMock()
    ctx.deps = agent_ctx
    return ctx


@pytest.mark.integration
async def test_auto_init_full_flow(tmp_path: Any) -> None:
    """Given: TeamCommCapability with auto_init config, lead role, no team_id.

    When: team_status is called (triggers auto_init).
    Then: team_id written to metadata, FileTeamState has the team.
    """
    from agentpool.capabilities.file_team_state import FileTeamState

    config = _make_auto_init_config(str(tmp_path))

    mock_pool = MagicMock()
    mock_pool.create_session = AsyncMock()
    mock_pool.send_message = AsyncMock(return_value="msg_id")
    mock_pool.close_session = AsyncMock()

    mock_registry = MagicMock()
    mock_registry.exists = MagicMock(return_value=True)

    lead_metadata: dict[str, Any] = {
        "team_role": "lead",
        "team_member_name": "coordinator",
    }
    ctx = _make_run_context(lead_metadata, mock_pool, config, mock_registry)
    cap = TeamCommCapability(config, "coordinator", lead_metadata)

    # team_status triggers auto_init, then reads the created team state.
    await cap.team_status(ctx)

    # Auto-init should have created the team.
    assert "team_id" in lead_metadata
    team_id: str = lead_metadata["team_id"]
    assert lead_metadata["team_name"] == "auto_integration_team"

    # FileTeamState should have the team on disk.
    team_state = FileTeamState(str(tmp_path))
    state_path = team_state._state_path(team_id)
    assert state_path.exists()

    state = team_state._read_json(state_path)
    assert state["team_name"] == "auto_integration_team"
    assert "translator" in state["members"]
    assert "reviewer" in state["members"]

    # Session pool should have been called for each member.
    assert mock_pool.create_session.await_count == 2
    assert mock_pool.send_message.await_count == 2


@pytest.mark.integration
async def test_auto_init_graceful_degradation(tmp_path: Any) -> None:
    """Given: auto_init config, but session_pool.create_session raises.

    When: a tool is called.
    Then: error message returned, no crash.
    """
    config = _make_auto_init_config(str(tmp_path))

    mock_pool = MagicMock()
    mock_pool.create_session = AsyncMock(
        side_effect=RuntimeError("Session creation failed")
    )
    mock_pool.send_message = AsyncMock(return_value="msg_id")

    mock_registry = MagicMock()
    mock_registry.exists = MagicMock(return_value=True)

    lead_metadata: dict[str, Any] = {
        "team_role": "lead",
        "team_member_name": "coordinator",
    }
    ctx = _make_run_context(lead_metadata, mock_pool, config, mock_registry)
    cap = TeamCommCapability(config, "coordinator", lead_metadata)

    result = await cap.team_status(ctx)

    assert "Auto-init failed" in result
    assert "Team tools unavailable" in result
    # team_id should NOT have been written to metadata on failure.
    assert "team_id" not in lead_metadata


@pytest.mark.integration
async def test_auto_init_team_can_be_deleted(tmp_path: Any) -> None:
    """Given: auto_init creates a team on first call.

    When: team_delete is called afterwards.
    Then: team is successfully deleted.
    """
    from agentpool.capabilities.file_team_state import FileTeamState

    config = _make_auto_init_config(str(tmp_path))

    mock_pool = MagicMock()
    mock_pool.create_session = AsyncMock()
    mock_pool.send_message = AsyncMock(return_value="msg_id")
    mock_pool.close_session = AsyncMock()

    mock_registry = MagicMock()
    mock_registry.exists = MagicMock(return_value=True)

    lead_metadata: dict[str, Any] = {
        "team_role": "lead",
        "team_member_name": "coordinator",
    }
    ctx = _make_run_context(lead_metadata, mock_pool, config, mock_registry)
    cap = TeamCommCapability(config, "coordinator", lead_metadata)

    # First call triggers auto_init.
    await cap.team_status(ctx)
    assert "team_id" in lead_metadata
    team_id: str = lead_metadata["team_id"]

    # Verify team exists on disk.
    team_state = FileTeamState(str(tmp_path))
    assert team_state._state_path(team_id).exists()

    # Now delete the team — auto_init should be skipped (team_id exists).
    result = await cap.team_delete(ctx)

    assert result == "Team deleted"
    # Team state should be cleaned up.
    assert not team_state._state_path(team_id).exists()
    # close_session should have been called for each member.
    assert mock_pool.close_session.await_count == 2
