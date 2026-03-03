"""MCP server configuration models."""

from __future__ import annotations

from pydantic import BaseModel


class StdioMcpServer(BaseModel):
    """MCP server running as a subprocess via stdio transport."""

    command: str
    args: list[str] = []
    env: dict[str, str] | None = None
    enabled: bool = True


class HttpMcpServer(BaseModel):
    """MCP server accessible via HTTP/SSE transport."""

    url: str
    bearer_token_env_var: str | None = None
    http_headers: dict[str, str] | None = None
    enabled: bool = True


McpServerConfig = StdioMcpServer | HttpMcpServer
