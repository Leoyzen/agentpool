from __future__ import annotations

from typing import Any, Literal, Self

from codex_adapter.models.base import CodexBaseModel
from codex_adapter.models.codex_types import (  # noqa: TC001
    ApprovalPolicy,
    CollaborationMode,
    CommandExecutionApprovalDecision,
    MergeStrategy,
    Personality,
    ReasoningEffort,
    ReasoningSummary,
    ReviewDelivery,
    SandboxMode,
    ThreadSortKey,
    ThreadSourceKind,
)
from codex_adapter.models.command_action import CommandAction  # noqa: TC001
from codex_adapter.models.input_item import TurnInputItem  # noqa: TC001
from codex_adapter.models.misc import (  # noqa: TC001
    ClientInfo,
    ConfigEdit,
    ExecPolicyAmendment,
    ExternalAgentConfigMigrationItem,
    NetworkApprovalContext,
    NetworkPolicyAmendment,
    ToolRequestUserInputQuestion,
)


LoginType = Literal["apiKey", "chatgpt", "chatgptAuthTokens"]


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
    collaboration_mode: CollaborationMode | None = None


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


class FileChangeRequestApprovalParams(CodexBaseModel):
    """Parameters for item/fileChange/requestApproval server request."""

    thread_id: str
    turn_id: str
    item_id: str
    reason: str | None = None
    grant_root: str | None = None


class ToolRequestUserInputParams(CodexBaseModel):
    """Parameters for item/tool/requestUserInput server request."""

    thread_id: str
    turn_id: str
    item_id: str
    questions: list[ToolRequestUserInputQuestion]


class SkillRequestApprovalParams(CodexBaseModel):
    """Parameters for skill/requestApproval server request."""

    item_id: str
    skill_name: str


class DynamicToolCallParams(CodexBaseModel):
    """Parameters for item/tool/call server request."""

    thread_id: str
    turn_id: str
    call_id: str
    tool: str
    arguments: Any
