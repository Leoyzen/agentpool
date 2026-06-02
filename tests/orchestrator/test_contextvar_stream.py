"""Test that _current_run_ctx_var is set during _run_stream_once and reset after.

Verifies RFC-0021 compliance: the ContextVar is active for the duration of the
stream and cleaned up once the generator is exhausted.
"""

from __future__ import annotations

import pytest
from pydantic_ai.models.test import TestModel

from agentpool import Agent
from agentpool.agents.base_agent import _current_run_ctx_var
from agentpool.agents.context import AgentRunContext


@pytest.fixture
def ctxvar_agent() -> Agent[None]:
    """Agent with instant TestModel for ContextVar testing."""
    model = TestModel(custom_output_text="Hello")
    return Agent(name="ctxvar-test-agent", model=model)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_contextvar_set_during_run_stream_once(ctxvar_agent: Agent[None]) -> None:
    """_current_run_ctx_var must be non-None during _run_stream_once and None after."""
    # Before stream starts
    assert _current_run_ctx_var.get() is None

    captured_ctx: AgentRunContext | None = None

    # Fully consume the stream so the generator's finally block runs naturally
    async for _event in ctxvar_agent.run_stream("Test prompt"):
        # During the stream _run_stream_once is active
        if captured_ctx is None:
            captured_ctx = _current_run_ctx_var.get()
            assert captured_ctx is not None, (
                "_current_run_ctx_var must be set during _run_stream_once"
            )
            assert isinstance(captured_ctx, AgentRunContext)

    # After stream completes the finally block in run_stream should have reset it
    assert _current_run_ctx_var.get() is None, (
        "_current_run_ctx_var must be reset to None after run_stream completes"
    )
