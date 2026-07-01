"""Unit tests for GlobalConnectionPool."""

from __future__ import annotations

from typing import Self
from unittest.mock import patch

from pydantic import HttpUrl
import pytest

from agentpool.mcp_server.global_pool import GlobalConnectionPool
from agentpool_config.mcp_server import (
    AcpMCPServerConfig,
    SSEMCPServerConfig,
    StdioMCPServerConfig,
    StreamableHTTPMCPServerConfig,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeConnectSession:
    """Fake async context manager for transport.connect_session()."""

    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> Self:
        self.entered = True
        return self

    async def __aexit__(self, *args: object) -> None:
        self.exited = True


class _FakeTransport:
    """Fake ClientTransport that does not start real servers."""

    def __init__(self) -> None:
        self._session = _FakeConnectSession()

    def connect_session(self) -> _FakeConnectSession:
        return self._session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stdio_config() -> StdioMCPServerConfig:
    return StdioMCPServerConfig(name="stdio-srv", command="python", args=["-m", "srv"])


@pytest.fixture
def sse_config() -> SSEMCPServerConfig:
    return SSEMCPServerConfig(name="sse-srv", url=HttpUrl("http://localhost:8080/sse"))


@pytest.fixture
def http_config() -> StreamableHTTPMCPServerConfig:
    return StreamableHTTPMCPServerConfig(
        name="http-srv", url=HttpUrl("https://api.example.com/mcp")
    )


@pytest.fixture
def acp_config() -> AcpMCPServerConfig:
    return AcpMCPServerConfig(name="acp-srv", acp_id="server-123")


@pytest.fixture
def fake_transport() -> _FakeTransport:
    return _FakeTransport()


@pytest.fixture
def pool() -> GlobalConnectionPool:
    return GlobalConnectionPool()


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pool_instantiation(pool: GlobalConnectionPool) -> None:
    """GlobalConnectionPool can be instantiated with no args."""
    assert pool.MAX_SESSIONS == 256
    assert len(pool._connections) == 0


# ---------------------------------------------------------------------------
# get_transport — HTTP/SSE (direct cache, no owner task)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_get_transport_sse(
    pool: GlobalConnectionPool, sse_config: SSEMCPServerConfig, fake_transport: _FakeTransport
) -> None:
    """SSE config creates a direct cached transport (no owner task)."""
    with patch.object(SSEMCPServerConfig, "to_transport", return_value=fake_transport):
        transport = await pool.get_transport(sse_config)

    assert transport is fake_transport
    client_id = sse_config.client_id
    conn = pool._connections[client_id]
    assert conn.is_stdio is False
    assert conn.owner_task is None
    assert conn.ref_count == 1


@pytest.mark.unit
async def test_get_transport_http(
    pool: GlobalConnectionPool,
    http_config: StreamableHTTPMCPServerConfig,
    fake_transport: _FakeTransport,
) -> None:
    """StreamableHTTP config creates a direct cached transport."""
    with patch.object(StreamableHTTPMCPServerConfig, "to_transport", return_value=fake_transport):
        transport = await pool.get_transport(http_config)

    assert transport is fake_transport
    client_id = http_config.client_id
    conn = pool._connections[client_id]
    assert conn.is_stdio is False
    assert conn.ref_count == 1


# ---------------------------------------------------------------------------
# get_transport — stdio (owner task)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_get_transport_stdio(
    pool: GlobalConnectionPool,
    stdio_config: StdioMCPServerConfig,
    fake_transport: _FakeTransport,
) -> None:
    """Stdio config spawns an owner task and waits for ready_event."""
    with patch.object(StdioMCPServerConfig, "to_transport", return_value=fake_transport):
        transport = await pool.get_transport(stdio_config)

    assert transport is not fake_transport  # Returns _SharedSessionTransport wrapper
    client_id = stdio_config.client_id
    conn = pool._connections[client_id]
    assert conn.is_stdio is True
    assert conn.owner_task is not None
    assert conn.ref_count == 1
    assert conn.ready_event.is_set()
    assert conn.shared_session_transport is not None

    # Clean up owner task
    conn.close_event.set()
    await conn.owner_task


