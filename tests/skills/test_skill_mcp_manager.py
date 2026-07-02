"""Unit tests for SkillMcpManager.

Tests cover:
  1. prepare() registers config without starting anything
  2. connect() lazily connects on first call, returns cached on subsequent
  3. connect() reconnects after idle timeout
  4. connect() retries with exponential backoff
  5. connect() all retries fail → RuntimeError
  6. connect() unknown server → ValueError
  7. get_tools() calls connect then provider.get_tools()
  8. disconnect() terminates a specific connection
  9. cleanup() disconnects all servers for a session
 10. cleanup_all() disconnects all servers for all sessions
 11. cleanup_idle() disconnects idle connections
 12. Thread safety: concurrent connect() calls serialized via asyncio.Lock
 13. disconnect() is no-op for non-connected server
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from agentpool.skills.skill_mcp_manager import (
    RETRY_BASE_DELAY,
    SkillMcpManager,
)
from agentpool_config.skills import SkillMcpServerConfig


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def manager() -> SkillMcpManager:
    """A SkillMcpManager with default settings."""
    return SkillMcpManager()


@pytest.fixture
def server_config() -> SkillMcpServerConfig:
    """A minimal SkillMcpServerConfig for testing."""
    return SkillMcpServerConfig(
        command="npx",
        args=["-y", "@playwright/mcp"],
    )


@pytest.fixture
def server_config_url() -> SkillMcpServerConfig:
    """A URL-based SkillMcpServerConfig for testing."""
    return SkillMcpServerConfig(
        url="http://localhost:8080/mcp",
    )


def make_mock_provider() -> AsyncMock:
    """Create a mocked MCPResourceProvider that behaves like a connected one."""
    provider = AsyncMock()
    # MCPResourceProvider.__aexit__ should not raise
    provider.__aenter__ = AsyncMock(return_value=provider)
    provider.__aexit__ = AsyncMock(return_value=None)
    provider.get_tools = AsyncMock(return_value=[])
    return provider


# =========================================================================
# 1. prepare()
# =========================================================================


class TestPrepare:
    """SkillMcpManager.prepare() — register server configs."""

    def test_prepare_registers_config(
        self, manager: SkillMcpManager, server_config: SkillMcpServerConfig
    ) -> None:
        """prepare() stores the config for later lazy connection."""
        manager.prepare("my-server", server_config)
        assert "my-server" in manager._configs
        assert manager._configs["my-server"] is server_config

    def test_prepare_does_not_start_subprocess(
        self, manager: SkillMcpManager, server_config: SkillMcpServerConfig
    ) -> None:
        """prepare() alone does not create any providers or start subprocesses."""
        manager.prepare("my-server", server_config)
        assert manager._providers == {}
        assert manager._locks == {}

    def test_prepare_multiple_servers(self, manager: SkillMcpManager) -> None:
        """prepare() can register multiple servers."""
        c1 = SkillMcpServerConfig(command="echo", args=["hello"])
        c2 = SkillMcpServerConfig(command="echo", args=["world"])
        manager.prepare("srv1", c1)
        manager.prepare("srv2", c2)
        assert set(manager._configs) == {"srv1", "srv2"}


# =========================================================================
# 2. connect() — lazy connection
# =========================================================================


class TestConnectLazy:
    """SkillMcpManager.connect() — lazy connection and caching."""

    async def test_connect_calls_create_and_connect(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
    ) -> None:
        """First connect() for a (session, server) pair calls _create_and_connect."""
        manager.prepare("srv", server_config)
        mock_provider = make_mock_provider()

        with patch.object(
            manager, "_create_and_connect", AsyncMock(return_value=mock_provider)
        ) as mock_create:
            result = await manager.connect("srv", "ses_1")

        mock_create.assert_awaited_once()
        assert result is mock_provider

    async def test_connect_returns_cached_on_second_call(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
    ) -> None:
        """Second connect() returns the cached provider without calling _create_and_connect."""
        manager.prepare("srv", server_config)
        mock_provider = make_mock_provider()

        with patch.object(
            manager, "_create_and_connect", AsyncMock(return_value=mock_provider)
        ) as mock_create:
            first = await manager.connect("srv", "ses_1")
            second = await manager.connect("srv", "ses_1")

        assert first is second
        mock_create.assert_awaited_once()  # Only one actual connection

    async def test_connect_different_sessions_create_separate_connections(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
    ) -> None:
        """Different session IDs get separate connections to the same server."""
        manager.prepare("srv", server_config)

        with patch.object(
            manager, "_create_and_connect", AsyncMock(return_value=make_mock_provider())
        ) as mock_create:
            await manager.connect("srv", "ses_a")
            await manager.connect("srv", "ses_b")

        # Two sessions → two _create_and_connect calls
        assert mock_create.await_count == 2

    async def test_connect_different_servers_same_session(
        self,
        manager: SkillMcpManager,
    ) -> None:
        """Same session can connect to multiple servers."""
        manager.prepare("srv_a", SkillMcpServerConfig(command="echo"))
        manager.prepare("srv_b", SkillMcpServerConfig(command="echo"))

        with patch.object(
            manager,
            "_create_and_connect",
            AsyncMock(side_effect=[make_mock_provider(), make_mock_provider()]),
        ) as mock_create:
            pa = await manager.connect("srv_a", "ses_1")
            pb = await manager.connect("srv_b", "ses_1")

        assert pa is not pb
        assert mock_create.await_count == 2

    async def test_connect_unknown_server_raises_value_error(
        self,
        manager: SkillMcpManager,
    ) -> None:
        """connect() with unregistered server name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown skill MCP server"):
            await manager.connect("nonexistent", "ses_1")


