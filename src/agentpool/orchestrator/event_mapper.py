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
from pydantic_ai.messages import ThinkingPart, ThinkingPartDelta

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
                    return _normalize_thinking_event(
                        PartDeltaEvent(
                            index=event.index, delta=event.delta, message_id=self._message_id
                        )
                    )
                if isinstance(event, PyAIPartStartEvent) and not isinstance(event, PartStartEvent):
                    return _normalize_thinking_event(
                        PartStartEvent(
                            index=event.index, part=event.part, message_id=self._message_id
                        )
                    )
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


def _extract_raw_content_text(
    provider_details: Any,
) -> str | None:
    """Extract reasoning text from provider_details['raw_content'].

    For raw CoT providers (vLLM, LM Studio, litellm), pydantic-ai stores
    reasoning text in ``provider_details['raw_content']`` instead of
    ``ThinkingPart.content``.  This function extracts the latest delta
    text from either a dict or a callable provider_details.

    Args:
        provider_details: A dict, a callable, or None.

    Returns:
        The extracted text, or None if no text could be extracted.
    """
    resolved: dict[str, Any] | None = None
    if callable(provider_details):
        try:
            result = provider_details(None)
        except Exception:  # noqa: BLE001
            return None
        if isinstance(result, dict):
            resolved = result
    elif isinstance(provider_details, dict):
        resolved = provider_details

    if resolved is None:
        return None

    raw = resolved.get("raw_content")
    if not raw or not isinstance(raw, list):
        return None
    text = raw[-1]
    if not text or not isinstance(text, str):
        return None
    return text


def _normalize_thinking_event(
    event: PartStartEvent | PartDeltaEvent,
) -> PartStartEvent | PartDeltaEvent:
    """Normalize ThinkingPart/ThinkingPartDelta events from raw CoT providers.

    When ``content``/``content_delta`` is empty/None and
    ``provider_details`` contains ``raw_content``, populate
    ``content``/``content_delta`` from the raw reasoning text so that
    protocol converters can read it directly.

    This handles pydantic-ai's by-design behavior where raw CoT providers
    (vLLM, LM Studio, litellm bridge, gpt-oss via OpenRouter) store
    reasoning in ``provider_details['raw_content']`` instead of
    ``ThinkingPart.content``.

    Events with populated ``content``/``content_delta`` are returned
    unchanged.
    """
    match event:
        case PartStartEvent(part=ThinkingPart(content="") as part):
            text = _extract_raw_content_text(part.provider_details)
            if text:
                new_part = dataclasses.replace(part, content=text)
                return dataclasses.replace(event, part=new_part)
        case PartDeltaEvent(delta=ThinkingPartDelta(content_delta=None) as delta):
            text = _extract_raw_content_text(delta.provider_details)
            if text:
                new_delta = dataclasses.replace(delta, content_delta=text)
                return dataclasses.replace(event, delta=new_delta)
    return event


def normalize_thinking_parts_in_messages(
    messages: list[Any],
) -> None:
    """Normalize ``ThinkingPart`` instances in a message list in-place.

    After streaming completes, pydantic-ai's ``StreamedResponse.get()`` builds
    a final ``ModelResponse`` from ``_parts_manager.get_parts()``.  For raw
    CoT providers (vLLM, LM Studio, litellm bridge, gpt-oss via OpenRouter),
    the resulting ``ThinkingPart`` has ``content=""`` while the actual
    reasoning text lives in ``provider_details['raw_content']``.

    The streaming ``_normalize_thinking_event`` fixes individual stream events
    but never touches the assembled ``ModelResponse`` stored in
    ``message_history``.  This function walks every ``ModelResponse`` in the
    list and copies ``raw_content`` text into ``content`` for each affected
    ``ThinkingPart``, ensuring downstream consumers (OTel, next-round
    requests, protocol converters) see the reasoning text.

    This is idempotent: parts with non-empty ``content`` are left unchanged,
    and parts without ``raw_content`` are skipped.

    Args:
        messages: A list of pydantic-ai messages (e.g. from
            ``agent_run.all_messages()``).  Only ``ModelResponse`` messages
            with ``ThinkingPart`` instances are affected; other messages
            pass through untouched.
    """
    from pydantic_ai.messages import ModelResponse

    for idx, msg in enumerate(messages):
        if not isinstance(msg, ModelResponse):
            continue
        new_parts: list[Any] | None = None
        for i, part in enumerate(msg.parts):
            if not isinstance(part, ThinkingPart) or part.content:
                continue
            # The final ModelResponse holds ALL accumulated chunks in
            # provider_details['raw_content'] (unlike streaming deltas where
            # each event's list holds only the latest incremental chunk), so
            # join the full list rather than taking the last entry.
            pd = part.provider_details
            resolved = pd(None) if callable(pd) else pd
            if not isinstance(resolved, dict):
                continue
            raw = resolved.get("raw_content")
            if not raw or not isinstance(raw, list):
                continue
            text = "".join(chunk for chunk in raw if isinstance(chunk, str))
            if not text:
                continue
            if new_parts is None:
                new_parts = list(msg.parts)
            new_parts[i] = dataclasses.replace(part, content=text)
        if new_parts is not None:
            messages[idx] = dataclasses.replace(msg, parts=new_parts)
