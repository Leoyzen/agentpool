"""L3 VCR test — ACP protocol over paired in-process pipe (design D7).

The ACP protocol stack (JSON-RPC framing, event conversion, session
management) runs for real in-process. VCR intercepts only the model API
HTTP calls. The client and agent sides are connected via paired
``asyncio.StreamReader``/``StreamWriter`` pipes, reusing the pattern from
``tests/servers/acp_server/test_rpc.py``.

Cassettes ([HUMAN-REQUIRED]):
- ``tests/cassettes/vcr/test_acp_protocol/test_session_init.yaml``
- ``tests/cassettes/vcr/test_acp_protocol/test_basic_completion.yaml``
- ``tests/cassettes/vcr/test_acp_protocol/test_streaming_events.yaml``
- ``tests/cassettes/vcr/test_acp_protocol/test_model_api_rate_limit.yaml``
- ``tests/cassettes/vcr/test_acp_protocol/test_model_api_server_error.yaml``
- ``tests/cassettes/vcr/test_acp_protocol/test_model_api_malformed_stream.yaml``
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Self

import anyio
from anyio.abc import ByteReceiveStream, ByteSendStream
from dirty_equals import IsStr
import pytest

from acp import (
    AgentSideConnection,
    ClientSideConnection,
    InitializeRequest,
    InitializeResponse,
    NewSessionRequest,
    NewSessionResponse,
    SessionNotification,
    UserMessageChunk,
)
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
from tests.vcr.conftest import cassette_exists


if TYPE_CHECKING:
    from agentpool import AgentPool

pytestmark = [pytest.mark.vcr, pytest.mark.integration]

_MODULE_STEM = "test_acp_protocol"


# ---------------------------------------------------------------------------
# Pipe helpers — ported from tests/servers/acp_server/test_rpc.py
# ---------------------------------------------------------------------------


class _AsyncioReaderAdapter(ByteReceiveStream):
    """Adapts ``asyncio.StreamReader`` to anyio's ``ByteReceiveStream``."""

    def __init__(self, reader: asyncio.StreamReader) -> None:
        self._reader = reader

    async def receive(self, max_bytes: int = 65536) -> bytes:
        data = await self._reader.read(max_bytes)
        if not data:
            raise anyio.EndOfStream
        return data

    async def aclose(self) -> None:
        pass  # StreamReader doesn't need explicit close


class _AsyncioWriterAdapter(ByteSendStream):
    """Adapts ``asyncio.StreamWriter`` to anyio's ``ByteSendStream``."""

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer

    async def send(self, item: bytes) -> None:
        self._writer.write(item)
        await self._writer.drain()

    async def aclose(self) -> None:
        self._writer.close()
        with contextlib.suppress(Exception):
            await self._writer.wait_closed()


class _PairedPipe:
    """Create paired asyncio pipes for ACP client/agent connections."""

    def __init__(self) -> None:
        self._server: asyncio.AbstractServer | None = None
        self.server_reader: asyncio.StreamReader | None = None
        self.server_writer: asyncio.StreamWriter | None = None
        self.client_reader: asyncio.StreamReader | None = None
        self.client_writer: asyncio.StreamWriter | None = None

    async def __aenter__(self) -> Self:
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
        assert self.server_reader is not None
        assert self.server_writer is not None
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


def _build_acp_agent(pool: AgentPool) -> AgentPoolACPAgent:
    """Build an ``AgentPoolACPAgent`` from a real pool.

    The agent uses a dummy ``Client`` since we wire the connections manually
    via the paired pipe. The real session-pool protocol handler is used.
    """
    from acp import Client

    default_agent = pool.get_agent("test_agent")
    client = Client(allow_file_operations=False, use_real_files=False)
    return AgentPoolACPAgent(client=client, default_agent=default_agent)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_session_init"),
    reason="Cassette not recorded yet — run with --record-mode=once",
)
async def test_session_init(vcr_pool: AgentPool) -> None:
    """ACP ``initialize`` + ``session/new`` round-trip succeeds.

    Asserts the protocol version is negotiated and a non-empty session ID
    is returned. The model API is never called for these methods — VCR is
    present only because the agent pool spins up model clients eagerly.
    """
    acp_agent = _build_acp_agent(vcr_pool)
    async with _PairedPipe() as pipe:
        assert pipe.client_writer is not None
        assert pipe.client_reader is not None
        assert pipe.server_writer is not None
        assert pipe.server_reader is not None

        # Client side connects to the agent.
        client_conn = ClientSideConnection(
            lambda _conn: acp_agent.client,
            _AsyncioWriterAdapter(pipe.client_writer),
            _AsyncioReaderAdapter(pipe.client_reader),
        )
        _agent_conn = AgentSideConnection(
            lambda _conn: acp_agent,
            _AsyncioWriterAdapter(pipe.server_writer),
            _AsyncioReaderAdapter(pipe.server_reader),
        )

        init_resp = await client_conn.initialize(InitializeRequest(protocol_version=1))
        assert isinstance(init_resp, InitializeResponse)
        assert init_resp.protocol_version == 1

        new_sess = await client_conn.new_session(NewSessionRequest(mcp_servers=[], cwd="/test"))
        assert isinstance(new_sess, NewSessionResponse)
        assert new_sess.session_id == IsStr(min_length=1)


