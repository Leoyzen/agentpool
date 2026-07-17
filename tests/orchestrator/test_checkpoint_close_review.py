"""Tests for checkpoint status preservation on close (2nd round review).

Verifies that when a session is checkpointed before close (due to pending
deferred calls), the _close_session_unlocked() method does NOT overwrite
the "checkpointed" status with "closed" in the store.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.orchestrator.core import SessionController
from agentpool.sessions.models import PendingDeferredCall, SessionData


pytestmark = pytest.mark.unit


def make_pending_call(
    tool_call_id: str = "call-1",
    tool_name: str = "bash",
) -> PendingDeferredCall:
    """Create a PendingDeferredCall for testing."""
    return PendingDeferredCall(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        deferred_kind="external",
        deferred_strategy="block",
    )


def make_session_data(
    session_id: str = "sess-1",
    agent_name: str = "test-agent",
    pending: list[PendingDeferredCall] | None = None,
    status: str = "active",
) -> SessionData:
    """Create a SessionData with optional pending deferred calls."""
    return SessionData(
        session_id=session_id,
        agent_name=agent_name,
        pending_deferred_calls=pending or [],
        status=status,
    )


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return a mocked AgentPool."""
    pool = MagicMock()
    pool.main_agent = MagicMock()
    pool.main_agent.name = "main-agent"
    pool.manifest = MagicMock()
    pool.manifest.agents = {}
    return pool


@pytest.fixture
def mock_store() -> MagicMock:
    """Return a mocked SessionStore."""
    store = MagicMock()
    store.load_session = AsyncMock(return_value=None)
    store.save_session = AsyncMock(return_value=None)
    store.delete_session = AsyncMock(return_value=None)
    return store


@pytest.mark.anyio
async def test_checkpointed_status_not_overwritten_on_close(
    mock_pool: MagicMock,
    mock_store: MagicMock,
) -> None:
    """Checkpointed status must not be overwritten with 'closed' on close.

    When a session is checkpointed before close (due to pending deferred
    calls), _close_session_unlocked must NOT call _mark_session_closed()
    which would overwrite the "checkpointed" status with "closed".
    """
    data = make_session_data(pending=[make_pending_call()])
    # Simulate the store already having the checkpointed data from
    # _save_close_checkpoint
    checkpointed_data = data.model_copy(update={"status": "checkpointed"})

    # load_session returns checkpointed data (as it would after
    # _save_close_checkpoint)
    mock_store.load_session = AsyncMock(return_value=checkpointed_data)
    mock_store.save_session = AsyncMock(return_value=None)

    ctrl = SessionController(pool=mock_pool, store=mock_store)

    await ctrl.get_or_create_session("sess-1")
    await ctrl.close_session("sess-1")

    # After close, the store should still have "checkpointed" status,
    # NOT "closed".
    all_saves = mock_store.save_session.await_args_list
    closed_saves = [call for call in all_saves if call[0][0].status == "closed"]
    checkpointed_saves = [call for call in all_saves if call[0][0].status == "checkpointed"]

    assert len(closed_saves) == 0, (
        f"Expected no save with status='closed', but found {len(closed_saves)}. "
        "The checkpointed status was overwritten!"
    )
    assert len(checkpointed_saves) >= 1, "Expected at least one save with status='checkpointed'"


@pytest.mark.anyio
async def test_non_checkpointed_still_marked_closed(
    mock_pool: MagicMock,
    mock_store: MagicMock,
) -> None:
    """Normal close (no pending calls) should still mark as 'closed'.

    When a session has NO pending deferred calls, close_session should
    still mark it as "closed" (normal behavior, no regression).
    """
    data = make_session_data(pending=[])

    mock_store.load_session = AsyncMock(return_value=data)
    mock_store.save_session = AsyncMock(return_value=None)

    ctrl = SessionController(pool=mock_pool, store=mock_store)

    await ctrl.get_or_create_session("sess-1")
    await ctrl.close_session("sess-1")

    all_saves = mock_store.save_session.await_args_list
    closed_saves = [call for call in all_saves if call[0][0].status == "closed"]

    assert len(closed_saves) >= 1, "Expected save with status='closed' for normal close"
