"""Tests for child_done_events mechanism (OpenSpec tasks 8.1-8.18).

Covers:
- complete_background_task() ordering, error handling, edge cases
- RunExecutor re-iteration loop with child_done_events
- close_session snapshot+set+clear semantics
- _run_turn_unlocked finally safety net
- Synchronous child, safety net without steer, multiple concurrent children
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Literal
from unittest.mock import MagicMock

import anyio

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters, ModelSettings
from pydantic_ai.models.test import TestModel

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunStartedEvent, StreamCompleteEvent
from agentpool.messaging import ChatMessage, MessageHistory
from agentpool.orchestrator.core import EventBus, SessionPool
from agentpool.orchestrator.run import RunHandle, RunStatus
from agentpool.orchestrator.run_executor import RunExecutor


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_steer_callback(calls: list[tuple[str, str]]) -> Any:
    """Create a steer_callback that records its calls."""

    async def steer_callback(session_id: str, message: str) -> bool:
        calls.append((session_id, message))
        return True

    return steer_callback


class SequencedTestModel(TestModel):
    """TestModel that produces different output text on each text-producing call."""

    def __init__(
        self,
        *,
        outputs: list[str],
        call_tools: list[str] | Literal["all"] = "all",
    ) -> None:
        super().__init__(call_tools=call_tools)
        self._outputs = outputs
        self._text_call_index = 0

    def _request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        if self._text_call_index < len(self._outputs):
            self.custom_output_text = self._outputs[self._text_call_index]
        else:
            self.custom_output_text = self._outputs[-1]

        result = super()._request(messages, model_settings, model_request_parameters)

        if any(isinstance(p, TextPart) for p in result.parts):
            self._text_call_index += 1

        return result


class _MockAgent:
    """Minimal mock agent for SessionPool/TurnRunner tests."""

    AGENT_TYPE: str = "native"

    def __init__(self, name: str = "mock-agent") -> None:
        self.name: str = name
        self.get_active_run_context: Any = MagicMock(return_value=None)
        self._stream_impl: Any = None

    async def run_stream(
        self,
        *prompts: Any,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        if self._stream_impl is not None:
            run_ctx: Any = kwargs.get("run_ctx")
            if inspect.isasyncgenfunction(self._stream_impl):
                async for event in self._stream_impl(run_ctx, *prompts, **kwargs):
                    yield event
            else:
                await self._stream_impl(run_ctx, *prompts, **kwargs)
        yield RunStartedEvent(session_id=session_id or "", run_id="run-mock")

    async def _run_stream_once(
        self,
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> Any:
        if self._stream_impl is not None:
            if inspect.isasyncgenfunction(self._stream_impl):
                async for event in self._stream_impl(run_ctx, *prompts, **kwargs):
                    yield event
            else:
                await self._stream_impl(run_ctx, *prompts, **kwargs)
        yield RunStartedEvent(session_id="", run_id="run-mock")


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return a mocked AgentPool."""
    pool = MagicMock()
    pool.main_agent = MagicMock()
    pool.main_agent.name = "main-agent"
    pool.manifest = MagicMock()
    pool.manifest.agents = {}
    return pool


async def _setup_session(
    ctrl: Any,
    session_id: str,
    agent: _MockAgent,
    mock_pool: MagicMock,
) -> None:
    """Create a session and attach the mock agent directly."""
    state, _ = await ctrl.get_or_create_session(session_id)
    state.agent = agent  # type: ignore[assignment]
    ctrl._session_agents[session_id] = agent  # type: ignore[assignment]
    mock_pool.get_agent.return_value = agent


# ---------------------------------------------------------------------------
# 8.1: create_child_session registers done_event on parent run_ctx.child_done_events
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_child_session_registers_done_event() -> None:
    """create_child_session registers a done_event on parent run_ctx.child_done_events.

    Given: An AgentContext with run_ctx set and an agent_pool with session_pool.
    When: create_child_session is called.
    Then: run_ctx.child_done_events contains a new anyio.Event keyed by child_session_id.
    """
    from pydantic_ai.models.test import TestModel

    from agentpool.agents.context import AgentContext

    run_ctx = AgentRunContext(session_id="parent-session")
    agent = Agent(name="test-agent", model=TestModel())
    # agent.agent_pool is None by default → ephemeral ID path

    ctx: AgentContext[Any] = agent.get_context(run_ctx=run_ctx)

    child_sid = await ctx.create_child_session(
        agent_name="child-agent",
        agent_type="native",
    )

    assert child_sid in run_ctx.child_done_events
    assert isinstance(run_ctx.child_done_events[child_sid], anyio.Event)


