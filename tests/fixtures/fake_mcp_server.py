"""Fake MCP server for L4 e2e testing of MCP status reporting.

This module is spawned as a subprocess by the ``subprocess_server`` fixture
(see ``tests/e2e/conftest.py``) to verify that ``GET /mcp`` reports real
MCP server connection status, tools, and self-reported ``serverInfo``.

It uses ``fastmcp`` (the project's existing MCP SDK) to avoid hand-rolled
JSON-RPC. The server exposes a single deterministic tool (``search_kb``)
and runs over stdio, which is the transport used by ``StdioMCPServerConfig``.

Run directly:

    python tests/fixtures/fake_mcp_server.py

The server reports its name as ``fake-kb-server`` and version as ``0.1.0``
via the MCP ``initialize`` handshake's ``serverInfo`` field. The L4 test
asserts that this name surfaces through ``MCPClient.server_info`` →
``MCPServerStatus.server_name`` → ``GET /mcp`` response.
"""

from __future__ import annotations

from fastmcp import FastMCP


mcp = FastMCP("fake-kb-server", version="0.1.0")


@mcp.tool()
def search_kb(query: str) -> str:
    """Search the knowledge base.

    Args:
        query: The search query string.

    Returns:
        A deterministic result string containing the query.
    """
    return f"Results for: {query}"


if __name__ == "__main__":
    mcp.run()