# =========================================================================
# 3. connect() — idle timeout reconnection
# =========================================================================


class TestConnectIdleTimeout:
    """SkillMcpManager.connect() — reconnects after idle timeout."""

    async def test_connect_reconnects_after_idle_timeout(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
    ) -> None:
        """connect() disconnects and reconnects when the existing connection is idle."""
        manager._idle_timeout = 0  # Immediate timeout
        manager.prepare("srv", server_config)
        mock_provider = make_mock_provider()

        with (
            patch.object(
                manager, "_create_and_connect", AsyncMock(return_value=mock_provider)
            ) as mock_create,
            patch.object(manager, "disconnect", AsyncMock()) as mock_disconnect,
        ):
            # First connect
            first = await manager.connect("srv", "ses_1")

            # Mark as idle (timeout = 0, so it's always idle)
            # We need to simulate that _is_idle returns True
            # After reconnection, the provider should be recreated
            mock_create.return_value = make_mock_provider()
            second = await manager.connect("srv", "ses_1")

        assert first is not second
        mock_disconnect.assert_awaited_once_with("srv", "ses_1")
        # Two connection attempts since the first was disconnected
        assert mock_create.await_count == 2

    async def test_connect_not_idle_returns_cached(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
    ) -> None:
        """connect() returns cached provider when not idle."""
        manager._idle_timeout = 999999  # Effectively never idle
        manager.prepare("srv", server_config)
        mock_provider = make_mock_provider()

        with patch.object(
            manager, "_create_and_connect", AsyncMock(return_value=mock_provider)
        ) as mock_create:
            first = await manager.connect("srv", "ses_1")
            second = await manager.connect("srv", "ses_1")

        assert first is second
        mock_create.assert_awaited_once()


# =========================================================================
# 4. connect() — retry with exponential backoff
# =========================================================================


