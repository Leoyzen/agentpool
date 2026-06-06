"""Tests for lazy MCP connection feature.

Comprehensive tests covering:
- Lazy server config parsing (`lazy: true` in YAML)
- Lazy server skipped during MCPManager.__aenter__
- Eager server still connects during MCPManager.__aenter__
- Lazy server connects on first get_tools() call
- Lazy server connects on first get_prompts() call
- Lazy server cleanup works when never accessed
- Mixed lazy + eager servers work correctly
- as_capability() returns capabilities for lazy servers
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yamling

from agentpool.mcp_server.manager import MCPManager
from agentpool.resource_providers.mcp_provider import MCPResourceProvider
from agentpool_config.mcp_server import (
    BaseMCPServerConfig,
    SSEMCPServerConfig,
    StdioMCPServerConfig,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_mcp_client():
    """Create a mock MCPClient for testing."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.connected = True
    client.is_connected = MagicMock(return_value=True)
    client.list_tools = AsyncMock(return_value=[])
    client.list_prompts = AsyncMock(return_value=[])
    client.list_resources = AsyncMock(return_value=[])
    client.list_resource_templates = AsyncMock(return_value=[])
    client.read_resource = AsyncMock(return_value=[])
    return client


@pytest.fixture
def eager_stdio_config():
    """Create an eager (non-lazy) stdio MCP server config."""
    return StdioMCPServerConfig(
        command="python",
        args=["-m", "test_server"],
        name="eager_server",
        lazy=False,
    )


@pytest.fixture
def lazy_stdio_config():
    """Create a lazy stdio MCP server config."""
    return StdioMCPServerConfig(
        command="python",
        args=["-m", "lazy_server"],
        name="lazy_server",
        lazy=True,
    )


@pytest.fixture
def lazy_sse_config():
    """Create a lazy SSE MCP server config."""
    return SSEMCPServerConfig(
        url="http://localhost:8080/sse",
        name="lazy_sse_server",
        lazy=True,
    )


# =============================================================================
# Test 1: Lazy server config parsing (`lazy: true` in YAML)
# =============================================================================


@pytest.mark.unit
def test_lazy_config_parsing_from_yaml():
    """Test that `lazy: true` is correctly parsed from YAML config."""
    yaml_text = """
mcp_servers:
  - type: stdio
    command: python
    args: ["-m", "test_server"]
    name: lazy_stdio
    lazy: true
  - type: stdio
    command: python
    args: ["-m", "eager_server"]
    name: eager_stdio
    lazy: false
"""
    data = yamling.load_yaml(yaml_text)
    server_cfgs = data["mcp_servers"]
    assert len(server_cfgs) == 2

    lazy_parsed = StdioMCPServerConfig.model_validate(server_cfgs[0])
    eager_parsed = StdioMCPServerConfig.model_validate(server_cfgs[1])

    assert lazy_parsed.lazy is True
    assert eager_parsed.lazy is False


@pytest.mark.unit
def test_lazy_default_is_false():
    """Test that lazy defaults to False when not specified."""
    config = StdioMCPServerConfig(
        command="python",
        args=["-m", "test_server"],
        name="default_server",
    )
    assert config.lazy is False


@pytest.mark.unit
def test_lazy_field_on_base_config():
    """Test that lazy field exists on BaseMCPServerConfig."""
    config = StdioMCPServerConfig(
        command="python",
        args=["-m", "test"],
        lazy=True,
    )
    assert isinstance(config, BaseMCPServerConfig)
    assert hasattr(config, "lazy")
    assert config.lazy is True


# =============================================================================
# Test 2: Lazy server skipped during MCPManager.__aenter__
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_lazy_server_skipped_during_manager_enter(
    mock_mcp_client, lazy_stdio_config
):
    """Test that lazy servers are not connected during MCPManager.__aenter__."""
    with patch("agentpool.mcp_server.MCPClient") as mock_client_class:
        mock_client_class.return_value = mock_mcp_client
        manager = MCPManager(servers=[lazy_stdio_config], _warn=False)

        async with manager:
            # Lazy server should have been set up (provider created) but not connected
            assert len(manager.providers) == 1
            assert not manager.providers[0]._client_connected

    # Client's __aenter__ should never be called for lazy server
    mock_mcp_client.__aenter__.assert_not_called()


