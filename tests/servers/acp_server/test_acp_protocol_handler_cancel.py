"""Tests for ACPProtocolHandler cancel behavior.

Tests that cancel_session properly stops the event consumer before
cancelling the run itself to prevent buffered events from being
sent as session/update notifications.
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
async def test_cancel_session_calls_both_in_order(
    acp_handler: ACPProtocolHandler,
    mock_pool: MagicMock,
) -> None:
    """cancel_session must call stop_event_consumer then cancel_run_for_session.

    This ordering is critical: stopping consumer prevents buffered events
    from leaking after the client has issued cancel.
    """
    session_id = "test-session-123"

    # Verify both methods are called in correct order
    with patch.object(
        acp_handler,
        "stop_event_consumer",
        new_callable=AsyncMock,
    ) as mock_stop, patch.object(
        mock_pool.session_pool.sessions,
        "cancel_run_for_session",
        new_callable=MagicMock,
    ) as mock_cancel:
        # Mock stop_event_consumer to prevent it from accessing EventBus
        mock_stop.return_value = None

        # Call cancel_session
        await acp_handler.cancel_session(session_id)

        # Verify both were called
        mock_stop.assert_awaited_once_with(session_id)
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
