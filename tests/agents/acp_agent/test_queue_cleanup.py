"""Tests verifying TurnRunner queues are NOT populated when using the ACP RunHandle path.

When ``AGENTPOOL_USE_RUN_TURN_FOR_ACP=true``, the ACP execution path uses
``RunHandle`` for message routing (``_message_queue``, ``queued_steer_messages``)
and ``PromptInjectionManager.inject()`` / ``consume()`` for context injection.
The deprecated ``TurnRunner._post_turn_injections`` and
``_post_turn_prompts`` queues must remain empty throughout the ACP turn
lifecycle.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock
import warnings

import pytest

from acp import InitializeRequest
from acp.schema import AgentMessageChunk, TextContentBlock
from agentpool.agents.acp_agent import ACPAgent
from agentpool.agents.acp_agent.turn import ACPTurn
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import StreamCompleteEvent
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import SessionState, TurnRunner
from agentpool.orchestrator.run import RunHandle, RunStatus
from agentpool.orchestrator.turn import Turn


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# ---------------------------------------------------------------------------
# Shared helpers (adapted from test_turn_integration.py)
# ---------------------------------------------------------------------------


class MockACPClient:
    """Mock ACP client implementing ACPClientProtocol for testing."""

    def __init__(
        self,
        *,
        updates: list[Any] | None = None,
        messages: list[Any] | None = None,
    ) -> None:
        self._updates = updates or []
        self._messages = messages or []
        self.prompt_calls: list[tuple[str, list[Any]]] = []

    async def prompt(self, session_id: str, content: list[Any]) -> Any:
        self.prompt_calls.append((session_id, content))
        return MagicMock(name="PromptResponse")

    async def stream_events(self, response: Any) -> AsyncIterator[Any]:
        for update in self._updates:
            yield update

    async def get_messages(self, session_id: str) -> list[Any]:
        return list(self._messages)


def _text_update(text: str) -> AgentMessageChunk:
    """Create an AgentMessageChunk with a text content block."""
    return AgentMessageChunk(content=TextContentBlock(text=text))


def _make_acp_agent() -> ACPAgent:
    """Create a mocked ACPAgent without subprocess/ACP initialization."""
    init_request = MagicMock(spec=InitializeRequest)
    agent = ACPAgent(command="test-cmd", init_request=init_request)
    # Mock the _api so create_turn() can pass it as acp_client
    agent._api = MagicMock(name="ACPAgentAPI")
    agent._sdk_session_id = "acp-test-session"
    return agent


def _make_turn_runner() -> TurnRunner:
    """Create a TurnRunner with suppressed deprecation warning."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return TurnRunner(session_controller=MagicMock())


def _make_session_state(session_id: str = "test-session") -> SessionState:
    """Create a SessionState for testing."""
    return SessionState(session_id=session_id, agent_name="test-agent")


def _make_run_handle(
    agent: ACPAgent,
    event_bus: Any,
    session: SessionState,
    run_ctx: AgentRunContext | None = None,
) -> RunHandle:
    """Create a RunHandle wired for the ACP RunHandle path."""
    ctx = run_ctx or AgentRunContext(session_id=session.session_id)
    return RunHandle(
        run_id="test-run-id",
        session_id=session.session_id,
        agent_type="acp",
        agent=agent,
        event_bus=event_bus,
        session=session,
        run_ctx=ctx,
    )


class _BlockingTurn(Turn):
    """Stub Turn that blocks on a release event before completing.

    Used to keep _status == RunStatus.running long enough to call
    steer()/followup() during a running turn.
    """

    def __init__(self, *, release_event: asyncio.Event) -> None:
        super().__init__()
        self._release_event = release_event

    async def execute(self):  # type: ignore[override]
        await self._release_event.wait()
        self._message_history: list[Any] = ["msg1"]
        self._final_message = ChatMessage(content="done", role="assistant")
        yield StreamCompleteEvent(message=self._final_message)