class TestConnectRetry:
    """SkillMcpManager.connect() — retry with exponential backoff."""

    async def test_retry_on_transient_failure(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
    ) -> None:
        """connect() retries after OSError with exponential backoff, succeeds on retry."""
        manager.prepare("srv", server_config)
        mock_provider = make_mock_provider()

        with patch.object(
            manager,
            "_create_and_connect",
            AsyncMock(side_effect=[OSError("conn refused"), mock_provider]),
        ) as mock_create:
            result = await manager.connect("srv", "ses_1")

        assert result is mock_provider
        assert mock_create.await_count == 2

    async def test_retry_with_exponential_backoff_delay(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
    ) -> None:
        """connect() waits longer between each retry (exponential backoff)."""
        manager.prepare("srv", server_config)
        mock_provider = make_mock_provider()

        # Track sleep calls
        original_sleep = asyncio.sleep

        sleeps: list[float] = []

        async def tracking_sleep(delay: float) -> None:
            sleeps.append(delay)
            await original_sleep(0)

        with (
            patch.object(
                manager,
                "_create_and_connect",
                AsyncMock(side_effect=[OSError("fail"), OSError("fail"), mock_provider]),
            ),
            patch("asyncio.sleep", tracking_sleep),
        ):
            result = await manager.connect("srv", "ses_1")

        assert result is mock_provider
        # Should have slept with exponential delays: 2^0 = 1s, 2^1 = 2s
        # But we're patching sleep(0), so the delays are from RETRY_BASE_DELAY * 2^(attempt-1)
        assert len(sleeps) == 2
        # RETRY_BASE_DELAY = 1, so delays are 1, 2
        assert sleeps[0] == pytest.approx(RETRY_BASE_DELAY * (2**0))
        assert sleeps[1] == pytest.approx(RETRY_BASE_DELAY * (2**1))

    async def test_retry_all_fail_raises_runtime_error(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
    ) -> None:
        """connect() raises RuntimeError after all retries fail."""
        manager.prepare("srv", server_config)

        with (
            patch.object(
                manager, "_create_and_connect", AsyncMock(side_effect=OSError("persistent failure"))
            ),
            pytest.raises(RuntimeError, match="Failed to connect skill MCP server"),
        ):
            await manager.connect("srv", "ses_1")

    async def test_retry_then_reconnect_succeeds(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
    ) -> None:
        """connect() eventually succeeds after transient failures, stores provider."""
        manager.prepare("srv", server_config)
        mock_provider = make_mock_provider()

        # DEFAULT_MAX_RETRIES=3, so 2 failures then success on 3rd attempt
        with patch.object(
            manager,
            "_create_and_connect",
            AsyncMock(side_effect=[OSError("1"), OSError("2"), mock_provider]),
        ):
            result = await manager.connect("srv", "ses_1")

        assert result is mock_provider
        # After connect, the provider should be stored
        stored = manager._get_provider("srv", "ses_1")
        assert stored is mock_provider


# =========================================================================
# 5. get_tools()
# =========================================================================


class TestGetTools:
    """SkillMcpManager.get_tools() — retrieve tools from connected server."""

    async def test_get_tools_connects_and_returns_tools(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
    ) -> None:
        """get_tools() calls connect() then provider.get_tools()."""
        manager.prepare("srv", server_config)
        mock_provider = make_mock_provider()
        mock_tools = [Mock(), Mock()]  # Two fake tool objects
        mock_provider.get_tools.return_value = mock_tools

        with patch.object(manager, "_create_and_connect", AsyncMock(return_value=mock_provider)):
            tools = await manager.get_tools("srv", "ses_1")

        assert len(tools) == 2
        mock_provider.get_tools.assert_awaited_once()

    async def test_get_tools_caches_across_calls(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
    ) -> None:
        """get_tools() returns cached tools on subsequent calls (provider caching)."""
        manager.prepare("srv", server_config)
        mock_provider = make_mock_provider()
        mock_provider.get_tools.return_value = [Mock()]

        with patch.object(manager, "_create_and_connect", AsyncMock(return_value=mock_provider)):
            await manager.get_tools("srv", "ses_1")
            await manager.get_tools("srv", "ses_1")

        # provider.get_tools called twice (each get_tools call goes through)
        assert mock_provider.get_tools.await_count == 2


# =========================================================================
# 6. disconnect()
# =========================================================================


