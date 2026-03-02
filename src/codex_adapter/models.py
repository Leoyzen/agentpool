"""Pydantic models for Codex JSON-RPC API requests and responses."""

from __future__ import annotations

import sys
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from codex_adapter.codex_types import (
    ApprovalPolicy,
    CollabAgentStatus,
    CollabAgentTool,
    CollabAgentToolCallStatus,
    CommandExecutionApprovalDecision,
    CommandExecutionStatus,
    DynamicToolCallStatus,
    ExperimentalFeatureStage,
    ExternalAgentConfigMigrationItemType,
    FileChangeApprovalDecision,
    InputModality,
    McpAuthStatusValue,
    McpToolCallStatus,
    MergeStrategy,
    MessagePhase,
    ModelProvider,
    ModelRerouteReason,
    NetworkApprovalProtocol,
    NetworkPolicyRuleAction,
    PatchApplyStatus,
    Personality,
    PlanType,
    ReasoningEffort,
    ReasoningSummary,
    ReviewDelivery,
    SandboxMode,
    SessionSource,
    SkillApprovalDecision,
    SkillScope,
    ThreadActiveFlag,
    ThreadSortKey,
    ThreadSourceKind,
    WriteStatus,
)


# Strict validation in tests to catch schema changes, lenient in production
IS_DEV = "pytest" in sys.modules

LoginType = Literal["apiKey", "chatgpt", "chatgptAuthTokens"]
TurnStatusValue = Literal["completed", "interrupted", "failed", "inProgress"]
PlanStepStatus = Literal["pending", "inProgress", "completed"]
# ============================================================================
# Base classes with shared configuration
# ============================================================================


class CodexBaseModel(BaseModel):
    """Base model for all Codex API models.

    Provides:
    - Strict validation in tests (forbids extra fields to catch schema changes)
    - Lenient validation in production (ignores extra fields for forward compat)
    - Snake_case Python fields with camelCase JSON aliases
    - Both field names and aliases accepted for parsing (populate_by_name=True)
    """

    model_config = ConfigDict(
        extra="forbid" if IS_DEV else "ignore",
        populate_by_name=True,
        alias_generator=to_camel,
    )


# ============================================================================
# Request parameter models
# ============================================================================


class ClientInfo(CodexBaseModel):
    """Client information for initialization."""

    name: str
    version: str


class InitializeParams(CodexBaseModel):
    """Parameters for initialize request."""

    client_info: ClientInfo

    @classmethod
    def create(cls, name: str, version: str) -> Self:
        return cls(client_info=ClientInfo(name=name, version=version))


class ThreadStartParams(CodexBaseModel):
    """Parameters for thread/start request."""

    cwd: str | None = None
    model: str | None = None
    model_provider: str | None = None
    base_instructions: str | None = None
    developer_instructions: str | None = None
    approval_policy: ApprovalPolicy | None = None
    sandbox: SandboxMode | None = None
    config: dict[str, Any] | None = None
    service_name: str | None = None
    personality: Personality | None = None
    ephemeral: bool | None = None
    experimental_raw_events: bool = False
    persist_extended_history: bool = False


class ThreadResumeParams(CodexBaseModel):
    """Parameters for thread/resume request."""

    thread_id: str
    history: list[dict[str, Any]] | None = None
    path: str | None = None
    cwd: str | None = None
    model: str | None = None
    model_provider: str | None = None
    base_instructions: str | None = None
    developer_instructions: str | None = None
    approval_policy: ApprovalPolicy | None = None
    sandbox: SandboxMode | None = None
    config: dict[str, Any] | None = None
    personality: Personality | None = None
    persist_extended_history: bool = False


class ThreadForkParams(CodexBaseModel):
    """Parameters for thread/fork request."""

    thread_id: str
    path: str | None = None
    cwd: str | None = None
    model: str | None = None
    model_provider: str | None = None
    base_instructions: str | None = None
    developer_instructions: str | None = None
    approval_policy: ApprovalPolicy | None = None
    sandbox: SandboxMode | None = None
    config: dict[str, Any] | None = None
    personality: Personality | None = None
    persist_extended_history: bool = False


class ThreadListParams(CodexBaseModel):
    """Parameters for thread/list request."""

    cursor: str | None = None
    limit: int | None = None
    sort_key: ThreadSortKey | None = None
    model_providers: list[str] | None = None
    source_kinds: list[ThreadSourceKind] | None = None
    archived: bool | None = None
    cwd: str | None = None
    search_term: str | None = None


class ThreadReadParams(CodexBaseModel):
    """Parameters for thread/read request."""

    thread_id: str
    include_turns: bool = False


class ThreadArchiveParams(CodexBaseModel):
    """Parameters for thread/archive request."""

    thread_id: str


class ThreadUnarchiveParams(CodexBaseModel):
    """Parameters for thread/unarchive request."""

    thread_id: str


class ThreadSetNameParams(CodexBaseModel):
    """Parameters for thread/name/set request."""

    thread_id: str
    name: str


class ThreadCompactStartParams(CodexBaseModel):
    """Parameters for thread/compact/start request."""

    thread_id: str


class ThreadRollbackParams(CodexBaseModel):
    """Parameters for thread/rollback request."""

    thread_id: str
    turns: int


