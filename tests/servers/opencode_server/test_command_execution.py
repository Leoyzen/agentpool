"""Tests for OpenCode server command execution.

Tests slashed command execution, MCP prompt fallback, and precedence handling.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, Mock

from agentpool.skills.command import SkillCommand
from agentpool.skills.skill import Skill
from agentpool_config.session_pool import OpenCodeConfig
from agentpool_server.opencode_server.state import ServerState
from upathtools import UPath


if TYPE_CHECKING:
    from httpx import AsyncClient


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


async def test_concurrent_slash_commands_same_session_are_serialized(
    async_client: AsyncClient,
    server_state: ServerState,
    mock_agent: Mock,
):
    """Test that concurrent slash commands to the same session are serialized.

    The route-level lock in ``execute_command`` ensures that multiple commands
    sent to the same session concurrently are processed sequentially, not in
    parallel. This prevents race conditions during command execution.
    """
    # Create session first
    response = await async_client.post("/session", json={"title": "Test Session"})
    assert response.status_code == 200
    session_id = response.json()["id"]

    # Track concurrent execution
    active_executions = 0
    max_concurrent = 0
    execution_lock = asyncio.Lock()

    async def tracked_execute(*args, **kwargs):
        nonlocal active_executions, max_concurrent
        async with execution_lock:
            active_executions += 1
            max_concurrent = max(max_concurrent, active_executions)
        # Simulate some work
        await asyncio.sleep(0.1)
        async with execution_lock:
            active_executions -= 1

    # Mock CommandStore with tracked command
    mock_command = MagicMock()
    mock_command.execute = AsyncMock(side_effect=tracked_execute)
    mock_command_store = MagicMock()
    mock_command_store.get_command = MagicMock(return_value=mock_command)
    server_state.command_store = mock_command_store

    # Mock empty MCP prompts
    mock_agent.tools.list_prompts = AsyncMock(return_value=[])

    # Send two commands concurrently to the same session
    async def send_command(cmd: str):
        return await async_client.post(
            f"/session/{session_id}/command",
            json={"command": cmd},
        )

    results = await asyncio.gather(
        send_command("cmd-a"),
        send_command("cmd-b"),
    )

    # Both should succeed
    assert all(r.status_code == 200 for r in results)

    # Verify commands were executed sequentially (never concurrently)
    assert max_concurrent == 1, (
        f"Expected sequential execution (max_concurrent=1), "
        f"but got max_concurrent={max_concurrent}. "
        f"Route-level lock is not serializing commands."
    )


async def test_skill_command_routes_through_session_pool_when_flag_enabled(
    async_client: "AsyncClient",
    server_state: ServerState,
    mock_agent: Mock,
):
    """Test that skill command routes through SessionPool.run_stream() when flag enabled.

    When ``use_session_pool_for_skills`` is True, skill commands should use
    ``session_pool.run_stream(session_id, user_prompt, scope='descendants')``
    instead of calling ``agent.run_stream()`` directly.
    """
    # Create session first
    response = await async_client.post("/session", json={"title": "Test Session"})
    assert response.status_code == 200
    session_id = response.json()["id"]

    # CommandStore doesn't have it
    mock_command_store = MagicMock()
    mock_command_store.get_command = MagicMock(return_value=None)
    server_state.command_store = mock_command_store

    # Add skill to pool.skill_commands
    skill = Skill(
        name="test-skill",
        description="Test skill",
        skill_path=UPath("/tmp/test"),
        instructions="Test skill instructions",
    )
    skill_cmd = SkillCommand(name="test-skill", description="Test skill", skill=skill)
    mock_agent.agent_pool.skill_commands = {"test-skill": skill_cmd}  # type: ignore[attr-defined]
    mock_agent.agent_pool.skill_provider = None  # type: ignore[attr-defined]

    # Enable session pool for skills by replacing opencode config with a mock
    mock_opencode = MagicMock()
    mock_opencode.should_use_session_pool_for.return_value = True
    mock_agent.agent_pool.manifest.opencode = mock_opencode  # type: ignore[attr-defined]

    # Track session_pool.run_stream calls
    session_pool_calls: list[tuple[Any, Any]] = []

    async def _mock_run_stream(*args: Any, **kwargs: Any) -> Any:
        session_pool_calls.append((args, kwargs))
        if False:
            yield MagicMock()

    mock_agent.agent_pool.session_pool.run_stream = _mock_run_stream  # type: ignore[attr-defined]

    # Mock empty MCP prompts
    mock_agent.tools.list_prompts = AsyncMock(return_value=[])

    response = await async_client.post(
        f"/session/{session_id}/command",
        json={"command": "test-skill", "arguments": "some args"},
    )

    # Fallback to skill_commands should work — returns 200
    assert response.status_code == 200
    result = response.json()
    assert "info" in result
    assert "parts" in result

    # Verify session_pool.run_stream was called with scope="session"
    assert len(session_pool_calls) == 1
    _args, kwargs = session_pool_calls[0]
    assert kwargs.get("scope") == "session"


async def test_skill_command_routes_through_session_pool(
    async_client: "AsyncClient",
    server_state: ServerState,
    mock_agent: Mock,
):
    """Test that skill command routes through SessionPool.run_stream().

    SessionPool is now the default execution path for all categories.
    """
    # Create session first
    response = await async_client.post("/session", json={"title": "Test Session"})
    assert response.status_code == 200
    session_id = response.json()["id"]

    # CommandStore doesn't have it
    mock_command_store = MagicMock()
    mock_command_store.get_command = MagicMock(return_value=None)
    server_state.command_store = mock_command_store

    # Add skill to pool.skill_commands
    skill = Skill(
        name="direct-skill",
        description="Direct skill",
        skill_path=UPath("/tmp/direct"),
        instructions="Direct skill instructions",
    )
    skill_cmd = SkillCommand(name="direct-skill", description="Direct skill", skill=skill)
    mock_agent.agent_pool.skill_commands = {"direct-skill": skill_cmd}  # type: ignore[attr-defined]
    mock_agent.agent_pool.skill_provider = None  # type: ignore[attr-defined]

    # Track agent.run_stream calls
    agent_calls: list[tuple[Any, Any]] = []

    async def _mock_run_stream(*args: Any, **kwargs: Any) -> Any:
        agent_calls.append((args, kwargs))
        if False:
            yield MagicMock()

    mock_agent.run_stream = _mock_run_stream  # type: ignore[method-assign]

    # Track session_pool.run_stream calls
    session_pool_calls: list[tuple[Any, Any]] = []

    async def _mock_session_run_stream(*args: Any, **kwargs: Any) -> Any:
        session_pool_calls.append((args, kwargs))
        if False:
            yield MagicMock()

    mock_agent.agent_pool.session_pool.run_stream = _mock_session_run_stream  # type: ignore[attr-defined]

    # Mock empty MCP prompts
    mock_agent.tools.list_prompts = AsyncMock(return_value=[])

    response = await async_client.post(
        f"/session/{session_id}/command",
        json={"command": "direct-skill", "arguments": "some args"},
    )

    # Fallback to skill_commands should work — returns 200
    assert response.status_code == 200
    result = response.json()
    assert "info" in result
    assert "parts" in result

    # Verify session_pool.run_stream was called (not direct agent.run_stream)
    assert len(session_pool_calls) == 1
    assert len(agent_calls) == 0


async def test_slash_command_routes_through_session_pool_when_flag_enabled(
    async_client: "AsyncClient",
    server_state: ServerState,
    mock_agent: Mock,
):
    """Test that slash command routes through SessionPool.run_stream() when flag enabled.

    When ``use_session_pool_for_commands`` is True, slashed commands should use
    ``session_pool.run_stream(session_id, agent_prompt, scope='descendants')``
    instead of calling ``agent.run_stream()`` directly. ``command.execute()``
    must still run before the agent stream.
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

    # Enable session pool for commands by replacing opencode config with a mock
    mock_opencode = MagicMock()
    mock_opencode.should_use_session_pool_for.return_value = True
    mock_agent.agent_pool.manifest.opencode = mock_opencode  # type: ignore[attr-defined]

    # Track session_pool.run_stream calls
    session_pool_calls: list[tuple[Any, Any]] = []

    async def _mock_run_stream(*args: Any, **kwargs: Any) -> Any:
        session_pool_calls.append((args, kwargs))
        if False:
            yield MagicMock()

    mock_agent.agent_pool.session_pool.run_stream = _mock_run_stream  # type: ignore[attr-defined]

    # Mock empty MCP prompts
    mock_agent.tools.list_prompts = AsyncMock(return_value=[])

    response = await async_client.post(
        f"/session/{session_id}/command",
        json={"command": "test-cmd", "arguments": "arg1 arg2"},
    )

    # Verify success
    assert response.status_code == 200
    result = response.json()
    assert "info" in result
    assert "parts" in result

    # Verify command.execute() was called before the agent stream
    mock_command.execute.assert_called_once()

    # Verify session_pool.run_stream was called with scope="session"
    assert len(session_pool_calls) == 1
    _args, kwargs = session_pool_calls[0]
    assert kwargs.get("scope") == "session"


