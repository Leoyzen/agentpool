"""Test suite for ClaudeCodeHookManager parameter corrections.

Tests that ClaudeCodeHookManager is initialized with correct parameters.
"""

import sys
from pathlib import Path
from typing import Any

import pytest

# Add src to path for imports
sys_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(sys_path))


def test_hook_manager_signature():
    """Test that ClaudeCodeHookManager has correct signature."""

    from agentpool.agents.claude_code_agent.hook_manager import ClaudeCodeHookManager
    from exxec import ExecutionEnvironment
    from agentpool.hooks import AgentHooks

    import inspect

    sig = inspect.signature(ClaudeCodeHookManager.__init__)

    # Check parameter names
    params = list(sig.parameters.keys())
    expected_params = ["self", "agent", "agent_hooks", "set_mode", "env"]

    for param in expected_params:
        assert param in params, f"Missing parameter: {param}"

    # Check that unexpected params are NOT present
    unexpected_params = ["event_queue", "get_session_id", "injection_manager"]
    for param in unexpected_params:
        assert param not in params, f"Unexpected parameter found: {param}"

    print("✓ ClaudeCodeHookManager signature is correct")


def test_hook_manager_initialization():
    """Test ClaudeCodeHookManager initialization with correct parameters."""

    from agentpool.agents.claude_code_agent.hook_manager import ClaudeCodeHookManager

    # Create a mock agent
    class MockAgent:
        name = "test_agent"

    agent = MockAgent()

    # Initialize with correct parameters only
    hook_manager = ClaudeCodeHookManager(
        agent=agent,
        agent_hooks=None,
        set_mode=None,
        env=None,
    )

    assert hook_manager.agent_name == "test_agent"
    assert hook_manager.agent_hooks is None
    assert hook_manager._agent is agent
    assert hook_manager._set_mode is None
    assert hook_manager._env is None

    print("✓ ClaudeCodeHookManager initialization works correctly")


def test_hook_manager_rejects_unexpected_params():
    """Test that ClaudeCodeHookManager rejects unexpected parameters."""

    from agentpool.agents.claude_code_agent.hook_manager import ClaudeCodeHookManager

    class MockAgent:
        name = "test_agent"

    agent = MockAgent()

    # Should raise TypeError with unexpected parameters
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        ClaudeCodeHookManager(
            agent=agent,
            agent_hooks=None,
            set_mode=None,
            env=None,
            event_queue=None,  # This should cause an error
        )

    print("✓ ClaudeCodeHookManager correctly rejects unexpected parameters")


def test_hook_manager_build_hooks():
    """Test that build_hooks works with correct initialization."""

    from agentpool.agents.claude_code_agent.hook_manager import ClaudeCodeHookManager

    class MockAgent:
        name = "test_agent"

    agent = MockAgent()

    hook_manager = ClaudeCodeHookManager(
        agent=agent,
        agent_hooks=None,
        set_mode=None,
        env=None,
    )

    hooks = hook_manager.build_hooks()

    assert isinstance(hooks, dict)
    assert "PostToolUse" in hooks
    assert len(hooks["PostToolUse"]) > 0

    print("✓ ClaudeCodeHookManager.build_hooks works correctly")


def test_hook_manager_with_set_mode():
    """Test ClaudeCodeHookManager with set_mode callback."""

    from agentpool.agents.claude_code_agent.hook_manager import ClaudeCodeHookManager

    class MockAgent:
        name = "test_agent"

    agent = MockAgent()

    async def mock_set_mode(mode_id: str, category_id: str) -> None:
        pass

    hook_manager = ClaudeCodeHookManager(
        agent=agent,
        agent_hooks=None,
        set_mode=mock_set_mode,
        env=None,
    )

    assert hook_manager._set_mode is mock_set_mode

    print("✓ ClaudeCodeHookManager with set_mode works correctly")


def test_hook_manager_with_env():
    """Test ClaudeCodeHookManager with execution environment."""

    from agentpool.agents.claude_code_agent.hook_manager import ClaudeCodeHookManager
    from exxec.mock_provider import MockExecutionEnvironment

    class MockAgent:
        name = "test_agent"

    agent = MockAgent()

    env = MockExecutionEnvironment()

    hook_manager = ClaudeCodeHookManager(
        agent=agent,
        agent_hooks=None,
        set_mode=None,
        env=env,
    )

    assert hook_manager._env is env

    print("✓ ClaudeCodeHookManager with env works correctly")


if __name__ == "__main__":
    print("Testing ClaudeCodeHookManager parameter corrections...\n")
    test_hook_manager_signature()
    test_hook_manager_initialization()
    test_hook_manager_rejects_unexpected_params()
    test_hook_manager_build_hooks()
    test_hook_manager_with_set_mode()
    test_hook_manager_with_env()
    print("\n✓ All ClaudeCodeHookManager tests passed!")
