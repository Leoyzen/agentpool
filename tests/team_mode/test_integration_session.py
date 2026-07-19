"""L2 integration test: member session leak when lead run terminates.

Reproduces the bug where member sessions spawned by ``team_create`` leak
when the lead agent's RunHandle terminates without ``close_session`` being
called on the lead session (e.g., the run finishes but the session stays
alive for follow-ups).

The test uses a **real** AgentPool + SessionPool (no mocks) with a
manually created RunHandle so the full session lifecycle (RunHandle,
background tasks, complete_event) is exercised without depending on
TestModel timing.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock
import uuid

import pytest
import yamling

from agentpool import AgentPool, AgentsManifest
from agentpool.capabilities.agent_context import AgentContext
from agentpool.capabilities.runloop_delegation import RunLoopDelegationService
from agentpool.capabilities.team_comm_capability import TeamCommCapability
from agentpool.host.context import RunScope
from agentpool.host.registry import AgentRegistry
from agentpool.orchestrator.core import SessionState  # noqa: TC001
from agentpool.orchestrator.run import RunHandle


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


def _inject_run_handle(
    session_pool: Any,
    session: SessionState,
) -> RunHandle:
    """Manually create and register a RunHandle for the given session.

    This avoids depending on TestModel timing — the RunHandle is created
    in IDLE state and can be closed directly to simulate run termination.
    """
    run_id = str(uuid.uuid4())
    run_handle = RunHandle(
        run_id=run_id,
        session_id=session.session_id,
        agent_type="native",
    )
    session_pool.sessions._runs[run_id] = run_handle
    session.current_run_id = run_id
    return run_handle


@pytest.mark.integration
async def test_member_sessions_closed_when_lead_run_terminates(
    tmp_path: Any,
) -> None:
    """Given: real AgentPool with team_mode, lead session with active RunHandle.

    When: team_create spawns member sessions, then the lead goes idle
        (last_active_at set far in the past to trigger auto-cleanup).

    Then: member sessions are automatically closed (not leaked).
        The cleanup polls last_active_at every _poll_interval seconds
        and closes members after _idle_timeout seconds of inactivity.
    """
    # Use short intervals for testing.
    TeamCommCapability._idle_timeout = 0.5
    TeamCommCapability._poll_interval = 0.1
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

        lead_session = session_pool.sessions.get_session(lead_session_id)
        assert lead_session is not None

        # Manually inject a RunHandle so the session has an active run
        # (needed for team_create's cleanup scheduling).
        _inject_run_handle(session_pool, lead_session)

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

        # Extract member session IDs from team state (exclude lead).
        from agentpool.capabilities.file_team_state import FileTeamState

        team_id = create_result.split("team_id=")[1].strip()
        team_state = FileTeamState(str(tmp_path))
        state = team_state._read_json(team_state._state_path(team_id))
        lead_sid = session_pool.sessions.get_session("lead-session-001")
        lead_session_id = lead_sid.session_id if lead_sid else "lead-session-001"
        member_session_ids: list[str] = [
            m["session_id"]
            for m in state.get("members", {}).values()
            if m.get("session_id") and m["session_id"] != lead_session_id
        ]
        assert len(member_session_ids) == 2

        # Verify member sessions exist in SessionPool.
        for msid in member_session_ids:
            ms = session_pool.sessions.get_session(msid)
            assert ms is not None, f"Member session {msid} should exist after team_create"

        # Simulate the lead going idle: set last_active_at far in the past
        # so the polling cleanup triggers after _idle_timeout seconds.
        import time

        lead_session.last_active_at = time.monotonic() - 100.0

        # Wait for the polling cleanup to detect idle and close members.
        # _poll_interval=0.1, _idle_timeout=0.5 → cleanup fires within ~1s.
        await asyncio.sleep(2.0)

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