async def test_slash_command_routes_through_session_pool(
    async_client: "AsyncClient",
    server_state: ServerState,
    mock_agent: Mock,
):
    """Test that slash command routes through SessionPool.run_stream().

    SessionPool is now the default execution path for all categories.
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

    # Track agent.run_stream calls
    agent_calls: list[tuple[Any, Any]] = []

    async def _mock_run_stream(*args: Any, **kwargs: Any) -> Any:
        agent_calls.append((args, kwargs))
        if False:
            yield MagicMock()

    mock_agent.run_stream = _mock_run_stream  # type: ignore[method-assign]

    # Track session_pool.run_stream calls
    session_pool_calls: list[tuple[Any, Any]] = []

    async def _mock_session_run_stream(*args: Any, **kwargs: Any) -> Any:
        session_pool_calls.append((args, kwargs))
        if False:
            yield MagicMock()

    mock_agent.agent_pool.session_pool.run_stream = _mock_session_run_stream  # type: ignore[attr-defined]

    # Mock empty MCP prompts
    mock_agent.tools.list_prompts = AsyncMock(return_value=[])

    response = await async_client.post(
        f"/session/{session_id}/command",
        json={"command": "test-cmd", "arguments": "arg1 arg2"},
    )

    # Verify success
    assert response.status_code == 200
    result = response.json()
    assert "info" in result
    assert "parts" in result

    # Verify command.execute() was called
    mock_command.execute.assert_called_once()

    # Verify session_pool.run_stream was called (not direct agent.run_stream)
    assert len(session_pool_calls) == 1
    assert len(agent_calls) == 0


async def test_mcp_prompt_routes_through_session_pool_when_flag_enabled(
    async_client: "AsyncClient",
    server_state: ServerState,
    mock_agent: Mock,
):
    """Test that MCP prompt routes through SessionPool.receive_request() when flag enabled.

    When ``use_session_pool_for_mcp`` is True, MCP prompts should use
    ``session_pool.receive_request(session_id, prompt_text)`` instead of
    calling ``agent.run()`` directly. RunHandle should be stored for cancellation.
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
    mock_prompt.name = "test-prompt"
    mock_prompt.arguments = [{"name": "arg1"}]
    mock_prompt.get_components = AsyncMock(return_value=[])
    mock_agent.tools.list_prompts = AsyncMock(return_value=[mock_prompt])

    # Enable session pool for MCP by replacing opencode config with a mock
    mock_opencode = MagicMock()
    mock_opencode.should_use_session_pool_for.return_value = True
    mock_agent.agent_pool.manifest.opencode = mock_opencode  # type: ignore[attr-defined]

    # Track session_pool.receive_request calls
    receive_request_calls: list[tuple[Any, Any]] = []

    async def _mock_receive_request(*args: Any, **kwargs: Any) -> Any:
        receive_request_calls.append((args, kwargs))
        # Return a mock RunHandle
        mock_handle = MagicMock()
        mock_handle.run_id = "test-run-id"
        return mock_handle

    mock_agent.agent_pool.session_pool.receive_request = _mock_receive_request  # type: ignore[attr-defined]

    response = await async_client.post(
        f"/session/{session_id}/command",
        json={"command": "test-prompt", "arguments": "value1"},
    )

    # Verify success
    assert response.status_code == 200
    result = response.json()
    assert "info" in result
    assert "parts" in result

    # Verify MCP prompt was used
    mock_agent.tools.list_prompts.assert_called()
    mock_prompt.get_components.assert_called_once()

    # Verify session_pool.receive_request was called
    assert len(receive_request_calls) == 1
    _args, kwargs = receive_request_calls[0]
    assert kwargs.get("session_id") == session_id
    assert kwargs.get("priority") == "when_idle"

    # Verify RunHandle was stored
    assert hasattr(server_state, "_run_handles")
    assert session_id in server_state._run_handles
    assert server_state._run_handles[session_id].run_id == "test-run-id"


