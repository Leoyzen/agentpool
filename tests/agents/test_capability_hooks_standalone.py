"""Tests verifying pdai Capability hooks fire on standalone run path.

Phase 2 of thin-wrapper refactor: BaseAgent.run_stream() now delegates
directly to _run_stream_once() → _stream_events() → NativeTurn.execute()
which calls agent_run.next(node) explicitly, ensuring all pdai Capability
hooks fire on every run path.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic_ai.capabilities import AbstractCapability

from agentpool.agents.native_agent.agent import Agent
from agentpool.models.agents import NativeAgentConfig


class HookTrackerCapability(AbstractCapability[Any]):
    """Capability that records when wrap_run is called."""

    def __init__(self) -> None:
        super().__init__()
        self.wrap_run_called = False

    async def wrap_run(self, ctx: Any, *, handler: Any) -> Any:
        self.wrap_run_called = True
        return await handler()


pytestmark = [pytest.mark.unit, pytest.mark.anyio]


async def test_wrap_run_hook_fires_on_standalone_run() -> None:
    """wrap_run Capability hook SHALL fire on standalone run_stream()."""
    tracker = HookTrackerCapability()
    config = NativeAgentConfig(
        name="test-agent",
        model="test:test",
        system_prompt="You are a test agent.",
        capabilities=[tracker],
    )
    agent: Agent[Any, Any] = Agent(
        name="test-agent",
        model="test:test",
        agent_config=config,
    )

    async with agent:
        async for _event in agent.run_stream("Hello"):
            pass

    assert tracker.wrap_run_called, (
        "wrap_run hook did not fire on standalone run_stream() path — "
        "agent_run.next(node) may not be called on this path"
    )
