"""Red-flag test: ACPSessionManager.get_session() should not return None.

when the session exists in _acp_sessions but not yet in _session_controller.

This is a standalone test that mocks all dependencies to avoid circular imports.
"""

from __future__ import annotations

import pytest

from unittest.mock import MagicMock

pytestmark = pytest.mark.integration


def _make_manager_with_controller() -> tuple[object, MagicMock]:
    """Create an ACPSessionManager with _session_controller set but no session registered.

    Uses dependency injection via __init__ mocks to avoid circular imports.
    """
    from agentpool_server.acp_server.session_manager import ACPSessionManager

    # Create a pool mock with a SessionController that returns None for our session
    pool = MagicMock()
    session_controller = MagicMock()
    session_controller.get_session = MagicMock(return_value=None)
    pool.session_pool = MagicMock()
    pool.session_pool.sessions = session_controller

    manager = ACPSessionManager(pool=pool)
    return manager, session_controller


def _make_manager_without_controller() -> MagicMock:
    """Create an ACPSessionManager without _session_controller (no SessionPool)."""
    from agentpool_server.acp_server.session_manager import ACPSessionManager

    pool = MagicMock()
    pool.session_pool = None
    return ACPSessionManager(pool=pool)


class TestGetSessionRedFlag:
    """Red-flag tests for get_session() behavior with _session_controller."""

    def test_returns_session_when_not_yet_in_controller(self):
        """RED FLAG: get_session() MUST return the ACPSession from _acp_sessions.

        even when _session_controller doesn't have it yet.

        This reproduces the bug: during session/new, create_session() adds the
        session to _acp_sessions but the orchestrator registers it with
        _session_controller asynchronously. If get_session() returns None
        during this window, create_task(send_available_commands_update())
        is skipped and available_commands_update is never sent.
        """
        manager, controller = _make_manager_with_controller()
        session_id = "sess-red-flag-001"

        # Simulate what create_session() does: add to _acp_sessions
        mock_session = MagicMock()
        mock_session.session_id = session_id
        manager._acp_sessions[session_id] = mock_session

        # Precondition: _session_controller exists but doesn't have the session
        assert manager._session_controller is not None
        assert controller.get_session(session_id) is None, (
            "Precondition: session should NOT be in controller yet"
        )

        # THE BUG: get_session() returns None even though session is in _acp_sessions
        result = manager.get_session(session_id)

        assert result is not None, (
            f"RED FLAG: get_session({session_id!r}) returned None even though "
            f"the session exists in _acp_sessions.\n\n"
            f"This means create_task(session.send_available_commands_update()) "
            f"in new_session/load_session/resume_session is silently skipped, "
            f"and available_commands_update is never sent to the client."
        )
        assert result is mock_session

    def test_returns_none_when_not_in_either(self):
        """get_session() should return None for nonexistent sessions."""
        manager, _ = _make_manager_with_controller()
        result = manager.get_session("nonexistent")
        assert result is None

    def test_returns_session_when_in_both(self):
        """get_session() should work when session is in both places."""
        manager, controller = _make_manager_with_controller()
        session_id = "sess-in-both-001"

        mock_session = MagicMock()
        mock_session.session_id = session_id
        manager._acp_sessions[session_id] = mock_session

        # Register with controller too
        controller.get_session = MagicMock(return_value=MagicMock())

        result = manager.get_session(session_id)
        assert result is not None
        assert result is mock_session

    def test_returns_session_without_controller(self):
        """get_session() should work when _session_controller is None.

        (no SessionPool active).

        """
        manager = _make_manager_without_controller()
        session_id = "sess-no-controller-001"

        mock_session = MagicMock()
        mock_session.session_id = session_id
        manager._acp_sessions[session_id] = mock_session

        assert manager._session_controller is None
        result = manager.get_session(session_id)
        assert result is not None
        assert result is mock_session
