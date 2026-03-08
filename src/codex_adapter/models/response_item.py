"""ResponseItem types for the Codex protocol.

These represent the conversation history items used in thread/resume.
Matches the Rust `ResponseItem` discriminated union from the protocol.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Discriminator, Field, Tag

from codex_adapter.models.base import CodexBaseModel


# --- Content items ---


class InputTextContent(CodexBaseModel):
    """Text input content item."""

    type: Literal["input_text"] = "input_text"
    text: str


class InputImageContent(CodexBaseModel):
    """Image input content item."""

    type: Literal["input_image"] = "input_image"
    image_url: str


class OutputTextContent(CodexBaseModel):
    """Text output content item."""

    type: Literal["output_text"] = "output_text"
    text: str


ContentItem = Annotated[
    Annotated[InputTextContent, Tag("input_text")]
    | Annotated[InputImageContent, Tag("input_image")]
    | Annotated[OutputTextContent, Tag("output_text")],
    Discriminator("type"),
]


# --- Reasoning ---


class ReasoningTextContent(CodexBaseModel):
    """Reasoning text content."""

    type: Literal["reasoning_text"] = "reasoning_text"
    text: str


class ReasoningPlainTextContent(CodexBaseModel):
    """Plain text reasoning content."""

    type: Literal["text"] = "text"
    text: str


ReasoningItemContent = Annotated[
    Annotated[ReasoningTextContent, Tag("reasoning_text")]
    | Annotated[ReasoningPlainTextContent, Tag("text")],
    Discriminator("type"),
]


class ReasoningSummaryText(CodexBaseModel):
    """Reasoning summary text."""

    type: Literal["summary_text"] = "summary_text"
    text: str


# --- Function call output ---


class FunctionCallOutputTextItem(CodexBaseModel):
    """Text content in function call output."""

    type: Literal["input_text"] = "input_text"
    text: str


class FunctionCallOutputImageItem(CodexBaseModel):
    """Image content in function call output."""

    type: Literal["input_image"] = "input_image"
    image_url: str
    detail: Literal["auto", "low", "high"] | None = None


FunctionCallOutputContentItem = Annotated[
    Annotated[FunctionCallOutputTextItem, Tag("input_text")]
    | Annotated[FunctionCallOutputImageItem, Tag("input_image")],
    Discriminator("type"),
]


class FunctionCallOutputPayload(CodexBaseModel):
    """Payload for function call output."""

    body: str | list[FunctionCallOutputContentItem]
    success: bool | None = None


# --- Shell action ---


class LocalShellExecAction(CodexBaseModel):
    """Shell exec action details."""

    type: Literal["exec"] = "exec"
    command: list[str]
    timeout_ms: int | None = None
    working_directory: str | None = None
    env: dict[str, str] | None = None
    user: str | None = None


LocalShellAction = LocalShellExecAction  # Currently only one variant


# --- Web search action ---


class WebSearchActionSearch(CodexBaseModel):
    """Web search action."""

    type: Literal["search"] = "search"
    query: str | None = None
    queries: list[str] | None = None


class WebSearchActionOpenPage(CodexBaseModel):
    """Open page action."""

    type: Literal["open_page"] = "open_page"
    url: str | None = None


class WebSearchActionFindInPage(CodexBaseModel):
    """Find in page action."""

    type: Literal["find_in_page"] = "find_in_page"
    url: str | None = None
    pattern: str | None = None


class WebSearchActionOther(CodexBaseModel):
    """Other web search action."""

    type: Literal["other"] = "other"


WebSearchAction = Annotated[
    Annotated[WebSearchActionSearch, Tag("search")]
    | Annotated[WebSearchActionOpenPage, Tag("open_page")]
    | Annotated[WebSearchActionFindInPage, Tag("find_in_page")]
    | Annotated[WebSearchActionOther, Tag("other")],
    Discriminator("type"),
]


# --- Ghost commit ---


class GhostCommit(CodexBaseModel):
    """Details of a ghost commit created from a repository state."""

    id: str
    parent: str | None = None
    preexisting_untracked_files: list[str] = Field(default_factory=list)
    preexisting_untracked_dirs: list[str] = Field(default_factory=list)


# --- ResponseItem variants ---


class MessageResponseItem(CodexBaseModel):
    """Message response item."""

    type: Literal["message"] = "message"
    role: str
    content: list[ContentItem]
    end_turn: bool | None = None
    phase: Literal["commentary", "final_answer"] | None = None


class ReasoningResponseItem(CodexBaseModel):
    """Reasoning response item."""

    type: Literal["reasoning"] = "reasoning"
    summary: list[ReasoningSummaryText]
    content: list[ReasoningItemContent] | None = None
    encrypted_content: str | None = None


class LocalShellCallResponseItem(CodexBaseModel):
    """Local shell call response item."""

    type: Literal["local_shell_call"] = "local_shell_call"
    call_id: str | None = None
    status: Literal["completed", "in_progress", "incomplete"]
    action: LocalShellAction


class FunctionCallResponseItem(CodexBaseModel):
    """Function call response item."""

    type: Literal["function_call"] = "function_call"
    name: str
    arguments: str
    call_id: str


class FunctionCallOutputResponseItem(CodexBaseModel):
    """Function call output response item."""

    type: Literal["function_call_output"] = "function_call_output"
    call_id: str
    output: FunctionCallOutputPayload


class CustomToolCallResponseItem(CodexBaseModel):
    """Custom tool call response item."""

    type: Literal["custom_tool_call"] = "custom_tool_call"
    status: str | None = None
    call_id: str
    name: str
    input: str


class CustomToolCallOutputResponseItem(CodexBaseModel):
    """Custom tool call output response item."""

    type: Literal["custom_tool_call_output"] = "custom_tool_call_output"
    call_id: str
    output: FunctionCallOutputPayload


class WebSearchCallResponseItem(CodexBaseModel):
    """Web search call response item."""

    type: Literal["web_search_call"] = "web_search_call"
    status: str | None = None
    action: WebSearchAction | None = None


class ImageGenerationCallResponseItem(CodexBaseModel):
    """Image generation call response item."""

    type: Literal["image_generation_call"] = "image_generation_call"
    id: str
    status: str
    revised_prompt: str | None = None
    result: str


class GhostSnapshotResponseItem(CodexBaseModel):
    """Ghost snapshot response item."""

    type: Literal["ghost_snapshot"] = "ghost_snapshot"
    ghost_commit: GhostCommit


class CompactionResponseItem(CodexBaseModel):
    """Compaction response item."""

    type: Literal["compaction"] = "compaction"
    encrypted_content: str


class OtherResponseItem(CodexBaseModel):
    """Other/unknown response item."""

    type: Literal["other"] = "other"


ResponseItem = Annotated[
    Annotated[MessageResponseItem, Tag("message")]
    | Annotated[ReasoningResponseItem, Tag("reasoning")]
    | Annotated[LocalShellCallResponseItem, Tag("local_shell_call")]
    | Annotated[FunctionCallResponseItem, Tag("function_call")]
    | Annotated[FunctionCallOutputResponseItem, Tag("function_call_output")]
    | Annotated[CustomToolCallResponseItem, Tag("custom_tool_call")]
    | Annotated[CustomToolCallOutputResponseItem, Tag("custom_tool_call_output")]
    | Annotated[WebSearchCallResponseItem, Tag("web_search_call")]
    | Annotated[ImageGenerationCallResponseItem, Tag("image_generation_call")]
    | Annotated[GhostSnapshotResponseItem, Tag("ghost_snapshot")]
    | Annotated[CompactionResponseItem, Tag("compaction")]
    | Annotated[OtherResponseItem, Tag("other")],
    Discriminator("type"),
]
