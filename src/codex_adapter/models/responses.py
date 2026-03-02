from __future__ import annotations

from typing import Any, Literal

from codex_adapter.models.base import CodexBaseModel
from codex_adapter.models.codex_types import (  # noqa: TC001
    AskForApproval,
    CommandExecutionApprovalDecision,
    FileChangeApprovalDecision,
    ReasoningEffort,
    SandboxPolicy,
    WriteStatus,
)
from codex_adapter.models.misc import (  # noqa: TC001
    AppInfo,
    ConfigLayer,
    ConfigLayerMetadata,
    ConfigRequirements,
    ExperimentalFeature,
    ExternalAgentConfigMigrationItem,
    McpServerStatusEntry,
    ModelData,
    SkillsContainer,
    ThreadData,
    ToolRequestUserInputAnswer,
    Turn,
    TurnData,
)
from codex_adapter.models.thread_item import DynamicToolCallOutputContentItem  # noqa: TC001


class CommandExecutionRequestApprovalResponse(CodexBaseModel):
    """Response for item/commandExecution/requestApproval server request."""

    decision: CommandExecutionApprovalDecision


class FileChangeRequestApprovalResponse(CodexBaseModel):
    """Response for item/fileChange/requestApproval server request."""

    decision: FileChangeApprovalDecision


class ToolRequestUserInputResponse(CodexBaseModel):
    """Response for item/tool/requestUserInput server request."""

    answers: dict[str, ToolRequestUserInputAnswer]


class DynamicToolCallResponse(CodexBaseModel):
    """Response for item/tool/call server request."""

    content_items: list[DynamicToolCallOutputContentItem]
    success: bool


class ThreadReadResponse(CodexBaseModel):
    """Response for thread/read request."""

    thread: ThreadData


class ThreadResponse(CodexBaseModel):
    """Response for thread/start, thread/resume, and thread/fork."""

    thread: ThreadData
    model: str
    model_provider: str
    cwd: str
    approval_policy: AskForApproval
    sandbox: SandboxPolicy
    reasoning_effort: ReasoningEffort | None = None


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


class SkillsListResponse(CodexBaseModel):
    """Response for skills/list request."""

    data: list[SkillsContainer]


class SkillsConfigWriteResponse(CodexBaseModel):
    """Response for skills/config/write request."""


class ModelListResponse(CodexBaseModel):
    """Response for model/list request."""

    data: list[ModelData]
    next_cursor: str | None = None


class CommandExecResponse(CodexBaseModel):
    """Response for command/exec request."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""


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


CancelLoginAccountStatus = Literal["canceled", "notFound"]


class CancelLoginAccountResponse(CodexBaseModel):
    """Response for account/login/cancel request."""

    status: CancelLoginAccountStatus


class GetAccountRateLimitsResponse(CodexBaseModel):
    """Response for account/rateLimits/read request."""

    rate_limits: dict[str, Any]  # RateLimitSnapshot - flexible
    rate_limits_by_limit_id: dict[str, Any] | None = None


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


class ConfigRequirementsReadResponse(CodexBaseModel):
    """Response for configRequirements/read request."""

    requirements: ConfigRequirements | None = None


class AppsListResponse(CodexBaseModel):
    """Response for app/list request."""

    data: list[AppInfo]
    next_cursor: str | None = None


class ExperimentalFeatureListResponse(CodexBaseModel):
    """Response for experimentalFeature/list request."""

    data: list[ExperimentalFeature]
    next_cursor: str | None = None


class FeedbackUploadResponse(CodexBaseModel):
    """Response for feedback/upload request."""

    thread_id: str


class ExternalAgentConfigDetectResponse(CodexBaseModel):
    """Response for externalAgentConfig/detect request."""

    items: list[ExternalAgentConfigMigrationItem]
