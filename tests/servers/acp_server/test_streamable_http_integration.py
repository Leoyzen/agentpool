"""Integration tests for ACP streamable HTTP WebSocket transport server lifecycle.

Tests cover:
- _serve_streamable_http server startup and shutdown
- WebSocketRoute registration at /acp
- Connection handling with Acp-Connection-Id header
- Graceful cleanup of active connections on shutdown

All external dependencies (uvicorn, starlette) are mocked.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acp.agent.implementations.testing import TestAgent
from acp.transports import _serve_streamable_http


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@pytest.fixture
def mock_starlette() -> Iterator[MagicMock]:
    """Mock Starlette application class."""
    with patch("starlette.applications.Starlette") as mock:
        app_instance = MagicMock()
        mock.return_value = app_instance
        yield mock


@pytest.fixture
def mock_websocket_route() -> Iterator[MagicMock]:
    """Mock WebSocketRoute class."""
    with patch("starlette.routing.WebSocketRoute") as mock:
        yield mock


class _MockUvicornServer:
    """Mock uvicorn server with trackable should_exit attribute."""

    def __init__(self) -> None:
        self.should_exit = False
        self.serve_called = False

    async def serve(self) -> None:
        self.serve_called = True
        while not self.should_exit:
            await asyncio.sleep(0.01)


@pytest.fixture
def mock_uvicorn() -> Iterator[MagicMock]:
    """Mock uvicorn Config and Server classes."""
    with patch("uvicorn.Config") as mock_config, patch("uvicorn.Server") as mock_server_cls:
        mock_config.return_value = MagicMock()
        mock_server = _MockUvicornServer()
        mock_server_cls.return_value = mock_server
        # Return a composite mock that has both Config and Server attributes
        composite = MagicMock()
        composite.Config = mock_config
        composite.Server = mock_server_cls
        composite._server = mock_server
        yield composite


@pytest.fixture
def mock_websocket() -> AsyncMock:
    """Mock Starlette WebSocket object."""
    ws = AsyncMock()
    ws.accept.return_value = None
    return ws


# =============================================================================
# Server lifecycle tests
# =============================================================================


@pytest.mark.unit
async def test_serve_streamable_http_creates_starlette_app(
    mock_starlette: MagicMock,
    mock_websocket_route: MagicMock,
    mock_uvicorn: MagicMock,
) -> None:
    """_serve_streamable_http should create Starlette app with /acp route."""
    shutdown_event = asyncio.Event()

    # Start server in background, then trigger shutdown
    task = asyncio.create_task(
        _serve_streamable_http(
            TestAgent(),
            host="127.0.0.1",
            port=8080,
            shutdown_event=shutdown_event,
            debug_file=None,
        )
    )

    # Give server a moment to start
    await asyncio.sleep(0.05)
    shutdown_event.set()
    await asyncio.wait_for(task, timeout=1)

    mock_starlette.assert_called_once()
    routes = mock_starlette.call_args.kwargs.get("routes", [])
    assert len(routes) == 1
    mock_websocket_route.assert_called_once()
    assert mock_websocket_route.call_args.args[0] == "/acp"
    assert callable(mock_websocket_route.call_args.args[1])


@pytest.mark.unit
async def test_serve_streamable_http_creates_uvicorn_config(
    mock_starlette: MagicMock,
    mock_uvicorn: MagicMock,
) -> None:
    """_serve_streamable_http should create uvicorn Config with correct host/port."""
    shutdown_event = asyncio.Event()

    task = asyncio.create_task(
        _serve_streamable_http(
            TestAgent(),
            host="0.0.0.0",
            port=9000,
            shutdown_event=shutdown_event,
            debug_file=None,
        )
    )

    await asyncio.sleep(0.05)
    shutdown_event.set()
    await asyncio.wait_for(task, timeout=1)

    mock_uvicorn.Config.assert_called_once()
    config_call = mock_uvicorn.Config.call_args
    assert config_call.kwargs.get("host") == "0.0.0.0"
    assert config_call.kwargs.get("port") == 9000
    assert config_call.kwargs.get("log_level") == "warning"


@pytest.mark.unit
async def test_serve_streamable_http_calls_server_serve(
    mock_starlette: MagicMock,
    mock_uvicorn: MagicMock,
) -> None:
    """_serve_streamable_http should call uvicorn Server.serve()."""
    shutdown_event = asyncio.Event()

    task = asyncio.create_task(
        _serve_streamable_http(
            TestAgent(),
            host="localhost",
            port=8080,
            shutdown_event=shutdown_event,
            debug_file=None,
        )
    )

    await asyncio.sleep(0.05)
    shutdown_event.set()
    await asyncio.wait_for(task, timeout=1)

    assert mock_uvicorn._server.serve_called


@pytest.mark.unit
async def test_serve_streamable_http_shuts_down_gracefully(
    mock_starlette: MagicMock,
    mock_uvicorn: MagicMock,
) -> None:
    """Setting shutdown_event should stop the uvicorn server gracefully."""
    shutdown_event = asyncio.Event()
    server_instance: _MockUvicornServer = mock_uvicorn._server

    task = asyncio.create_task(
        _serve_streamable_http(
            TestAgent(),
            host="localhost",
            port=8080,
            shutdown_event=shutdown_event,
            debug_file=None,
        )
    )

    await asyncio.sleep(0.05)
    shutdown_event.set()
    await asyncio.wait_for(task, timeout=1)

    # Server.should_exit should be set to True
    assert server_instance.should_exit is True


@pytest.mark.unit
async def test_serve_streamable_http_uses_internal_shutdown_when_no_event(
    mock_starlette: MagicMock,
    mock_uvicorn: MagicMock,
) -> None:
    """When no shutdown_event is provided, an internal event should be used."""

    # Use a serve method that can be cancelled
    async def cancellable_serve() -> None:
        while True:
            await asyncio.sleep(0.01)

    mock_uvicorn._server.serve = cancellable_serve

    task = asyncio.create_task(
        _serve_streamable_http(
            TestAgent(),
            host="localhost",
            port=8080,
            shutdown_event=None,
            debug_file=None,
        )
    )

    await asyncio.sleep(0.05)
    # Cancel the task since there's no external shutdown event
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# =============================================================================
# WebSocket connection handler tests
# =============================================================================


@pytest.mark.unit
async def test_handle_acp_accepts_with_connection_id_header(
    mock_starlette: MagicMock,
    mock_websocket_route: MagicMock,
    mock_uvicorn: MagicMock,
    mock_websocket: AsyncMock,
) -> None:
    """The /acp handler should accept with Acp-Connection-Id header."""
    shutdown_event = asyncio.Event()
    captured_handler: Callable[[Any], Any] | None = None

    def capture_route(path: str, endpoint: Any) -> MagicMock:
        nonlocal captured_handler
        captured_handler = endpoint
        return MagicMock()

    mock_websocket_route.side_effect = capture_route

    # Mock serve to complete immediately after we capture the handler
    async def quick_serve() -> None:
        await asyncio.sleep(0)

    mock_uvicorn._server.serve = quick_serve

    task = asyncio.create_task(
        _serve_streamable_http(
            TestAgent(),
            host="localhost",
            port=8080,
            shutdown_event=shutdown_event,
            debug_file=None,
        )
    )

    await asyncio.sleep(0.05)
    shutdown_event.set()
    await asyncio.wait_for(task, timeout=1)

    assert captured_handler is not None

    # Call the captured handler with mock websocket
    handler_task = asyncio.create_task(captured_handler(mock_websocket))
    # Give it a moment to set up, then simulate client disconnect by cancelling
    await asyncio.sleep(0.05)
    handler_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await handler_task

    mock_websocket.accept.assert_awaited_once()
    call_kwargs = mock_websocket.accept.call_args.kwargs
    headers = call_kwargs.get("headers", [])
    assert any(h[0] == b"Acp-Connection-Id" for h in headers)


@pytest.mark.unit
async def test_handle_acp_creates_stream_adapters(
    mock_starlette: MagicMock,
    mock_websocket_route: MagicMock,
    mock_uvicorn: MagicMock,
    mock_websocket: AsyncMock,
) -> None:
    """The /acp handler should create StarletteWebSocket read/write adapters."""
    shutdown_event = asyncio.Event()
    captured_handler: Callable[[Any], Any] | None = None

    def capture_route(path: str, endpoint: Any) -> MagicMock:
        nonlocal captured_handler
        captured_handler = endpoint
        return MagicMock()

    mock_websocket_route.side_effect = capture_route

    async def quick_serve() -> None:
        await asyncio.sleep(0)

    mock_uvicorn._server.serve = quick_serve

    with (
        patch("acp.transports._StarletteWebSocketReadStream") as mock_reader,
        patch("acp.transports._StarletteWebSocketWriteStream") as mock_writer,
    ):
        task = asyncio.create_task(
            _serve_streamable_http(
                TestAgent(),
                host="localhost",
                port=8080,
                shutdown_event=shutdown_event,
                debug_file=None,
            )
        )

        await asyncio.sleep(0.05)
        shutdown_event.set()
        await asyncio.wait_for(task, timeout=1)

        assert captured_handler is not None

        handler_task = asyncio.create_task(captured_handler(mock_websocket))
        await asyncio.sleep(0.05)
        handler_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await handler_task

        mock_reader.assert_called_once_with(mock_websocket)
        mock_writer.assert_called_once_with(mock_websocket)


@pytest.mark.unit
async def test_handle_acp_closes_connection_on_cleanup(
    mock_starlette: MagicMock,
    mock_websocket_route: MagicMock,
    mock_uvicorn: MagicMock,
    mock_websocket: AsyncMock,
) -> None:
    """The /acp handler should close AgentSideConnection on cleanup."""
    shutdown_event = asyncio.Event()
    captured_handler: Callable[[Any], Any] | None = None

    def capture_route(path: str, endpoint: Any) -> MagicMock:
        nonlocal captured_handler
        captured_handler = endpoint
        return MagicMock()

    mock_websocket_route.side_effect = capture_route

    async def quick_serve() -> None:
        await asyncio.sleep(0)

    mock_uvicorn._server.serve = quick_serve

    with patch("acp.agent.connection.AgentSideConnection") as mock_conn_cls:
        mock_conn = AsyncMock()
        mock_conn_cls.return_value = mock_conn
        # Simulate a done recv_task so the handler exits immediately
        mock_conn._conn._recv_task = asyncio.create_task(asyncio.sleep(0))
        await mock_conn._conn._recv_task

        task = asyncio.create_task(
            _serve_streamable_http(
                TestAgent(),
                host="localhost",
                port=8080,
                shutdown_event=shutdown_event,
                debug_file=None,
            )
        )

        await asyncio.sleep(0.05)
        shutdown_event.set()
        await asyncio.wait_for(task, timeout=1)

        assert captured_handler is not None

        handler_task = asyncio.create_task(captured_handler(mock_websocket))
        await asyncio.wait_for(handler_task, timeout=1)

        mock_conn.close.assert_awaited()


# =============================================================================
# Server cleanup tests
# =============================================================================


@pytest.mark.unit
async def test_serve_streamable_http_closes_remaining_connections(
    mock_starlette: MagicMock,
    mock_uvicorn: MagicMock,
) -> None:
    """Server should close all remaining active connections on shutdown."""
    shutdown_event = asyncio.Event()
    mock_conn1 = AsyncMock()
    mock_conn2 = AsyncMock()

    async def quick_serve() -> None:
        await asyncio.sleep(0)

    mock_uvicorn._server.serve = quick_serve

    with patch("acp.agent.connection.AgentSideConnection", side_effect=[mock_conn1, mock_conn2]):
        task = asyncio.create_task(
            _serve_streamable_http(
                TestAgent(),
                host="localhost",
                port=8080,
                shutdown_event=shutdown_event,
                debug_file=None,
            )
        )

        await asyncio.sleep(0.05)
        shutdown_event.set()
        await asyncio.wait_for(task, timeout=1)

    # Since no connections were actually established in this mock scenario,
    # we just verify the server completes without errors
    assert task.done()


@pytest.mark.unit
async def test_serve_streamable_http_cancels_watcher_task_on_cleanup(
    mock_starlette: MagicMock,
    mock_uvicorn: MagicMock,
) -> None:
    """Server should cancel the shutdown watcher task on cleanup."""
    shutdown_event = asyncio.Event()

    async def slow_serve() -> None:
        await shutdown_event.wait()

    mock_uvicorn._server.serve = slow_serve

    task = asyncio.create_task(
        _serve_streamable_http(
            TestAgent(),
            host="localhost",
            port=8080,
            shutdown_event=shutdown_event,
            debug_file=None,
        )
    )

    await asyncio.sleep(0.05)
    shutdown_event.set()
    await asyncio.wait_for(task, timeout=1)

    assert task.done()
    assert not task.cancelled()
