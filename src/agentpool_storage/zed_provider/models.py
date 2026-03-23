"""Zed IDE storage format models."""

from __future__ import annotations

import io
import sys
from typing import Any, Literal

import anyenv
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


IS_DEV = "pytest" in sys.modules


class ZedBaseModel(BaseModel):
    """Base model with Zed storage."""

    model_config = ConfigDict(
        use_attribute_docstrings=True,
        extra="forbid" if IS_DEV else "ignore",
    )


class ZedMentionUri(ZedBaseModel):
    """Mention URI - can be File, Directory, Symbol, etc."""

    File: dict[str, Any] | None = None
    Directory: dict[str, Any] | None = None
    Symbol: dict[str, Any] | None = None
    Selection: dict[str, Any] | None = None
    Thread: dict[str, Any] | None = None
    TextThread: dict[str, Any] | None = None
    Rule: dict[str, Any] | None = None
    Fetch: dict[str, Any] | None = None
    PastedImage: bool | None = None
    Diagnostics: dict[str, Any] | None = None
    TerminalSelection: dict[str, Any] | None = None
    GitDiff: dict[str, Any] | None = None
    MergeConflict: dict[str, Any] | None = None


class ZedMention(ZedBaseModel):
    """A file/symbol mention in Zed."""

    uri: ZedMentionUri
    content: str


class ZedImage(ZedBaseModel):
    """An image in Zed (base64 encoded)."""

    source: str  # base64 encoded


class ZedThinking(ZedBaseModel):
    """Thinking block from model."""

    text: str
    signature: str | None = None


class ZedToolUse(ZedBaseModel):
    """Tool use block."""

    id: str
    name: str
    raw_input: str
    input: dict[str, Any]
    is_input_complete: bool = True
    thought_signature: str | None = None


class ZedToolResult(ZedBaseModel):
    """Tool result."""

    tool_use_id: str
    tool_name: str
    is_error: bool = False
    content: dict[str, Any] | str | None = None
    output: dict[str, Any] | str | None = None


# User message content blocks (v0.2.0+)


class ZedTextContent(ZedBaseModel):
    """Text content block."""

    Text: str


class ZedImageContent(ZedBaseModel):
    """Image content block."""

    Image: ZedImage


class ZedMentionContent(ZedBaseModel):
    """Mention content block."""

    Mention: ZedMention


ZedUserContent = ZedTextContent | ZedImageContent | ZedMentionContent


# Agent message content blocks (v0.2.0+)


class ZedTextBlock(ZedBaseModel):
    """Text block in agent message."""

    Text: str


class ZedThinkingBlock(ZedBaseModel):
    """Thinking block in agent message."""

    Thinking: ZedThinking


class ZedRedactedThinkingBlock(ZedBaseModel):
    """Redacted thinking block in agent message."""

    RedactedThinking: str


class ZedToolUseBlock(ZedBaseModel):
    """Tool use block in agent message."""

    ToolUse: ZedToolUse


ZedAgentContent = ZedTextBlock | ZedThinkingBlock | ZedRedactedThinkingBlock | ZedToolUseBlock


# v0.2.0+ nested message format


class ZedUserMessage(ZedBaseModel):
    """User message in Zed thread (v0.2.0+ format)."""

    id: str
    content: list[ZedUserContent]


class ZedAgentMessage(ZedBaseModel):
    """Agent message in Zed thread (v0.2.0+ format)."""

    content: list[ZedAgentContent]
    tool_results: dict[str, ZedToolResult] = Field(default_factory=dict)
    reasoning_details: Any | None = None


class ZedNestedMessage(ZedBaseModel):
    """A message in Zed thread v0.2.0+ - nested under User or Agent key."""

    User: ZedUserMessage | None = Field(default=None)
    Agent: ZedAgentMessage | None = Field(default=None)


# Flat message format (v0.1.0, v0.2.0)


class ZedCrease(ZedBaseModel):
    """A foldable region in the assistant panel."""

    start: int
    end: int
    icon_path: str = ""
    label: str = ""


class ZedSegment(ZedBaseModel):
    """A segment in a flat message."""

    type: Literal["text", "thinking", "RedactedThinking"]
    text: str | None = None
    signature: str | None = None  # For thinking segments
    data: str | None = None  # For RedactedThinking segments


