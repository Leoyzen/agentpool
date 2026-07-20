"""Steer/queue delivery semantics tests (Group 6).

Verifies behavioral semantics of steer() and followup() through the
RunHandle — NOT event type assertions, since AgentPool does not emit
steer-specific events by design.

Tests use mock agents with stub turns to control timing and inspect
the prompts passed to create_turn, following the pattern in
tests/orchestrator/test_run_handle.py.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunErrorEvent, RunStartedEvent, StreamCompleteEvent
from agentpool.lifecycle import RunState
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import EventBus, SessionState
from agentpool.orchestrator.run import RunHandle


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

    handle, bus, captured = _make_handle(agent=mock_agent)

    events: list[Any] = []
    gen = handle.start("initial")

    async with anyio.create_task_group() as tg:

        async def _consume() -> None:
            async for event in gen:
                events.append(event)

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

    handle, bus, captured = _make_handle(agent=mock_agent)

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

    handle, bus, captured = _make_handle(agent=mock_agent)

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

    handle, bus, captured = _make_handle(agent=mock_agent)

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

    handle, bus, captured = _make_handle(agent=mock_agent)

    events: list[Any] = []
    gen = handle.start("initial")

    async with anyio.create_task_group() as tg:

        async def _consume() -> None:
            async for event in gen:
                events.append(event)

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