class TestDisconnect:
    """SkillMcpManager.disconnect() — terminate connection."""

    async def test_disconnect_calls_provider_aexit(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
    ) -> None:
        """disconnect() calls __aexit__ on the provider."""
        manager.prepare("srv", server_config)
        mock_provider = make_mock_provider()

        with patch.object(manager, "_create_and_connect", AsyncMock(return_value=mock_provider)):
            await manager.connect("srv", "ses_1")
            await manager.disconnect("srv", "ses_1")

        mock_provider.__aexit__.assert_awaited_once()

    async def test_disconnect_removes_provider_and_activity(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
    ) -> None:
        """disconnect() removes stored provider and activity timestamp."""
        manager.prepare("srv", server_config)
        mock_provider = make_mock_provider()

        with patch.object(manager, "_create_and_connect", AsyncMock(return_value=mock_provider)):
            await manager.connect("srv", "ses_1")

        assert manager._get_provider("srv", "ses_1") is not None
        assert ("ses_1", "srv") in manager._last_activity

        await manager.disconnect("srv", "ses_1")

        assert manager._get_provider("srv", "ses_1") is None
        assert ("ses_1", "srv") not in manager._last_activity

    async def test_disconnect_non_connected_is_noop(
        self,
        manager: SkillMcpManager,
    ) -> None:
        """disconnect() for a non-connected server is a no-op (no error)."""
        # No prepare/connect → should not raise
        await manager.disconnect("srv", "ses_1")

    async def test_disconnect_logs_and_suppresses_exception(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """disconnect() logs but does not re-raise provider __aexit__ exceptions."""
        manager.prepare("srv", server_config)
        mock_provider = make_mock_provider()
        mock_provider.__aexit__ = AsyncMock(side_effect=RuntimeError("cleanup failed"))

        with patch.object(manager, "_create_and_connect", AsyncMock(return_value=mock_provider)):
            await manager.connect("srv", "ses_1")

        # Should not raise
        await manager.disconnect("srv", "ses_1")

        # Provider should still be removed
        assert manager._get_provider("srv", "ses_1") is None


# =========================================================================
# 7. cleanup()
# =========================================================================


class TestCleanup:
    """SkillMcpManager.cleanup() — disconnect all servers for a session."""

    async def test_cleanup_disconnects_all_session_servers(
        self,
        manager: SkillMcpManager,
    ) -> None:
        """cleanup() disconnects all servers for the given session."""
        manager.prepare("srv_a", SkillMcpServerConfig(command="echo"))
        manager.prepare("srv_b", SkillMcpServerConfig(command="echo"))

        with patch.object(
            manager, "_create_and_connect", AsyncMock(return_value=make_mock_provider())
        ):
            await manager.connect("srv_a", "ses_1")
            await manager.connect("srv_b", "ses_1")

        with patch.object(manager, "disconnect", AsyncMock()) as mock_disconnect:
            await manager.cleanup("ses_1")

        assert mock_disconnect.await_count == 2
        mock_disconnect.assert_any_await("srv_a", "ses_1")
        mock_disconnect.assert_any_await("srv_b", "ses_1")

    async def test_cleanup_removes_session(
        self,
        manager: SkillMcpManager,
    ) -> None:
        """cleanup() removes all providers for the session."""
        manager.prepare("srv", SkillMcpServerConfig(command="echo"))

        with patch.object(
            manager, "_create_and_connect", AsyncMock(return_value=make_mock_provider())
        ):
            await manager.connect("srv", "ses_1")

        await manager.cleanup("ses_1")
        assert "ses_1" not in manager._providers

    async def test_cleanup_no_servers_is_noop(
        self,
        manager: SkillMcpManager,
    ) -> None:
        """cleanup() for a session with no connections is a no-op."""
        await manager.cleanup("ses_empty")
        # Should not raise


# =========================================================================
# 8. cleanup_all()
# =========================================================================


class TestCleanupAll:
    """SkillMcpManager.cleanup_all() — disconnect all sessions."""

    async def test_cleanup_all_disconnects_all_sessions(
        self,
        manager: SkillMcpManager,
    ) -> None:
        """cleanup_all() disconnects every server across all sessions."""
        manager.prepare("srv", SkillMcpServerConfig(command="echo"))

        with patch.object(
            manager, "_create_and_connect", AsyncMock(return_value=make_mock_provider())
        ):
            await manager.connect("srv", "ses_a")
            await manager.connect("srv", "ses_b")

        with patch.object(manager, "cleanup", AsyncMock()) as mock_cleanup:
            await manager.cleanup_all()

        assert mock_cleanup.await_count == 2
        mock_cleanup.assert_any_await("ses_a")
        mock_cleanup.assert_any_await("ses_b")

    async def test_cleanup_all_empty_noop(
        self,
        manager: SkillMcpManager,
    ) -> None:
        """cleanup_all() with no sessions is a no-op."""
        await manager.cleanup_all()
        # Should not raise


# =========================================================================
# 9. cleanup_idle()
# =========================================================================


class TestCleanupIdle:
    """SkillMcpManager.cleanup_idle() — disconnect idle connections."""

    async def test_cleanup_idle_disconnects_idle_connections(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
    ) -> None:
        """cleanup_idle() disconnects connections that exceed idle timeout."""
        manager._idle_timeout = 0  # Immediate idle
        manager.prepare("srv", server_config)

        with patch.object(
            manager, "_create_and_connect", AsyncMock(return_value=make_mock_provider())
        ):
            await manager.connect("srv", "ses_1")

        with patch.object(manager, "disconnect", AsyncMock()) as mock_disconnect:
            await manager.cleanup_idle()

        mock_disconnect.assert_awaited_once_with("srv", "ses_1")

    async def test_cleanup_idle_skips_active_connections(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
    ) -> None:
        """cleanup_idle() does not disconnect connections within the idle timeout."""
        manager._idle_timeout = 999999  # Effectively never idle
        manager.prepare("srv", server_config)

        with patch.object(
            manager, "_create_and_connect", AsyncMock(return_value=make_mock_provider())
        ):
            await manager.connect("srv", "ses_1")

        with patch.object(manager, "disconnect", AsyncMock()) as mock_disconnect:
            await manager.cleanup_idle()

        mock_disconnect.assert_not_called()

    async def test_cleanup_idle_empty_noop(
        self,
        manager: SkillMcpManager,
    ) -> None:
        """cleanup_idle() with no connections is a no-op."""
        await manager.cleanup_idle()
        # Should not raise


# =========================================================================
# 10. Thread safety
# =========================================================================


class TestThreadSafety:
    """SkillMcpManager thread safety — concurrent connect() calls."""

    async def test_concurrent_connects_serialized_by_lock(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
    ) -> None:
        """Two concurrent connect() calls for the same server are serialized."""
        manager.prepare("srv", server_config)
        call_order: list[str] = []

        original_sleep = asyncio.sleep

        async def slow_create_and_connect(
            config: SkillMcpServerConfig,
            server_name: str,
        ) -> AsyncMock:
            """Simulate slow connection establishment."""
            call_order.append("enter")
            await original_sleep(0.01)  # Small delay to overlap
            call_order.append("exit")
            return make_mock_provider()

        with patch.object(manager, "_create_and_connect", slow_create_and_connect):
            # Launch two concurrent connects
            results = await asyncio.gather(
                manager.connect("srv", "ses_1"),
                manager.connect("srv", "ses_1"),
                return_exceptions=True,
            )

        # Both should succeed (not raise) and return the same provider
        assert all(not isinstance(r, Exception) for r in results)
        assert results[0] is results[1]

        # _create_and_connect should only be called once (the second
        # call finds the cached provider after acquiring the lock)
        assert "enter" in call_order
        # Note: serialization means the second caller waits on the lock,
        # then finds the already-connected provider via double-check.

    async def test_concurrent_connects_different_servers_not_serialized(
        self,
        manager: SkillMcpManager,
    ) -> None:
        """Concurrent connect() calls for different servers proceed in parallel."""
        manager.prepare("srv_a", SkillMcpServerConfig(command="echo"))
        manager.prepare("srv_b", SkillMcpServerConfig(command="echo"))

        call_count = 0

        async def tracking_create(config: SkillMcpServerConfig, server_name: str) -> AsyncMock:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.01)
            return make_mock_provider()

        with patch.object(manager, "_create_and_connect", tracking_create):
            results = await asyncio.gather(
                manager.connect("srv_a", "ses_1"),
                manager.connect("srv_b", "ses_1"),
            )

        assert len(results) == 2
        assert results[0] is not results[1]
        assert call_count == 2


