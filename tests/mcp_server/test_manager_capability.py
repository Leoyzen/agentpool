"""Tests for MCPManager.as_capability()."""

from __future__ import annotations

import warnings

from pydantic import HttpUrl
from pydantic_ai.mcp import MCPServerSSE, MCPServerStdio, MCPServerStreamableHTTP

from agentpool.mcp_server.manager import MCPManager
from agentpool_config.mcp_server import (
    AcpMCPServerConfig,
    SSEMCPServerConfig,
    StdioMCPServerConfig,
    StreamableHTTPMCPServerConfig,
)


class TestMCPManagerAsCapability:
    """Test MCPManager.as_capability() method."""

    def test_empty_servers_returns_empty_list(self) -> None:
        """An MCPManager with no servers should return an empty list."""
        manager = MCPManager(servers=[])
        caps = manager.as_capability()
        assert caps == []

    def test_single_stdio_server(self) -> None:
        """A single stdio server should produce one MCP capability."""
        config = StdioMCPServerConfig(
            name="test_stdio",
            command="python",
            args=["-m", "my_server"],
            env={"FOO": "bar"},
            timeout=30.0,
        )
        manager = MCPManager(servers=[config])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            caps = manager.as_capability()

        assert len(caps) == 1
        cap = caps[0]
        assert cap.url == "mcp://stdio/python_-m my_server"
        assert cap.native is False
        assert cap.id == "test_stdio"
        assert cap.allowed_tools is None

        # Verify the local server is configured correctly
        local = cap.local
        assert isinstance(local, MCPServerStdio)
        assert local.command == "python"
        assert local.args == ["-m", "my_server"]
        assert local.timeout == 30.0

    def test_single_sse_server(self) -> None:
        """A single SSE server should produce one MCP capability with the URL."""
        config = SSEMCPServerConfig(
            name="test_sse",
            url=HttpUrl("http://localhost:8080/sse"),
            headers={"Authorization": "Bearer token"},
            timeout=45.0,
        )
        manager = MCPManager(servers=[config])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            caps = manager.as_capability()

        assert len(caps) == 1
        cap = caps[0]
        assert cap.url == "http://localhost:8080/sse"
        assert cap.native is False
        assert cap.id == "test_sse"

        local = cap.local
        assert isinstance(local, MCPServerSSE)
        assert local.url == "http://localhost:8080/sse"
        assert local.headers == {"Authorization": "Bearer token"}
        assert local.timeout == 45.0

    def test_single_streamable_http_server(self) -> None:
        """A single StreamableHTTP server should produce one MCP capability."""
        config = StreamableHTTPMCPServerConfig(
            name="test_http",
            url=HttpUrl("https://api.example.com/mcp"),
            headers={"X-Api-Key": "secret"},
            timeout=60.0,
        )
        manager = MCPManager(servers=[config])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            caps = manager.as_capability()

        assert len(caps) == 1
        cap = caps[0]
        assert cap.url == "https://api.example.com/mcp"
        assert cap.native is False
        assert cap.id == "test_http"

        local = cap.local
        assert isinstance(local, MCPServerStreamableHTTP)
        assert local.url == "https://api.example.com/mcp"
        assert local.headers == {"X-Api-Key": "secret"}
        assert local.timeout == 60.0

    def test_multiple_servers(self) -> None:
        """Multiple servers should produce multiple capabilities."""
        stdio_cfg = StdioMCPServerConfig(command="python", args=["server.py"])
        sse_cfg = SSEMCPServerConfig(url=HttpUrl("http://localhost:8080/sse"))
        manager = MCPManager(servers=[stdio_cfg, sse_cfg])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            caps = manager.as_capability()

        assert len(caps) == 2
        urls = {c.url for c in caps}
        assert urls == {"mcp://stdio/python_server.py", "http://localhost:8080/sse"}

    def test_disabled_server_is_skipped(self) -> None:
        """Disabled servers should not produce capabilities."""
        enabled = StdioMCPServerConfig(command="python", args=["enabled.py"])
        disabled = StdioMCPServerConfig(
            command="python", args=["disabled.py"], enabled=False
        )
        manager = MCPManager(servers=[enabled, disabled])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            caps = manager.as_capability()

        assert len(caps) == 1
        assert caps[0].id == "python_enabled.py"

    def test_acp_server_is_skipped(self) -> None:
        """ACP transport servers should be skipped (not supported by pydantic-ai)."""
        stdio = StdioMCPServerConfig(command="python", args=["server.py"])
        acp = AcpMCPServerConfig(acp_id="my-acp-server")
        manager = MCPManager(servers=[stdio, acp])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            caps = manager.as_capability()

        assert len(caps) == 1
        assert caps[0].id == "python_server.py"

    def test_allowed_tools_passed_through(self) -> None:
        """enabled_tools from config should be passed to the capability."""
        config = StdioMCPServerConfig(
            command="python",
            args=["server.py"],
            enabled_tools=["read_file", "list_directory"],
        )
        manager = MCPManager(servers=[config])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            caps = manager.as_capability()

        assert len(caps) == 1
        assert caps[0].allowed_tools == ["read_file", "list_directory"]

    def test_capability_is_abstract_capability(self) -> None:
        """Returned capabilities should be instances of AbstractCapability."""
        from pydantic_ai.capabilities import AbstractCapability

        config = StdioMCPServerConfig(command="echo", args=["hello"])
        manager = MCPManager(servers=[config])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            caps = manager.as_capability()

        assert len(caps) == 1
        assert isinstance(caps[0], AbstractCapability)

    def test_server_without_name_uses_client_id(self) -> None:
        """When server name is not set, client_id should be used as capability id."""
        config = StdioMCPServerConfig(command="python", args=["server.py"])
        manager = MCPManager(servers=[config])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            caps = manager.as_capability()

        assert len(caps) == 1
        assert caps[0].id == "python_server.py"

    def test_does_not_modify_manager_state(self) -> None:
        """as_capability() should be a pure read-only operation."""
        config = StdioMCPServerConfig(command="python", args=["server.py"])
        manager = MCPManager(servers=[config])

        # Call as_capability twice
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            caps1 = manager.as_capability()
            caps2 = manager.as_capability()

        # Should return equivalent but separate objects
        assert len(caps1) == len(caps2) == 1
        assert caps1[0].url == caps2[0].url
        # Should be distinct objects
        assert caps1[0] is not caps2[0]
        # Manager state should be unchanged
        assert len(manager.servers) == 1
        assert len(manager.providers) == 0