# ---------------------------------------------------------------------------
# 8.2: complete_background_task() calls steer_callback before setting done_event
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_complete_background_task_calls_steer_before_set() -> None:
    """complete_background_task calls steer_callback BEFORE setting done_event.

    Given: A run_ctx with steer_callback set and a child_done_event registered.
    When: complete_background_task is called.
    Then: steer_callback is called first, then the done_event is set.
    """
    run_ctx = AgentRunContext(session_id="parent-session")
    steer_calls: list[tuple[str, str]] = []
    run_ctx.steer_callback = _make_steer_callback(steer_calls)

    child_sid = "child-1"
    done_event = anyio.Event()
    run_ctx.child_done_events[child_sid] = done_event

    set_at_call_count: list[int] = []

    async def _check_order() -> None:
        """Wait for the done_event and record how many steer calls happened by then."""
        await done_event.wait()
        set_at_call_count.append(len(steer_calls))

    checker = asyncio.create_task(_check_order())

    await run_ctx.complete_background_task(child_sid, "result message")

    await checker

    assert len(steer_calls) == 1
    assert steer_calls[0] == ("parent-session", "result message")
    # The done_event was set after steer_callback was called
    assert set_at_call_count[0] == 1
    # The event was popped from child_done_events
    assert child_sid not in run_ctx.child_done_events


# ---------------------------------------------------------------------------
# 8.3: complete_background_task() with unknown child_session_id still calls steer_callback
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_complete_background_task_unknown_child_still_calls_steer() -> None:
    """complete_background_task with unknown child_session_id still calls steer_callback.

    Given: A run_ctx with steer_callback set but no matching child_done_event.
    When: complete_background_task is called with an unknown child_session_id.
    Then: steer_callback is still called; no crash.
    """
    run_ctx = AgentRunContext(session_id="parent-session")
    steer_calls: list[tuple[str, str]] = []
    run_ctx.steer_callback = _make_steer_callback(steer_calls)

    # No child_done_events registered
    assert len(run_ctx.child_done_events) == 0

    await run_ctx.complete_background_task("unknown-child", "result")

    assert len(steer_calls) == 1
    assert steer_calls[0] == ("parent-session", "result")


# ---------------------------------------------------------------------------
# 8.4: complete_background_task() when steer_callback is None
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_complete_background_task_no_steer_callback() -> None:
    """complete_background_task when steer_callback is None — skips steer, sets event.

    Given: A run_ctx with steer_callback=None and a child_done_event registered.
    When: complete_background_task is called.
    Then: done_event is set; no crash (warning logged).
    """
    run_ctx = AgentRunContext(session_id="parent-session")
    run_ctx.steer_callback = None

    child_sid = "child-none-steer"
    done_event = anyio.Event()
    run_ctx.child_done_events[child_sid] = done_event

    await run_ctx.complete_background_task(child_sid, "result")

    assert done_event.is_set()
    assert child_sid not in run_ctx.child_done_events


# ---------------------------------------------------------------------------
# 8.5: complete_background_task() when steer_callback raises
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_complete_background_task_steer_callback_raises() -> None:
    """complete_background_task catches steer_callback exceptions, still sets event.

    Given: A run_ctx with a steer_callback that raises ValueError.
    When: complete_background_task is called.
    Then: Exception is caught (logged), done_event is still set.
    """
    run_ctx = AgentRunContext(session_id="parent-session")

    async def raising_steer(session_id: str, message: str) -> bool:
        msg = "steer boom"
        raise ValueError(msg)

    run_ctx.steer_callback = raising_steer

    child_sid = "child-raise"
    done_event = anyio.Event()
    run_ctx.child_done_events[child_sid] = done_event

    # Should NOT raise
    await run_ctx.complete_background_task(child_sid, "result")

    assert done_event.is_set()
    assert child_sid not in run_ctx.child_done_events