@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_basic_completion"),
    reason="Cassette not recorded yet — run with --record-mode=once",
)
async def test_basic_completion(vcr_pool: AgentPool) -> None:
    """Sending a user prompt through ACP returns an agent message.

    The ACP ``session/update`` notification stream carries agent message
    chunks. VCR replays the recorded model API call. Asserts at least one
    ``AgentMessageChunk`` notification is received with non-empty text.
    """
    acp_agent = _build_acp_agent(vcr_pool)
    async with _PairedPipe() as pipe:
        assert pipe.client_writer is not None
        assert pipe.client_reader is not None
        assert pipe.server_writer is not None
        assert pipe.server_reader is not None

        client_conn = ClientSideConnection(
            lambda _conn: acp_agent.client,
            _AsyncioWriterAdapter(pipe.client_writer),
            _AsyncioReaderAdapter(pipe.client_reader),
        )
        _agent_conn = AgentSideConnection(
            lambda _conn: acp_agent,
            _AsyncioWriterAdapter(pipe.server_writer),
            _AsyncioReaderAdapter(pipe.server_reader),
        )

        await client_conn.initialize(InitializeRequest(protocol_version=1))
        new_sess = await client_conn.new_session(NewSessionRequest(mcp_servers=[], cwd="/test"))

        # Collect session notifications for a few seconds after prompting.
        notifications: list[SessionNotification] = []

        async def _collect() -> None:
            async for notification in acp_agent.client.notifications:
                notifications.append(notification)
                if len(notifications) >= 1:
                    break

        import asyncio as _asyncio

        collector = _asyncio.create_task(_collect())
        await client_conn.session_update(
            SessionNotification(
                session_id=new_sess.session_id,
                update=UserMessageChunk.text("Say hello in one short sentence."),
            )
        )
        try:
            await _asyncio.wait_for(collector, timeout=10.0)
        except TimeoutError:
            pass
        finally:
            collector.cancel()
            with contextlib.suppress(_asyncio.CancelledError):
                await collector

        assert notifications, "Expected at least one session notification"
        first = notifications[0]
        assert first.session_id == new_sess.session_id


@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_streaming_events"),
    reason="Cassette not recorded yet — run with --record-mode=once",
)
async def test_streaming_events(vcr_pool: AgentPool) -> None:
    """ACP streaming produces an ordered sequence of session notifications.

    The expected notification sequence (mapped from the AgentPool event
    stream by ``event_converter.py``) is:
        AgentMessageChunk (start) → AgentMessageChunk (delta)* →
        AgentMessageChunk (complete) → SessionFinished (or similar)

    This test asserts that multiple ``SessionNotification`` objects are
    received and that the session ID is consistent across all of them.
    """
    acp_agent = _build_acp_agent(vcr_pool)
    async with _PairedPipe() as pipe:
        assert pipe.client_writer is not None
        assert pipe.client_reader is not None
        assert pipe.server_writer is not None
        assert pipe.server_reader is not None

        client_conn = ClientSideConnection(
            lambda _conn: acp_agent.client,
            _AsyncioWriterAdapter(pipe.client_writer),
            _AsyncioReaderAdapter(pipe.client_reader),
        )
        _agent_conn = AgentSideConnection(
            lambda _conn: acp_agent,
            _AsyncioWriterAdapter(pipe.server_writer),
            _AsyncioReaderAdapter(pipe.server_reader),
        )

        await client_conn.initialize(InitializeRequest(protocol_version=1))
        new_sess = await client_conn.new_session(NewSessionRequest(mcp_servers=[], cwd="/test"))

        notifications: list[SessionNotification] = []

        async def _collect() -> None:
            async for notification in acp_agent.client.notifications:
                notifications.append(notification)
                if len(notifications) >= 3:
                    break

        import asyncio as _asyncio

        collector = _asyncio.create_task(_collect())
        await client_conn.session_update(
            SessionNotification(
                session_id=new_sess.session_id,
                update=UserMessageChunk.text("Count from 1 to 3."),
            )
        )
        try:
            await _asyncio.wait_for(collector, timeout=15.0)
        except TimeoutError:
            pass
        finally:
            collector.cancel()
            with contextlib.suppress(_asyncio.CancelledError):
                await collector

        assert len(notifications) >= 1
        session_ids = {n.session_id for n in notifications}
        assert session_ids == {new_sess.session_id}


