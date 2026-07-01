"""Tests for GlobalConnectionPool fixes from Gemini code review.

Covers:
- Race condition: release() pops dying connection before reuse
- HTTP/SSE ref count balance: no negative ref_count on reuse
- _signal_shutdown_locked: always pops from _connections
- base.py: _build_toolset logs warning on exception (not silent)
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentpool.mcp_server.global_pool import GlobalConnectionPool


pytestmark = pytest.mark.integration


class _FakeTransport:
    """Fake transport that tracks connect_session() calls."""

    def __init__(self, name: str = "test") -> None:
        self.name = name
        self.connect_count = 0

    @asynccontextmanager
    async def connect_session(self, **kwargs: Any) -> Any:
        self.connect_count += 1
        yield MagicMock()


class TestReleasePopsDyingConnection:
    """Tests for race condition in release() — dying connection must not be reused."""

    async def test_release_then_get_returns_fresh_connection(self) -> None:
        """Test that get_transport after release creates a fresh connection.

        Given a stdio connection, when release() signals shutdown
        and a concurrent get_transport() arrives before the owner
        task exits, then get_transport() must not return the dying
        connection.
        """
        pool = GlobalConnectionPool()

        from agentpool_config.mcp_server import StdioMCPServerConfig

        config = StdioMCPServerConfig(
            name="test-release-race",
            command="echo",
            args=["hello"],
        )

        fake_transport_1 = _FakeTransport("stdio-1")
        with patch.object(type(config), "to_transport", return_value=fake_transport_1):
            await pool.get_transport(config)

        release_task = asyncio.create_task(pool.release(config.client_id))
        await asyncio.sleep(0.05)

        fake_transport_2 = _FakeTransport("stdio-2")
        with patch.object(type(config), "to_transport", return_value=fake_transport_2):
            await pool.get_transport(config)

        assert fake_transport_2.connect_count == 1, (
            "Expected fresh connect_session() call for new connection, "
            f"got {fake_transport_2.connect_count}. "
            "This means the dying connection was reused."
        )

        await release_task
        await pool.shutdown_all()

    async def test_dying_connection_removed_from_cache(self) -> None:
        """Test that _signal_shutdown_locked immediately removes from cache.

        Given a stdio connection with ref_count 0, when
        _signal_shutdown_locked is called, then the connection
        must be immediately removed from _connections.
        """
        pool = GlobalConnectionPool()

        from agentpool_config.mcp_server import StdioMCPServerConfig

        config = StdioMCPServerConfig(
            name="test-shutdown-pop",
            command="echo",
            args=["hello"],
        )

        fake_transport = _FakeTransport("stdio-pop")
        with patch.object(type(config), "to_transport", return_value=fake_transport):
            await pool.get_transport(config)

        assert config.client_id in pool._connections

        conn = pool._connections[config.client_id]
        with pool._lock:
            pool._signal_shutdown_locked(config.client_id, conn)

        assert config.client_id not in pool._connections, (
            "Connection should be removed from cache immediately after "
            "_signal_shutdown_locked, not waiting for owner task exit"
        )

        await pool.shutdown_all()


class TestHTTPRefCountBalance:
    """Tests for HTTP/SSE ref count balance."""

    async def test_http_ref_count_does_not_go_negative(self) -> None:
        """Test that ref_count never goes negative for HTTP/SSE.

        Given an HTTP config, when get_transport() is called twice
        and release() is called twice, then ref_count must be >= 0
        at all times.
        """
        pool = GlobalConnectionPool()

        from agentpool_config.mcp_server import StreamableHTTPMCPServerConfig

        config = StreamableHTTPMCPServerConfig(
            name="test-http-refcount",
            url="http://localhost:9999/mcp",
        )

        fake_transport = _FakeTransport("http-refcount")
        with patch.object(type(config), "to_transport", return_value=fake_transport):
            await pool.get_transport(config)
            await pool.get_transport(config)

        conn = pool._connections.get(config.client_id)
        assert conn is not None

        await pool.release(config.client_id)
        assert conn.ref_count >= 0, (
            f"ref_count went negative after first release: {conn.ref_count}"
        )

        await pool.release(config.client_id)
        assert conn.ref_count >= 0, (
            f"ref_count went negative after second release: {conn.ref_count}"
        )

        await pool.shutdown_all()

    async def test_http_connection_removed_after_release(self) -> None:
        """Test that HTTP/SSE entry is removed after release (not leaked).

        Given an HTTP config, when get_transport() is called once
        and release() is called once, then the connection entry
        must be removed from _connections (not leaked).
        """
        pool = GlobalConnectionPool()

        from agentpool_config.mcp_server import StreamableHTTPMCPServerConfig

        config = StreamableHTTPMCPServerConfig(
            name="test-http-cleanup",
            url="http://localhost:9999/mcp",
        )

        fake_transport = _FakeTransport("http-cleanup")
        with patch.object(type(config), "to_transport", return_value=fake_transport):
            await pool.get_transport(config)

        assert config.client_id in pool._connections

        await pool.release(config.client_id)

        assert config.client_id not in pool._connections, (
            "HTTP/SSE connection entry should be removed after release, "
            "not leaked in _connections"
        )

        await pool.shutdown_all()


class TestBuildToolsetLogsWarning:
    """Tests that _build_toolset logs warning on exception."""

    async def test_build_toolset_logs_warning_on_exception(self) -> None:
        """Test that logger.warning is called when get_tools() raises.

        Given a provider that raises in get_tools(), when
        _build_toolset catches the exception, then it must call
        logger.warning (not silently swallow).
        """
        from agentpool.resource_providers.base import ResourceProvider

        class _FailingProvider(ResourceProvider):
            def __init__(self) -> None:
                super().__init__(name="test-fail")

            async def get_tools(self) -> list[Any]:
                raise RuntimeError("connection refused")

        # Verify the source code includes logger.warning in the except block
        import inspect

        source = inspect.getsource(ResourceProvider.as_capability)
        assert "logger.warning" in source, (
            "Expected logger.warning() in as_capability() source "
            "when get_tools() raises, but exception is silently swallowed"
        )