class ThreadLoadedListParams(CodexBaseModel):
    """Parameters for thread/loaded/list request."""


class TextInputItem(CodexBaseModel):
    """Text input for a turn."""

    type: Literal["text"] = "text"
    text: str


class LocalImageInputItem(CodexBaseModel):
    """Local image file input for a turn."""

    type: Literal["localImage"] = "localImage"
    path: str


class ImageInputItem(CodexBaseModel):
    """Image URL input for a turn."""

    type: Literal["image"] = "image"
    url: str


class SkillInputItem(CodexBaseModel):
    """Skill input for a turn."""

    type: Literal["skill"] = "skill"
    name: str
    path: str


class MentionInputItem(CodexBaseModel):
    """Mention input for a turn."""

    type: Literal["mention"] = "mention"
    name: str
    path: str


# Discriminated union of input types
TurnInputItem = (
    TextInputItem | LocalImageInputItem | ImageInputItem | SkillInputItem | MentionInputItem
)


class TurnStartParams(CodexBaseModel):
    """Parameters for turn/start request."""

    thread_id: str
    input: list[TurnInputItem]
    model: str | None = None
    effort: ReasoningEffort | None = None
    approval_policy: ApprovalPolicy | None = None
    cwd: str | None = None
    sandbox_policy: dict[str, Any] | None = None  # Sandbox config - flexible structure
    summary: ReasoningSummary | None = None
    output_schema: dict[str, Any] | None = None  # JSON Schema - arbitrary structure
    personality: Personality | None = None
    collaboration_mode: dict[str, Any] | None = None  # CollaborationMode - flexible structure


class TurnSteerParams(CodexBaseModel):
    """Parameters for turn/steer request."""

    thread_id: str
    input: list[TurnInputItem]
    expected_turn_id: str


class TurnInterruptParams(CodexBaseModel):
    """Parameters for turn/interrupt request."""

    thread_id: str
    turn_id: str


class ReviewStartParams(CodexBaseModel):
    """Parameters for review/start request."""

    thread_id: str
    target: dict[str, Any]  # ReviewTarget - discriminated union
    delivery: ReviewDelivery | None = None


class SkillsListParams(CodexBaseModel):
    """Parameters for skills/list request."""

    cwds: list[str] | None = None
    force_reload: bool | None = None
    per_cwd_extra_user_roots: list[dict[str, Any]] | None = None


class SkillsConfigWriteParams(CodexBaseModel):
    """Parameters for skills/config/write request."""

    path: str
    enabled: bool


class CommandExecParams(CodexBaseModel):
    """Parameters for command/exec request."""

    command: list[str]
    cwd: str | None = None
    sandbox_policy: dict[str, Any] | None = None  # Sandbox config - flexible structure
    timeout_ms: int | None = None


class ModelListParams(CodexBaseModel):
    """Parameters for model/list request."""

    cursor: str | None = None
    limit: int | None = None
    include_hidden: bool | None = None


class McpServerOauthLoginParams(CodexBaseModel):
    """Parameters for mcpServer/oauth/login request."""

    name: str
    scopes: list[str] | None = None
    timeout_secs: int | None = None


class ListMcpServerStatusParams(CodexBaseModel):
    """Parameters for mcpServerStatus/list request."""

    cursor: str | None = None
    limit: int | None = None


class AppsListParams(CodexBaseModel):
    """Parameters for app/list request."""

    cursor: str | None = None
    limit: int | None = None
    thread_id: str | None = None
    force_refetch: bool | None = None


class ExperimentalFeatureListParams(CodexBaseModel):
    """Parameters for experimentalFeature/list request."""

    cursor: str | None = None
    limit: int | None = None


class FeedbackUploadParams(CodexBaseModel):
    """Parameters for feedback/upload request."""

    classification: str
    reason: str | None = None
    thread_id: str | None = None
    include_logs: bool = False
    extra_log_files: list[str] | None = None


class ConfigReadParams(CodexBaseModel):
    """Parameters for config/read request."""

    include_layers: bool
    cwd: str | None = None


class ConfigValueWriteParams(CodexBaseModel):
    """Parameters for config/value/write request."""

    key_path: str
    value: Any
    merge_strategy: MergeStrategy
    file_path: str | None = None
    expected_version: str | None = None


class ConfigEdit(CodexBaseModel):
    """A single config edit operation."""

    key_path: str
    value: Any
    merge_strategy: MergeStrategy


class ConfigBatchWriteParams(CodexBaseModel):
    """Parameters for config/batchWrite request."""

    edits: list[ConfigEdit]
    file_path: str | None = None
    expected_version: str | None = None


class GetAccountParams(CodexBaseModel):
    """Parameters for account/read request."""

    refresh_token: bool


class LoginAccountParams(CodexBaseModel):
    """Parameters for account/login/start request.

    This is a discriminated union - use type field.
    """

    type: LoginType
    api_key: str | None = None
    access_token: str | None = None
    chatgpt_account_id: str | None = None
    chatgpt_plan_type: str | None = None


class CancelLoginAccountParams(CodexBaseModel):
    """Parameters for account/login/cancel request."""

    login_id: str


class ExternalAgentConfigDetectParams(CodexBaseModel):
    """Parameters for externalAgentConfig/detect request."""

    include_home: bool | None = None
    cwds: list[str] | None = None