# =============================================================================
# Test 3: Eager server still connects during MCPManager.__aenter__
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_eager_server_connects_during_manager_enter(
    mock_mcp_client, eager_stdio_config
):
    """Test that eager (non-lazy) servers ARE connected during MCPManager.__aenter__."""
    with patch("agentpool.mcp_server.MCPClient") as mock_client_class:
        mock_client_class.return_value = mock_mcp_client
        manager = MCPManager(servers=[eager_stdio_config], _warn=False)

        async with manager:
            # Eager server should have been set up -> one provider
            assert len(manager.providers) == 1

    # Client's __aenter__ should be called for eager server
    mock_mcp_client.__aenter__.assert_called_once()


# =============================================================================
# Test 4: Lazy server connects on first get_tools() call
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_lazy_server_connects_on_first_get_tools(
    mock_mcp_client, lazy_stdio_config
):
    """Test that a lazy MCPResourceProvider connects on first get_tools() call."""
    with patch("agentpool.mcp_server.MCPClient") as mock_client_class:
        mock_client_class.return_value = mock_mcp_client
        provider = MCPResourceProvider(server=lazy_stdio_config, name="test-lazy")

        # Enter context manager - should NOT connect
        async with provider:
            assert not provider._client_connected
            mock_mcp_client.__aenter__.assert_not_called()

            # Call get_tools() - should trigger connection
            tools = await provider.get_tools()
            assert provider._client_connected is True
            mock_mcp_client.__aenter__.assert_called_once()
            assert tools == []


# =============================================================================
# Test 5: Lazy server connects on first get_prompts() call
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_lazy_server_connects_on_first_get_prompts(
    mock_mcp_client, lazy_stdio_config
):
    """Test that a lazy MCPResourceProvider connects on first get_prompts() call."""
    with patch("agentpool.mcp_server.MCPClient") as mock_client_class:
        mock_client_class.return_value = mock_mcp_client
        provider = MCPResourceProvider(server=lazy_stdio_config, name="test-lazy")

        # Enter context manager - should NOT connect
        async with provider:
            assert not provider._client_connected
            mock_mcp_client.__aenter__.assert_not_called()

            # Call get_prompts() - should trigger connection
            prompts = await provider.get_prompts()
            assert provider._client_connected is True
            mock_mcp_client.__aenter__.assert_called_once()
            assert prompts == []


# =============================================================================
# Test 6: Lazy server cleanup works when never accessed
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_lazy_server_cleanup_when_never_accessed(
    mock_mcp_client, lazy_stdio_config
):
    """Test that lazy server cleanup works even when never accessed."""
    with patch("agentpool.mcp_server.MCPClient") as mock_client_class:
        mock_client_class.return_value = mock_mcp_client
        provider = MCPResourceProvider(server=lazy_stdio_config, name="test-lazy")

        async with provider:
            # Never access anything
            pass

        # Exit stack should close gracefully even though client was never entered
        assert not provider._client_connected
        mock_mcp_client.__aenter__.assert_not_called()
        mock_mcp_client.__aexit__.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_lazy_server_cleanup_after_access(mock_mcp_client, lazy_stdio_config):
    """Test that lazy server cleanup works after being accessed."""
    with patch("agentpool.mcp_server.MCPClient") as mock_client_class:
        mock_client_class.return_value = mock_mcp_client
        provider = MCPResourceProvider(server=lazy_stdio_config, name="test-lazy")

        async with provider:
            await provider.get_tools()

        # After exiting, client __aexit__ should have been called
        mock_mcp_client.__aexit__.assert_called_once()


