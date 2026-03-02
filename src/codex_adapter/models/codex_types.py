"""Codex data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator, Field, Tag

from codex_adapter.models.base import CodexBaseModel


# Type aliases for Codex types
ModelProvider = Literal["openai", "anthropic", "google", "mistral"]
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
ReasoningSummary = Literal["auto", "concise", "detailed", "none"]
ApprovalPolicy = Literal["untrusted", "on-failure", "on-request", "never"]
SandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]
NetworkAccess = Literal["restricted", "enabled"]
Personality = Literal["none", "friendly", "pragmatic"]
TurnStatus = Literal["pending", "inProgress", "completed", "error", "interrupted"]
ItemType = Literal[
    "reasoning",
    "agent_message",
    "command_execution",
    "user_message",
    "file_change",
    "mcp_tool_call",
]
ItemStatus = Literal["pending", "running", "completed", "error"]

# New type aliases
SessionSource = Literal["cli", "vscode", "exec", "appServer", "unknown"]
ThreadSortKey = Literal["created_at", "updated_at"]
ThreadSourceKind = Literal[
    "cli",
    "vscode",
    "exec",
    "appServer",
    "subAgent",
    "subAgentReview",
    "subAgentCompact",
    "subAgentThreadSpawn",
    "subAgentOther",
    "unknown",
]
MessagePhase = Literal["commentary", "final_answer"]
PatchApplyStatus = Literal["inProgress", "completed", "failed", "declined"]
CommandExecutionStatus = Literal["inProgress", "completed", "failed", "declined"]
McpToolCallStatus = Literal["inProgress", "completed", "failed"]
DynamicToolCallStatus = Literal["inProgress", "completed", "failed"]
CollabAgentTool = Literal["spawnAgent", "sendInput", "resumeAgent", "wait", "closeAgent"]
CollabAgentToolCallStatus = Literal["inProgress", "completed", "failed"]
CollabAgentStatus = Literal[
    "pendingInit", "running", "completed", "errored", "shutdown", "notFound"
]
InputModality = Literal["text", "image"]
SkillScope = Literal["user", "repo", "system", "admin"]
McpAuthStatusValue = Literal["Unsupported", "NotAuthenticated", "Authenticated"]
ReviewDelivery = Literal["inline", "detached"]
ThreadActiveFlag = Literal["waitingOnApproval", "waitingOnUserInput"]
CommandExecutionApprovalDecision = Literal["allow", "allowForSession", "deny", "denyForSession"]
FileChangeApprovalDecision = Literal["allow", "allowForSession", "deny", "denyForSession"]
SkillApprovalDecision = Literal["allow", "deny"]
ModelRerouteReason = Literal["rateLimited", "contextWindowExceeded", "other"]
WriteStatus = Literal["ok", "conflict"]
MergeStrategy = Literal["replace", "merge"]
ExperimentalFeatureStage = Literal["alpha", "beta"]
ElicitationAction = Literal["accept", "decline", "cancel"]
NetworkApprovalProtocol = Literal["http", "https", "socks5Tcp", "socks5Udp"]
NetworkPolicyRuleAction = Literal["allow", "deny"]
ExternalAgentConfigMigrationItemType = Literal["AGENTS_MD", "CONFIG", "SKILLS", "MCP_SERVER_CONFIG"]
PlanType = Literal["free", "go", "plus", "pro", "team", "business", "enterprise", "edu", "unknown"]


# ============================================================================
# AskForApproval (tagged union: string literals or {"reject": RejectConfig})
# ============================================================================


class RejectConfig(CodexBaseModel):
    """Fine-grained rejection controls for approval prompts.

    When a field is True, prompts of that category are automatically
    rejected instead of shown to the user.
    """

    sandbox_approval: bool
    rules: bool
    mcp_elicitations: bool


class RejectApprovalPolicy(CodexBaseModel):
    """Approval policy variant with fine-grained rejection controls."""

    reject: RejectConfig


def _ask_for_approval_discriminator(v: Any) -> str:
    if isinstance(v, str):
        return "simple"
    if isinstance(v, dict) and "reject" in v:
        return "reject"
    if isinstance(v, RejectApprovalPolicy):
        return "reject"
    return "simple"


AskForApproval = Annotated[
    Annotated[ApprovalPolicy, Tag("simple")] | Annotated[RejectApprovalPolicy, Tag("reject")],
    Discriminator(_ask_for_approval_discriminator),
]
"""Full AskForApproval type: simple string policy or reject config."""


# ============================================================================
# SandboxPolicy (discriminated union on 'type' field)
# ============================================================================


# Mapping from camelCase type values (v1 protocol) to kebab-case (v2/canonical)
_SANDBOX_TYPE_ALIASES: dict[str, str] = {
    "workspaceWrite": "workspace-write",
    "dangerFullAccess": "danger-full-access",
    "readOnly": "read-only",
    "externalSandbox": "external-sandbox",
}

_READ_ONLY_ACCESS_TYPE_ALIASES: dict[str, str] = {
    "fullAccess": "full-access",
}


def _normalize_sandbox_type(v: Any) -> Any:
    """Normalize camelCase type values to kebab-case for sandbox policies."""
    if isinstance(v, dict) and "type" in v:
        v = dict(v)  # shallow copy to avoid mutating input
        v["type"] = _SANDBOX_TYPE_ALIASES.get(v["type"], v["type"])
        # Also normalize nested read_only_access / readOnlyAccess
        for key in ("read_only_access", "readOnlyAccess", "access"):
            nested = v.get(key)
            if isinstance(nested, dict) and "type" in nested:
                nested = dict(nested)
                nested["type"] = _READ_ONLY_ACCESS_TYPE_ALIASES.get(nested["type"], nested["type"])
                v[key] = nested
    return v


def _sandbox_policy_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        raw_type = v.get("type", "")
        return _SANDBOX_TYPE_ALIASES.get(raw_type, raw_type)
    if isinstance(v, BaseModel):
        return v.type  # type: ignore[return-value]
    return str(v)


def _read_only_access_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        raw_type = v.get("type", "")
        return _READ_ONLY_ACCESS_TYPE_ALIASES.get(raw_type, raw_type)
    if isinstance(v, BaseModel):
        return v.type  # type: ignore[return-value]
    return str(v)


class RestrictedReadOnlyAccess(CodexBaseModel):
    """Restrict reads to an explicit set of roots."""

    type: Literal["restricted"]
    readable_roots: list[str] = Field(default_factory=list)
    include_platform_defaults: bool = True


class FullAccessReadOnlyAccess(CodexBaseModel):
    """Allow unrestricted file reads."""

    type: Literal["full-access", "fullAccess"]


ReadOnlyAccess = Annotated[
    Annotated[RestrictedReadOnlyAccess, Tag("restricted")]
    | Annotated[FullAccessReadOnlyAccess, Tag("full-access")],
    Discriminator(_read_only_access_discriminator),
]


class DangerFullAccessSandboxPolicy(CodexBaseModel):
    """No restrictions whatsoever. Use with caution."""

    type: Literal["danger-full-access", "dangerFullAccess"]


class ReadOnlySandboxPolicy(CodexBaseModel):
    """Read-only access configuration."""

    type: Literal["read-only", "readOnly"]
    access: ReadOnlyAccess | None = None


class ExternalSandboxPolicy(CodexBaseModel):
    """Process is already in an external sandbox."""

    type: Literal["external-sandbox", "externalSandbox"]
    network_access: NetworkAccess = "restricted"


class WorkspaceWriteSandboxPolicy(CodexBaseModel):
    """Grants write access to the workspace directory."""

    type: Literal["workspace-write", "workspaceWrite"]
    writable_roots: list[str] = Field(default_factory=list)
    read_only_access: ReadOnlyAccess | None = None
    network_access: bool = False
    exclude_slash_tmp: bool = False
    exclude_tmpdir_env_var: bool = False


SandboxPolicy = Annotated[
    Annotated[DangerFullAccessSandboxPolicy, Tag("danger-full-access")]
    | Annotated[ReadOnlySandboxPolicy, Tag("read-only")]
    | Annotated[ExternalSandboxPolicy, Tag("external-sandbox")]
    | Annotated[WorkspaceWriteSandboxPolicy, Tag("workspace-write")],
    Discriminator(_sandbox_policy_discriminator),
]
"""Discriminated union for sandbox execution restrictions."""


@dataclass
class CodexTurn:
    """Represents a turn in a Codex conversation."""

    id: str
    thread_id: str
    status: TurnStatus = "pending"
    items: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    usage: dict[str, int] | None = None


@dataclass
class CodexItem:
    """Represents an item (message, tool call, etc.) in a turn."""

    id: str
    type: ItemType
    content: str = ""
    status: ItemStatus = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)


# MCP Server Configuration Types


class StdioMcpServer(BaseModel):
    """MCP server running as a subprocess via stdio transport.

    Example:
        StdioMcpServer(
            command="npx",
            args=["-y", "@openai/codex-shell-tool-mcp"]
        )
    """

    command: str
    args: list[str] = []
    env: dict[str, str] | None = None
    enabled: bool = True


class HttpMcpServer(BaseModel):
    """MCP server accessible via HTTP/SSE transport.

    Example:
        HttpMcpServer(
            url="http://localhost:8000/mcp",
            bearer_token_env_var="MY_MCP_TOKEN"
        )
    """

    url: str
    bearer_token_env_var: str | None = None
    http_headers: dict[str, str] | None = None
    enabled: bool = True


# Union type for any MCP server config
McpServerConfig = StdioMcpServer | HttpMcpServer
