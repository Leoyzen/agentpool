"""Unit tests for ACP streamable HTTP WebSocket transport.

Tests cover:
- ACPWebSocketTransport dataclass creation and defaults
- Starlette WebSocket stream adapters (read/write)
- Legacy WebSocket stream adapters (read/write)
- AgentSideConnection initialize guard (-32002 error before initialize)
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import anyenv
import anyio
from anyio.abc import ByteReceiveStream, ByteSendStream
import pytest

from acp import AgentSideConnection
from acp.agent.implementations.testing import TestAgent
from acp.transports import (
    ACPWebSocketTransport,
    StdioTransport,
    StreamTransport,
    WebSocketTransport,
    _StarletteWebSocketReadStream,
    _StarletteWebSocketWriteStream,
    _WebSocketReadStream,
    _WebSocketWriteStream,
)


if TYPE_CHECKING:
    from acp.transports import Transport


class _AsyncioReaderAdapter(ByteReceiveStream):
    """Adapts asyncio.StreamReader to anyio's ByteReceiveStream interface."""

    def __init__(self, reader: asyncio.StreamReader) -> None:
        self._reader = reader

    async def receive(self, max_bytes: int = 65536) -> bytes:
        data = await self._reader.read(max_bytes)
        if not data:
            raise anyio.EndOfStream
        return data

    async def aclose(self) -> None:
        pass


class _AsyncioWriterAdapter(ByteSendStream):
    """Adapts asyncio.StreamWriter to anyio's ByteSendStream interface."""

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer

    async def send(self, item: bytes) -> None:
        self._writer.write(item)
        await self._writer.drain()

    async def aclose(self) -> None:
        self._writer.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()


class _ConnectedStreams:
    """Pairs of connected asyncio streams for testing JSON-RPC over wires."""

    def __init__(self) -> None:
        self.server_reader: asyncio.StreamReader | None = None
        self.server_writer: asyncio.StreamWriter | None = None
        self.client_reader: asyncio.StreamReader | None = None
        self.client_writer: asyncio.StreamWriter | None = None

    async def __aenter__(self):
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            self.server_reader = reader
            self.server_writer = writer

        self._server = await asyncio.start_server(handle, host="127.0.0.1", port=0)
        host, port = self._server.sockets[0].getsockname()[:2]
        self.client_reader, self.client_writer = await asyncio.open_connection(host, port)

        for _ in range(100):
            if self.server_reader and self.server_writer:
                break
            await anyio.sleep(0.01)
        assert self.server_reader
        assert self.server_writer
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self.client_writer:
            self.client_writer.close()
            with contextlib.suppress(Exception):
                await self.client_writer.wait_closed()
        if self.server_writer:
            self.server_writer.close()
            with contextlib.suppress(Exception):
                await self.server_writer.wait_closed()
        if self._server:
            self._server.close()
            await self._server.wait_closed()


# =============================================================================
# ACPWebSocketTransport dataclass tests
# =============================================================================


@pytest.mark.unit
def test_acp_websocket_transport_defaults() -> None:
    """ACPWebSocketTransport should have correct default values."""
    transport = ACPWebSocketTransport()
    assert transport.host == "localhost"
    assert transport.port == 8080


@pytest.mark.unit
def test_acp_websocket_transport_custom_values() -> None:
    """ACPWebSocketTransport should accept custom host and port."""
    transport = ACPWebSocketTransport(host="0.0.0.0", port=9000)
    assert transport.host == "0.0.0.0"
    assert transport.port == 9000


@pytest.mark.unit
def test_acp_websocket_transport_equality() -> None:
    """Two transports with same values should be equal."""
    t1 = ACPWebSocketTransport(host="localhost", port=8080)
    t2 = ACPWebSocketTransport(host="localhost", port=8080)
    t3 = ACPWebSocketTransport(host="0.0.0.0", port=8080)
    assert t1 == t2
    assert t1 != t3


@pytest.mark.unit
def test_acp_websocket_transport_repr() -> None:
    """Transport repr should include class name and fields."""
    transport = ACPWebSocketTransport(host="localhost", port=8080)
    repr_str = repr(transport)
    assert "ACPWebSocketTransport" in repr_str
    assert "localhost" in repr_str
    assert "8080" in repr_str


# =============================================================================
# Starlette WebSocket read stream adapter tests
# =============================================================================


@pytest.mark.unit
async def test_starlette_read_stream_receive_text() -> None:
    """_StarletteWebSocketReadStream should encode text and append newline."""
    mock_ws = AsyncMock()
    mock_ws.receive_text.return_value = '{"jsonrpc":"2.0","id":1}'
    reader = _StarletteWebSocketReadStream(mock_ws)

    data = await reader.receive()
    assert data == b'{"jsonrpc":"2.0","id":1}\n'
    mock_ws.receive_text.assert_awaited_once()


@pytest.mark.unit
async def test_starlette_read_stream_preserves_existing_newline() -> None:
    """_StarletteWebSocketReadStream should not double-append newline."""
    mock_ws = AsyncMock()
    mock_ws.receive_text.return_value = '{"jsonrpc":"2.0","id":1}\n'
    reader = _StarletteWebSocketReadStream(mock_ws)

    data = await reader.receive()
    assert data == b'{"jsonrpc":"2.0","id":1}\n'