async def test_mcp_prompt_routes_through_session_pool(
    async_client: "AsyncClient",
    server_state: ServerState,
    mock_agent: Mock,
):
    """Test that MCP prompt routes through SessionPool.receive_request().

    SessionPool is now the default execution path for all categories.
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
    mock_prompt.name = "direct-prompt"
    mock_prompt.arguments = []
    mock_prompt.get_components = AsyncMock(return_value=[])
    mock_agent.tools.list_prompts = AsyncMock(return_value=[mock_prompt])
    mock_agent.run = AsyncMock(return_value=MagicMock(data="Direct result"))

    # Track session_pool.receive_request calls
    receive_request_calls: list[tuple[Any, Any]] = []

    async def _mock_receive_request(*args: Any, **kwargs: Any) -> Any:
        receive_request_calls.append((args, kwargs))
        return None

    mock_agent.agent_pool.session_pool.receive_request = _mock_receive_request  # type: ignore[attr-defined]

    response = await async_client.post(
        f"/session/{session_id}/command",
        json={"command": "direct-prompt"},
    )

    # Verify success
    assert response.status_code == 200
    result = response.json()
    assert "info" in result
    assert "parts" in result

    # Verify session_pool.receive_request was called (not direct agent.run)
    assert len(receive_request_calls) == 1
    mock_agent.run.assert_not_called()
