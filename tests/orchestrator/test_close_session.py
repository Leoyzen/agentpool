"""Tests for SessionController.close_session() RunHandle lifecycle.

Covers four scenarios:
1. Flag ON + graceful close: RunHandle.close() called, turn_lock acquired,
   complete_event set, session removed from _sessions.
2. Flag ON + timeout triggers cancel: turn_lock never acquired (held by
   another task), timeout fires, RunHandle.cancel() called.
3. Flag OFF + existing behavior: legacy path runs, no RunHandle interaction.
4. Flag ON + no active run: session closes cleanly without RunHandle.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from agentpool.orchestrator.core import SessionController, SessionState
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


def _make_session(session_id: str) -> SessionState:
    """Return a minimal SessionState for testing."""
    return SessionState(session_id=session_id, agent_name="test-agent")


def _make_mock_run_handle(run_id: str = "run-1") -> MagicMock:
    """Return a MagicMock simulating a RunHandle with close/cancel/complete_event."""
    rh = MagicMock(spec=RunHandle)
    rh.run_id = run_id
    rh.close = MagicMock()
    rh.cancel = MagicMock()
    rh.complete_event = asyncio.Event()
    return rh


# ---------------------------------------------------------------------------
# Test 1: Flag ON + graceful close
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_flag_on_graceful_close(
    controller: SessionController,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When flag is ON, RunHandle.close() is called and session is cleaned up."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")

    session = _make_session("sess-1")
    session.current_run_id = "run-1"
    controller._sessions["sess-1"] = session

    run_handle = _make_mock_run_handle("run-1")
    # complete_event is already set — simulates immediate graceful completion
    controller._runs["run-1"] = run_handle

    await controller.close_session("sess-1")

    run_handle.close.assert_called_once()
    assert session.is_closing is True
    assert "sess-1" not in controller._sessions


# ---------------------------------------------------------------------------
# Test 2: Flag ON + timeout triggers cancel
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_flag_on_timeout_triggers_cancel(
    controller: SessionController,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When turn_lock acquisition times out, RunHandle.cancel() is called."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")

    session = _make_session("sess-2")
    session.current_run_id = "run-2"
    controller._sessions["sess-2"] = session

    run_handle = _make_mock_run_handle("run-2")
    controller._runs["run-2"] = run_handle

    # Pre-acquire the turn_lock so close_session cannot get it within timeout.
    # Use a very short timeout patch to avoid waiting 30 seconds.
    held_lock = session.turn_lock
    await held_lock.acquire()

    # Patch asyncio.timeout to use a tiny duration for testing
    original_timeout = asyncio.timeout

    def fast_timeout(delay: float) -> asyncio.Timeout:
        return original_timeout(0.05)

    monkeypatch.setattr(asyncio, "timeout", fast_timeout)

    await controller.close_session("sess-2")

    run_handle.close.assert_called_once()
    # Since turn_lock was held, cancel should have been called
    run_handle.cancel.assert_called_once()
    assert session.is_closing is True
    assert "sess-2" not in controller._sessions

    held_lock.release()


# ---------------------------------------------------------------------------
# Test 3: Flag OFF + existing behavior unchanged
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_flag_off_existing_behavior(
    controller: SessionController,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When flag is OFF, the legacy close path runs without RunHandle interaction."""
    monkeypatch.delenv("AGENTPOOL_USE_RUN_TURN", raising=False)

    session = _make_session("sess-3")
    session.current_run_id = "run-3"
    controller._sessions["sess-3"] = session

    run_handle = _make_mock_run_handle("run-3")
    controller._runs["run-3"] = run_handle

    await controller.close_session("sess-3")

    # Legacy path does NOT call RunHandle.close() or cancel()
    run_handle.close.assert_not_called()
    run_handle.cancel.assert_not_called()
    # Session is still removed from _sessions
    assert "sess-3" not in controller._sessions
    assert session.is_closing is True


# ---------------------------------------------------------------------------
# Test 4: Flag ON + no active run
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_flag_on_no_active_run(
    controller: SessionController,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When flag is ON and no active run exists, session closes cleanly."""
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")

    session = _make_session("sess-4")
    session.current_run_id = None
    controller._sessions["sess-4"] = session

    await controller.close_session("sess-4")

    assert session.is_closing is True
    assert "sess-4" not in controller._sessions
