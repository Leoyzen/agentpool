"""MCP server configuration models."""

from __future__ import annotations

from pydantic import BaseModel


class StdioMcpServer(BaseModel):
    """MCP server running as a subprocess via stdio transport.

    Example:
        StdioMcpServer(
            command="npx",
            args=["-y", "@openai/codex-shell-tool-mcp"]
        )
    """

    command: str
    args: list[str] = []
    env: dict[str, str] | None = None
    enabled: bool = True


class HttpMcpServer(BaseModel):
    """MCP server accessible via HTTP/SSE transport.

    Example:
        HttpMcpServer(
            url="http://localhost:8000/mcp",
            bearer_token_env_var="MY_MCP_TOKEN"
        )
    """

    url: str
    bearer_token_env_var: str | None = None
    http_headers: dict[str, str] | None = None
    enabled: bool = True


# Union type for any MCP server config
McpServerConfig = StdioMcpServer | HttpMcpServer