# ---------------------------------------------------------------------------
# Test 1: TurnRunner queues empty after ACP turn completes
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_turnrunner_queues_empty_after_acp_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given an ACP RunHandle path, TurnRunner queues stay empty after turn.

    Verifies that after running a full ACP turn via RunHandle.start(),
    the deprecated TurnRunner._post_turn_injections and
    _post_turn_prompts dicts remain empty (no session_id keys present).
    """
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN_FOR_ACP", "true")

    updates = [_text_update("Hello from ACP")]
    messages = [_text_update("Hello from ACP")]
    client = MockACPClient(updates=updates, messages=messages)

    agent = _make_acp_agent()
    agent.create_turn = MagicMock(
        return_value=ACPTurn(
            acp_client=client,
            prompts=["Say hello"],
            run_ctx=AgentRunContext(session_id="test-session"),
            message_history=[],
            session_id="test-session",
        ),
    )

    event_bus = AsyncMock()
    session = _make_session_state()
    turn_runner = _make_turn_runner()
    handle = _make_run_handle(agent, event_bus, session)

    # Run the full turn to completion, breaking after StreamCompleteEvent
    # to avoid hanging on the idle/wake loop.
    events: list[Any] = []
    async for event in handle.start("Say hello"):
        events.append(event)
        if isinstance(event, StreamCompleteEvent):
            handle.close()

    # Verify turn completed
    assert any(isinstance(e, StreamCompleteEvent) for e in events)

    # KEY ASSERTION: TurnRunner queues must be empty
    assert "test-session" not in turn_runner._post_turn_injections
    assert "test-session" not in turn_runner._post_turn_prompts
    assert len(turn_runner._post_turn_injections) == 0
    assert len(turn_runner._post_turn_prompts) == 0


# ---------------------------------------------------------------------------
# Test 2: Steer goes to RunHandle queues, NOT TurnRunner queues
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_steer_does_not_populate_turnrunner_queues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given an ACP RunHandle, steer() routes to RunHandle queues only.

    While the turn is running, steer() queues the message to
    ``run_ctx.queued_steer_messages`` (ACP path). The deprecated
    TurnRunner._post_turn_injections must NOT receive the message.
    """
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN_FOR_ACP", "true")

    release_event = asyncio.Event()
    agent = _make_acp_agent()
    agent.create_turn = MagicMock(
        return_value=_BlockingTurn(release_event=release_event),
    )

    event_bus = AsyncMock()
    session = _make_session_state()
    turn_runner = _make_turn_runner()
    handle = _make_run_handle(agent, event_bus, session)

    events: list[Any] = []
    gen = handle.start("hello")

    async def _consume() -> None:
        async for event in gen:
            events.append(event)  # noqa: PERF401

    consumer_task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    # Turn should be running (blocked on release_event inside _BlockingTurn)
    assert handle._status == RunStatus.running

    # Steer while running — ACP path queues to queued_steer_messages
    result = handle.steer("steered message")
    assert result is True

    # Verify message went to RunHandle's queue, NOT TurnRunner's
    assert "steered message" in handle.run_ctx.queued_steer_messages
    assert "test-session" not in turn_runner._post_turn_injections
    assert len(turn_runner._post_turn_injections) == 0

    # Also test steer while idle (after turn completes)
    release_event.set()
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task

    # Steer after close returns False
    result_after = handle.steer("post-close message")
    assert result_after is False

    # TurnRunner queues still empty
    assert "test-session" not in turn_runner._post_turn_injections
    assert len(turn_runner._post_turn_injections) == 0


