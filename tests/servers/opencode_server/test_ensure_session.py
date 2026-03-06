"""Tests for the ServerState.ensure_session() method."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool.agents.base_agent import BaseAgent
from agentpool_server.opencode_server.models import (
    Session,
    TimeCreatedUpdated,
)
from agentpool_server.opencode_server.state import ServerState


def create_mock_agent() -> MagicMock:
    """Create a properly configured mock agent."""
    agent = MagicMock(spec=BaseAgent)
    agent.name = "test_agent"
    agent.agent_pool = MagicMock()
    agent.agent_pool.manifest.config_file_path = "test_config.yml"
    agent.agent_pool.storage.save_session = AsyncMock()
    agent.env = MagicMock()
    agent.env.cwd = "/test/dir"
    return agent


@pytest.fixture
def mock_state() -> ServerState:
    """Create a ServerState with mocked dependencies."""
    agent = create_mock_agent()
    return ServerState(
        working_dir="/test/working/dir",
        agent=agent,
    )


@pytest.mark.asyncio
async def test_ensure_session_creates_new_session(mock_state: ServerState) -> None:
    """Test that ensure_session creates a new session when it doesn't exist."""
    session_id = "test_session_123"
    parent_id = "parent_session_456"

    with patch(
        "agentpool_server.opencode_server.converters.opencode_to_session_data"
    ) as mock_converter:
        mock_converter.return_value = MagicMock()

        with patch(
            "agentpool_server.opencode_server.input_provider.OpenCodeInputProvider"
        ) as mock_provider_class:
            mock_provider = MagicMock()
            mock_provider_class.return_value = mock_provider

            result = await mock_state.ensure_session(session_id, parent_id=parent_id)

    assert result.id == session_id
    assert result.parent_id == parent_id
    assert result.project_id == "default"
    assert result.directory == mock_state.working_dir
    assert result.title == "New Session"
    assert result.version == "1"
    assert isinstance(result.time, TimeCreatedUpdated)


@pytest.mark.asyncio
async def test_ensure_session_returns_existing_session(mock_state: ServerState) -> None:
    """Test that ensure_session returns existing session if already in memory."""
    session_id = "test_session_123"

    existing_session = Session(
        id=session_id,
        project_id="test_project",
        directory="/custom/dir",
        title="Custom Title",
        version="2",
        time=TimeCreatedUpdated(created=1000, updated=2000),
        parent_id=None,
    )
    mock_state.sessions[session_id] = existing_session

    result = await mock_state.ensure_session(session_id)

    assert result is existing_session
    assert result.title == "Custom Title"
    assert result.project_id == "test_project"


@pytest.mark.asyncio
async def test_ensure_session_persists_to_storage(mock_state: ServerState) -> None:
    """Test that ensure_session persists the session to storage."""
    session_id = "test_session_789"

    with patch(
        "agentpool_server.opencode_server.converters.opencode_to_session_data"
    ) as mock_converter:
        mock_session_data = MagicMock()
        mock_converter.return_value = mock_session_data

        with patch(
            "agentpool_server.opencode_server.input_provider.OpenCodeInputProvider"
        ) as mock_provider_class:
            mock_provider_class.return_value = MagicMock()

            await mock_state.ensure_session(session_id)

    mock_converter.assert_called_once()
    args, kwargs = mock_converter.call_args
    session_arg = args[0]
    assert session_arg.id == session_id
    assert kwargs["agent_name"] == "test_agent"
    assert kwargs["pool_id"] == "test_config.yml"

    mock_state.agent.agent_pool.storage.save_session.assert_awaited_once_with(mock_session_data)


@pytest.mark.asyncio
async def test_ensure_session_caches_in_memory(mock_state: ServerState) -> None:
    """Test that ensure_session caches all session state in memory."""
    session_id = "test_session_abc"

    with (
        patch("agentpool_server.opencode_server.converters.opencode_to_session_data"),
        patch(
            "agentpool_server.opencode_server.input_provider.OpenCodeInputProvider"
        ) as mock_provider_class,
    ):
        mock_provider = MagicMock()
        mock_provider_class.return_value = mock_provider

        result = await mock_state.ensure_session(session_id)

    assert session_id in mock_state.sessions
    assert mock_state.sessions[session_id] is result

    assert session_id in mock_state.messages
    assert mock_state.messages[session_id] == []

    assert session_id in mock_state.session_status
    assert mock_state.session_status[session_id].type == "idle"

    assert session_id in mock_state.todos
    assert mock_state.todos[session_id] == []

    assert session_id in mock_state.input_providers
    assert mock_state.input_providers[session_id] is mock_provider


@pytest.mark.asyncio
async def test_ensure_session_creates_input_provider(mock_state: ServerState) -> None:
    """Test that ensure_session creates and stores an OpenCodeInputProvider."""
    session_id = "test_session_def"

    with (
        patch("agentpool_server.opencode_server.converters.opencode_to_session_data"),
        patch(
            "agentpool_server.opencode_server.input_provider.OpenCodeInputProvider"
        ) as mock_provider_class,
    ):
        mock_provider = MagicMock()
        mock_provider_class.return_value = mock_provider

        await mock_state.ensure_session(session_id)

    mock_provider_class.assert_called_once_with(mock_state, session_id)
    assert mock_state.input_providers[session_id] is mock_provider


@pytest.mark.asyncio
async def test_ensure_session_without_parent_id(mock_state: ServerState) -> None:
    """Test that ensure_session works without a parent_id."""
    session_id = "test_session_no_parent"

    with (
        patch("agentpool_server.opencode_server.converters.opencode_to_session_data"),
        patch("agentpool_server.opencode_server.input_provider.OpenCodeInputProvider"),
    ):
        result = await mock_state.ensure_session(session_id)

    assert result.id == session_id
    assert result.parent_id is None


@pytest.mark.asyncio
async def test_ensure_session_is_idempotent(mock_state: ServerState) -> None:
    """Test that calling ensure_session twice with the same ID returns the same session."""
    session_id = "test_session_idempotent"

    with (
        patch("agentpool_server.opencode_server.converters.opencode_to_session_data"),
        patch("agentpool_server.opencode_server.input_provider.OpenCodeInputProvider"),
    ):
        result1 = await mock_state.ensure_session(session_id)
        result2 = await mock_state.ensure_session(session_id)

    assert result1 is result2
    mock_state.agent.agent_pool.storage.save_session.assert_awaited_once()
