"""Stream adapter for converting Claude SDK messages to agentpool events.

Tool Call Event Flow
--------------------
The SDK streams events in a specific order. Understanding this is critical for
avoiding race conditions with permission dialogs:

1. **content_block_start** (StreamEvent)
   - Contains tool_use_id, tool name
   - We emit ToolCallStartEvent here (early, with empty args)
   - ACP converter sends `tool_call` notification to client

2. **content_block_delta** (StreamEvent, multiple)
   - Contains input_json_delta with partial JSON args
   - We emit PartDeltaEvent(ToolCallPartDelta) for streaming
   - ACP converter accumulates args, doesn't send notifications

3. **AssistantMessage** with ToolUseBlock
   - Contains complete tool call info (id, name, full args)
   - We do NOT emit events here (would race with permission)
   - Just track file modifications silently

4. **content_block_stop**, **message_delta**, **message_stop** (StreamEvent)
   - Signal completion of the message

5. **can_use_tool callback** (~100ms after message_stop)
   - SDK calls our permission callback
   - We send permission request to ACP client
   - Client shows permission dialog to user
   - IMPORTANT: No notifications should be sent while dialog is open!

6. **Tool execution or denial**
   - If allowed: tool runs, emits ToolCallCompleteEvent
   - If denied: SDK receives denial, continues with next turn
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Any, assert_never, cast

from clawd_code_sdk.models import (
    AuthStatusMessage,
    ElicitationCompleteMessage,
    FilesPersistedSystemMessage,
    HookProgressSystemMessage,
    HookResponseSystemMessage,
    HookStartedSystemMessage,
    ImageBlock,
    InitSystemMessage,
    LocalCommandOutputMessage,
    PromptSuggestionMessage,
    RateLimitMessage,
    ResultErrorMessage,
    ResultSuccessMessage,
    StatusSystemMessage,
    TaskNotificationSystemMessage,
    TaskProgressSystemMessage,
    TaskStartedSystemMessage,
    ToolProgressMessage,
    ToolUseSummaryMessage,
)
from pydantic_ai import FunctionToolResultEvent, PartEndEvent, TextPart, ToolReturnPart

from agentpool.agents.claude_code_agent.converters import convert_to_opencode_metadata
from agentpool.agents.events import (
    CompactionEvent,
    PartDeltaEvent,
    PartStartEvent,
    TerminalContentItem,
    ToolCallCompleteEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)
from agentpool.agents.events.infer_info import derive_rich_tool_info


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from clawd_code_sdk import ResultMessage, ToolUseBlock
    from clawd_code_sdk.models import StopReason

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.events.events import ToolCallContentItem

_MCP_TOOL_PATTERN = re.compile(r"^mcp__agentpool-(.+)-tools__(.+)$")
"""Pattern to detect CC-provided tool names."""


def _strip_mcp_prefix(tool_name: str) -> str:
    """Strip MCP server prefix from tool names for cleaner UI display."""
    if match := _MCP_TOOL_PATTERN.match(tool_name):
        return match.group(2)
    return tool_name


@dataclass
class StreamAdapterResult:
    """Accumulated state from processing the Claude SDK stream.

    Populated during stream processing and consumed by the caller
    to build the final ChatMessage. Message reconstruction (model_messages,
    response_parts, text) is handled by the caller's ``MessageReconstructor``.
    """

    result_message: ResultMessage | None = None
    """The SDK ResultMessage captured at end of stream (contains usage, cost, etc.)."""

    resolved_model: str | None = None
    """Model identifier resolved from AssistantMessage responses."""

    @property
    def stop_reason(self) -> StopReason | None:
        """Extract stop reason from result message."""
        return self.result_message.stop_reason if self.result_message else None


async def adapt_claude_stream(  # noqa: PLR0915
    merged_stream: AsyncIterator[Any],
    tool_metadata: dict[str, dict[str, Any]],
    agent_name: str,
    session_id: str,
) -> AsyncIterator[RichAgentStreamEvent[Any] | StreamAdapterResult]:
    """Convert a Claude SDK message stream into agentpool events.

    Takes an already-merged stream (SDK messages + injected events) and
    converts SDK message types into agentpool's RichAgentStreamEvent types.
    As the final yielded item, produces a StreamAdapterResult containing
    accumulated state needed for building the final ChatMessage.

    Args:
        merged_stream: Pre-merged async iterator of SDK Messages and injected events.
        tool_metadata: Metadata dict from tool bridge, keyed by tool_call_id.
        agent_name: Name of the agent (for event attribution).
        session_id: Current session ID.

    Yields:
        RichAgentStreamEvent instances, followed by a final StreamAdapterResult.
    """
    from anthropic.types.beta import (
        BetaCitationsDelta as CitationsDelta,
        BetaInputJSONDelta as InputJSONDelta,
        BetaRawContentBlockDeltaEvent,
        BetaRawContentBlockStartEvent,
        BetaRawContentBlockStopEvent,
        BetaSignatureDelta as SignatureDelta,
        BetaTextBlock as AnthTextBlock,
        BetaTextDelta as TextDelta,
        BetaThinkingBlock as AnthThinkingBlock,
        BetaThinkingDelta as ThinkingDelta,
        BetaToolUseBlock as AnthToolUseBlock,
    )
    from clawd_code_sdk.models import (
        AssistantMessage,
        CompactBoundarySystemMessage,
        MessageUnion,
        ResultMessage,
        StreamEvent,
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )

    result = StreamAdapterResult()
    pending_tool_calls: dict[str, ToolUseBlock] = {}
    streaming_tc_id: str | None = None
    # Note: message reconstruction (model_messages, response_parts, text accumulation)
    # is handled by the caller's MessageReconstructor, which observes the yielded events.

    async for event_or_message in merged_stream:
        if not isinstance(event_or_message, MessageUnion):
            yield event_or_message
            continue
        message = event_or_message
        match message:
            case AssistantMessage(model=model, content=msg_content):
                if model:
                    result.resolved_model = model
                for block in msg_content:
                    match block:
                        case ToolUseBlock(id=tc_id, name=name, input=input_data):
                            pending_tool_calls[tc_id] = block
                            display_name = _strip_mcp_prefix(name)
                            # Emit progress update with complete args
                            # (ToolCallStartEvent was already emitted via streaming
                            # with empty args; now we have the full picture)
                            rich_info = derive_rich_tool_info(name, input_data)
                            yield ToolCallProgressEvent(
                                tool_call_id=tc_id,
                                tool_name=display_name,
                                title=rich_info.title,
                                tool_input=cast(dict[str, Any], input_data),
                            )
                        case ToolResultBlock() | ThinkingBlock() | TextBlock() | ImageBlock():
                            pass  # ToolResult Blocks only appear in UserMessages
                        case _ as unknown_block:
                            assert_never(unknown_block)  # ty:ignore[type-assertion-failure]

            case UserMessage(content=list() as user_blocks):
                for user_block in user_blocks:
                    if not isinstance(user_block, ToolResultBlock):
                        continue
                    tc_id = user_block.tool_use_id
                    result_content = user_block.get_parsed_content()
                    # Flush + tool return handled by reconstructor via
                    # ToolCcleanupallCompleteEvent observation
                    tool_use = pending_tool_calls.pop(tc_id)
                    # For Bash tools: stream output + exit to virtual terminal
                    # before signaling completion. This matches the 3-step
                    # display-only terminal lifecycle.
                    if tool_use.name == "Bash":
                        output_str = str(result_content) if result_content else ""
                        exit_code = 1 if user_block.is_error else 0
                        yield ToolCallProgressEvent(
                            tool_call_id=tc_id,
                            tool_name=_strip_mcp_prefix(tool_use.name),
                            field_meta={
                                "terminal_output": {
                                    "terminal_id": tc_id,
                                    "data": output_str,
                                },
                            },
                        )
                        yield ToolCallProgressEvent(
                            tool_call_id=tc_id,
                            tool_name=_strip_mcp_prefix(tool_use.name),
                            field_meta={
                                "terminal_exit": {
                                    "terminal_id": tc_id,
                                    "exit_code": exit_code,
                                    "signal": None,
                                },
                            },
                        )

                    return_part = ToolReturnPart(
                        tool_name=_strip_mcp_prefix(tool_use.name),
                        content=result_content,
                        tool_call_id=tc_id,
                    )
                    yield FunctionToolResultEvent(result=return_part)
                    tool_input = cast(dict[str, Any], tool_use.input) if tool_use else {}
                    metadata: dict[str, Any] | None = tool_metadata.get(tc_id)
                    if not metadata and isinstance(message.tool_use_result, list):
                        tool_use_result = (
                            message.tool_use_result[0] if message.tool_use_result else {}
                        )
                        oc_metadata = convert_to_opencode_metadata(
                            tool_use.name,
                            tool_use_result,
                            tool_input,
                        )
                        metadata = cast(dict[str, Any] | None, oc_metadata)

                    yield ToolCallCompleteEvent(
                        tool_name=_strip_mcp_prefix(tool_use.name),
                        tool_call_id=tc_id,
                        tool_input=tool_input,
                        tool_result=result_content,
                        agent_name=agent_name,
                        message_id="",
                        metadata=metadata,
                    )

            # Real-time streaming: content_block_start
            case StreamEvent(
                event=BetaRawContentBlockStartEvent(index=index, content_block=content_block)
            ):
                match content_block:
                    case AnthTextBlock():
                        yield PartStartEvent.text(index=index, content="")
                    case AnthThinkingBlock():
                        yield PartStartEvent.thinking(index=index, content="")
                    case AnthToolUseBlock(id=tc_id, name=raw_tool_name):
                        tool_name = _strip_mcp_prefix(raw_tool_name)
                        streaming_tc_id = tc_id
                        rich_info = derive_rich_tool_info(raw_tool_name, {})
                        # For Bash tools: signal client to create a display-only
                        # terminal. Claude Code executes commands server-side, so
                        # we use the _meta virtual terminal convention instead of
                        # the ACP terminal/create RPC.
                        is_bash = raw_tool_name == "Bash"
                        if is_bash:
                            tc_content: list[ToolCallContentItem] = [
                                TerminalContentItem(terminal_id=tc_id),
                            ]
                            meta: dict[str, Any] | None = {
                                "terminal_info": {"terminal_id": tc_id},
                            }
                        else:
                            tc_content = rich_info.content
                            meta = None
                        yield ToolCallStartEvent(
                            tool_call_id=tc_id,
                            tool_name=tool_name,
                            title=rich_info.title,
                            kind=rich_info.kind,
                            locations=[],
                            content=tc_content,
                            raw_input={},
                            field_meta=meta,
                        )

            # content_block_delta events
            case StreamEvent(event=BetaRawContentBlockDeltaEvent(index=index, delta=delta)):
                match delta:
                    case TextDelta(text=text):
                        yield PartDeltaEvent.text(index=index, content=text)
                    case ThinkingDelta(thinking=thinking):
                        yield PartDeltaEvent.thinking(index=index, content=thinking)
                    case InputJSONDelta(partial_json=json_) if json_ and streaming_tc_id:
                        yield PartDeltaEvent.tool_call(
                            index, content=json_, tool_call_id=streaming_tc_id
                        )
                    case CitationsDelta() | SignatureDelta() | InputJSONDelta():
                        pass
                    case _ as unreachable:
                        assert_never(unreachable)  # ty:ignore[type-assertion-failure]

            # content_block_stop
            case StreamEvent(event=BetaRawContentBlockStopEvent(index=index)):
                streaming_tc_id = None
                yield PartEndEvent(index=index, part=TextPart(content=""))

            case StatusSystemMessage(status="compacting"):
                yield CompactionEvent(session_id=session_id, trigger="auto", phase="starting")

            case CompactBoundarySystemMessage(compact_metadata=compact_metadata):
                yield CompactionEvent(
                    session_id=session_id,
                    trigger=compact_metadata["trigger"],
                    phase="completed",
                    pre_tokens=compact_metadata["pre_tokens"],
                )

            case (
                StreamEvent()
                | UserMessage()
                | PromptSuggestionMessage()
                | ResultSuccessMessage()
                | ResultErrorMessage()
                | HookStartedSystemMessage()
                | HookProgressSystemMessage()
                | HookResponseSystemMessage()
                | RateLimitMessage()
                | AuthStatusMessage()
                | ToolProgressMessage()
                | ToolUseSummaryMessage()
                | InitSystemMessage()
                | StatusSystemMessage()
                | TaskStartedSystemMessage()
                | TaskProgressSystemMessage()
                | TaskNotificationSystemMessage()
                | FilesPersistedSystemMessage()
                | ElicitationCompleteMessage()
                | LocalCommandOutputMessage()
            ):
                pass
            case _ as unreachable:
                assert_never(unreachable)

        # Check for result (end of response)
        if isinstance(message, ResultMessage):
            result.result_message = message
            break

    yield result
