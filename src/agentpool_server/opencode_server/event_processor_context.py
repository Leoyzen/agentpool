"""Event processor context for OpenCode server.

Holds mutable state for event processing per session/level.
This context is designed for recursive subagent handling where each
child session gets its own child context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agentpool_server.opencode_server.models.parts import ReasoningPart, TextPart, ToolPart


if TYPE_CHECKING:
    from agentpool_server.opencode_server.models import MessageWithParts
    from agentpool_server.opencode_server.models.parts import ToolPart
    from agentpool_server.opencode_server.state import ServerState


@dataclass
class EventProcessorContext:
    """Mutable state context for the EventProcessor.

    Holds all tracking state that changes during stream processing:
    - Token and cost tracking
    - Tool call state accumulation
    - Text and reasoning accumulation
    - Subagent tool part tracking

    Contexts are created per session/level. For recursive subagent handling,
    each child session gets its own child EventProcessorContext.

    Args:
        session_id: The OpenCode session ID for this context.
        assistant_msg_id: The assistant message ID for updates.
        assistant_msg: The mutable assistant message to append parts to.
        state: The server state for session management and event routing.
        working_dir: Working directory for path context.
    """

    # Context identifier fields
    session_id: str
    assistant_msg_id: str
    assistant_msg: MessageWithParts
    state: ServerState
    working_dir: str

    # --- mutable tracking state ---

    # Text accumulation
    response_text: str = field(default="", init=False)
    text_part: TextPart | None = field(default=None, init=False)
    reasoning_part: ReasoningPart | None = field(default=None, init=False)

    # Token and cost tracking
    input_tokens: int = field(default=0, init=False)
    output_tokens: int = field(default=0, init=False)
    total_cost: float = field(default=0.0, init=False)
    stream_start_ms: int = field(default=0, init=False)

    # Tool call tracking
    tool_parts: dict[str, ToolPart] = field(default_factory=dict, init=False)
    tool_outputs: dict[str, str] = field(default_factory=dict, init=False)
    tool_inputs: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)

    # Subagent tool parts tracking (key: "depth:source_name" -> ToolPart)
    subagent_tool_parts: dict[str, ToolPart] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        from agentpool.utils.time_utils import now_ms

        self.stream_start_ms = now_ms()

    # --- public read-only accessors ---

    @property
    def text_accumulated(self) -> str:
        """Return the accumulated response text."""
        return self.response_text

    @property
    def has_text_part(self) -> bool:
        """Return True if a text part has been created."""
        return self.text_part is not None

    @property
    def has_reasoning_part(self) -> bool:
        """Return True if a reasoning part has been created."""
        return self.reasoning_part is not None

    # --- state update helpers ---

    def accumulate_text(self, delta: str) -> None:
        """Accumulate text into the response."""
        self.response_text += delta

    def set_text(self, text: str) -> None:
        """Set the response text (used for initial text)."""
        self.response_text = text

    def update_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """Update token counts."""
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def update_cost(self, total_cost: float) -> None:
        """Update the total cost."""
        self.total_cost = total_cost

    def add_tool_part(self, tool_call_id: str, tool_part: ToolPart) -> None:
        """Register a tool part for tracking.

        Args:
            tool_call_id: The unique identifier for the tool call.
            tool_part: The ToolPart to track.
        """
        self.tool_parts[tool_call_id] = tool_part

    def remove_tool_part(self, tool_call_id: str) -> ToolPart | None:
        """Remove and return a tracked tool part.

        Args:
            tool_call_id: The tool call ID to remove.

        Returns:
            The removed ToolPart or None if not found.
        """
        return self.tool_parts.pop(tool_call_id, None)

    def get_tool_part(self, tool_call_id: str) -> ToolPart | None:
        """Get a tracked tool part without removing it.

        Args:
            tool_call_id: The tool call ID to look up.

        Returns:
            The ToolPart or None if not found.
        """
        return self.tool_parts.get(tool_call_id)

    def has_tool_part(self, tool_call_id: str) -> bool:
        """Check if a tool part is being tracked.

        Args:
            tool_call_id: The tool call ID to check.

        Returns:
            True if the tool part exists in tracking.
        """
        return tool_call_id in self.tool_parts

    def set_tool_output(self, tool_call_id: str, output: str) -> None:
        """Set the accumulated output for a tool call.

        Args:
            tool_call_id: The tool call ID.
            output: The output string to set or append to.
        """
        self.tool_outputs[tool_call_id] = output

    def append_tool_output(self, tool_call_id: str, delta: str) -> None:
        """Append to the accumulated output for a tool call.

        Args:
            tool_call_id: The tool call ID.
            delta: The text to append.
        """
        current = self.tool_outputs.get(tool_call_id, "")
        self.tool_outputs[tool_call_id] = current + delta

    def get_tool_output(self, tool_call_id: str) -> str:
        """Get the accumulated output for a tool call.

        Args:
            tool_call_id: The tool call ID.

        Returns:
            The accumulated output string or empty string if not found.
        """
        return self.tool_outputs.get(tool_call_id, "")

    def set_tool_input(self, tool_call_id: str, tool_input: dict[str, Any]) -> None:
        """Set the input parameters for a tool call.

        Args:
            tool_call_id: The tool call ID.
            tool_input: The input parameters dictionary.
        """
        self.tool_inputs[tool_call_id] = tool_input

    def get_tool_input(self, tool_call_id: str) -> dict[str, Any] | None:
        """Get the input parameters for a tool call.

        Args:
            tool_call_id: The tool call ID.

        Returns:
            The input parameters dictionary or None if not found.
        """
        return self.tool_inputs.get(tool_call_id)

    def add_subagent_tool_part(self, subagent_key: str, tool_part: ToolPart) -> None:
        """Register a subagent tool part for tracking.

        Args:
            subagent_key: The composite key "depth:source_name" for the subagent.
            tool_part: The ToolPart to track.
        """
        self.subagent_tool_parts[subagent_key] = tool_part

    def get_subagent_tool_part(self, subagent_key: str) -> ToolPart | None:
        """Get a tracked subagent tool part.

        Args:
            subagent_key: The composite key "depth:source_name" for the subagent.

        Returns:
            The ToolPart or None if not found.
        """
        return self.subagent_tool_parts.get(subagent_key)

    def has_subagent_tool_part(self, subagent_key: str) -> bool:
        """Check if a subagent tool part is being tracked.

        Args:
            subagent_key: The composite key "depth:source_name" for the subagent.

        Returns:
            True if the subagent tool part exists in tracking.
        """
        return subagent_key in self.subagent_tool_parts
