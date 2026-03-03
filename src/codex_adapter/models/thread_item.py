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


class DynamicToolCallOutputContentItem(CodexBaseModel):
    """Output content item for dynamic tool call response."""

    type: Literal["inputText", "inputImage"]
    text: str | None = None
    image_url: str | None = None


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


class ThreadItemUserMessage(CodexBaseModel):
    """User message item."""

    type: Literal["userMessage"] = "userMessage"
    id: str
    content: list[UserInput]


class ThreadItemAgentMessage(CodexBaseModel):
    """Agent message item."""

    type: Literal["agentMessage"] = "agentMessage"
    id: str
    text: str
    phase: MessagePhase | None = None


class ThreadItemPlan(CodexBaseModel):
    """Plan item."""

    type: Literal["plan"] = "plan"
    id: str
    text: str


class ThreadItemReasoning(CodexBaseModel):
    """Reasoning item."""

    type: Literal["reasoning"] = "reasoning"
    id: str
    summary: list[str] = Field(default_factory=list)
    content: list[str] = Field(default_factory=list)


class ThreadItemCommandExecution(CodexBaseModel):
    """Command execution item."""

    type: Literal["commandExecution"] = "commandExecution"
    id: str
    command: str
    cwd: str
    process_id: str | None = None
    status: CommandExecutionStatus
    command_actions: list[CommandAction] = Field(default_factory=list)
    aggregated_output: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None


class ThreadItemFileChange(CodexBaseModel):
    """File change item."""

    type: Literal["fileChange"] = "fileChange"
    id: str
    changes: list[FileUpdateChange]
    status: PatchApplyStatus


class ThreadItemMcpToolCall(CodexBaseModel):
    """MCP tool call item."""

    type: Literal["mcpToolCall"] = "mcpToolCall"
    id: str
    server: str
    tool: str
    status: McpToolCallStatus
    arguments: dict[str, Any] | None = None
    result: McpToolCallResult | None = None
    error: McpToolCallError | None = None
    duration_ms: int | None = None


class ThreadItemDynamicToolCall(CodexBaseModel):
    """Dynamic tool call item."""

    type: Literal["dynamicToolCall"] = "dynamicToolCall"
    id: str
    tool: str
    arguments: dict[str, Any] | None = None
    status: DynamicToolCallStatus
    content_items: list[DynamicToolCallOutputContentItem] | None = None
    success: bool | None = None
    duration_ms: int | None = None


class ThreadItemWebSearch(CodexBaseModel):
    """Web search item."""

    type: Literal["webSearch"] = "webSearch"
    id: str
    query: str
    action: WebSearchAction | None = None


class ThreadItemImageView(CodexBaseModel):
    """Image view item."""

    type: Literal["imageView"] = "imageView"
    id: str
    path: str


class ThreadItemEnteredReviewMode(CodexBaseModel):
    """Entered review mode item."""

    type: Literal["enteredReviewMode"] = "enteredReviewMode"
    id: str
    review: str


class ThreadItemExitedReviewMode(CodexBaseModel):
    """Exited review mode item."""

    type: Literal["exitedReviewMode"] = "exitedReviewMode"
    id: str
    review: str


class ThreadItemContextCompaction(CodexBaseModel):
    """Context compaction item."""

    type: Literal["contextCompaction"] = "contextCompaction"
    id: str


class ThreadItemCollabAgentToolCall(CodexBaseModel):
    """Collab agent tool call item."""

    type: Literal["collabAgentToolCall"] = "collabAgentToolCall"
    id: str
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
