"""Module providing functionality to manage and update parts of a model's streamed response."""

from __future__ import annotations as _annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic_ai._utils import generate_tool_call_id as _generate_tool_call_id
from pydantic_ai.messages import (
    ModelResponsePart,
    PartStartEvent,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolCallPartDelta,
)


if TYPE_CHECKING:
    from collections.abc import Iterator


ManagedPart = ModelResponsePart | ToolCallPartDelta
"""
A union of types that are managed by the PartsManager.
Because many vendors have streaming APIs that may produce not-fully-formed tool calls,
this includes ToolCallPartDelta's in addition to the more fully-formed ModelResponsePart's.
"""

PartT = TypeVar("PartT", bound=ManagedPart)


@dataclass
class PartsManager:
    """Manages a sequence of parts that make up a model's streamed response.

    Parts are generally added and/or updated by providing deltas,
    which are tracked by vendor-specific IDs.
    """

    _parts: list[ManagedPart] = field(default_factory=list[ManagedPart], init=False)
    """List of parts (text or tool calls) that make up the current state of the model's response."""
    provider_name: str

    def get_parts(self) -> list[ModelResponsePart]:
        """Return only model response parts that are complete (i.e., not ToolCallPartDelta's).

        Returns:
            A list of ModelResponsePart objects. ToolCallPartDelta objects are excluded.
        """
        return [p for p in self._parts if not isinstance(p, ToolCallPartDelta)]

    def handle_text_delta(
        self,
        *,
        content: str,
        part_id: str | None = None,
        provider_name: str | None = None,
        provider_details: dict[str, Any] | None = None,
    ) -> Iterator[PartStartEvent]:
        """Handle incoming text content."""
        # There is no existing text part that should be updated, so create a new one
        part = TextPart(
            content=content,
            id=part_id,
            provider_name=provider_name,
            provider_details=provider_details,
        )
        new_part_index = self._append_part(part)
        yield PartStartEvent(index=new_part_index, part=part)

    def handle_thinking_delta(
        self,
        *,
        content: str | None = None,
        part_id: str | None = None,
        signature: str | None = None,
        provider_name: str | None = None,
        provider_details: dict[str, Any] | None = None,
    ) -> Iterator[PartStartEvent]:
        """Handle incoming thinking content."""
        part = ThinkingPart(
            content=content or "",
            id=part_id,
            signature=signature,
            provider_name=provider_name,
            provider_details=provider_details,
        )
        new_part_index = self._append_part(part)
        yield PartStartEvent(index=new_part_index, part=part)

    def handle_tool_call_delta(
        self,
        *,
        tool_name: str | None = None,
        args: str | dict[str, Any] | None = None,
        tool_call_id: str | None = None,
        provider_details: dict[str, Any] | None = None,
    ) -> ToolCallPartDelta:
        """Handle or update a tool call,."""
        return ToolCallPartDelta(
            tool_name_delta=tool_name,
            args_delta=args,
            tool_call_id=tool_call_id,
            provider_name=self.provider_name,
            provider_details=provider_details,
        )

    def handle_tool_call_part(
        self,
        *,
        tool_name: str,
        args: str | dict[str, Any] | None,
        tool_call_id: str | None = None,
        part_id: str | None = None,
        provider_name: str | None = None,
        provider_details: dict[str, Any] | None = None,
    ) -> PartStartEvent:
        """Immediately create or fully-overwrite a ToolCallPart with the given information.

        This does not apply a delta; it directly sets the tool call part contents.

        Args:
            tool_name: The name of the tool being invoked.
            args: The arguments for the tool call, either as a string, a dictionary, or None.
            tool_call_id: An optional string identifier for this tool call.
            part_id: An optional identifier for this tool call part.
            provider_name: An optional provider name for the tool call part.
            provider_details: An optional dictionary of provider-specific details.

        Returns:
            ModelResponseStreamEvent: A `PartStartEvent` indicating that a new tool call part
            has been added to the manager, or replaced an existing part.
        """
        new_part = ToolCallPart(
            tool_name=tool_name,
            args=args,
            tool_call_id=tool_call_id or _generate_tool_call_id(),
            id=part_id,
            provider_name=provider_name,
            provider_details=provider_details,
        )
        new_part_index = self._append_part(new_part)
        return PartStartEvent(index=new_part_index, part=new_part)

    def _append_part(self, part: ManagedPart) -> int:
        """Append a part, return new index."""
        new_index = len(self._parts)
        self._parts.append(part)
        return new_index
