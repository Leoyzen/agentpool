"""Tests for TeamCommCapability skeleton, registration, and per-session."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from agentpool.capabilities.team_comm_capability import TeamCommCapability
from agentpool_config.team_mode import TeamModeConfig


# ---- Helpers ----


def _make_enabled_config(
    *,
    member_eligible: list[str] | None = None,
    lead_eligible: list[str] | None = None,
    protocol_template: str | None = None,
) -> TeamModeConfig:
    """Create an enabled TeamModeConfig for testing.

    Args:
        member_eligible: Agent names eligible as members.
        lead_eligible: Agent names eligible as leads.
        protocol_template: Custom protocol template string.

    Returns:
        A frozen TeamModeConfig with enabled=True.
    """
    return TeamModeConfig(
        enabled=True,
        member_eligible=member_eligible or ["worker"],
        lead_eligible=lead_eligible or ["coordinator"],
        protocol_template=protocol_template
        or "Team={team_name}, Role={role}, Member={member_name}",
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
async def test_skeleton_get_tools_returns_empty_when_enabled() -> None:
    """Given: enabled config with no registered tools.

    When: get_tools() is called.
    Then: returns empty list (skeleton stage — T7/T8 will add tools).
    """
    config = _make_enabled_config()
    cap = TeamCommCapability(config, "worker", _make_session_metadata())

    result = await cap.get_tools()

    assert list(result) == []


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
