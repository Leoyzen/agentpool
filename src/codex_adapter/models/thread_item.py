from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field
from pydantic.alias_generators import to_camel

from codex_adapter.models.base import CodexBaseModel
from codex_adapter.models.codex_types import (  # noqa: TC001
    CollabAgentStatus,
    CollabAgentTool,
    CollabAgentToolCallStatus,
    CommandExecutionStatus,
    DynamicToolCallStatus,
    McpToolCallStatus,
    MessagePhase,
    PatchApplyStatus,
)
from codex_adapter.models.command_action import CommandAction  # noqa: TC001
from codex_adapter.models.user_input import UserInput  # noqa: TC001
from codex_adapter.models.web_search import WebSearchAction  # noqa: TC001


# ---------------------------------------------------------------------------
# Types shared with misc.py — defined here to avoid circular imports.
# misc.py re-imports these from this module.
# ---------------------------------------------------------------------------


class DynamicToolCallOutputTextItem(CodexBaseModel):
    """Text output content item for dynamic tool call response."""

    type: Literal["inputText"] = "inputText"
    text: str


class DynamicToolCallOutputImageItem(CodexBaseModel):
    """Image output content item for dynamic tool call response."""

    type: Literal["inputImage"] = "inputImage"
    image_url: str


DynamicToolCallOutputContentItem = DynamicToolCallOutputTextItem | DynamicToolCallOutputImageItem


class PatchChangeKind(CodexBaseModel):
    """Kind of file change (nested object in Codex's fileChange item)."""

    kind: Literal["add", "delete", "update"] = Field(validation_alias="type")
    move_path: str | None = None


class FileUpdateChange(CodexBaseModel):
    """File update change."""

    path: str
    kind: PatchChangeKind
    diff: str | None = None  # May be absent in "inProgress" state


class McpContentBlock(CodexBaseModel):
    """MCP content block (from external mcp_types crate).

    We allow extra fields since this comes from an external library.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True, alias_generator=to_camel)


class McpToolCallResult(CodexBaseModel):
    """MCP tool call result."""

    content: list[McpContentBlock]
    structured_content: Any = None


class McpToolCallError(CodexBaseModel):
    """MCP tool call error."""

    message: str


class CollabAgentState(CodexBaseModel):
    """Collab agent state."""

    status: CollabAgentStatus
    message: str | None = None


class BaseThreadItem(CodexBaseModel):
    """Base class for thread items."""

    id: str


class ThreadItemUserMessage(BaseThreadItem):
    """User message item."""

    type: Literal["userMessage"] = "userMessage"
    content: list[UserInput]


class ThreadItemAgentMessage(BaseThreadItem):
    """Agent message item."""

    type: Literal["agentMessage"] = "agentMessage"
    text: str
    phase: MessagePhase | None = None


class ThreadItemPlan(BaseThreadItem):
    """Plan item."""

    type: Literal["plan"] = "plan"
    text: str


class ThreadItemReasoning(BaseThreadItem):
    """Reasoning item."""

    type: Literal["reasoning"] = "reasoning"
    summary: list[str] = Field(default_factory=list)
    content: list[str] = Field(default_factory=list)


class ThreadItemCommandExecution(BaseThreadItem):
    """Command execution item."""

    type: Literal["commandExecution"] = "commandExecution"
    command: str
    cwd: str
    process_id: str | None = None
    status: CommandExecutionStatus
    command_actions: list[CommandAction] = Field(default_factory=list)
    aggregated_output: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None


class ThreadItemFileChange(BaseThreadItem):
    """File change item."""

    type: Literal["fileChange"] = "fileChange"
    changes: list[FileUpdateChange]
    status: PatchApplyStatus


class ThreadItemMcpToolCall(BaseThreadItem):
    """MCP tool call item."""

    type: Literal["mcpToolCall"] = "mcpToolCall"
    server: str
    tool: str
    status: McpToolCallStatus
    arguments: dict[str, Any] | None = None
    result: McpToolCallResult | None = None
    error: McpToolCallError | None = None
    duration_ms: int | None = None


class ThreadItemDynamicToolCall(BaseThreadItem):
    """Dynamic tool call item."""

    type: Literal["dynamicToolCall"] = "dynamicToolCall"
    tool: str
    arguments: dict[str, Any] | None = None
    status: DynamicToolCallStatus
    content_items: list[DynamicToolCallOutputContentItem] | None = None
    success: bool | None = None
    duration_ms: int | None = None


class ThreadItemWebSearch(BaseThreadItem):
    """Web search item."""

    type: Literal["webSearch"] = "webSearch"
    query: str
    action: WebSearchAction | None = None


class ThreadItemImageView(BaseThreadItem):
    """Image view item."""

    type: Literal["imageView"] = "imageView"
    path: str


class ThreadItemEnteredReviewMode(BaseThreadItem):
    """Entered review mode item."""

    type: Literal["enteredReviewMode"] = "enteredReviewMode"
    review: str


class ThreadItemExitedReviewMode(BaseThreadItem):
    """Exited review mode item."""

    type: Literal["exitedReviewMode"] = "exitedReviewMode"
    review: str


class ThreadItemContextCompaction(BaseThreadItem):
    """Context compaction item."""

    type: Literal["contextCompaction"] = "contextCompaction"


class ThreadItemCollabAgentToolCall(BaseThreadItem):
    """Collab agent tool call item."""

    type: Literal["collabAgentToolCall"] = "collabAgentToolCall"
    tool: CollabAgentTool
    status: CollabAgentToolCallStatus
    sender_thread_id: str
    receiver_thread_ids: list[str] = Field(default_factory=list)
    prompt: str | None = None
    agents_states: dict[str, CollabAgentState] = Field(default_factory=dict)


# Discriminated union of all ThreadItem types
ThreadItem = (
    ThreadItemUserMessage
    | ThreadItemAgentMessage
    | ThreadItemPlan
    | ThreadItemReasoning
    | ThreadItemCommandExecution
    | ThreadItemFileChange
    | ThreadItemMcpToolCall
    | ThreadItemDynamicToolCall
    | ThreadItemCollabAgentToolCall
    | ThreadItemWebSearch
    | ThreadItemImageView
    | ThreadItemEnteredReviewMode
    | ThreadItemExitedReviewMode
    | ThreadItemContextCompaction
)