# ---------------------------------------------------------------------------
# Test 3: Followup goes to RunHandle._message_queue, NOT TurnRunner queues
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_followup_does_not_populate_turnrunner_queues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given an ACP RunHandle, followup() routes to _message_queue only.

    The deprecated TurnRunner._post_turn_prompts must NOT receive the
    followup message.
    """
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN_FOR_ACP", "true")

    release_event = asyncio.Event()
    agent = _make_acp_agent()
    agent.create_turn = MagicMock(
        return_value=_BlockingTurn(release_event=release_event),
    )

    event_bus = AsyncMock()
    session = _make_session_state()
    turn_runner = _make_turn_runner()
    handle = _make_run_handle(agent, event_bus, session)

    events: list[Any] = []
    gen = handle.start("hello")

    async def _consume() -> None:
        async for event in gen:
            events.append(event)  # noqa: PERF401

    consumer_task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    # Turn should be running
    assert handle._status == RunStatus.running

    # Followup while running — goes to _message_queue
    result = handle.followup("followup message")
    assert result is True
    assert "followup message" in handle._message_queue

    # TurnRunner._post_turn_prompts must NOT have the message
    assert "test-session" not in turn_runner._post_turn_prompts
    assert len(turn_runner._post_turn_prompts) == 0

    # Release the turn, let it process the followup as next turn
    release_event.set()
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task

    # TurnRunner queues still empty after full lifecycle
    assert "test-session" not in turn_runner._post_turn_prompts
    assert len(turn_runner._post_turn_prompts) == 0
    assert "test-session" not in turn_runner._post_turn_injections
    assert len(turn_runner._post_turn_injections) == 0


# ---------------------------------------------------------------------------
# Test 4: Injection uses inject()/consume(), not deprecated queue()
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_injection_uses_inject_consume_not_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given an ACP turn, inject()/consume() are used, not queue().

    Verifies that:
    1. ``PromptInjectionManager.inject()`` populates ``_pending_injections``
       (not ``_queued_prompts`` via deprecated ``queue()``).
    2. ``consume()`` drains pending injections and returns wrapped context.
    3. ``queue()`` is never called during the ACP turn lifecycle.
    4. After execute, ``flush_pending_to_queue()`` (deprecated but still
       functional) moves unconsumed injections to queued prompts.
    """
    monkeypatch.setenv("AGENTPOOL_USE_RUN_TURN_FOR_ACP", "true")

    updates = [_text_update("ACP response")]
    messages = [_text_update("ACP response")]
    client = MockACPClient(updates=updates, messages=messages)

    run_ctx = AgentRunContext(session_id="test-session")
    injection_manager = run_ctx.injection_manager

    # Spy on queue() to verify it's never called directly
    original_queue = injection_manager.queue
    queue_call_count = 0

    def _spy_queue(*args: Any, **kwargs: Any) -> Any:
        nonlocal queue_call_count
        queue_call_count += 1
        return original_queue(*args, **kwargs)

    injection_manager.queue = _spy_queue  # type: ignore[method-assign]

    # Phase 1: inject() adds to pending, NOT to queued
    injection_manager.inject("extra tool context")
    assert injection_manager.has_pending()
    assert not injection_manager.has_queued()

    # Phase 2: consume() drains pending injections
    consumed = await injection_manager.consume()
    assert consumed is not None
    assert "extra tool context" in consumed
    assert "<injected-context>" in consumed
    assert not injection_manager.has_pending()

    # Phase 3: inject again, then run ACPTurn which calls flush_pending_to_queue
    injection_manager.inject("unconsumed context")

    turn = ACPTurn(
        acp_client=client,
        prompts=["Hello"],
        run_ctx=run_ctx,
        message_history=[],
        session_id="test-session",
    )

    # Execute the turn — flush_pending_to_queue() is called at end
    events: list[Any] = []
    async for event in turn.execute():
        events.append(event)  # noqa: PERF401

    # Verify turn completed
    assert any(isinstance(e, StreamCompleteEvent) for e in events)

    # After execute: pending flushed to queued (via deprecated flush_pending_to_queue)
    assert not injection_manager.has_pending()
    assert injection_manager.has_queued()

    # KEY ASSERTION: queue() was never called directly during the turn
    assert queue_call_count == 0, (
        "PromptInjectionManager.queue() should not be called during ACP turn. "
        f"Got {queue_call_count} calls."
    )

    # The queued prompts came from flush_pending_to_queue(), not queue()
    queued = injection_manager._queued_prompts
    assert len(queued) == 1
    assert "unconsumed context" in queued[0][0]
