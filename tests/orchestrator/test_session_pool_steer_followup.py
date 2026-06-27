"""Tests for SessionPool.steer() and SessionPool.followup() delegation.

Verifies that the new public API methods correctly delegate to
TurnRunner.steer() and TurnRunner.followup().
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.orchestrator import SessionPool


pytestmark = [pytest.mark.unit, pytest.mark.deprecated]


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
def session_pool(mock_pool: MagicMock) -> SessionPool:
    """Return a SessionPool with mocked turns."""
    sp = SessionPool(pool=mock_pool)
    # Replace the real TurnRunner with a mock for delegation testing
    sp.turns = AsyncMock()
    sp.turns.steer = AsyncMock(return_value=True)
    sp.turns.followup = AsyncMock(return_value=True)
    return sp


# =============================================================================
# SessionPool.steer() delegates to TurnRunner.steer()
# =============================================================================


class TestSessionPoolSteer:
    """Tests for SessionPool.steer()."""

    @pytest.mark.anyio
    async def test_steer_delegates_to_turns_steer(
        self,
        session_pool: SessionPool,
    ) -> None:
        """steer() should delegate to self.turns.steer()."""
        result = await session_pool.steer("test-session", "test message")

        session_pool.turns.steer.assert_awaited_once_with(
            "test-session", "test message",
        )
        assert result is True

    @pytest.mark.anyio
    async def test_steer_passes_kwargs(
        self,
        session_pool: SessionPool,
    ) -> None:
        """steer() should forward kwargs to turns.steer()."""
        await session_pool.steer("sess-1", "msg", extra="value", flag=True)

        session_pool.turns.steer.assert_awaited_once_with(
            "sess-1", "msg", extra="value", flag=True,
        )


# =============================================================================
# SessionPool.followup() delegates to TurnRunner.followup()
# =============================================================================


class TestSessionPoolFollowup:
    """Tests for SessionPool.followup()."""

    @pytest.mark.anyio
    async def test_followup_delegates_to_turns_followup(
        self,
        session_pool: SessionPool,
    ) -> None:
        """followup() should delegate to self.turns.followup()."""
        result = await session_pool.followup("test-session", "test message")

        session_pool.turns.followup.assert_awaited_once_with(
            "test-session", "test message",
        )
        assert result is True

    @pytest.mark.anyio
    async def test_followup_passes_kwargs(
        self,
        session_pool: SessionPool,
    ) -> None:
        """followup() should forward kwargs to turns.followup()."""
        await session_pool.followup("sess-1", "msg", priority="high")

        session_pool.turns.followup.assert_awaited_once_with(
            "sess-1", "msg", priority="high",
        )