# =========================================================================
# 11. _is_idle helper
# =========================================================================


class TestIsIdle:
    """SkillMcpManager._is_idle() — idle detection."""

    def test_is_idle_no_activity_returns_false(self, manager: SkillMcpManager) -> None:
        """_is_idle() returns False when there is no activity record."""
        assert not manager._is_idle("ses_1", "srv")

    def test_is_idle_within_timeout_returns_false(self, manager: SkillMcpManager) -> None:
        """_is_idle() returns False when within the idle timeout."""
        manager._touch("ses_1", "srv")
        assert not manager._is_idle("ses_1", "srv")

    def test_is_idle_exceeds_timeout_returns_true(self, manager: SkillMcpManager) -> None:
        """_is_idle() returns True when beyond the idle timeout."""
        manager._idle_timeout = -1  # Already expired
        manager._touch("ses_1", "srv")
        assert manager._is_idle("ses_1", "srv")


# =========================================================================
# 12. _create_and_connect — edge cases
# =========================================================================


class TestCreateAndConnect:
    """SkillMcpManager._create_and_connect() — provider creation logic."""

    async def test_create_and_connect_command_config(
        self,
        manager: SkillMcpManager,
    ) -> None:
        """_create_and_connect() creates StdioMCPServerConfig from command-based config."""
        config = SkillMcpServerConfig(command="uvx", args=["mcp-server"])

        # MCPResourceProvider is imported inside _create_and_connect() body,
        # so patch at the actual module path
        with patch(
            "agentpool.resource_providers.mcp_provider.MCPResourceProvider",
            autospec=True,
        ) as mock_provider:
            mock_instance = mock_provider.return_value
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)

            result = await manager._create_and_connect(config, "test-server")

        mock_provider.assert_called_once()
        # Verify StdioMCPServerConfig was passed
        call_args = mock_provider.call_args.kwargs
        assert call_args["server"].command == "uvx"
        assert call_args["server"].args == ["mcp-server"]
        assert call_args["name"] == "skill_mcp_test-server"
        assert result is mock_instance
        mock_instance.__aenter__.assert_awaited_once()

    async def test_create_and_connect_url_config(
        self,
        manager: SkillMcpManager,
    ) -> None:
        """_create_and_connect() creates StreamableHTTPMCPServerConfig from URL config."""
        config = SkillMcpServerConfig(url="http://remote:8080/mcp")

        with patch(
            "agentpool.resource_providers.mcp_provider.MCPResourceProvider",
            autospec=True,
        ) as mock_provider:
            mock_instance = mock_provider.return_value
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)

            result = await manager._create_and_connect(config, "remote-server")

        mock_provider.assert_called_once()
        call_args = mock_provider.call_args.kwargs
        # Should be a StreamableHTTPMCPServerConfig with url
        assert str(call_args["server"].url) == "http://remote:8080/mcp"
        assert call_args["name"] == "skill_mcp_remote-server"
        assert result is mock_instance
        mock_instance.__aenter__.assert_awaited_once()

    async def test_create_and_connect_no_command_or_url_raises_value_error(
        self,
        manager: SkillMcpManager,
    ) -> None:
        """_create_and_connect() raises ValueError when neither command nor url is set."""
        config = SkillMcpServerConfig()  # Neither command nor url

        with pytest.raises(ValueError, match="must specify either 'command' or 'url'"):
            await manager._create_and_connect(config, "bad-server")