@pytest.mark.unit
async def test_starlette_read_stream_end_of_stream_on_exception() -> None:
    """_StarletteWebSocketReadStream should raise EndOfStream on exception."""
    mock_ws = AsyncMock()
    mock_ws.receive_text.side_effect = RuntimeError("Connection closed")
    reader = _StarletteWebSocketReadStream(mock_ws)

    with pytest.raises(anyio.EndOfStream):
        await reader.receive()


@pytest.mark.unit
async def test_starlette_read_stream_aclose_is_noop() -> None:
    """_StarletteWebSocketReadStream.aclose should be a no-op."""
    mock_ws = AsyncMock()
    reader = _StarletteWebSocketReadStream(mock_ws)
    await reader.aclose()
    mock_ws.close.assert_not_called()


# =============================================================================
# Starlette WebSocket write stream adapter tests
# =============================================================================


@pytest.mark.unit
async def test_starlette_write_stream_sends_stripped_text() -> None:
    """_StarletteWebSocketWriteStream should strip trailing newline and send text."""
    mock_ws = AsyncMock()
    writer = _StarletteWebSocketWriteStream(mock_ws)

    await writer.send(b'{"jsonrpc":"2.0","id":1}\n')
    mock_ws.send_text.assert_awaited_once_with('{"jsonrpc":"2.0","id":1}')


@pytest.mark.unit
async def test_starlette_write_stream_skips_empty_message() -> None:
    """_StarletteWebSocketWriteStream should skip empty messages after stripping."""
    mock_ws = AsyncMock()
    writer = _StarletteWebSocketWriteStream(mock_ws)

    await writer.send(b"\n")
    mock_ws.send_text.assert_not_called()


@pytest.mark.unit
async def test_starlette_write_stream_aclose_is_noop() -> None:
    """_StarletteWebSocketWriteStream.aclose should be a no-op."""
    mock_ws = AsyncMock()
    writer = _StarletteWebSocketWriteStream(mock_ws)
    await writer.aclose()
    mock_ws.close.assert_not_called()


# =============================================================================
# Legacy WebSocket read stream adapter tests
# =============================================================================


@pytest.mark.unit
async def test_websocket_read_stream_receive_string() -> None:
    """_WebSocketReadStream should handle string messages from websocket."""
    mock_ws = AsyncMock()
    mock_ws.recv.return_value = '{"jsonrpc":"2.0","id":1}'
    reader = _WebSocketReadStream(mock_ws)

    data = await reader.receive()
    assert data == b'{"jsonrpc":"2.0","id":1}\n'


@pytest.mark.unit
async def test_websocket_read_stream_receive_bytes() -> None:
    """_WebSocketReadStream should handle bytes messages from websocket."""
    mock_ws = AsyncMock()
    mock_ws.recv.return_value = b'{"jsonrpc":"2.0","id":1}'
    reader = _WebSocketReadStream(mock_ws)

    data = await reader.receive()
    assert data == b'{"jsonrpc":"2.0","id":1}\n'


@pytest.mark.unit
async def test_websocket_read_stream_buffering() -> None:
    """_WebSocketReadStream should buffer data across receive calls."""
    mock_ws = AsyncMock()
    mock_ws.recv.return_value = b"abc"
    reader = _WebSocketReadStream(mock_ws)

    # First receive gets 2 bytes
    data1 = await reader.receive(max_bytes=2)
    assert data1 == b"ab"

    # Second receive gets remaining 1 byte + newline
    data2 = await reader.receive(max_bytes=2)
    assert data2 == b"c\n"


@pytest.mark.unit
async def test_websocket_read_stream_end_of_stream_on_exception() -> None:
    """_WebSocketReadStream should raise EndOfStream on exception."""
    mock_ws = AsyncMock()
    mock_ws.recv.side_effect = RuntimeError("Connection closed")
    reader = _WebSocketReadStream(mock_ws)

    with pytest.raises(anyio.EndOfStream):
        await reader.receive()


# =============================================================================
# Legacy WebSocket write stream adapter tests
# =============================================================================


@pytest.mark.unit
async def test_websocket_write_stream_sends_stripped_text() -> None:
    """_WebSocketWriteStream should strip trailing newline and send message."""
    mock_ws = AsyncMock()
    writer = _WebSocketWriteStream(mock_ws)

    await writer.send(b'{"jsonrpc":"2.0","id":1}\n')
    mock_ws.send.assert_awaited_once_with('{"jsonrpc":"2.0","id":1}')


@pytest.mark.unit
async def test_websocket_write_stream_skips_empty_message() -> None:
    """_WebSocketWriteStream should skip empty messages after stripping."""
    mock_ws = AsyncMock()
    writer = _WebSocketWriteStream(mock_ws)

    await writer.send(b"\n")
    mock_ws.send.assert_not_called()


# =============================================================================
# AgentSideConnection initialize guard tests
# =============================================================================


