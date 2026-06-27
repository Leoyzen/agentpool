"""Tests for SessionController deprecated methods feature-flag gating.

Covers _create_run(), _cleanup_run(), cancel_run_for_session():
1. Each emits DeprecationWarning when AGENTPOOL_USE_RUN_TURN is enabled.
2. Each works normally when the flag is off (no warning, full behavior).
3. Each returns early (no-op) when flag is on.
"""

from __future__ import annotations

from unittest.mock import MagicMock
import warnings

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


@pytest.fixture
def session_state() -> SessionState:
    """Return a minimal SessionState."""
    return SessionState(session_id="test-session", agent_name="test-agent")


# ---------------------------------------------------------------------------
# _create_run() tests
# ---------------------------------------------------------------------------


class TestCreateRun:
    """Tests for SessionController._create_run()."""

    def test_deprecated_when_flag_on(
        self,
        controller: SessionController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_create_run() emits DeprecationWarning and returns None when flag is on."""
        monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
        with pytest.warns(DeprecationWarning, match="_create_run is deprecated"):
            result = controller._create_run("test-session", "hello")
        assert result is None

    def test_works_when_flag_off(
        self,
        controller: SessionController,
        session_state: SessionState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_create_run() returns a RunHandle when flag is off."""
        monkeypatch.delenv("AGENTPOOL_USE_RUN_TURN", raising=False)
        controller._sessions["test-session"] = session_state
        result = controller._create_run("test-session", "hello")
        assert isinstance(result, RunHandle)
        assert result.session_id == "test-session"

    def test_deprecation_not_emitted_when_flag_off(
        self,
        controller: SessionController,
        session_state: SessionState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_create_run() does not emit DeprecationWarning when flag is off."""
        monkeypatch.delenv("AGENTPOOL_USE_RUN_TURN", raising=False)
        controller._sessions["test-session"] = session_state
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = controller._create_run("test-session", "hello")
        assert isinstance(result, RunHandle)
        deprecation_warnings = [
            x for x in w if issubclass(x.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) == 0

    def test_noop_when_flag_on(
        self,
        controller: SessionController,
        session_state: SessionState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_create_run() does not create a run when flag is on."""
        monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
        controller._sessions["test-session"] = session_state
        with pytest.warns(DeprecationWarning, match="_create_run is deprecated"):
            result = controller._create_run("test-session", "hello")
        assert result is None
        # Verify no entry was added to _runs
        assert len(controller._runs) == 0


# ---------------------------------------------------------------------------
# _cleanup_run() tests
# ---------------------------------------------------------------------------


class TestCleanupRun:
    """Tests for SessionController._cleanup_run()."""

    def test_deprecated_when_flag_on(
        self,
        controller: SessionController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_cleanup_run() emits DeprecationWarning when flag is on."""
        monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
        with pytest.warns(DeprecationWarning, match="_cleanup_run is deprecated"):
            controller._cleanup_run("some-run-id")

    def test_works_when_flag_off(
        self,
        controller: SessionController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_cleanup_run() removes the handle from _runs when flag is off."""
        monkeypatch.delenv("AGENTPOOL_USE_RUN_TURN", raising=False)
        handle = RunHandle(
            run_id="test-run-id",
            session_id="test-session",
            agent_type="native",
        )
        controller._runs["test-run-id"] = handle
        controller._cleanup_run("test-run-id")
        assert "test-run-id" not in controller._runs

    def test_signals_completion_when_flag_off(
        self,
        controller: SessionController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_cleanup_run() sets complete_event when flag is off."""
        monkeypatch.delenv("AGENTPOOL_USE_RUN_TURN", raising=False)
        handle = RunHandle(
            run_id="test-run-id",
            session_id="test-session",
            agent_type="native",
        )
        controller._runs["test-run-id"] = handle
        assert not handle.complete_event.is_set()
        controller._cleanup_run("test-run-id")
        assert handle.complete_event.is_set()

    def test_noop_when_flag_on(
        self,
        controller: SessionController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_cleanup_run() does not modify _runs when flag is on."""
        monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
        handle = RunHandle(
            run_id="test-run-id",
            session_id="test-session",
            agent_type="native",
        )
        controller._runs["test-run-id"] = handle
        with pytest.warns(DeprecationWarning, match="_cleanup_run is deprecated"):
            controller._cleanup_run("test-run-id")
        # Verify handle was NOT removed (_runs unchanged)
        assert "test-run-id" in controller._runs


# ---------------------------------------------------------------------------
# cancel_run_for_session() tests
# ---------------------------------------------------------------------------


class TestCancelRunForSession:
    """Tests for SessionController.cancel_run_for_session()."""

    def test_deprecated_when_flag_on(
        self,
        controller: SessionController,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cancel_run_for_session() emits DeprecationWarning when flag is on."""
        monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
        with pytest.warns(
            DeprecationWarning, match="cancel_run_for_session is deprecated"
        ):
            controller.cancel_run_for_session("test-session")

    def test_works_when_flag_off(
        self,
        controller: SessionController,
        session_state: SessionState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cancel_run_for_session() cancels the active run when flag is off."""
        monkeypatch.delenv("AGENTPOOL_USE_RUN_TURN", raising=False)
        handle = MagicMock(spec=RunHandle)
        controller._runs["test-run-id"] = handle
        session_state.current_run_id = "test-run-id"
        controller._sessions["test-session"] = session_state
        controller.cancel_run_for_session("test-session")
        handle.cancel.assert_called_once()

    def test_noop_when_flag_on(
        self,
        controller: SessionController,
        session_state: SessionState,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cancel_run_for_session() does not cancel the run when flag is on."""
        monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN", "true")
        handle = MagicMock(spec=RunHandle)
        controller._runs["test-run-id"] = handle
        session_state.current_run_id = "test-run-id"
        controller._sessions["test-session"] = session_state
        with pytest.warns(DeprecationWarning, match="cancel_run_for_session is deprecated"):
            controller.cancel_run_for_session("test-session")
        handle.cancel.assert_not_called()
