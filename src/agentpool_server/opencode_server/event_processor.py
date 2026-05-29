"""Event processor for OpenCode server.

Translates RichAgentStreamEvent objects from the agent event system
into OpenCode SSE Event objects. Uses EventProcessorContext for mutable
state, enabling stateless recursive processing.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from pydantic_ai import FunctionToolCallEvent
from pydantic_ai.messages import (
    PartDeltaEvent as PydanticPartDeltaEvent,
    PartStartEvent,
    TextPart as PydanticTextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart as PydanticToolCallPart,
)

from agentpool.agents.events import (
    FileContentItem,
    LocationContentItem,
    RunErrorEvent,
    RunStartedEvent,
    SpawnSessionStart,
    StreamCompleteEvent,
    SubAgentEvent,
    TextContentItem,
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)
from agentpool.agents.events.infer_info import derive_rich_tool_info
from agentpool.log import get_logger
from agentpool.utils import identifiers as identifier
from agentpool.utils.pydantic_ai_helpers import safe_args_as_dict
from agentpool.utils.time_utils import now_ms
from agentpool_server.opencode_server.converters import (
    _convert_params_for_ui,
    opencode_to_chat_message,
)
from agentpool_server.opencode_server.event_processor_context import (
    EventProcessorContext,
)
from agentpool_server.opencode_server.models import (
    MessagePath,
    MessageTime,
    MessageUpdatedEvent,
    MessageWithParts,
    PartDeltaEvent,
    PartUpdatedEvent,
    SessionErrorEvent,
    SessionIdleEvent,
    SessionStatusEvent,
    TimeCreated,
    TokenCache,
    Tokens,
)
from agentpool_server.opencode_server.models.parts import (
    ReasoningPart,
    StepFinishPart,
    TextPart,
    TimeStart,
    TimeStartEnd,
    TimeStartEndCompacted,
    TimeStartEndOptional,
    ToolPart,
    ToolStateCompleted,
    ToolStateError,
    ToolStateRunning,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Sequence

    from agentpool.agents.events import ToolCallContentItem
    from agentpool.agents.events.events import RichAgentStreamEvent
    from agentpool.messaging import ChatMessage
    from agentpool_server.opencode_server.models.events import Event
    from agentpool_server.opencode_server.models.parts import ToolState

logger = get_logger(__name__)

_MAX_SUBAGENT_DEPTH = 5


class EventProcessor:
    """Processes RichAgentStreamEvent objects into OpenCode SSE events.

    Stateless processor that uses EventProcessorContext for all mutable state.
    This design enables recursive processing with different contexts at different
    depths (e.g., for subagent handling).

    The processor yields OpenCode Event objects ready for broadcasting.
    """

    def __init__(self) -> None:
        """Initialize the event processor."""
        # Child contexts keyed by child_session_id for recursive subagent handling
        self._child_contexts: dict[str, EventProcessorContext] = {}

    async def process(
        self,
        event: RichAgentStreamEvent[Any],
        ctx: EventProcessorContext,
    ) -> AsyncIterator[Event]:
        """Process a single agent event and yield OpenCode SSE events.

        Args:
            event: The agent stream event to process.
            ctx: The event processor context holding mutable state.

        Yields:
            OpenCode Event objects for broadcasting.
        """
        match event:
            case PartStartEvent(part=PydanticTextPart(content=delta)):
                for e in self._process_text_start(ctx, delta):
                    yield e

            case PydanticPartDeltaEvent(delta=TextPartDelta(content_delta=delta)) if delta:
                for e in self._process_text_delta(ctx, delta):
                    yield e

            case PartStartEvent(part=ThinkingPart(content=delta)):
                for e in self._process_thinking_start(ctx, delta):
                    yield e

            case PydanticPartDeltaEvent(delta=ThinkingPartDelta(content_delta=delta)):
                for e in self._process_thinking_delta(ctx, delta):
                    yield e

            case ToolCallStartEvent(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                raw_input=raw_input,
                title=title,
            ):
                for e in self._process_tool_call_start(
                    ctx, tool_name, tool_call_id, raw_input, title
                ):
                    yield e

            case (
                FunctionToolCallEvent(part=tc_part)
                | PartStartEvent(part=PydanticToolCallPart() as tc_part)
            ) if not ctx.has_tool_part(tc_part.tool_call_id):
                for e in self._process_pydantic_tool_call(ctx, tc_part):
                    yield e
            case (
                FunctionToolCallEvent(part=tc_part)
                | PartStartEvent(part=PydanticToolCallPart() as tc_part)
            ) if ctx.has_tool_part(tc_part.tool_call_id):
                # Tool part already exists (from ToolCallStartEvent), update input if empty
                for e in self._update_tool_call_input(ctx, tc_part):
                    yield e

            case ToolCallProgressEvent(
                tool_call_id=tool_call_id,
                title=title,
                items=items,
                tool_name=tool_name,
                tool_input=event_tool_input,
            ) if tool_call_id:
                for e in self._process_tool_progress(
                    ctx, tool_call_id, title, items, tool_name, event_tool_input
                ):
                    yield e

            case ToolCallCompleteEvent(
                tool_call_id=tool_call_id,
                tool_result=result,
                metadata=event_metadata,
            ) if ctx.has_tool_part(tool_call_id):
                for e in self._process_tool_complete(ctx, tool_call_id, result, event_metadata):
                    yield e

            case StreamCompleteEvent(message=msg) if msg:
                for e in self._process_stream_complete(ctx, msg):
                    yield e

            case SubAgentEvent() as subagent_event:
                async for e in self._process_subagent_event(subagent_event, ctx):
                    yield e

            case SpawnSessionStart() as spawn_event:
                async for e in self._process_spawn_start(spawn_event, ctx):
                    yield e

            case RunStartedEvent() as run_started_event:
                yield SessionStatusEvent.create(
                    session_id=run_started_event.session_id,
                    status_type="busy",
                )

    def _process_text_start(
        self,
        ctx: EventProcessorContext,
        delta: str,
    ) -> Iterator[Event]:
        """Process the start of a text part.

        Args:
            ctx: The event processor context.
            delta: The initial text content.

        Yields:
            PartUpdatedEvent for the created text part.
        """
        ctx.set_text(delta)
        # Reset reasoning part reference when text starts (marks end of thinking phase)
        ctx.reasoning_part = None

        text_part = TextPart(
            id=identifier.ascending("part"),
            message_id=ctx.assistant_msg_id,
            session_id=ctx.session_id,
            text=delta,
        )
        ctx.text_part = text_part
        ctx.assistant_msg.parts.append(text_part)
        yield PartUpdatedEvent.create(text_part)

    def _process_text_delta(
        self,
        ctx: EventProcessorContext,
        delta: str,
    ) -> Iterator[Event]:
        """Process an incremental text delta.

        Args:
            ctx: The event processor context.
            delta: The text delta to append.

        Yields:
            PartUpdatedEvent for the updated text part.
        """
        ctx.accumulate_text(delta)
        if ctx.text_part is not None:
            updated = TextPart(
                id=ctx.text_part.id,
                message_id=ctx.assistant_msg_id,
                session_id=ctx.session_id,
                text=ctx.response_text,
            )
            ctx.assistant_msg.update_part(updated)
            ctx.text_part = updated
            yield PartDeltaEvent.create(
                session_id=ctx.session_id,
                message_id=ctx.assistant_msg_id,
                part_id=updated.id,
                delta=delta,
            )
        else:
            # No text part exists yet (no PartStartEvent received)
            # Create one now with the accumulated text
            text_part = TextPart(
                id=identifier.ascending("part"),
                message_id=ctx.assistant_msg_id,
                session_id=ctx.session_id,
                text=ctx.response_text,
            )
            ctx.text_part = text_part
            ctx.assistant_msg.parts.append(text_part)
            # Part doesn't exist on frontend yet, send full PartUpdatedEvent
            yield PartUpdatedEvent.create(text_part)

    def _process_thinking_start(
        self,
        ctx: EventProcessorContext,
        delta: str,
    ) -> Iterator[Event]:
        """Process the start of a thinking/reasoning part.

        Args:
            ctx: The event processor context.
            delta: The initial thinking content.

        Yields:
            PartUpdatedEvent for the created reasoning part.
        """
        # Skip empty reasoning content (but preserve whitespace-only like newlines)
        if not delta:
            return

        reasoning_part_id = identifier.ascending("part")
        reasoning_part = ReasoningPart(
            id=reasoning_part_id,
            message_id=ctx.assistant_msg_id,
            session_id=ctx.session_id,
            text=delta,
            time=TimeStartEndOptional(start=now_ms()),
        )
        ctx.reasoning_part = reasoning_part
        ctx.assistant_msg.parts.append(reasoning_part)
        yield PartUpdatedEvent.create(reasoning_part)

    def _process_thinking_delta(
        self,
        ctx: EventProcessorContext,
        delta: str | None,
    ) -> Iterator[Event]:
        """Process an incremental thinking delta.

        Args:
            ctx: The event processor context.
            delta: The thinking delta to append.

        Yields:
            PartUpdatedEvent for the updated or created reasoning part.
        """
        # Skip empty reasoning content (but preserve whitespace-only like newlines)
        if not delta:
            return

        if ctx.reasoning_part is not None:
            # Update existing reasoning part
            updated = ReasoningPart(
                id=ctx.reasoning_part.id,
                message_id=ctx.assistant_msg_id,
                session_id=ctx.session_id,
                text=ctx.reasoning_part.text + delta,
                time=ctx.reasoning_part.time,
            )
            ctx.assistant_msg.update_part(updated)
            ctx.reasoning_part = updated
            yield PartDeltaEvent.create(
                session_id=ctx.session_id,
                message_id=ctx.assistant_msg_id,
                part_id=updated.id,
                delta=delta,
            )
        else:
            # No reasoning part exists yet (e.g., after text reset or orphaned delta)
            # Create a new reasoning part with the delta content
            reasoning_part_id = identifier.ascending("part")
            reasoning_part = ReasoningPart(
                id=reasoning_part_id,
                message_id=ctx.assistant_msg_id,
                session_id=ctx.session_id,
                text=delta,
                time=TimeStartEndOptional(start=now_ms()),
            )
            ctx.reasoning_part = reasoning_part
            ctx.assistant_msg.parts.append(reasoning_part)
            # Part doesn't exist on frontend yet, send full PartUpdatedEvent
            yield PartUpdatedEvent.create(reasoning_part)

    def _process_tool_call_start(
        self,
        ctx: EventProcessorContext,
        tool_name: str,
        tool_call_id: str,
        raw_input: dict[str, Any] | None,
        title: str | None,
    ) -> Iterator[Event]:
        """Process the start of a tool call (rich events).

        Args:
            ctx: The event processor context.
            tool_name: The name of the tool being called.
            tool_call_id: The unique identifier for this tool call.
            raw_input: The raw input arguments for the tool.
            title: Optional display title for the tool call.

        Yields:
            PartUpdatedEvent for the created or updated tool part.
        """
        ui_input = _convert_params_for_ui(raw_input) if raw_input else {}

        if ctx.has_tool_part(tool_call_id):
            # Update existing part with the custom title
            existing = ctx.get_tool_part(tool_call_id)
            if existing is not None:
                existing_input = ctx.get_tool_input(tool_call_id) or {}
                ctx.set_tool_input(tool_call_id, ui_input or existing_input)
                tool_input = ctx.get_tool_input(tool_call_id) or {}
                running_state = ToolStateRunning(
                    time=TimeStart(start=ctx.stream_start_ms),
                    input=tool_input,
                    title=title,
                )
                updated = ToolPart(
                    id=existing.id,
                    message_id=existing.message_id,
                    session_id=existing.session_id,
                    tool=existing.tool,
                    call_id=existing.call_id,
                    state=running_state,
                )
                ctx.add_tool_part(tool_call_id, updated)
                ctx.assistant_msg.update_part(updated)
                yield PartUpdatedEvent.create(updated)
        else:
            # Create new tool part
            ctx.set_tool_input(tool_call_id, ui_input)
            ctx.set_tool_output(tool_call_id, "")
            ts = TimeStart(start=now_ms())
            tool_state = ToolStateRunning(time=ts, input=ui_input, title=title)
            tool_part = ToolPart(
                id=identifier.ascending("part"),
                message_id=ctx.assistant_msg_id,
                session_id=ctx.session_id,
                tool=tool_name,
                call_id=tool_call_id,
                state=tool_state,
            )
            ctx.add_tool_part(tool_call_id, tool_part)
            ctx.assistant_msg.parts.append(tool_part)
            yield PartUpdatedEvent.create(tool_part)

    def _process_pydantic_tool_call(
        self,
        ctx: EventProcessorContext,
        tc_part: PydanticToolCallPart,
    ) -> Iterator[Event]:
        """Process a pydantic-ai tool call event (fallback for pydantic-ai agents).

        Args:
            ctx: The event processor context.
            tc_part: The pydantic-ai tool call part.

        Yields:
            PartUpdatedEvent for the created tool part.
        """
        tool_call_id = tc_part.tool_call_id
        tool_name = tc_part.tool_name
        raw_input = safe_args_as_dict(tc_part)
        ui_input = _convert_params_for_ui(raw_input)

        ctx.set_tool_input(tool_call_id, ui_input)
        ctx.set_tool_output(tool_call_id, "")

        rich_info = derive_rich_tool_info(tool_name, raw_input)
        ts = TimeStart(start=now_ms())
        tool_state = ToolStateRunning(time=ts, input=ui_input, title=rich_info.title)
        tool_part = ToolPart(
            id=identifier.ascending("part"),
            message_id=ctx.assistant_msg_id,
            session_id=ctx.session_id,
            tool=tool_name,
            call_id=tool_call_id,
            state=tool_state,
        )
        ctx.add_tool_part(tool_call_id, tool_part)
        ctx.assistant_msg.parts.append(tool_part)
        yield PartUpdatedEvent.create(tool_part)

    def _update_tool_call_input(
        self,
        ctx: EventProcessorContext,
        tc_part: PydanticToolCallPart,
    ) -> Iterator[Event]:
        """Update existing tool part with input from pydantic ToolCallPart.

        This handles the case where ToolCallStartEvent (from ctx.events.tool_call_start())
        arrives before PartStartEvent, creating an empty tool part that needs to be
        populated with actual arguments from the pydantic event.

        Args:
            ctx: The event processor context.
            tc_part: The pydantic-ai tool call part containing args.

        Yields:
            PartUpdatedEvent if the tool part was updated with new input.
        """
        tool_call_id = tc_part.tool_call_id
        existing_input = ctx.get_tool_input(tool_call_id) or {}

        # Only update if current input is empty and we have args
        if not existing_input and tc_part.args:
            raw_input = safe_args_as_dict(tc_part)
            if raw_input:
                ui_input = _convert_params_for_ui(raw_input)
                ctx.set_tool_input(tool_call_id, ui_input)

                # Update the existing tool part with new input
                existing = ctx.get_tool_part(tool_call_id)
                if existing is not None:
                    existing_title = _extract_title_from_tool_state(existing.state)
                    tool_state = ToolStateRunning(
                        time=TimeStart(start=now_ms()),
                        input=ui_input,
                        title=existing_title or tc_part.tool_name,
                    )
                    updated = ToolPart(
                        id=existing.id,
                        message_id=existing.message_id,
                        session_id=existing.session_id,
                        tool=existing.tool,
                        call_id=existing.call_id,
                        state=tool_state,
                    )
                    ctx.add_tool_part(tool_call_id, updated)
                    ctx.assistant_msg.update_part(updated)
                    yield PartUpdatedEvent.create(updated)

    def _process_tool_progress(
        self,
        ctx: EventProcessorContext,
        tool_call_id: str,
        title: str | None,
        items: Sequence[ToolCallContentItem],
        tool_name: str | None,
        event_tool_input: dict[str, Any] | None,
    ) -> Iterator[Event]:
        """Process tool call progress updates.

        Args:
            ctx: The event processor context.
            tool_call_id: The unique identifier for this tool call.
            title: Optional display title for the tool call.
            items: Content items representing progress output.
            tool_name: Optional tool name (for new tool parts).
            event_tool_input: Optional input parameters (for new tool parts).

        Yields:
            PartUpdatedEvent for the updated or created tool part.
        """
        new_output = ""
        for item in items:
            match item:
                case TextContentItem(text=text):
                    new_output += text
                case FileContentItem(content=content):
                    new_output += content
                case LocationContentItem():
                    pass

        if new_output:
            ctx.append_tool_output(tool_call_id, new_output)

        if ctx.has_tool_part(tool_call_id):
            existing = ctx.get_tool_part(tool_call_id)
            if existing is not None:
                existing_title = _extract_title_from_tool_state(existing.state)
                tool_input = ctx.get_tool_input(tool_call_id) or {}
                accumulated_output = ctx.get_tool_output(tool_call_id)
                tool_state = ToolStateRunning(
                    time=TimeStart(start=now_ms()),
                    title=title or existing_title,
                    input=tool_input,
                    metadata={"output": accumulated_output} if accumulated_output else None,
                )
                updated = ToolPart(
                    id=existing.id,
                    message_id=existing.message_id,
                    session_id=existing.session_id,
                    tool=existing.tool,
                    call_id=existing.call_id,
                    state=tool_state,
                )
                ctx.add_tool_part(tool_call_id, updated)
                ctx.assistant_msg.update_part(updated)
                yield PartUpdatedEvent.create(updated)
        else:
            # Create new tool part from progress event
            ui_input = _convert_params_for_ui(event_tool_input) if event_tool_input else {}
            ctx.set_tool_input(tool_call_id, ui_input)
            accumulated_output = ctx.get_tool_output(tool_call_id)
            tool_state = ToolStateRunning(
                time=TimeStart(start=now_ms()),
                input=ui_input,
                title=title or tool_name or "Running...",
                metadata={"output": accumulated_output} if accumulated_output else None,
            )
            tool_part = ToolPart(
                id=identifier.ascending("part"),
                message_id=ctx.assistant_msg_id,
                session_id=ctx.session_id,
                tool=tool_name or "unknown",
                call_id=tool_call_id,
                state=tool_state,
            )
            ctx.add_tool_part(tool_call_id, tool_part)
            ctx.assistant_msg.parts.append(tool_part)
            yield PartUpdatedEvent.create(tool_part)

    def _process_tool_complete(
        self,
        ctx: EventProcessorContext,
        tool_call_id: str,
        result: Any,
        event_metadata: dict[str, Any] | None,
    ) -> Iterator[Event]:
        """Process tool call completion.

        Args:
            ctx: The event processor context.
            tool_call_id: The unique identifier for this tool call.
            result: The result of the tool execution.
            event_metadata: Optional metadata about the tool execution.

        Yields:
            PartUpdatedEvent for the completed tool part.
        """
        existing = ctx.get_tool_part(tool_call_id)
        if existing is None:
            return

        result_str = str(result) if result else ""
        tool_input = ctx.get_tool_input(tool_call_id) or {}
        is_error = isinstance(result, dict) and result.get("error")
        start = ctx.stream_start_ms

        new_state: ToolStateCompleted | ToolStateError
        if is_error:
            t = TimeStartEnd(start=start, end=now_ms())
            error_string = str(result.get("error", "Unknown error"))
            new_state = ToolStateError(error=error_string, input=tool_input, time=t)
        else:
            new_state = ToolStateCompleted(
                title=f"Completed {existing.tool}",
                input=tool_input,
                output=result_str,
                metadata=event_metadata or {},
                time=TimeStartEndCompacted(start=start, end=now_ms()),
            )

        updated = ToolPart(
            id=existing.id,
            message_id=existing.message_id,
            session_id=existing.session_id,
            tool=existing.tool,
            call_id=existing.call_id,
            state=new_state,
        )
        ctx.add_tool_part(tool_call_id, updated)
        ctx.assistant_msg.update_part(updated)
        yield PartUpdatedEvent.create(updated)

    def _process_stream_complete(
        self,
        ctx: EventProcessorContext,
        msg: ChatMessage[Any],
    ) -> Iterator[Event]:
        """Process stream completion and update token/cost tracking.

        Args:
            ctx: The event processor context.
            msg: The completed chat message with usage and cost info.

        Yields:
            Final events including text part timing update and step finish part.
        """
        # Update token and cost tracking from the message
        if msg.usage:
            ctx.update_tokens(
                msg.usage.input_tokens or 0,
                msg.usage.output_tokens or 0,
            )
        if msg.cost_info and msg.cost_info.total_cost:
            ctx.update_cost(float(msg.cost_info.total_cost))

        response_time = now_ms()
        start = ctx.stream_start_ms

        # Final text part
        if ctx.response_text and ctx.text_part is None:
            # Text was never streamed incrementally — create a text part now
            text_part = TextPart(
                id=identifier.ascending("part"),
                message_id=ctx.assistant_msg_id,
                session_id=ctx.session_id,
                text=ctx.response_text,
                time=TimeStartEndOptional(start=start, end=response_time),
            )
            ctx.assistant_msg.parts.append(text_part)
            yield PartUpdatedEvent.create(text_part)
        elif ctx.text_part is not None:
            # Update streamed text part with final timing
            final_text_part = TextPart(
                id=ctx.text_part.id,
                message_id=ctx.assistant_msg_id,
                session_id=ctx.session_id,
                text=ctx.response_text,
                time=TimeStartEndOptional(start=start, end=response_time),
            )
            ctx.assistant_msg.update_part(final_text_part)

        # Step finish part
        cache = TokenCache(read=0, write=0)
        tokens = Tokens(
            cache=cache,
            input=ctx.input_tokens,
            output=ctx.output_tokens,
            reasoning=0,
        )
        step_finish = StepFinishPart(
            id=identifier.ascending("part"),
            message_id=ctx.assistant_msg_id,
            session_id=ctx.session_id,
            tokens=tokens,
            cost=ctx.total_cost,
        )
        ctx.assistant_msg.parts.append(step_finish)
        yield PartUpdatedEvent.create(step_finish)

    async def _process_subagent_event(
        self,
        subagent_event: SubAgentEvent,
        ctx: EventProcessorContext,
    ) -> AsyncIterator[Event]:
        """Process a SubAgentEvent by recursively processing the wrapped event.

        Handles depth capping, session creation, and child context management.
        All events from subagents are routed to their child sessions.

        Args:
            subagent_event: The SubAgentEvent containing the wrapped event.
            ctx: The parent event processor context.

        Yields:
            OpenCode Event objects for broadcasting to appropriate sessions.
        """
        # 1. Check and cap depth at 5
        if subagent_event.depth >= _MAX_SUBAGENT_DEPTH:
            logger.warning(
                "Subagent recursion depth %s >= %s, processing at depth %s",
                subagent_event.depth,
                _MAX_SUBAGENT_DEPTH,
                _MAX_SUBAGENT_DEPTH,
            )
            depth = 5
        else:
            depth = subagent_event.depth

        # 2. Unwrap nested SubAgentEvent recursively
        wrapped_event: RichAgentStreamEvent[Any] = subagent_event.event
        while isinstance(wrapped_event, SubAgentEvent):
            logger.debug("Unwrapping nested SubAgentEvent")
            wrapped_event = wrapped_event.event

        source_name = subagent_event.source_name
        child_session_id = subagent_event.child_session_id

        # 3. Ensure child session exists if ID provided
        if child_session_id:
            await ctx.state.ensure_session(child_session_id, parent_id=ctx.session_id)

        # 4. Get or create child context
        child_ctx: EventProcessorContext | None = None
        if child_session_id:
            child_ctx = self._child_contexts.get(child_session_id)

        # 5. Create child context if it doesn't exist yet
        # This handles out-of-order events (e.g., PartDeltaEvent before RunStartedEvent)
        if child_session_id and child_ctx is None:
            # Import here to avoid circular imports
            from agentpool.utils import identifiers

            # Create user message in child session first (the task prompt)
            user_msg_id = identifiers.ascending("message")
            user_msg = MessageWithParts.user(
                message_id=user_msg_id,
                session_id=child_session_id,
                time=TimeCreated(created=now_ms()),
                agent_name=source_name,
            )
            user_msg.add_text_part(f"Task: {source_name}")
            ctx.state.messages[child_session_id].append(user_msg)
            yield MessageUpdatedEvent.create(user_msg.info)
            # Yield PartUpdatedEvent so the TUI's store.part[message.id] gets the
            # text part.  Without this, the UserMessage component finds no text
            # part and renders nothing for the first user message card.
            if user_msg.parts:
                yield PartUpdatedEvent.create(user_msg.parts[0])

            # Persist user message to storage
            with contextlib.suppress(Exception):
                chat_msg = opencode_to_chat_message(user_msg, session_id=child_session_id)
                await ctx.state.storage.log_message(chat_msg)

            # Now create assistant message with user_msg as parent
            child_assistant_msg_id = identifiers.ascending("message")
            child_assistant_msg = MessageWithParts.assistant(
                message_id=child_assistant_msg_id,
                session_id=child_session_id,
                time=MessageTime(created=now_ms()),
                agent_name=source_name,
                model_id=subagent_event.model_id or "subagent",
                parent_id=user_msg_id,
                provider_id="agentpool",
                path=MessagePath(cwd=ctx.working_dir, root=ctx.working_dir),
                mode=(subagent_event.mode or source_name) if source_name else "default",
            )

            child_ctx = EventProcessorContext(
                session_id=child_session_id,
                assistant_msg_id=child_assistant_msg_id,
                assistant_msg=child_assistant_msg,
                state=ctx.state,
                working_dir=ctx.working_dir,
            )
            self._child_contexts[child_session_id] = child_ctx

            # Create child session assistant message
            ctx.state.messages[child_session_id].append(child_assistant_msg)
            yield MessageUpdatedEvent.create(child_assistant_msg.info)

            # Persist assistant message to storage
            with contextlib.suppress(Exception):
                chat_msg = opencode_to_chat_message(
                    child_assistant_msg, session_id=child_session_id
                )
                await ctx.state.storage.log_message(chat_msg)

            # Create ToolPart in parent session representing the subagent
            subagent_key = f"{depth}:{source_name}:{child_session_id}"
            if not ctx.has_subagent_tool_part(subagent_key):
                ts = TimeStart(start=now_ms())
                tool_title = source_name
                running_state = ToolStateRunning(
                    time=ts,
                    input={
                        "description": tool_title,
                        "subagent_type": tool_title,
                        "prompt": "",
                    },
                    metadata={"sessionId": child_session_id, "title": tool_title},
                    title=tool_title,
                )
                tool_part = ToolPart(
                    id=identifier.ascending("part"),
                    message_id=ctx.assistant_msg_id,
                    session_id=ctx.session_id,
                    tool="task",
                    call_id=identifier.ascending("part"),
                    state=running_state,
                )
                ctx.add_subagent_tool_part(subagent_key, tool_part)
                ctx.assistant_msg.parts.append(tool_part)
                yield PartUpdatedEvent.create(tool_part)

        # 6. If still no child context, we can't process
        if child_ctx is None:
            return

        # 7. Process wrapped event in child context
        async for event in self.process(wrapped_event, child_ctx):
            yield event

        # 8. Handle RunErrorEvent - transition parent ToolPart to error state
        if isinstance(wrapped_event, RunErrorEvent):
            error_msg = wrapped_event.message or "Unknown error"
            subagent_key = f"{depth}:{source_name}:{child_session_id}"
            if ctx.has_subagent_tool_part(subagent_key):
                existing = ctx.get_subagent_tool_part(subagent_key)
                if existing is not None:
                    tool_title = source_name
                    start_time = (
                        existing.state.time.start
                        if isinstance(existing.state, ToolStateRunning)
                        else now_ms()
                    )
                    error_state = ToolStateError(
                        error=error_msg,
                        input={
                            "description": tool_title,
                            "subagent_type": tool_title,
                            "prompt": "",
                        },
                        metadata={"sessionId": child_session_id, "title": tool_title},
                        time=TimeStartEnd(start=start_time, end=now_ms()),
                    )
                    updated = ToolPart(
                        id=existing.id,
                        message_id=existing.message_id,
                        session_id=existing.session_id,
                        tool=existing.tool,
                        call_id=existing.call_id,
                        state=error_state,
                    )
                    ctx.add_subagent_tool_part(subagent_key, updated)
                    ctx.assistant_msg.update_part(updated)
                    yield PartUpdatedEvent.create(updated)

            # Emit SessionErrorEvent for the child session
            yield SessionErrorEvent.create(
                session_id=child_session_id,
                error_name=wrapped_event.code or "AgentError",
                error_message=error_msg,
            )

            # Mark the child context as errored to prevent StreamCompleteEvent
            # from overriding the error state
            child_ctx.is_errored = True

            # Persist final child assistant message to storage even on error
            with contextlib.suppress(Exception):
                chat_msg = opencode_to_chat_message(
                    child_ctx.assistant_msg, session_id=child_ctx.session_id
                )
                await ctx.state.storage.log_message(chat_msg)

        # 9. Handle StreamCompleteEvent - finalize child session and update parent
        # Skip if the subagent already errored (RunErrorEvent was processed)
        if (
            isinstance(wrapped_event, StreamCompleteEvent)
            and wrapped_event.message
            and not child_ctx.is_errored
        ):
            msg = wrapped_event.message
            content = str(msg.content) if msg.content else "(no output)"

            # Update child context with final content
            if not child_ctx.has_text_part:
                # Text was never streamed - add it now
                text_part = TextPart(
                    id=identifier.ascending("part"),
                    message_id=child_ctx.assistant_msg_id,
                    session_id=child_ctx.session_id,
                    text=content,
                    time=TimeStartEndOptional(start=child_ctx.stream_start_ms, end=now_ms()),
                )
                child_ctx.assistant_msg.parts.append(text_part)
                yield PartUpdatedEvent.create(text_part)

            # Persist final child assistant message to storage
            # This ensures all parts (text, tool calls, etc.) are saved
            with contextlib.suppress(Exception):
                chat_msg = opencode_to_chat_message(
                    child_ctx.assistant_msg, session_id=child_ctx.session_id
                )
                await ctx.state.storage.log_message(chat_msg)

            # Update the ToolPart in parent to completed state
            subagent_key = f"{depth}:{source_name}:{child_session_id}"
            if ctx.has_subagent_tool_part(subagent_key):
                existing = ctx.get_subagent_tool_part(subagent_key)
                if existing is not None:
                    tool_title = source_name
                    completed_state = ToolStateCompleted(
                        input={
                            "description": tool_title,
                            "subagent_type": tool_title,
                            "prompt": "",
                        },
                        output=content,
                        title=tool_title,
                        metadata={"sessionId": child_session_id, "title": tool_title},
                        time=TimeStartEndCompacted(start=now_ms(), end=now_ms()),
                    )
                    updated = ToolPart(
                        id=existing.id,
                        message_id=existing.message_id,
                        session_id=existing.session_id,
                        tool=existing.tool,
                        call_id=existing.call_id,
                        state=completed_state,
                    )
                    ctx.add_subagent_tool_part(subagent_key, updated)
                    ctx.assistant_msg.update_part(updated)
                    yield PartUpdatedEvent.create(updated)

            # Emit idle events for the child session so the TUI updates
            # the subagent card from "running" to "completed".
            # This mirrors what mark_session_idle() does for parent sessions
            # in message_routes.py, but here we only yield the events
            # (broadcast is handled by the caller).
            if child_session_id:
                from agentpool_server.opencode_server.models import SessionStatus

                ctx.state.session_status[child_session_id] = SessionStatus(type="idle")
                yield SessionStatusEvent.create(child_session_id, SessionStatus(type="idle"))
                yield SessionIdleEvent.create(child_session_id)

    async def _process_spawn_start(
        self,
        event: SpawnSessionStart,
        ctx: EventProcessorContext,
    ) -> AsyncIterator[Event]:
        """Process a SpawnSessionStart event for eager session creation.

        Provides duplicate session guard and eager child session creation,
        allowing SubAgentEvent processing to focus on event propagation.

        Args:
            event: The spawn session start event.
            ctx: The parent event processor context.

        Yields:
            OpenCode Event objects for broadcasting.
        """
        # Duplicate guard - skip if session already exists
        if event.child_session_id in self._child_contexts:
            logger.debug(
                "SpawnSessionStart for %s already exists, skipping",
                event.child_session_id,
            )
            return

        # Ensure child session exists
        await ctx.state.ensure_session(event.child_session_id, parent_id=ctx.session_id)

        # Import identifiers
        from agentpool.utils import identifiers

        # Create user message
        user_msg_id = identifiers.ascending("message")
        user_msg = MessageWithParts.user(
            message_id=user_msg_id,
            session_id=event.child_session_id,
            time=TimeCreated(created=now_ms()),
            agent_name=event.source_name,
        )
        # Use prompt from metadata if available, fall back to description
        text_part = user_msg.add_text_part(event.metadata.get("prompt") or event.description)
        ctx.state.messages[event.child_session_id].append(user_msg)
        yield MessageUpdatedEvent.create(user_msg.info)
        # Yield PartUpdatedEvent so the TUI's store.part[message.id] gets the
        # text part.  Without this, the UserMessage component finds no text
        # part and renders nothing for the first user message card.
        yield PartUpdatedEvent.create(text_part)

        # Persist user message to storage
        with contextlib.suppress(Exception):
            chat_msg = opencode_to_chat_message(user_msg, session_id=event.child_session_id)
            await ctx.state.storage.log_message(chat_msg)

        # Create assistant message
        child_assistant_msg_id = identifiers.ascending("message")
        child_assistant_msg = MessageWithParts.assistant(
            message_id=child_assistant_msg_id,
            session_id=event.child_session_id,
            time=MessageTime(created=now_ms()),
            agent_name=event.source_name,
            model_id=event.model_id or "subagent",
            parent_id=user_msg_id,
            provider_id="agentpool",
            path=MessagePath(cwd=ctx.working_dir, root=ctx.working_dir),
            mode=(event.mode or event.source_name) if event.source_name else "default",
        )

        child_ctx = EventProcessorContext(
            session_id=event.child_session_id,
            assistant_msg_id=child_assistant_msg_id,
            assistant_msg=child_assistant_msg,
            state=ctx.state,
            working_dir=ctx.working_dir,
        )
        self._child_contexts[event.child_session_id] = child_ctx
        ctx.state.messages[event.child_session_id].append(child_assistant_msg)
        yield MessageUpdatedEvent.create(child_assistant_msg.info)

        # Persist assistant message to storage
        with contextlib.suppress(Exception):
            chat_msg = opencode_to_chat_message(
                child_assistant_msg, session_id=event.child_session_id
            )
            await ctx.state.storage.log_message(chat_msg)

        # Create ToolPart in parent session
        subagent_key = f"{event.depth}:{event.source_name}:{event.child_session_id}"
        if not ctx.has_subagent_tool_part(subagent_key):
            ts = TimeStart(start=now_ms())
            # Extract prompt from metadata, fallback to empty string
            subagent_prompt = event.metadata.get("prompt") or ""
            # Tool title uses event.description for display
            tool_title = event.description or event.source_name
            running_state = ToolStateRunning(
                time=ts,
                input={
                    "description": tool_title,
                    "subagent_type": event.source_name,
                    "prompt": subagent_prompt,
                },
                metadata={"sessionId": event.child_session_id, "title": tool_title},
                title=tool_title,
            )
            tool_part = ToolPart(
                id=identifiers.ascending("part"),
                message_id=ctx.assistant_msg_id,
                session_id=ctx.session_id,
                tool="task",
                call_id=identifiers.ascending("part"),
                state=running_state,
            )
            ctx.add_subagent_tool_part(subagent_key, tool_part)
            ctx.assistant_msg.parts.append(tool_part)
            yield PartUpdatedEvent.create(tool_part)


def _extract_title_from_tool_state(state: ToolState) -> str:
    """Extract the title from a tool state without getattr.

    Args:
        state: The tool state to extract title from.

    Returns:
        The title string or empty string if no title available.
    """
    match state:
        case ToolStateRunning(title=title):
            return title or ""
        case ToolStateCompleted(title=title):
            return title or ""
        case ToolStateError() | _:
            return ""