class ExternalAgentConfigImportParams(CodexBaseModel):
    """Parameters for externalAgentConfig/import request."""

    migration_items: list[ExternalAgentConfigMigrationItem]


# ============================================================================
# Server Request models (server -> client callbacks)
# ============================================================================


class NetworkApprovalContext(CodexBaseModel):
    """Network approval context for command approvals."""

    host: str
    protocol: NetworkApprovalProtocol


class NetworkPolicyAmendment(CodexBaseModel):
    """Proposed network policy amendment."""

    host: str
    action: NetworkPolicyRuleAction


class ExecPolicyAmendment(CodexBaseModel):
    """Proposed execpolicy amendment (prefix rule)."""

    command: list[str]


class CommandExecutionRequestApprovalParams(CodexBaseModel):
    """Parameters for item/commandExecution/requestApproval server request."""

    thread_id: str
    turn_id: str
    item_id: str
    approval_id: str | None = None
    reason: str | None = None
    network_approval_context: NetworkApprovalContext | None = None
    command: str | None = None
    cwd: str | None = None
    command_actions: list[CommandAction] | None = None
    additional_permissions: dict[str, Any] | None = None
    proposed_execpolicy_amendment: ExecPolicyAmendment | None = None
    proposed_network_policy_amendments: list[NetworkPolicyAmendment] | None = None
    available_decisions: list[CommandExecutionApprovalDecision] | None = None


class CommandExecutionRequestApprovalResponse(CodexBaseModel):
    """Response for item/commandExecution/requestApproval server request."""

    decision: CommandExecutionApprovalDecision


class FileChangeRequestApprovalParams(CodexBaseModel):
    """Parameters for item/fileChange/requestApproval server request."""

    thread_id: str
    turn_id: str
    item_id: str
    reason: str | None = None
    grant_root: str | None = None


class FileChangeRequestApprovalResponse(CodexBaseModel):
    """Response for item/fileChange/requestApproval server request."""

    decision: FileChangeApprovalDecision


class ToolRequestUserInputOption(CodexBaseModel):
    """A selectable option for a user input question."""

    label: str
    description: str


class ToolRequestUserInputQuestion(CodexBaseModel):
    """A question in a tool request for user input."""

    id: str
    header: str
    question: str
    is_other: bool = False
    is_secret: bool = False
    options: list[ToolRequestUserInputOption] | None = None


class ToolRequestUserInputParams(CodexBaseModel):
    """Parameters for item/tool/requestUserInput server request."""

    thread_id: str
    turn_id: str
    item_id: str
    questions: list[ToolRequestUserInputQuestion]


class ToolRequestUserInputAnswer(CodexBaseModel):
    """A user's answer to a request_user_input question."""

    answers: list[str]


class ToolRequestUserInputResponse(CodexBaseModel):
    """Response for item/tool/requestUserInput server request."""

    answers: dict[str, ToolRequestUserInputAnswer]


class SkillRequestApprovalParams(CodexBaseModel):
    """Parameters for skill/requestApproval server request."""

    item_id: str
    skill_name: str


class SkillRequestApprovalResponse(CodexBaseModel):
    """Response for skill/requestApproval server request."""

    decision: SkillApprovalDecision


class DynamicToolCallParams(CodexBaseModel):
    """Parameters for item/tool/call server request."""

    thread_id: str
    turn_id: str
    call_id: str
    tool: str
    arguments: Any


class DynamicToolCallOutputContentItem(CodexBaseModel):
    """Output content item for dynamic tool call response."""

    type: Literal["inputText", "inputImage"]
    text: str | None = None
    image_url: str | None = None


class DynamicToolCallResponse(CodexBaseModel):
    """Response for item/tool/call server request."""

    content_items: list[DynamicToolCallOutputContentItem]
    success: bool


# ============================================================================
# Response models
# ============================================================================


class GitInfo(CodexBaseModel):
    """Git metadata captured when thread was created."""

    sha: str | None = None
    branch: str | None = None
    origin_url: str | None = None


class TurnStatus(CodexBaseModel):
    """Turn status enumeration."""

    # This is actually an enum in Rust but sent as string
    status: TurnStatusValue


class TurnError(CodexBaseModel):
    """Turn error information."""

    message: str
    codex_error_info: dict[str, Any] | str | None = (
        None  # Error metadata - varied structure (dict or string like "other")
    )
    additional_details: str | None = None


# ============================================================================
# UserInput and dependent types for ThreadItem
# ============================================================================


class ByteRange(CodexBaseModel):
    """Byte range within a UTF-8 text buffer.

    start: Start byte offset (inclusive).
    end: End byte offset (exclusive).
    """

    start: int = Field(..., ge=0)
    end: int = Field(..., ge=0)


class TextElement(CodexBaseModel):
    """Element within text content for rich input markers.

    Used to render or persist rich input markers (e.g., image placeholders)
    across history and resume without mutating the literal text.
    """

    byte_range: ByteRange
    placeholder: str | None = None


class UserInputText(CodexBaseModel):
    """Text user input."""

    type: Literal["text"] = "text"
    text: str
    text_elements: list[TextElement] = Field(default_factory=list)


class UserInputImage(CodexBaseModel):
    """Image URL user input."""

    type: Literal["image"] = "image"
    url: str


