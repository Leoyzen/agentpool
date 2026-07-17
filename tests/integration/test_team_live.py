"""L4 live model tests for the dynamic team mode feature.

These tests exercise the full ``AgentPool`` + ``SessionPool`` stack against
real model API endpoints.  They are **skipped by default** and only run when
the ``--run-real-models`` pytest flag is passed, ensuring CI does not incur
API costs.

Run manually::

    uv run pytest tests/integration/test_team_live.py -v --run-real-models

Requires one of ``OPENAI_API_KEY`` or ``MODEL_GATEWAY_URL`` environment
variables to be set.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import pytest


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


# ---------------------------------------------------------------------------
# pytest CLI option + fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def run_real_models(request: pytest.FixtureRequest) -> None:
    """Skip tests unless ``--run-real-models`` is passed and credentials exist.

    Returns:
        None — the fixture's sole purpose is to gate test execution.
    """
    if not request.config.getoption("--run-real-models"):
        pytest.skip("Needs --run-real-models flag to run")
    if not os.environ.get("OPENAI_API_KEY") and not os.environ.get("MODEL_GATEWAY_URL"):
        pytest.skip("Needs OPENAI_API_KEY or MODEL_GATEWAY_URL environment variable")


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------

_LIVE_CONFIG_YAML = """\
agents:
  team_lead:
    type: native
    model: openai:svc/kimi-k2
    system_prompt: |
      You are a team lead. Use team tools to coordinate.
      When asked to test team mode, follow the instructions exactly and call
      the requested tools in order.
    team_mode:
      enabled: true
      lead_eligible:
        - team_lead
      member_eligible:
        - team_lead
        - team_member
  team_member:
    type: native
    model: openai:svc/kimi-k2
    system_prompt: |
      You are a team member. Follow instructions from the lead.

team_mode:
  enabled: true
  lead_eligible:
    - team_lead
  member_eligible:
    - team_lead
    - team_member
"""


def _create_test_config(tmp_path: Path) -> Path:
    """Write the test team-mode config YAML to ``tmp_path``.

    Args:
        tmp_path: pytest-provided temporary directory.

    Returns:
        Path to the written YAML config file.
    """
    config_path = tmp_path / "test-team-live.yaml"
    config_path.write_text(_LIVE_CONFIG_YAML)
    return config_path


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------


def _collect_tool_names(events: list[Any]) -> list[str]:
    """Extract tool names from ``ToolCallStartEvent`` entries.

    Args:
        events: Collected stream events.

    Returns:
        Ordered list of tool names that were invoked during the run.
    """
    from agentpool.agents.events.events import ToolCallStartEvent

    return [e.tool_name for e in events if isinstance(e, ToolCallStartEvent)]


def _collect_text(events: list[Any]) -> str:
    """Concatenate all text deltas from ``PartDeltaEvent`` entries.

    Args:
        events: Collected stream events.

    Returns:
        Full text response from the model.
    """
    from pydantic_ai import TextPartDelta, ThinkingPartDelta

    from agentpool.agents.events.events import PartDeltaEvent

    parts: list[str] = []
    for e in events:
        if isinstance(e, PartDeltaEvent):
            match e.delta:
                case TextPartDelta(content_delta=str(text)):
                    parts.append(text)
                case ThinkingPartDelta(content_delta=str(text)):
                    parts.append(text)
                case _:
                    pass
    return "".join(parts)


async def _drain_events(
    stream: AsyncIterator[Any],
) -> list[Any]:
    """Collect all events from an async iterator into a list.

    Args:
        stream: An async iterator of stream events.

    Returns:
        List of all events yielded by the stream.
    """
    events: list[Any] = [event async for event in stream]
    return events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_LIFECYCLE_PROMPT = """\
