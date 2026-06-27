"""Tests for SessionController.receive_request() RunTurn feature-flag gating.

Covers six scenarios:
1. Flag ON + idle session -> creates RunHandle, registers in _runs.
2. Flag ON + busy session + asap -> calls RunHandle.steer().
3. Flag ON + busy session + when_idle -> calls RunHandle.followup().
4. Flag OFF -> delegates to legacy TurnRunner path.
5. Session not found -> returns None.
6. Session closing -> returns None.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.orchestrator.core import SessionController
from agentpool.orchestrator.run import RunHandle


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
    """Return a mocked TurnRunner with event_bus, steer, and followup."""
    tr = MagicMock()
    tr.event_bus = MagicMock()
    tr.event_bus.publish = AsyncMock()
    tr.steer = AsyncMock(return_value=None)
    tr.followup = AsyncMock(return_value=None)
    tr.run_loop = AsyncMock(return_value=None)
    return tr


@pytest.fixture
def mock_agent() -> MagicMock:
    """Return a MagicMock simulating a native Agent (AGENT_TYPE = 'native')."""
    agent = MagicMock()
    agent.AGENT_TYPE = "native"
    return agent


def _setup_session(
    controller: SessionController,
    session_id: str,
    agent: MagicMock,
) -> None:
    """Create a session and register an agent for it."""
    import asyncio

    controller._sessions[session_id] = MagicMock()
    controller._sessions[session_id].session_id = session_id
    controller._sessions[session_id].current_run_id = None
    controller._sessions[session_id].closing = False
    controller._sessions[session_id].is_closing = False
    controller._sessions[session_id]._request_lock = asyncio.Lock()
    controller._sessions[session_id].turn_lock = asyncio.Lock()
    controller._sessions[session_id].input_provider = None
    controller._session_agents[session_id] = agent


# ---------------------------------------------------------------------------
# Test 1: Flag ON + idle -> creates RunHandle
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_flag_on_idle_creates_run_handle(
    controller: SessionController,
    mock_turn_runner: MagicMock,
    mock_agent: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When flag is ON and session is idle, a RunHandle is created and registered."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
    controller._turn_runner = mock_turn_runner
    _setup_session(controller, "sess-1", mock_agent)

    # Patch _use_run_turn to return True (bypass isinstance check)
    controller._use_run_turn = lambda _agent: True  # type: ignore[method-assign]

    # Patch _consume_run so asyncio.create_task doesn't block
    controller._consume_run = AsyncMock(return_value=None)  # type: ignore[method-assign]

    result = await controller.receive_request("sess-1", "hello")

    assert result is not None
    assert isinstance(result, RunHandle)
    assert result.agent is mock_agent
    assert result.event_bus is mock_turn_runner.event_bus
    assert result.session is controller.get_session("sess-1")
    assert result.run_id in controller._runs
    session = controller.get_session("sess-1")
    assert session is not None
    assert session.current_run_id == result.run_id


# ---------------------------------------------------------------------------
# Test 2: Flag ON + busy + asap -> calls steer()
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_flag_on_busy_asap_calls_steer(
    controller: SessionController,
    mock_turn_runner: MagicMock,
    mock_agent: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When flag is ON and session is busy with asap, RunHandle.steer() is called."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
    controller._turn_runner = mock_turn_runner
    _setup_session(controller, "sess-2", mock_agent)

    # Simulate an active run
    existing_run = MagicMock(spec=RunHandle)
    existing_run.steer = MagicMock(return_value=True)
    existing_run.followup = MagicMock(return_value=True)
    existing_run.run_id = "existing-run-id"
    controller._runs["existing-run-id"] = existing_run
    controller.get_session("sess-2").current_run_id = "existing-run-id"  # type: ignore[union-attr]

    controller._use_run_turn = lambda _agent: True  # type: ignore[method-assign]

    await controller.receive_request("sess-2", "urgent", priority="asap")

    existing_run.steer.assert_called_once_with("urgent")
    existing_run.followup.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: Flag ON + busy + when_idle -> calls followup()
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_flag_on_busy_when_idle_calls_followup(
    controller: SessionController,
    mock_turn_runner: MagicMock,
    mock_agent: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When flag is ON and session is busy with when_idle, RunHandle.followup() is called."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
    controller._turn_runner = mock_turn_runner
    _setup_session(controller, "sess-3", mock_agent)

    existing_run = MagicMock(spec=RunHandle)
    existing_run.steer = MagicMock(return_value=True)
    existing_run.followup = MagicMock(return_value=True)
    existing_run.run_id = "existing-run-id"
    controller._runs["existing-run-id"] = existing_run
    controller.get_session("sess-3").current_run_id = "existing-run-id"  # type: ignore[union-attr]

    controller._use_run_turn = lambda _agent: True  # type: ignore[method-assign]

    await controller.receive_request("sess-3", "later", priority="when_idle")

    existing_run.followup.assert_called_once_with("later")
    existing_run.steer.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: Flag OFF -> uses TurnRunner path
# ---------------------------------------------------------------------------


@pytest.mark.deprecated
@pytest.mark.anyio
async def test_flag_off_uses_turn_runner(
    controller: SessionController,
    mock_turn_runner: MagicMock,
    mock_agent: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When flag is OFF, the legacy TurnRunner path is used."""
    monkeypatch.delenv("AGENTPOOL_USE_RUN_TURN", raising=False)
    controller._turn_runner = mock_turn_runner
    _setup_session(controller, "sess-4", mock_agent)

    await controller.get_or_create_session("sess-4", agent_name="agent-a")
    session = controller.get_session("sess-4")
    assert session is not None
    session.current_run_id = "existing-run-id"

    await controller.receive_request("sess-4", "message", priority="asap")

    mock_turn_runner.steer.assert_awaited_once_with("sess-4", "message")
    mock_turn_runner.followup.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test 5: Session not found -> returns None
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_session_not_found_returns_none(
    controller: SessionController,
    mock_turn_runner: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the session does not exist, receive_request returns None."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
    controller._turn_runner = mock_turn_runner
    controller._use_run_turn = lambda _agent: True  # type: ignore[method-assign]

    result = await controller.receive_request("nonexistent-session", "hello")

    assert result is None


# ---------------------------------------------------------------------------
# Test 6: Session closing -> returns None
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_session_closing_returns_none(
    controller: SessionController,
    mock_turn_runner: MagicMock,
    mock_agent: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the session is closing, receive_request returns None."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
    controller._turn_runner = mock_turn_runner
    _setup_session(controller, "sess-closing", mock_agent)
    controller._use_run_turn = lambda _agent: True  # type: ignore[method-assign]

    # Mark session as closing
    controller._sessions["sess-closing"].closing = True

    result = await controller.receive_request("sess-closing", "hello")

    assert result is None