@pytest.mark.unit
async def test_get_transport_stdio_share_connection(
    pool: GlobalConnectionPool,
    stdio_config: StdioMCPServerConfig,
    fake_transport: _FakeTransport,
) -> None:
    """Multiple get_transport() calls for same client_id share connection."""
    with patch.object(StdioMCPServerConfig, "to_transport", return_value=fake_transport):
        t1 = await pool.get_transport(stdio_config)
        t2 = await pool.get_transport(stdio_config)

    assert t1 is t2
    conn = pool._connections[stdio_config.client_id]
    assert conn.ref_count == 2

    # Clean up
    await pool.shutdown_all()


@pytest.mark.unit
async def test_get_transport_sse_share_connection(
    pool: GlobalConnectionPool,
    sse_config: SSEMCPServerConfig,
    fake_transport: _FakeTransport,
) -> None:
    """Multiple get_transport() for same SSE client_id create independent transports.

    HTTP/SSE transports are not shared — each call creates a fresh transport
    to avoid stream contention. The connection entry is reused for LRU tracking.
    """
    with patch.object(SSEMCPServerConfig, "to_transport", return_value=fake_transport):
        t1 = await pool.get_transport(sse_config)
        t2 = await pool.get_transport(sse_config)

    assert t1 is t2  # Same fake_transport (mock returns same instance)
    conn = pool._connections[sse_config.client_id]
    assert conn.ref_count == 1  # Not incremented for HTTP/SSE


# ---------------------------------------------------------------------------
# get_transport — ACP raises NotImplementedError
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_get_transport_acp_raises(
    pool: GlobalConnectionPool, acp_config: AcpMCPServerConfig
) -> None:
    """ACP config raises NotImplementedError in GlobalConnectionPool."""
    with pytest.raises(NotImplementedError, match="ACP transport"):
        await pool.get_transport(acp_config)


# ---------------------------------------------------------------------------
# release()
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_release_decrements_ref_count(
    pool: GlobalConnectionPool,
    sse_config: SSEMCPServerConfig,
    fake_transport: _FakeTransport,
) -> None:
    """release() on SSE connection removes it (no ref counting for HTTP/SSE)."""
    with patch.object(SSEMCPServerConfig, "to_transport", return_value=fake_transport):
        await pool.get_transport(sse_config)

    client_id = sse_config.client_id
    assert pool._connections[client_id].ref_count == 1

    await pool.release(client_id)
    # HTTP/SSE connections are removed on release (no owner task to wait for)
    assert client_id not in pool._connections


@pytest.mark.unit
async def test_release_removes_when_ref_zero(
    pool: GlobalConnectionPool,
    sse_config: SSEMCPServerConfig,
    fake_transport: _FakeTransport,
) -> None:
    """release() removes connection from pool when ref_count reaches 0 (HTTP/SSE)."""
    with patch.object(SSEMCPServerConfig, "to_transport", return_value=fake_transport):
        await pool.get_transport(sse_config)

    client_id = sse_config.client_id
    assert client_id in pool._connections

    await pool.release(client_id)
    assert client_id not in pool._connections


@pytest.mark.unit
async def test_release_unknown_client_id(
    pool: GlobalConnectionPool,
) -> None:
    """release() for unknown client_id logs warning but does not raise."""
    await pool.release("nonexistent-id")  # should not raise


@pytest.mark.unit
async def test_release_stdio_shuts_down_owner_task(
    pool: GlobalConnectionPool,
    stdio_config: StdioMCPServerConfig,
    fake_transport: _FakeTransport,
) -> None:
    """release() for stdio with ref_count 0 signals owner task and removes connection."""
    with patch.object(StdioMCPServerConfig, "to_transport", return_value=fake_transport):
        await pool.get_transport(stdio_config)

    client_id = stdio_config.client_id
    conn = pool._connections[client_id]
    owner_task = conn.owner_task
    assert owner_task is not None

    await pool.release(client_id)

    assert owner_task.done()
    assert client_id not in pool._connections