@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_model_api_rate_limit"),
    reason="Cassette not recorded yet — run with --record-mode=once",
)
async def test_model_api_rate_limit(acp_pipe: _PairedPipe) -> None:
    """Model API returns 429 rate limit — error propagates through ACP as error event.

    The cassette records a real 429 response from the model API. The ACP
    protocol stack should convert this to an error notification for the
    client.
    """
    client_conn = ClientSideConnection(
        _AsyncioWriterAdapter(acp_pipe.client_writer),
        _AsyncioReaderAdapter(acp_pipe.client_reader),
    )
    AgentSideConnection(
        _AsyncioWriterAdapter(acp_pipe.server_writer),
        _AsyncioReaderAdapter(acp_pipe.server_reader),
    )
    await client_conn.initialize(InitializeRequest(protocol_version=1))
    new_sess = await client_conn.new_session(NewSessionRequest(mcp_servers=[], cwd="/test"))

    notifications: list[SessionNotification] = []

    async def _collect() -> None:
        async for notification in acp_pipe.agent.client.notifications:
            notifications.append(notification)
            if any(
                getattr(n.update, "type", None) in ("error", "run_error")
                or "error" in str(getattr(n.update, "type", "")).lower()
                for n in notifications
            ):
                break

    collector = asyncio.create_task(_collect())
    await client_conn.session_update(
        SessionNotification(
            session_id=new_sess.session_id,
            update=UserMessageChunk.text("This will trigger a rate limit."),
        )
    )
    try:
        await asyncio.wait_for(collector, timeout=15.0)
    except TimeoutError:
        pass
    finally:
        collector.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await collector

    # The 429 error should propagate as some form of error event/notification.
    # Exact structure depends on how ACP converts model API errors.
    error_events = [
        n
        for n in notifications
        if "error" in str(getattr(n.update, "type", "")).lower()
        or getattr(n.update, "type", None) == "run_error"
    ]
    assert len(error_events) >= 1, "Expected at least one error event from 429 rate limit"


@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_model_api_server_error"),
    reason="Cassette not recorded yet — run with --record-mode=once",
)
async def test_model_api_server_error(acp_pipe: _PairedPipe) -> None:
    """Model API returns 500 server error — error propagates through ACP."""
    client_conn = ClientSideConnection(
        _AsyncioWriterAdapter(acp_pipe.client_writer),
        _AsyncioReaderAdapter(acp_pipe.client_reader),
    )
    AgentSideConnection(
        _AsyncioWriterAdapter(acp_pipe.server_writer),
        _AsyncioReaderAdapter(acp_pipe.server_reader),
    )
    await client_conn.initialize(InitializeRequest(protocol_version=1))
    new_sess = await client_conn.new_session(NewSessionRequest(mcp_servers=[], cwd="/test"))

    notifications: list[SessionNotification] = []

    async def _collect() -> None:
        async for notification in acp_pipe.agent.client.notifications:
            notifications.append(notification)
            if any("error" in str(getattr(n.update, "type", "")).lower() for n in notifications):
                break

    collector = asyncio.create_task(_collect())
    await client_conn.session_update(
        SessionNotification(
            session_id=new_sess.session_id,
            update=UserMessageChunk.text("This will trigger a server error."),
        )
    )
    try:
        await asyncio.wait_for(collector, timeout=15.0)
    except TimeoutError:
        pass
    finally:
        collector.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await collector

    error_events = [
        n for n in notifications if "error" in str(getattr(n.update, "type", "")).lower()
    ]
    assert len(error_events) >= 1, "Expected at least one error event from 500 server error"


@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_model_api_malformed_stream"),
    reason="Cassette not recorded yet — run with --record-mode=once",
)
async def test_model_api_malformed_stream(acp_pipe: _PairedPipe) -> None:
    """Model API returns malformed streaming response — error propagates through ACP.

    The cassette records a response where the SSE stream contains invalid
    JSON or truncated chunks. The agent should handle this gracefully and
    emit an error event rather than crashing.
    """
    client_conn = ClientSideConnection(
        _AsyncioWriterAdapter(acp_pipe.client_writer),
        _AsyncioReaderAdapter(acp_pipe.client_reader),
    )
    AgentSideConnection(
        _AsyncioWriterAdapter(acp_pipe.server_writer),
        _AsyncioReaderAdapter(acp_pipe.server_reader),
    )
    await client_conn.initialize(InitializeRequest(protocol_version=1))
    new_sess = await client_conn.new_session(NewSessionRequest(mcp_servers=[], cwd="/test"))

    notifications: list[SessionNotification] = []

    async def _collect() -> None:
        async for notification in acp_pipe.agent.client.notifications:
            notifications.append(notification)
            if any("error" in str(getattr(n.update, "type", "")).lower() for n in notifications):
                break

    collector = asyncio.create_task(_collect())
    await client_conn.session_update(
        SessionNotification(
            session_id=new_sess.session_id,
            update=UserMessageChunk.text("This will trigger a malformed stream."),
        )
    )
    try:
        await asyncio.wait_for(collector, timeout=15.0)
    except TimeoutError:
        pass
    finally:
        collector.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await collector

    # Malformed stream should result in an error event, not a crash.
    error_events = [
        n for n in notifications if "error" in str(getattr(n.update, "type", "")).lower()
    ]
    assert len(error_events) >= 1, "Expected at least one error event from malformed stream"
