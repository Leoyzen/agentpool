"""L3 integration test: member session leak when lead run terminates.

Reproduces the bug where member sessions spawned by ``team_create`` leak
when the lead agent's RunHandle terminates without ``close_session`` being
called on the lead session (e.g., the run finishes but the session stays
alive for follow-ups).

The test uses a **real** AgentPool + SessionPool (no mocks) with TestModel
agents so the full session lifecycle (RunHandle, background tasks, EventBus)
is exercised end-to-end.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
import yamling

from agentpool import AgentPool, AgentsManifest
from agentpool.capabilities.agent_context import AgentContext
from agentpool.capabilities.runloop_delegation import RunLoopDelegationService
from agentpool.capabilities.team_comm_capability import TeamCommCapability
from agentpool.host.context import RunScope
from agentpool.host.registry import AgentRegistry


if TYPE_CHECKING:
    from agentpool_config.team_mode import TeamModeConfig


def _make_manifest(tmp_path: Any) -> AgentsManifest:
    """Create a manifest with team_mode enabled and TestModel agents."""
    yaml_str = """
agents:
  coordinator:
    type: native
    model: test
    system_prompt: "You are a team coordinator"
  worker:
    type: native
    model: test
    system_prompt: "You are a team worker"
  reviewer:
    type: native
    model: test
    system_prompt: "You are a team reviewer"

team_mode:
  enabled: true
  member_eligible: [worker, reviewer]
  lead_eligible: [coordinator]
  base_dir: {base_dir}
"""
    yaml_str = yaml_str.format(base_dir=str(tmp_path))
    config_dict = yamling.load_yaml(yaml_str, verify_type=dict)
    return AgentsManifest(**config_dict)


def _make_agent_context(
    pool: AgentPool[Any],
    session_id: str,
    team_mode_config: TeamModeConfig,
) -> AgentContext:
    """Construct a real AgentContext for calling team_create directly."""
    session_pool = pool.session_pool
    assert session_pool is not None
    session = session_pool.sessions.get_session(session_id)
    assert session is not None

    host_ctx = pool.get_context()
    registry = AgentRegistry(dict.fromkeys(pool.manifest.agents))
    delegation = RunLoopDelegationService(
        registry=registry,
        host=host_ctx,
        session_id=session_id,
    )
    scope = RunScope(
        config_id=None,
        tenant_id=None,
        user_id=None,
        session_id=session_id,
    )
    return AgentContext(
        agent_registry=registry,
        delegation=delegation,
        session=session,
        scope=scope,
        host=host_ctx,
        team_mode_config=team_mode_config,
    )


def _make_mock_run_context(agent_ctx: AgentContext) -> MagicMock:
    """Create a mock pydantic-ai RunContext with AgentContext as deps.

    ``_resolve_agent_context`` checks ``isinstance(deps, AgentContext)``
    from ``capabilities.agent_context`` — our AgentContext matches that
    check and is returned directly.
    """
    ctx = MagicMock()
    ctx.deps = agent_ctx
    return ctx


@pytest.mark.integration
async def test_member_sessions_closed_when_lead_run_terminates(
    tmp_path: Any,
) -> None:
    """Given: real AgentPool with team_mode, lead session with active run.

    When: team_create spawns member sessions, then the lead's RunHandle
        terminates (RunHandle.close() called directly, simulating run
        completion without close_session on the lead).

    Then: member sessions are automatically closed (not leaked).
        Without the fix, member sessions stay active because
        ``_close_session_unlocked`` cascade close only runs when
        ``close_session(lead)`` is called — not when the RunHandle
        terminates independently.
    """
    manifest = _make_manifest(tmp_path)
    team_mode_config: TeamModeConfig | None = manifest.team_mode
    assert team_mode_config is not None

    async with AgentPool(manifest) as pool:
        session_pool = pool.session_pool
        assert session_pool is not None

        lead_session_id = "lead-session-001"

        # Create lead session.
        await session_pool.create_session(
            lead_session_id,
            agent_name="coordinator",
            team_role="lead",
            team_member_name="coordinator",
        )

        # Start a run on the lead session by sending a message.
        # TestModel will respond immediately, but the run stays alive
        # in IDLE state for follow-ups.
        await session_pool.send_message(
            lead_session_id,
            "Start working",
        )
        # Give the run a moment to start and process the TestModel response.
        await asyncio.sleep(0.5)

        # Verify the lead session has an active run.
        lead_session = session_pool.sessions.get_session(lead_session_id)
        assert lead_session is not None
        assert lead_session.current_run_id is not None

        # Construct AgentContext and call team_create.
        agent_ctx = _make_agent_context(pool, lead_session_id, team_mode_config)
        cap = TeamCommCapability(
            team_mode_config,
            "coordinator",
            session_metadata={
                "team_role": "lead",
                "team_member_name": "coordinator",
            },
        )

        mock_ctx = _make_mock_run_context(agent_ctx)

        create_result = await cap.team_create(
            mock_ctx,
            "test_team",
            [
                {"agent": "worker", "name": "worker_1"},
                {"agent": "reviewer", "name": "reviewer_1"},
            ],
        )
        assert "Team 'test_team' created with 2 members" in create_result

        # Extract member session IDs from team state.
        from agentpool.capabilities.file_team_state import FileTeamState

        team_id = create_result.split("team_id=")[1].strip()
        team_state = FileTeamState(str(tmp_path))
        state = team_state._read_json(team_state._state_path(team_id))
        member_session_ids: list[str] = [
            m["session_id"] for m in state.get("members", {}).values() if m.get("session_id")
        ]
        assert len(member_session_ids) == 2

        # Verify member sessions exist in SessionPool.
        for msid in member_session_ids:
            ms = session_pool.sessions.get_session(msid)
            assert ms is not None, f"Member session {msid} should exist after team_create"

        # Simulate the lead's RunHandle terminating WITHOUT close_session.
        # This happens when the run finishes but the session stays alive
        # for follow-ups (protocol server scenario).
        lead_run_id = lead_session.current_run_id
        lead_run_handle = session_pool.sessions._runs.get(lead_run_id)
        assert lead_run_handle is not None

        # Close the RunHandle directly (not close_session).
        lead_run_handle.close()

        # Wait for complete_event to fire and cleanup callbacks to execute.
        await asyncio.wait_for(
            lead_run_handle.complete_event.wait(),
            timeout=10.0,
        )
        # Give the fix's cleanup callback time to run after complete_event.
        # Member sessions have active runs that need to be cancelled and
        # cleaned up, which involves RunHandle cancellation with timeouts.
        await asyncio.sleep(3.0)

        # Assert member sessions are closed (not leaked).
        # Without the fix: member sessions stay active (BUG).
        # With the fix: member sessions are auto-closed by the callback
        # registered in team_create.
        for msid in member_session_ids:
            ms = session_pool.sessions.get_session(msid)
            assert ms is None, (
                f"Member session {msid} should be closed after lead run terminates, "
                "but it is still active — session leak detected"
            )

        # Cleanup: close the lead session (still alive without a run).
        await session_pool.close_session(lead_session_id)