class ZedFlatToolUse(ZedBaseModel):
    """Tool use in flat/legacy message format."""

    id: str
    name: str
    input: dict[str, Any]


class ZedFlatToolResult(ZedBaseModel):
    """Tool result in flat/legacy message format."""

    tool_use_id: str
    is_error: bool = False
    content: dict[str, Any] | str | None = None
    output: dict[str, Any] | str | None = None


class ZedFlatMessage(ZedBaseModel):
    """A message in flat format (used in v0.1.0 and v0.2.0)."""

    id: int
    role: Literal["user", "assistant"]
    segments: list[ZedSegment] = Field(default_factory=list)
    tool_uses: list[ZedFlatToolUse] = Field(default_factory=list)
    tool_results: list[ZedFlatToolResult] = Field(default_factory=list)
    context: str = ""
    creases: list[ZedCrease] = Field(default_factory=list)
    is_hidden: bool = False


# Union of all message formats - put ZedFlatMessage first since it's more specific
# (has required 'id' and 'role' fields that ZedNestedMessage doesn't have)
ZedMessage = ZedFlatMessage | ZedNestedMessage


class ZedLanguageModel(ZedBaseModel):
    """Model configuration."""

    provider: str
    model: str


class ZedTokenUsage(ZedBaseModel):
    """Token usage for a request or cumulative."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class ZedGitState(ZedBaseModel):
    """Git state for a worktree."""

    remote_url: str | None = None
    head_sha: str | None = None
    current_branch: str | None = None
    diff: str | None = None


class ZedWorktreeSnapshot(ZedBaseModel):
    """Git worktree snapshot."""

    worktree_path: str
    git_state: ZedGitState | None = None


class ZedProjectSnapshot(ZedBaseModel):
    """Project snapshot with git state."""

    worktree_snapshots: list[ZedWorktreeSnapshot] = Field(default_factory=list)
    timestamp: str


class ZedSubagentContext(ZedBaseModel):
    """Context passed to a subagent thread for lifecycle management."""

    parent_thread_id: str
    depth: int


class ZedScrollPosition(ZedBaseModel):
    """Serialized scroll position in the UI."""

    item_ix: int
    offset_in_item: float


class ZedThread(ZedBaseModel):
    """A Zed conversation thread."""

    model_config = ConfigDict(populate_by_name=True)

    # v0.3.0 uses "title", v0.2.0 uses "summary"
    title: str = Field(alias="title", validation_alias=AliasChoices("title", "summary"))
    messages: list[ZedMessage | Literal["Resume"]]  # Control messages
    updated_at: str
    version: str | None = None
    detailed_summary: str | None = None  # v0.3.0 field
    detailed_summary_state: str | dict[str, Any] | None = None  # v0.2.0 field
    initial_project_snapshot: ZedProjectSnapshot | None = None
    cumulative_token_usage: ZedTokenUsage = Field(default_factory=ZedTokenUsage)
    # v0.2.0: list of token usage per request
    # v0.3.0+: dict keyed by message ID
    request_token_usage: list[ZedTokenUsage] | dict[str, ZedTokenUsage] = Field(
        default_factory=list
    )
    model: ZedLanguageModel | None = None
    profile: str | None = None
    tool_use_limit_reached: bool = False
    imported: bool = False
    subagent_context: ZedSubagentContext | None = None
    speed: Literal["standard", "fast"] | None = None
    thinking_enabled: bool = False
    thinking_effort: str | None = None
    draft_prompt: list[dict[str, Any]] | None = None
    ui_scroll_position: ZedScrollPosition | None = None

    @classmethod
    def from_compressed(cls, data: bytes, data_type: Literal["zstd", "plain"]) -> ZedThread:
        """Decompress and parse thread data.

        Args:
            data: Compressed thread data
            data_type: Type of compression ("zstd" or "plain")

        Returns:
            Parsed ZedThread object
        """
        import zstandard

        if data_type == "zstd":
            dctx = zstandard.ZstdDecompressor()
            reader = dctx.stream_reader(io.BytesIO(data))
            json_data = reader.read()
        else:
            json_data = data

        thread_dict = anyenv.load_json(json_data)
        return cls.model_validate(thread_dict)
