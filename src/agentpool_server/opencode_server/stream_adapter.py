"""OpenCode stream adapter.

Translates RichAgentStreamEvent objects from the agent event system
into OpenCode SSE Event objects. The adapter yields events; broadcasting
and persistence are handled by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai import FunctionToolCallEvent
from pydantic_ai.messages import (
    PartDeltaEvent,
    PartStartEvent,
    TextPart as PydanticTextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart as PydanticToolCallPart,
)

from agentpool.agents.events import (
    CompactionEvent,
    FileContentItem,
    LocationContentItem,
    RunErrorEvent,
    RunStartedEvent,
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
from agentpool_server.opencode_server.converters import _convert_params_for_ui
from agentpool_server.opencode_server.event_processor import EventProcessor
from agentpool_server.opencode_server.event_processor_context import (
    EventProcessorContext,
)
from agentpool_server.opencode_server.models import (
    MessagePath,
    MessageTime,
    MessageUpdatedEvent,
    MessageWithParts,
    PartUpdatedEvent,
    SessionErrorEvent,
    TimeCreated,
    TokenCache,
    Tokens,
)
from agentpool_server.opencode_server.models.parts import (
    StepFinishPart,
    TextPart,
    TimeStartEndOptional,
    ToolPart,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator, Sequence

    from agentpool.agents.events import ToolCallContentItem
    from agentpool.agents.events.events import RichAgentStreamEvent
    from agentpool.messaging import ChatMessage
    from agentpool_server.opencode_server.models import MessageWithParts
    from agentpool_server.opencode_server.models.events import Event
    from agentpool_server.opencode_server.models.parts import ToolState
    from agentpool_server.opencode_server.state import ServerState

logger = get_logger(__name__)


@dataclass
class OpenCodeStreamAdapter:
    """Translates agent stream events into OpenCode SSE events.

    Owns all mutable tracking state (tool parts, text accumulation, token
    counters). Yields OpenCode ``Event`` objects ready for broadcasting.

    The adapter does NOT own:
    - Broadcasting (caller does ``state.broadcast_event``)
    - Agent invocation (caller provides the async iterator)
    - Message creation (caller sets up user/assistant messages)
    - Storage persistence (caller persists after streaming)
    - LSP warmup (caller provides ``on_file_paths`` callback)

    Args:
        state: The server state for session management and event routing.
        session_id: The OpenCode session ID.
        assistant_msg_id: The assistant message ID.
        assistant_msg: The mutable assistant message to append parts to.
        working_dir: Working directory for path context.
        on_file_paths: Optional callback invoked with file paths discovered during
            tool progress events (used for LSP warmup).
    """

    state: ServerState
    session_id: str
    assistant_msg_id: str
    assistant_msg: MessageWithParts
    working_dir: str
    on_file_paths: Callable[[list[str]], None] | None = None

    # Event processor and context for stream processing
    processor: EventProcessor = field(default_factory=EventProcessor, init=False)
    main_context: EventProcessorContext = field(init=False)

    def __post_init__(self) -> None:
        self.main_context = EventProcessorContext(
            session_id=self.session_id,
            assistant_msg_id=self.assistant_msg_id,
            assistant_msg=self.assistant_msg,
            state=self.state,
            working_dir=self.working_dir,
        )

    # --- public read-only accessors ---

    @property
    def response_text(self) -> str:
        return self.main_context.response_text

    @property
    def input_tokens(self) -> int:
        return self.main_context.input_tokens

    @property
    def output_tokens(self) -> int:
        return self.main_context.output_tokens

    @property
    def total_cost(self) -> float:
        return self.main_context.total_cost

    @property
    def text_part(self) -> TextPart | None:
        return self.main_context.text_part

    # --- main entry point ---

    async def process_stream(
        self,
        stream: AsyncIterator[RichAgentStreamEvent[Any]],
    ) -> AsyncIterator[Event]:
        """Consume agent events and yield OpenCode SSE events.

        Wraps the entire stream in error handling; on exception yields
        a ``SessionErrorEvent`` so the UI shows the failure.
        """
        try:
            async for event in stream:
                async for oc_event in self.processor.process(event, self.main_context):
                    yield oc_event
        except Exception as e:  # noqa: BLE001
            self.main_context.response_text = f"Error calling agent: {e}"
            yield SessionErrorEvent.from_exception(session_id=self.session_id, exception=e)

    async def _handle_event(self, event: RichAgentStreamEvent[Any]) -> AsyncIterator[Event]:
        """Backward-compatible event handler that delegates to EventProcessor.

        This method is deprecated but kept for tests that directly call it.
        Use process_stream instead for new code.

        Args:
            event: The agent stream event to process.

        Yields:
            OpenCode Event objects for broadcasting.
        """
        async for oc_event in self.processor.process(event, self.main_context):
            yield oc_event

    def finalize(self) -> Iterator[Event]:
        """Yield final events after the stream has ended.

        Produces the final text part update (or creates one if text was never
        streamed), the step-finish part, and the final text timing update.
        """
        response_time = now_ms()
        start = self.main_context.stream_start_ms

        # Final text part
        if self.main_context.response_text and self.main_context.text_part is None:
            # Text was never streamed incrementally — create a text part now
            text_part = TextPart(
                id=identifier.ascending("part"),
                message_id=self.assistant_msg_id,
                session_id=self.session_id,
                text=self.main_context.response_text,
                time=TimeStartEndOptional(start=start, end=response_time),
            )
            self.assistant_msg.parts.append(text_part)
            yield PartUpdatedEvent.create(text_part)
        elif self.main_context.text_part is not None:
            # Update streamed text part with final timing
            final_text_part = TextPart(
                id=self.main_context.text_part.id,
                message_id=self.assistant_msg_id,
                session_id=self.session_id,
                text=self.main_context.response_text,
                time=TimeStartEndOptional(start=start, end=response_time),
            )
            self.assistant_msg.update_part(final_text_part)

        # Step finish
        cache = TokenCache(read=0, write=0)
        tokens = Tokens(
            cache=cache,
            input=self.main_context.input_tokens,
            output=self.main_context.output_tokens,
            reasoning=0,
        )
        step_finish = StepFinishPart(
            id=identifier.ascending("part"),
            message_id=self.assistant_msg_id,
            session_id=self.session_id,
            tokens=tokens,
            cost=self.main_context.total_cost,
        )
        self.assistant_msg.parts.append(step_finish)
        yield PartUpdatedEvent.create(step_finish)
