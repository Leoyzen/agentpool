"""Tests for TeamCommCapability skeleton, registration, and per-session."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.capabilities.team_comm_capability import TeamCommCapability
from agentpool_config.team_mode import TeamBounds, TeamModeConfig


# ---- Helpers ----


def _make_enabled_config(
    *,
    member_eligible: list[str] | None = None,
    lead_eligible: list[str] | None = None,
    protocol_template: str | None = None,
    base_dir: str | None = None,
) -> TeamModeConfig:
    """Create an enabled TeamModeConfig for testing.

    Args:
        member_eligible: Agent names eligible as members.
        lead_eligible: Agent names eligible as leads.
        protocol_template: Custom protocol template string.
        base_dir: Base directory for team state files.

    Returns:
        A frozen TeamModeConfig with enabled=True.
    """
    return TeamModeConfig(
        enabled=True,
        member_eligible=member_eligible or ["worker"],
        lead_eligible=lead_eligible or ["coordinator"],
        protocol_template=protocol_template
        or "Team={team_name}, Role={role}, Member={member_name}",
        base_dir=base_dir,
    )


def _make_disabled_config() -> TeamModeConfig:
    """Create a disabled TeamModeConfig for testing."""
    return TeamModeConfig(
        enabled=False,
        member_eligible=["worker"],
        lead_eligible=["coordinator"],
    )


def _make_session_metadata() -> dict[str, Any]:
    """Create typical session metadata for a team session."""
    return {
        "team_id": "team_123",
        "team_name": "alpha_team",
        "team_role": "translator",
        "team_member_name": "translator_agent",
    }


# ---- Skeleton tests ----


@pytest.mark.unit
def test_skeleton_get_instructions_renders_template_with_metadata() -> None:
    """Given: enabled config + session metadata.

    When: get_instructions() is called.
    Then: returns rendered protocol template with actual metadata values.
    """
    config = _make_enabled_config()
    metadata = _make_session_metadata()
    cap = TeamCommCapability(config, "worker", metadata)

    result = cap.get_instructions()

    assert result is not None
    assert "alpha_team" in result
    assert "translator" in result
    assert "translator_agent" in result


@pytest.mark.unit
def test_skeleton_get_instructions_returns_none_when_disabled() -> None:
    """Given: disabled config + session metadata.

    When: get_instructions() is called.
    Then: returns None.
    """
    config = _make_disabled_config()
    metadata = _make_session_metadata()
    cap = TeamCommCapability(config, "worker", metadata)

    result = cap.get_instructions()

    assert result is None


@pytest.mark.unit
def test_skeleton_get_instructions_returns_none_when_no_metadata() -> None:
    """Given: enabled config + session_metadata=None.

    When: get_instructions() is called.
    Then: returns None (no session context to render template with).
    """
    config = _make_enabled_config()
    cap = TeamCommCapability(config, "worker", session_metadata=None)

    result = cap.get_instructions()

    assert result is None


@pytest.mark.unit
def test_skeleton_get_instructions_returns_none_when_empty_metadata() -> None:
    """Given: enabled config + empty session metadata dict.

    When: get_instructions() is called.
    Then: returns None.
    """
    config = _make_enabled_config()
    cap = TeamCommCapability(config, "worker", session_metadata={})

    result = cap.get_instructions()

    assert result is None


@pytest.mark.unit
async def test_skeleton_get_tools_returns_12_tools_when_enabled() -> None:
    """Given: enabled config with T7 universal tools + T8 lead-only tools.

    When: get_tools() is called.
    Then: returns 12 tools (send_message, task_create, task_list,
        task_update, read_blackboard, write_blackboard, list_blackboard,
        team_status, team_create, team_delete, delete_blackboard,
        shutdown_request).
    """
    config = _make_enabled_config()
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    result = await cap.get_tools()

    tool_names = {t.name for t in result}
    assert tool_names == {
        "send_message",
        "task_create",
        "task_list",
        "task_update",
        "read_blackboard",
        "write_blackboard",
        "list_blackboard",
        "team_status",
        "team_create",
        "team_delete",
        "delete_blackboard",
        "shutdown_request",
    }


@pytest.mark.unit
async def test_skeleton_get_tools_returns_empty_when_disabled() -> None:
    """Given: disabled config.

    When: get_tools() is called.
    Then: returns empty list.
    """
    config = _make_disabled_config()
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    result = await cap.get_tools()

    assert list(result) == []


@pytest.mark.unit
def test_skeleton_get_instructions_uses_agent_name_as_default_member() -> None:
    """Given: enabled config + metadata without team_member_name key.

    When: get_instructions() is called.
    Then: uses agent_name as the default member_name.
    """
    config = _make_enabled_config()
    metadata: dict[str, Any] = {"team_name": "beta", "team_role": "lead"}
    cap = TeamCommCapability(config, "coordinator", metadata)

    result = cap.get_instructions()

    assert result is not None
    assert "coordinator" in result
    assert "beta" in result
    assert "lead" in result


@pytest.mark.unit
def test_skeleton_get_instructions_uses_unknown_for_missing_keys() -> None:
    """Given: enabled config + metadata with only team_id.

    When: get_instructions() is called.
    Then: uses 'unknown' for missing team_name and team_role.
    """
    config = _make_enabled_config()
    metadata: dict[str, Any] = {"team_id": "t1"}
    cap = TeamCommCapability(config, "worker", metadata)

    result = cap.get_instructions()

    assert result is not None
    assert "unknown" in result


# ---- Registration tests ----


def _make_factory() -> Any:
    """Create a minimal AgentFactory for testing _compile_agent_capabilities.

    Returns an AgentFactory with a mock pool (the method under test
    does not access self._pool).
    """
    from agentpool.host.factory import AgentFactory

    mock_pool = MagicMock()
    return AgentFactory(mock_pool)


def _make_host_context(team_mode: Any) -> Any:
    """Create a mock HostContext with the given manifest.team_mode.

    Args:
        team_mode: Value to return for host_context.manifest.team_mode.

    Returns:
        A MagicMock configured with .manifest.team_mode and .skills_tools_provider=None.
    """
    mock = MagicMock()
    mock.manifest.team_mode = team_mode
    mock.skills_tools_provider = None
    return mock


def _make_native_config(team_mode: Any = None) -> Any:
    """Create a minimal NativeAgentConfig for testing.

    Args:
        team_mode: TeamModeConfig or None for the per-agent overlay.

    Returns:
        A NativeAgentConfig with model='openai:test' and no tools.
    """
    from agentpool.models.agents import NativeAgentConfig

    return NativeAgentConfig(
        name="test_agent",
        model="openai:test",
        tools=[],
        team_mode=team_mode,
    )


@pytest.mark.unit
def test_registration_team_comm_added_when_enabled_and_eligible() -> None:
    """Given: enabled global team_mode, agent in member_eligible.

    When: _compile_agent_capabilities() is called.
    Then: TeamCommCapability is present in the returned capability list.
    """
    config = _make_enabled_config(member_eligible=["test_agent"])
    factory = _make_factory()
    host_ctx = _make_host_context(team_mode=config)
    cfg = _make_native_config()

    caps = factory._compile_agent_capabilities("test_agent", cfg, host_ctx)

    team_caps = [c for c in caps if isinstance(c, TeamCommCapability)]
    assert len(team_caps) == 1
    assert team_caps[0]._agent_name == "test_agent"
    # Shared instance at compile time has no session metadata.
    assert team_caps[0]._session_metadata == {}


@pytest.mark.unit
def test_registration_no_team_comm_when_disabled() -> None:
    """Given: disabled global team_mode, agent in member_eligible.

    When: _compile_agent_capabilities() is called.
    Then: no TeamCommCapability in the returned list.
    """
    config = _make_disabled_config()
    factory = _make_factory()
    host_ctx = _make_host_context(team_mode=config)
    cfg = _make_native_config()

    caps = factory._compile_agent_capabilities("worker", cfg, host_ctx)

    team_caps = [c for c in caps if isinstance(c, TeamCommCapability)]
    assert len(team_caps) == 0


@pytest.mark.unit
def test_registration_no_team_comm_when_agent_not_eligible() -> None:
    """Given: enabled global team_mode, agent NOT in any eligible list.

    When: _compile_agent_capabilities() is called.
    Then: no TeamCommCapability in the returned list.
    """
    config = _make_enabled_config(
        member_eligible=["other_agent"],
        lead_eligible=["other_lead"],
    )
    factory = _make_factory()
    host_ctx = _make_host_context(team_mode=config)
    cfg = _make_native_config()

    caps = factory._compile_agent_capabilities("test_agent", cfg, host_ctx)

    team_caps = [c for c in caps if isinstance(c, TeamCommCapability)]
    assert len(team_caps) == 0


@pytest.mark.unit
def test_registration_team_comm_for_lead_eligible() -> None:
    """Given: enabled global team_mode, agent in lead_eligible.

    When: _compile_agent_capabilities() is called.
    Then: TeamCommCapability is present.
    """
    config = _make_enabled_config(lead_eligible=["coordinator"])
    factory = _make_factory()
    host_ctx = _make_host_context(team_mode=config)
    cfg = _make_native_config()

    caps = factory._compile_agent_capabilities("coordinator", cfg, host_ctx)

    team_caps = [c for c in caps if isinstance(c, TeamCommCapability)]
    assert len(team_caps) == 1


@pytest.mark.unit
def test_registration_no_team_comm_when_global_is_none() -> None:
    """Given: global team_mode is None, per-agent team_mode is None.

    When: _compile_agent_capabilities() is called.
    Then: no TeamCommCapability in the returned list.
    """
    factory = _make_factory()
    host_ctx = _make_host_context(team_mode=None)
    cfg = _make_native_config()

    caps = factory._compile_agent_capabilities("test_agent", cfg, host_ctx)

    team_caps = [c for c in caps if isinstance(c, TeamCommCapability)]
    assert len(team_caps) == 0


# ---- Per-session tests ----


@pytest.mark.unit
def test_per_session_get_instructions_renders_with_actual_metadata() -> None:
    """Given: enabled config + session metadata containing team_id.

    When: TeamCommCapability is constructed with session metadata.
    Then: get_instructions() renders the template with actual metadata values.
    """
    config = _make_enabled_config()
    metadata = _make_session_metadata()
    cap = TeamCommCapability(config, "worker", metadata)

    result = cap.get_instructions()

    assert result is not None
    assert "alpha_team" in result
    assert "translator" in result
    assert "translator_agent" in result


@pytest.mark.unit
def test_per_session_shared_instance_has_no_instructions() -> None:
    """Given: shared instance created at compile time (session_metadata=None).

    When: get_instructions() is called.
    Then: returns None (no session context yet).
    """
    config = _make_enabled_config()
    shared_cap = TeamCommCapability(config, "worker", session_metadata=None)

    result = shared_cap.get_instructions()

    assert result is None


@pytest.mark.unit
def test_per_session_replacement_provides_instructions() -> None:
    """Given: shared instance (no metadata) replaced by per-session instance.

    When: per-session instance's get_instructions() is called.
    Then: returns rendered instructions with actual team metadata.
    """
    config = _make_enabled_config()
    shared_cap = TeamCommCapability(config, "worker", session_metadata=None)
    assert shared_cap.get_instructions() is None

    # Simulate per-session replacement.
    metadata = _make_session_metadata()
    per_session_cap = TeamCommCapability(config, "worker", metadata)

    result = per_session_cap.get_instructions()

    assert result is not None
    assert "alpha_team" in result


# ---- T7 Universal tool tests ----


def _make_run_context(
    metadata: dict[str, Any] | None = None,
    session_pool: MagicMock | None = None,
    config: TeamModeConfig | None = None,
    base_dir: str | None = None,
    agent_registry: MagicMock | None = None,
    session_id: str | None = None,
    delegation: MagicMock | None = None,
) -> MagicMock:
    """Create a mock RunContext with AgentContext deps.

    Args:
        metadata: Session metadata dict (defaults to team session metadata).
        session_pool: Mock SessionPool (or None to test missing pool).
        config: TeamModeConfig (defaults to enabled config).
        base_dir: Optional base_dir override for TeamModeConfig.
        agent_registry: Mock AgentRegistry (defaults to a permissive mock).
        session_id: Optional session_id string for the mock SessionState.
        delegation: Mock DelegationService (defaults to a generic MagicMock).

    Returns:
        A MagicMock whose .deps is a mock AgentContext.
    """
    from agentpool.capabilities.agent_context import AgentContext

    cfg = config or _make_enabled_config(base_dir=base_dir)

    agent_ctx = MagicMock(spec=AgentContext)
    agent_ctx.session.metadata = metadata if metadata is not None else _make_session_metadata()
    agent_ctx.host.session_pool = session_pool
    agent_ctx.team_mode_config = cfg
    agent_ctx.agent_registry = agent_registry or MagicMock()
    agent_ctx.session.session_id = session_id or "lead_session_001"
    agent_ctx.delegation = delegation or MagicMock()

    ctx = MagicMock()
    ctx.deps = agent_ctx
    return ctx


def _make_lead_metadata(team_id: str = "team_123") -> dict[str, Any]:
    """Create session metadata for a lead agent."""
    return {
        "team_id": team_id,
        "team_name": "alpha_team",
        "team_role": "lead",
        "team_member_name": "coordinator",
    }


def _init_team(base_dir: str, team_id: str = "team_123") -> None:
    """Initialize a real FileTeamState with a team and members."""
    from agentpool.capabilities.file_team_state import FileTeamState

    state = FileTeamState(base_dir)
    state.init(
        team_id,
        "alpha_team",
        [
            {"name": "translator_agent", "agent": "worker"},
            {"name": "reviewer_agent", "agent": "reviewer"},
        ],
    )
    state.register_member(team_id, "translator_agent", "sess_translator")
    state.register_member(team_id, "reviewer_agent", "sess_reviewer")


@pytest.mark.unit
async def test_send_message_happy_path(tmp_path: Any) -> None:
    """Given: team session with registered members + mock session_pool.

    When: send_message is called with valid recipient.
    Then: returns "Message sent to {to}" and session_pool.send_message called.
    """
    _init_team(str(tmp_path))
    mock_pool = MagicMock()
    mock_pool.send_message = AsyncMock(return_value="msg_id_123")
    ctx = _make_run_context(session_pool=mock_pool, base_dir=str(tmp_path))
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    result = await cap.send_message(ctx, "reviewer_agent", "hello")

    assert result == "Message sent to reviewer_agent"
    mock_pool.send_message.assert_awaited_once()


@pytest.mark.unit
async def test_send_message_broadcast_returns_error() -> None:
    """Given: team session.

    When: send_message is called with to='*'.
    Then: returns "Broadcast is lead-only" error.
    """
    ctx = _make_run_context()
    cap = TeamCommCapability(_make_enabled_config(), "worker", _make_session_metadata())

    result = await cap.send_message(ctx, "*", "announcement")

    assert result == "Broadcast is lead-only"


@pytest.mark.unit
async def test_send_message_no_team_id() -> None:
    """Given: session metadata without team_id.

    When: send_message is called.
    Then: returns "Not in a team session".
    """
    ctx = _make_run_context(metadata={"team_name": "foo"})
    cap = TeamCommCapability(_make_enabled_config(), "worker", {"team_name": "foo"})

    result = await cap.send_message(ctx, "reviewer_agent", "hello")

    assert result == "Not in a team session"


@pytest.mark.unit
async def test_send_message_no_session_pool(tmp_path: Any) -> None:
    """Given: team session but session_pool is None.

    When: send_message is called.
    Then: returns "SessionPool not available".
    """
    _init_team(str(tmp_path))
    ctx = _make_run_context(session_pool=None, base_dir=str(tmp_path))
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    result = await cap.send_message(ctx, "reviewer_agent", "hello")

    assert result == "SessionPool not available"


@pytest.mark.unit
async def test_send_message_member_not_found(tmp_path: Any) -> None:
    """Given: team session but recipient not registered.

    When: send_message is called with unknown member.
    Then: returns error about member not found.
    """
    _init_team(str(tmp_path))
    mock_pool = MagicMock()
    ctx = _make_run_context(session_pool=mock_pool, base_dir=str(tmp_path))
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    result = await cap.send_message(ctx, "nonexistent", "hello")

    assert "not found" in result


@pytest.mark.unit
async def test_send_message_urgent_uses_steer(tmp_path: Any) -> None:
    """Given: team session, urgent=True.

    When: send_message is called.
    Then: session_pool.send_message called with DeliveryMode.STEER.
    """
    from agentpool.lifecycle.types import DeliveryMode

    _init_team(str(tmp_path))
    mock_pool = MagicMock()
    mock_pool.send_message = AsyncMock(return_value="msg_urgent")
    ctx = _make_run_context(session_pool=mock_pool, base_dir=str(tmp_path))
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    result = await cap.send_message(ctx, "reviewer_agent", "urgent msg", urgent=True)

    assert result == "Message sent to reviewer_agent"
    call_kwargs = mock_pool.send_message.call_args
    assert call_kwargs.kwargs["mode"] is DeliveryMode.STEER


# ---- T9 Bounds enforcement tests ----


@pytest.mark.unit
async def test_bounds_max_members_exceeded(tmp_path: Any) -> None:
    """Given: lead agent with 4 members but max_members=3.

    When: team_create is called with 4 members.
    Then: returns error about exceeding max_members.
    """
    config = _make_enabled_config(
        member_eligible=["worker", "reviewer", "editor", "writer"],
        base_dir=str(tmp_path),
    )
    config = config.model_copy(update={"bounds": TeamBounds(max_members=3)})
    mock_registry = MagicMock()
    mock_registry.exists = MagicMock(return_value=True)
    mock_pool = MagicMock()
    mock_pool.create_session = AsyncMock()
    mock_pool.send_message = AsyncMock(return_value="msg_id")
    ctx = _make_run_context(
        metadata=_make_lead_metadata(),
        session_pool=mock_pool,
        config=config,
        base_dir=str(tmp_path),
        agent_registry=mock_registry,
    )
    cap = TeamCommCapability(config, "coordinator", _make_lead_metadata())

    result = await cap.team_create(
        ctx,
        "big_team",
        [
            {"agent": "worker", "name": "m1"},
            {"agent": "reviewer", "name": "m2"},
            {"agent": "editor", "name": "m3"},
            {"agent": "writer", "name": "m4"},
        ],
    )

    assert "exceeds max_members" in result
    assert "4" in result
    assert "3" in result


@pytest.mark.unit
async def test_bounds_max_members_ok(tmp_path: Any) -> None:
    """Given: lead agent with 2 members and max_members=3.

    When: team_create is called with 2 members.
    Then: returns success message (within bounds).
    """
    config = _make_enabled_config(
        member_eligible=["worker", "reviewer"],
        base_dir=str(tmp_path),
    )
    config = config.model_copy(update={"bounds": TeamBounds(max_members=3)})
    mock_registry = MagicMock()
    mock_registry.exists = MagicMock(return_value=True)
    mock_pool = MagicMock()
    mock_pool.send_message = AsyncMock(return_value="msg_id")
    mock_delegation = MagicMock()
    mock_delegation.create_child_session = AsyncMock(return_value="child_session_001")
    ctx = _make_run_context(
        metadata=_make_lead_metadata(),
        session_pool=mock_pool,
        config=config,
        base_dir=str(tmp_path),
        agent_registry=mock_registry,
        delegation=mock_delegation,
    )
    cap = TeamCommCapability(config, "coordinator", _make_lead_metadata())

    result = await cap.team_create(
        ctx,
        "ok_team",
        [
            {"agent": "worker", "name": "translator_agent"},
            {"agent": "reviewer", "name": "reviewer_agent"},
        ],
    )

    assert "Team 'ok_team' created with 2 members" in result


@pytest.mark.unit
async def test_bounds_started_at_recorded(tmp_path: Any) -> None:
    """Given: lead agent successfully creates a team.

    When: team_create completes.
    Then: state.json contains a 'started_at' field with an ISO timestamp.
    """
    from agentpool.capabilities.file_team_state import FileTeamState

    config = _make_enabled_config(
        member_eligible=["worker", "reviewer"],
        base_dir=str(tmp_path),
    )
    mock_registry = MagicMock()
    mock_registry.exists = MagicMock(return_value=True)
    mock_pool = MagicMock()
    mock_pool.send_message = AsyncMock(return_value="msg_id")
    mock_delegation = MagicMock()
    mock_delegation.create_child_session = AsyncMock(return_value="child_session_001")
    ctx = _make_run_context(
        metadata=_make_lead_metadata(),
        session_pool=mock_pool,
        config=config,
        base_dir=str(tmp_path),
        agent_registry=mock_registry,
        delegation=mock_delegation,
    )
    cap = TeamCommCapability(config, "coordinator", _make_lead_metadata())

    result = await cap.team_create(
        ctx,
        "timed_team",
        [
            {"agent": "worker", "name": "translator_agent"},
            {"agent": "reviewer", "name": "reviewer_agent"},
        ],
    )

    assert "team_id=" in result
    team_id = result.split("team_id=")[1].strip()
    state = FileTeamState._read_json(FileTeamState(str(tmp_path))._state_path(team_id))
    assert "started_at" in state
    assert state["started_at"] is not None
    import datetime

    datetime.datetime.fromisoformat(state["started_at"])


@pytest.mark.unit
async def test_bounds_inbox_max_bytes_exceeded(tmp_path: Any) -> None:
    """Given: team session with inbox_max_bytes set very small.

    When: send_message is called with a body that would exceed the inbox limit.
    Then: returns error about inbox exceeding max size.
    """
    _init_team(str(tmp_path))
    config = _make_enabled_config(base_dir=str(tmp_path))
    config = config.model_copy(update={"inbox_max_bytes": 50})
    mock_pool = MagicMock()
    mock_pool.send_message = AsyncMock(return_value="msg_id")
    ctx = _make_run_context(
        session_pool=mock_pool,
        config=config,
        base_dir=str(tmp_path),
    )
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    # First message should succeed (inbox is empty, body is small).
    result1 = await cap.send_message(ctx, "reviewer_agent", "hi")
    assert result1 == "Message sent to reviewer_agent"

    # Second message with a large body should exceed the inbox limit.
    big_body = "x" * 100
    result2 = await cap.send_message(ctx, "reviewer_agent", big_body)

    assert "Inbox exceeds max size" in result2


@pytest.mark.unit
async def test_bounds_max_member_turns_exceeded(tmp_path: Any) -> None:
    """Given: team session where recipient has reached max_member_turns.

    When: send_message is called for that recipient.
    Then: returns error about member exceeding max turns.
    """
    _init_team(str(tmp_path))
    config = _make_enabled_config(base_dir=str(tmp_path))
    config = config.model_copy(update={"bounds": TeamBounds(max_member_turns=2)})
    mock_pool = MagicMock()
    mock_pool.send_message = AsyncMock(return_value="msg_id")
    ctx = _make_run_context(
        session_pool=mock_pool,
        config=config,
        base_dir=str(tmp_path),
    )
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    # Send 2 messages (should succeed, turn_count goes 0->1, 1->2).
    result1 = await cap.send_message(ctx, "reviewer_agent", "msg1")
    assert result1 == "Message sent to reviewer_agent"
    result2 = await cap.send_message(ctx, "reviewer_agent", "msg2")
    assert result2 == "Message sent to reviewer_agent"

    # Third message should be rejected (turn_count=2 >= max_member_turns=2).
    result3 = await cap.send_message(ctx, "reviewer_agent", "msg3")
    assert "exceeded max turns" in result3
    assert "2" in result3


@pytest.mark.unit
async def test_bounds_blackboard_max_size_exceeded(tmp_path: Any) -> None:
    """Given: team session with max_size_mb=1 (minimum allowed).

    When: write_blackboard is called with > 1MB of data.
    Then: returns error about blackboard exceeding max size.
    """
    _init_team(str(tmp_path))
    config = _make_enabled_config(base_dir=str(tmp_path))
    config = config.model_copy(
        update={"blackboard": config.blackboard.model_copy(update={"max_size_mb": 1})}
    )
    ctx = _make_run_context(
        config=config,
        base_dir=str(tmp_path),
    )
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    # Write > 1MB of data.
    big_value = "x" * (1024 * 1024 + 1)
    result = await cap.write_blackboard(ctx, "big_key", big_value)

    assert "Blackboard write exceeds max size" in result
    assert "MB" in result


@pytest.mark.unit
async def test_task_create_happy_path(tmp_path: Any) -> None:
    """Given: team session with initialized state.

    When: task_create is called with subject and description.
    Then: returns "Task created: {task_id}" and task is persisted.
    """
    _init_team(str(tmp_path))
    ctx = _make_run_context(base_dir=str(tmp_path))
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    result = await cap.task_create(ctx, "Translate docs", "Translate API docs to French")

    assert result.startswith("Task created: ")


@pytest.mark.unit
async def test_task_create_no_team_id() -> None:
    """Given: session metadata without team_id.

    When: task_create is called.
    Then: returns "Not in a team session".
    """
    ctx = _make_run_context(metadata={"team_name": "foo"})
    cap = TeamCommCapability(_make_enabled_config(), "worker", {"team_name": "foo"})

    result = await cap.task_create(ctx, "Task")

    assert result == "Not in a team session"


@pytest.mark.unit
async def test_task_list_returns_tasks(tmp_path: Any) -> None:
    """Given: team session with existing tasks.

    When: task_list is called.
    Then: returns JSON array with at least one task.
    """
    _init_team(str(tmp_path))
    ctx = _make_run_context(base_dir=str(tmp_path))
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    await cap.task_create(ctx, "Task A")
    await cap.task_create(ctx, "Task B")

    result = await cap.task_list(ctx)
    import json

    tasks = json.loads(result)
    assert len(tasks) == 2
    subjects = {t["subject"] for t in tasks}
    assert subjects == {"Task A", "Task B"}


@pytest.mark.unit
async def test_task_update_changes_status(tmp_path: Any) -> None:
    """Given: team session with an existing task.

    When: task_update is called with status="completed".
    Then: returns updated task JSON with status="completed".
    """
    _init_team(str(tmp_path))
    ctx = _make_run_context(base_dir=str(tmp_path))
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    create_result = await cap.task_create(ctx, "Task X")
    task_id = create_result.replace("Task created: ", "")

    update_result = await cap.task_update(ctx, task_id, status="completed")
    import json

    updated = json.loads(update_result)
    assert updated["status"] == "completed"


@pytest.mark.unit
async def test_task_update_no_updates_specified(tmp_path: Any) -> None:
    """Given: team session.

    When: task_update called with empty status and owner.
    Then: returns "No updates specified".
    """
    _init_team(str(tmp_path))
    ctx = _make_run_context(base_dir=str(tmp_path))
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    result = await cap.task_update(ctx, "some_id")

    assert result == "No updates specified"


@pytest.mark.unit
async def test_read_blackboard_returns_value(tmp_path: Any) -> None:
    """Given: team session with a blackboard key written.

    When: read_blackboard is called.
    Then: returns JSON with the value and version.
    """
    _init_team(str(tmp_path))
    ctx = _make_run_context(base_dir=str(tmp_path))
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    await cap.write_blackboard(ctx, "config", "value1")
    result = await cap.read_blackboard(ctx, "config")
    import json

    data = json.loads(result)
    assert data["value"]["text"] == "value1"
    assert data["version"] == 1


@pytest.mark.unit
async def test_read_blackboard_key_not_found(tmp_path: Any) -> None:
    """Given: team session with empty blackboard.

    When: read_blackboard is called with unknown key.
    Then: returns "Key not found".
    """
    _init_team(str(tmp_path))
    ctx = _make_run_context(base_dir=str(tmp_path))
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    result = await cap.read_blackboard(ctx, "nonexistent")

    assert result == "Key not found"


@pytest.mark.unit
async def test_write_blackboard_returns_version(tmp_path: Any) -> None:
    """Given: team session.

    When: write_blackboard is called with a new key.
    Then: returns "Written, version=1".
    """
    _init_team(str(tmp_path))
    ctx = _make_run_context(base_dir=str(tmp_path))
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    result = await cap.write_blackboard(ctx, "key1", "val1")

    assert result == "Written, version=1"


@pytest.mark.unit
async def test_write_blackboard_conflict(tmp_path: Any) -> None:
    """Given: team session with existing blackboard key at version 1.

    When: write_blackboard called with expected_version=0 (wrong).
    Then: returns "Conflict: current version is 1".
    """
    _init_team(str(tmp_path))
    ctx = _make_run_context(base_dir=str(tmp_path))
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    await cap.write_blackboard(ctx, "key1", "val1")
    result = await cap.write_blackboard(ctx, "key1", "val2", expected_version=0)

    assert result == "Conflict: current version is 1"


@pytest.mark.unit
async def test_list_blackboard_returns_keys(tmp_path: Any) -> None:
    """Given: team session with multiple blackboard keys.

    When: list_blackboard is called.
    Then: returns JSON array of sorted key names.
    """
    _init_team(str(tmp_path))
    ctx = _make_run_context(base_dir=str(tmp_path))
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    await cap.write_blackboard(ctx, "zebra", "z")
    await cap.write_blackboard(ctx, "alpha", "a")

    result = await cap.list_blackboard(ctx)
    import json

    keys = json.loads(result)
    assert keys == ["alpha", "zebra"]


@pytest.mark.unit
async def test_team_status_returns_formatted_string(tmp_path: Any) -> None:
    """Given: team session with initialized state and members.

    When: team_status is called.
    Then: returns formatted string with team name, status, and members.
    """
    _init_team(str(tmp_path))
    ctx = _make_run_context(base_dir=str(tmp_path))
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    result = await cap.team_status(ctx)

    assert "alpha_team" in result
    assert "active" in result
    assert "translator_agent" in result
    assert "reviewer_agent" in result


@pytest.mark.unit
async def test_team_status_no_team_id() -> None:
    """Given: session metadata without team_id.

    When: team_status is called.
    Then: returns "Not in a team session".
    """
    ctx = _make_run_context(metadata={"team_name": "foo"})
    cap = TeamCommCapability(_make_enabled_config(), "worker", {"team_name": "foo"})

    result = await cap.team_status(ctx)

    assert result == "Not in a team session"


@pytest.mark.unit
async def test_disabled_config_registers_no_tools() -> None:
    """Given: disabled config.

    When: TeamCommCapability is constructed.
    Then: no tools are registered and get_tools() returns empty list.
    """
    config = _make_disabled_config()
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    result = await cap.get_tools()

    assert list(result) == []


# ---- T8 Lead-only tool tests ----


@pytest.mark.unit
async def test_team_create_success(tmp_path: Any) -> None:
    """Given: lead agent with eligible members and mock delegation service.

    When: team_create is called with 2 eligible members.
    Then: returns success message with team_id and creates child sessions.
    """
    config = _make_enabled_config(
        member_eligible=["worker", "reviewer"],
        base_dir=str(tmp_path),
    )
    mock_registry = MagicMock()
    mock_registry.exists = MagicMock(return_value=True)
    mock_pool = MagicMock()
    mock_pool.send_message = AsyncMock(return_value="msg_id")
    mock_delegation = MagicMock()
    mock_delegation.create_child_session = AsyncMock(return_value="child_session_001")
    ctx = _make_run_context(
        metadata=_make_lead_metadata(),
        session_pool=mock_pool,
        config=config,
        base_dir=str(tmp_path),
        agent_registry=mock_registry,
        delegation=mock_delegation,
    )
    cap = TeamCommCapability(config, "coordinator", _make_lead_metadata())

    result = await cap.team_create(
        ctx,
        "my_team",
        [
            {"agent": "worker", "name": "translator_agent"},
            {"agent": "reviewer", "name": "reviewer_agent"},
        ],
    )

    assert "Team 'my_team' created with 2 members" in result
    assert "team_id=" in result
    assert mock_delegation.create_child_session.await_count == 2
    assert mock_pool.send_message.await_count == 2


@pytest.mark.unit
async def test_team_create_not_lead() -> None:
    """Given: non-lead agent (team_role='translator').

    When: team_create is called.
    Then: returns "Only lead can use team_create".
    """
    ctx = _make_run_context()
    cap = TeamCommCapability(_make_enabled_config(), "worker", _make_session_metadata())

    result = await cap.team_create(ctx, "test", [])

    assert result == "Only lead can use team_create"


@pytest.mark.unit
async def test_team_create_agent_not_in_registry(tmp_path: Any) -> None:
    """Given: lead agent but member agent not in registry.

    When: team_create is called.
    Then: returns "Agent '{name}' not found in registry".
    """
    config = _make_enabled_config(
        member_eligible=["ghost"],
        base_dir=str(tmp_path),
    )
    mock_registry = MagicMock()
    mock_registry.exists = MagicMock(return_value=False)
    ctx = _make_run_context(
        metadata=_make_lead_metadata(),
        config=config,
        base_dir=str(tmp_path),
        agent_registry=mock_registry,
    )
    cap = TeamCommCapability(config, "coordinator", _make_lead_metadata())

    result = await cap.team_create(
        ctx,
        "test_team",
        [{"agent": "ghost", "name": "ghost_member"}],
    )

    assert "not found in registry" in result


@pytest.mark.unit
async def test_team_create_agent_not_eligible(tmp_path: Any) -> None:
    """Given: lead agent, agent exists in registry but not in member_eligible.

    When: team_create is called.
    Then: returns "Agent '{name}' is not eligible for team membership".
    """
    config = _make_enabled_config(
        member_eligible=["worker"],
        base_dir=str(tmp_path),
    )
    mock_registry = MagicMock()
    mock_registry.exists = MagicMock(return_value=True)
    ctx = _make_run_context(
        metadata=_make_lead_metadata(),
        config=config,
        base_dir=str(tmp_path),
        agent_registry=mock_registry,
    )
    cap = TeamCommCapability(config, "coordinator", _make_lead_metadata())

    result = await cap.team_create(
        ctx,
        "test_team",
        [{"agent": "non_eligible", "name": "member1"}],
    )

    assert "not eligible for team membership" in result


@pytest.mark.unit
async def test_team_delete_success(tmp_path: Any) -> None:
    """Given: lead agent with initialized team.

    When: team_delete is called.
    Then: closes all member sessions and returns "Team deleted".
    """
    _init_team(str(tmp_path))
    mock_pool = MagicMock()
    mock_pool.close_session = AsyncMock()
    ctx = _make_run_context(
        metadata=_make_lead_metadata(),
        session_pool=mock_pool,
        base_dir=str(tmp_path),
    )
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "coordinator", _make_lead_metadata())

    result = await cap.team_delete(ctx)

    assert result == "Team deleted"
    # Two members registered in _init_team.
    assert mock_pool.close_session.await_count == 2


@pytest.mark.unit
async def test_team_delete_not_lead() -> None:
    """Given: non-lead agent (team_role='translator').

    When: team_delete is called.
    Then: returns "Only lead can use team_delete".
    """
    ctx = _make_run_context()
    cap = TeamCommCapability(_make_enabled_config(), "worker", _make_session_metadata())

    result = await cap.team_delete(ctx)

    assert result == "Only lead can use team_delete"


@pytest.mark.unit
async def test_shutdown_request_success(tmp_path: Any) -> None:
    """Given: lead agent with initialized team.

    When: shutdown_request is called with a valid member name.
    Then: closes the member's session and returns success.
    """
    _init_team(str(tmp_path))
    mock_pool = MagicMock()
    mock_pool.close_session = AsyncMock()
    ctx = _make_run_context(
        metadata=_make_lead_metadata(),
        session_pool=mock_pool,
        base_dir=str(tmp_path),
    )
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "coordinator", _make_lead_metadata())

    result = await cap.shutdown_request(ctx, "translator_agent")

    assert result == "Shutdown completed for translator_agent"
    mock_pool.close_session.assert_awaited_once_with("sess_translator")


@pytest.mark.unit
async def test_shutdown_request_not_lead() -> None:
    """Given: non-lead agent (team_role='translator').

    When: shutdown_request is called.
    Then: returns "Only lead can use shutdown_request".
    """
    ctx = _make_run_context()
    cap = TeamCommCapability(_make_enabled_config(), "worker", _make_session_metadata())

    result = await cap.shutdown_request(ctx, "some_member")

    assert result == "Only lead can use shutdown_request"


@pytest.mark.unit
async def test_delete_blackboard_success(tmp_path: Any) -> None:
    """Given: lead agent with a blackboard key written.

    When: delete_blackboard is called.
    Then: key is removed and returns "Blackboard key '{key}' deleted".
    """
    _init_team(str(tmp_path))
    ctx = _make_run_context(
        metadata=_make_lead_metadata(),
        base_dir=str(tmp_path),
    )
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "coordinator", _make_lead_metadata())

    # Write a key first so we can delete it.
    await cap.write_blackboard(ctx, "test_key", "test_value")
    result = await cap.delete_blackboard(ctx, "test_key")

    assert result == "Blackboard key 'test_key' deleted"
    # Verify it's gone.
    read_result = await cap.read_blackboard(ctx, "test_key")
    assert read_result == "Key not found"


@pytest.mark.unit
async def test_delete_blackboard_not_lead() -> None:
    """Given: non-lead agent (team_role='translator').

    When: delete_blackboard is called.
    Then: returns "Only lead can use delete_blackboard".
    """
    ctx = _make_run_context()
    cap = TeamCommCapability(_make_enabled_config(), "worker", _make_session_metadata())

    result = await cap.delete_blackboard(ctx, "some_key")

    assert result == "Only lead can use delete_blackboard"


@pytest.mark.unit
async def test_broadcast_lead(tmp_path: Any) -> None:
    """Given: lead agent sends broadcast (to='*').

    When: send_message is called with to='*'.
    Then: all members receive the message.
    """
    _init_team(str(tmp_path))
    mock_pool = MagicMock()
    mock_pool.send_message = AsyncMock(return_value="msg_id")
    ctx = _make_run_context(
        metadata=_make_lead_metadata(),
        session_pool=mock_pool,
        base_dir=str(tmp_path),
    )
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "coordinator", _make_lead_metadata())

    result = await cap.send_message(ctx, "*", "announcement")

    assert "Broadcast sent to 2 members" in result
    assert mock_pool.send_message.await_count == 2


@pytest.mark.unit
async def test_broadcast_not_lead() -> None:
    """Given: non-lead agent (team_role='translator') sends broadcast.

    When: send_message is called with to='*'.
    Then: returns "Broadcast is lead-only".
    """
    ctx = _make_run_context()
    cap = TeamCommCapability(_make_enabled_config(), "worker", _make_session_metadata())

    result = await cap.send_message(ctx, "*", "announcement")

    assert result == "Broadcast is lead-only"


@pytest.mark.unit
async def test_message_size_exceeds_limit() -> None:
    """Given: message body exceeding message_max_bytes.

    When: send_message is called.
    Then: returns error about message exceeding max size.
    """
    config = _make_enabled_config()
    config = config.model_copy(update={"message_max_bytes": 10})
    ctx = _make_run_context(config=config)
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    big_body = "x" * 100
    result = await cap.send_message(ctx, "reviewer_agent", big_body)

    assert "exceeds max size" in result
    assert "100" in result
    assert "10" in result


@pytest.mark.unit
async def test_auto_urgent(tmp_path: Any) -> None:
    """Given: message_type in auto_urgent list.

    When: send_message is called with message_type='escalation'.
    Then: urgent is forced to True and DeliveryMode.STEER is used.
    """
    from agentpool.lifecycle.types import DeliveryMode

    _init_team(str(tmp_path))
    mock_pool = MagicMock()
    mock_pool.send_message = AsyncMock(return_value="msg_urgent")
    ctx = _make_run_context(
        session_pool=mock_pool,
        base_dir=str(tmp_path),
    )
    config = _make_enabled_config(base_dir=str(tmp_path))
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    result = await cap.send_message(
        ctx,
        "reviewer_agent",
        "urgent escalation",
        message_type="escalation",
    )

    assert result == "Message sent to reviewer_agent"
    call_kwargs = mock_pool.send_message.call_args
    assert call_kwargs.kwargs["mode"] is DeliveryMode.STEER


# ---- Config default members tests ----


@pytest.mark.unit
async def test_team_create_uses_config_default_members(tmp_path: Any) -> None:
    """Given: lead agent with auto_init config, team_create called with empty members.

    When: team_create is called with members=[].
    Then: uses auto_init.members from config to create the team.
    """
    from agentpool_config.team_mode import AutoInitConfig, MemberSpec

    config = _make_enabled_config(
        member_eligible=["translator", "reviewer"],
        base_dir=str(tmp_path),
    ).model_copy(
        update={
            "auto_init": AutoInitConfig(
                team_name="default_team",
                members=[
                    MemberSpec(name="translator", agent="translator"),
                    MemberSpec(name="reviewer", agent="reviewer"),
                ],
            )
        }
    )
    mock_registry = MagicMock()
    mock_registry.exists = MagicMock(return_value=True)
    mock_pool = MagicMock()
    mock_pool.send_message = AsyncMock(return_value="msg_id")
    mock_delegation = MagicMock()
    mock_delegation.create_child_session = AsyncMock(return_value="child_session_001")
    ctx = _make_run_context(
        metadata=_make_lead_metadata(),
        session_pool=mock_pool,
        config=config,
        base_dir=str(tmp_path),
        agent_registry=mock_registry,
        delegation=mock_delegation,
    )
    cap = TeamCommCapability(config, "coordinator", _make_lead_metadata())

    result = await cap.team_create(ctx, "my_team", [])

    assert "Team 'my_team' created with 2 members" in result
    assert mock_delegation.create_child_session.await_count == 2
    assert mock_pool.send_message.await_count == 2


@pytest.mark.unit
async def test_team_create_empty_members_no_auto_init(tmp_path: Any) -> None:
    """Given: lead agent with auto_init=None, team_create called with empty members.

    When: team_create is called with members=[].
    Then: creates team with 0 members (no crash, no auto_init fallback).
    """
    config = _make_enabled_config(
        member_eligible=["worker"],
        base_dir=str(tmp_path),
    )
    mock_registry = MagicMock()
    mock_registry.exists = MagicMock(return_value=True)
    mock_pool = MagicMock()
    mock_pool.send_message = AsyncMock(return_value="msg_id")
    mock_delegation = MagicMock()
    mock_delegation.create_child_session = AsyncMock(return_value="child_001")
    ctx = _make_run_context(
        metadata=_make_lead_metadata(),
        session_pool=mock_pool,
        config=config,
        base_dir=str(tmp_path),
        agent_registry=mock_registry,
        delegation=mock_delegation,
    )
    cap = TeamCommCapability(config, "coordinator", _make_lead_metadata())

    result = await cap.team_create(ctx, "empty_team", [])

    assert "Team 'empty_team' created with 0 members" in result
    assert mock_delegation.create_child_session.await_count == 0
