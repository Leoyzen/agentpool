"""Steer/queue delivery semantics tests (Group 6).

Verifies behavioral semantics of steer() and followup() through the
RunHandle — NOT event type assertions, since AgentPool does not emit
steer-specific events by design.

Tests use mock agents with stub turns to control timing and inspect
the prompts passed to create_turn, following the pattern in
tests/orchestrator/test_run_handle.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import anyio
import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunErrorEvent, RunStartedEvent, StreamCompleteEvent
from agentpool.lifecycle import RunState
from agentpool.lifecycle.journal import MemoryJournal
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import EventBus, SessionState
from agentpool.orchestrator.run import RunHandle


if TYPE_CHECKING:
    from agentpool.lifecycle.comm_channel import ProtocolChannel


pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def _make_handle(
    *,
    agent: Any | None = None,
    event_bus: EventBus | None = None,
) -> tuple[RunHandle, EventBus, list[list[Any]]]:
    """Create a RunHandle with a mock agent that captures prompts.

    Returns (handle, event_bus, captured_prompts).
    """
    bus = event_bus or EventBus()
    captured_prompts: list[list[Any]] = []

    mock_agent = agent or MagicMock()
    # Capture prompts passed to create_turn.
    original_create_turn = mock_agent.create_turn

    def _capturing_create_turn(*, prompts: list[Any], **kwargs: Any) -> Any:
        captured_prompts.append(list(prompts))
        return original_create_turn(prompts=prompts, **kwargs)

    mock_agent.create_turn = _capturing_create_turn

    session = SessionState(session_id="test-sess", agent_name="test-agent")
    run_ctx = AgentRunContext(session_id="test-sess", event_bus=bus)
    handle = RunHandle(
        run_id="test-run",
        session_id="test-sess",
        agent_type="native",
        agent=mock_agent,
        event_bus=bus,
        session=session,
        run_ctx=run_ctx,
    )
    return handle, bus, captured_prompts


def _stub_turn(
    *,
    output: str = "done",
    fail: bool = False,
) -> Any:
    """Create a stub Turn that yields minimal events."""
    turn = MagicMock()

    async def _execute():
        yield RunStartedEvent(session_id="test-sess", run_id="test-run")
        if fail:
            yield RunErrorEvent(
                session_id="test-sess",
                run_id="test-run",
                error_type="TestError",
                error_message="Turn failed",
            )
            return
        yield StreamCompleteEvent(
            message=ChatMessage(content=output, role="assistant"),
            cancelled=False,
            session_id="test-sess",
        )

    turn.execute = _execute
    return turn


async def test_steer_message_appears_as_prompt() -> None:
    """Steer message while idle becomes a prompt for the next turn.

    Given: A RunHandle that completed its first turn and is idle.
    When: handle.steer("important update") is called.
    Then: The next turn's prompts include "important update".
    """
    call_count = 0

    def create_turn(**kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return _stub_turn()

    mock_agent = MagicMock()
    mock_agent.create_turn = create_turn

    handle, _bus, captured = _make_handle(agent=mock_agent)

    events: list[Any] = []
    gen = handle.start("initial")

    async with anyio.create_task_group() as tg:

        async def _consume() -> None:
            async for event in gen:
                events.append(event)  # noqa: PERF401

        tg.start_soon(_consume)
        await anyio.sleep(0.05)

        handle.steer("important update")
        await anyio.sleep(0.05)

        handle.close()
        await anyio.sleep(0.05)

    # Two turns: initial + steer.
    assert call_count == 2
    # Second turn's prompts should include the steer message.
    assert len(captured) >= 2
    steer_prompts = captured[1]
    assert any("important update" in str(p) for p in steer_prompts)


async def test_multiple_steers_coalesce_into_one_turn() -> None:
    """Two steers while idle produce one additional turn, not two.

    Given: A RunHandle that completed its first turn and is idle.
    When: handle.steer("msg1") and handle.steer("msg2") are called.
    Then: Only one additional turn is created containing both messages.
    """
    call_count = 0

    def create_turn(**kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return _stub_turn()

    mock_agent = MagicMock()
    mock_agent.create_turn = create_turn

    handle, _bus, captured = _make_handle(agent=mock_agent)

    gen = handle.start("initial")

    async with anyio.create_task_group() as tg:

        async def _consume() -> None:
            async for _event in gen:
                pass

        tg.start_soon(_consume)
        await anyio.sleep(0.05)

        handle.steer("msg1")
        handle.steer("msg2")
        await anyio.sleep(0.05)

        handle.close()
        await anyio.sleep(0.05)

    # Two turns: initial + one for both steers (coalesced).
    assert call_count == 2
    assert len(captured) >= 2
    all_steer_text = " ".join(str(p) for p in captured[1])
    assert "msg1" in all_steer_text
    assert "msg2" in all_steer_text


async def test_followup_triggers_new_turn() -> None:
    """Followup while idle starts a new turn.

    Given: A RunHandle that completed its first turn and is idle.
    When: handle.followup("next prompt") is called.
    Then: A new turn starts (RunStartedEvent emitted for second turn).
    """
    call_count = 0

    def create_turn(**kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return _stub_turn()

    mock_agent = MagicMock()
    mock_agent.create_turn = create_turn

    handle, _bus, captured = _make_handle(agent=mock_agent)

    gen = handle.start("initial")

    async with anyio.create_task_group() as tg:

        async def _consume() -> None:
            async for _event in gen:
                pass

        tg.start_soon(_consume)
        await anyio.sleep(0.05)

        handle.followup("next prompt")
        await anyio.sleep(0.05)

        handle.close()
        await anyio.sleep(0.05)

    assert call_count == 2
    assert len(captured) >= 2
    assert any("next prompt" in str(p) for p in captured[1])


async def test_followup_fifo_ordering() -> None:
    """Multiple sequential followups are processed in FIFO order.

    Given: A RunHandle that completed turns for "first" and "second".
    When: followup("third") is called after "second" completes.
    Then: The prompts arrive in order: initial, first, second, third.
    """
    call_count = 0

    def create_turn(**kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return _stub_turn()

    mock_agent = MagicMock()
    mock_agent.create_turn = create_turn

    handle, _bus, captured = _make_handle(agent=mock_agent)

    gen = handle.start("initial")

    async with anyio.create_task_group() as tg:

        async def _consume() -> None:
            async for _event in gen:
                pass

        tg.start_soon(_consume)
        await anyio.sleep(0.05)

        handle.followup("first")
        await anyio.sleep(0.05)
        handle.followup("second")
        await anyio.sleep(0.05)
        handle.followup("third")
        await anyio.sleep(0.05)

        handle.close()
        await anyio.sleep(0.05)

    # Four turns: initial + first + second + third.
    assert call_count == 4
    assert len(captured) >= 4
    assert any("first" in str(p) for p in captured[1])
    assert any("second" in str(p) for p in captured[2])
    assert any("third" in str(p) for p in captured[3])


@pytest.mark.xfail(
    reason="RunLoop exits after failed turn (RunErrorEvent), closing the handle "
    "before steer can be processed. See run.py _handle_turn_result 'break' on "
    "turn_failed. Design issue: failed turns prevent recovery via steer.",
    strict=False,
    raises=BaseException,
)
@pytest.mark.known_bug
async def test_steer_during_failed_turn() -> None:
    """Steer after a failed turn is processed in the next attempt.

    Given: A RunHandle where the first turn fails (RunErrorEvent).
    When: handle.steer("retry info") is called.
    Then: The steer message should be processed in a subsequent turn.

    NOTE: This test may FAIL if the RunLoop breaks after a failed turn
    instead of continuing to idle. This would indicate a design issue
    where failed turns prevent recovery via steer.
    """
    call_count = 0

    def create_turn(**kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _stub_turn(fail=True)
        return _stub_turn()

    mock_agent = MagicMock()
    mock_agent.create_turn = create_turn

    handle, _bus, captured = _make_handle(agent=mock_agent)

    events: list[Any] = []
    gen = handle.start("initial")

    async with anyio.create_task_group() as tg:

        async def _consume() -> None:
            async for event in gen:
                events.append(event)  # noqa: PERF401

        tg.start_soon(_consume)
        await anyio.sleep(0.05)

        handle.steer("retry info")
        await anyio.sleep(0.05)

        handle.close()
        await anyio.sleep(0.05)

    # Check if the steer was processed.
    # If the loop breaks after failure, call_count will be 1 (no second turn).
    # If the loop continues, call_count will be 2 (steer triggered new turn).
    has_error = any(isinstance(e, RunErrorEvent) for e in events)
    assert has_error, "First turn should have failed"

    # This assertion may fail — documenting the behavior.
    if call_count == 1:
        pytest.fail(
            "RunLoop breaks after failed turn — steer message is never processed. "
            "The loop exits on RunErrorEvent without returning to idle, so queued "
            "steer messages are lost. This may be a design issue if recovery via "
            "steer is expected."
        )
    assert call_count >= 2
    assert any("retry info" in str(p) for p in captured[1])


# ---------------------------------------------------------------------------
# In-flight steer: steer during an ACTIVE turn (RunState.RUNNING)
# ---------------------------------------------------------------------------


def _blocking_stub_turn(
    *,
    block_event: anyio.Event,
    output: str = "done",
) -> Any:
    """Create a stub Turn that blocks on an event before completing.

    This allows testing steer/followup during an active turn.
    """
    turn = MagicMock()

    async def _execute() -> Any:
        yield RunStartedEvent(session_id="test-sess", run_id="test-run")
        await block_event.wait()
        yield StreamCompleteEvent(
            message=ChatMessage(content=output, role="assistant"),
            cancelled=False,
            session_id="test-sess",
        )

    turn.execute = _execute
    # Simulate message_history accumulation (NativeTurn sets this).
    turn.message_history = []
    return turn


async def test_inflight_steer_single_message() -> None:
    """Steer during an active turn is enqueued to agent_run with priority='asap'.

    Given: A RunHandle with a turn actively running (RunState.RUNNING).
    When: handle.steer("in-flight msg") is called.
    Then: agent_run.enqueue("in-flight msg", priority="asap") is called.
    """
    block_event = anyio.Event()
    mock_agent_run = MagicMock()
    mock_agent_run.enqueue = MagicMock()

    def create_turn(**kwargs: Any) -> Any:
        turn = _blocking_stub_turn(block_event=block_event)
        # Simulate NativeTurn setting active_agent_run.
        turn._mock_agent_run = mock_agent_run
        return turn

    mock_agent = MagicMock()
    mock_agent.create_turn = create_turn

    handle, _bus, _captured = _make_handle(agent=mock_agent)

    events: list[Any] = []
    gen = handle.start("initial")

    async with anyio.create_task_group() as tg:

        async def _consume() -> None:
            async for event in gen:
                events.append(event)  # noqa: PERF401

        tg.start_soon(_consume)
        await anyio.sleep(0.05)

        # Turn is now running and blocked on block_event.
        assert handle._run_state == RunState.RUNNING
        # Simulate NativeTurn setting active_agent_run during turn execution.
        handle.active_agent_run = mock_agent_run

        handle.steer("in-flight msg")
        await anyio.sleep(0.02)

        # Verify enqueue was called with the steer message and asap priority.
        mock_agent_run.enqueue.assert_called_once()
        call_args = mock_agent_run.enqueue.call_args
        assert call_args.args[0] == "in-flight msg"
        assert call_args.kwargs.get("priority") == "asap"

        # Release the turn to complete.
        block_event.set()
        await anyio.sleep(0.05)

        handle.close()
        await anyio.sleep(0.05)


async def test_inflight_steer_multiple_messages() -> None:
    """Multiple steers during an active turn are all enqueued to agent_run.

    Given: A RunHandle with a turn actively running.
    When: handle.steer("msg1"), steer("msg2"), steer("msg3") are called.
    Then: agent_run.enqueue is called 3 times, each with priority="asap".
    """
    block_event = anyio.Event()
    mock_agent_run = MagicMock()
    mock_agent_run.enqueue = MagicMock()

    def create_turn(**kwargs: Any) -> Any:
        return _blocking_stub_turn(block_event=block_event)

    mock_agent = MagicMock()
    mock_agent.create_turn = create_turn

    handle, _bus, _captured = _make_handle(agent=mock_agent)

    gen = handle.start("initial")

    async with anyio.create_task_group() as tg:

        async def _consume() -> None:
            async for _event in gen:
                pass

        tg.start_soon(_consume)
        await anyio.sleep(0.05)

        handle.active_agent_run = mock_agent_run

        handle.steer("msg1")
        handle.steer("msg2")
        handle.steer("msg3")
        await anyio.sleep(0.02)

        # All 3 steers should be enqueued.
        assert mock_agent_run.enqueue.call_count == 3
        enqueued_msgs = [call.args[0] for call in mock_agent_run.enqueue.call_args_list]
        assert enqueued_msgs == ["msg1", "msg2", "msg3"]
        # All with asap priority.
        for call in mock_agent_run.enqueue.call_args_list:
            assert call.kwargs.get("priority") == "asap"

        block_event.set()
        await anyio.sleep(0.05)

        handle.close()
        await anyio.sleep(0.05)


# ---------------------------------------------------------------------------
# History preservation: steer/followup does not lose conversation history
# ---------------------------------------------------------------------------


def _history_capturing_create_turn(
    captured_history: list[list[Any]],
    call_count: list[int],
    *,
    output: str = "done",
    history: list[Any] | None = None,
) -> Any:
    """Create a create_turn factory that captures message_history.

    Each call appends the message_history to captured_history.
    The first call returns a turn with a fake message_history;
    subsequent calls receive that history.
    """

    def create_turn(**kwargs: Any) -> Any:
        call_count[0] += 1
        msg_history = kwargs.get("message_history", [])
        captured_history.append(list(msg_history))

        turn = _stub_turn(output=output)
        # Simulate NativeTurn accumulating message_history after execution.
        # Each turn adds a user message and assistant response to history.
        turn.message_history = [
            *msg_history,
            ChatMessage(content=f"user-msg-{call_count[0]}", role="user"),
            ChatMessage(content=output, role="assistant"),
        ]
        return turn

    return create_turn


async def test_steer_preserves_conversation_history() -> None:
    """Steer does not lose conversation history across turns.

    Given: A RunHandle that completed turn 1 with some message history.
    When: handle.steer("steer msg") triggers turn 2.
    Then: Turn 2's message_history includes turn 1's messages AND the steer msg.
    """
    call_count = [0]
    captured_history: list[list[Any]] = []

    mock_agent = MagicMock()
    mock_agent.create_turn = _history_capturing_create_turn(
        captured_history,
        call_count,
    )

    handle, _bus, captured = _make_handle(agent=mock_agent)

    gen = handle.start("initial")

    async with anyio.create_task_group() as tg:

        async def _consume() -> None:
            async for _event in gen:
                pass

        tg.start_soon(_consume)
        await anyio.sleep(0.05)

        handle.steer("steer msg")
        await anyio.sleep(0.05)

        handle.close()
        await anyio.sleep(0.05)

    # Two turns: initial + steer.
    assert call_count[0] == 2
    # Turn 1 starts with empty history.
    assert len(captured_history[0]) == 0
    # Turn 2 starts with turn 1's accumulated history (2 messages).
    assert len(captured_history[1]) == 2
    # History includes user message from turn 1.
    history_contents = [getattr(msg, "content", str(msg)) for msg in captured_history[1]]
    assert any("user-msg-1" in str(c) for c in history_contents)
    # Steer message appears as a prompt in turn 2.
    assert any("steer msg" in str(p) for p in captured[1])


async def test_followup_preserves_conversation_history() -> None:
    """Followup does not lose conversation history across turns.

    Given: A RunHandle that completed turn 1 with some message history.
    When: handle.followup("next prompt") triggers turn 2.
    Then: Turn 2's message_history includes turn 1's messages AND the followup.
    """
    call_count = [0]
    captured_history: list[list[Any]] = []

    mock_agent = MagicMock()
    mock_agent.create_turn = _history_capturing_create_turn(
        captured_history,
        call_count,
    )

    handle, _bus, captured = _make_handle(agent=mock_agent)

    gen = handle.start("initial")

    async with anyio.create_task_group() as tg:

        async def _consume() -> None:
            async for _event in gen:
                pass

        tg.start_soon(_consume)
        await anyio.sleep(0.05)

        handle.followup("next prompt")
        await anyio.sleep(0.05)

        handle.close()
        await anyio.sleep(0.05)

    assert call_count[0] == 2
    # Turn 1 starts with empty history.
    assert len(captured_history[0]) == 0
    # Turn 2 starts with turn 1's accumulated history.
    assert len(captured_history[1]) == 2
    history_contents = [getattr(msg, "content", str(msg)) for msg in captured_history[1]]
    assert any("user-msg-1" in str(c) for c in history_contents)
    # Followup message appears as a prompt in turn 2.
    assert any("next prompt" in str(p) for p in captured[1])


# ---------------------------------------------------------------------------
# In-flight followup: followup during an ACTIVE turn
# ---------------------------------------------------------------------------


async def test_inflight_followup_queued_for_next_turn() -> None:
    """Followup during an active turn is queued and processed after turn ends.

    Given: A RunHandle with a turn actively running (RunState.RUNNING).
    When: handle.followup("queued msg") is called.
    Then: The message is queued (not lost) and processed in the next turn
        after the current one completes.
    """
    block_event = anyio.Event()
    turn_count = 0

    def create_turn(**kwargs: Any) -> Any:
        nonlocal turn_count
        turn_count += 1
        if turn_count == 1:
            return _blocking_stub_turn(block_event=block_event)
        return _stub_turn()

    mock_agent = MagicMock()
    mock_agent.create_turn = create_turn

    handle, _bus, captured = _make_handle(agent=mock_agent)

    gen = handle.start("initial")

    async with anyio.create_task_group() as tg:

        async def _consume() -> None:
            async for _event in gen:
                pass

        tg.start_soon(_consume)
        await anyio.sleep(0.05)

        # Turn 1 is running and blocked.
        assert handle._run_state == RunState.RUNNING

        # Followup while running — should queue for next turn.
        handle.followup("queued msg")
        await anyio.sleep(0.02)

        # Release turn 1 to complete.
        block_event.set()
        await anyio.sleep(0.05)

        handle.close()
        await anyio.sleep(0.05)

    # Two turns: initial + followup.
    assert turn_count == 2
    # Turn 2's prompts include the followup message.
    assert len(captured) >= 2
    assert any("queued msg" in str(p) for p in captured[1])


# ---------------------------------------------------------------------------
# ProtocolChannel delivery: steer/followup via CommChannel feedback loop
# ---------------------------------------------------------------------------


def _make_handle_with_protocol_channel(
    *,
    agent: Any | None = None,
) -> tuple[RunHandle, EventBus, list[list[Any]], ProtocolChannel]:
    """Create a RunHandle wired with a ProtocolChannel (instead of DirectChannel).

    Returns (handle, event_bus, captured_prompts, protocol_channel).
    """
    from agentpool.lifecycle.comm_channel import ProtocolChannel

    bus = EventBus()
    captured_prompts: list[list[Any]] = []

    mock_agent = agent or MagicMock()
    original_create_turn = mock_agent.create_turn

    def _capturing_create_turn(*, prompts: list[Any], **kwargs: Any) -> Any:
        captured_prompts.append(list(prompts))
        return original_create_turn(prompts=prompts, **kwargs)

    mock_agent.create_turn = _capturing_create_turn

    session = SessionState(session_id="test-sess", agent_name="test-agent")
    run_ctx = AgentRunContext(session_id="test-sess", event_bus=bus)
    channel = ProtocolChannel(
        journal=MemoryJournal(),
        event_bus=bus,
        session_id="test-sess",
    )
    handle = RunHandle(
        run_id="test-run",
        session_id="test-sess",
        agent_type="native",
        agent=mock_agent,
        event_bus=bus,
        session=session,
        run_ctx=run_ctx,
        _comm_channel=channel,
    )
    return handle, bus, captured_prompts, channel


async def test_steer_via_protocol_channel_delivered_to_next_turn() -> None:
    """Steer via ProtocolChannel is delivered to the next turn's prompts.

    Given: A RunHandle wired with a ProtocolChannel (not DirectChannel).
    When: handle.steer("steered via protocol") is called while idle.
    Then: The second turn's prompts include "steered via protocol".
    """
    call_count = 0

    def create_turn(**kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return _stub_turn()

    mock_agent = MagicMock()
    mock_agent.create_turn = create_turn

    handle, _bus, captured, _channel = _make_handle_with_protocol_channel(
        agent=mock_agent,
    )

    gen = handle.start("initial")

    async with anyio.create_task_group() as tg:

        async def _consume() -> None:
            async for _event in gen:
                pass

        tg.start_soon(_consume)
        await anyio.sleep(0.05)

        handle.steer("steered via protocol")
        await anyio.sleep(0.05)

        handle.close()
        await anyio.sleep(0.05)

    assert call_count == 2
    assert len(captured) >= 2
    assert any("steered via protocol" in str(p) for p in captured[1])


async def test_followup_via_protocol_channel_delivered_to_next_turn() -> None:
    """Followup via ProtocolChannel is delivered to the next turn's prompts.

    Given: A RunHandle wired with a ProtocolChannel (not DirectChannel).
    When: handle.followup("followup via protocol") is called while idle.
    Then: The second turn's prompts include "followup via protocol".
    """
    call_count = 0

    def create_turn(**kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return _stub_turn()

    mock_agent = MagicMock()
    mock_agent.create_turn = create_turn

    handle, _bus, captured, _channel = _make_handle_with_protocol_channel(
        agent=mock_agent,
    )

    gen = handle.start("initial")

    async with anyio.create_task_group() as tg:

        async def _consume() -> None:
            async for _event in gen:
                pass

        tg.start_soon(_consume)
        await anyio.sleep(0.05)

        handle.followup("followup via protocol")
        await anyio.sleep(0.05)

        handle.close()
        await anyio.sleep(0.05)

    assert call_count == 2
    assert len(captured) >= 2
    assert any("followup via protocol" in str(p) for p in captured[1])


async def test_steer_prioritized_over_followup_via_protocol_channel() -> None:
    """Steer feedback is prioritized over followup in _drain_events.

    Given: A RunHandle wired with a ProtocolChannel, with a turn
        actively running (blocked).
    When: followup("followup msg") then steer("steer msg") are called
        during the running turn (both routed via ProtocolChannel
        feedback queue).
    Then: After the turn completes, _drain_events drains the feedback
        queue with steer prioritized — "steer msg" appears BEFORE
        "followup msg" in the second turn's prompts.
    """
    block_event = anyio.Event()
    turn_count = 0

    def create_turn(**kwargs: Any) -> Any:
        nonlocal turn_count
        turn_count += 1
        if turn_count == 1:
            return _blocking_stub_turn(block_event=block_event)
        return _stub_turn()

    mock_agent = MagicMock()
    mock_agent.create_turn = create_turn

    handle, _bus, captured, _channel = _make_handle_with_protocol_channel(
        agent=mock_agent,
    )

    gen = handle.start("initial")

    async with anyio.create_task_group() as tg:

        async def _consume() -> None:
            async for _event in gen:
                pass

        tg.start_soon(_consume)
        await anyio.sleep(0.05)

        # Turn 1 is running and blocked.
        assert handle._run_state == RunState.RUNNING

        # Deliver followup first, then steer — both via ProtocolChannel
        # feedback queue. _drain_events will prioritize steer.
        handle.followup("followup msg")
        handle.steer("steer msg")
        await anyio.sleep(0.02)

        # Release turn 1 to complete — _drain_events drains feedback.
        block_event.set()
        await anyio.sleep(0.05)

        handle.close()
        await anyio.sleep(0.05)

    assert turn_count == 2
    assert len(captured) >= 2
    second_turn_prompts = [str(p) for p in captured[1]]
    steer_idx = next(
        (i for i, p in enumerate(second_turn_prompts) if "steer msg" in p),
        None,
    )
    followup_idx = next(
        (i for i, p in enumerate(second_turn_prompts) if "followup msg" in p),
        None,
    )
    assert steer_idx is not None, "steer msg not found in second turn prompts"
    assert followup_idx is not None, "followup msg not found in second turn prompts"
    assert steer_idx < followup_idx, (
        f"steer msg (idx={steer_idx}) should come before followup msg "
        f"(idx={followup_idx}) in second turn prompts"
    )


# ---------------------------------------------------------------------------
# Queued steer: steer during RUNNING with no active_agent_run
# ---------------------------------------------------------------------------


async def test_queued_steer_delivered_to_next_turn() -> None:
    """Steer during a running turn with no active_agent_run is queued.

    Given: A RunHandle with a turn actively running (RunState.RUNNING)
        but active_agent_run set to None (no agent_run available).
    When: handle.steer("queued steer") is called.
    Then: The message lands in run_ctx.queued_steer_messages and appears
        in the second turn's prompts after the blocking turn completes.
    """
    block_event = anyio.Event()
    turn_count = 0

    def create_turn(**kwargs: Any) -> Any:
        nonlocal turn_count
        turn_count += 1
        if turn_count == 1:
            return _blocking_stub_turn(block_event=block_event)
        return _stub_turn()

    mock_agent = MagicMock()
    mock_agent.create_turn = create_turn

    handle, _bus, captured = _make_handle(agent=mock_agent)

    gen = handle.start("initial")

    async with anyio.create_task_group() as tg:

        async def _consume() -> None:
            async for _event in gen:
                pass

        tg.start_soon(_consume)
        await anyio.sleep(0.05)

        # Turn 1 is running and blocked.
        assert handle._run_state == RunState.RUNNING
        # Simulate no active agent_run available for enqueue.
        handle.active_agent_run = None

        handle.steer("queued steer")
        await anyio.sleep(0.02)

        # The steer message should be in queued_steer_messages.
        assert any("queued steer" in str(m) for m in handle.run_ctx.queued_steer_messages), (
            "steer message should be queued in run_ctx.queued_steer_messages"
        )

        # Release turn 1 to complete.
        block_event.set()
        await anyio.sleep(0.05)

        handle.close()
        await anyio.sleep(0.05)

    # Two turns: initial + queued steer.
    assert turn_count == 2
    assert len(captured) >= 2
    assert any("queued steer" in str(p) for p in captured[1])


# ---------------------------------------------------------------------------
# History accumulation across three turns
# ---------------------------------------------------------------------------


async def test_message_history_accumulates_across_three_turns() -> None:
    """Message history accumulates across three turns via steer.

    Given: A RunHandle with a history-capturing create_turn that
        returns increasing message_history: ["m1"], ["m1","m2"],
        ["m1","m2","m3"].
    When: Start with "turn1", steer "turn2", steer "turn3".
    Then: create_turn is called 3 times, and the third call's
        message_history contains all prior messages.
    """
    call_count = [0]
    captured_history: list[list[Any]] = []

    def create_turn(**kwargs: Any) -> Any:
        call_count[0] += 1
        msg_history = kwargs.get("message_history", [])
        captured_history.append(list(msg_history))

        # Simulate NativeTurn accumulating message_history after execution.
        # Each turn adds a user message and assistant response to history.
        turn = _stub_turn()
        turn.message_history = [
            *msg_history,
            ChatMessage(content=f"m{call_count[0]}", role="user"),
            ChatMessage(content=f"resp{call_count[0]}", role="assistant"),
        ]
        return turn

    mock_agent = MagicMock()
    mock_agent.create_turn = create_turn

    handle, _bus, _captured = _make_handle(agent=mock_agent)

    gen = handle.start("turn1")

    async with anyio.create_task_group() as tg:

        async def _consume() -> None:
            async for _event in gen:
                pass

        tg.start_soon(_consume)
        await anyio.sleep(0.05)

        handle.steer("turn2")
        await anyio.sleep(0.05)

        handle.steer("turn3")
        await anyio.sleep(0.05)

        handle.close()
        await anyio.sleep(0.05)

    # Three turns total.
    assert call_count[0] == 3, f"Expected 3 turns, got {call_count[0]}"
    # Turn 1 starts with empty history.
    assert len(captured_history[0]) == 0
    # Turn 2 starts with turn 1's accumulated history (2 messages).
    assert len(captured_history[1]) == 2
    # Turn 3 starts with turns 1+2 accumulated history (4 messages).
    assert len(captured_history[2]) == 4
    # The third call's message_history contains all prior messages.
    history_contents = [getattr(msg, "content", str(msg)) for msg in captured_history[2]]
    all_history_text = " ".join(str(c) for c in history_contents)
    assert "m1" in all_history_text
    assert "m2" in all_history_text
    assert "resp1" in all_history_text
    assert "resp2" in all_history_text
