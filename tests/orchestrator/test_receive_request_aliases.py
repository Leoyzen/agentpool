"""Tests for priority alias mapping in SessionController.receive_request().

Verifies that ``"steer"`` routes identically to ``"asap"`` and
``"followup"`` routes identically to ``"when_idle"``, and that
the original values still work for backward compatibility.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.orchestrator.core import SessionController


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return a mocked AgentPool with a main_agent."""
    pool = MagicMock()
    pool.main_agent = MagicMock()
    pool.main_agent.name = "main-agent"
    pool.manifest = MagicMock()
    pool.manifest.agents = {}
    return pool


@pytest.fixture
def controller(mock_pool: MagicMock) -> SessionController:
    """Return a SessionController backed by the mock pool."""
    return SessionController(pool=mock_pool)


@pytest.fixture
def mock_turn_runner() -> MagicMock:
    """Return a mocked TurnRunner with inject_prompt and queue_prompt."""
    tr = MagicMock()
    tr.inject_prompt = AsyncMock(return_value=True)
    tr.queue_prompt = AsyncMock(return_value=False)
    return tr


# ---------------------------------------------------------------------------
# Alias: steer  →  asap  →  inject_prompt
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_receive_request_steer_routes_to_inject_prompt(
    controller: SessionController,
    mock_turn_runner: MagicMock,
) -> None:
    """``priority="steer"`` routes identically to ``priority="asap"``."""
    controller._turn_runner = mock_turn_runner
    await controller.get_or_create_session("sess-1", agent_name="agent-a")

    session = controller.get_session("sess-1")
    assert session is not None
    session.current_run_id = "existing-run-id"

    await controller.receive_request("sess-1", "urgent", priority="steer")
    await controller.receive_request("sess-1", "urgent", priority="asap")

    # Both steer and asap should route to inject_prompt
    assert mock_turn_runner.inject_prompt.await_count == 2
    mock_turn_runner.queue_prompt.assert_not_awaited()


# ---------------------------------------------------------------------------
# Alias: followup  →  when_idle  →  queue_prompt
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_receive_request_followup_routes_to_queue_prompt(
    controller: SessionController,
    mock_turn_runner: MagicMock,
) -> None:
    """``priority="followup"`` routes identically to ``priority="when_idle"``."""
    controller._turn_runner = mock_turn_runner
    await controller.get_or_create_session("sess-1", agent_name="agent-a")

    session = controller.get_session("sess-1")
    assert session is not None
    session.current_run_id = "existing-run-id"

    await controller.receive_request("sess-1", "later", priority="followup")
    await controller.receive_request("sess-1", "later", priority="when_idle")

    # Both followup and when_idle should route to queue_prompt
    assert mock_turn_runner.queue_prompt.await_count == 2
    mock_turn_runner.inject_prompt.assert_not_awaited()


# ---------------------------------------------------------------------------
# Backward compatibility: asap  →  inject_prompt
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_receive_request_asap_still_routes_to_inject_prompt(
    controller: SessionController,
    mock_turn_runner: MagicMock,
) -> None:
    """``priority="asap"`` still works (backward compat)."""
    controller._turn_runner = mock_turn_runner
    await controller.get_or_create_session("sess-1", agent_name="agent-a")

    session = controller.get_session("sess-1")
    assert session is not None
    session.current_run_id = "existing-run-id"

    await controller.receive_request("sess-1", "urgent", priority="asap")

    mock_turn_runner.inject_prompt.assert_awaited_once_with("sess-1", "urgent")
    mock_turn_runner.queue_prompt.assert_not_awaited()


# ---------------------------------------------------------------------------
# Backward compatibility: when_idle  →  queue_prompt
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_receive_request_when_idle_still_routes_to_queue_prompt(
    controller: SessionController,
    mock_turn_runner: MagicMock,
) -> None:
    """``priority="when_idle"`` still works (backward compat)."""
    controller._turn_runner = mock_turn_runner
    await controller.get_or_create_session("sess-1", agent_name="agent-a")

    session = controller.get_session("sess-1")
    assert session is not None
    session.current_run_id = "existing-run-id"

    await controller.receive_request("sess-1", "later", priority="when_idle")

    mock_turn_runner.queue_prompt.assert_awaited_once_with("sess-1", "later")
    mock_turn_runner.inject_prompt.assert_not_awaited()
