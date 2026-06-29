"""Tests for v2 event converter and prompt lifecycle."""

from __future__ import annotations

from pydantic_ai import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolReturnPart,
)
import pytest

from acp_v2.schema.session_updates import (
    AgentMessageChunk,
    StateUpdate,
    ToolCallUpdate,
)
from agentpool_server.acp_server.v2.event_converter import ACPEventConverterV2
from agentpool_server.acp_server.v2.prompt_lifecycle import PromptLifecycleManager


class TestPromptLifecycleManager:
    """Verify v2 prompt lifecycle state machine."""

    @pytest.mark.unit
    def test_initial_state_is_idle(self) -> None:
        mgr = PromptLifecycleManager()
        assert mgr.state == "idle"

    @pytest.mark.unit
    def test_idle_to_running(self) -> None:
        mgr = PromptLifecycleManager()
        result = mgr.transition_to_running()
        assert result == "running"
        assert mgr.state == "running"

    @pytest.mark.unit
    def test_running_to_idle(self) -> None:
        mgr = PromptLifecycleManager()
        mgr.transition_to_running()
        result = mgr.transition_to_idle("end_turn")
        assert result == "idle"
        assert mgr.stop_reason == "end_turn"

    @pytest.mark.unit
    def test_running_to_requires_action(self) -> None:
        mgr = PromptLifecycleManager()
        mgr.transition_to_running()
        result = mgr.transition_to_requires_action()
        assert result == "requires_action"

    @pytest.mark.unit
    def test_requires_action_to_running(self) -> None:
        mgr = PromptLifecycleManager()
        mgr.transition_to_running()
        mgr.transition_to_requires_action()
        result = mgr.transition_to_running()
        assert result == "running"


class TestEventConverterV2Text:
    """Verify v2 event converter text streaming."""

    @pytest.mark.unit
    async def test_text_part_start_emits_agent_message_chunk(self) -> None:
        converter = ACPEventConverterV2()
        event = PartStartEvent(index=0, part=TextPart(content="Hello"))
        updates = [u async for u in converter.convert(event)]
        assert len(updates) == 1
        assert isinstance(updates[0], AgentMessageChunk)
        assert updates[0].message_id  # required in v2

    @pytest.mark.unit
    async def test_text_delta_emits_agent_message_chunk(self) -> None:
        converter = ACPEventConverterV2()
        event = PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=" world"))
        updates = [u async for u in converter.convert(event)]
        assert len(updates) == 1
        assert isinstance(updates[0], AgentMessageChunk)
        assert updates[0].content.text == " world"  # type: ignore[attr-defined]

    @pytest.mark.unit
    async def test_chunk_message_id_is_consistent(self) -> None:
        converter = ACPEventConverterV2()
        event1 = PartStartEvent(index=0, part=TextPart(content="A"))
        event2 = PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="B"))
        updates1 = [u async for u in converter.convert(event1)]
        updates2 = [u async for u in converter.convert(event2)]
        assert updates1[0].message_id == updates2[0].message_id


class TestEventConverterV2StateUpdate:
    """Verify v2 event converter emits state_update."""

    @pytest.mark.unit
    async def test_stream_complete_emits_idle(self) -> None:
        from agentpool.agents.events import StreamCompleteEvent
        from agentpool.messaging.messages import ChatMessage

        converter = ACPEventConverterV2()
        msg = ChatMessage(content="done", role="assistant")
        event = StreamCompleteEvent(message=msg)
        updates = [u async for u in converter.convert(event)]
        assert len(updates) == 1
        assert isinstance(updates[0], StateUpdate)
        assert updates[0].state == "idle"
        assert updates[0].stop_reason == "end_turn"


class TestEventConverterV2ToolCallUpdate:
    """Verify v2 event converter uses unified tool_call_update."""

    @pytest.mark.unit
    async def test_tool_call_start_emits_tool_call_update(self) -> None:
        converter = ACPEventConverterV2()
        event = FunctionToolCallEvent(
            part=type(
                "MockPart",
                (),
                {
                    "tool_call_id": "tc1",
                    "tool_name": "read_file",
                    "args": {"path": "/test"},
                },
            )()
        )
        updates = [u async for u in converter.convert(event)]
        assert len(updates) >= 1
        assert isinstance(updates[0], ToolCallUpdate)
        assert updates[0].tool_call_id == "tc1"
        assert updates[0].status == "pending"

    @pytest.mark.unit
    async def test_tool_result_emits_completed_update(self) -> None:
        converter = ACPEventConverterV2()
        start_event = FunctionToolCallEvent(
            part=type(
                "MockPart",
                (),
                {
                    "tool_call_id": "tc2",
                    "tool_name": "search",
                    "args": {"q": "test"},
                },
            )()
        )
        _ = [u async for u in converter.convert(start_event)]

        result_part = ToolReturnPart(
            tool_name="search",
            content="found it",
            tool_call_id="tc2",
        )
        result_event = FunctionToolResultEvent(result=result_part)
        updates = [u async for u in converter.convert(result_event)]
        assert len(updates) >= 1
        update = updates[-1]
        assert isinstance(update, ToolCallUpdate)
        assert update.tool_call_id == "tc2"
        assert update.status == "completed"
