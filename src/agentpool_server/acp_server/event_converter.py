"""Convert agent stream events to ACP notifications.

This module provides a stateful converter that transforms agent stream events
into ACP session update objects. The converter tracks tool call state but does
not perform any I/O - it yields notification objects that can be emitted
by the caller.

This separation enables easy testing without mocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal
import uuid

from pydantic_ai import (
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    NativeToolCallPart,
    NativeToolReturnPart,
    PartDeltaEvent,
    PartEndEvent,
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
    TurnCompleteUpdate,
    Usage,
    UsageUpdate,
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
    SpawnSessionStart,
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

logger = get_logger(__name__)


# Type alias for all session updates the converter can yield
ACPSessionUpdate = (
    AgentMessageChunk
    | AgentThoughtChunk
    | ToolCallStart
    | ToolCallProgress
    | AgentPlanUpdate
    | UsageUpdate
    | TurnCompleteUpdate
)
ACPSessionUpdate = (
    AgentMessageChunk | AgentThoughtChunk | ToolCallStart | ToolCallProgress | AgentPlanUpdate
)


# ============================================================================
# Helper functions
# ============================================================================


def _get_display_mode() -> Literal["legacy"]:
    """Get the subagent display mode.

    Only "legacy" mode is supported. inline and tool_box modes were removed.

    Returns:
        "legacy"
    """
    return "legacy"


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

    # Subagent display mode (legacy only — inline and tool_box removed)
    _display_mode: Literal["legacy"] = field(
        default_factory=_get_display_mode,
    )

    # Deprecated: kept for backward compatibility of constructor calls
    subagent_display_mode: Literal["legacy"] = "legacy"
    """How to display subagent output. Only "legacy" is supported."""

    # Feature flag for TurnCompleteUpdate emission
    client_supports_turn_complete: bool = False
    """Whether the connected ACP client supports TurnCompleteUpdate.

    When True, the converter yields TurnCompleteUpdate on StreamCompleteEvent.
    When False (default), no TurnCompleteUpdate is emitted for backward
    compatibility with clients that do not handle the update type.
    """

    # Internal state
    _tool_states: dict[str, _ToolState] = field(default_factory=dict)
    """Active tool call states."""

    _current_tool_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Current tool inputs by tool_call_id."""

    _subagent_headers: set[str] = field(default_factory=set)
    """Track which subagent headers have been sent (for inline mode)."""

    _subagent_content: dict[str, list[str]] = field(default_factory=dict)
    """Accumulated content per subagent (for tool_box mode)."""

    _child_sessions: set[str] = field(default_factory=set)
    """Track child session IDs that have been spawned."""

    _current_message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """Message ID for the current agent response."""

    last_usage: Usage | None = field(default=None, init=False)
    """Usage from the last completed stream, if available."""

    def reset(self) -> None:
        """Reset converter state for a new run."""
        self._tool_states.clear()
        self._current_tool_inputs.clear()
        self._subagent_headers.clear()
        self._current_message_id = str(uuid.uuid4())
        self.last_usage = None
        self._subagent_content.clear()
        self._child_sessions.clear()
        self._current_message_id = str(uuid.uuid4())
        self.last_usage = None

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
            case PartStartEvent(part=NativeToolCallPart() as part):
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
            case PartStartEvent(part=NativeToolReturnPart(content=out, tool_call_id=tc_id)):
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

            # Regular tool call started (e.g., question_for_user)
            case PartStartEvent(part=ToolCallPart() as part):
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

            case PartStartEvent(part=part):
                logger.debug("Received unhandled PartStartEvent", part=part)

            # Tool call streaming delta
            case PartDeltaEvent(delta=ToolCallPartDelta() as delta):
                delta_part = delta.as_part()

                if delta_part:
                    # We have a complete tool name - this is either a new tool call
                    # or an update with tool_name present
                    tool_call_id = delta_part.tool_call_id
                    tool_name = delta_part.tool_name

                    # Create/get state with empty args initially
                    state = self._get_or_create_tool_state(tool_call_id, tool_name, {})

                    # Emit ToolCallStart immediately with pending status
                    # (per ACP spec: send pending as soon as we know the tool)
                    if not state.started:
                        state.started = True
                        yield ToolCallStart(
                            tool_call_id=tool_call_id,
                            title=state.title,
                            kind=state.kind,
                            raw_input=state.raw_input,
                            status="pending",
                        )

                    # Try to get complete args - if successful, update to in_progress
                    try:
                        tool_input = delta_part.args_as_dict()
                    except ValueError:
                        pass  # Args still streaming, not valid JSON yet
                    else:
                        self._current_tool_inputs[tool_call_id] = tool_input
                        state.raw_input = tool_input
                        # Update title since it may depend on args
                        state.title = generate_tool_title(tool_name, tool_input)
                        yield ToolCallProgress(
                            tool_call_id=tool_call_id,
                            title=state.title,
                            raw_input=tool_input,
                            status="in_progress",
                        )
                elif delta.tool_call_id:
                    # No tool_name_delta but we have tool_call_id.
                    # This could be a follow-up args update for an existing tool call.
                    # We can't parse args from delta alone, but ensure start was emitted.
                    tool_call_id = delta.tool_call_id
                    if tool_call_id in self._tool_states:
                        state = self._tool_states[tool_call_id]
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
                elif state.raw_input != tool_input:
                    # Streaming already started, update with complete args
                    state.raw_input = tool_input
                    state.title = generate_tool_title(part.tool_name, tool_input)
                    yield ToolCallProgress(
                        tool_call_id=tool_call_id,
                        title=state.title,
                        raw_input=tool_input,
                        status="in_progress",
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
                status=status,
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

                # Use the actual status from the event (failed, completed, in_progress)
                yield ToolCallProgress(
                    tool_call_id=tool_call_id,
                    title=effective_title,
                    status=status or "in_progress",
                    content=acp_content or None,
                    locations=locations or None,
                )
                if acp_content:
                    state.has_content = True

            case FinalResultEvent():
                pass  # No notification needed

            case StreamCompleteEvent(message=message):
                request_usage = message.usage
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
                # Always yield UsageUpdate on stream completion so clients
                # know the turn has ended — especially critical for inject-
                # triggered turns where no PromptResponse(stop_reason) is sent.
                yield UsageUpdate(
                    used=request_usage.total_tokens,
                    size=request_usage.total_tokens,  # best approximation
                    cost=cost_obj,
                )
                # Turn-complete signal: explicit end-of-turn barrier for clients.
                # Based on draft RFD PR #644 (not yet merged into ACP spec).
                # See: https://github.com/agentclientprotocol/agent-client-protocol/pull/644
                if self.client_supports_turn_complete:
                    yield TurnCompleteUpdate(stop_reason="end_turn")
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

            case SpawnSessionStart(
                child_session_id=child_session_id,
                source_name=source_name,
                description=description,
                spawn_mechanism=spawn_mechanism,
            ):
                icon = "⚡" if spawn_mechanism == "spawn" else "🚀"
                text = f"\n{icon} **`{source_name}`**: {description}\n"
                yield AgentMessageChunk.text(text, message_id=self._current_message_id)
                self._child_sessions.add(child_session_id)

            case SubAgentEvent(
                source_name=source_name,
                source_type=source_type,
                event=inner_event,
                depth=depth,
            ):
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
                yield AgentMessageChunk.text(
                    f"\n{indent}---\n", message_id=self._current_message_id
                )

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
                | SpawnSessionStart()
                | SubAgentEvent()
                | ToolCallCompleteEvent()
                | ToolCallProgressEvent()
                | ToolCallStartEvent()
                | ToolResultMetadataEvent()
                | CustomEvent()
            ):
                pass  # TODO

            case _:
                # Graceful fallback for unknown event types
                # Handles future events like ToolRequiresAuthEvent without crashing
                logger.debug("Unhandled event", event_type=type(inner_event).__name__)