class UserInputLocalImage(CodexBaseModel):
    """Local image file user input."""

    type: Literal["local_image"] = "local_image"
    path: str


class UserInputSkill(CodexBaseModel):
    """Skill file user input."""

    type: Literal["skill"] = "skill"
    name: str
    path: str


class UserInputMention(CodexBaseModel):
    """Mention user input."""

    type: Literal["mention"] = "mention"
    name: str
    path: str


# Discriminated union of user input types
UserInput = UserInputText | UserInputImage | UserInputLocalImage | UserInputSkill | UserInputMention


class CommandActionRead(CodexBaseModel):
    """Read command action."""

    type: Literal["read"] = "read"
    command: str
    name: str
    path: str


class CommandActionListFiles(CodexBaseModel):
    """List files command action."""

    type: Literal["listFiles"] = "listFiles"
    command: str
    path: str | None = None


class CommandActionSearch(CodexBaseModel):
    """Search command action."""

    type: Literal["search"] = "search"
    command: str
    query: str | None = None
    path: str | None = None


class CommandActionUnknown(CodexBaseModel):
    """Unknown command action."""

    type: Literal["unknown"] = "unknown"
    command: str


# Discriminated union of command actions
CommandAction = (
    CommandActionRead | CommandActionListFiles | CommandActionSearch | CommandActionUnknown
)


class PatchChangeKind(CodexBaseModel):
    """Kind of file change (nested object in Codex's fileChange item)."""

    # Codex sends 'type' but we use validation_alias to map it
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
    structured_content: dict[str, Any] | list[Any] | str | int | float | bool | None = None


class McpToolCallError(CodexBaseModel):
    """MCP tool call error."""

    message: str


# ============================================================================
# WebSearch types
# ============================================================================


class WebSearchActionSearch(CodexBaseModel):
    """Web search action - search."""

    type: Literal["search"] = "search"
    query: str | None = None
    queries: list[str] | None = None


class WebSearchActionOpenPage(CodexBaseModel):
    """Web search action - open page."""

    type: Literal["openPage"] = "openPage"
    url: str | None = None


class WebSearchActionFindInPage(CodexBaseModel):
    """Web search action - find in page."""

    type: Literal["findInPage"] = "findInPage"
    url: str | None = None
    pattern: str | None = None


class WebSearchActionOther(CodexBaseModel):
    """Web search action - other."""

    type: Literal["other"] = "other"


WebSearchAction = (
    WebSearchActionSearch
    | WebSearchActionOpenPage
    | WebSearchActionFindInPage
    | WebSearchActionOther
)


# ============================================================================
# ThreadItem discriminated union
# ============================================================================


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
    arguments: dict[str, Any] | list[Any] | str | int | float | bool | None = None
    result: McpToolCallResult | None = None
    error: McpToolCallError | None = None
    duration_ms: int | None = None


class ThreadItemDynamicToolCall(CodexBaseModel):
    """Dynamic tool call item."""

    type: Literal["dynamicToolCall"] = "dynamicToolCall"
    id: str
    tool: str
    arguments: Any = None
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


class CollabAgentState(CodexBaseModel):
    """Collab agent state."""

    status: CollabAgentStatus
    message: str | None = None


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


# ============================================================================
# Thread status types
# ============================================================================


class ThreadStatusNotLoaded(CodexBaseModel):
    """Thread status: not loaded."""

    type: Literal["notLoaded"] = "notLoaded"


class ThreadStatusIdle(CodexBaseModel):
    """Thread status: idle."""

    type: Literal["idle"] = "idle"


class ThreadStatusSystemError(CodexBaseModel):
    """Thread status: system error."""

    type: Literal["systemError"] = "systemError"


class ThreadStatusActive(CodexBaseModel):
    """Thread status: active."""

    type: Literal["active"] = "active"
    active_flags: list[ThreadActiveFlag] = Field(default_factory=list)


ThreadStatusValue = (
    ThreadStatusNotLoaded | ThreadStatusIdle | ThreadStatusSystemError | ThreadStatusActive
)


class Turn(CodexBaseModel):
    """Turn data structure."""

    id: str
    items: list[ThreadItem] = Field(default_factory=list)
    status: TurnStatusValue = "inProgress"
    error: TurnError | None = None


class Thread(CodexBaseModel):
    """Thread data structure."""

    id: str
    preview: str = ""
    ephemeral: bool = False
    model_provider: str = "openai"
    created_at: int = 0
    updated_at: int = 0
    status: ThreadStatusValue | None = None
    path: str | None = None
    cwd: str = ""
    cli_version: str = ""
    source: SessionSource = "appServer"
    agent_nickname: str | None = None
    agent_role: str | None = None
    git_info: GitInfo | None = None
    name: str | None = None
    turns: list[Turn] = Field(default_factory=list)


class ThreadData(CodexBaseModel):
    """Thread data in responses."""

    id: str
    preview: str = ""
    ephemeral: bool = False
    model_provider: ModelProvider = "openai"
    created_at: int = 0
    updated_at: int = 0
    status: ThreadStatusValue | None = None
    path: str | None = None
    cwd: str | None = None
    cli_version: str | None = None
    source: str | None = None
    agent_nickname: str | None = None
    agent_role: str | None = None
    git_info: GitInfo | None = None
    name: str | None = None
    turns: list[Turn] = Field(default_factory=list)


