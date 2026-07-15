"""Integration tests: staged_content consumption through NativeTurn pipeline.

Verifies that skill instructions injected via ``staged_content`` are
correctly delivered to the model through the new RunHandle → NativeTurn
path. Two bugs are covered:

1. **staged_content not consumed**: The old ``run_stream()`` path
   consumed ``staged_content`` before calling the agentlet. The new
   ``NativeTurn.execute()`` path bypasses this, so skill instructions
   loaded by ``skill_bridge.py`` are silently discarded.

2. **str([]) → "[]" conversion**: When a user sends only a slash
   command, the ACP handler passes an empty list as ``content``.
   ``receive_request()`` calls ``str(content)`` which converts ``[]``
   to the literal string ``"[]"``, which becomes the model's prompt.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events.events import StreamCompleteEvent
from agentpool.agents.native_agent.turn import NativeTurn
from agentpool.orchestrator.core import EventBus, SessionState
from agentpool.orchestrator.run import RunHandle


if TYPE_CHECKING:
    from agentpool.agents.events.events import RichAgentStreamEvent


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Bug 1: staged_content not consumed in NativeTurn path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staged_content_consumed_by_native_turn() -> None:
    """NativeTurn.execute() must consume agent.staged_content.

    When a skill command injects instructions into ``staged_content``,
    NativeTurn must prepend them to the prompts before calling
    ``agentlet.iter()``. Without this, skill instructions are silently
    discarded and the model never sees them.
    """
    skill_instructions = "<skill-instruction>\nDo the thing.\n</skill-instruction>"
    user_request = "run the skill"

    agent = Agent(
        name="test-staged",
        model=TestModel(custom_output_text="skill executed"),
    )
    async with agent:
        # Simulate skill_bridge injecting instructions into staged_content
        agent.staged_content.add_text(skill_instructions)

        run_ctx = AgentRunContext(session_id="test-staged-session")
        turn = NativeTurn(
            agent=agent,
            prompts=[user_request],
            run_ctx=run_ctx,
            message_history=[],
        )

        events: list[Any] = []
        events.extend([event async for event in turn.execute()])

        # After execute(), staged_content should be consumed (empty)
        assert len(agent.staged_content) == 0, (
            "staged_content was not consumed by NativeTurn.execute() — "
            "skill instructions were silently discarded"
        )

        # The message history should contain the skill instructions
        history = turn.message_history
        all_text = " ".join(
            str(getattr(part, "content", ""))
            for msg in history
            for part in getattr(msg, "parts", [])
        )

        assert skill_instructions in all_text or "Do the thing" in all_text, (
            f"Skill instructions not found in message history. History text: {all_text[:500]}"
        )


@pytest.mark.asyncio
async def test_staged_content_prepended_to_prompts_in_native_turn() -> None:
    """staged_content should be prepended to user prompts in NativeTurn.

    The combined prompt should be: [staged_content, user_prompt]
    so the model sees skill instructions first, then the user request.
    """
    skill_text = "SKILL_INSTRUCTIONS_HERE"
    user_text = "USER_REQUEST_HERE"

    agent = Agent(
        name="test-staged-order",
        model=TestModel(custom_output_text="ok"),
    )
    async with agent:
        agent.staged_content.add_text(skill_text)

        run_ctx = AgentRunContext(session_id="test-order-session")
        turn = NativeTurn(
            agent=agent,
            prompts=[user_text],
            run_ctx=run_ctx,
            message_history=[],
        )

        async for _ in turn.execute():
            pass

        # Verify the first user message contains both skill and user text
        history = turn.message_history
        # Find the user prompt message (ModelRequest with UserPromptPart)
        user_parts = [
            part
            for msg in history
            for part in getattr(msg, "parts", [])
            if "USER_REQUEST_HERE" in str(getattr(part, "content", ""))
            or "SKILL_INSTRUCTIONS_HERE" in str(getattr(part, "content", ""))
        ]

        assert len(user_parts) > 0, (
            "Neither skill instructions nor user request found in message history"
        )

        # Check that both are present
        combined = " ".join(str(p.content) for p in user_parts)
        assert "SKILL_INSTRUCTIONS_HERE" in combined, (
            f"Skill instructions missing from prompt. Combined: {combined[:300]}"
        )
        assert "USER_REQUEST_HERE" in combined, (
            f"User request missing from prompt. Combined: {combined[:300]}"
        )


@pytest.mark.asyncio
async def test_no_staged_content_does_not_break_native_turn() -> None:
    """When staged_content is empty, NativeTurn should work normally.

    This is the control case — no skill instructions, just a regular prompt.
    """
    agent = Agent(
        name="test-no-staged",
        model=TestModel(custom_output_text="normal response"),
    )
    async with agent:
        # Don't add any staged content
        assert len(agent.staged_content) == 0

        run_ctx = AgentRunContext(session_id="test-no-staged-session")
        turn = NativeTurn(
            agent=agent,
            prompts=["hello"],
            run_ctx=run_ctx,
            message_history=[],
        )

        events: list[Any] = [event async for event in turn.execute()]

        # Should still work normally
        assert len(events) > 0
        assert turn.final_message is not None
        assert "normal response" in turn.final_message.content


# ---------------------------------------------------------------------------
# Bug 2: str([]) → "[]" conversion in receive_request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_receive_request_empty_list_not_converted_to_string() -> None:
    """receive_request must not convert empty list [] to string "[]".

    When the ACP handler sends only a slash command, non_command_content
    is an empty list. With D9, list content is passed directly to
    followup()/steer() without stringification. The initial prompt
    is routed through followup() (D17), and start() is called with "".
    """
    from unittest.mock import MagicMock

    from agentpool.orchestrator.core import SessionController

    mock_pool = MagicMock()
    mock_pool.main_agent = MagicMock()
    mock_pool.main_agent.name = "main-agent"
    mock_pool.manifest = MagicMock()
    mock_pool.manifest.agents = {}

    controller = SessionController(pool=mock_pool)
    event_bus = EventBus()
    controller._event_bus = event_bus

    mock_agent = MagicMock()
    mock_agent.AGENT_TYPE = "native"

    import asyncio as _asyncio

    session_id = "sess-empty-content"
    controller._sessions[session_id] = MagicMock()
    controller._sessions[session_id].session_id = session_id
    controller._sessions[session_id].current_run_id = None
    controller._sessions[session_id].closing = False
    controller._sessions[session_id].is_closing = False
    controller._sessions[session_id]._request_lock = _asyncio.Lock()
    controller._sessions[session_id].turn_lock = _asyncio.Lock()
    controller._sessions[session_id].input_provider = None
    controller._session_agents[session_id] = mock_agent

    # Patch _consume_run so we can inspect what content was passed
    captured_content: list[str] = []

    async def _capture_consume(run_handle: Any, initial_prompt: str) -> None:
        captured_content.append(initial_prompt)

    controller._consume_run = _capture_consume  # type: ignore[method-assign]

    # Call receive_request with empty list (what ACP handler passes)
    result = await controller.receive_request(session_id, [])

    assert result is not None, "Expected a message_id to be returned"

    # Give the background task a moment to run
    import asyncio as _aio

    await _aio.sleep(0.1)

    # D17: start() is called with "" (empty string), not the content.
    # The content was routed through followup() before start().
    assert len(captured_content) > 0, " _consume_run was never called"
    assert captured_content[0] == "", (
        f"Expected empty string for start() initial_prompt, got {captured_content[0]!r}"
    )
    assert captured_content[0] != "[]", "Empty list was converted to '[]' — this is the bug"


# ---------------------------------------------------------------------------
# Full pipeline integration: staged_content + RunHandle + EventBus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staged_content_reaches_model_through_runhandle_pipeline() -> None:
    """Full pipeline: staged_content → RunHandle.start() → NativeTurn → EventBus.

    This mirrors the real ACP flow:
    1. Skill bridge injects instructions into staged_content
    2. ACP handler calls receive_request with empty content
    3. RunHandle.start() drives NativeTurn
    4. NativeTurn must consume staged_content and pass to agentlet
    5. Model should receive skill instructions, not "[]"
    """
    skill_instructions = "IMPORTANT_SKILL_DIRECTIVE"
    agent = Agent(
        name="test-pipeline-staged",
        model=TestModel(custom_output_text="pipeline response"),
    )
    async with agent:
        # Step 1: Skill bridge injects instructions
        agent.staged_content.add_text(skill_instructions)

        event_bus = EventBus()
        session = SessionState(
            session_id="test-pipeline-session",
            agent_name="test-pipeline-staged",
        )
        run_ctx = AgentRunContext(
            session_id="test-pipeline-session",
            event_bus=event_bus,
        )
        run_handle = RunHandle(
            run_id="test-pipeline-run",
            session_id="test-pipeline-session",
            agent_type="test-pipeline-staged",
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
        )

        # Step 2: Subscribe to EventBus
        receive_stream = await event_bus.subscribe(
            "test-pipeline-session",
            scope="session",
        )

        # Step 3: Start run — D17 pattern: initial prompt via followup()
        # before start(""). With DirectChannel, followup("") appends to
        # _message_queue, and start("") enters _idle_loop() which finds
        # it without blocking.
        run_handle.followup("")

        async def _drive_run() -> None:
            async for _ in run_handle.start(""):
                pass

        drive_task = asyncio.create_task(_drive_run())

        # Step 4: Consume events, wait for StreamCompleteEvent
        received_events: list[RichAgentStreamEvent[Any]] = []
        stream_complete_received = False

        try:
            async with asyncio.timeout(10):
                while True:
                    try:
                        envelope = await receive_stream.get()
                    except asyncio.QueueShutDown:
                        break

                    event = envelope.event if hasattr(envelope, "event") else envelope
                    received_events.append(event)

                    if isinstance(event, StreamCompleteEvent):
                        stream_complete_received = True
                        break
        except TimeoutError:
            pytest.fail(
                "Timed out waiting for StreamCompleteEvent. "
                f"Events: {[type(e).__name__ for e in received_events]}"
            )
        finally:
            drive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await drive_task

        assert stream_complete_received, "Consumer never received StreamCompleteEvent"

        # Step 5: Verify staged_content was consumed
        assert len(agent.staged_content) == 0, (
            "staged_content was not consumed through the full pipeline"
        )

        # Step 6: Verify the model received skill instructions
        # (message history should contain the skill text)
        history = run_handle._message_history
        all_text = " ".join(
            str(getattr(part, "content", ""))
            for msg in history
            for part in getattr(msg, "parts", [])
        )
        assert skill_instructions in all_text, (
            f"Skill instructions '{skill_instructions}' not found in "
            f"message history. Text: {all_text[:500]}"
        )