# ---------------------------------------------------------------------------
# 8.6: complete_background_task() called twice — second finds key missing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_complete_background_task_called_twice() -> None:
    """complete_background_task called twice — second call finds key missing.

    Given: A run_ctx with steer_callback and one child_done_event.
    When: complete_background_task is called twice with the same child_session_id.
    Then: First call pops+sets the event; second call still calls steer but pop returns None.
    """
    run_ctx = AgentRunContext(session_id="parent-session")
    steer_calls: list[tuple[str, str]] = []
    run_ctx.steer_callback = _make_steer_callback(steer_calls)

    child_sid = "child-twice"
    done_event = anyio.Event()
    run_ctx.child_done_events[child_sid] = done_event

    # First call
    await run_ctx.complete_background_task(child_sid, "first result")
    assert done_event.is_set()
    assert child_sid not in run_ctx.child_done_events
    assert len(steer_calls) == 1

    # Second call — key already popped, but steer still fires
    await run_ctx.complete_background_task(child_sid, "second result")
    assert len(steer_calls) == 2
    assert steer_calls[1] == ("parent-session", "second result")


# ---------------------------------------------------------------------------
# 8.7: RunExecutor waits on child_done_events when non-empty
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_executor_waits_on_child_done_events() -> None:
    """RunExecutor waits on child_done_events when the dict is non-empty.

    Given: A run_ctx with a child_done_event that will be set after 50ms.
    When: RunExecutor.execute is called with a tool that registers the event.
    Then: Execute blocks until the event is set, then re-iterates.
    """
    session_id = "test-wait"
    run_ctx = AgentRunContext(session_id=session_id)
    steer_calls: list[tuple[str, str]] = []
    run_ctx.steer_callback = _make_steer_callback(steer_calls)

    child_sid = "child-wait"
    tool_called = False

    async def spawn_tool() -> str:
        nonlocal tool_called
        if tool_called:
            return "already"
        tool_called = True
        run_ctx.child_done_events[child_sid] = anyio.Event()

        async def _complete() -> None:
            await anyio.sleep(0.05)
            assert run_ctx.steer_callback is not None
            await run_ctx.steer_callback(session_id, "done")
            ev = run_ctx.child_done_events.pop(child_sid, None)
            if ev is not None:
                ev.set()

        asyncio.create_task(_complete())
        return "started"

    model = SequencedTestModel(outputs=["Initial", "Re-iterated"])
    agent = Agent(name="wait-test", model=model, tools=[spawn_tool])
    executor = RunExecutor(agent)

    event_bus = EventBus()
    stream = await event_bus.subscribe(session_id, scope="session")

    result = await executor.execute(
        prompts=["Start"],
        run_ctx=run_ctx,
        user_msg=ChatMessage.user_prompt("Start"),
        message_history=MessageHistory(),
        message_id="msg-1",
        session_id=session_id,
        event_bus=event_bus,
    )

    await event_bus.close_session(session_id)
    async with stream:
        pass

    assert result is not None
    # Steer was called, proving the background task completed and triggered re-iteration
    assert len(steer_calls) == 1


# ---------------------------------------------------------------------------
# 8.8: RunExecutor skips wait when child_done_events is empty
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_executor_skips_wait_when_empty() -> None:
    """RunExecutor skips the wait when child_done_events is empty.

    Given: A run_ctx with no child_done_events.
    When: RunExecutor.execute completes the first iteration.
    Then: No wait occurs — execute returns immediately after first iteration.
    """
    session_id = "test-no-wait"
    run_ctx = AgentRunContext(session_id=session_id)

    model = TestModel(custom_output_text="Done")
    agent = Agent(name="no-wait-test", model=model)
    executor = RunExecutor(agent)

    event_bus = EventBus()
    stream = await event_bus.subscribe(session_id, scope="session")

    result = await executor.execute(
        prompts=["Hello"],
        run_ctx=run_ctx,
        user_msg=ChatMessage.user_prompt("Hello"),
        message_history=MessageHistory(),
        message_id="msg-1",
        session_id=session_id,
        event_bus=event_bus,
    )

    await event_bus.close_session(session_id)
    async with stream:
        pass

    assert result is not None
    # No child_done_events were registered
    assert len(run_ctx.child_done_events) == 0


