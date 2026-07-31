"""Tests for OpenCode slash command routing: skill vs non-skill commands.

Skill commands (``category="skill"``) must route through SessionPool's
EventBus-only path (``route_message`` → ``send_message`` → ``_consume_run``)
with a proper USER message — NOT through ``run_stream()``. The legacy
``run_stream()`` path double-injected prompts (staged_content from
skill_bridge + an assembled agent_prompt) and swallowed the user message
(the TUI showed "Loading skill: ..." as the AI reply and never displayed
the user's input).

Regression coverage:
- skill command → user message broadcast + ``route_message`` called,
  ``run_stream`` NOT called.
- non-skill command → ``run_stream`` still used (legacy behavior kept).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest


if TYPE_CHECKING:
    from httpx import AsyncClient

    from agentpool_server.opencode_server.state import ServerState


pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _create_session(async_client: AsyncClient) -> str:
    """Create a session and return its ID."""
    response = await async_client.post("/session", json={"title": "Skill Test Session"})
    assert response.status_code == 200
    return response.json()["id"]


def _install_skill_command(server_state: ServerState, name: str = "lodestone") -> MagicMock:
    """Install a category='skill' command into the mock command store."""
    mock_command = MagicMock()
    mock_command.execute = AsyncMock()
    mock_command.category = "skill"
    mock_command_store = MagicMock()
    mock_command_store.get_command = MagicMock(return_value=mock_command)
    server_state.command_store = mock_command_store
    # Ensure skill_bridge is not present so only category detection is used.
    server_state.skill_bridge = None
    return mock_command


def _install_plain_command(server_state: ServerState, name: str = "help") -> MagicMock:
    """Install a non-skill command into the mock command store."""
    mock_command = MagicMock()
    mock_command.execute = AsyncMock()
    mock_command.category = "general"
    mock_command_store = MagicMock()
    mock_command_store.get_command = MagicMock(return_value=mock_command)
    server_state.command_store = mock_command_store
    server_state.skill_bridge = None
    return mock_command


async def test_skill_command_routes_via_route_message_not_run_stream(
    async_client: AsyncClient,
    server_state: ServerState,
    mock_agent: Mock,
):
    """Skill commands must create a user message and route via route_message.

    When a skill command executes, the old path called
    ``session_pool.run_stream(agent_prompt)`` which swallowed the user
    message and double-injected the prompt. The new path must:
    1. Broadcast a USER message (visible in the TUI)
    2. Call ``session_pool_integration.route_message(...)`` (EventBus-only)
    3. NOT call ``session_pool.run_stream(...)``
    """
    session_id = await _create_session(async_client)
    _install_skill_command(server_state)
    mock_agent.list_prompts = AsyncMock(return_value=[])

    # Ensure run_stream exists on the mock pool so we can assert it was
    # NOT called by the skill path.
    server_state.pool_or_none.session_pool.run_stream = AsyncMock(  # type: ignore[union-attr]
        return_value=iter(())
    )
    server_state.session_pool_integration.route_message = AsyncMock(return_value="mid-1")

    captured_events: list[Any] = []
    original_broadcast = server_state.broadcast_event

    async def capturing_broadcast(event: Any) -> None:
        captured_events.append(event)
        await original_broadcast(event)

    server_state.broadcast_event = capturing_broadcast  # type: ignore[method-assign]

    response = await async_client.post(
        f"/session/{session_id}/command",
        json={"command": "lodestone", "arguments": "analyze this codebase"},
    )

    assert response.status_code == 200
    result = response.json()
    assert "info" in result
    assert "parts" in result

    # 1. A USER message was broadcast (PartUpdatedEvent + MessageUpdatedEvent)
    from agentpool_server.opencode_server.models import MessageUpdatedEvent

    user_message_events = [
        e
        for e in captured_events
        if isinstance(e, MessageUpdatedEvent) and getattr(e.properties.info, "role", None) == "user"
    ]
    assert user_message_events, (
        "Skill command did not broadcast a USER message — the user's input is swallowed by the TUI."
    )

    # 2. route_message was called with the user text as content.
    server_state.session_pool_integration.route_message.assert_called_once()
    _call_kwargs = server_state.session_pool_integration.route_message.call_args.kwargs
    assert _call_kwargs["content"] == "analyze this codebase"
    assert _call_kwargs["session_id"] == session_id

    # 3. run_stream was NOT called (single EventBus-only path).
    server_state.pool_or_none.session_pool.run_stream.assert_not_called()  # type: ignore[union-attr]


async def test_plain_command_keeps_run_stream_path(
    async_client: AsyncClient,
    server_state: ServerState,
    mock_agent: Mock,
):
    """Non-skill commands keep the legacy run_stream() behavior."""
    session_id = await _create_session(async_client)
    _install_plain_command(server_state)
    mock_agent.list_prompts = AsyncMock(return_value=[])

    run_stream = AsyncMock(return_value=iter(()))
    server_state.pool_or_none.session_pool.run_stream = run_stream  # type: ignore[union-attr]

    response = await async_client.post(
        f"/session/{session_id}/command",
        json={"command": "help", "arguments": ""},
    )

    assert response.status_code == 200
    # Legacy path still calls run_stream.
    run_stream.assert_called_once()
