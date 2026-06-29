"""ACP v2 event converter.

Converts RichAgentStreamEvent to v2 SessionUpdate types.

Key differences from v1:
- Independent class (NOT subclass of ACPEventConverter)
- Emits state_update (running/idle) instead of TurnCompleteUpdate
- Emits unified tool_call_update (no separate tool_call creation)
- messageId is required on all chunks
- plan_update with tagged content (type="items", id="main")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
import uuid

from pydantic_ai import (
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    NativeToolCallPart,
    NativeToolReturnPart,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
)

from acp.schema.tool_call import (
    ContentToolCallContent,
    ToolCallKind,
    ToolCallLocation,
)
from acp.utils import generate_tool_title, infer_tool_kind, to_acp_content_blocks
from acp_v2.schema._unset import _UNSET
from acp_v2.schema.session_updates import (
    AgentMessageChunk,
    AgentThoughtChunk,
    PlanItems,
    PlanUpdate,
    StateUpdate,
    ToolCallUpdate,
)
from agentpool.agents.events import (
    PlanUpdateEvent,
    RunErrorEvent,
    RunFailedEvent,
    SpawnSessionStart,
    StreamCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)
from agentpool.log import get_logger
from agentpool.utils.pydantic_ai_helpers import safe_args_as_dict


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from acp_v2.schema.session_updates import SessionUpdate
    from agentpool.agents.events import RichAgentStreamEvent

logger = get_logger(__name__)


@dataclass
class _V2ToolState:
    tool_call_id: str
    tool_name: str
    title: str
    kind: ToolCallKind
    raw_input: dict[str, Any]
    started: bool = False


@dataclass
class ACPEventConverterV2:
    """Convert agent stream events to v2 ACP session updates.

    Independent from v1 ACPEventConverter. Produces v2 SessionUpdate types
    including state_update, unified tool_call_update, and plan_update.
    """

    _tool_states: dict[str, _V2ToolState] = field(default_factory=dict)
    _current_message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _state_emitted: bool = field(default=False)

    def reset(self) -> None:
        self._tool_states.clear()
        self._current_message_id = str(uuid.uuid4())
        self._state_emitted = False

    def cleanup(self) -> None:
        self._tool_states.clear()

    async def cancel_pending_tools(self) -> AsyncIterator[ToolCallUpdate]:
        for tool_call_id in list(self._tool_states):
            if self._tool_states[tool_call_id].started:
                yield ToolCallUpdate(
                    tool_call_id=tool_call_id, status="completed"
                )
        self.reset()

    def _get_or_create_tool_state(
        self,
        tool_call_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> _V2ToolState:
        if tool_call_id not in self._tool_states:
            self._tool_states[tool_call_id] = _V2ToolState(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                title=generate_tool_title(tool_name, tool_input),
                kind=infer_tool_kind(tool_name),
                raw_input=tool_input,
            )
        return self._tool_states[tool_call_id]

    def _cleanup_tool_state(self, tool_call_id: str) -> None:
        self._tool_states.pop(tool_call_id, None)

    async def convert(
        self, event: RichAgentStreamEvent[Any]
    ) -> AsyncIterator[SessionUpdate]:
        """Convert an agent event to zero or more v2 session updates."""
        match event:
            case PartStartEvent(part=TextPart(content=delta)) | PartDeltaEvent(
                delta=TextPartDelta(content_delta=delta)
            ):
                yield AgentMessageChunk(
                    message_id=self._current_message_id,
                    content={"type": "text", "text": delta},
                )

            case PartStartEvent(
                part=ThinkingPart(content=delta)
            ) | PartDeltaEvent(delta=ThinkingPartDelta(content_delta=delta)):
                yield AgentThoughtChunk(
                    message_id=self._current_message_id,
                    content={"type": "text", "text": delta or "\n"},
                )

            case PartStartEvent(part=NativeToolCallPart() as part):
                tool_call_id = part.tool_call_id
                tool_input = safe_args_as_dict(part, default={})
                state = self._get_or_create_tool_state(
                    tool_call_id, part.tool_name, tool_input
                )
                if not state.started:
                    state.started = True
                    yield ToolCallUpdate(
                        tool_call_id=tool_call_id,
                        title=state.title,
                        kind=state.kind,
                        raw_input=state.raw_input,
                        status="pending",
                    )

            case PartStartEvent(
                part=NativeToolReturnPart(content=out, tool_call_id=tc_id)
            ):
                tool_state = self._tool_states.get(tc_id)
                if tool_state and tool_state.started:
                    converted = to_acp_content_blocks(out)
                    content_items = [
                        ContentToolCallContent(content=block)
                        for block in converted
                    ]
                    yield ToolCallUpdate(
                        tool_call_id=tc_id,
                        status="completed",
                        raw_output=out,
                        content=content_items,
                    )
                else:
                    yield ToolCallUpdate(
                        tool_call_id=tc_id, status="completed", raw_output=out
                    )
                self._cleanup_tool_state(tc_id)

            case PartStartEvent(part=ToolCallPart() as part):
                tool_call_id = part.tool_call_id
                tool_input = safe_args_as_dict(part, default={})
                state = self._get_or_create_tool_state(
                    tool_call_id, part.tool_name, tool_input
                )
                if not state.started:
                    state.started = True
                    yield ToolCallUpdate(
                        tool_call_id=tool_call_id,
                        title=state.title,
                        kind=state.kind,
                        raw_input=state.raw_input,
                        status="pending",
                    )

            case PartStartEvent(part=part):
                logger.debug(
                    "Received unhandled PartStartEvent", part=part
                )

            case PartDeltaEvent(delta=ToolCallPartDelta() as delta):
                delta_part = delta.as_part()
                if delta_part and delta_part.tool_name:
                    tool_call_id = delta_part.tool_call_id
                    tool_name = delta_part.tool_name
                    state = self._get_or_create_tool_state(
                        tool_call_id, tool_name, {}
                    )
                    if not state.started:
                        state.started = True
                        yield ToolCallUpdate(
                            tool_call_id=tool_call_id,
                            title=state.title,
                            kind=state.kind,
                            raw_input=state.raw_input,
                            status="pending",
                        )
                    try:
                        tool_input = delta_part.args_as_dict()
                    except ValueError:
                        pass
                    else:
                        state.raw_input = tool_input
                        state.title = generate_tool_title(
                            tool_name, tool_input
                        )
                        yield ToolCallUpdate(
                            tool_call_id=tool_call_id,
                            title=state.title,
                            raw_input=tool_input,
                            status="in_progress",
                        )

            case FunctionToolCallEvent(part=part):
                tool_call_id = part.tool_call_id
                tool_input = safe_args_as_dict(part, default={})
                state = self._get_or_create_tool_state(
                    tool_call_id, part.tool_name, tool_input
                )
                if not state.started:
                    state.started = True
                    yield ToolCallUpdate(
                        tool_call_id=tool_call_id,
                        title=state.title,
                        kind=state.kind,
                        raw_input=state.raw_input,
                        status="pending",
                    )
                elif state.raw_input != tool_input:
                    state.raw_input = tool_input
                    state.title = generate_tool_title(
                        part.tool_name, tool_input
                    )
                    yield ToolCallUpdate(
                        tool_call_id=tool_call_id,
                        title=state.title,
                        raw_input=tool_input,
                        status="in_progress",
                    )

            case FunctionToolResultEvent(
                result=ToolReturnPart(content=out), tool_call_id=tc_id
            ):
                converted = to_acp_content_blocks(out)
                content_items = [
                    ContentToolCallContent(content=block)
                    for block in converted
                ]
                yield ToolCallUpdate(
                    tool_call_id=tc_id,
                    status="completed",
                    raw_output=out,
                    content=content_items,
                )
                self._cleanup_tool_state(tc_id)

            case FunctionToolResultEvent(
                result=RetryPromptPart() as result, tool_call_id=tc_id
            ):
                error_message = result.model_response()
                content = ContentToolCallContent.text(f"Error: {error_message}")
                yield ToolCallUpdate(
                    tool_call_id=tc_id,
                    status="failed",
                    content=[content],
                )
                self._cleanup_tool_state(tc_id)

            case ToolCallStartEvent(
                tool_call_id=tc_id,
                tool_name=tool_name,
                title=title,
                kind=kind,
                locations=loc_items,
                raw_input=raw_input,
            ):
                state = self._get_or_create_tool_state(
                    tc_id, tool_name, raw_input or {}
                )
                acp_locations = [
                    ToolCallLocation(path=i.path, line=i.line)
                    for i in loc_items
                ]
                if not state.started:
                    state.started = True
                    yield ToolCallUpdate(
                        tool_call_id=tc_id,
                        title=title,
                        kind=kind,
                        raw_input=raw_input,
                        locations=acp_locations or _UNSET,
                        status="pending",
                    )
                else:
                    yield ToolCallUpdate(
                        tool_call_id=tc_id,
                        title=title,
                        kind=kind,
                        locations=acp_locations or _UNSET,
                    )

            case ToolCallProgressEvent(
                tool_call_id=tool_call_id,
                title=title,
                status=status,
            ) if tool_call_id:
                state = self._get_or_create_tool_state(
                    tool_call_id, "unknown", {}
                )
                if not state.started:
                    state.started = True
                    yield ToolCallUpdate(
                        tool_call_id=tool_call_id,
                        title=title or state.title,
                        kind=state.kind,
                        status="pending",
                    )
                yield ToolCallUpdate(
                    tool_call_id=tool_call_id,
                    title=title if title else _UNSET,
                    status=status if status else _UNSET,
                )

            case PlanUpdateEvent(entries=entries):
                yield PlanUpdate(
                    plan=PlanItems(
                        id="main",
                        entries=[
                            type(entries[0])(
                                content=e.content,
                                priority=e.priority,
                                status=e.status,
                            )
                            for e in entries
                        ]
                        if entries
                        else [],
                    )
                )

            case StreamCompleteEvent(message=msg):
                stop_reason = getattr(msg, "stop_reason", "end_turn")
                if isinstance(stop_reason, str):
                    pass
                else:
                    stop_reason = "end_turn"
                yield StateUpdate(state="idle", stop_reason=stop_reason)

            case RunErrorEvent() | RunFailedEvent():
                yield StateUpdate(state="idle", stop_reason="refusal")

            case FinalResultEvent():
                pass

            case SpawnSessionStart():
                pass

            case _:
                logger.debug(
                    "Unhandled event", event_type=type(event).__name__
                )
