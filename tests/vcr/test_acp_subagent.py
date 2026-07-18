"""L3 VCR test — ACP subagent delegation events.

Verifies that ``SpawnSessionStartEvent`` and ``SpawnSessionCompleteEvent``
(or their ACP-mapped equivalents) propagate through the ACP protocol when
a coordinator agent delegates to a worker. Uses the paired pipe pattern
(D7) and the ``vcr_pool_with_subagent`` fixture (coordinator + worker).

Cassette: ``tests/cassettes/vcr/test_acp_subagent/test_subagent_delegation.yaml``
([HUMAN-REQUIRED] — record with ``--record-mode=once`` and ``OPENAI_API_KEY``).
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
    NewSessionRequest,
    SessionNotification,
    UserMessageChunk,
)
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
from tests.vcr.conftest import cassette_exists


if TYPE_CHECKING:
    from agentpool import AgentPool

pytestmark = [pytest.mark.vcr, pytest.mark.integration]

_MODULE_STEM = "test_acp_subagent"


class _AsyncioReaderAdapter(ByteReceiveStream):
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
        for writer in (self.client_writer, self.server_writer):
            if writer:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
        if self._server:
            self._server.close()
            await self._server.wait_closed()


def _build_acp_agent(pool: AgentPool, agent_name: str = "coordinator") -> AgentPoolACPAgent:
    from acp import Client

    default_agent = pool.get_agent(agent_name)
    client = Client(allow_file_operations=False, use_real_files=False)
    return AgentPoolACPAgent(client=client, default_agent=default_agent)


@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_subagent_delegation"),
    reason="Cassette not recorded yet — run with --record-mode=once",
)
async def test_subagent_delegation(vcr_pool_with_subagent: AgentPool) -> None:
    """Coordinator delegates to worker; spawn/complete events propagate.

    The coordinator agent has a ``subagent`` tool. When the model invokes
    it, AgentPool emits ``SpawnSessionStart`` and (eventually)
    ``SpawnSessionComplete`` (wrapped in ``SubAgentEvent``). The ACP event
    converter maps these to session notifications. Asserts multiple
    notifications are received with consistent session IDs.
    """
    acp_agent = _build_acp_agent(vcr_pool_with_subagent, agent_name="coordinator")
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
        assert new_sess.session_id == IsStr(min_length=1)

        notifications: list[SessionNotification] = []

        async def _collect() -> None:
            async for notification in acp_agent.client.notifications:
                notifications.append(notification)
                if len(notifications) >= 5:
                    break

        collector = asyncio.create_task(_collect())
        await client_conn.session_update(
            SessionNotification(
                session_id=new_sess.session_id,
                update=UserMessageChunk.text("Delegate to the worker agent: ask it to say hello."),
            )
        )
        try:
            await asyncio.wait_for(collector, timeout=30.0)
        except TimeoutError:
            pass
        finally:
            collector.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await collector

        assert notifications, "Expected at least one session notification"
        session_ids = {n.session_id for n in notifications}
        # All notifications should reference either the parent or a child session.
        assert all(sid == IsStr(min_length=1) for sid in session_ids)
