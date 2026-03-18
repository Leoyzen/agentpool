"""Tests for OpenCode server command execution.

Tests slashed command execution, MCP prompt fallback, and precedence handling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool_server.opencode_server.models import CommandRequest


if TYPE_CHECKING:
    from unittest.mock import Mock

    from httpx import AsyncClient

    from agentpool_server.opencode_server.state import ServerState


pytestmark = pytest.mark.asyncio


async def test_execute_slashed_command_success(
    async_client: AsyncClient,
    server_state: ServerState,
    mock_agent: Mock,
):
    """Test slashed command execution when command is in CommandStore.

    Happy path - command exists in CommandStore, executes successfully.
    """
    # Create session first
    response = await async_client.post("/session", json={"title": "Test Session"})
    assert response.status_code == 200
    session_id = response.json()["id"]

    # Mock CommandStore with a command
    mock_command = MagicMock()
    mock_command.execute = AsyncMock()
    mock_command_store = MagicMock()
    mock_command_store.get_command = MagicMock(return_value=mock_command)
    server_state.command_store = mock_command_store

    # Mock empty MCP prompts (no collision)
    mock_agent.tools.list_prompts = AsyncMock(return_value=[])

    # Execute command
    response = await async_client.post(
        f"/session/{session_id}/command",
        json={"command": "test-cmd", "arguments": "arg1 arg2"},
    )

    # Verify success
    assert response.status_code == 200
    result = response.json()
    assert "info" in result
    assert "parts" in result

    # Verify command was called (get_command is called twice: once for check, once to retrieve)
    assert mock_command_store.get_command.call_count == 2
    mock_command_store.get_command.assert_called_with("test-cmd")
    mock_command.execute.assert_called_once()


async def test_mcp_prompt_fallback(
    async_client: AsyncClient,
    server_state: ServerState,
    mock_agent: Mock,
):
    """Test MCP prompt fallback when command not in CommandStore.

    Command doesn't exist in CommandStore but exists as MCP prompt.
    Should fall back and execute via MCP.
    """
    # Create session first
    response = await async_client.post("/session", json={"title": "Test Session"})
    assert response.status_code == 200
    session_id = response.json()["id"]

    # Mock CommandStore without the command
    mock_command_store = MagicMock()
    mock_command_store.get_command = MagicMock(return_value=None)
    server_state.command_store = mock_command_store

    # Mock MCP prompt
    mock_prompt = MagicMock()
    mock_prompt.name = "test-cmd"
    mock_prompt.arguments = [{"name": "arg1"}]
    mock_prompt.get_components = AsyncMock(return_value=[])
    mock_agent.tools.list_prompts = AsyncMock(return_value=[mock_prompt])
    mock_agent.run = AsyncMock(return_value=MagicMock(data="MCP prompt result"))

    # Execute command via MCP fallback
    response = await async_client.post(
        f"/session/{session_id}/command",
        json={"command": "test-cmd", "arguments": "value1"},
    )

    # Verify success
    assert response.status_code == 200
    result = response.json()
    assert "info" in result
    assert "parts" in result

    # Verify MCP prompt was used
    mock_agent.tools.list_prompts.assert_called()
    mock_prompt.get_components.assert_called_once()


async def test_precedence_slashed_over_mcp(
    async_client: AsyncClient,
    server_state: ServerState,
    mock_agent: Mock,
):
    """Test that CommandStore commands take precedence over MCP prompts.

    Both exist, CommandStore should be used.
    """
    # Create session first
    response = await async_client.post("/session", json={"title": "Test Session"})
    assert response.status_code == 200
    session_id = response.json()["id"]

    # Mock CommandStore with command
    mock_command = MagicMock()
    mock_command.execute = AsyncMock()
    mock_command_store = MagicMock()
    mock_command_store.get_command = MagicMock(return_value=mock_command)
    server_state.command_store = mock_command_store

    # Mock MCP prompt with same name
    mock_prompt = MagicMock()
    mock_prompt.name = "test-cmd"
    mock_agent.tools.list_prompts = AsyncMock(return_value=[mock_prompt])

    # Execute command
    response = await async_client.post(
        f"/session/{session_id}/command",
        json={"command": "test-cmd"},
    )

    # Verify success
    assert response.status_code == 200

    # Verify CommandStore command was executed (not MCP)
    mock_command.execute.assert_called_once()

    # Verify MCP prompt.get_components was NOT called
    mock_prompt.get_components.assert_not_called()


async def test_unknown_command_returns_404(
    async_client: AsyncClient,
    server_state: ServerState,
    mock_agent: Mock,
):
    """Test 404 response when command not found anywhere.

    Neither CommandStore nor MCP has the command.
    """
    # Create session first
    response = await async_client.post("/session", json={"title": "Test Session"})
    assert response.status_code == 200
    session_id = response.json()["id"]

    # Mock CommandStore without the command
    mock_command_store = MagicMock()
    mock_command_store.get_command = MagicMock(return_value=None)
    server_state.command_store = mock_command_store

    # Mock empty MCP prompts
    mock_agent.tools.list_prompts = AsyncMock(return_value=[])

    # Execute unknown command
    response = await async_client.post(
        f"/session/{session_id}/command",
        json={"command": "unknown-cmd"},
    )

    # Verify 404
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


async def test_none_command_store_graceful(
    async_client: AsyncClient,
    server_state: ServerState,
    mock_agent: Mock,
):
    """Test graceful handling when command_store is None.

    Should fall back to MCP prompts.
    """
    # Create session first
    response = await async_client.post("/session", json={"title": "Test Session"})
    assert response.status_code == 200
    session_id = response.json()["id"]

    # Set command_store to None
    server_state.command_store = None

    # Mock MCP prompt
    mock_prompt = MagicMock()
    mock_prompt.name = "fallback-cmd"
    mock_prompt.arguments = []
    mock_prompt.get_components = AsyncMock(return_value=[])
    mock_agent.tools.list_prompts = AsyncMock(return_value=[mock_prompt])
    mock_agent.run = AsyncMock(return_value=MagicMock(data="Fallback result"))

    # Execute command
    response = await async_client.post(
        f"/session/{session_id}/command",
        json={"command": "fallback-cmd"},
    )

    # Verify success via MCP fallback
    assert response.status_code == 200
    result = response.json()
    assert "info" in result

    # Verify MCP was checked and used
    mock_agent.tools.list_prompts.assert_called()


async def test_command_execution_error(
    async_client: AsyncClient,
    server_state: ServerState,
    mock_agent: Mock,
):
    """Test graceful handling of command execution failures.

    Command exists but raises exception during execution.
    """
    # Create session first
    response = await async_client.post("/session", json={"title": "Test Session"})
    assert response.status_code == 200
    session_id = response.json()["id"]

    # Mock CommandStore with failing command
    mock_command = MagicMock()
    mock_command.execute = AsyncMock(side_effect=RuntimeError("Command failed"))
    mock_command_store = MagicMock()
    mock_command_store.get_command = MagicMock(return_value=mock_command)
    server_state.command_store = mock_command_store

    # Mock empty MCP prompts
    mock_agent.tools.list_prompts = AsyncMock(return_value=[])

    # Execute command that will fail
    response = await async_client.post(
        f"/session/{session_id}/command",
        json={"command": "failing-cmd"},
    )

    # Verify 500 error
    assert response.status_code == 500
    assert "failed" in response.json()["detail"].lower()


async def test_collision_warning_logged(
    async_client: AsyncClient,
    server_state: ServerState,
    mock_agent: Mock,
    caplog: pytest.LogCaptureFixture,
):
    """Test warning is logged when both slashed command and MCP prompt exist.

    Uses caplog to capture log output.
    """
    # Create session first
    response = await async_client.post("/session", json={"title": "Test Session"})
    assert response.status_code == 200
    session_id = response.json()["id"]

    # Mock CommandStore with command
    mock_command = MagicMock()
    mock_command.execute = AsyncMock()
    mock_command_store = MagicMock()
    mock_command_store.get_command = MagicMock(return_value=mock_command)
    server_state.command_store = mock_command_store

    # Mock MCP prompt with same name (collision)
    mock_prompt = MagicMock()
    mock_prompt.name = "collision-cmd"
    mock_agent.tools.list_prompts = AsyncMock(return_value=[mock_prompt])

    # Execute command and capture logs
    with caplog.at_level("WARNING"):
        response = await async_client.post(
            f"/session/{session_id}/command",
            json={"command": "collision-cmd"},
        )

    # Verify success
    assert response.status_code == 200

    # Verify warning was logged
    assert "Both slashed command and prompt exist" in caplog.text
    assert "collision-cmd" in caplog.text
    assert "slashed command" in caplog.text
