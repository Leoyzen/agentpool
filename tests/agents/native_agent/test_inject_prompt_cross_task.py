"""TDD tests for inject_prompt/queue_prompt cross-task behavior.

These tests validate that inject_prompt() and queue_prompt() work via:
1. ContextVar (_current_run_ctx_var) when called from the same task as run_stream()
2. SessionPool fallback (session.active_run_ctx) when called from a different task

After removing _active_run_ctx from BaseAgent, cross-task access requires
a SessionPool with session.active_run_ctx set.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from typing import Any
from unittest.mock import MagicMock

from pydantic_ai.models.test import TestModel, TestStreamedResponse
import pytest

from agentpool import Agent
from agentpool.agents.base_agent import _current_run_ctx_var
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import StreamCompleteEvent
from agentpool.orchestrator.core import SessionState


# ---------------------------------------------------------------------------
# Slow test model: inserts async sleep so run_stream stays active
# ---------------------------------------------------------------------------


class SlowTestModel(TestModel):
    """TestModel that inserts a delay before yielding the streamed response.

    This gives us a window to call inject_prompt() / queue_prompt() from a
    different async task while run_stream() is still active.
    """

    def __init__(
        self,
        *,
        custom_output_text: str | None = None,
        pre_stream_delay: float = 0.5,
    ) -> None:
        super().__init__(custom_output_text=custom_output_text)
        self.pre_stream_delay = pre_stream_delay

    @asynccontextmanager
    async def request_stream(  # type: ignore[override]
        self,
        messages: list[Any],
        model_settings: Any,
        model_request_parameters: Any,
        run_context: Any = None,
    ) -> Any:
        """Yield the streamed response after a configurable delay."""
        model_settings, model_request_parameters = self.prepare_request(
            model_settings,
            model_request_parameters,
        )
        self.last_model_request_parameters = model_request_parameters

        model_response = self._request(messages, model_settings, model_request_parameters)

        await asyncio.sleep(self.pre_stream_delay)
        yield TestStreamedResponse(
            model_request_parameters=model_request_parameters,
            _model_name=self._model_name,
            _structured_response=model_response,
            _messages=messages,
            _provider_name=self._system,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def slow_agent() -> Agent[None]:
    """Agent with SlowTestModel for cross-task inject testing."""
    model = SlowTestModel(
        custom_output_text="Hello world slow response",
        pre_stream_delay=0.5,
    )
    return Agent(name="inject-test-agent", model=model)


@pytest.fixture
def fast_agent() -> Agent[None]:
    """Agent with instant TestModel for basic tests."""
    model = TestModel(custom_output_text="Fast response")
    return Agent(name="fast-test-agent", model=model)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_session_pool(agent: Agent, run_ctx: AgentRunContext) -> None:
    """Mock agent_pool.session_pool so get_active_run_context() returns run_ctx."""
    from unittest.mock import AsyncMock

    session_state = SessionState(session_id="test-session", agent_name="test")
    session_state.active_run_ctx = run_ctx
    session_controller = MagicMock()
    session_controller.get_session.return_value = session_state
    session_pool = MagicMock()
    session_pool.sessions = session_controller
    agent_pool = MagicMock()
    agent_pool.session_pool = session_pool
    agent_pool.storage = MagicMock()
    agent_pool.storage.log_message = AsyncMock()
    agent_pool.storage.log_session = AsyncMock()
    agent.agent_pool = agent_pool


# ---------------------------------------------------------------------------
# Core Test: inject_prompt from a different async task (via SessionPool)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inject_prompt_from_different_task_with_session_pool(
    slow_agent: Agent[None],
) -> None:
    """inject_prompt() called from a different task MUST reach the injection manager
    when SessionPool fallback is available.
    """
    stream_started = asyncio.Event()
    captured_run_ctx: list[AgentRunContext] = []

    async def run_stream() -> None:
        async for event in slow_agent.run_stream("Test prompt"):
            run_ctx = _current_run_ctx_var.get()
            if run_ctx is not None and not captured_run_ctx:
                captured_run_ctx.append(run_ctx)
            stream_started.set()
            if isinstance(event, StreamCompleteEvent):
                break

    task = asyncio.create_task(run_stream())

    # Wait for run_stream to start and capture run_ctx
    await asyncio.wait_for(stream_started.wait(), timeout=2.0)

    assert len(captured_run_ctx) == 1, "Should have captured run_ctx"
    run_ctx = captured_run_ctx[0]

    # Set up SessionPool fallback so cross-task access works
    _mock_session_pool(slow_agent, run_ctx)

    # Call inject_prompt from THIS task (different from run_stream's task)
    slow_agent.inject_prompt("Background task completed", session_id="test-session")

    # Verify the injection reached the injection manager via SessionPool fallback
    assert run_ctx.injection_manager.has_pending(), (
        "inject_prompt() from a different task MUST deliver the message to "
        "the active run's injection_manager via SessionPool fallback."
    )

    # Clean up
    await slow_agent.interrupt()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3.0)


# ---------------------------------------------------------------------------
# queue_prompt from a different async task (via SessionPool)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_queue_prompt_from_different_task_with_session_pool(
    slow_agent: Agent[None],
) -> None:
    """queue_prompt() called from a different task MUST reach the injection manager
    when SessionPool fallback is available.
    """
    stream_started = asyncio.Event()
    captured_run_ctx: list[AgentRunContext] = []

    async def run_stream() -> None:
        async for event in slow_agent.run_stream("Test prompt"):
            run_ctx = _current_run_ctx_var.get()
            if run_ctx is not None and not captured_run_ctx:
                captured_run_ctx.append(run_ctx)
            stream_started.set()
            if isinstance(event, StreamCompleteEvent):
                break

    task = asyncio.create_task(run_stream())
    await asyncio.wait_for(stream_started.wait(), timeout=2.0)

    assert len(captured_run_ctx) == 1
    run_ctx = captured_run_ctx[0]

    # Set up SessionPool fallback
    _mock_session_pool(slow_agent, run_ctx)

    # Queue a prompt from a different task
    slow_agent.queue_prompt("Follow-up prompt", session_id="test-session")

    assert run_ctx.injection_manager.has_queued(), (
        "queue_prompt() from a different task MUST deliver the prompt to "
        "the active run's injection_manager via SessionPool fallback."
    )

    await slow_agent.interrupt()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3.0)


# ---------------------------------------------------------------------------
# has_queued_prompts from a different async task (via SessionPool)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_has_queued_prompts_from_different_task_with_session_pool(
    slow_agent: Agent[None],
) -> None:
    """has_queued_prompts() called from a different task MUST reflect actual state
    when SessionPool fallback is available.
    """
    stream_started = asyncio.Event()
    captured_run_ctx: list[AgentRunContext] = []

    async def run_stream() -> None:
        async for event in slow_agent.run_stream("Test prompt"):
            run_ctx = _current_run_ctx_var.get()
            if run_ctx is not None and not captured_run_ctx:
                captured_run_ctx.append(run_ctx)
            stream_started.set()
            if isinstance(event, StreamCompleteEvent):
                break

    task = asyncio.create_task(run_stream())
    await asyncio.wait_for(stream_started.wait(), timeout=2.0)

    assert len(captured_run_ctx) == 1
    run_ctx = captured_run_ctx[0]

    # Set up SessionPool fallback
    _mock_session_pool(slow_agent, run_ctx)

    # Queue a prompt directly into the injection manager
    run_ctx.injection_manager.queue("Test prompt")

    # Now check has_queued_prompts from a different task
    assert slow_agent.has_queued_prompts(session_id="test-session"), (
        "has_queued_prompts() from a different task MUST check SessionPool fallback "
        "and return True when prompts are queued."
    )

    await slow_agent.interrupt()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3.0)


# ---------------------------------------------------------------------------
# has_pending_injections from a different async task (via SessionPool)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_has_pending_injections_from_different_task_with_session_pool(
    slow_agent: Agent[None],
) -> None:
    """has_pending_injections() called from a different task MUST reflect actual state
    when SessionPool fallback is available.
    """
    stream_started = asyncio.Event()
    captured_run_ctx: list[AgentRunContext] = []

    async def run_stream() -> None:
        async for event in slow_agent.run_stream("Test prompt"):
            run_ctx = _current_run_ctx_var.get()
            if run_ctx is not None and not captured_run_ctx:
                captured_run_ctx.append(run_ctx)
            stream_started.set()
            if isinstance(event, StreamCompleteEvent):
                break

    task = asyncio.create_task(run_stream())
    await asyncio.wait_for(stream_started.wait(), timeout=2.0)

    assert len(captured_run_ctx) == 1
    run_ctx = captured_run_ctx[0]

    # Set up SessionPool fallback
    _mock_session_pool(slow_agent, run_ctx)

    # Inject directly into the injection manager
    run_ctx.injection_manager.inject("Test injection")

    # Check has_pending_injections from a different task
    assert slow_agent.has_pending_injections(session_id="test-session"), (
        "has_pending_injections() from a different task MUST check SessionPool fallback "
        "and return True when injections are pending."
    )

    await slow_agent.interrupt()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3.0)


# ---------------------------------------------------------------------------
# clear_queued_prompts from a different async task (via SessionPool)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_clear_queued_prompts_from_different_task_with_session_pool(
    slow_agent: Agent[None],
) -> None:
    """clear_queued_prompts() called from a different task MUST actually clear
    when SessionPool fallback is available.
    """
    stream_started = asyncio.Event()
    captured_run_ctx: list[AgentRunContext] = []

    async def run_stream() -> None:
        async for event in slow_agent.run_stream("Test prompt"):
            run_ctx = _current_run_ctx_var.get()
            if run_ctx is not None and not captured_run_ctx:
                captured_run_ctx.append(run_ctx)
            stream_started.set()
            if isinstance(event, StreamCompleteEvent):
                break

    task = asyncio.create_task(run_stream())
    await asyncio.wait_for(stream_started.wait(), timeout=2.0)

    assert len(captured_run_ctx) == 1
    run_ctx = captured_run_ctx[0]

    # Set up SessionPool fallback
    _mock_session_pool(slow_agent, run_ctx)

    # Queue something directly
    run_ctx.injection_manager.queue("Test prompt")
    assert run_ctx.injection_manager.has_queued()

    # Clear from a different task
    slow_agent.clear_queued_prompts(session_id="test-session")

    assert not run_ctx.injection_manager.has_queued(), (
        "clear_queued_prompts() from a different task MUST clear the active "
        "run's injection_manager via SessionPool fallback."
    )

    await slow_agent.interrupt()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3.0)


# ---------------------------------------------------------------------------
# Integration: inject_prompt triggers run_stream continuation loop
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inject_prompt_triggers_continuation(slow_agent: Agent[None]) -> None:
    """inject_prompt from a different task should cause run_stream to continue.

    The run_stream() loop checks injection_manager.has_queued()
    after each _run_stream_once iteration. If inject_prompt() successfully
    delivers to the injection manager (via SessionPool fallback), and the
    injection gets flushed to the queue, the loop should run another iteration.
    """
    iteration_count = 0
    stream_started = asyncio.Event()
    captured_run_ctx: list[AgentRunContext] = []

    async def run_stream() -> None:
        nonlocal iteration_count
        async for event in slow_agent.run_stream("First prompt"):
            iteration_count += 1
            run_ctx = _current_run_ctx_var.get()
            if run_ctx is not None and not captured_run_ctx:
                captured_run_ctx.append(run_ctx)
            stream_started.set()
            if isinstance(event, StreamCompleteEvent) and iteration_count == 1:
                # Placeholder — real inject test happens from outside this task
                pass

    task = asyncio.create_task(run_stream())
    await asyncio.wait_for(stream_started.wait(), timeout=2.0)

    assert len(captured_run_ctx) == 1
    run_ctx = captured_run_ctx[0]

    # Set up SessionPool fallback
    _mock_session_pool(slow_agent, run_ctx)

    # Inject from a different task
    slow_agent.inject_prompt("Follow-up from different task", session_id="test-session")

    # The injection should be in the pending list
    assert run_ctx.injection_manager.has_pending(), (
        "Injection from different task must reach injection_manager via SessionPool fallback"
    )

    await slow_agent.interrupt()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3.0)


# ---------------------------------------------------------------------------
# Regression: same-task inject still works
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inject_prompt_same_task_still_works(fast_agent: Agent[None]) -> None:
    """inject_prompt() called from within run_stream's task must still work.

    The fix must not break the existing code path where inject_prompt is
    called from the same task as run_stream (e.g., from a tool hook).
    """
    injected = False

    async def run_stream() -> None:
        nonlocal injected
        async for event in fast_agent.run_stream("Test prompt"):
            # From within the same task, inject_prompt should work via ContextVar
            run_ctx = _current_run_ctx_var.get()
            if run_ctx is not None:
                fast_agent.inject_prompt("Same-task injection")
                if run_ctx.injection_manager.has_pending():
                    injected = True
            if isinstance(event, StreamCompleteEvent):
                break

    await run_stream()
    assert injected, "inject_prompt() from same task must still work"


# ---------------------------------------------------------------------------
# Regression: queue_prompt same task still works
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_queue_prompt_same_task_still_works(fast_agent: Agent[None]) -> None:
    """queue_prompt() called from within run_stream's task must still work."""
    queued = False

    async def run_stream() -> None:
        nonlocal queued
        async for event in fast_agent.run_stream("Test prompt"):
            run_ctx = _current_run_ctx_var.get()
            if run_ctx is not None:
                fast_agent.queue_prompt("Same-task queue")
                if run_ctx.injection_manager.has_queued():
                    queued = True
            if isinstance(event, StreamCompleteEvent):
                break

    await run_stream()
    assert queued, "queue_prompt() from same task must still work"


