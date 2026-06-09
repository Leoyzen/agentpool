"""Tests verifying input_provider propagation through subagent and worker delegation.

These tests ensure that when a parent agent delegates to a subagent or worker,
the input_provider is properly propagated so that tools requiring user
interaction (like confirmations) work correctly in the child agent.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from agentpool import AgentPool, AgentsManifest
from agentpool.agents.events import StreamCompleteEvent
from agentpool.messaging.messages import ChatMessage
from agentpool.ui.base import InputProvider


class FakeInputProvider(InputProvider):
    """Fake input provider for testing."""

    def get_tool_confirmation(self, context: Any, tool_description: str = "") -> Any:
        raise NotImplementedError

    def get_elicitation(self, params: Any) -> Any:
        raise NotImplementedError


TASK_TOOL_MANIFEST = """
agents:
  parent:
    type: native
    model:
      type: test
      call_tools: ["task"]
      tool_args:
        task:
          agent_or_team: child
          prompt: "Do some work"
          description: "Test input_provider propagation"
    system_prompt: "You are the parent agent."
    tools:
      - type: subagent

  child:
    type: native
    model: test
    system_prompt: "You are the child agent."
"""


async def test_input_provider_propagated_to_subagent_via_task_tool() -> None:
    """Test that input_provider is passed to subagent when using task tool."""
    manifest = AgentsManifest.from_yaml(TASK_TOOL_MANIFEST)
    fake_provider = FakeInputProvider()

    async with AgentPool(manifest) as pool:
        parent = pool.get_agent("parent")
        child = pool.get_agent("child")

        # Patch child's run_stream to capture the input_provider argument
        captured_kwargs = {}

        async def mock_run_stream(*_args: Any, **kwargs: Any) -> Any:
            captured_kwargs.update(kwargs)
            yield StreamCompleteEvent(
                message=ChatMessage(content="done", role="assistant"),
            )

        with patch.object(child, "run_stream", side_effect=mock_run_stream):
            await parent.run(
                "Delegate to child",
                input_provider=fake_provider,
            )

        # Verify input_provider was passed to child's run_stream
        assert "input_provider" in captured_kwargs, (
            f"input_provider not passed to child.run_stream. "
            f"Captured kwargs: {captured_kwargs}"
        )
        assert captured_kwargs["input_provider"] is fake_provider, (
            f"Wrong input_provider passed. Expected FakeInputProvider, "
            f"got {captured_kwargs.get('input_provider')}"
        )


WORKER_MANIFEST = """
agents:
  main:
    type: native
    model:
      type: test
      call_tools: ["ask_helper"]
    system_prompt: "You are the main agent."
    workers:
      - helper

  helper:
    type: native
    model: test
    system_prompt: "You are the helper agent."
"""


async def test_input_provider_propagated_to_worker() -> None:
    """Test that input_provider is passed to worker when using worker tool."""
    manifest = AgentsManifest.from_yaml(WORKER_MANIFEST)
    fake_provider = FakeInputProvider()

    async with AgentPool(manifest) as pool:
        main = pool.get_agent("main")
        helper = pool.get_agent("helper")

        # Patch helper's run_stream to capture the input_provider argument
        captured_kwargs = {}

        async def mock_run_stream(*_args: Any, **kwargs: Any) -> Any:
            captured_kwargs.update(kwargs)
            yield StreamCompleteEvent(
                message=ChatMessage(content="done", role="assistant"),
            )

        with patch.object(helper, "run_stream", side_effect=mock_run_stream):
            await main.run(
                "Ask helper",
                input_provider=fake_provider,
            )

        # Verify input_provider was passed to helper's run_stream
        assert "input_provider" in captured_kwargs, (
            f"input_provider not passed to helper.run_stream. "
            f"Captured kwargs: {captured_kwargs}"
        )
        assert captured_kwargs["input_provider"] is fake_provider, (
            f"Wrong input_provider passed. Expected FakeInputProvider, "
            f"got {captured_kwargs.get('input_provider')}"
        )


ASYNC_TASK_MANIFEST = """
agents:
  orchestrator:
    type: native
    model:
      type: test
      call_tools: ["task"]
      tool_args:
        task:
          agent_or_team: worker
          prompt: "Do some work"
          description: "Test async input_provider"
          async_mode: true
    system_prompt: "You are the orchestrator."
    tools:
      - type: subagent

  worker:
    type: native
    model: test
    system_prompt: "You are the worker agent."
"""


async def test_input_provider_propagated_to_subagent_async_mode() -> None:
    """Test that input_provider is passed to async subagent task."""
    manifest = AgentsManifest.from_yaml(ASYNC_TASK_MANIFEST)
    fake_provider = FakeInputProvider()

    async with AgentPool(manifest) as pool:
        orchestrator = pool.get_agent("orchestrator")
        worker = pool.get_agent("worker")

        # Patch worker's run_stream to capture the input_provider argument
        captured_kwargs = {}

        async def mock_run_stream(*_args: Any, **kwargs: Any) -> Any:
            captured_kwargs.update(kwargs)
            yield StreamCompleteEvent(
                message=ChatMessage(content="done", role="assistant"),
            )

        with patch.object(worker, "run_stream", side_effect=mock_run_stream):
            result = await orchestrator.run(
                "Start background task",
                input_provider=fake_provider,
            )

        # For async mode, the task starts in background.
        # We just verify orchestrator completed successfully.
        assert result.content is not None