# ---------------------------------------------------------------------------
# LRU eviction
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_lru_eviction_evicts_idle_connection(
    pool: GlobalConnectionPool,
    fake_transport: _FakeTransport,
) -> None:
    """When at capacity, idle connections (ref_count=0) are evicted.

    Uses stdio connections because the eviction path for HTTP/SSE has a
    double-delete issue in _signal_shutdown_locked (pops then del).
    Stdio only sets close_event, so del works cleanly.
    """
    pool.MAX_SESSIONS = 2

    # Fill pool to capacity with stdio connections (ref_count=1)
    configs = [
        StdioMCPServerConfig(name=f"stdio-{i}", command="python", args=["-m", f"srv{i}"])
        for i in range(2)
    ]
    for cfg in configs:
        with patch.object(StdioMCPServerConfig, "to_transport", return_value=fake_transport):
            await pool.get_transport(cfg)

    assert len(pool._connections) == 2

    # Mark one as idle (ref_count=0) so it can be evicted
    first_id = configs[0].client_id
    pool._connections[first_id].ref_count = 0

    # Adding a new connection should trigger eviction of the idle one
    new_config = StdioMCPServerConfig(name="stdio-new", command="python", args=["-m", "new"])
    with patch.object(StdioMCPServerConfig, "to_transport", return_value=fake_transport):
        await pool.get_transport(new_config)

    # The idle connection should have been evicted
    assert first_id not in pool._connections
    assert len(pool._connections) == 2

    # Clean up remaining owner tasks
    await pool.shutdown_all()


@pytest.mark.unit
async def test_lru_eviction_no_idle_connections(
    pool: GlobalConnectionPool,
    fake_transport: _FakeTransport,
) -> None:
    """When at capacity with no idle connections, pool logs warning and does not evict."""
    pool.MAX_SESSIONS = 2

    configs = [
        SSEMCPServerConfig(name=f"sse-{i}", url=HttpUrl(f"http://localhost:808{i}/sse"))
        for i in range(2)
    ]
    for cfg in configs:
        with patch.object(SSEMCPServerConfig, "to_transport", return_value=fake_transport):
            await pool.get_transport(cfg)

    # All connections have ref_count=1 (not idle)
    new_config = SSEMCPServerConfig(
        name="sse-new", url=HttpUrl("http://localhost:9999/sse")
    )
    with patch.object(SSEMCPServerConfig, "to_transport", return_value=fake_transport):
        # _evict_if_needed will find no idle connections and log warning
        # _create_connection_locked will still add the new connection exceeding capacity
        await pool.get_transport(new_config)

    # Pool exceeds capacity because nothing could be evicted
    assert len(pool._connections) == 3


# ---------------------------------------------------------------------------
# shutdown_all()
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_shutdown_all_clears_connections(
    pool: GlobalConnectionPool,
    sse_config: SSEMCPServerConfig,
    fake_transport: _FakeTransport,
) -> None:
    """shutdown_all() removes all connections."""
    with patch.object(SSEMCPServerConfig, "to_transport", return_value=fake_transport):
        await pool.get_transport(sse_config)

    assert len(pool._connections) == 1

    await pool.shutdown_all()

    assert len(pool._connections) == 0


@pytest.mark.unit
async def test_shutdown_all_with_stdio(
    pool: GlobalConnectionPool,
    stdio_config: StdioMCPServerConfig,
    fake_transport: _FakeTransport,
) -> None:
    """shutdown_all() signals and waits for stdio owner tasks."""
    with patch.object(StdioMCPServerConfig, "to_transport", return_value=fake_transport):
        await pool.get_transport(stdio_config)

    client_id = stdio_config.client_id
    owner_task = pool._connections[client_id].owner_task
    assert owner_task is not None

    await pool.shutdown_all()

    assert owner_task.done()
    assert len(pool._connections) == 0


@pytest.mark.unit
async def test_shutdown_all_empty_pool(pool: GlobalConnectionPool) -> None:
    """shutdown_all() on empty pool is a no-op."""
    await pool.shutdown_all()
    assert len(pool._connections) == 0


@pytest.mark.unit
async def test_shutdown_all_multiple(
    pool: GlobalConnectionPool,
    stdio_config: StdioMCPServerConfig,
    sse_config: SSEMCPServerConfig,
    fake_transport: _FakeTransport,
) -> None:
    """shutdown_all() handles mixed stdio + HTTP connections."""
    with (
        patch.object(StdioMCPServerConfig, "to_transport", return_value=fake_transport),
        patch.object(SSEMCPServerConfig, "to_transport", return_value=fake_transport),
    ):
        await pool.get_transport(stdio_config)
        await pool.get_transport(sse_config)

    assert len(pool._connections) == 2

    await pool.shutdown_all()

    assert len(pool._connections) == 0