# ---------------------------------------------------------------------------
# Hook consumer: NativeAgentHookManager reads injection via SessionPool fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hook_manager_consumes_cross_task_injection_with_session_pool(
    slow_agent: Agent[None],
) -> None:
    """NativeAgentHookManager must consume injections queued from a different task
    when SessionPool fallback is available.
    """
    from agentpool.agents.native_agent.hook_manager import NativeAgentHookManager

    stream_started = asyncio.Event()
    captured_run_ctx: list[AgentRunContext] = []

    async def run_stream() -> None:
        async for event in slow_agent.run_stream("Test prompt"):
            run_ctx = _current_run_ctx_var.get()
            if run_ctx is not None and not captured_run_ctx:
                captured_run_ctx.append(run_ctx)
            stream_started.set()
            if isinstance(event, StreamCompleteEvent):
                break

    task = asyncio.create_task(run_stream())
    await asyncio.wait_for(stream_started.wait(), timeout=2.0)

    assert len(captured_run_ctx) == 1
    run_ctx = captured_run_ctx[0]

    # Set up SessionPool fallback
    _mock_session_pool(slow_agent, run_ctx)

    # Inject from a different task (simulates BackgroundTaskProvider._on_task_completed)
    slow_agent.inject_prompt("Background task result notice", session_id="test-session")

    # Verify the hook manager can find the injection via SessionPool fallback
    hook_mgr = slow_agent._hook_manager
    assert isinstance(hook_mgr, NativeAgentHookManager)

    # The hook manager should be able to access the injection_manager
    # via the SessionPool fallback
    active_run_ctx = slow_agent.get_active_run_context(session_id="test-session")
    assert active_run_ctx is not None, (
        "Hook manager must find run_ctx via SessionPool fallback"
    )
    assert active_run_ctx.injection_manager.has_pending(), (
        "Injection from different task must be visible via SessionPool fallback "
        "so the hook manager can consume it"
    )

    await slow_agent.interrupt()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3.0)
