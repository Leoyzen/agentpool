"""Convert between Codex and AgentPool types.

Provides converters for:
- Event conversion (Codex streaming events -> AgentPool events)
- MCP server configs (Native configs -> Codex types)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentpool.agents.codex_agent.codex_converters import (
    _format_tool_result,
    _thread_item_to_tool_call_part,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from codexed.models import CodexEvent

    from agentpool.agents.events import RichAgentStreamEvent


async def convert_codex_stream(  # noqa: PLR0915
    events: AsyncIterator[CodexEvent],
) -> AsyncIterator[RichAgentStreamEvent[Any]]:
    """Convert Codex event stream to native events with stateful accumulation.

    Args:
        events: Async iterator of Codex events from the app-server

    Yields:
        Native AgentPool stream events
    """
    from codexed.models import (
        AgentMessageDeltaEvent,
        CommandExecutionOutputDeltaEvent,
        FileChangeOutputDeltaEvent,
        ItemCompletedEvent,
        ItemStartedEvent,
        McpToolCallProgressEvent,
        ReasoningTextDeltaEvent,
        ThreadCompactedEvent,
        ThreadItemCommandExecution,
        ThreadItemFileChange,
        ThreadItemMcpToolCall,
        TurnPlanUpdatedEvent,
    )

    from agentpool.agents.events import (
        CompactionEvent,
        PartDeltaEvent,
        PlanUpdateEvent,
        TextContentItem,
        ToolCallCompleteEvent,
        ToolCallProgressEvent,
        ToolCallStartEvent,
    )
    from agentpool.utils.todos import PlanEntry

    # Accumulation state for streaming tool outputs
    tool_outputs: dict[str, list[str]] = {}

    async for event in events:
        match event:
            # === Stateful: Accumulate command execution output ===
            case CommandExecutionOutputDeltaEvent(data=data):
                item_id = data.item_id
                tool_outputs.setdefault(item_id, []).append(data.delta)
                # Emit accumulated progress with replace semantics, wrapped in code block
                output = "".join(tool_outputs[item_id])
                items = [TextContentItem(text=f"```\n{output}\n```")]
                yield ToolCallProgressEvent(tool_call_id=item_id, items=items, replace_content=True)

            # === File change output delta - ignore the summary, we show diff from item/started ===
            case FileChangeOutputDeltaEvent():
                # The outputDelta is just "Success. Updated..." summary - not useful
                # We already emitted the actual diff content in item/started
                pass

            case AgentMessageDeltaEvent(data=data):
                yield PartDeltaEvent.text(index=0, content=data.delta)

            case ReasoningTextDeltaEvent(data=data):
                yield PartDeltaEvent.thinking(index=0, content=data.delta)

            case ItemStartedEvent(data=data):
                if part := _thread_item_to_tool_call_part(data.item):
                    # Extract title based on tool type
                    match data.item:
                        case ThreadItemCommandExecution(command=command):
                            title = f"Execute: {command}"
                        case ThreadItemFileChange(changes=changes):
                            # Build title from file paths
                            paths = [c.path for c in changes[:3]]  # First 3 paths
                            if len(changes) > 3:  # noqa: PLR2004
                                title = f"Edit: {', '.join(paths)} (+{len(changes) - 3} more)"
                            else:
                                title = f"Edit: {', '.join(paths)}"
                        case ThreadItemMcpToolCall(tool=tool):
                            title = f"Call {tool}"
                        case _:
                            title = f"Call {part.tool_name}"

                    yield ToolCallStartEvent(
                        tool_call_id=part.tool_call_id,
                        tool_name=part.tool_name,
                        title=title,
                        raw_input=part.args_as_dict(),
                    )

                    # For file changes, immediately emit the diff as progress
                    if isinstance(data.item, ThreadItemFileChange):
                        diff_parts = []
                        for change in data.item.changes:
                            diff_parts.append(f"{change.kind.kind.upper()}: {change.path}")
                            if change.diff:
                                diff_parts.append(change.diff)
                        if diff_parts:
                            items = [TextContentItem(text="\n".join(diff_parts))]
                            yield ToolCallProgressEvent(tool_call_id=part.tool_call_id, items=items)

            # === Stateful: Tool/command completed - clean up accumulator ===
            case ItemCompletedEvent(data=data):
                item = data.item
                # Clean up accumulated output for this item
                tool_outputs.pop(item.id, None)
                if part := _thread_item_to_tool_call_part(item):
                    yield ToolCallCompleteEvent(
                        tool_name=part.tool_name,
                        tool_call_id=part.tool_call_id,
                        tool_input=part.args_as_dict(),
                        tool_result=await _format_tool_result(item),
                        agent_name="codex",  # Will be overridden by agent
                        message_id=data.turn_id,
                    )

            # === Stateless: MCP tool call progress ===
            case McpToolCallProgressEvent(data=data):
                yield ToolCallProgressEvent(tool_call_id=data.item_id, message=data.message)

            # === Stateless: Thread compacted ===
            case ThreadCompactedEvent(data=data):
                yield CompactionEvent(session_id=data.thread_id, phase="completed")

            # === Stateless: Turn plan updated ===
            case TurnPlanUpdatedEvent(data=data):
                entries = [
                    PlanEntry(
                        content=step.step,
                        priority="medium",  # Codex doesn't provide priority
                        status="in_progress" if step.status == "inProgress" else step.status,
                    )
                    for step in data.plan
                ]
                yield PlanUpdateEvent(entries=entries)

            # Ignore other events (token usage, turn started/completed, etc.)
            case _:
                pass
