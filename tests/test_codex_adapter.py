"""Tests for Codex adapter."""

from __future__ import annotations

import asyncio

import pytest

from codex_adapter import CodexClient, HttpMcpServer, StdioMcpServer
from codex_adapter.client import _mcp_config_to_toml_inline
from codex_adapter.exceptions import CodexProcessError, CodexRequestError
from codex_adapter.models.events import AgentMessageDeltaEvent


async def test_process_message_routes_response_to_future():
    """JSON-RPC responses are routed to pending request futures."""
    client = CodexClient()
    future: asyncio.Future[dict] = asyncio.Future()
    client._pending_requests[1] = future
    result = {"threadId": "thread-123"}
    await client._process_message({"jsonrpc": "2.0", "id": 1, "result": result})
    assert future.result() == {"threadId": "thread-123"}


async def test_process_message_error_raises():
    """JSON-RPC error responses set exception on future."""
    client = CodexClient()
    future: asyncio.Future[dict] = asyncio.Future()
    client._pending_requests[1] = future
    error = {"code": -32602, "message": "Invalid params"}
    await client._process_message({"jsonrpc": "2.0", "id": 1, "error": error})
    with pytest.raises(CodexRequestError) as exc:
        future.result()
    assert exc.value.code == -32602


async def test_process_message_notification_queued():
    """JSON-RPC notifications are parsed and queued."""
    client = CodexClient()

    await client._process_message({
        "jsonrpc": "2.0",
        "method": "item/agentMessage/delta",
        "params": {"delta": "Hello", "itemId": "1", "threadId": "t", "turnId": "u"},
    })

    event = await asyncio.wait_for(client._event_queue.get(), timeout=1.0)
    assert isinstance(event, AgentMessageDeltaEvent)
    assert event.data.delta == "Hello"


async def test_send_request_not_connected_raises():
    """Sending request before connecting raises CodexProcessError."""
    client = CodexClient()
    with pytest.raises(CodexProcessError, match="Not connected"):
        await client._send_request("thread/start")


def test_mcp_config_to_toml_stdio():
    """StdioMcpServer serializes to TOML inline format."""
    config = StdioMcpServer(command="npx", args=["-y", "pkg"])
    result = _mcp_config_to_toml_inline("bash", config)
    assert result == 'mcp_servers.bash={command = "npx", args = ["-y", "pkg"]}'


def test_mcp_config_to_toml_http():
    """HttpMcpServer serializes to TOML inline format."""
    config = HttpMcpServer(url="http://localhost:8000", bearer_token_env_var="TOKEN")
    result = _mcp_config_to_toml_inline("api", config)
    assert 'url = "http://localhost:8000"' in result
    assert 'bearer_token_env_var = "TOKEN"' in result


if __name__ == "__main__":
    pytest.main([__file__, "-vv"])