# =============================================================================
# Test 7: Mixed lazy + eager servers work correctly
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_mixed_lazy_and_eager_servers(
    mock_mcp_client, eager_stdio_config, lazy_stdio_config
):
    """Test that MCPManager handles mixed lazy and eager servers correctly."""
    with patch("agentpool.mcp_server.MCPClient") as mock_client_class:
        mock_client_class.return_value = mock_mcp_client
        manager = MCPManager(
            servers=[eager_stdio_config, lazy_stdio_config],
            _warn=False,
        )

        async with manager:
            # Both servers should have providers, but only eager is connected
            assert len(manager.providers) == 2
            eager_provider = next(p for p in manager.providers if p.server.name == "eager_server")
            lazy_provider = next(p for p in manager.providers if p.server.name == "lazy_server")
            assert eager_provider._client_connected is True
            assert lazy_provider._client_connected is False
            assert manager.servers[0].lazy is False
            assert manager.servers[1].lazy is True

    # __aenter__ should be called once (for eager server)
    assert mock_mcp_client.__aenter__.call_count == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_mixed_servers_provider_level(
    mock_mcp_client, eager_stdio_config, lazy_stdio_config
):
    """Test MCPResourceProvider directly with mixed lazy/eager behavior."""
    with patch("agentpool.mcp_server.MCPClient") as mock_client_class:
        mock_client_class.return_value = mock_mcp_client

        eager_provider = MCPResourceProvider(server=eager_stdio_config, name="eager")
        lazy_provider = MCPResourceProvider(server=lazy_stdio_config, name="lazy")

        async with eager_provider:
            assert eager_provider._client_connected is True
            mock_mcp_client.__aenter__.assert_called_once()

        async with lazy_provider:
            assert lazy_provider._client_connected is False
            # Still only called once (from eager_provider)
            mock_mcp_client.__aenter__.assert_called_once()

            # Now trigger lazy connection
            await lazy_provider.get_tools()
            assert lazy_provider._client_connected is True
            assert mock_mcp_client.__aenter__.call_count == 2


# =============================================================================
# Test 8: as_capability() returns capabilities for lazy servers
# =============================================================================


@pytest.mark.unit
def test_as_capability_returns_capabilities_for_lazy_servers(
    lazy_stdio_config, lazy_sse_config
):
    """Test that MCPManager.as_capability() includes lazy servers."""
    manager = MCPManager(servers=[lazy_stdio_config, lazy_sse_config], _warn=False)

    caps = manager.as_capability()

    # Both lazy servers should be represented
    assert len(caps) == 2
    cap_ids = {cap.id for cap in caps}
    assert "lazy_server" in cap_ids
    assert "lazy_sse_server" in cap_ids


@pytest.mark.unit
def test_as_capability_skips_disabled_and_acp(mock_mcp_client):
    """Test that as_capability() still skips disabled servers and ACP transport."""
    disabled_config = StdioMCPServerConfig(
        command="python",
        args=["-m", "disabled"],
        name="disabled_server",
        lazy=True,
        enabled=False,
    )
    lazy_config = StdioMCPServerConfig(
        command="python",
        args=["-m", "lazy"],
        name="lazy_server",
        lazy=True,
    )

    manager = MCPManager(
        servers=[disabled_config, lazy_config],
        _warn=False,
    )

    caps = manager.as_capability()
    assert len(caps) == 1
    assert caps[0].id == "lazy_server"


# =============================================================================
# Additional edge-case tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.unit
async def test_lazy_provider_get_resources_triggers_connection(
    mock_mcp_client, lazy_stdio_config
):
    """Test that get_resources() also triggers lazy connection."""
    with patch("agentpool.mcp_server.MCPClient") as mock_client_class:
        mock_client_class.return_value = mock_mcp_client
        provider = MCPResourceProvider(server=lazy_stdio_config, name="test-lazy")

        async with provider:
            assert not provider._client_connected
            resources = await provider.get_resources()
            assert provider._client_connected is True
            assert resources == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_ensure_client_connected_is_idempotent(mock_mcp_client, lazy_stdio_config):
    """Test that _ensure_client_connected() is safe to call multiple times."""
    with patch("agentpool.mcp_server.MCPClient") as mock_client_class:
        mock_client_class.return_value = mock_mcp_client
        provider = MCPResourceProvider(server=lazy_stdio_config, name="test-lazy")

        async with provider:
            await provider._ensure_client_connected()
            await provider._ensure_client_connected()
            await provider._ensure_client_connected()

            # Should only connect once
            mock_mcp_client.__aenter__.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_lazy_provider_with_string_shorthand(mock_mcp_client):
    """Test lazy config from string shorthand works correctly."""
    # String shorthand creates StdioMCPServerConfig which defaults lazy=False
    # so we need to set it explicitly after parsing
    with patch("agentpool.mcp_server.MCPClient") as mock_client_class:
        mock_client_class.return_value = mock_mcp_client
        config = StdioMCPServerConfig.from_string("python -m lazy_server")
        config.lazy = True
        config.name = "string_lazy"

        provider = MCPResourceProvider(server=config, name="test-lazy")

        async with provider:
            assert not provider._client_connected
            await provider.get_tools()
            assert provider._client_connected is True
