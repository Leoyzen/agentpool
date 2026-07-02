"""Event mapper for PydanticAI to AgentPool event translation.

Extracts the inline event mapping logic into a reusable,
testable class. Maps PydanticAI stream events to AgentPool
:class:`RichAgentStreamEvent` types.

Mapping rules:
    - ``FunctionToolCallEvent`` → :class:`ToolCallStartEvent`
    - ``PartStartEvent`` with ``BaseToolCallPart`` → :class:`ToolCallStartEvent`
    - ``FunctionToolResultEvent`` → :class:`ToolCallCompleteEvent`
    - pydantic-ai ``PartDeltaEvent`` → AgentPool :class:`PartDeltaEvent` subclass
    - pydantic-ai ``PartStartEvent`` (non-tool) → AgentPool :class:`PartStartEvent` subclass
    - Already-mapped :class:`RichAgentStreamEvent` instances pass through.
    - Unknown objects return ``None``.
"""

from __future__ import annotations

import dataclasses
from typing import Any, cast

from pydantic_ai import (
    BaseToolCallPart,
    BaseToolReturnPart,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent as PyAIPartDeltaEvent,
    PartStartEvent as PyAIPartStartEvent,
    RetryPromptPart,
)

from agentpool.agents.events.events import (
    PartDeltaEvent,
    PartStartEvent,
    RichAgentStreamEvent,
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)
from agentpool.tools.base import ToolKind
from agentpool.utils.pydantic_ai_helpers import safe_args_as_dict


class EventMapper:
    """Maps PydanticAI stream events to AgentPool RichAgentStreamEvent types.

    Tracks in-progress tool calls by ``tool_call_id`` so that
    :class:`FunctionToolResultEvent` can be correlated with the originating
    :class:`FunctionToolCallEvent` or :class:`PartStartEvent`.

    Attributes:
        tool_kind_map: Optional mapping of tool name to ToolKind string.
            Populate after construction to enable kind lookup.  Defaults to
            empty, in which case all tools receive ``"other"``.
    """

    def __init__(self, agent_name: str, message_id: str) -> None:
        self._agent_name = agent_name
        self._message_id = message_id
        self._pending_tool_calls: dict[str, str] = {}
        self._pending_tool_inputs: dict[str, dict[str, Any]] = {}
        self.tool_kind_map: dict[str, str] = {}

    def map_event(self, event: Any) -> RichAgentStreamEvent[Any] | None:
        """Map a stream event to a RichAgentStreamEvent.

        Args:
            event: A PydanticAI stream event or an AgentPool event.

        Returns:
            Mapped event, the original event if it is already a
            RichAgentStreamEvent, or ``None`` if the event is unrecognized.
        """
        match event:
            case FunctionToolCallEvent(part=tool_part) if isinstance(tool_part, BaseToolCallPart):
                return self._emit_tool_call_start(tool_part)
            case PyAIPartStartEvent(part=tool_part) if isinstance(tool_part, BaseToolCallPart):
                return self._emit_tool_call_start(tool_part)
            case FunctionToolResultEvent(part=tool_return):
                return self._emit_tool_call_complete(tool_return)
            case _:
                # Convert pydantic-ai events to AgentPool subclasses so
                # downstream isinstance checks (e.g. EventBus coalescing)
                # work correctly.  Without this, pydantic-ai's base
                # PartDeltaEvent / PartStartEvent bypass coalescing because
                # ``isinstance(base, subclass)`` is False.
                if isinstance(event, PyAIPartDeltaEvent) and not isinstance(event, PartDeltaEvent):
                    return PartDeltaEvent(index=event.index, delta=event.delta)
                if isinstance(event, PyAIPartStartEvent) and not isinstance(event, PartStartEvent):
                    return PartStartEvent(index=event.index, part=event.part)
                return event if self._is_rich_event(event) else None

    def _emit_tool_call_start(
        self,
        tool_part: BaseToolCallPart,
    ) -> ToolCallStartEvent | ToolCallProgressEvent | None:
        """Create a ToolCallStartEvent from a tool call part.

        Returns ``None`` if a start event was already emitted for the same
        ``tool_call_id`` and the args are identical (deduplication).

        If the ``tool_call_id`` is already tracked but the args differ
        (e.g., streaming assembled a more complete version), returns a
        :class:`ToolCallProgressEvent` with ``status="in_progress"`` and
        the updated ``tool_input``.
        """
        call_id = tool_part.tool_call_id
        if call_id in self._pending_tool_calls:
            new_input = safe_args_as_dict(tool_part, default={})
            stored_input = self._pending_tool_inputs.get(call_id, {})
            if new_input == stored_input:
                return None
            self._pending_tool_inputs[call_id] = new_input
            return ToolCallProgressEvent(
                tool_call_id=call_id,
                status="in_progress",
                tool_name=tool_part.tool_name,
                tool_input=new_input,
            )
        tool_name = tool_part.tool_name
        tool_input = safe_args_as_dict(tool_part, default={})
        self._pending_tool_calls[call_id] = tool_name
        self._pending_tool_inputs[call_id] = tool_input
        kind = cast(ToolKind, self.tool_kind_map.get(tool_name, "other"))
        return ToolCallStartEvent(
            tool_call_id=call_id,
            tool_name=tool_name,
            title=f"Executing: {tool_name}",
            kind=kind,
            raw_input=tool_input,
        )

    def _emit_tool_call_complete(
        self,
        tool_return: BaseToolReturnPart | RetryPromptPart,
    ) -> ToolCallCompleteEvent | None:
        """Create a ToolCallCompleteEvent from a tool return part.

        Returns ``None`` if no matching tool call start was seen (i.e. the
        ``tool_call_id`` is not in ``_pending_tool_calls``).

        Note:
            ``RetryPromptPart`` is not a ``BaseToolReturnPart`` but shares
            the ``tool_call_id`` and ``content`` attributes. When the part
            is a ``RetryPromptPart``, ``metadata={"is_error": True}`` is
            set so downstream consumers can distinguish failures from
            successful completions.
        """
        call_id = tool_return.tool_call_id
        tool_name = self._pending_tool_calls.pop(call_id, None)
        if tool_name is None:
            return None
        tool_input = self._pending_tool_inputs.pop(call_id, {})
        is_error = isinstance(tool_return, RetryPromptPart)
        return ToolCallCompleteEvent(
            tool_name=tool_name,
            tool_call_id=call_id,
            tool_input=tool_input,
            tool_result=tool_return.content,
            agent_name=self._agent_name,
            message_id=self._message_id,
            metadata={"is_error": True} if is_error else None,
        )

    @staticmethod
    def _is_rich_event(event: object) -> bool:
        """Check if *event* is a RichAgentStreamEvent.

        Both PydanticAI stream events and AgentPool events are dataclasses
        with an ``event_kind`` field.  This check covers both families
        without needing ``isinstance`` against the ``AgentStreamEvent``
        union (which is a ``typing.Annotated`` and cannot be used with
        ``isinstance`` at runtime).
        """
        if dataclasses.is_dataclass(event):
            return any(f.name == "event_kind" for f in dataclasses.fields(event))
        return False
