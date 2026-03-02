"""Codex data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel


# Type aliases for Codex types
ModelProvider = Literal["openai", "anthropic", "google", "mistral"]
ReasoningEffort = Literal["low", "medium", "high", "xhigh"]
ReasoningSummary = Literal["auto", "concise", "detailed", "none"]
ApprovalPolicy = Literal["untrusted", "on-failure", "on-request", "never"]
SandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]
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
PlanType = Literal["free", "go", "plus", "pro", "team", "business", "enterprise", "edu", "unknown"]


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
