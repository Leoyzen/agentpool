"""Tests for deprecation warnings on ``inject_prompt`` and ``queue_prompt``.

Pooled native agents (those with ``agent_pool is not None``) should emit
a ``DeprecationWarning`` when calling ``inject_prompt()`` or ``queue_prompt()``,
guiding users toward ``TurnRunner.steer()`` and ``TurnRunner.followup()``.

Standalone native agents and non-native agents should NOT emit any warning.
"""

from __future__ import annotations

import warnings
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, AgentPool


TEST_RESPONSE = "I am a test response"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pooled_native_agent() -> Agent:
    """Create a native Agent with a mocked agent_pool / session_pool.

    The session_pool.turns object has real coroutine methods so that
    ``task_manager.fire_and_forget()`` can schedule them without error.
    """
    agent = Agent(name="test-agent", model=TestModel(custom_output_text=TEST_RESPONSE))
    agent.agent_pool = MagicMock(spec=AgentPool)
    turns = MagicMock()
    turns.steer = AsyncMock(return_value=True)
    turns.followup = AsyncMock(return_value=True)
    agent.agent_pool.session_pool.turns = turns
    agent._events.session_id = "test-session-id"
    return agent


# ---------------------------------------------------------------------------
# Pooled native agents — DeprecationWarning expected
# ---------------------------------------------------------------------------


def test_pooled_native_inject_prompt_deprecation_warning() -> None:
    """Pooled native inject_prompt() emits DeprecationWarning."""
    agent = _make_pooled_native_agent()

    with pytest.warns(DeprecationWarning, match="inject_prompt"):
        agent.inject_prompt("test message")


def test_pooled_native_queue_prompt_deprecation_warning() -> None:
    """Pooled native queue_prompt() emits DeprecationWarning."""
    agent = _make_pooled_native_agent()

    with pytest.warns(DeprecationWarning, match="queue_prompt"):
        agent.queue_prompt("follow-up message")


# ---------------------------------------------------------------------------
# Standalone native agent — no warning
# ---------------------------------------------------------------------------


def test_standalone_native_inject_prompt_no_deprecation() -> None:
    """Standalone native inject_prompt() does NOT emit DeprecationWarning."""
    agent = Agent(name="test-agent", model=TestModel(custom_output_text=TEST_RESPONSE))
    # agent_pool is None by default for standalone agents

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        # Should complete without raising DeprecationWarning
        agent.inject_prompt("test message")


# ---------------------------------------------------------------------------
# Non-native agent — no warning
# ---------------------------------------------------------------------------


def test_non_native_inject_prompt_no_deprecation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-native inject_prompt() does NOT emit DeprecationWarning.

    Uses a native Agent instance with ``AGENT_TYPE`` temporarily overridden to
    ``"acp"`` to simulate an ACP agent without spawning a subprocess.
    """
    agent = Agent(name="test-agent", model=TestModel(custom_output_text=TEST_RESPONSE))
    monkeypatch.setattr(agent, "AGENT_TYPE", "acp")

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        # Should complete without raising DeprecationWarning
        agent.inject_prompt("test message")