@pytest.mark.unit
async def test_initialize_guard_rejects_methods_before_initialize() -> None:
    """Calling session/new before initialize should return -32002 error."""
    async with _ConnectedStreams() as s:
        assert s.client_writer is not None
        assert s.client_reader is not None
        assert s.server_writer is not None
        assert s.server_reader is not None

        _agent_conn = AgentSideConnection(
            lambda _conn: TestAgent(),
            _AsyncioWriterAdapter(s.server_writer),
            _AsyncioReaderAdapter(s.server_reader),
        )

        # Send session/new without initialize
        req = {"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {"cwd": "/test"}}
        s.client_writer.write((anyenv.dump_json(req) + "\n").encode())
        await s.client_writer.drain()

        line = await asyncio.wait_for(s.client_reader.readline(), timeout=1)
        resp = anyenv.load_json(line)
        assert resp["id"] == 1
        assert "error" in resp
        assert resp["error"]["code"] == -32002
        assert "initialize required" in resp["error"]["message"]


@pytest.mark.unit
async def test_initialize_guard_allows_initialize() -> None:
    """Calling initialize should succeed even before initialization."""
    async with _ConnectedStreams() as s:
        assert s.client_writer is not None
        assert s.client_reader is not None
        assert s.server_writer is not None
        assert s.server_reader is not None

        _agent_conn = AgentSideConnection(
            lambda _conn: TestAgent(),
            _AsyncioWriterAdapter(s.server_writer),
            _AsyncioReaderAdapter(s.server_reader),
        )

        req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}}
        s.client_writer.write((anyenv.dump_json(req) + "\n").encode())
        await s.client_writer.drain()

        line = await asyncio.wait_for(s.client_reader.readline(), timeout=1)
        resp = anyenv.load_json(line)
        assert resp["id"] == 1
        assert "result" in resp
        assert resp["result"]["protocolVersion"] == 1


@pytest.mark.unit
async def test_initialize_guard_allows_methods_after_initialize() -> None:
    """Calling session/new after initialize should succeed."""
    async with _ConnectedStreams() as s:
        assert s.client_writer is not None
        assert s.client_reader is not None
        assert s.server_writer is not None
        assert s.server_reader is not None

        _agent_conn = AgentSideConnection(
            lambda _conn: TestAgent(),
            _AsyncioWriterAdapter(s.server_writer),
            _AsyncioReaderAdapter(s.server_reader),
        )

        # Initialize first
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": 1},
        }
        s.client_writer.write((anyenv.dump_json(init_req) + "\n").encode())
        await s.client_writer.drain()
        line = await asyncio.wait_for(s.client_reader.readline(), timeout=1)
        init_resp = anyenv.load_json(line)
        assert "result" in init_resp

        # Now session/new should work
        req = {"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"cwd": "/test"}}
        s.client_writer.write((anyenv.dump_json(req) + "\n").encode())
        await s.client_writer.drain()

        line = await asyncio.wait_for(s.client_reader.readline(), timeout=1)
        resp = anyenv.load_json(line)
        assert resp["id"] == 2
        assert "result" in resp
        assert resp["result"]["sessionId"] == "test-session-123"


@pytest.mark.unit
async def test_initialize_guard_allows_notifications_before_initialize() -> None:
    """Notifications should be allowed before initialize (no error response)."""
    async with _ConnectedStreams() as s:
        assert s.client_writer is not None
        assert s.client_reader is not None
        assert s.server_writer is not None
        assert s.server_reader is not None

        agent = TestAgent()
        _agent_conn = AgentSideConnection(
            lambda _conn: agent,
            _AsyncioWriterAdapter(s.server_writer),
            _AsyncioReaderAdapter(s.server_reader),
        )

        # Send cancel notification (no id = notification)
        req = {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": "test-123"}}
        s.client_writer.write((anyenv.dump_json(req) + "\n").encode())
        await s.client_writer.drain()

        # Should not receive any response (notifications don't get responses)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(s.client_reader.readline(), timeout=0.2)

        # Allow async dispatch
        for _ in range(50):
            if agent.cancellations:
                break
            await anyio.sleep(0.01)
        assert agent.cancellations == ["test-123"]


# =============================================================================
# Transport type alias coverage
# =============================================================================


@pytest.mark.unit
def test_transport_type_includes_all_transports() -> None:
    """Transport type alias should cover all transport variants."""
    # Verify the type alias exists and accepts all variants
    stdio: Transport = "stdio"
    websocket: Transport = "websocket"
    streamable_http: Transport = "streamable-http"
    stdio_obj: Transport = StdioTransport()
    ws_obj: Transport = WebSocketTransport()
    stream_obj: Transport = StreamTransport(reader=MagicMock(), writer=MagicMock())
    acp_ws_obj: Transport = ACPWebSocketTransport()

    # Just asserting they type-check; runtime values are simple
    assert stdio == "stdio"
    assert websocket == "websocket"
    assert streamable_http == "streamable-http"
    assert isinstance(stdio_obj, StdioTransport)
    assert isinstance(ws_obj, WebSocketTransport)
    assert isinstance(stream_obj, StreamTransport)
    assert isinstance(acp_ws_obj, ACPWebSocketTransport)
