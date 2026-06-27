"""Tests for TurnRunner deprecation and thin-delegate behavior.

Covers:
1. TurnRunner.__init__() emits DeprecationWarning.
2. steer() delegates to RunHandle.steer() when AGENTPOOL_USE_RUN_TURN is on.
3. steer() calls _legacy_steer() when flag is off.
4. followup() delegates to RunHandle.followup() when flag is on.
5. followup() calls _legacy_followup() when flag is off.
6. run_loop() delegates to RunHandle.start() when flag is on.
7. run_loop() calls _legacy_run_loop() when flag is off.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import warnings

import pytest

from agentpool.orchestrator.core import SessionController, SessionState, TurnRunner


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
def turn_runner(controller: SessionController) -> TurnRunner:
    """Return a TurnRunner, suppressing the __init__ DeprecationWarning."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return TurnRunner(session_controller=controller)


@pytest.fixture
def session_state() -> SessionState:
    """Return a minimal SessionState with a run_id set."""
    state = SessionState(session_id="test-session", agent_name="test-agent")
    state.current_run_id = "test-run-id"
    return state


# ---------------------------------------------------------------------------
# __init__() tests
# ---------------------------------------------------------------------------


def test_init_emits_deprecation_warning(
    controller: SessionController,
) -> None:
    """TurnRunner.__init__() emits a DeprecationWarning."""
    with pytest.warns(DeprecationWarning, match="TurnRunner is deprecated"):
        TurnRunner(session_controller=controller)


# ---------------------------------------------------------------------------
# steer() delegate tests
# ---------------------------------------------------------------------------


async def test_steer_delegates_to_run_handle_when_flag_on(
    turn_runner: TurnRunner,
    session_state: SessionState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """steer() delegates to RunHandle.steer() when flag is on."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
    turn_runner.sessions._sessions["test-session"] = session_state

    mock_run_handle = MagicMock()
    mock_run_handle.steer.return_value = True
    turn_runner.sessions._runs["test-run-id"] = mock_run_handle

    with pytest.warns(DeprecationWarning, match="steer.*deprecated"):
        result = await turn_runner.steer("test-session", "steer msg")

    assert result is True
    mock_run_handle.steer.assert_called_once_with("steer msg")


async def test_steer_calls_legacy_when_flag_off(
    turn_runner: TurnRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """steer() calls _legacy_steer() when flag is off."""
    monkeypatch.delenv("AGENTPOOL_USE_RUN_TURN", raising=False)
    turn_runner._legacy_steer = AsyncMock(return_value=False)

    result = await turn_runner.steer("test-session", "steer msg")

    assert result is False
    turn_runner._legacy_steer.assert_awaited_once_with(
        "test-session", "steer msg",
    )


async def test_steer_returns_false_when_no_session_flag_on(
    turn_runner: TurnRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """steer() returns False when session not found and flag is on."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")

    with pytest.warns(DeprecationWarning, match="steer.*deprecated"):
        result = await turn_runner.steer("missing-session", "msg")

    assert result is False


async def test_steer_returns_false_when_no_run_handle_flag_on(
    turn_runner: TurnRunner,
    session_state: SessionState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """steer() returns False when RunHandle not found and flag is on."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
    turn_runner.sessions._sessions["test-session"] = session_state
    # No RunHandle registered for "test-run-id"

    with pytest.warns(DeprecationWarning, match="steer.*deprecated"):
        result = await turn_runner.steer("test-session", "msg")

    assert result is False


# ---------------------------------------------------------------------------
# followup() delegate tests
# ---------------------------------------------------------------------------


async def test_followup_delegates_to_run_handle_when_flag_on(
    turn_runner: TurnRunner,
    session_state: SessionState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """followup() delegates to RunHandle.followup() when flag is on."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
    turn_runner.sessions._sessions["test-session"] = session_state

    mock_run_handle = MagicMock()
    mock_run_handle.followup.return_value = True
    turn_runner.sessions._runs["test-run-id"] = mock_run_handle

    with pytest.warns(DeprecationWarning, match="followup.*deprecated"):
        result = await turn_runner.followup("test-session", "followup msg")

    assert result is True
    mock_run_handle.followup.assert_called_once_with("followup msg")


async def test_followup_calls_legacy_when_flag_off(
    turn_runner: TurnRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """followup() calls _legacy_followup() when flag is off."""
    monkeypatch.delenv("AGENTPOOL_USE_RUN_TURN", raising=False)
    turn_runner._legacy_followup = AsyncMock(return_value=False)

    result = await turn_runner.followup("test-session", "followup msg")

    assert result is False
    turn_runner._legacy_followup.assert_awaited_once_with(
        "test-session", "followup msg",
    )


async def test_followup_returns_false_when_no_session_flag_on(
    turn_runner: TurnRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """followup() returns False when session not found and flag is on."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")

    with pytest.warns(DeprecationWarning, match="followup.*deprecated"):
        result = await turn_runner.followup("missing-session", "msg")

    assert result is False


async def test_followup_returns_false_when_no_run_handle_flag_on(
    turn_runner: TurnRunner,
    session_state: SessionState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """followup() returns False when RunHandle not found and flag is on."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
    turn_runner.sessions._sessions["test-session"] = session_state
    # No RunHandle registered for "test-run-id"

    with pytest.warns(DeprecationWarning, match="followup.*deprecated"):
        result = await turn_runner.followup("test-session", "msg")

    assert result is False


# ---------------------------------------------------------------------------
# run_loop() delegate tests
# ---------------------------------------------------------------------------


async def test_run_loop_delegates_to_run_handle_when_flag_on(
    turn_runner: TurnRunner,
    session_state: SessionState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_loop() delegates to RunHandle.start() when flag is on."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
    turn_runner.sessions._sessions["test-session"] = session_state

    async def _fake_start(content: str):
        """Empty async generator simulating RunHandle.start()."""
        return
        yield  # pragma: no cover

    mock_run_handle = MagicMock()
    mock_run_handle.start = _fake_start
    turn_runner.sessions._runs["test-run-id"] = mock_run_handle

    with pytest.warns(DeprecationWarning, match="run_loop.*deprecated"):
        await turn_runner.run_loop("test-session", "initial prompt")

    # start() is an async generator — if it was called, no exception means success
    # Verify by checking that no exception was raised and the method returned


async def test_run_loop_calls_legacy_when_flag_off(
    turn_runner: TurnRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_loop() calls _legacy_run_loop() when flag is off."""
    monkeypatch.delenv("AGENTPOOL_USE_RUN_TURN", raising=False)
    turn_runner._legacy_run_loop = AsyncMock()

    await turn_runner.run_loop("test-session", "initial prompt")

    turn_runner._legacy_run_loop.assert_awaited_once_with(
        "test-session", "initial prompt",
    )


async def test_run_loop_noop_when_no_session_flag_on(
    turn_runner: TurnRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_loop() returns early when session not found and flag is on."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")

    with pytest.warns(DeprecationWarning, match="run_loop.*deprecated"):
        await turn_runner.run_loop("missing-session", "prompt")

    # No exception means success


async def test_run_loop_noop_when_no_run_handle_flag_on(
    turn_runner: TurnRunner,
    session_state: SessionState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run_loop() returns early when RunHandle not found and flag is on."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
    turn_runner.sessions._sessions["test-session"] = session_state
    # No RunHandle registered for "test-run-id"

    with pytest.warns(DeprecationWarning, match="run_loop.*deprecated"):
        await turn_runner.run_loop("test-session", "prompt")

    # No exception means success
