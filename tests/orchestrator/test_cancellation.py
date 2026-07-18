"""Tests for context preservation after run cancellation.

Regression tests for the bug where RunHandle._message_history was not
updated when a turn was cancelled, causing the next turn to start with
stale (empty) message history. The model would "forget" all previous
conversation context after a cancel.

Covers three bugs:
1. RunHandle.start() skips _message_history update on cancel (continue
   at line 293 skips line 300).
2. _start_run_handle() creates RunHandle with empty _message_history,
   never bridging agent.conversation (ChatMessage list) to
   list[ModelMessage].
3. NativeTurn.execute() Path B (CancelledError) does not capture
   _message_history from agent_run, unlike Path A (graceful cancel).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, AgentPool
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import (
    RunFailedEvent,
    RunStartedEvent,
    StreamCompleteEvent,
    ToolCallStartEvent,
)
from agentpool.agents.native_agent.turn import NativeTurn
from agentpool.lifecycle import RunState
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import EventBus, EventEnvelope, SessionPool, SessionState
from agentpool.orchestrator.run import RunHandle
from agentpool.orchestrator.session_pool_messaging import SessionPoolMessagingMixin
from agentpool.orchestrator.session_pool_runs import SessionPoolRunsMixin
from agentpool.orchestrator.turn import Turn


if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _BlockingTurnWithHistory(Turn):
    """Turn that sets _message_history, then blocks until cancelled.

    Simulates a turn that partially executes (accumulating messages)
    before being cancelled mid-stream.
    """

    def __init__(self, run_ctx: AgentRunContext, history: list[Any]) -> None:
        self._run_ctx = run_ctx
        self._history = history

    async def execute(self):  # type: ignore[override]
        # Simulate partial execution: messages were accumulated
        # before the cancel signal arrived.
        self._message_history = list(self._history)
        self._final_message = ChatMessage(
            content="partial response",
            role="assistant",
        )
        while not self._run_ctx.cancelled:
            await asyncio.sleep(0.01)
        return
        yield  # unreachable — makes this an async generator


class _StubTurn(Turn):
    """Minimal Turn that yields events and sets message history."""

    def __init__(
        self,
        *,
        events: list[Any] | None = None,
        message_history: list[Any] | None = None,
    ) -> None:
        self._events = events or []
        self._history = message_history or []

    async def execute(self):  # type: ignore[override]
        self._message_history = self._history
        self._final_message = ChatMessage(content="done", role="assistant")
        for event in self._events:
            yield event


def _stream_complete_event() -> StreamCompleteEvent[Any]:
    return StreamCompleteEvent(message=ChatMessage(content="done", role="assistant"))


def _make_run_handle(
    *,
    agent: Any | None = None,
    event_bus: Any | None = None,
    session: Any | None = None,
    run_id: str = "test-run",
    session_id: str = "test-session",
    agent_type: str = "native",
    message_history: list[Any] | None = None,
) -> RunHandle:
    """Create a RunHandle with mocked dependencies."""
    if agent is None:
        agent = MagicMock()
        agent.create_turn = MagicMock(return_value=_StubTurn())
    if event_bus is None:
        event_bus = AsyncMock()
    if session is None:
        session = MagicMock()
        session.turn_lock = asyncio.Lock()
    handle = RunHandle(
        run_id=run_id,
        session_id=session_id,
        agent_type=agent_type,
        agent=agent,
        event_bus=event_bus,
        session=session,
    )
    if message_history is not None:
        handle._message_history = message_history
    return handle


async def _consume_gen(gen: Any) -> None:
    """Consume an async generator to completion."""
    async for _ in gen:
        pass


# ---------------------------------------------------------------------------
# Test 1: Cancel preserves _message_history on RunHandle
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="L2 migration: requires mock RunHandle/agent internals — remains L1 unit test"
)
@pytest.mark.unit
async def test_cancel_preserves_message_history() -> None:
    """Given a cancelled turn that set _message_history, RunHandle._message_history is updated.

    Bug: RunHandle.start() line 293 `continue` skips line 300
    `self._message_history = turn.message_history`, so the cancelled
    turn's messages are lost. The next turn starts with stale history.

    Given: A RunHandle with a _BlockingTurnWithHistory that sets
           _message_history to ["partial_msg"].
    When: The turn is cancelled.
    Then: handle._message_history includes the partial turn's messages.
    """
    handle = _make_run_handle()
    # Turn that blocks until cancelled, but has already set history
    blocking_turn = _BlockingTurnWithHistory(
        run_ctx=handle.run_ctx,
        history=["partial_msg_1", "partial_msg_2"],
    )
    # Second turn (after cancel) that completes normally
    stub_turn = _StubTurn(
        events=[_stream_complete_event()],
        message_history=["next_turn_msg"],
    )
    handle.agent.create_turn = MagicMock(side_effect=[blocking_turn, stub_turn])  # type: ignore[method-assign]

    gen = handle.start("hello")
    consumer_task = asyncio.create_task(_consume_gen(gen))
    await asyncio.sleep(0.05)

    # Cancel the blocking turn
    handle.cancel()
    await asyncio.sleep(0.1)

    # The cancelled turn's _message_history should be preserved on the handle.
    # BUG: This fails because `continue` at line 293 skips the assignment.
    assert handle._message_history == ["partial_msg_1", "partial_msg_2"], (
        f"Expected ['partial_msg_1', 'partial_msg_2'], "
        f"got {handle._message_history!r} — cancelled turn's history was lost"
    )

    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task


# ---------------------------------------------------------------------------
# Test 2: New RunHandle bridges agent.conversation → list[ModelMessage]
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="L2 migration: requires mock pool/agent internals — remains L1 unit test")
@pytest.mark.unit
async def test_new_runhandle_bridges_conversation() -> None:
    """Given a new RunHandle, _message_history is populated from agent.conversation.

    Bug: _start_run_handle() creates RunHandle with default empty
    _message_history, never bridging agent.conversation (ChatMessage list)
    to list[ModelMessage]. The standalone path (_stream_events) does this
    conversion, but the RunHandle path does not.

    Given: An agent with conversation history containing ChatMessages
           with .messages (ModelMessage list).
    When: _start_run_handle creates a RunHandle.
    Then: RunHandle._message_history contains the ModelMessages from
          agent.conversation.
    """
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    # Create ChatMessages with .messages (ModelMessage list)
    prior_messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="previous question")]),
        ModelResponse(parts=[TextPart(content="previous answer")]),
    ]
    chat_msg1 = ChatMessage(content="previous question", role="user")
    chat_msg1.messages = [prior_messages[0]]
    chat_msg2 = ChatMessage(content="previous answer", role="assistant")
    chat_msg2.messages = [prior_messages[1]]

    # Mock agent with conversation history
    agent = MagicMock()
    agent.AGENT_TYPE = "native"
    agent.conversation = MagicMock()
    agent.conversation.get_history.return_value = [chat_msg1, chat_msg2]

    # Use real EventBus and SessionState
    event_bus = EventBus()
    session = SessionState(
        session_id="test-bridge-session",
        agent_name="test-bridge",
    )

    from agentpool.orchestrator.core import SessionController

    controller = SessionController.__new__(SessionController)
    controller._event_bus = event_bus
    controller._runs = {}
    controller._background_tasks = set()
    controller._sessions = {"test-bridge-session": session}

    # Mock pool — _start_run_handle calls pool.get_context(), pool._factory,
    # and pool.manifest.agents
    mock_pool = MagicMock()
    mock_pool.get_context.return_value = MagicMock()
    mock_pool._factory.resource_sources = {}
    mock_pool.manifest.agents = {"test-bridge": MagicMock()}
    controller.pool = mock_pool  # type: ignore[attr-defined]

    # Call _start_run_handle directly — returns message_id (str | None)
    message_id = controller._start_run_handle(
        session=session,
        agent=agent,
        session_id="test-bridge-session",
        content="new prompt",
    )

    assert message_id is not None, "Expected a message_id from _start_run_handle"

    # Get the RunHandle from controller._runs
    session_state = controller.get_session("test-bridge-session")
    assert session_state is not None
    assert session_state.current_run_id is not None
    run_handle = controller._runs[session_state.current_run_id]

    # RunHandle._message_history should contain ModelMessages from conversation
    # BUG: This fails because _start_run_handle doesn't bridge conversation.
    assert len(run_handle._message_history) == 2, (
        f"Expected 2 ModelMessages from conversation history, "
        f"got {len(run_handle._message_history)} — "
        f"agent.conversation was not bridged to _message_history"
    )
    assert run_handle._message_history == prior_messages, (
        "RunHandle._message_history should contain the ModelMessages "
        "from agent.conversation.get_history()"
    )


# ---------------------------------------------------------------------------
# Test 3: NativeTurn Path B (CancelledError) captures _message_history
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="L2 migration: requires mock agent_run internals — remains L1 unit test")
@pytest.mark.unit
async def test_cancellederror_path_captures_history() -> None:
    """Given a CancelledError during agent_run.next(), _message_history is captured.

    Bug: NativeTurn.execute() has two cancel paths:
    - Path A (run_ctx.cancelled check, line 158): breaks loop → line 199
      sets _message_history ✓
    - Path B (CancelledError from cancelled task, line 220): caught →
      _message_history NOT set ✗

    Given: A NativeTurn where agent_run.next() raises CancelledError
           while run_ctx.cancelled is True (simulating cancel() calling
           _interrupt() which cancels _iteration_task).
    When: The CancelledError is caught by Path B.
    Then: turn._message_history is set from agent_run.all_messages().
    """
    agent = Agent(
        name="test-path-b",
        model=TestModel(custom_output_text="Hello"),
    )
    async with agent:
        run_ctx = AgentRunContext(session_id="test-path-b-session")

        turn = NativeTurn(
            agent=agent,
            prompts=["Hello"],
            run_ctx=run_ctx,
            message_history=[],
        )

        # DO NOT set cancelled before execute(). The cancel happens
        # DURING agent_run.next(), simulating the real race where
        # cancel() sets run_ctx.cancelled = True and then cancels
        # _iteration_task, causing CancelledError.
        from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

        mock_agent_run = MagicMock()
        # Not End, so the while loop starts
        mock_agent_run.next_node = MagicMock()  # Not isinstance End

        async def _next_with_cancel(node: Any) -> Any:
            # Simulate what cancel() does: set cancelled=True, then
            # the task cancellation propagates as CancelledError.
            run_ctx.cancelled = True
            raise asyncio.CancelledError

        mock_agent_run.next = _next_with_cancel
        mock_agent_run.all_messages = MagicMock(
            return_value=[
                ModelRequest(parts=[UserPromptPart(content="Hello")]),
                ModelResponse(parts=[TextPart(content="partial")]),
            ],
        )
        mock_agent_run.new_messages = MagicMock(return_value=[])
        mock_agent_run.usage = MagicMock()
        mock_agent_run.result = None

        mock_iter_cm = AsyncMock()
        mock_iter_cm.__aenter__ = AsyncMock(return_value=mock_agent_run)
        mock_iter_cm.__aexit__ = AsyncMock(return_value=None)

        mock_agentlet = MagicMock()
        mock_agentlet.iter = MagicMock(return_value=mock_iter_cm)

        # Patch get_agentlet to return our mock
        with patch.object(agent, "get_agentlet", AsyncMock(return_value=mock_agentlet)):
            events: list[Any] = []
            events.extend([event async for event in turn.execute()])

        # Path B should have captured _message_history from agent_run
        # BUG: This fails because Path B doesn't call agent_run.all_messages()
        assert turn._message_history is not None, (
            "turn._message_history should be set after CancelledError — Path B doesn't capture it"
        )
        assert len(turn._message_history) == 2, (
            f"Expected 2 messages from agent_run.all_messages(), "
            f"got {len(turn._message_history) if turn._message_history else 0}"
        )


# ---------------------------------------------------------------------------
# Test 4: Multi-turn context preservation via _consume_run
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="L2 migration: requires mock pool/agent internals — remains L1 unit test")
@pytest.mark.unit
async def test_multi_turn_preserves_context_via_consume_run() -> None:
    """Given two consecutive RunHandles on the same session, the second gets history.

    Bug: _consume_run() closes the generator after StreamCompleteEvent,
    so _message_history is never updated on the first RunHandle. When
    _cleanup_run clears current_run_id, the next receive_request creates
    a new RunHandle with empty _message_history.

    Given: An agent with conversation history from a prior turn.
    When: A new RunHandle is created via _start_run_handle.
    Then: The new RunHandle._message_history contains ModelMessages
          from the prior turn's conversation.
    """
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    # Simulate: first turn completed, agent.conversation has history
    prior_messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="what is 2+2")]),
        ModelResponse(parts=[TextPart(content="4")]),
    ]
    chat_msg = ChatMessage(content="what is 2+2", role="user")
    chat_msg.messages = [prior_messages[0]]
    chat_msg2 = ChatMessage(content="4", role="assistant")
    chat_msg2.messages = [prior_messages[1]]

    agent = MagicMock()
    agent.AGENT_TYPE = "native"
    agent.conversation = MagicMock()
    agent.conversation.get_history.return_value = [chat_msg, chat_msg2]
    agent.create_turn = MagicMock(
        return_value=_StubTurn(
            events=[_stream_complete_event()],
            message_history=["new_msg"],
        )
    )

    event_bus = EventBus()
    session = SessionState(
        session_id="test-multi-session",
        agent_name="test-multi",
    )

    from agentpool.orchestrator.core import SessionController

    controller = SessionController.__new__(SessionController)
    controller._event_bus = event_bus
    controller._runs = {}
    controller._background_tasks = set()
    controller._sessions = {"test-multi-session": session}

    # Mock pool — _start_run_handle calls pool.get_context(), pool._factory,
    # and pool.manifest.agents
    mock_pool = MagicMock()
    mock_pool.get_context.return_value = MagicMock()
    mock_pool._factory.resource_sources = {}
    mock_pool.manifest.agents = {"test-multi": MagicMock()}
    controller.pool = mock_pool  # type: ignore[attr-defined]

    # First RunHandle (simulating prior turn that already completed)
    # Second RunHandle (new request on same session)
    message_id = controller._start_run_handle(
        session=session,
        agent=agent,
        session_id="test-multi-session",
        content="follow up question",
    )

    assert message_id is not None

    # Get the RunHandle from controller._runs
    session_state = controller.get_session("test-multi-session")
    assert session_state is not None
    assert session_state.current_run_id is not None
    second_handle = controller._runs[session_state.current_run_id]

    # The second RunHandle should have history from agent.conversation
    # BUG: This fails because _start_run_handle doesn't bridge conversation
    assert len(second_handle._message_history) == 2, (
        f"Expected 2 ModelMessages from conversation history, "
        f"got {len(second_handle._message_history)} — "
        f"prior turn's context was lost when new RunHandle was created"
    )

    second_handle.close()


# ---------------------------------------------------------------------------
# Test 5: Bridged history must not contain trailing unprocessed tool calls
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="L2 migration: requires mock pool/agent internals — remains L1 unit test")
@pytest.mark.unit
async def test_bridged_history_injects_cancelled_tool_results() -> None:
    """Given a cancelled turn with a pending tool call, bridged history injects tool results.

    When a turn is cancelled mid-tool-call, agent_run.all_messages() contains
    a ModelResponse with a tool call but no corresponding tool result. If this
    incomplete pair is passed to the next RunHandle, PydanticAI raises:
    "Cannot provide a new user prompt when the message history contains
    unprocessed tool calls."

    Fix: when bridging, inject a ModelRequest with RetryPromptPart for each
    unprocessed tool call, telling the model the tool was cancelled. This
    preserves the model's decision context (it knows it called the tool)
    while providing the required tool result to satisfy PydanticAI's
    message history validation.

    Given: An agent with conversation history whose last ChatMessage has a
           ModelResponse with a tool call but no tool result.
    When: _start_run_handle bridges conversation → _message_history.
    Then: A ModelRequest with RetryPromptPart is appended after the
          ModelResponse, one per unprocessed tool call.
    """
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        RetryPromptPart,
        ToolCallPart,
        UserPromptPart,
    )

    # Simulate: user asked something, model responded with a tool call,
    # but the tool result never came (turn was cancelled).
    tool_call = ToolCallPart(tool_name="bash", args={"cmd": "ls"})
    prior_messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="run a command")]),
        ModelResponse(parts=[tool_call]),
    ]
    chat_msg1 = ChatMessage(content="run a command", role="user")
    chat_msg1.messages = [prior_messages[0]]
    chat_msg2 = ChatMessage(content="", role="assistant")
    chat_msg2.messages = [prior_messages[1]]

    agent = MagicMock()
    agent.AGENT_TYPE = "native"
    agent.conversation = MagicMock()
    agent.conversation.get_history.return_value = [chat_msg1, chat_msg2]

    event_bus = EventBus()
    session = SessionState(
        session_id="test-cancel-tool-session",
        agent_name="test-cancel-tool",
    )

    from agentpool.orchestrator.core import SessionController

    controller = SessionController.__new__(SessionController)
    controller._event_bus = event_bus
    controller._runs = {}
    controller._background_tasks = set()
    controller._sessions = {"test-cancel-tool-session": session}

    # Mock pool — _start_run_handle calls pool.get_context(), pool._factory,
    # and pool.manifest.agents
    mock_pool = MagicMock()
    mock_pool.get_context.return_value = MagicMock()
    mock_pool._factory.resource_sources = {}
    mock_pool.manifest.agents = {"test-cancel-tool": MagicMock()}
    controller.pool = mock_pool  # type: ignore[attr-defined]

    message_id = controller._start_run_handle(
        session=session,
        agent=agent,
        session_id="test-cancel-tool-session",
        content="follow up",
    )

    assert message_id is not None

    # Get the RunHandle from controller._runs
    session_state = controller.get_session("test-cancel-tool-session")
    assert session_state is not None
    assert session_state.current_run_id is not None
    run_handle = controller._runs[session_state.current_run_id]

    # The bridged history must have:
    # 1. ModelRequest (user prompt)
    # 2. ModelResponse (tool call)
    # 3. ModelRequest (with RetryPromptPart for the cancelled tool call)
    assert len(run_handle._message_history) == 3, (
        f"Expected 3 messages (user + tool_call + cancelled_result), "
        f"got {len(run_handle._message_history)}: "
        f"{[type(m).__name__ for m in run_handle._message_history]}"
    )

    last_msg = run_handle._message_history[-1]
    assert isinstance(last_msg, ModelRequest), (
        f"Expected last message to be ModelRequest with cancelled tool result, "
        f"got {type(last_msg).__name__}"
    )
    retry_parts = [p for p in last_msg.parts if isinstance(p, RetryPromptPart)]
    assert len(retry_parts) == 1, (
        f"Expected 1 RetryPromptPart for the cancelled tool call, got {len(retry_parts)}"
    )
    assert retry_parts[0].tool_name == "bash", (
        f"Expected RetryPromptPart tool_name='bash', got {retry_parts[0].tool_name!r}"
    )
    assert "cancel" in str(retry_parts[0].content).lower(), (
        f"RetryPromptPart content should mention cancellation, got {retry_parts[0].content!r}"
    )

    run_handle.close()


# ---------------------------------------------------------------------------
# Merged from test_cancelled_cleanup_review.py (suffix: cr)
# ---------------------------------------------------------------------------


class _FakeGen:
    """Fake async generator that raises CancelledError on aclose()."""

    def __init__(self, events: list[Any] | None = None) -> None:
        self._events = events or []

    def __aiter__(self) -> _FakeGen:
        return self

    async def __anext__(self) -> Any:
        if self._events:
            return self._events.pop(0)
        raise StopAsyncIteration

    async def aclose(self) -> None:
        raise asyncio.CancelledError("simulated cancellation")


async def _drain_async_gen(gen: Any) -> None:
    """Drain an async generator to completion."""
    async for _ in gen:
        pass


@pytest.mark.skip(
    reason="L2 migration: requires mock mixin/session internals — remains L1 unit test"
)
@pytest.mark.unit
@pytest.mark.anyio
async def test_cancelled_error_cleanup_in_process_prompt() -> None:
    """CancelledError in _process_prompt_run_turn must not skip cleanup.

    When gen.aclose() raises CancelledError, session.current_run_id must
    still be set to None and _runs.pop must still be called, then
    CancelledError re-raised.
    """
    event_bus = EventBus()
    mixin: Any = SessionPoolMessagingMixin.__new__(SessionPoolMessagingMixin)
    session = MagicMock()
    session.is_closing = False
    session.current_run_id = None
    session._request_lock = asyncio.Lock()
    controller = MagicMock()
    controller.get_session = MagicMock(return_value=session)
    controller.get_or_create_session = AsyncMock(return_value=(session, True))
    controller.get_or_create_session_agent = AsyncMock(return_value=MagicMock())
    controller._runs = {}
    mixin.sessions = controller
    mixin.pool = MagicMock()
    mock_run_handle = MagicMock(spec=RunHandle)
    mock_run_handle.run_id = "run-123"
    mock_run_handle.start = MagicMock(return_value=_FakeGen())

    def fake_create(*args: Any, **kwargs: Any) -> Any:
        session.current_run_id = "run-123"
        controller._runs["run-123"] = mock_run_handle
        return mock_run_handle

    with (
        patch.object(
            type(mixin), "event_bus", new_callable=lambda: property(lambda self: event_bus)
        ),
        patch.object(
            SessionPoolMessagingMixin, "_create_run_handle", side_effect=fake_create, create=True
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await mixin._process_prompt_run_turn("sess-1", "hello")
    assert session.current_run_id is None, "current_run_id was not cleared"
    assert "run-123" not in controller._runs, "_runs.pop was not called"


@pytest.mark.skip(
    reason="L2 migration: requires mock mixin/session internals — remains L1 unit test"
)
@pytest.mark.unit
@pytest.mark.anyio
async def test_cancelled_error_cleanup_in_run_stream() -> None:
    """CancelledError in _run_stream_run_turn must not skip cleanup.

    When gen.aclose() raises CancelledError, session.current_run_id must
    still be set to None, _runs.pop must still be called, EventBus
    unsubscribe must still be attempted, and CancelledError re-raised.
    """
    event_bus = EventBus()
    mixin: Any = SessionPoolRunsMixin.__new__(SessionPoolRunsMixin)
    session = MagicMock()
    session.is_closing = False
    session.current_run_id = None
    session._request_lock = asyncio.Lock()
    controller = MagicMock()
    controller.get_session = MagicMock(return_value=session)
    controller.get_or_create_session = AsyncMock(return_value=(session, True))
    controller.get_or_create_session_agent = AsyncMock(return_value=MagicMock())
    controller._runs = {}
    mixin.sessions = controller
    mock_pool = MagicMock()
    mock_pool.get_context = MagicMock(return_value=MagicMock())
    mock_pool.manifest = MagicMock()
    mock_pool.manifest.agents = {}
    mixin.pool = mock_pool
    mock_run_handle = MagicMock(spec=RunHandle)
    mock_run_handle.run_id = "run-stream-1"
    mock_run_handle.start = MagicMock(return_value=_FakeGen())

    def fake_create(*args: Any, **kwargs: Any) -> Any:
        session.current_run_id = "run-stream-1"
        controller._runs["run-stream-1"] = mock_run_handle
        return mock_run_handle

    with (
        patch.object(
            type(mixin), "event_bus", new_callable=lambda: property(lambda self: event_bus)
        ),
        patch.object(
            SessionPoolRunsMixin, "_create_run_handle", side_effect=fake_create, create=True
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await _drain_async_gen(mixin._run_stream_run_turn("sess-1", "hello"))
    assert session.current_run_id is None, "current_run_id was not cleared"
    assert "run-stream-1" not in controller._runs, "_runs.pop was not called"


# ---------------------------------------------------------------------------
# Merged from test_cancel_e2e.py (suffix: e2e)
# ---------------------------------------------------------------------------


def _unwrap_event(event: Any) -> Any:
    """Unwrap EventEnvelope if present, otherwise return the event as-is."""
    return event.event if isinstance(event, EventEnvelope) else event


async def _receive_and_get_handle(
    session_pool: SessionPool, session_id: str, content: str, **kwargs: Any
) -> Any:
    """Call receive_request and return the RunHandle for the active run.

    receive_request() now returns str | None (message_id), but many tests
    need the RunHandle to inspect state. This helper bridges the gap.
    """
    message_id = await session_pool.send_message(session_id, content, **kwargs)
    assert message_id is not None, "receive_request should return a message_id for idle session"
    handle = session_pool._get_active_run_handle(session_id)
    assert handle is not None, "Expected an active RunHandle after receive_request"
    return handle


class _BlockingTurn(Turn):
    """Turn that blocks until run_ctx.cancelled, then returns without StreamCompleteEvent."""

    def __init__(self, run_ctx: AgentRunContext) -> None:
        self._run_ctx = run_ctx

    async def execute(self):
        self._message_history = []
        self._final_message = ChatMessage(content="blocked", role="assistant")
        while not self._run_ctx.cancelled:
            await asyncio.sleep(0.01)
        return
        yield


class _StubTurn_e2e(Turn):  # noqa: N801
    """Minimal Turn that yields events from a list and sets message history."""

    def __init__(
        self, *, events: list[Any] | None = None, message_history: list[Any] | None = None
    ) -> None:
        self._events = events or []
        self._history = message_history or []

    async def execute(self):
        self._message_history = self._history
        self._final_message = ChatMessage(content="done", role="assistant")
        for event in self._events:
            yield event


class _ToolBlockingTurn(Turn):
    """Turn that yields ToolCallStartEvent then blocks until run_ctx.cancelled."""

    def __init__(self, run_ctx: AgentRunContext) -> None:
        self._run_ctx = run_ctx

    async def execute(self):
        self._message_history = []
        self._final_message = ChatMessage(content="tool-blocked", role="assistant")
        yield ToolCallStartEvent(
            tool_call_id="test-tool-1", tool_name="bash", title="Running bash command"
        )
        while not self._run_ctx.cancelled:
            await asyncio.sleep(0.01)


async def _patch_agent_create_turn(
    session_pool: SessionPool,
    session_id: str,
    create_turn_fn: Any,
) -> Any:
    """Get the real agent from the pool and patch its create_turn method.

    Returns the real agent for any further manipulation.
    """
    agent = await session_pool.sessions.get_or_create_session_agent(session_id)
    agent.create_turn = create_turn_fn  # type: ignore[method-assign]
    return agent


def _make_cancel_aware_create_turn() -> Any:
    """Return a create_turn function whose first call returns _BlockingTurn.

    Subsequent calls return _StubTurn instances that yield StreamCompleteEvent.
    """
    call_count = 0

    def _create_turn(
        prompts: Any, run_ctx: AgentRunContext, message_history: Any, **kwargs: Any
    ) -> Turn:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _BlockingTurn(run_ctx)
        return _StubTurn_e2e(
            events=[
                RunStartedEvent(run_id="test-run"),
                StreamCompleteEvent(message=ChatMessage(content="response", role="assistant")),
            ],
            message_history=["msg"],
        )

    return _create_turn


async def _drain_queue(queue: asyncio.Queue) -> list[Any]:
    """Drain all currently-available events from a queue without blocking."""
    events: list[Any] = []
    while True:
        with contextlib.suppress(asyncio.QueueEmpty):
            events.append(queue.get_nowait())
            continue
        break
    return events


@pytest.mark.integration
@pytest.mark.anyio
async def test_cancel_then_new_prompt_full_flow(minimal_pool: AgentPool) -> None:
    """End-to-end: cancel a running turn, then send a new prompt.

    Steps:
        1. Start a run with a blocking turn (patched on real agent).
        2. Cancel via cancel_run_for_session().
        3. Send new prompt via receive_request().
        4. Verify new prompt processed (events published, no hang).
        5. Verify RunHandle is same instance (1:1 model) or new one (if old died).

    Uses asyncio.wait_for() with a 30s timeout to catch hangs.
    """
    session_pool = minimal_pool.session_pool
    assert session_pool is not None
    session_id = "sess-cancel-e2e"
    await session_pool.create_session(session_id, agent_name="test_agent")
    await _patch_agent_create_turn(session_pool, session_id, _make_cancel_aware_create_turn())
    queue = await session_pool.event_bus.subscribe(session_id)
    first_handle = await _receive_and_get_handle(session_pool, session_id, "first prompt")
    await asyncio.sleep(0.1)
    session_pool.sessions.cancel_run_for_session(session_id)
    await asyncio.sleep(0.2)
    pre_events = await _drain_queue(queue)
    pre_event_types = [type(_unwrap_event(e)) for e in pre_events]
    assert RunFailedEvent in pre_event_types, (
        f"Expected RunFailedEvent from cancelled turn, got: {pre_event_types}"
    )
    second_msg_id = await asyncio.wait_for(
        session_pool.send_message(session_id, "second prompt"), timeout=30.0
    )
    post_events: list[Any] = []
    try:
        async with asyncio.timeout(30.0):
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=5.0)
                    post_events.append(event)
                    unwrapped = _unwrap_event(event)
                    if isinstance(unwrapped, StreamCompleteEvent):
                        break
                except TimeoutError:
                    break
    except TimeoutError:
        pytest.fail("Timed out waiting for events after cancel-then-prompt")
    post_event_types = [type(_unwrap_event(e)) for e in post_events]
    assert RunStartedEvent in post_event_types, (
        f"Expected RunStartedEvent for new prompt, got: {post_event_types}"
    )
    assert StreamCompleteEvent in post_event_types, (
        f"Expected StreamCompleteEvent for new prompt, got: {post_event_types}"
    )
    if second_msg_id is not None:
        second_handle = session_pool._get_active_run_handle(session_id)
        if second_handle is not None and second_handle is not first_handle:
            pass
    assert first_handle._run_state in (RunState.IDLE, RunState.DONE), (
        f"First RunHandle should be idle or done, got: {first_handle._run_state}"
    )
    first_handle.close()
    await asyncio.sleep(0.1)


def _make_tool_blocking_create_turn() -> Any:
    """Return a create_turn function whose first call returns _ToolBlockingTurn.

    Subsequent calls return _StubTurn instances that yield StreamCompleteEvent.
    """
    call_count = 0

    def _create_turn(
        prompts: Any, run_ctx: AgentRunContext, message_history: Any, **kwargs: Any
    ) -> Turn:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _ToolBlockingTurn(run_ctx)
        return _StubTurn_e2e(
            events=[
                RunStartedEvent(run_id="test-run"),
                StreamCompleteEvent(message=ChatMessage(content="response", role="assistant")),
            ],
            message_history=["msg"],
        )

    return _create_turn


def _make_stub_then_die_create_turn() -> Any:
    """Return a create_turn function: first returns _StubTurn, second raises, rest _StubTurn."""
    call_count = 0

    def _create_turn(
        prompts: Any, run_ctx: AgentRunContext, message_history: Any, **kwargs: Any
    ) -> Turn:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            msg = "Simulated unrecoverable error in create_turn"
            raise RuntimeError(msg)
        return _StubTurn_e2e(
            events=[
                RunStartedEvent(run_id="test-run"),
                StreamCompleteEvent(message=ChatMessage(content="response", role="assistant")),
            ],
            message_history=["msg"],
        )

    return _create_turn


async def _collect_events_until(
    queue: asyncio.Queue, target_type: type, *, timeout: float = 30.0
) -> list[Any]:
    """Collect events from a queue until a target event type is seen."""
    events: list[Any] = []
    try:
        async with asyncio.timeout(timeout):
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=5.0)
                    events.append(event)
                    if isinstance(_unwrap_event(event), target_type):
                        break
                except TimeoutError:
                    break
    except TimeoutError:
        pytest.fail(f"Timed out waiting for {target_type.__name__}")
    return events


@pytest.mark.integration
@pytest.mark.anyio
async def test_double_cancel(minimal_pool: AgentPool) -> None:
    """Call cancel() twice during active turn — idempotent, no errors.

    Given: a running turn with _BlockingTurn.
    When: cancel() is called twice.
    Then: no exceptions, RunHandle returns to idle/done, new prompt works.
    """
    session_pool = minimal_pool.session_pool
    assert session_pool is not None
    session_id = "sess-double-cancel"
    await session_pool.create_session(session_id, agent_name="test_agent")
    await _patch_agent_create_turn(session_pool, session_id, _make_cancel_aware_create_turn())
    queue = await session_pool.event_bus.subscribe(session_id)
    first_handle = await _receive_and_get_handle(session_pool, session_id, "first prompt")
    assert first_handle is not None
    await asyncio.sleep(0.1)
    session_pool.sessions.cancel_run_for_session(session_id)
    session_pool.sessions.cancel_run_for_session(session_id)
    await asyncio.sleep(0.2)
    pre_events = await _drain_queue(queue)
    pre_types = [type(_unwrap_event(e)) for e in pre_events]
    assert RunFailedEvent in pre_types, f"Expected RunFailedEvent, got: {pre_types}"
    assert first_handle._run_state in (RunState.IDLE, RunState.DONE), (
        f"RunHandle should be idle/done after double cancel, got: {first_handle._run_state}"
    )
    await asyncio.wait_for(session_pool.send_message(session_id, "second prompt"), timeout=30.0)
    post_events = await _collect_events_until(queue, StreamCompleteEvent)
    post_types = [type(_unwrap_event(e)) for e in post_events]
    assert StreamCompleteEvent in post_types, f"Expected StreamCompleteEvent, got: {post_types}"
    first_handle.close()
    await asyncio.sleep(0.1)


@pytest.mark.integration
@pytest.mark.anyio
async def test_cancel_during_idle_then_new_prompt(minimal_pool: AgentPool) -> None:
    """Cancel while idle (no active turn), then send new prompt.

    Given: a completed turn, RunHandle is idle.
    When: cancel() is called while idle, then a new prompt is sent.
    Then: cancelled flag is reset before new turn starts, prompt is processed.
    """
    session_pool = minimal_pool.session_pool
    assert session_pool is not None
    session_id = "sess-cancel-idle"
    await session_pool.create_session(session_id, agent_name="test_agent")
    agent = await _patch_agent_create_turn(
        session_pool, session_id, _make_cancel_aware_create_turn()
    )
    queue = await session_pool.event_bus.subscribe(session_id)
    # Re-patch with a stub-only create_turn for immediate completion
    agent.create_turn = lambda prompts, run_ctx, message_history, **kwargs: _StubTurn_e2e(
        events=[
            RunStartedEvent(run_id="test-run"),
            StreamCompleteEvent(message=ChatMessage(content="response", role="assistant")),
        ],
        message_history=["msg"],
    )  # type: ignore[method-assign]
    first_handle = await _receive_and_get_handle(session_pool, session_id, "first prompt")
    assert first_handle is not None
    await _collect_events_until(queue, StreamCompleteEvent)
    await asyncio.sleep(0.1)
    session_pool.sessions.cancel_run_for_session(session_id)
    await asyncio.sleep(0.05)
    assert first_handle.run_ctx.cancelled is False, (
        "cancelled flag should remain False when cancel is called while idle"
        " (no active run to cancel)"
    )
    await asyncio.wait_for(session_pool.send_message(session_id, "second prompt"), timeout=30.0)
    post_events = await _collect_events_until(queue, StreamCompleteEvent)
    post_types = [type(_unwrap_event(e)) for e in post_events]
    assert StreamCompleteEvent in post_types, (
        f"Expected StreamCompleteEvent from new turn, got: {post_types}"
    )
    assert first_handle.run_ctx.cancelled is False, (
        "cancelled flag should remain False — new turn ran without cancel"
    )
    first_handle.close()
    await asyncio.sleep(0.1)


@pytest.mark.integration
@pytest.mark.anyio
async def test_cancel_then_steer_continues_turn(minimal_pool: AgentPool) -> None:
    """Cancel then immediately steer() — cancel interrupts turn, steer queues for next.

    Given: a running turn with _BlockingTurn.
    When: cancel() is called, then steer() is called immediately.
    Then: cancel interrupts the current turn, steer message is queued,
          and a subsequent turn processes it.
    """
    session_pool = minimal_pool.session_pool
    assert session_pool is not None
    session_id = "sess-cancel-steer"
    await session_pool.create_session(session_id, agent_name="test_agent")
    await _patch_agent_create_turn(session_pool, session_id, _make_cancel_aware_create_turn())
    queue = await session_pool.event_bus.subscribe(session_id)
    first_handle = await _receive_and_get_handle(session_pool, session_id, "first prompt")
    assert first_handle is not None
    await asyncio.sleep(0.1)
    session_pool.sessions.cancel_run_for_session(session_id)
    steer_result = first_handle.steer("steer message")
    assert steer_result is not None, "steer() should return message_id (message delivered/queued)"
    post_events = await _collect_events_until(queue, StreamCompleteEvent, timeout=30.0)
    post_types = [type(_unwrap_event(e)) for e in post_events]
    assert RunFailedEvent in post_types, (
        f"Expected RunFailedEvent from cancelled turn, got: {post_types}"
    )
    assert StreamCompleteEvent in post_types, (
        f"Expected StreamCompleteEvent from subsequent turn, got: {post_types}"
    )
    first_handle.close()
    await asyncio.sleep(0.1)


@pytest.mark.integration
@pytest.mark.anyio
async def test_cancel_during_tool_execution(minimal_pool: AgentPool) -> None:
    """Cancel during tool execution — run_ctx.cancelled is set, turn exits after tool.

    Given: a turn that yields ToolCallStartEvent then blocks.
    When: cancel() is called during the blocking period.
    Then: run_ctx.cancelled is set, turn exits, RunFailedEvent is published.
    """
    session_pool = minimal_pool.session_pool
    assert session_pool is not None
    session_id = "sess-cancel-tool"
    await session_pool.create_session(session_id, agent_name="test_agent")
    await _patch_agent_create_turn(session_pool, session_id, _make_tool_blocking_create_turn())
    queue = await session_pool.event_bus.subscribe(session_id)
    first_handle = await _receive_and_get_handle(session_pool, session_id, "first prompt")
    assert first_handle is not None
    await asyncio.sleep(0.1)
    session_pool.sessions.cancel_run_for_session(session_id)
    await asyncio.sleep(0.2)
    events = await _drain_queue(queue)
    event_types = [type(_unwrap_event(e)) for e in events]
    assert ToolCallStartEvent in event_types, (
        f"Expected ToolCallStartEvent before cancel, got: {event_types}"
    )
    assert RunFailedEvent in event_types, (
        f"Expected RunFailedEvent after cancel, got: {event_types}"
    )
    assert first_handle._run_state in (RunState.IDLE, RunState.DONE), (
        f"RunHandle should be idle/done after cancel, got: {first_handle._run_state}"
    )
    first_handle.close()
    await asyncio.sleep(0.1)


@pytest.mark.integration
@pytest.mark.anyio
async def test_cancel_then_followup_next_turn(minimal_pool: AgentPool) -> None:
    """Cancel then followup() — next turn processes the followup message.

    Given: a running turn with _BlockingTurn.
    When: cancel() is called, then after propagation, followup() is called.
    Then: the followup message is processed in a subsequent turn.
    """
    session_pool = minimal_pool.session_pool
    assert session_pool is not None
    session_id = "sess-cancel-followup"
    await session_pool.create_session(session_id, agent_name="test_agent")
    await _patch_agent_create_turn(session_pool, session_id, _make_cancel_aware_create_turn())
    queue = await session_pool.event_bus.subscribe(session_id)
    first_handle = await _receive_and_get_handle(session_pool, session_id, "first prompt")
    assert first_handle is not None
    await asyncio.sleep(0.1)
    session_pool.sessions.cancel_run_for_session(session_id)
    await asyncio.sleep(0.3)
    pre_events = await _drain_queue(queue)
    pre_types = [type(_unwrap_event(e)) for e in pre_events]
    assert RunFailedEvent in pre_types, (
        f"Expected RunFailedEvent from cancelled turn, got: {pre_types}"
    )
    followup_result = first_handle.followup("followup message")
    assert followup_result is not None, "followup() should return message_id (message queued)"
    post_events = await _collect_events_until(queue, StreamCompleteEvent, timeout=30.0)
    post_types = [type(_unwrap_event(e)) for e in post_events]
    assert RunStartedEvent in post_types, (
        f"Expected RunStartedEvent for followup turn, got: {post_types}"
    )
    assert StreamCompleteEvent in post_types, (
        f"Expected StreamCompleteEvent for followup turn, got: {post_types}"
    )
    first_handle.close()
    await asyncio.sleep(0.1)


@pytest.mark.integration
@pytest.mark.anyio
async def test_double_cancel_then_new_prompt(minimal_pool: AgentPool) -> None:
    """Double cancel then new prompt — no hang, new prompt processed.

    Given: a running turn with _BlockingTurn.
    When: cancel() is called twice, then a new prompt is sent via receive_request().
    Then: no hang, new prompt is processed (StreamCompleteEvent published).
    """
    session_pool = minimal_pool.session_pool
    assert session_pool is not None
    session_id = "sess-double-cancel-prompt"
    await session_pool.create_session(session_id, agent_name="test_agent")
    await _patch_agent_create_turn(session_pool, session_id, _make_cancel_aware_create_turn())
    queue = await session_pool.event_bus.subscribe(session_id)
    first_handle = await _receive_and_get_handle(session_pool, session_id, "first prompt")
    assert first_handle is not None
    await asyncio.sleep(0.1)
    session_pool.sessions.cancel_run_for_session(session_id)
    session_pool.sessions.cancel_run_for_session(session_id)
    await asyncio.sleep(0.2)
    pre_events = await _drain_queue(queue)
    pre_types = [type(_unwrap_event(e)) for e in pre_events]
    assert RunFailedEvent in pre_types, (
        f"Expected RunFailedEvent from cancelled turn, got: {pre_types}"
    )
    await asyncio.wait_for(session_pool.send_message(session_id, "second prompt"), timeout=30.0)
    post_events = await _collect_events_until(queue, StreamCompleteEvent, timeout=30.0)
    post_types = [type(_unwrap_event(e)) for e in post_events]
    assert StreamCompleteEvent in post_types, (
        f"Expected StreamCompleteEvent from new prompt, got: {post_types}"
    )
    first_handle.close()
    await asyncio.sleep(0.1)


@pytest.mark.integration
@pytest.mark.anyio
async def test_runhandle_dies_in_idle_loop(minimal_pool: AgentPool) -> None:
    """Simulate unrecoverable error in start().

    Finally block sets events, cleanup clears current_run_id.

    Given: an agent whose second create_turn call raises RuntimeError.
    When: the first turn completes, followup triggers the second create_turn which raises.
    Then: finally block sets complete_event, _cleanup_run clears current_run_id,
          next receive_request creates a new RunHandle and processes the prompt.
    """
    session_pool = minimal_pool.session_pool
    assert session_pool is not None
    session_id = "sess-dies-in-idle"
    await session_pool.create_session(session_id, agent_name="test_agent")
    await _patch_agent_create_turn(session_pool, session_id, _make_stub_then_die_create_turn())
    queue = await session_pool.event_bus.subscribe(session_id)
    first_handle = await _receive_and_get_handle(session_pool, session_id, "first prompt")
    assert first_handle is not None
    await _collect_events_until(queue, StreamCompleteEvent)
    await asyncio.sleep(0.1)
    await asyncio.wait_for(session_pool.send_message(session_id, "trigger error"), timeout=30.0)
    await asyncio.sleep(0.5)
    crash_session = session_pool.sessions.get_session(session_id)
    assert crash_session is not None
    assert crash_session.current_run_id is None, (
        "current_run_id should be cleared by _cleanup_run after error"
    )
    second_msg_id = await asyncio.wait_for(
        session_pool.send_message(session_id, "new prompt after crash"), timeout=30.0
    )
    assert second_msg_id is not None, "receive_request should return a new message_id after cleanup"
    second_handle = session_pool._get_active_run_handle(session_id)
    assert second_handle is not None
    assert second_handle is not first_handle, "New RunHandle should be a different instance"
    post_events = await _collect_events_until(queue, StreamCompleteEvent, timeout=30.0)
    post_types = [type(_unwrap_event(e)) for e in post_events]
    assert StreamCompleteEvent in post_types, (
        f"Expected StreamCompleteEvent from new RunHandle, got: {post_types}"
    )
    second_handle.close()
    await asyncio.sleep(0.1)
