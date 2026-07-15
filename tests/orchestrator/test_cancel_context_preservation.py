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
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import (
    StreamCompleteEvent,
)
from agentpool.agents.native_agent.turn import NativeTurn
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import EventBus, SessionState
from agentpool.orchestrator.run import RunHandle
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
