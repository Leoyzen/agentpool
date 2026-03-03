"""Pydantic models for Codex JSON-RPC API requests and responses."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from codex_adapter.models.base import CodexBaseModel
from codex_adapter.models.codex_types import (  # noqa: TC001
    ExperimentalFeatureStage,
    ExternalAgentConfigMigrationItemType,
    InputModality,
    McpAuthStatusValue,
    MergeStrategy,
    ModelProvider,
    NetworkApprovalProtocol,
    NetworkPolicyRuleAction,
    PlanType,
    ReasoningEffort,
    SandboxMode,
    SessionSource,
    SkillApprovalDecision,
    SkillScope,
)
from codex_adapter.models.thread_item import ThreadItem  # noqa: TC001
from codex_adapter.models.thread_status import ThreadStatusValue  # noqa: TC001


# Strict validation in tests to catch schema changes, lenient in production

TurnStatusValue = Literal["completed", "interrupted", "failed", "inProgress"]
PlanStepStatus = Literal["pending", "inProgress", "completed"]


class TextPosition(CodexBaseModel):
    """1-based text position."""

    line: int
    column: int


class TextRange(CodexBaseModel):
    """Text range with start and end positions."""

    start: TextPosition
    end: TextPosition


class ClientInfo(CodexBaseModel):
    """Client information for initialization."""

    name: str
    version: str


class ConfigEdit(CodexBaseModel):
    """A single config edit operation."""

    key_path: str
    value: Any
    merge_strategy: MergeStrategy


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

    def to_schema_property(self) -> dict[str, Any]:
        """Convert a Codex user input question to a JSON Schema property.

        Maps question options to enum values, and handles secret/free-text questions.

        Args:
            question: Codex question with optional options list

        Returns:
            JSON Schema property definition
        """
        prop: dict[str, Any] = {"title": self.header or self.id}
        if self.question:
            prop["description"] = self.question

        if self.options and not self.is_other:
            # Question with fixed options -> enum
            prop["type"] = "string"
            prop["enum"] = [opt.label for opt in self.options]
        elif self.options and self.is_other:
            # Options with an "other" free-text fallback -> enum + freeform
            prop["type"] = "string"
            prop["enum"] = [opt.label for opt in self.options]
        else:
            # Free-text question
            prop["type"] = "string"

        if self.is_secret:
            prop["writeOnly"] = True

        return prop


class ToolRequestUserInputAnswer(CodexBaseModel):
    """A user's answer to a request_user_input question."""

    answers: list[str]


class SkillRequestApprovalResponse(CodexBaseModel):
    """Response for skill/requestApproval server request."""

    decision: SkillApprovalDecision


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


class TurnData(CodexBaseModel):
    """Turn data in responses."""

    id: str
    status: TurnStatusValue  # always provided by the server
    thread_id: str | None = None
    items: list[ThreadItem] = Field(default_factory=list)
    error: str | None = None


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


class ExperimentalFeature(CodexBaseModel):
    """An experimental feature."""

    name: str
    stage: ExperimentalFeatureStage
    description: str | None = None


class ExternalAgentConfigMigrationItem(CodexBaseModel):
    """External agent config migration item."""

    item_type: ExternalAgentConfigMigrationItemType
    description: str
    cwd: str | None = None


class TurnPlanStep(CodexBaseModel):
    """A single step in a turn plan."""

    step: str
    status: PlanStepStatus


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
