"""Tests for RunHandle.active_agent_run wired through RunExecutor.

Covers:
- Normal completion: active_agent_run is cleared after execute() finishes
- Exception path: active_agent_run is cleared when agentlet raises
- Cancellation path: active_agent_run is cleared when consumer is cancelled
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest
from pydantic_ai.models.test import TestModel

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.messaging import ChatMessage, MessageHistory
from agentpool.orchestrator.run import RunHandle
from agentpool.orchestrator.run_executor import RunExecutor


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_agent() -> Agent[None]:
    """Agent with instant TestModel."""
    model = TestModel(custom_output_text="Hello from test")
    return Agent(name="active-agent-run-test-agent", model=model)


@pytest.fixture
def run_ctx() -> AgentRunContext:
    """Fresh AgentRunContext for each test."""
    return AgentRunContext()


@pytest.fixture
def message_history() -> MessageHistory:
    """Empty message history."""
    return MessageHistory()


@pytest.fixture
def run_handle() -> RunHandle:
    """Fresh RunHandle for each test."""
    return RunHandle(
        run_id="test-run-id",
        session_id="test-session",
        agent_type="native",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _collect_events(
    executor: RunExecutor,
    *,
    prompts: list[str],
    run_ctx: AgentRunContext,
    user_msg: ChatMessage[Any],
    message_history: MessageHistory,
    session_id: str = "test-session",
) -> list[Any]:
    """Execute RunExecutor and collect all events."""
    events: list[Any] = []
    async for event in executor.execute(
        prompts=prompts,
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
        message_id="msg-1",
        session_id=session_id,
    ):
        events.append(event)
    return events


# ---------------------------------------------------------------------------
# Normal completion: active_agent_run cleared after execute() finishes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_active_agent_run_none_after_normal_completion(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
    run_handle: RunHandle,
) -> None:
    """active_agent_run must be None after normal execute() completion."""
    executor = RunExecutor(test_agent, run_handle=run_handle)
    user_msg = ChatMessage.user_prompt("Say hello")

    await _collect_events(
        executor,
        prompts=["Say hello"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
    )

    assert run_handle.active_agent_run is None, (
        f"Expected active_agent_run to be None after normal completion, "
        f"got {run_handle.active_agent_run}"
    )


# ---------------------------------------------------------------------------
# Exception path: active_agent_run cleared after agentlet raises
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_active_agent_run_none_after_exception(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
    run_handle: RunHandle,
) -> None:
    """active_agent_run must be None when execution raises."""
    executor = RunExecutor(test_agent, run_handle=run_handle)
    user_msg = ChatMessage.user_prompt("Say hello")

    # Patch get_agentlet to raise immediately
    original_get_agentlet = test_agent.get_agentlet

    async def broken_get_agentlet(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("agentlet creation failed")

    test_agent.get_agentlet = broken_get_agentlet  # type: ignore[method-assign]

    try:
        with pytest.raises(RuntimeError, match="agentlet creation failed"):
            async for _event in executor.execute(
                prompts=["Say hello"],
                run_ctx=run_ctx,
                user_msg=user_msg,
                message_history=message_history,
                message_id="msg-1",
                session_id="sess-1",
            ):
                pass
    finally:
        test_agent.get_agentlet = original_get_agentlet  # type: ignore[method-assign]

    # After exception propagation, active_agent_run should be cleared
    assert run_handle.active_agent_run is None, (
        f"Expected active_agent_run to be None after exception, "
        f"got {run_handle.active_agent_run}"
    )


# ---------------------------------------------------------------------------
# Cancellation path: active_agent_run cleared on consumer cancellation
# ---------------------------------------------------------------------------


class SlowTestModel(TestModel):
    """TestModel that inserts a delay before yielding the streamed response."""

    def __init__(
        self,
        *,
        custom_output_text: str | None = None,
        pre_stream_delay: float = 0.3,
    ) -> None:
        super().__init__(custom_output_text=custom_output_text)
        self.pre_stream_delay = pre_stream_delay

    @asynccontextmanager
    async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):  # type: ignore[override]
        """Yield the streamed response after a configurable delay."""
        from pydantic_ai.models.test import TestStreamedResponse

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


@pytest.fixture
def slow_agent() -> Agent[None]:
    """Agent with SlowTestModel for cancellation testing."""
    model = SlowTestModel(
        custom_output_text="Slow response",
        pre_stream_delay=0.3,
    )
    return Agent(name="run-executor-slow-agent", model=model)


@pytest.mark.anyio
async def test_active_agent_run_none_after_cancellation(
    slow_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
    run_handle: RunHandle,
) -> None:
    """active_agent_run must be None after consumer cancellation."""
    executor = RunExecutor(slow_agent, run_handle=run_handle)
    user_msg = ChatMessage.user_prompt("Say hello")

    async def consume() -> None:
        async for event in executor.execute(
            prompts=["Say hello"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=message_history,
            message_id="msg-1",
            session_id="sess-1",
        ):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # Let iteration start
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # After cancellation, active_agent_run should be cleared
    assert run_handle.active_agent_run is None, (
        f"Expected active_agent_run to be None after cancellation, "
        f"got {run_handle.active_agent_run}"
    )
