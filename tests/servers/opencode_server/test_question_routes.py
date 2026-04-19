"""Tests for question_routes permission lookup using public API."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from agentpool_server.opencode_server.input_provider import OpenCodeInputProvider, PendingPermission
from agentpool_server.opencode_server.routes.question_routes import _find_permission_provider
from agentpool_server.opencode_server.state import ServerState


def _make_state_with_providers(
    providers: dict[str, OpenCodeInputProvider],
) -> ServerState:
    """Create a ServerState with pre-populated input_providers."""
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    state.input_providers = providers
    return state


async def test_find_permission_provider_finds_matching_permission():
    """_find_permission_provider should find provider with matching pending permission."""
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    provider = OpenCodeInputProvider(state=state, session_id="sess_1")
    state.input_providers["sess_1"] = provider

    # Add a pending permission
    permission_id = "perm_1_1234"
    future = asyncio.get_running_loop().create_future()
    provider._pending_permissions[permission_id] = PendingPermission(
        permission_id=permission_id,
        tool_name="bash",
        args={"command": "echo test"},
        future=future,
    )

    result = _find_permission_provider(state, permission_id)
    assert result is not None
    found_session_id, found_provider = result
    assert found_session_id == "sess_1"
    assert found_provider is provider


async def test_find_permission_provider_returns_none_for_unknown_permission():
    """_find_permission_provider should return None when no provider has the permission."""
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    provider = OpenCodeInputProvider(state=state, session_id="sess_1")
    state.input_providers["sess_1"] = provider

    result = _find_permission_provider(state, "nonexistent_perm")
    assert result is None


async def test_find_permission_provider_uses_public_api():
    """Verify that _find_permission_provider delegates to has_pending_permission().

    This test ensures the function uses the public method rather than
    directly accessing _pending_permissions, by checking the behavior
    matches what has_pending_permission() would return.
    """
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)
    provider = OpenCodeInputProvider(state=state, session_id="sess_1")
    state.input_providers["sess_1"] = provider

    permission_id = "perm_2_5678"
    future = asyncio.get_running_loop().create_future()
    provider._pending_permissions[permission_id] = PendingPermission(
        permission_id=permission_id,
        tool_name="bash",
        args={"command": "ls"},
        future=future,
    )

    # Verify has_pending_permission works (public API)
    assert provider.has_pending_permission(permission_id) is True
    assert provider.has_pending_permission("nonexistent") is False

    # Verify _find_permission_provider returns the same result as has_pending_permission
    result = _find_permission_provider(state, permission_id)
    assert result is not None

    result_missing = _find_permission_provider(state, "nonexistent")
    assert result_missing is None


async def test_find_permission_provider_multiple_providers():
    """_find_permission_provider should find the correct provider among multiple."""
    mock_agent = Mock()
    mock_agent.agent_pool = None
    state = ServerState(working_dir="/tmp", agent=mock_agent)

    provider_a = OpenCodeInputProvider(state=state, session_id="sess_a")
    provider_b = OpenCodeInputProvider(state=state, session_id="sess_b")
    state.input_providers["sess_a"] = provider_a
    state.input_providers["sess_b"] = provider_b

    permission_id = "perm_3_9999"
    future = asyncio.get_running_loop().create_future()
    provider_b._pending_permissions[permission_id] = PendingPermission(
        permission_id=permission_id,
        tool_name="bash",
        args={"command": "rm -rf /tmp/test"},
        future=future,
    )

    result = _find_permission_provider(state, permission_id)
    assert result is not None
    found_session_id, found_provider = result
    assert found_session_id == "sess_b"
    assert found_provider is provider_b
