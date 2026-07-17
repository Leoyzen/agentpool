"""Tests for CancelledError handling in cleanup paths (2nd round review).

Verifies that when gen.aclose() raises asyncio.CancelledError (a
BaseException, not caught by ``except Exception``), the cleanup steps
(session.current_run_id = None, _runs.pop) still execute and the
CancelledError is re-raised.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool.orchestrator.core import EventBus
from agentpool.orchestrator.run import RunHandle
from agentpool.orchestrator.session_pool_messaging import SessionPoolMessagingMixin
from agentpool.orchestrator.session_pool_runs import SessionPoolRunsMixin


pytestmark = pytest.mark.unit


class _FakeGen:
    """Fake async generator that raises CancelledError on aclose()."""

    def __init__(self, events: list[Any] | None = None) -> None:
        self._events = events or []

    def __aiter__(self) -> _FakeGen:
        return self

    async def __anext__(self) -> Any:
        if self._events:
            return self._events.pop(0)
        raise StopAsyncIteration

    async def aclose(self) -> None:
        raise asyncio.CancelledError("simulated cancellation")


async def _drain_async_gen(gen: Any) -> None:
    """Drain an async generator to completion."""
    async for _ in gen:
        pass


@pytest.mark.anyio
async def test_cancelled_error_cleanup_in_process_prompt() -> None:
    """CancelledError in _process_prompt_run_turn must not skip cleanup.

    When gen.aclose() raises CancelledError, session.current_run_id must
    still be set to None and _runs.pop must still be called, then
    CancelledError re-raised.
    """
    event_bus = EventBus()

    mixin: Any = SessionPoolMessagingMixin.__new__(SessionPoolMessagingMixin)

    session = MagicMock()
    session.is_closing = False
    session.current_run_id = None
    session._request_lock = asyncio.Lock()

    controller = MagicMock()
    controller.get_session = MagicMock(return_value=session)
    controller.get_or_create_session = AsyncMock(return_value=(session, True))
    controller.get_or_create_session_agent = AsyncMock(return_value=MagicMock())
    controller._runs = {}
    mixin.sessions = controller
    mixin.pool = MagicMock()

    mock_run_handle = MagicMock(spec=RunHandle)
    mock_run_handle.run_id = "run-123"
    mock_run_handle.start = MagicMock(return_value=_FakeGen())

    def fake_create(*args: Any, **kwargs: Any) -> Any:
        session.current_run_id = "run-123"
        controller._runs["run-123"] = mock_run_handle
        return mock_run_handle

    with (
        patch.object(
            type(mixin),
            "event_bus",
            new_callable=lambda: property(lambda self: event_bus),
        ),
        patch.object(
            SessionPoolMessagingMixin,
            "_create_run_handle",
            side_effect=fake_create,
            create=True,
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await mixin._process_prompt_run_turn("sess-1", "hello")

    assert session.current_run_id is None, "current_run_id was not cleared"
    assert "run-123" not in controller._runs, "_runs.pop was not called"


@pytest.mark.anyio
async def test_cancelled_error_cleanup_in_run_stream() -> None:
    """CancelledError in _run_stream_run_turn must not skip cleanup.

    When gen.aclose() raises CancelledError, session.current_run_id must
    still be set to None, _runs.pop must still be called, EventBus
    unsubscribe must still be attempted, and CancelledError re-raised.
    """
    event_bus = EventBus()

    mixin: Any = SessionPoolRunsMixin.__new__(SessionPoolRunsMixin)

    session = MagicMock()
    session.is_closing = False
    session.current_run_id = None
    session._request_lock = asyncio.Lock()

    controller = MagicMock()
    controller.get_session = MagicMock(return_value=session)
    controller.get_or_create_session = AsyncMock(return_value=(session, True))
    controller.get_or_create_session_agent = AsyncMock(return_value=MagicMock())
    controller._runs = {}
    mixin.sessions = controller

    mock_pool = MagicMock()
    mock_pool.get_context = MagicMock(return_value=MagicMock())
    mock_pool.manifest = MagicMock()
    mock_pool.manifest.agents = {}
    mixin.pool = mock_pool

    mock_run_handle = MagicMock(spec=RunHandle)
    mock_run_handle.run_id = "run-stream-1"
    mock_run_handle.start = MagicMock(return_value=_FakeGen())

    def fake_create(*args: Any, **kwargs: Any) -> Any:
        session.current_run_id = "run-stream-1"
        controller._runs["run-stream-1"] = mock_run_handle
        return mock_run_handle

    with (
        patch.object(
            type(mixin),
            "event_bus",
            new_callable=lambda: property(lambda self: event_bus),
        ),
        patch.object(
            SessionPoolRunsMixin,
            "_create_run_handle",
            side_effect=fake_create,
            create=True,
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await _drain_async_gen(mixin._run_stream_run_turn("sess-1", "hello"))

    assert session.current_run_id is None, "current_run_id was not cleared"
    assert "run-stream-1" not in controller._runs, "_runs.pop was not called"