# ---------------------------------------------------------------------------
# 8.9: RunExecutor reset uses child_done_events.clear()
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_executor_clears_child_done_events_on_reiterate() -> None:
    """RunExecutor clears child_done_events after re-iteration.

    Given: A run_ctx with a child_done_event that gets set.
    When: RunExecutor re-iterates and the loop completes.
    Then: child_done_events is cleared after the re-iteration.
    """
    session_id = "test-clear"
    run_ctx = AgentRunContext(session_id=session_id)
    steer_calls: list[tuple[str, str]] = []
    run_ctx.steer_callback = _make_steer_callback(steer_calls)

    child_sid = "child-clear"
    tool_called = False

    async def spawn_tool() -> str:
        nonlocal tool_called
        if tool_called:
            return "already"
        tool_called = True
        run_ctx.child_done_events[child_sid] = anyio.Event()

        async def _complete() -> None:
            await anyio.sleep(0.05)
            assert run_ctx.steer_callback is not None
            await run_ctx.steer_callback(session_id, "done")
            ev = run_ctx.child_done_events.pop(child_sid, None)
            if ev is not None:
                ev.set()

        asyncio.create_task(_complete())
        return "started"

    model = SequencedTestModel(outputs=["Initial", "Re-iterated"])
    agent = Agent(name="clear-test", model=model, tools=[spawn_tool])
    executor = RunExecutor(agent)

    event_bus = EventBus()
    stream = await event_bus.subscribe(session_id, scope="session")

    await executor.execute(
        prompts=["Start"],
        run_ctx=run_ctx,
        user_msg=ChatMessage.user_prompt("Start"),
        message_history=MessageHistory(),
        message_id="msg-1",
        session_id=session_id,
        event_bus=event_bus,
    )

    await event_bus.close_session(session_id)
    async with stream:
        pass

    # After re-iteration, child_done_events should be empty
    assert len(run_ctx.child_done_events) == 0


