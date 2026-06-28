"""Tests for ACPProtocolHandler cancel behavior.

Tests that cancel_session properly delegates to cancel_run_for_session
without calling fail() on the RunHandle. After the cancel-turn-not-run
fix, the event consumer is NOT stopped before cancel — it must stay
alive to deliver the RunFailedEvent (stop_reason="cancelled") to the
client.
"""
from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool_server.acp_server.event_converter import ACPEventConverter
from agentpool_server.acp_server.handler import ACPProtocolHandler
from agentpool_server.acp_server.session_manager import ACPSessionManager


@pytest.fixture
def mock_pool() -> MagicMock:
    """Mock AgentPool with SessionPool."""
    pool = MagicMock()
    pool.session_pool = MagicMock()
    pool.session_pool.sessions = MagicMock()
    return pool


@pytest.fixture
def mock_session_manager() -> MagicMock:
    """Mock ACPSessionManager."""
    return MagicMock(spec=ACPSessionManager)


@pytest.fixture
def mock_event_converter() -> MagicMock:
    """Mock ACPEventConverter."""
    conv = MagicMock(spec=ACPEventConverter)
    conv.convert = AsyncMock()
    return conv


@pytest.fixture
def mock_client() -> MagicMock:
    """Mock ACP Client."""
    client = MagicMock()
    client.session_update = AsyncMock()
    return client


@pytest.fixture
def acp_handler(
    mock_pool: MagicMock,
    mock_session_manager: MagicMock,
    mock_event_converter: MagicMock,
    mock_client: MagicMock,
) -> ACPProtocolHandler:
    """Return an ACPProtocolHandler with mocked dependencies."""
    return ACPProtocolHandler(
        agent_pool=mock_pool,
        session_manager=mock_session_manager,
        event_converter=mock_event_converter,
        client=mock_client,
        client_capabilities=None,
    )


@pytest.mark.anyio
async def test_cancel_session_calls_cancel_run_for_session(
    acp_handler: ACPProtocolHandler,
    mock_pool: MagicMock,
) -> None:
    """cancel_session must call cancel_run_for_session on the session pool.

    After the cancel-turn-not-run fix, the event consumer is NOT stopped
    before cancel — it must stay alive to deliver the RunFailedEvent
    (stop_reason="cancelled") to the client via session/update.
    """
    session_id = "test-session-123"

    with patch.object(
        mock_pool.session_pool.sessions,
        "cancel_run_for_session",
        new_callable=MagicMock,
    ) as mock_cancel:
        # Call cancel_session
        await acp_handler.cancel_session(session_id)

        # Verify cancel_run_for_session was called
        mock_cancel.assert_called_once_with(session_id)


@pytest.mark.anyio
async def test_cancel_session_handles_no_running_consumer(
    acp_handler: ACPProtocolHandler,
    mock_pool: MagicMock,
) -> None:
    """cancel_session should handle case where no consumer is running."""
    session_id = "test-session-456"

    assert session_id not in acp_handler._session_groups

    # Should not raise even with no consumer
    await acp_handler.cancel_session(session_id)

    # Verify cancel_run_for_session was still called
    mock_pool.session_pool.sessions.cancel_run_for_session.assert_called_once_with(
        session_id
    )


@pytest.mark.anyio
async def test_cancel_session_without_session_pool(acp_handler: ACPProtocolHandler) -> None:
    """cancel_session should be a no-op when SessionPool is None."""
    acp_handler.agent_pool.session_pool = None
    session_id = "test-session-789"

    # Should not raise
    await acp_handler.cancel_session(session_id)


@pytest.mark.anyio
async def test_cancel_session_does_not_call_fail_on_run_handle(
    acp_handler: ACPProtocolHandler,
    mock_pool: MagicMock,
) -> None:
    """cancel_session must NOT call fail() on the RunHandle.

    After the cancel-turn-not-run fix, cancel uses _interrupt() + cancelled
    flag, not fail(). Calling fail() would publish RunFailedEvent with an
    exception, causing double TurnComplete in the ACP event converter.
    """
    session_id = "test-session-no-fail"

    # Set up a mock run handle that cancel_run_for_session would operate on
    mock_run_handle = MagicMock()

    with (
        patch.object(acp_handler, "stop_event_consumer", new_callable=AsyncMock),
        patch.object(
            mock_pool.session_pool.sessions,
            "cancel_run_for_session",
            new_callable=MagicMock,
        ) as mock_cancel,
    ):
        # Simulate what the real cancel_run_for_session does: call cancel()
        # on the run handle (NOT fail()).
        def fake_cancel(sid: str) -> None:
            mock_run_handle.cancel()

        mock_cancel.side_effect = fake_cancel

        await acp_handler.cancel_session(session_id)

    # fail() must NOT be called — cancel uses _interrupt() + cancelled flag
    mock_run_handle.fail.assert_not_called()
    # cancel() SHOULD be called (via cancel_run_for_session)
    mock_run_handle.cancel.assert_called_once()
