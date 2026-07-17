"""Tests for HookProxy coexistence with Conductor and ACPClientHandler.

Covers: get_turn_hooks() behavior with/without HookProxy,
_maybe_auto_insert_hook_proxy(), set_hooks_enabled().
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from acp.conductor import Conductor
from acp.proxy.impls.hook_proxy import HookProxy
from agentpool.hooks.agent_hooks import AgentHooks


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_agent_hooks(has_hooks: bool = True) -> MagicMock:
    """Create an AsyncMock(spec=AgentHooks) with has_hooks configured."""
    mock = AsyncMock(spec=AgentHooks)
    mock.has_hooks.return_value = has_hooks
    return mock


# ---------------------------------------------------------------------------
# Conductor.get_turn_hooks()
# ---------------------------------------------------------------------------


def test_conductor_get_turn_hooks_returns_hooks_when_no_hook_proxy() -> None:
    """When no HookProxy is active, get_turn_hooks returns the AgentHooks."""
    hooks = _make_agent_hooks(has_hooks=True)
    conductor = Conductor(
        name="test",
        command="echo",
        agent_hooks=hooks,
    )
    # _has_hook_proxy defaults to False (not yet initialized)
    assert conductor.has_hook_proxy is False
    result = conductor.get_turn_hooks()
    assert result is hooks


def test_conductor_get_turn_hooks_returns_none_when_hook_proxy() -> None:
    """When HookProxy is active, get_turn_hooks returns None."""
    hooks = _make_agent_hooks(has_hooks=True)
    conductor = Conductor(
        name="test",
        command="echo",
        agent_hooks=hooks,
    )
    # Simulate HookProxy being active
    conductor._has_hook_proxy = True
    assert conductor.has_hook_proxy is True
    result = conductor.get_turn_hooks()
    assert result is None


def test_conductor_get_turn_hooks_returns_none_when_no_hooks() -> None:
    """When agent_hooks is None, get_turn_hooks returns None."""
    conductor = Conductor(
        name="test",
        command="echo",
        agent_hooks=None,
    )
    result = conductor.get_turn_hooks()
    assert result is None


# ---------------------------------------------------------------------------
# Conductor._maybe_auto_insert_hook_proxy()
# ---------------------------------------------------------------------------


def test_conductor_auto_insert_hook_proxy() -> None:
    """Agent with hooks gets HookProxy auto-inserted at position 0."""
    hooks = _make_agent_hooks(has_hooks=True)
    conductor = Conductor(
        name="test",
        command="echo",
        agent_hooks=hooks,
    )
    assert len(conductor.proxy_chain) == 0
    conductor._maybe_auto_insert_hook_proxy()
    assert len(conductor.proxy_chain) == 1
    assert isinstance(conductor.proxy_chain[0], HookProxy)
    assert conductor.has_hook_proxy is True


def test_conductor_no_auto_insert_when_no_hooks() -> None:
    """Agent without hooks does not get HookProxy inserted."""
    hooks = _make_agent_hooks(has_hooks=False)
    conductor = Conductor(
        name="test",
        command="echo",
        agent_hooks=hooks,
    )
    conductor._maybe_auto_insert_hook_proxy()
    assert len(conductor.proxy_chain) == 0
    assert conductor.has_hook_proxy is False


def test_conductor_no_auto_insert_when_already_present() -> None:
    """When HookProxy already in chain, no duplicate is inserted."""
    hooks = _make_agent_hooks(has_hooks=True)
    existing_proxy = HookProxy(hooks=[hooks])
    conductor = Conductor(
        name="test",
        command="echo",
        agent_hooks=hooks,
        proxy_chain=[existing_proxy],
    )
    conductor._maybe_auto_insert_hook_proxy()
    assert len(conductor.proxy_chain) == 1
    assert conductor.proxy_chain[0] is existing_proxy
    assert conductor.has_hook_proxy is True


def test_conductor_no_auto_insert_when_agent_hooks_none() -> None:
    """When agent_hooks is None, no HookProxy is inserted."""
    conductor = Conductor(
        name="test",
        command="echo",
        agent_hooks=None,
    )
    conductor._maybe_auto_insert_hook_proxy()
    assert len(conductor.proxy_chain) == 0
    assert conductor.has_hook_proxy is False


# ---------------------------------------------------------------------------
# ACPClientHandler.set_hooks_enabled()
# ---------------------------------------------------------------------------


def test_client_handler_set_hooks_enabled() -> None:
    """set_hooks_enabled(False) sets _hooks_enabled to False."""
    # Build a minimal mock ACPAgent and ACPState to satisfy __init__
    mock_agent = MagicMock()
    mock_agent.client_env = MagicMock()
    mock_agent.auto_approve = False
    mock_agent.acp_permission_callback = None
    mock_agent._init_request = MagicMock()
    mock_agent._init_request.client_capabilities = None
    mock_agent.state_updated = MagicMock()
    mock_agent.state_updated.emit = AsyncMock()

    mock_state = MagicMock()

    from agentpool.agents.acp_agent.client_handler import ACPClientHandler

    handler = ACPClientHandler(agent=mock_agent, state=mock_state)
    assert handler._hooks_enabled is True

    handler.set_hooks_enabled(False)
    assert handler._hooks_enabled is False

    handler.set_hooks_enabled(True)
    assert handler._hooks_enabled is True
