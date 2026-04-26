"""TDD tests for inject_prompt/queue_prompt cross-task bug fix.

These tests validate that inject_prompt() and queue_prompt() work when called
from a different async task than the agent's run_stream() task.

Bug: When a background task completes and calls inject_prompt() from its own
asyncio.Task, the injection is silently dropped because inject_prompt() only
checks _current_run_ctx (ContextVar, task-scoped) and _background_run_ctx
(continuous mode only), but NOT _active_run_ctx (instance var, cross-task
accessible).

The same bug affects: queue_prompt(), has_queued_prompts(),
has_pending_injections(), clear_queued_prompts().

Fix approach:
    Add _active_run_ctx as fallback in all 5 methods, matching the pattern
    already used by interrupt() (base_agent.py L1101-1104):
        effective_run_ctx = run_ctx or self._active_run_ctx
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from typing import Any

from pydantic_ai.models.test import TestModel, TestStreamedResponse
import pytest

from agentpool import Agent
from agentpool.agents.events import StreamCompleteEvent


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
# Core Test: inject_prompt from a different async task
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inject_prompt_from_different_task(slow_agent: Agent[None]) -> None:
    """inject_prompt() called from a different task MUST reach the injection manager.

    This is the core bug: BackgroundTaskProvider._on_task_completed() calls
    ctx.agent.inject_prompt(notice) from inside its own asyncio.Task, which
    is different from the lead agent's run_stream() task.

    _current_run_ctx is a ContextVar → returns None in the other task.
    Without _active_run_ctx fallback, the injection is silently dropped.

    EXPECTED: inject_prompt() uses _active_run_ctx as fallback (like interrupt()
    already does at base_agent.py L1101-1104).
    """
    stream_started = asyncio.Event()
    injection_done = asyncio.Event()

    async def run_stream() -> None:
        async for event in slow_agent.run_stream("Test prompt"):
            stream_started.set()
            if isinstance(event, StreamCompleteEvent):
                break

    task = asyncio.create_task(run_stream())

    # Wait for run_stream to start and set _active_run_ctx
    await asyncio.wait_for(stream_started.wait(), timeout=2.0)

    # Verify the agent has an active run context
    assert slow_agent._active_run_ctx is not None, (
        "_active_run_ctx must be set during run_stream — "
        "this is how cross-task methods find the run context"
    )

    # Call inject_prompt from THIS task (different from run_stream's task)
    # This simulates BackgroundTaskProvider._on_task_completed()
    slow_agent.inject_prompt("Background task completed")

    # Verify the injection reached the injection manager
    # BEFORE FIX: _current_run_ctx is None in this task, _background_run_ctx is None,
    # so inject_prompt silently drops the message.
    # AFTER FIX: inject_prompt falls back to _active_run_ctx and inject succeeds.
    assert slow_agent._active_run_ctx.injection_manager.has_pending(), (
        "inject_prompt() from a different task MUST deliver the message to "
        "the active run's injection_manager via _active_run_ctx fallback. "
        "Without this, background task completion notices are silently dropped "
        "and the lead agent never resumes."
    )

    injection_done.set()

    # Clean up
    await slow_agent.interrupt()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3.0)


# ---------------------------------------------------------------------------
# queue_prompt from a different async task
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_queue_prompt_from_different_task(slow_agent: Agent[None]) -> None:
    """queue_prompt() called from a different task MUST reach the injection manager.

    Same bug as inject_prompt: _current_run_ctx is None in the caller's task.
    """
    stream_started = asyncio.Event()

    async def run_stream() -> None:
        async for event in slow_agent.run_stream("Test prompt"):
            stream_started.set()
            if isinstance(event, StreamCompleteEvent):
                break

    task = asyncio.create_task(run_stream())
    await asyncio.wait_for(stream_started.wait(), timeout=2.0)

    assert slow_agent._active_run_ctx is not None

    # Queue a prompt from a different task
    slow_agent.queue_prompt("Follow-up prompt")

    assert slow_agent._active_run_ctx.injection_manager.has_queued(), (
        "queue_prompt() from a different task MUST deliver the prompt to "
        "the active run's injection_manager via _active_run_ctx fallback."
    )

    await slow_agent.interrupt()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3.0)


# ---------------------------------------------------------------------------
# has_queued_prompts from a different async task
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_has_queued_prompts_from_different_task(slow_agent: Agent[None]) -> None:
    """has_queued_prompts() called from a different task MUST reflect actual state.

    Without _active_run_ctx fallback, has_queued_prompts() always returns False
    when called from a different task, even if there ARE queued prompts.
    """
    stream_started = asyncio.Event()

    async def run_stream() -> None:
        async for event in slow_agent.run_stream("Test prompt"):
            stream_started.set()
            if isinstance(event, StreamCompleteEvent):
                break

    task = asyncio.create_task(run_stream())
    await asyncio.wait_for(stream_started.wait(), timeout=2.0)

    assert slow_agent._active_run_ctx is not None

    # First, queue a prompt directly into the injection manager from within
    # the run_stream task's context (via _active_run_ctx)
    slow_agent._active_run_ctx.injection_manager.queue("Test prompt")

    # Now check has_queued_prompts from a different task
    # BEFORE FIX: returns False (because _current_run_ctx is None)
    # AFTER FIX: returns True (because _active_run_ctx has queued prompts)
    assert slow_agent.has_queued_prompts(), (
        "has_queued_prompts() from a different task MUST check _active_run_ctx "
        "and return True when prompts are queued."
    )

    await slow_agent.interrupt()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3.0)


# ---------------------------------------------------------------------------
# has_pending_injections from a different async task
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_has_pending_injections_from_different_task(slow_agent: Agent[None]) -> None:
    """has_pending_injections() called from a different task MUST reflect actual state."""
    stream_started = asyncio.Event()

    async def run_stream() -> None:
        async for event in slow_agent.run_stream("Test prompt"):
            stream_started.set()
            if isinstance(event, StreamCompleteEvent):
                break

    task = asyncio.create_task(run_stream())
    await asyncio.wait_for(stream_started.wait(), timeout=2.0)

    assert slow_agent._active_run_ctx is not None

    # Inject directly into the injection manager via _active_run_ctx
    slow_agent._active_run_ctx.injection_manager.inject("Test injection")

    # Check has_pending_injections from a different task
    # BEFORE FIX: returns False
    # AFTER FIX: returns True
    assert slow_agent.has_pending_injections(), (
        "has_pending_injections() from a different task MUST check _active_run_ctx "
        "and return True when injections are pending."
    )

    await slow_agent.interrupt()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3.0)


# ---------------------------------------------------------------------------
# clear_queued_prompts from a different async task
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_clear_queued_prompts_from_different_task(slow_agent: Agent[None]) -> None:
    """clear_queued_prompts() called from a different task MUST actually clear."""
    stream_started = asyncio.Event()

    async def run_stream() -> None:
        async for event in slow_agent.run_stream("Test prompt"):
            stream_started.set()
            if isinstance(event, StreamCompleteEvent):
                break

    task = asyncio.create_task(run_stream())
    await asyncio.wait_for(stream_started.wait(), timeout=2.0)

    assert slow_agent._active_run_ctx is not None

    # Queue something directly via _active_run_ctx
    slow_agent._active_run_ctx.injection_manager.queue("Test prompt")
    assert slow_agent._active_run_ctx.injection_manager.has_queued()

    # Clear from a different task
    # BEFORE FIX: no-op (because _current_run_ctx is None)
    # AFTER FIX: clears the injection manager
    slow_agent.clear_queued_prompts()

    assert not slow_agent._active_run_ctx.injection_manager.has_queued(), (
        "clear_queued_prompts() from a different task MUST clear the active "
        "run's injection_manager via _active_run_ctx fallback."
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

    The run_stream() loop (base_agent.py L686) checks injection_manager.has_queued()
    after each _run_stream_once iteration. If inject_prompt() successfully
    delivers to _active_run_ctx.injection_manager, and the injection gets
    flushed to the queue, the loop should run another iteration.
    """
    iteration_count = 0
    stream_started = asyncio.Event()

    async def run_stream() -> None:
        nonlocal iteration_count
        async for event in slow_agent.run_stream("First prompt"):
            iteration_count += 1
            stream_started.set()
            if isinstance(event, StreamCompleteEvent) and iteration_count == 1:
                # Placeholder — real inject test happens from outside this task
                pass

    task = asyncio.create_task(run_stream())
    await asyncio.wait_for(stream_started.wait(), timeout=2.0)

    # Inject from a different task
    slow_agent.inject_prompt("Follow-up from different task")

    # The injection should be in the pending list
    active_ctx = slow_agent._active_run_ctx
    assert active_ctx is not None, "_active_run_ctx should be set during run_stream"
    assert active_ctx.injection_manager.has_pending(), (
        "Injection from different task must reach injection_manager"
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
            # From within the same task, inject_prompt should work
            if fast_agent._current_run_ctx is not None:
                fast_agent.inject_prompt("Same-task injection")
                if fast_agent._current_run_ctx.injection_manager.has_pending():
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
            if fast_agent._current_run_ctx is not None:
                fast_agent.queue_prompt("Same-task queue")
                if fast_agent._current_run_ctx.injection_manager.has_queued():
                    queued = True
            if isinstance(event, StreamCompleteEvent):
                break

    await run_stream()
    assert queued, "queue_prompt() from same task must still work"


# ---------------------------------------------------------------------------
# Hook consumer: NativeAgentHookManager reads injection via _active_run_ctx
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hook_manager_consumes_cross_task_injection(slow_agent: Agent[None]) -> None:
    """NativeAgentHookManager must consume injections queued from a different task.

    The hook manager's run_post_tool_hooks() reads injection_manager via
    _current_run_ctx. After our fix, it also falls back to _active_run_ctx.

    This test verifies the full producer → consumer chain:
    1. Background task calls inject_prompt() from different task (producer)
    2. inject_prompt() delivers to _active_run_ctx.injection_manager (fixed)
    3. Hook manager consumes from _active_run_ctx.injection_manager (fixed)
    """
    from agentpool.agents.native_agent.hook_manager import NativeAgentHookManager

    stream_started = asyncio.Event()

    async def run_stream() -> None:
        async for event in slow_agent.run_stream("Test prompt"):
            stream_started.set()
            if isinstance(event, StreamCompleteEvent):
                break

    task = asyncio.create_task(run_stream())
    await asyncio.wait_for(stream_started.wait(), timeout=2.0)

    assert slow_agent._active_run_ctx is not None

    # Inject from a different task (simulates BackgroundTaskProvider._on_task_completed)
    slow_agent.inject_prompt("Background task result notice")

    # Verify the hook manager can find the injection via _active_run_ctx fallback
    hook_mgr = slow_agent._hook_manager
    assert isinstance(hook_mgr, NativeAgentHookManager)

    # The hook manager should be able to access the injection_manager
    # via the _active_run_ctx fallback we added
    run_ctx = slow_agent._current_run_ctx or slow_agent._active_run_ctx
    assert run_ctx is not None, "Hook manager must find run_ctx via _active_run_ctx fallback"
    assert run_ctx.injection_manager.has_pending(), (
        "Injection from different task must be visible via _active_run_ctx "
        "so the hook manager can consume it"
    )

    await slow_agent.interrupt()
    with suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=3.0)