class ThreadResponse(CodexBaseModel):
    """Response for thread operations."""

    thread: ThreadData
    model: str | None = None
    model_provider: ModelProvider | None = None
    cwd: str | None = None
    approval_policy: str | None = None
    sandbox: dict[str, Any] | None = None  # Sandbox config - flexible structure
    reasoning_effort: str | None = None


class TurnData(CodexBaseModel):
    """Turn data in responses."""

    id: str
    status: TurnStatusValue  # always provided by the server
    thread_id: str | None = None
    items: list[ThreadItem] = Field(default_factory=list)
    error: str | None = None


class TurnStartResponse(CodexBaseModel):
    """Response for turn/start request."""

    turn: TurnData


class TurnSteerResponse(CodexBaseModel):
    """Response for turn/steer request."""

    turn_id: str


class ReviewStartResponse(CodexBaseModel):
    """Response for review/start request."""

    turn: TurnData
    review_thread_id: str


class ThreadListResponse(CodexBaseModel):
    """Response for thread/list request."""

    data: list[ThreadData]
    next_cursor: str | None = None


class ThreadLoadedListResponse(CodexBaseModel):
    """Response for thread/loaded/list request."""

    data: list[str]


class ThreadRollbackResponse(CodexBaseModel):
    """Response for thread/rollback request."""

    thread: ThreadData
    turns: list[Turn]


class ThreadUnarchiveResponse(CodexBaseModel):
    """Response for thread/unarchive request."""

    thread: ThreadData


# ============================================================================
# Skills models
# ============================================================================


class SkillInterface(CodexBaseModel):
    """Skill interface metadata."""

    display_name: str | None = None
    short_description: str | None = None
    icon_small: str | None = None
    icon_large: str | None = None
    brand_color: str | None = None
    default_prompt: str | None = None


class SkillToolDependency(CodexBaseModel):
    """Skill tool dependency."""

    type: str
    value: str
    description: str | None = None
    transport: str | None = None
    command: str | None = None
    url: str | None = None


class SkillDependencies(CodexBaseModel):
    """Skill dependencies."""

    tools: list[SkillToolDependency] = Field(default_factory=list)


class SkillData(CodexBaseModel):
    """A single skill definition (SkillMetadata in upstream)."""

    name: str
    description: str | None = None
    short_description: str | None = None
    interface: SkillInterface | None = None
    dependencies: SkillDependencies | None = None
    path: str | None = None
    scope: SkillScope | None = None
    enabled: bool | None = None


class SkillErrorInfo(CodexBaseModel):
    """Skill error information."""

    path: str
    message: str


class SkillsContainer(CodexBaseModel):
    """Container for skills with cwd (SkillsListEntry in upstream)."""

    cwd: str
    skills: list[SkillData]
    errors: list[SkillErrorInfo] = Field(default_factory=list)


class SkillsListResponse(CodexBaseModel):
    """Response for skills/list request."""

    data: list[SkillsContainer]


class SkillsConfigWriteResponse(CodexBaseModel):
    """Response for skills/config/write request."""


# ============================================================================
# Model models
# ============================================================================


class ReasoningEffortOption(CodexBaseModel):
    """A reasoning effort option with metadata."""

    reasoning_effort: ReasoningEffort
    description: str | None = None


class ModelData(CodexBaseModel):
    """A single model definition."""

    id: str
    model: str
    upgrade: str | None = None
    display_name: str | None = None
    description: str | None = None
    hidden: bool = False
    is_default: bool = False
    supported_reasoning_efforts: list[ReasoningEffortOption] | None = None
    default_reasoning_effort: ReasoningEffort | None = None
    input_modalities: list[InputModality] | None = None
    supports_personality: bool = False


class ModelListResponse(CodexBaseModel):
    """Response for model/list request."""

    data: list[ModelData]
    next_cursor: str | None = None


# ============================================================================
# Command exec models
# ============================================================================