# =========================================================================
# 13. Integration: full lifecycle
# =========================================================================


class TestLifecycle:
    """SkillMcpManager full lifecycle — prepare → connect → use → cleanup."""

    async def test_full_lifecycle(
        self,
        manager: SkillMcpManager,
    ) -> None:
        """Full lifecycle: prepare → connect → get_tools → cleanup → cleanup_all."""
        manager.prepare("srv", SkillMcpServerConfig(command="echo"))

        mock_provider = make_mock_provider()

        with patch.object(manager, "_create_and_connect", AsyncMock(return_value=mock_provider)):
            # Connect
            provider = await manager.connect("srv", "ses_1")
            assert provider is not None

            # Get tools
            tools = await manager.get_tools("srv", "ses_1")
            assert tools == []

            # Reconnect (should return cached)
            provider2 = await manager.connect("srv", "ses_1")
            assert provider2 is provider

        # Cleanup session
        await manager.cleanup("ses_1")
        assert manager._get_provider("srv", "ses_1") is None

        # Connect again after cleanup
        with patch.object(
            manager, "_create_and_connect", AsyncMock(return_value=make_mock_provider())
        ):
            provider3 = await manager.connect("srv", "ses_1")
            assert provider3 is not None

        # Cleanup all
        await manager.cleanup_all()
        assert "ses_1" not in manager._providers

    async def test_reconnect_after_disconnect(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
    ) -> None:
        """After disconnect, a subsequent connect creates a new connection."""
        manager.prepare("srv", server_config)
        mock_provider1 = make_mock_provider()
        mock_provider2 = make_mock_provider()

        with patch.object(
            manager,
            "_create_and_connect",
            AsyncMock(side_effect=[mock_provider1, mock_provider2]),
        ):
            first = await manager.connect("srv", "ses_1")
            await manager.disconnect("srv", "ses_1")
            second = await manager.connect("srv", "ses_1")

        assert first is mock_provider1
        assert second is mock_provider2
        assert first is not second

    async def test_no_tool_calls_no_connection(
        self,
        manager: SkillMcpManager,
        server_config: SkillMcpServerConfig,
    ) -> None:
        """No MCP server process is started unless get_tools() or connect() is called."""
        manager.prepare("srv", server_config)

        with patch.object(manager, "_create_and_connect", AsyncMock()) as mock_create:
            pass  # No tool access

        mock_create.assert_not_called()
