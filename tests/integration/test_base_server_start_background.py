"""L2 integration tests for BaseServer start_background with ManagedTaskGroup.

Tests that BaseServer properly manages its ManagedTaskGroup during
start_background()/stop() and run_context() lifecycle, ensuring
no task leaks.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool_server.base import BaseServer


pytestmark = pytest.mark.integration


class _MinimalServer(BaseServer):
    """Minimal BaseServer subclass for testing lifecycle."""

    def __init__(self, pool: Any) -> None:
        super().__init__(pool, name="test-server")
        self._start_called = False

    async def _start_async(self) -> None:
        """Run until shutdown event is set."""
        self._start_called = True
        await self._shutdown_event.wait()


def _make_mock_pool() -> Any:
    """Create a mock AgentPool that supports async context manager."""
    pool = MagicMock()
    pool.__aenter__ = AsyncMock(return_value=pool)
    pool.__aexit__ = AsyncMock(return_value=None)
    return pool


async def test_start_background_and_stop_closes_task_group() -> None:
    """Given a BaseServer, when start_background then stop, TG is closed and not busy."""
    pool = _make_mock_pool()
    server = _MinimalServer(pool)

    server.start_background()
    # Give the server a moment to start
    import anyio

    await anyio.sleep(0.05)
    assert server._start_called

    server.stop()
    await server.wait_until_stopped()

    assert server._task_group._closed
    assert not server._task_group.is_busy()


async def test_run_context_closes_task_group() -> None:
    """Given a BaseServer, when run_context is used, the task group is closed on exit."""
    pool = _make_mock_pool()
    server = _MinimalServer(pool)

    async with server.run_context():
        import anyio

        await anyio.sleep(0.05)
        assert server._start_called

    # After run_context exits, server is stopped and TG is closed
    assert server._task_group._closed
    assert not server._task_group.is_busy()


async def test_aenter_aexit_closes_task_group() -> None:
    """Given a BaseServer, when used as async context manager, the task group is closed on exit."""
    pool = _make_mock_pool()
    server = _MinimalServer(pool)

    async with server:
        assert not server._task_group._closed

    assert server._task_group._closed
    assert not server._task_group.is_busy()