class CommandExecResponse(CodexBaseModel):
    """Response for command/exec request."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""


# ============================================================================
# MCP server status models
# ============================================================================


class McpTool(CodexBaseModel):
    """Tool exposed by an MCP server."""

    name: str
    description: str | None = None


class McpResource(CodexBaseModel):
    """Resource exposed by an MCP server."""

    uri: str
    name: str | None = None
    description: str | None = None
    mime_type: str | None = None


class McpResourceTemplate(CodexBaseModel):
    """Resource template exposed by an MCP server."""

    uri_template: str
    name: str | None = None
    description: str | None = None
    mime_type: str | None = None


class McpServerStatusEntry(CodexBaseModel):
    """Status of a single MCP server."""

    name: str
    tools: dict[str, McpTool] = Field(default_factory=dict)
    resources: list[McpResource] = Field(default_factory=list)
    resource_templates: list[McpResourceTemplate] = Field(default_factory=list)
    auth_status: McpAuthStatusValue = "Unsupported"


class ListMcpServerStatusResponse(CodexBaseModel):
    """Response for mcpServerStatus/list request."""

    data: list[McpServerStatusEntry]
    next_cursor: str | None = None


class McpServerOauthLoginResponse(CodexBaseModel):
    """Response for mcpServer/oauth/login request."""

    authorization_url: str


class McpServerRefreshResponse(CodexBaseModel):
    """Response for config/mcpServer/reload request."""


# ============================================================================
# Account models
# ============================================================================


class GetAccountResponse(CodexBaseModel):
    """Response for account/read request."""

    account: dict[str, Any] | None = None  # Account enum - flexible
    requires_openai_auth: bool = False


class LoginAccountResponse(CodexBaseModel):
    """Response for account/login/start request."""

    type: Literal["apiKey", "chatgpt", "chatgptAuthTokens"]
    login_id: str | None = None
    auth_url: str | None = None


class CancelLoginAccountResponse(CodexBaseModel):
    """Response for account/login/cancel request."""

    status: str


class GetAccountRateLimitsResponse(CodexBaseModel):
    """Response for account/rateLimits/read request."""

    rate_limits: dict[str, Any]  # RateLimitSnapshot - flexible
    rate_limits_by_limit_id: dict[str, Any] | None = None


# ============================================================================
# Config models
# ============================================================================


class ConfigLayerMetadata(CodexBaseModel):
    """Config layer metadata."""

    source: str
    path: str | None = None


class ConfigLayer(CodexBaseModel):
    """A single config layer."""

    source: str
    path: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class ConfigReadResponse(CodexBaseModel):
    """Response for config/read request."""

    config: dict[str, Any]
    origins: dict[str, ConfigLayerMetadata] | None = None
    layers: list[ConfigLayer] | None = None


class ConfigWriteResponse(CodexBaseModel):
    """Response for config/value/write and config/batchWrite requests."""

    status: WriteStatus
    version: str
    file_path: str
    overridden_metadata: dict[str, Any] | None = None


class NetworkRequirements(CodexBaseModel):
    """Network requirements configuration."""

    enabled: bool | None = None
    http_port: int | None = None
    socks_port: int | None = None
    allow_upstream_proxy: bool | None = None
    dangerously_allow_non_loopback_proxy: bool | None = None
    dangerously_allow_non_loopback_admin: bool | None = None
    dangerously_allow_all_unix_sockets: bool | None = None
    allowed_domains: list[str] | None = None
    denied_domains: list[str] | None = None
    allow_unix_sockets: list[str] | None = None
    allow_local_binding: bool | None = None


class ConfigRequirements(CodexBaseModel):
    """Configuration requirements (from requirements.toml / MDM)."""

    allowed_approval_policies: list[Any] | None = None  # AskForApproval tagged union
    allowed_sandbox_modes: list[SandboxMode] | None = None
    allowed_web_search_modes: list[str] | None = None
    enforce_residency: str | None = None
    network: NetworkRequirements | None = None


class ConfigRequirementsReadResponse(CodexBaseModel):
    """Response for configRequirements/read request."""

    requirements: ConfigRequirements | None = None


# ============================================================================
# Apps models
# ============================================================================


class AppBranding(CodexBaseModel):
    """App branding information."""

    primary_color: str | None = None
    icon: str | None = None


class AppReview(CodexBaseModel):
    """App review status."""

    status: str


class AppScreenshot(CodexBaseModel):
    """App screenshot information."""

    url: str | None = None
    file_id: str | None = None
    user_prompt: str


class AppMetadata(CodexBaseModel):
    """App metadata information."""

    review: AppReview | None = None
    categories: list[str] | None = None
    sub_categories: list[str] | None = None
    seo_description: str | None = None
    screenshots: list[AppScreenshot] | None = None
    developer: str | None = None
    version: str | None = None
    version_id: str | None = None
    version_notes: str | None = None
    first_party_type: str | None = None
    first_party_requires_install: bool | None = None
    show_in_composer_when_unlinked: bool | None = None


class AppInfo(CodexBaseModel):
    """App information."""

    id: str
    name: str
    description: str | None = None
    logo_url: str | None = None
    logo_url_dark: str | None = None
    distribution_channel: str | None = None
    branding: AppBranding | None = None
    app_metadata: AppMetadata | None = None
    labels: dict[str, str] | None = None
    install_url: str | None = None
    is_accessible: bool = False
    is_enabled: bool = True


class AppsListResponse(CodexBaseModel):
    """Response for app/list request."""

    data: list[AppInfo]
    next_cursor: str | None = None


# ============================================================================
# Experimental feature models
# ============================================================================


class ExperimentalFeature(CodexBaseModel):
    """An experimental feature."""

    name: str
    stage: ExperimentalFeatureStage
    description: str | None = None


class ExperimentalFeatureListResponse(CodexBaseModel):
    """Response for experimentalFeature/list request."""

    data: list[ExperimentalFeature]
    next_cursor: str | None = None


# ============================================================================
# Feedback models
# ============================================================================


class FeedbackUploadResponse(CodexBaseModel):
    """Response for feedback/upload request."""

    thread_id: str


# ============================================================================
# External agent config models
# ============================================================================


class ExternalAgentConfigMigrationItem(CodexBaseModel):
    """External agent config migration item."""

    item_type: ExternalAgentConfigMigrationItemType
    description: str
    cwd: str | None = None


class ExternalAgentConfigDetectResponse(CodexBaseModel):
    """Response for externalAgentConfig/detect request."""

    items: list[ExternalAgentConfigMigrationItem]


# ============================================================================
# JSON-RPC message models
# ============================================================================


class JsonRpcRequest(CodexBaseModel):
    """JSON-RPC 2.0 request message."""

    jsonrpc: Literal["2.0"] = "2.0"
    id: int
    method: str
    params: dict[str, Any] = Field(default_factory=dict)  # Method-specific params


class JsonRpcError(CodexBaseModel):
    """JSON-RPC 2.0 error object."""

    code: int
    message: str
    data: Any = None


class JsonRpcResponse(CodexBaseModel):
    """JSON-RPC 2.0 response message."""

    jsonrpc: Literal["2.0"] = "2.0"
    id: int
    result: Any = None
    error: JsonRpcError | None = None


class JsonRpcNotification(CodexBaseModel):
    """JSON-RPC 2.0 notification message (no id)."""

    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: dict[str, Any] | None = None  # Event-specific params


# ============================================================================
# Event payload models - exact match to app-server-protocol v2
# ============================================================================


class TokenUsageBreakdown(CodexBaseModel):
    """Token usage breakdown."""

    total_tokens: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int = 0


class ThreadTokenUsage(CodexBaseModel):
    """Thread token usage information."""

    total: TokenUsageBreakdown
    last: TokenUsageBreakdown
    model_context_window: int | None = None


class Usage(CodexBaseModel):
    """Simple token usage (legacy)."""

    input_tokens: int
    cached_input_tokens: int
    output_tokens: int


# Thread lifecycle notifications


class ThreadStartedData(CodexBaseModel):
    """Payload for thread/started notification (V2 protocol)."""

    thread: Thread
    thread_id: str | None = None


class ThreadStatusChangedData(CodexBaseModel):
    """Payload for thread/status/changed notification."""

    thread_id: str
    status: ThreadStatusValue


class ThreadArchivedData(CodexBaseModel):
    """Payload for thread/archived notification."""

    thread_id: str


class ThreadUnarchivedData(CodexBaseModel):
    """Payload for thread/unarchived notification."""

    thread_id: str


class ThreadNameUpdatedData(CodexBaseModel):
    """Payload for thread/name/updated notification."""

    thread_id: str
    thread_name: str | None = None


class ThreadTokenUsageUpdatedData(CodexBaseModel):
    """Payload for thread/tokenUsage/updated notification (V2 protocol)."""

    thread_id: str
    turn_id: str
    token_usage: ThreadTokenUsage


class ThreadCompactedData(CodexBaseModel):
    """Payload for thread/compacted notification."""

    thread_id: str
    turn_id: str | None = None


# Turn lifecycle notifications


class TurnStartedData(CodexBaseModel):
    """Payload for turn/started notification (V2 protocol)."""

    thread_id: str
    turn: Turn


class TurnCompletedData(CodexBaseModel):
    """Payload for turn/completed notification (V2 protocol)."""

    thread_id: str
    turn: Turn


class TurnErrorData(CodexBaseModel):
    """Payload for turn/error notification."""

    thread_id: str
    turn_id: str
    error: str


class TurnDiffUpdatedData(CodexBaseModel):
    """Payload for turn/diff/updated notification."""

    thread_id: str
    turn_id: str
    diff: str


class TurnPlanStep(CodexBaseModel):
    """A single step in a turn plan."""

    step: str
    status: PlanStepStatus


class TurnPlanUpdatedData(CodexBaseModel):
    """Payload for turn/plan/updated notification."""

    thread_id: str
    turn_id: str
    explanation: str | None = None
    plan: list[TurnPlanStep]


# Item lifecycle notifications


class ItemStartedData(CodexBaseModel):
    """Payload for item/started notification (V2 protocol)."""

    thread_id: str
    turn_id: str
    item: ThreadItem


class ItemCompletedData(CodexBaseModel):
    """Payload for item/completed notification (V2 protocol)."""

    thread_id: str
    turn_id: str
    item: ThreadItem


class RawResponseItemCompletedData(CodexBaseModel):
    """Payload for rawResponseItem/completed notification."""

    thread_id: str
    turn_id: str
    item: ThreadItem


# Item delta notifications


class AgentMessageDeltaData(CodexBaseModel):
    """Payload for item/agentMessage/delta notification."""

    thread_id: str
    turn_id: str
    item_id: str
    delta: str


class PlanDeltaData(CodexBaseModel):
    """Payload for item/plan/delta notification."""

    thread_id: str
    turn_id: str
    item_id: str
    delta: str


class ReasoningTextDeltaData(CodexBaseModel):
    """Payload for item/reasoning/textDelta notification."""

    thread_id: str
    turn_id: str
    item_id: str
    delta: str
    content_index: int


class ReasoningSummaryTextDeltaData(CodexBaseModel):
    """Payload for item/reasoning/summaryTextDelta notification."""

    thread_id: str
    turn_id: str
    item_id: str
    delta: str
    summary_index: int


class ReasoningSummaryPartAddedData(CodexBaseModel):
    """Payload for item/reasoning/summaryPartAdded notification."""

    thread_id: str
    turn_id: str
    item_id: str
    summary_index: int


class CommandExecutionOutputDeltaData(CodexBaseModel):
    """Payload for item/commandExecution/outputDelta notification."""

    thread_id: str
    turn_id: str
    item_id: str
    delta: str


class CommandExecutionTerminalInteractionData(CodexBaseModel):
    """Payload for item/commandExecution/terminalInteraction notification."""

    thread_id: str
    turn_id: str
    item_id: str
    process_id: str
    stdin: str


class FileChangeOutputDeltaData(CodexBaseModel):
    """Payload for item/fileChange/outputDelta notification."""

    thread_id: str
    turn_id: str
    item_id: str
    delta: str


class McpToolCallProgressData(CodexBaseModel):
    """Payload for item/mcpToolCall/progress notification."""

    thread_id: str
    turn_id: str
    item_id: str
    message: str


# MCP/Account/System notifications


class McpServerOAuthLoginCompletedData(CodexBaseModel):
    """Payload for mcpServer/oauthLogin/completed notification."""

    name: str
    success: bool
    error: str | None = None


class AccountUpdatedData(CodexBaseModel):
    """Payload for account/updated notification."""

    auth_mode: str | None = None


class RateLimitWindow(CodexBaseModel):
    """Rate limit window information."""

    used_percent: int
    window_duration_mins: int | None = None
    resets_at: int | None = None


class CreditsSnapshot(CodexBaseModel):
    """Credits snapshot information."""

    has_credits: bool
    unlimited: bool
    balance: str | None = None


class RateLimitSnapshot(CodexBaseModel):
    """Rate limit snapshot."""

    limit_id: str | None = None
    limit_name: str | None = None
    primary: RateLimitWindow | None = None
    secondary: RateLimitWindow | None = None
    credits: CreditsSnapshot | None = None
    plan_type: PlanType | None = None


class AccountRateLimitsUpdatedData(CodexBaseModel):
    """Payload for account/rateLimits/updated notification."""

    rate_limits: RateLimitSnapshot


class AccountLoginCompletedData(CodexBaseModel):
    """Payload for account/login/completed notification."""

    login_id: str | None = None
    success: bool
    error: str | None = None


class AuthStatusChangeData(CodexBaseModel):
    """Payload for authStatusChange notification (legacy v1)."""

    status: str


class LoginChatGptCompleteData(CodexBaseModel):
    """Payload for loginChatGptComplete notification (legacy v1)."""

    success: bool


class SessionConfiguredData(CodexBaseModel):
    """Payload for sessionConfigured notification."""

    config: dict[str, Any]  # Session config - flexible structure


class DeprecationNoticeData(CodexBaseModel):
    """Payload for deprecationNotice notification."""

    summary: str
    details: str | None = None


class WindowsWorldWritableWarningData(CodexBaseModel):
    """Payload for windows/worldWritableWarning notification."""

    sample_paths: list[str]
    extra_count: int
    failed_scan: bool


class ErrorEventData(CodexBaseModel):
    """Payload for error event."""

    error: TurnError
    will_retry: bool
    thread_id: str
    turn_id: str


class ModelReroutedData(CodexBaseModel):
    """Payload for model/rerouted notification."""

    thread_id: str
    turn_id: str
    from_model: str
    to_model: str
    reason: ModelRerouteReason


class ConfigWarningData(CodexBaseModel):
    """Payload for configWarning notification."""

    summary: str
    details: str | None = None
    path: str | None = None
    range: dict[str, Any] | None = None


class AppListUpdatedData(CodexBaseModel):
    """Payload for app/list/updated notification."""

    data: list[AppInfo]


class ContextCompactedData(CodexBaseModel):
    """Payload for thread/compacted/v2 notification."""

    thread_id: str
    turn_id: str | None = None


class ServerRequestResolvedData(CodexBaseModel):
    """Payload for serverRequest/resolved notification."""

    thread_id: str
    request_id: int | str


# Union type of all event data
EventData = (
    # Thread lifecycle
    ThreadStartedData
    | ThreadStatusChangedData
    | ThreadArchivedData
    | ThreadUnarchivedData
    | ThreadNameUpdatedData
    | ThreadTokenUsageUpdatedData
    | ThreadCompactedData
    # Turn lifecycle
    | TurnStartedData
    | TurnCompletedData
    | TurnErrorData
    | TurnDiffUpdatedData
    | TurnPlanUpdatedData
    # Item lifecycle
    | ItemStartedData
    | ItemCompletedData
    | RawResponseItemCompletedData
    # Item deltas - agent messages
    | AgentMessageDeltaData
    # Item deltas - plan
    | PlanDeltaData
    # Item deltas - reasoning
    | ReasoningTextDeltaData
    | ReasoningSummaryTextDeltaData
    | ReasoningSummaryPartAddedData
    # Item deltas - command execution
    | CommandExecutionOutputDeltaData
    | CommandExecutionTerminalInteractionData
    # Item deltas - file changes
    | FileChangeOutputDeltaData
    # Item deltas - MCP tool calls
    | McpToolCallProgressData
    # MCP OAuth
    | McpServerOAuthLoginCompletedData
    # Account/Auth events
    | AccountUpdatedData
    | AccountRateLimitsUpdatedData
    | AccountLoginCompletedData
    | AuthStatusChangeData
    | LoginChatGptCompleteData
    # System events
    | SessionConfiguredData
    | DeprecationNoticeData
    | WindowsWorldWritableWarningData
    # Error events
    | ErrorEventData
    # New events
    | ModelReroutedData
    | ConfigWarningData
    | AppListUpdatedData
    | ContextCompactedData
    | ServerRequestResolvedData
)