You are testing team mode functionality. Please execute these steps IN ORDER:
1. Call team_create to create a team named "test_team" with one member: \
name="assistant", agent="team_member"
2. Call team_status to check the team status
3. Call task_create with subject="Test task" and description="This is a test"
4. Call task_list to see the task
5. Call write_blackboard with key="test_key" and value="test_value"
6. Call read_blackboard with key="test_key" to verify
7. Call team_delete to clean up
8. Respond with "ALL TESTS PASSED" when done.
"""

_REQUIRED_TOOLS = {
    "team_create",
    "team_status",
    "task_create",
    "task_list",
    "write_blackboard",
    "read_blackboard",
    "team_delete",
}


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.run_real_models
async def test_live_full_team_lifecycle(
    run_real_models: None,
    tmp_path: Path,
) -> None:
    """Given: a live AgentPool with team mode enabled.

    When: the lead agent is asked to create a team, check status, create and
        list tasks, write and read the blackboard, then delete the team.

    Then: all seven team tools are invoked and the final response contains
        "ALL TESTS PASSED".
    """
    from agentpool.delegation.pool import AgentPool

    config_path = _create_test_config(tmp_path)

    async with AgentPool(str(config_path)) as pool:
        session_id = "test-lead-lifecycle"
        await pool.session_pool.create_session(
            session_id,
            agent_name="team_lead",
        )

        events = await _drain_events(
            pool.session_pool.run_stream(session_id, _LIFECYCLE_PROMPT),
        )

    tool_names = _collect_tool_names(events)
    invoked = set(tool_names)
    missing = _REQUIRED_TOOLS - invoked
    assert not missing, f"Missing tool calls: {missing}. Invoked: {tool_names}"

    text = _collect_text(events)
    assert "ALL TESTS PASSED" in text, (
        f"Expected 'ALL TESTS PASSED' in response, got: {text[-500:]}"
    )


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.run_real_models
async def test_live_member_session_cleanup(
    run_real_models: None,
    tmp_path: Path,
) -> None:
    """Given: a live AgentPool with team mode enabled.

    When: the lead agent creates a team with one member, then deletes the
        team.

    Then: after the run completes, no active child sessions with
        ``parent_session_id`` matching the lead session remain.
    """
    from agentpool.delegation.pool import AgentPool

    config_path = _create_test_config(tmp_path)

    async with AgentPool(str(config_path)) as pool:
        session_id = "test-lead-cleanup"
        await pool.session_pool.create_session(
            session_id,
            agent_name="team_lead",
        )

        prompt = (
            'Create a team with one member (name="assistant", '
            'agent="team_member"), then call team_delete to clean up. '
            'Respond with "DONE" when finished.'
        )

        await _drain_events(
            pool.session_pool.run_stream(session_id, prompt),
        )

        # Inspect session controller for orphaned child sessions.
        controller = pool.session_pool.sessions
        child_sessions = [
            s
            for s in controller._sessions.values()
            if s.parent_session_id == session_id and not s.is_closing and s.closed_at is None
        ]

    assert not child_sessions, (
        f"Found {len(child_sessions)} active child session(s) after team_delete: "
        f"{[s.session_id for s in child_sessions]}"
    )


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.run_real_models
async def test_live_team_status_reflects_members(
    run_real_models: None,
    tmp_path: Path,
) -> None:
    """Given: a live AgentPool with team mode enabled.

    When: the lead agent creates a team with two members and checks status.

    Then: the ``team_create`` and ``team_status`` tools are invoked, and the
        response text mentions both member names.
    """
    from agentpool.delegation.pool import AgentPool

    config_path = _create_test_config(tmp_path)

    async with AgentPool(str(config_path)) as pool:
        session_id = "test-lead-status"
        await pool.session_pool.create_session(
            session_id,
            agent_name="team_lead",
        )

        prompt = (
            "Execute these steps in order:\n"
            '1. Call team_create to create a team named "status_team" with two '
            'members: name="alpha", agent="team_member" and '
            'name="beta", agent="team_member"\n'
            "2. Call team_status to check the team status\n"
            "3. Call team_delete to clean up\n"
            '4. Respond with "STATUS_CHECK_DONE" when finished.'
        )

        events = await _drain_events(
            pool.session_pool.run_stream(session_id, prompt),
        )

    tool_names = _collect_tool_names(events)
    assert "team_create" in tool_names, f"team_create not called. Tools: {tool_names}"
    assert "team_status" in tool_names, f"team_status not called. Tools: {tool_names}"
    assert "team_delete" in tool_names, f"team_delete not called. Tools: {tool_names}"

    text = _collect_text(events)
    assert "STATUS_CHECK_DONE" in text, (
        f"Expected 'STATUS_CHECK_DONE' in response, got: {text[-500:]}"
    )


# ---------------------------------------------------------------------------
# Smoke test (no real model needed)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_config_yaml_writes_successfully(tmp_path: Path) -> None:
    """Given: the live test config template.

    When: ``_create_test_config`` writes it to ``tmp_path``.

    Then: the file exists and contains the expected agent names.
    """
    config_path = _create_test_config(tmp_path)
    assert config_path.exists()
    content = config_path.read_text()
    assert "team_lead" in content
    assert "team_member" in content
    assert "team_mode:" in content
