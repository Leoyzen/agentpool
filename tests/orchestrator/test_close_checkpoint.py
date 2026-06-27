"""Tests for close_session checkpoint-on-close behavior (Task 18).

Covers:
- Pre-close checkpoint hook when pending_deferred_calls exist
- Checkpoint failure prevents resource release
- Normal close without pending calls behaves as before
- SessionController._should_checkpoint_on_close predicate
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.orchestrator.core import SessionController, SessionPool
from agentpool.sessions.models import PendingDeferredCall, SessionData


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
def controller(mock_pool: MagicMock) -> SessionController:
    """Return a SessionController without a store (simplest case)."""
    return SessionController(pool=mock_pool)


@pytest.fixture
def mock_store() -> MagicMock:
    """Return a mocked SessionStore."""
    store = MagicMock()
    store.load = AsyncMock(return_value=None)
    store.save = AsyncMock(return_value=None)
    store.delete = AsyncMock(return_value=None)
    return store


# ===================================================================
# _should_checkpoint_on_close
# ===================================================================


class TestShouldCheckpointOnClose:
    """Test the _should_checkpoint_on_close predicate."""

    def test_returns_false_when_no_pending_calls(self, controller: SessionController) -> None:
        """No pending calls → no checkpoint needed."""
        data = make_session_data(pending=[])
        assert controller._should_checkpoint_on_close(data) is False

    def test_returns_true_when_pending_calls_exist(self, controller: SessionController) -> None:
        """Pending calls → checkpoint needed."""
        data = make_session_data(pending=[make_pending_call()])
        assert controller._should_checkpoint_on_close(data) is True

    def test_returns_false_when_data_is_none(self, controller: SessionController) -> None:
        """None data → no checkpoint needed."""
        assert controller._should_checkpoint_on_close(None) is False


# ===================================================================
# close_session - without pending calls (normal behavior)
# ===================================================================


class TestCloseSessionWithoutPendingCalls:
    """close_session() without pending deferred calls behaves as before."""

    @pytest.mark.anyio
    async def test_removes_session(self, controller: SessionController) -> None:
        """Session is removed from tracking."""
        await controller.get_or_create_session("sess-1")
        await controller.close_session("sess-1")
        assert controller.get_session("sess-1") is None

    @pytest.mark.anyio
    async def test_is_idempotent(self, controller: SessionController) -> None:
        """Double close does not raise."""
        await controller.get_or_create_session("sess-1")
        await controller.close_session("sess-1")
        await controller.close_session("sess-1")

    @pytest.mark.anyio
    async def test_marks_closed_in_store(self, mock_pool: MagicMock, mock_store: MagicMock) -> None:
        """When a store exists, the session is marked as closed (not deleted)."""
        mock_store.load = AsyncMock(return_value=make_session_data())
        mock_store.save = AsyncMock(return_value=None)
        ctrl = SessionController(pool=mock_pool, store=mock_store)
        await ctrl.get_or_create_session("sess-1")
        await ctrl.close_session("sess-1")
        mock_store.delete.assert_not_awaited()
        closed_saves = [
            call for call in mock_store.save.await_args_list if call[0][0].status == "closed"
        ]
        assert len(closed_saves) >= 1, "Expected save() with status='closed'"

    @pytest.mark.anyio
    async def test_does_not_save_checkpoint(
        self, mock_pool: MagicMock, mock_store: MagicMock
    ) -> None:
        """Without pending calls, save is NOT called for checkpoint status."""
        mock_store.load = AsyncMock(return_value=make_session_data())
        ctrl = SessionController(pool=mock_pool, store=mock_store)
        await ctrl.get_or_create_session("sess-1")
        await ctrl.close_session("sess-1")
        # save can still be called for other reasons, but never with "checkpointed" status
        for call in (
            mock_store.save.await_args_list if hasattr(mock_store.save, "await_args_list") else []
        ):
            args, _ = call
            if hasattr(args[0], "status") and args[0].status == "checkpointed":
                pytest.fail("save() was called with checkpointed status unexpectedly")


# ===================================================================
# close_session - with pending calls (checkpoint-on-close)
# ===================================================================


class TestCloseSessionWithPendingCalls:
    """close_session() with pending deferred calls triggers checkpoint."""

    @pytest.mark.anyio
    async def test_saves_checkpoint_before_release(
        self, mock_pool: MagicMock, mock_store: MagicMock
    ) -> None:
        """When pending deferred calls exist, session data is saved as checkpointed."""
        data = make_session_data(pending=[make_pending_call()])
        mock_store.load = AsyncMock(return_value=data)
        mock_store.save = AsyncMock(return_value=None)
        ctrl = SessionController(pool=mock_pool, store=mock_store)

        await ctrl.get_or_create_session("sess-1")
        await ctrl.close_session("sess-1")

        # Should save with checkpointed status
        saved_calls = [
            call
            for call in mock_store.save.await_args_list
            if call[0][0].session_id == "sess-1" and call[0][0].status == "checkpointed"
        ]
        assert len(saved_calls) >= 1, "Expected save() with checkpointed status"
        # Compare by identity fields (tool_call_id, tool_name) rather than full equality
        # because created_at timestamps differ between the original and copy
        saved_data: SessionData = saved_calls[0][0][0]
        assert saved_data.pending_deferred_calls[0].tool_call_id == "call-1"
        assert saved_data.pending_deferred_calls[0].tool_name == "bash"

    @pytest.mark.anyio
    async def test_does_not_delete_from_store(
        self, mock_pool: MagicMock, mock_store: MagicMock
    ) -> None:
        """When checkpointed, store.delete is NOT called."""
        data = make_session_data(pending=[make_pending_call()])
        mock_store.load = AsyncMock(return_value=data)
        ctrl = SessionController(pool=mock_pool, store=mock_store)

        await ctrl.get_or_create_session("sess-1")
        await ctrl.close_session("sess-1")

        mock_store.delete.assert_not_awaited()

    @pytest.mark.anyio
    async def test_releases_inmemory_resources(
        self, mock_pool: MagicMock, mock_store: MagicMock
    ) -> None:
        """Even when checkpointed, in-memory session state is cleaned up."""
        data = make_session_data(pending=[make_pending_call()])
        mock_store.load = AsyncMock(return_value=data)
        ctrl = SessionController(pool=mock_pool, store=mock_store)

        await ctrl.get_or_create_session("sess-1")
        await ctrl.close_session("sess-1")

        assert ctrl.get_session("sess-1") is None

    @pytest.mark.anyio
    async def test_checkpoint_failure_prevents_resource_release(
        self, mock_pool: MagicMock, mock_store: MagicMock
    ) -> None:
        """If checkpoint save fails, session resources are NOT released."""
        data = make_session_data(pending=[make_pending_call()])
        mock_store.load = AsyncMock(return_value=data)
        # Make save fail for the checkpointed update
        orig_save = AsyncMock(return_value=None)

        async def failing_save(obj: Any) -> None:
            if isinstance(obj, SessionData) and obj.status == "checkpointed":
                raise RuntimeError("Storage unavailable")
            await orig_save(obj)

        mock_store.save = AsyncMock(side_effect=failing_save)
        ctrl = SessionController(pool=mock_pool, store=mock_store)

        await ctrl.get_or_create_session("sess-1")
        await ctrl.close_session("sess-1")

        # Session should remain in memory since checkpoint failed
        assert ctrl.get_session("sess-1") is not None, (
            "Session should survive when checkpoint save fails"
        )
        mock_store.delete.assert_not_awaited()


# ===================================================================
# close_session - without store
# ===================================================================


class TestCloseSessionWithoutStore:
    """close_session() when no store is configured."""

    @pytest.mark.anyio
    async def test_no_store_no_checkpoint(self, controller: SessionController) -> None:
        """Without a store, close_session just removes the session."""
        await controller.get_or_create_session("sess-1")
        await controller.close_session("sess-1")
        assert controller.get_session("sess-1") is None


# ===================================================================
# SessionPool close_session checkpoint orchestration
# ===================================================================


class TestSessionPoolCloseCheckpoint:
    """SessionPool.close_session delegates to SessionController which handles checkpoint."""

    @pytest.mark.deprecated
    @pytest.mark.anyio
    async def test_pool_close_session_with_pending_calls(
        self, mock_pool: MagicMock, mock_store: MagicMock
    ) -> None:
        """SessionPool.close_session correctly handles checkpointed close."""
        data = make_session_data(pending=[make_pending_call()])
        mock_store.load = AsyncMock(return_value=data)
        mock_store.save = AsyncMock(return_value=None)

        pool = SessionPool(pool=mock_pool)
        # Inject the mock store into the underlying SessionController
        pool.sessions.store = mock_store

        await pool.create_session("sess-1", agent_name="test-agent")
        await pool.close_session("sess-1")

        # Verify checkpointed save happened
        saved_calls = [
            call
            for call in mock_store.save.await_args_list
            if call[0][0].session_id == "sess-1" and call[0][0].status == "checkpointed"
        ]
        assert len(saved_calls) >= 1, "Expected save() with checkpointed status"
        mock_store.delete.assert_not_awaited()


# ===================================================================
# _save_close_checkpoint helper
# ===================================================================


class TestSaveCloseCheckpoint:
    """Test the _save_close_checkpoint helper."""

    @pytest.mark.anyio
    async def test_saves_with_checkpoint_status(
        self, mock_pool: MagicMock, mock_store: MagicMock
    ) -> None:
        """_save_close_checkpoint saves session data as checkpointed."""
        data = make_session_data(pending=[make_pending_call()])
        mock_store.load = AsyncMock(return_value=data)
        ctrl = SessionController(pool=mock_pool, store=mock_store)

        result = await ctrl._save_close_checkpoint("sess-1", data)

        assert result is True
        mock_store.save.assert_awaited_once()
        saved_data = mock_store.save.await_args[0][0]
        assert saved_data.status == "checkpointed"
        assert len(saved_data.pending_deferred_calls) == 1

    @pytest.mark.anyio
    async def test_returns_false_on_failure(
        self, mock_pool: MagicMock, mock_store: MagicMock
    ) -> None:
        """_save_close_checkpoint returns False when save fails."""
        data = make_session_data(pending=[make_pending_call()])
        mock_store.save = AsyncMock(side_effect=RuntimeError("Storage error"))
        ctrl = SessionController(pool=mock_pool, store=mock_store)

        result = await ctrl._save_close_checkpoint("sess-1", data)

        assert result is False