# ---------------------------------------------------------------------------
# 8.10: close_session snapshots, sets all, clears dict
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_close_session_sets_all_and_clears(
    mock_pool: MagicMock,
) -> None:
    """close_session snapshots child_done_events, sets all, then clears.

    Given: A SessionPool with an active run that has 2 pending child_done_events.
    When: close_session is called.
    Then: Both events are set, and child_done_events is cleared.
    """
    stream_started = asyncio.Event()

    async def stream_with_bg_wait(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> Any:
        stream_started.set()
        run_ctx.child_done_events["child-a"] = anyio.Event()
        run_ctx.child_done_events["child-b"] = anyio.Event()
        # Wait for all events (or close_session to set them)
        for ev in list(run_ctx.child_done_events.values()):
            await asyncio.wait_for(ev.wait(), timeout=10.0)
        yield StreamCompleteEvent(
            message=ChatMessage(content="done", role="assistant"),
            cancelled=run_ctx.cancelled,
            session_id="sess-close-1",
        )

    agent = _MockAgent(name="close-test-agent")
    agent._stream_impl = stream_with_bg_wait

    session_pool = SessionPool(pool=mock_pool, enable_auto_resume=False)
    ctrl = session_pool.sessions

    await _setup_session(ctrl, "sess-close-1", agent, mock_pool)

    # Start the run
    await ctrl.receive_request("sess-close-1", "hello", priority="when_idle")
    await asyncio.wait_for(stream_started.wait(), timeout=2.0)

    # Verify events are registered
    session = ctrl.get_session("sess-close-1")
    assert session is not None
    assert session.current_run_id is not None
    run_handle = ctrl._runs.get(session.current_run_id)
    assert run_handle is not None
    assert run_handle.run_ctx is not None
    assert len(run_handle.run_ctx.child_done_events) == 2

    # close_session should set all events and clear the dict
    await asyncio.wait_for(session_pool.close_session("sess-close-1"), timeout=5.0)

    # After close, events should be set and dict cleared
    assert run_handle.run_ctx.cancelled is True
    assert len(run_handle.run_ctx.child_done_events) == 0


# ---------------------------------------------------------------------------
# 8.11: _run_turn_unlocked finally sets parent done_event via .pop(key, None)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_finally_sets_parent_done_event(mock_pool: MagicMock) -> None:
    """_run_turn_unlocked finally pops and sets parent done_event.

    Given: A child session with parent_session_id set, parent has child_done_event registered.
    When: The child's turn completes (without calling complete_background_task).
    Then: The finally block pops and sets the parent's done_event.
    """
    async def quick_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> Any:
        yield RunStartedEvent(session_id="child-sess", run_id="run-child")

    agent = _MockAgent(name="child-agent")
    agent._stream_impl = quick_stream

    session_pool = SessionPool(pool=mock_pool, enable_auto_resume=False)
    ctrl = session_pool.sessions

    # Create parent session
    parent_state, _ = await ctrl.get_or_create_session("parent-sess")

    # Create child session with parent FIRST, then set up agent
    child_state, _ = await ctrl.get_or_create_session(
        "child-sess", parent_session_id="parent-sess"
    )
    child_state.agent = agent  # type: ignore[assignment]
    ctrl._session_agents["child-sess"] = agent  # type: ignore[assignment]
    mock_pool.get_agent.return_value = agent

    # Set up parent run with child_done_event
    parent_run_handle = RunHandle(
        run_id="parent-run-1",
        session_id="parent-sess",
        agent_type="native",
    )
    parent_run_handle.status = RunStatus.running
    parent_run_handle.run_ctx = AgentRunContext(session_id="parent-sess")
    parent_run_handle.run_ctx.child_done_events["child-sess"] = anyio.Event()
    ctrl._runs["parent-run-1"] = parent_run_handle
    parent_state.current_run_id = "parent-run-1"

    # The child's done_event should NOT be set yet
    assert not parent_run_handle.run_ctx.child_done_events["child-sess"].is_set()

    # Start child run via receive_request — finally should set the event
    await ctrl.receive_request("child-sess", "hello", priority="when_idle")
    await asyncio.sleep(0.3)  # Let the turn complete

    # The finally block should have popped and set the done_event
    assert "child-sess" not in parent_run_handle.run_ctx.child_done_events


# ---------------------------------------------------------------------------
# 8.12: finally is no-op when complete_background_task already popped key
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_finally_noop_when_already_popped(mock_pool: MagicMock) -> None:
    """finally is no-op when complete_background_task already popped the key.

    Given: A child session that calls complete_background_task before finishing.
    When: The finally block runs.
    Then: pop(key, None) returns None — no crash, no double-set.
    """
    async def quick_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> Any:
        yield RunStartedEvent(session_id="child-sess-2", run_id="run-child-2")

    agent = _MockAgent(name="child-agent-2")
    agent._stream_impl = quick_stream

    session_pool = SessionPool(pool=mock_pool, enable_auto_resume=False)
    ctrl = session_pool.sessions

    parent_state, _ = await ctrl.get_or_create_session("parent-sess-2")
    await _setup_session(ctrl, "child-sess-2", agent, mock_pool)
    await ctrl.get_or_create_session(
        "child-sess-2", parent_session_id="parent-sess-2"
    )

    parent_run_handle = RunHandle(
        run_id="parent-run-2",
        session_id="parent-sess-2",
        agent_type="native",
    )
    parent_run_handle.status = RunStatus.running
    parent_run_handle.run_ctx = AgentRunContext(session_id="parent-sess-2")
    parent_run_handle.run_ctx.child_done_events["child-sess-2"] = anyio.Event()
    ctrl._runs["parent-run-2"] = parent_run_handle
    parent_state.current_run_id = "parent-run-2"

    # Simulate complete_background_task already popping the key
    parent_run_handle.run_ctx.child_done_events.pop("child-sess-2", None)

    # Run the child turn — finally should be no-op (key already gone)
    await ctrl.receive_request("child-sess-2", "hello", priority="when_idle")
    await asyncio.sleep(0.3)

    # No crash, key still absent
    assert "child-sess-2" not in parent_run_handle.run_ctx.child_done_events


# ---------------------------------------------------------------------------
# 8.13: finally is no-op when parent run already completed (current_run_id is None)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_finally_noop_when_parent_run_completed(mock_pool: MagicMock) -> None:
    """finally is no-op when parent run already completed (current_run_id is None).

    Given: A child session whose parent has current_run_id=None (run already finished).
    When: The finally block runs.
    Then: parent_session.current_run_id is None → no parent_run_handle lookup → no-op.
    """
    async def quick_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> Any:
        yield RunStartedEvent(session_id="child-sess-3", run_id="run-child-3")

    agent = _MockAgent(name="child-agent-3")
    agent._stream_impl = quick_stream

    session_pool = SessionPool(pool=mock_pool, enable_auto_resume=False)
    ctrl = session_pool.sessions

    parent_state, _ = await ctrl.get_or_create_session("parent-sess-3")
    await _setup_session(ctrl, "child-sess-3", agent, mock_pool)
    await ctrl.get_or_create_session(
        "child-sess-3", parent_session_id="parent-sess-3"
    )

    # Parent run already completed — current_run_id is None
    parent_state.current_run_id = None

    # Run the child turn — finally should be no-op
    await ctrl.receive_request("child-sess-3", "hello", priority="when_idle")
    await asyncio.sleep(0.3)

    # No crash
    assert parent_state.current_run_id is None


# ---------------------------------------------------------------------------
# 8.14: finally is no-op when parent session/RunHandle/run_ctx not found
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_finally_noop_when_parent_not_found(mock_pool: MagicMock) -> None:
    """finally is no-op when parent session, RunHandle, or run_ctx is not found.

    Given: A child session whose parent session doesn't exist in the pool.
    When: The finally block runs.
    Then: parent_session is None → no-op (no crash).
    """
    async def quick_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> Any:
        yield RunStartedEvent(session_id="child-sess-4", run_id="run-child-4")

    agent = _MockAgent(name="child-agent-4")
    agent._stream_impl = quick_stream

    session_pool = SessionPool(pool=mock_pool, enable_auto_resume=False)
    ctrl = session_pool.sessions

    # Create child with a parent that doesn't exist
    await _setup_session(ctrl, "child-sess-4", agent, mock_pool)
    await ctrl.get_or_create_session(
        "child-sess-4", parent_session_id="nonexistent-parent"
    )

    # Run the child turn — finally should be no-op (parent not found)
    await ctrl.receive_request("child-sess-4", "hello", priority="when_idle")
    await asyncio.sleep(0.3)

    # No crash
    assert ctrl.get_session("child-sess-4") is not None


# ---------------------------------------------------------------------------
# 8.15: finally is no-op when parent_session_id is None (top-level)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_finally_noop_when_no_parent(mock_pool: MagicMock) -> None:
    """finally is no-op when parent_session_id is None (top-level session).

    Given: A top-level session with parent_session_id=None.
    When: The finally block runs.
    Then: parent_session_id is None → safety net skipped → no-op.
    """
    async def quick_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> Any:
        yield RunStartedEvent(session_id="top-sess", run_id="run-top")

    agent = _MockAgent(name="top-agent")
    agent._stream_impl = quick_stream

    session_pool = SessionPool(pool=mock_pool, enable_auto_resume=False)
    ctrl = session_pool.sessions

    state, _ = await ctrl.get_or_create_session("top-sess")
    assert state.parent_session_id is None
    await _setup_session(ctrl, "top-sess", agent, mock_pool)

    # Run the turn — finally should be no-op (no parent)
    await ctrl.receive_request("top-sess", "hello", priority="when_idle")
    await asyncio.sleep(0.3)

    # No crash, no child_done_events on this run_ctx
    assert ctrl.get_session("top-sess") is not None


# ---------------------------------------------------------------------------
# 8.16: synchronous child — done_event set before RunExecutor reaches re-iteration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_synchronous_child_event_set_before_reiteration() -> None:
    """Synchronous child — done_event set before RunExecutor reaches re-iteration.

    Given: A tool that registers a child_done_event and immediately sets it.
    When: RunExecutor reaches the re-iteration loop.
    Then: The event is already set — no blocking, re-iteration proceeds immediately.
    """
    session_id = "test-sync-child"
    run_ctx = AgentRunContext(session_id=session_id)
    steer_calls: list[tuple[str, str]] = []
    run_ctx.steer_callback = _make_steer_callback(steer_calls)

    child_sid = "child-sync"

    async def spawn_sync_tool() -> str:
        # Register and immediately set the event (synchronous child)
        run_ctx.child_done_events[child_sid] = anyio.Event()
        assert run_ctx.steer_callback is not None
        await run_ctx.steer_callback(session_id, "sync result")
        ev = run_ctx.child_done_events.pop(child_sid, None)
        if ev is not None:
            ev.set()
        return "done"

    model = SequencedTestModel(outputs=["Initial", "Re-iterated"])
    agent = Agent(name="sync-test", model=model, tools=[spawn_sync_tool])
    executor = RunExecutor(agent)

    event_bus = EventBus()
    stream = await event_bus.subscribe(session_id, scope="session")

    result = await executor.execute(
        prompts=["Start"],
        run_ctx=run_ctx,
        user_msg=ChatMessage.user_prompt("Start"),
        message_history=MessageHistory(),
        message_id="msg-1",
        session_id=session_id,
        event_bus=event_bus,
    )

    await event_bus.close_session(session_id)
    async with stream:
        pass

    assert result is not None
    # child_done_events should be empty after re-iteration
    assert len(run_ctx.child_done_events) == 0


# ---------------------------------------------------------------------------
# 8.17: safety net fires without steer when tool didn't call complete_background_task
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_safety_net_fires_without_steer(mock_pool: MagicMock) -> None:
    """Safety net fires without steer when tool didn't call complete_background_task.

    Given: A child session whose tool did NOT call complete_background_task.
    When: The child's turn finishes and the finally block runs.
    Then: The safety net pops and sets the parent's done_event (without steer).
    """
    async def quick_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> Any:
        # Tool does NOT call complete_background_task
        yield RunStartedEvent(session_id="child-safety", run_id="run-safety")

    agent = _MockAgent(name="safety-agent")
    agent._stream_impl = quick_stream

    session_pool = SessionPool(pool=mock_pool, enable_auto_resume=False)
    ctrl = session_pool.sessions

    parent_state, _ = await ctrl.get_or_create_session("parent-safety")
    # Create child session with parent FIRST, then set up agent
    child_state, _ = await ctrl.get_or_create_session(
        "child-safety", parent_session_id="parent-safety"
    )
    child_state.agent = agent  # type: ignore[assignment]
    ctrl._session_agents["child-safety"] = agent  # type: ignore[assignment]
    mock_pool.get_agent.return_value = agent

    parent_done_event = anyio.Event()
    parent_run_handle = RunHandle(
        run_id="parent-safety-run",
        session_id="parent-safety",
        agent_type="native",
    )
    parent_run_handle.status = RunStatus.running
    parent_run_handle.run_ctx = AgentRunContext(session_id="parent-safety")
    parent_run_handle.run_ctx.child_done_events["child-safety"] = parent_done_event
    ctrl._runs["parent-safety-run"] = parent_run_handle
    parent_state.current_run_id = "parent-safety-run"

    # Run child turn — tool doesn't call complete_background_task
    await ctrl.receive_request("child-safety", "hello", priority="when_idle")
    await asyncio.sleep(0.3)

    # Safety net should have set the event
    assert parent_done_event.is_set()
    # And popped it from the dict
    assert "child-safety" not in parent_run_handle.run_ctx.child_done_events


# ---------------------------------------------------------------------------
# 8.18: multiple concurrent children — all must complete before RunExecutor wakes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_multiple_children_all_must_complete() -> None:
    """Multiple concurrent children — all must complete before RunExecutor wakes.

    Given: A run_ctx with 3 child_done_events, each set at different times.
    When: RunExecutor waits in the re-iteration loop.
    Then: All 3 events must be set before the wait completes.
    """
    import time

    session_id = "test-multi"
    run_ctx = AgentRunContext(session_id=session_id)
    steer_calls: list[tuple[str, str]] = []
    run_ctx.steer_callback = _make_steer_callback(steer_calls)

    child_sids = ["child-a", "child-b", "child-c"]
    tool_called = False

    async def spawn_multi_tool() -> str:
        nonlocal tool_called
        if tool_called:
            return "already"
        tool_called = True

        # Register all 3 events
        for sid in child_sids:
            run_ctx.child_done_events[sid] = anyio.Event()

        # Complete them at different times: 50ms, 100ms, 150ms
        async def _complete(sid: str, delay: float) -> None:
            await anyio.sleep(delay)
            ev = run_ctx.child_done_events.pop(sid, None)
            if ev is not None:
                ev.set()

        for i, sid in enumerate(child_sids):
            asyncio.create_task(_complete(sid, 0.05 * (i + 1)))

        return "started"

    model = SequencedTestModel(outputs=["Initial", "Re-iterated"])
    agent = Agent(name="multi-test", model=model, tools=[spawn_multi_tool])
    executor = RunExecutor(agent)

    event_bus = EventBus()
    stream = await event_bus.subscribe(session_id, scope="session")

    start = time.perf_counter()
    result = await executor.execute(
        prompts=["Start"],
        run_ctx=run_ctx,
        user_msg=ChatMessage.user_prompt("Start"),
        message_history=MessageHistory(),
        message_id="msg-1",
        session_id=session_id,
        event_bus=event_bus,
    )
    elapsed = time.perf_counter() - start

    await event_bus.close_session(session_id)
    async with stream:
        pass

    assert result is not None
    # All events should be consumed
    assert len(run_ctx.child_done_events) == 0
    # Should have waited at least 150ms (the last child's delay)
    assert elapsed >= 0.15, f"Expected >= 150ms, got {elapsed * 1000:.0f}ms"
