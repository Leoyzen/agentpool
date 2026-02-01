"""Convert agent stream events to ACP notifications.

This module provides a stateful converter that transforms agent stream events
into ACP session update objects. The converter tracks tool call state but does
not perform any I/O - it yields notification objects that can be emitted
by the caller.

This separation enables easy testing without mocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import TYPE_CHECKING, Any, Literal, assert_never
import uuid

from pydantic_ai import (
    BuiltinToolCallPart,
    BuiltinToolReturnPart,
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPartDelta,
    ToolReturnPart,
)
from pydantic_ai.messages import BuiltinToolCallEvent, BuiltinToolResultEvent

from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    ContentToolCallContent,
    Cost,
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
    Usage,
    UsageUpdate,
)
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    ContentToolCallContent,
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
)
from acp.utils import generate_tool_title, infer_tool_kind, to_acp_content_blocks
from agentpool.agents.events import (
    CompactionEvent,
    CustomEvent,
    DiffContentItem,
    FileContentItem,
    LocationContentItem,
    PlanUpdateEvent,
    RunErrorEvent,
    RunStartedEvent,
    StreamCompleteEvent,
    SubAgentEvent,
    TerminalContentItem,
    TextContentItem,
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
    ToolResultMetadataEvent,
)
from agentpool.log import get_logger
from agentpool.utils.pydantic_ai_helpers import safe_args_as_dict


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from acp.schema.tool_call import ToolCallContent, ToolCallKind
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.tools.base import ToolKind

logger = get_logger(__name__)


# Type alias for all session updates the converter can yield
ACPSessionUpdate = (
    AgentMessageChunk
    | AgentThoughtChunk
    | ToolCallStart
    | ToolCallProgress
    | AgentPlanUpdate
    | UsageUpdate
)
ACPSessionUpdate = (
    AgentMessageChunk | AgentThoughtChunk | ToolCallStart | ToolCallProgress | AgentPlanUpdate
)


# ============================================================================
# Helper functions
# ============================================================================


def _get_display_mode() -> Literal["legacy", "inline", "tool_box"]:
    """Get the subagent display mode from environment variable.

    Reads from ACP_SUBAGENT_DISPLAY_MODE env var, defaults to "legacy".

    Returns:
        Display mode value: "legacy", "inline", or "tool_box"
    """
    mode = os.getenv("ACP_SUBAGENT_DISPLAY_MODE", "legacy")
    if mode not in ("legacy", "inline", "tool_box"):
        return "legacy"
    return mode  # type: ignore[return-value]


def get_compaction_text(trigger: str) -> str:
    if trigger == "auto":
        return "\n\n---\n\n📦 **Context compaction** triggered. Summarizing...\n\n---\n\n"
    return "\n\n---\n\n📦 **Manual compaction** requested. Summarizing...\n\n---\n\n"


@dataclass
class _ToolState:
    """Internal state for a single tool call."""

    tool_call_id: str
    tool_name: str
    title: str
    kind: ToolCallKind
    raw_input: dict[str, Any]
    started: bool = False
    has_content: bool = False


@dataclass
class _SubagentInlineState:
    """State for inline subagent display mode.

    Tracks active tool call IDs and accumulated content for text output and thinking.
    """

    source_name: str
    depth: int
    text_output_call_id: str | None = None
    thinking_call_id: str | None = None
    text_content: list[str] = field(default_factory=list)
    thinking_content: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=lambda: __import__("time").time())


@dataclass
class _SubagentToolBoxState:
    """State for tool_box subagent display mode.

    Tracks header status and content accumulation for subagent display.
    """

    source_name: str
    depth: int
    invocation_id: str
    header_sent: bool = False
    content: list[str] = field(default_factory=list)
    title: str | None = None
    created_at: float = field(default_factory=lambda: __import__("time").time())


# ============================================================================
# Event Converter
# ============================================================================


@dataclass
class ACPEventConverter:
    """Converts agent stream events to ACP session updates.

    Stateful converter that tracks tool calls and subagent content,
    yielding ACP schema objects without performing I/O.

    Example:
        ```python
        converter = ACPEventConverter()
        async for event in agent.run_stream(...):
            async for update in converter.convert(event):
                await client.session_update(SessionNotification(session_id=sid, update=update))
        ```
    """

    # Feature flag for subagent display mode
    # Reads from ACP_SUBAGENT_DISPLAY_MODE env var, defaults to "legacy" for backward compatibility
    _display_mode: Literal["legacy", "inline", "tool_box"] = field(
        default_factory=_get_display_mode,
    )

    # Legacy mode fields (deprecated)
    subagent_display_mode: Literal["legacy", "inline", "tool_box"] = "legacy"
    """How to display subagent output. Deprecated: Use ACP_SUBAGENT_DISPLAY_MODE env var instead."""

    # Internal state
    _tool_states: dict[str, _ToolState] = field(default_factory=dict)
    """Active tool call states."""

    _current_tool_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Current tool inputs by tool_call_id."""

    _subagent_headers: set[str] = field(default_factory=set)
    """Track which subagent headers have been sent (for inline mode)."""

    _subagent_content: dict[str, list[str]] = field(default_factory=dict)
    """Accumulated content per subagent (for tool_box mode)."""

    _current_message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """Message ID for the current agent response."""

    last_usage: Usage | None = field(default=None, init=False)
    """Usage from the last completed stream, if available."""
    """Accumulated content per subagent (for tool_box mode)."""

    _current_message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """Message ID for the current agent response."""
    """Accumulated content per subagent (for tool_box mode)."""

    # New state management
    _subagent_inline_states: dict[str, _SubagentInlineState] = field(default_factory=dict)
    """Inline subagent states keyed by composite key."""

    _subagent_toolbox_states: dict[str, _SubagentToolBoxState] = field(default_factory=dict)
    """Tool_box subagent states keyed by composite key."""

    MAX_STATES: int = 100
    """Maximum number of subagent states to prevent DoS attacks."""

    STATE_TTL: float = 3600.0
    """Time-to-live for subagent states in seconds (1 hour)."""

    def __post_init__(self) -> None:
        """Reconcile _display_mode with subagent_display_mode if env var not set.

        The ACP_SUBAGENT_DISPLAY_MODE environment variable takes precedence.
        If not set, use the deprecated subagent_display_mode parameter.
        """
        if "ACP_SUBAGENT_DISPLAY_MODE" not in os.environ:
            self._display_mode = self.subagent_display_mode

    def reset(self) -> None:
        """Reset converter state for a new run."""
        self._tool_states.clear()
        self._current_tool_inputs.clear()
        self._subagent_headers.clear()
        self._subagent_content.clear()
        self._subagent_inline_states.clear()
        self._subagent_toolbox_states.clear()
        self._current_message_id = str(uuid.uuid4())
        self.last_usage = None
        """Reset converter state for a new run."""
        self._tool_states.clear()
        self._current_tool_inputs.clear()
        self._subagent_headers.clear()
        self._subagent_content.clear()
        self._subagent_inline_states.clear()
        self._subagent_toolbox_states.clear()

    async def cancel_pending_tools(self) -> AsyncIterator[ToolCallProgress]:
        """Cancel all pending tool calls.

        Yields ToolCallProgress notifications with status="completed" for all
        tool calls that were started but not completed. This should be called
        when the stream is interrupted to properly clean up client-side state.

        Note:
            Uses status="completed" since ACP doesn't have a "cancelled" status.
            This signals to the client that we're done with these tool calls.

        Yields:
            ToolCallProgress notifications for each pending tool call
        """
        for tool_call_id, state in list(self._tool_states.items()):
            if state.started:
                yield ToolCallProgress(tool_call_id=tool_call_id, status="completed")
        # Clean up all state
        self.reset()

    def _get_or_create_tool_state(
        self,
        tool_call_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> _ToolState:
        """Get existing tool state or create a new one."""
        if tool_call_id not in self._tool_states:
            self._tool_states[tool_call_id] = _ToolState(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                title=generate_tool_title(tool_name, tool_input),
                kind=infer_tool_kind(tool_name),
                raw_input=tool_input,
            )
        return self._tool_states[tool_call_id]

    def _cleanup_tool_state(self, tool_call_id: str) -> None:
        """Remove tool state after completion."""
        self._tool_states.pop(tool_call_id, None)
        self._current_tool_inputs.pop(tool_call_id, None)

    def _generate_composite_key(self, source_name: str, depth: int) -> str:
        """Generate composite key for subagent state.

        Args:
            source_name: Name of the subagent source
            depth: Nesting depth of the subagent call

        Returns:
            Composite key string in format "source_name:depth"
        """
        return f"{source_name}:{depth}"

    def _cleanup_expired_states(self) -> None:
        """Clean up expired states based on TTL to prevent memory leaks."""
        import time

        current_time = time.time()
        cutoff_time = current_time - self.STATE_TTL

        # Clean inline states
        self._subagent_inline_states = {
            key: state
            for key, state in self._subagent_inline_states.items()
            if state.created_at > cutoff_time
        }

        # Clean tool_box states
        self._subagent_toolbox_states = {
            key: state
            for key, state in self._subagent_toolbox_states.items()
            if state.created_at > cutoff_time
        }

    def _get_or_create_inline_state(self, source_name: str, depth: int) -> _SubagentInlineState:
        """Get existing inline state or create a new one.

        Args:
            source_name: Name of the subagent source
            depth: Nesting depth of the subagent call

        Returns:
            _SubagentInlineState instance

        Raises:
            RuntimeError: If maximum number of states exceeded (DoS protection)
        """
        # Clean up expired states first
        self._cleanup_expired_states()

        # Create composite key (using only source_name and depth)
        key = self._generate_composite_key(source_name, depth)

        # Return existing state if found (preserves invocation_id)
        if key in self._subagent_inline_states:
            return self._subagent_inline_states[key]

        # Enforce MAX_STATES limit
        if len(self._subagent_inline_states) >= self.MAX_STATES:
            raise RuntimeError(
                f"Maximum subagent states ({self.MAX_STATES}) exceeded. "
                "This may indicate a DoS attack or memory leak."
            )

        # Create new state
        new_state = _SubagentInlineState(
            source_name=source_name,
            depth=depth,
        )
        self._subagent_inline_states[key] = new_state
        return new_state

    def _get_or_create_toolbox_state(self, source_name: str, depth: int) -> _SubagentToolBoxState:
        """Get existing toolbox state or create a new one.

        Args:
            source_name: Name of the subagent source
            depth: Nesting depth of the subagent call

        Returns:
            _SubagentToolBoxState instance

        Raises:
            RuntimeError: If maximum number of states exceeded (DoS protection)
        """
        # Clean up expired states first
        self._cleanup_expired_states()

        # Create composite key (using only source_name and depth)
        key = self._generate_composite_key(source_name, depth)

        # Return existing state if found (preserves invocation_id)
        if key in self._subagent_toolbox_states:
            return self._subagent_toolbox_states[key]

        # Enforce MAX_STATES limit
        if len(self._subagent_toolbox_states) >= self.MAX_STATES:
            raise RuntimeError(
                f"Maximum subagent states ({self.MAX_STATES}) exceeded. "
                "This may indicate a DoS attack or memory leak."
            )

        # Create new state with fresh invocation_id
        invocation_id = str(uuid.uuid4())
        new_state = _SubagentToolBoxState(
            source_name=source_name,
            depth=depth,
            invocation_id=invocation_id,
        )
        self._subagent_toolbox_states[key] = new_state
        return new_state

    async def convert(  # noqa: PLR0915
        self, event: RichAgentStreamEvent[Any]
    ) -> AsyncIterator[ACPSessionUpdate]:
        """Convert an agent event to zero or more ACP session updates."""
        from acp.schema import (
            FileEditToolCallContent,
            PlanEntry as ACPPlanEntry,
            TerminalToolCallContent,
        )
        from agentpool_server.acp_server.syntax_detection import format_zed_code_block

        match event:
            # Text output
            case (
                PartStartEvent(part=TextPart(content=delta))
                | PartDeltaEvent(delta=TextPartDelta(content_delta=delta))
            ):
                yield AgentMessageChunk.text(delta, message_id=self._current_message_id)

            # Thinking/reasoning
            case (
                PartStartEvent(part=ThinkingPart(content=delta))
                | PartDeltaEvent(delta=ThinkingPartDelta(content_delta=delta))
            ):
                yield AgentThoughtChunk.text(delta or "\n", message_id=self._current_message_id)

            # Builtin tool call started (e.g., WebSearchTool, CodeExecutionTool)
            case PartStartEvent(part=BuiltinToolCallPart() as part):
                tool_call_id = part.tool_call_id
                tool_input = safe_args_as_dict(part, default={})
                self._current_tool_inputs[tool_call_id] = tool_input
                state = self._get_or_create_tool_state(tool_call_id, part.tool_name, tool_input)
                if not state.started:
                    state.started = True
                    yield ToolCallStart(
                        tool_call_id=tool_call_id,
                        title=state.title,
                        kind=state.kind,
                        raw_input=state.raw_input,
                        status="pending",
                    )

            # Builtin tool completed
            case PartStartEvent(part=BuiltinToolReturnPart(content=out, tool_call_id=tc_id)):
                tool_state = self._tool_states.get(tc_id)
                if tool_state and tool_state.has_content:
                    yield ToolCallProgress(tool_call_id=tc_id, status="completed", raw_output=out)
                else:
                    converted = to_acp_content_blocks(out)
                    yield ToolCallProgress(
                        tool_call_id=tc_id,
                        status="completed",
                        raw_output=out,
                        content=[ContentToolCallContent(content=block) for block in converted],
                    )
                self._cleanup_tool_state(tc_id)

            case PartStartEvent(part=part):
                logger.debug("Received unhandled PartStartEvent", part=part)

            # Tool call streaming delta
            case PartDeltaEvent(delta=ToolCallPartDelta() as delta):
                if delta_part := delta.as_part():
                    tool_call_id = delta_part.tool_call_id
                    try:
                        tool_input = delta_part.args_as_dict()
                    except ValueError:
                        pass  # Args still streaming, not valid JSON yet
                    else:
                        self._current_tool_inputs[tool_call_id] = tool_input
                        state = self._get_or_create_tool_state(
                            tool_call_id, delta_part.tool_name, tool_input
                        )
                        if not state.started:
                            state.started = True
                            yield ToolCallStart(
                                tool_call_id=tool_call_id,
                                title=state.title,
                                kind=state.kind,
                                raw_input=state.raw_input,
                                status="pending",
                            )

            # Function tool call started
            case FunctionToolCallEvent(part=part):
                tool_call_id = part.tool_call_id
                tool_input = safe_args_as_dict(part, default={})
                self._current_tool_inputs[tool_call_id] = tool_input
                state = self._get_or_create_tool_state(tool_call_id, part.tool_name, tool_input)
                if not state.started:
                    state.started = True
                    yield ToolCallStart(
                        tool_call_id=tool_call_id,
                        title=state.title,
                        kind=state.kind,
                        raw_input=state.raw_input,
                        status="pending",
                    )

            # Tool completed successfully
            case FunctionToolResultEvent(result=ToolReturnPart(content=out), tool_call_id=tc_id):
                # Handle async generator content
                tool_state = self._tool_states.get(tc_id)
                if tool_state and tool_state.has_content:
                    yield ToolCallProgress(tool_call_id=tc_id, status="completed", raw_output=out)
                else:
                    converted = to_acp_content_blocks(out)
                    content_items = [ContentToolCallContent(content=block) for block in converted]
                    yield ToolCallProgress(
                        tool_call_id=tc_id,
                        status="completed",
                        raw_output=out,
                        content=content_items,
                    )
                self._cleanup_tool_state(tc_id)

            # Tool failed with retry
            case FunctionToolResultEvent(result=RetryPromptPart() as result, tool_call_id=tc_id):
                error_message = result.model_response()
                content = ContentToolCallContent.text(f"Error: {error_message}")
                yield ToolCallProgress(tool_call_id=tc_id, status="failed", content=[content])
                self._cleanup_tool_state(tc_id)

            # Tool emits its own start event
            case ToolCallStartEvent(
                tool_call_id=tc_id,
                tool_name=tool_name,
                title=title,
                kind=kind,
                locations=loc_items,
                raw_input=raw_input,
            ):
                state = self._get_or_create_tool_state(tc_id, tool_name, raw_input or {})
                acp_locations = [ToolCallLocation(path=i.path, line=i.line) for i in loc_items]
                # If not started, send start notification
                if not state.started:
                    state.started = True
                    yield ToolCallStart(
                        tool_call_id=tc_id,
                        title=title,
                        kind=kind,
                        raw_input=raw_input,
                        locations=acp_locations or None,
                        status="pending",
                    )
                else:
                    # Send update with tool-provided details
                    yield ToolCallProgress(
                        tool_call_id=tc_id,
                        title=title,
                        kind=kind,
                        locations=acp_locations or None,
                    )

            # Tool progress event - create state if needed (tool may emit progress before SDK event)
            case ToolCallProgressEvent(
                tool_call_id=tool_call_id,
                title=title,
                # status=status,
                items=items,
                progress=progress,
                total=total,
                message=message,
            ) if tool_call_id:
                # Get or create state - handles race where tool emits before SDK event
                state = self._get_or_create_tool_state(tool_call_id, "unknown", {})
                # Emit start if this is the first event for this tool call
                if not state.started:
                    state.started = True
                    yield ToolCallStart(
                        tool_call_id=tool_call_id,
                        title=title or state.title,
                        kind=state.kind,
                        raw_input=state.raw_input,
                        status="pending",
                    )
                acp_content: list[ToolCallContent] = []
                locations: list[ToolCallLocation] = []
                for item in items:
                    match item:
                        case TerminalContentItem(terminal_id=tid):
                            acp_content.append(TerminalToolCallContent(terminal_id=tid))
                        case TextContentItem(text=text):
                            acp_content.append(ContentToolCallContent.text(text))
                        case FileContentItem(
                            content=file_content,
                            path=file_path,
                            start_line=start_line,
                            end_line=end_line,
                        ):
                            formatted = format_zed_code_block(
                                file_content, file_path, start_line, end_line
                            )
                            acp_content.append(ContentToolCallContent.text(formatted))
                            locations.append(ToolCallLocation(path=file_path, line=start_line or 0))
                        case DiffContentItem(path=diff_path, old_text=old, new_text=new):
                            acp_content.append(
                                FileEditToolCallContent(path=diff_path, old_text=old, new_text=new)
                            )
                            locations.append(ToolCallLocation(path=diff_path))
                            state.has_content = True
                        case LocationContentItem(path=loc_path, line=loc_line):
                            location = ToolCallLocation(path=loc_path, line=loc_line)
                            locations.append(location)

                # Build title: use provided title, or format MCP numeric progress
                effective_title = title
                if not effective_title and (progress is not None or message):
                    # MCP-style numeric progress - format into title
                    if progress is not None and total:
                        pct = int(progress / total * 100)
                        effective_title = f"{message} ({pct}%)" if message else f"Progress: {pct}%"
                    elif message:
                        effective_title = message

                # TODO: Progress events shouldn't control completion status.
                # The file_operation helper sets status="completed" on success, but that's
                # emitted mid-operation (before content display). Only FunctionToolResultEvent
                # should mark a tool as completed. For now, hardcode in_progress.
                yield ToolCallProgress(
                    tool_call_id=tool_call_id,
                    title=effective_title,
                    status="in_progress",
                    content=acp_content or None,
                    locations=locations or None,
                )
                if acp_content:
                    state.has_content = True

            case FinalResultEvent():
                pass  # No notification needed

            case StreamCompleteEvent(message=message):
                request_usage = message.usage
                if request_usage.total_tokens > 0:
                    thought = request_usage.details.get("reasoning_tokens") or None
                    self.last_usage = Usage(
                        total_tokens=request_usage.total_tokens,
                        input_tokens=request_usage.input_tokens,
                        output_tokens=request_usage.output_tokens,
                        thought_tokens=thought,
                        cached_read_tokens=request_usage.cache_read_tokens or None,
                        cached_write_tokens=request_usage.cache_write_tokens or None,
                    )
                    cost_obj: Cost | None = None
                    if message.cost_info and message.cost_info.total_cost:
                        cost_obj = Cost(
                            amount=float(message.cost_info.total_cost),
                            currency="USD",
                        )
                    yield UsageUpdate(
                        used=request_usage.total_tokens,
                        size=request_usage.total_tokens,  # best approximation
                        cost=cost_obj,
                    )
                self.reset()
                # Clean up all subagent states when stream completes
                # Prevents memory leaks by removing accumulated state
                self.reset()

            case PlanUpdateEvent(entries=entries):
                acp_entries = [
                    ACPPlanEntry(content=e.content, priority=e.priority, status=e.status)
                    for e in entries
                ]
                yield AgentPlanUpdate(entries=acp_entries)

            case CompactionEvent(trigger=trigger, phase=phase) if phase == "starting":
                text = get_compaction_text(trigger)
                yield AgentMessageChunk.text(text, message_id=self._current_message_id)

            case SubAgentEvent(
                source_name=source_name,
                source_type=source_type,
                event=inner_event,
                depth=depth,
            ):
                match self._display_mode:
                    case "inline":
                        async for update in self._convert_subagent_inline(
                            source_name, source_type, inner_event, depth
                        ):
                            yield update
                    case "tool_box":
                        async for update in self._convert_subagent_tool_box(
                            source_name, source_type, inner_event, depth
                        ):
                            yield update
                    case _:
                        async for update in self._convert_subagent_legacy(
                            source_name, source_type, inner_event, depth
                        ):
                            yield update

            case RunErrorEvent(message=message, agent_name=agent_name):
                # Display error as agent text with formatting
                agent_prefix = f"[{agent_name}] " if agent_name else ""
                error_text = f"\n\n❌ **Error**: {agent_prefix}{message}\n\n"
                yield AgentMessageChunk.text(error_text, message_id=self._current_message_id)

            case _:
                # Graceful fallback for unknown event types
                # Handles future events like ToolRequiresAuthEvent without crashing
                logger.debug("Unhandled event", event_type=type(event).__name__)

    async def _convert_subagent_inline(  # noqa: PLR0915
        self,
        source_name: str,
        _source_type: Literal["agent", "team_parallel", "team_sequential"],
        inner_event: RichAgentStreamEvent[Any],
        depth: int,
    ) -> AsyncIterator[ACPSessionUpdate]:
        """Convert subagent event to inline tool notifications (New Mode).

        Each distinct event type (text, thinking, tool calls) becomes an independent
        tool call with the subagent name prefixed to the tool name.

        PartStartEvent creates a new tool call, PartDeltaEvent accumulates content.
        Multi-turn patterns (think→output→tool_call→think) create independent tool calls.
        """
        state = self._get_or_create_inline_state(source_name, depth)

        match inner_event:
            case PartStartEvent(part=TextPart(content=delta)):
                # New text part = new tool call
                state.text_output_call_id = f"{source_name}:output:{uuid.uuid4()}"
                if delta:
                    state.text_content = [delta] if delta else []
                    full_content = "".join(state.text_content)
                else:
                    full_content = None
                yield ToolCallStart(
                    tool_call_id=state.text_output_call_id,
                    title=f"[`{source_name}`] Output",
                    kind="other",
                    status="pending",
                    content=[ContentToolCallContent.text(text=full_content)]
                    if full_content
                    else None,
                )

            case PartDeltaEvent(delta=TextPartDelta(content_delta=delta)):
                # Accumulate text content and send update
                if state.text_output_call_id and delta:
                    text_chunk: str = delta
                    state.text_content.append(text_chunk)
                    full_text = "".join(state.text_content)
                    yield ToolCallProgress(
                        tool_call_id=state.text_output_call_id,
                        status="in_progress",
                        content=[ContentToolCallContent.text(text=full_text)],
                    )

            case PartStartEvent(part=ThinkingPart(content=delta)):
                # New thinking part = new tool call
                state.thinking_call_id = f"{source_name}:think:{uuid.uuid4()}"
                state.thinking_content = [delta] if delta else []
                yield ToolCallStart(
                    tool_call_id=state.thinking_call_id,
                    title=f"[`{source_name}`] Thinking",
                    kind="think",
                    status="pending",
                )
                # Send initial progress with accumulated content
                full_text = "".join(state.thinking_content)
                yield ToolCallProgress(
                    tool_call_id=state.thinking_call_id,
                    status="in_progress",
                    content=[ContentToolCallContent.text(text=full_text)],
                )

            case PartDeltaEvent(delta=ThinkingPartDelta(content_delta=delta)):
                # Accumulate thinking content and send update
                if state.thinking_call_id and delta:
                    thinking_chunk: str = delta
                    state.thinking_content.append(thinking_chunk)
                    full_text = "".join(state.thinking_content)
                    yield ToolCallProgress(
                        tool_call_id=state.thinking_call_id,
                        status="in_progress",
                        content=[ContentToolCallContent.text(text=full_text)],
                    )

            case FunctionToolCallEvent(part=part):
                # Each tool call is independent with prefixed name
                prefixed_tool_name = f"{source_name}:{part.tool_name}"
                tool_call_id = f"{prefixed_tool_name}:{part.tool_call_id}"
                tool_input = safe_args_as_dict(part, default={})
                title = generate_tool_title(prefixed_tool_name, tool_input)
                kind = infer_tool_kind(prefixed_tool_name)

                yield ToolCallStart(
                    tool_call_id=tool_call_id,
                    title=f"[`{source_name}`]: {title}",
                    kind=kind,
                    raw_input=tool_input,
                    status="pending",
                )

            case FunctionToolResultEvent(
                result=ToolReturnPart() as result,
                tool_call_id=original_id,
            ):
                # Complete tool call with prefixed name
                prefixed_tool_name = f"{source_name}:{result.tool_name}"
                tool_call_id = f"{prefixed_tool_name}:{original_id}"

                # Handle async generator content (same as main converter)
                if isinstance(result.content, AsyncGenerator):
                    full_content = ""
                    async for chunk in result.content:
                        full_content += str(chunk)
                        yield ToolCallProgress(
                            tool_call_id=tool_call_id,
                            status="in_progress",
                            raw_output=chunk,
                        )
                    result.content = full_content
                    final_output = full_content
                else:
                    final_output = str(result.content)

                # Convert to content blocks and send completion
                converted = to_acp_content_blocks(final_output)
                content_items = [ContentToolCallContent(content=block) for block in converted]
                yield ToolCallProgress(
                    tool_call_id=tool_call_id,
                    status="completed",
                    raw_output=final_output,
                    content=content_items,
                )

            case FunctionToolResultEvent(
                result=RetryPromptPart(tool_name=tool_name) as result,
                tool_call_id=original_id,
            ):
                # Mark tool call as failed with prefixed name
                prefixed_tool_name = f"{source_name}:{tool_name}"
                tool_call_id = f"{prefixed_tool_name}:{original_id}"

                error_msg = result.model_response()
                yield ToolCallProgress(
                    tool_call_id=tool_call_id,
                    status="failed",
                    raw_output=error_msg,
                    content=[ContentToolCallContent.text(text=f"Error: {error_msg}")],
                )

            case StreamCompleteEvent():
                # Complete any pending text or thinking tool calls
                if state.text_output_call_id:
                    yield ToolCallProgress(
                        tool_call_id=state.text_output_call_id,
                        status="completed",
                    )
                if state.thinking_call_id:
                    yield ToolCallProgress(
                        tool_call_id=state.thinking_call_id,
                        status="completed",
                    )
                # Clean up any state that was created
                key = self._generate_composite_key(source_name, depth)
                self._subagent_inline_states.pop(key, None)

            case _:
                pass

    async def _convert_subagent_legacy(
        self,
        source_name: str,
        source_type: Literal["agent", "team_parallel", "team_sequential"],
        inner_event: RichAgentStreamEvent[Any],
        depth: int,
    ) -> AsyncIterator[ACPSessionUpdate]:
        """Convert subagent event to legacy inline text notifications."""
        indent = "  " * depth
        icon = "🤖" if source_type == "agent" else "👥"

        match inner_event:
            case (
                PartStartEvent(part=TextPart(content=delta))
                | PartDeltaEvent(delta=TextPartDelta(content_delta=delta))
            ):
                header_key = f"`{source_name}`:{depth}"
                if header_key not in self._subagent_headers:
                    self._subagent_headers.add(header_key)
                    yield AgentMessageChunk.text(
                        f"\n{indent}{icon} **{source_name}**: ", message_id=self._current_message_id
                    )
                yield AgentMessageChunk.text(delta, message_id=self._current_message_id)

            case (
                PartStartEvent(part=ThinkingPart(content=delta))
                | PartDeltaEvent(delta=ThinkingPartDelta(content_delta=delta))
            ):
                yield AgentThoughtChunk.text(delta or "", message_id=self._current_message_id)

            case FunctionToolCallEvent(part=part):
                text = f"\n{indent}- 🔧 [`{source_name}`] Using tool: ``{part.tool_name}``\n"
                yield AgentMessageChunk.text(text=text, message_id=self._current_message_id)

            case FunctionToolResultEvent(
                result=ToolReturnPart(content=content, tool_name=tool_name),
            ):
                result_str = str(content)
                if len(result_str) > 200:  # noqa: PLR2004
                    result_str = result_str[:200] + "..."
                text = f"{indent}- ✅ [`{source_name}`] `{tool_name}`\n"
                yield AgentMessageChunk.text(text=text, message_id=self._current_message_id)

            case FunctionToolResultEvent(result=RetryPromptPart(tool_name=tool_name) as result):
                error_msg = result.model_response()
                text = f"{indent}- ❌ [`{source_name}`] `{tool_name}`: `{error_msg}`\n"
                yield AgentMessageChunk.text(text=text, message_id=self._current_message_id)

            case StreamCompleteEvent():
                header_key = f"`{source_name}`:{depth}"
                self._subagent_headers.discard(header_key)
                yield AgentMessageChunk.text(f"\n{indent}---\n", message_id=self._current_message_id)

            case (
                BuiltinToolCallEvent()  # depracated
                | BuiltinToolResultEvent()  # depracated
                | CompactionEvent()
                | FinalResultEvent()
                | FunctionToolResultEvent()
                | PartDeltaEvent()
                | PartEndEvent()
                | PartStartEvent()
                | PlanUpdateEvent()
                | RunErrorEvent()
                | RunStartedEvent()
                | SubAgentEvent()
                | ToolCallCompleteEvent()
                | ToolCallProgressEvent()
                | ToolCallStartEvent()
                | ToolResultMetadataEvent()
                | CustomEvent()
            ):
                pass  # TODO

            case _ as unreachable:
                assert_never(unreachable)

    async def _convert_subagent_tool_box(  # noqa: PLR0915
        self,
        source_name: str,
        source_type: Literal["agent", "team_parallel", "team_sequential"],
        inner_event: RichAgentStreamEvent[Any],
        depth: int,
    ) -> AsyncIterator[ACPSessionUpdate]:
        """Convert subagent event to tool box notifications.

        Uses _SubagentToolBoxState to track header status and accumulates content
        for full transcript in the content field.
        """
        state = self._get_or_create_toolbox_state(source_name, depth)
        tool_call_id = state.invocation_id
        icon = "🤖" if source_type == "agent" else "👥"

        if not state.header_sent:
            state.header_sent = True
            initial_title = f"{icon} [`{source_name}`]: {source_type} start"
            state.title = initial_title
            yield ToolCallStart(
                tool_call_id=tool_call_id,
                title=initial_title,
                kind="other",
                raw_input={},
                status="pending",
            )

        new_title: str | None = None
        kind: ToolKind = "other"
        current_status: Literal["in_progress", "completed"] = "in_progress"

        match inner_event:
            case PartStartEvent(part=TextPart(content=delta)):
                tool_text = "\n" + delta
                state.content.append(tool_text)
                new_title = f"{icon} [`{source_name}`]: Output..."
                kind = "other"

            case PartDeltaEvent(delta=TextPartDelta(content_delta=delta)):
                if delta:
                    state.content.append(delta)
                    new_title = f"{icon} [`{source_name}`]: Output..."
                    kind = "other"

            case PartStartEvent(part=ThinkingPart(content=delta)):
                tool_text = "\n> **Thinking** :"
                if delta:
                    tool_text += delta.replace("\n", "\n> ")
                state.content.append(tool_text)
                new_title = f"💭 [`{source_name}`]: thinking..."
                kind = "think"

            case PartDeltaEvent(delta=ThinkingPartDelta(content_delta=delta)):
                if delta:
                    state.content.append(delta.replace("\n", "\n> "))
                    new_title = f"💭 [`{source_name}`]: thinking..."
                    kind = "think"

            case FunctionToolCallEvent(part=part):
                tool_text = f"\n- calling `{part.tool_name}`"
                state.content.append(tool_text)
                new_title = f"🔧 [`{source_name}`]: calling `{part.tool_name}`..."
                kind = "other"

            case FunctionToolResultEvent(
                result=ToolReturnPart(tool_name=tool_name),
            ):
                tool_text = f"\n- `{tool_name}` completed"
                state.content.append(tool_text)
                new_title = f"✅ [`{source_name}`]: `{tool_name}` completed"
                kind = "other"

            case FunctionToolResultEvent(result=RetryPromptPart(tool_name=tool_name) as result):
                error_msg = result.model_response()
                error_text = f"\n- `{tool_name}` failed: `{error_msg}`"
                state.content.append(error_text)
                new_title = f"❌ [`{source_name}`]: `{tool_name}` failed"
                kind = "other"

            case StreamCompleteEvent():
                # Complete the tool call
                if tool_call_id in self._tool_states:
                    yield ToolCallProgress(tool_call_id=tool_call_id, status="completed")
                    self._cleanup_tool_state(tool_call_id)
                self._subagent_content.pop(tool_call_id, None)

            case _:
                pass

        if new_title and (new_title != state.title or kind == "think"):
            state.title = new_title
            full_text = "".join(state.content)
            yield ToolCallProgress(
                tool_call_id=tool_call_id,
                title=new_title,
                kind=kind,
                status=current_status,
                content=[ContentToolCallContent.text(text=full_text)],
            )
