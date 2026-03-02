"""Codex event types for streaming.

Uses discriminated unions with TypeAdapter for type-safe event parsing.
Each event type is a proper BaseModel with the event_type as the discriminator.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter

from codex_adapter.models.base import CodexBaseModel
from codex_adapter.models.event_data import (  # noqa: TC001
    AccountLoginCompletedData,
    AccountRateLimitsUpdatedData,
    AccountUpdatedData,
    AgentMessageDeltaData,
    AppListUpdatedData,
    AuthStatusChangeData,
    CommandExecutionOutputDeltaData,
    CommandExecutionTerminalInteractionData,
    ConfigWarningData,
    ContextCompactedData,
    DeprecationNoticeData,
    ErrorEventData,
    FileChangeOutputDeltaData,
    ItemCompletedData,
    ItemStartedData,
    LoginChatGptCompleteData,
    McpServerOAuthLoginCompletedData,
    McpToolCallProgressData,
    ModelReroutedData,
    PlanDeltaData,
    RawResponseItemCompletedData,
    ReasoningSummaryPartAddedData,
    ReasoningSummaryTextDeltaData,
    ReasoningTextDeltaData,
    ServerRequestResolvedData,
    SessionConfiguredData,
    ThreadArchivedData,
    ThreadCompactedData,
    ThreadNameUpdatedData,
    ThreadStartedData,
    ThreadStatusChangedData,
    ThreadTokenUsageUpdatedData,
    ThreadUnarchivedData,
    TurnCompletedData,
    TurnDiffUpdatedData,
    TurnErrorData,
    TurnPlanUpdatedData,
    TurnStartedData,
    WindowsWorldWritableWarningData,
)


class ErrorEvent(CodexBaseModel):
    """Error event from the Codex server."""

    event_type: Literal["error"] = "error"
    data: ErrorEventData


# ============================================================================
# Thread lifecycle events
# ============================================================================


class ThreadStartedEvent(CodexBaseModel):
    """Thread started event."""

    event_type: Literal["thread/started"] = "thread/started"
    data: ThreadStartedData


class ThreadStatusChangedEvent(CodexBaseModel):
    """Thread status changed event."""

    event_type: Literal["thread/status/changed"] = "thread/status/changed"
    data: ThreadStatusChangedData


class ThreadArchivedEvent(CodexBaseModel):
    """Thread archived event."""

    event_type: Literal["thread/archived"] = "thread/archived"
    data: ThreadArchivedData


class ThreadUnarchivedEvent(CodexBaseModel):
    """Thread unarchived event."""

    event_type: Literal["thread/unarchived"] = "thread/unarchived"
    data: ThreadUnarchivedData


class ThreadNameUpdatedEvent(CodexBaseModel):
    """Thread name updated event."""

    event_type: Literal["thread/name/updated"] = "thread/name/updated"
    data: ThreadNameUpdatedData


class ThreadTokenUsageUpdatedEvent(CodexBaseModel):
    """Thread token usage updated event."""

    event_type: Literal["thread/tokenUsage/updated"] = "thread/tokenUsage/updated"
    data: ThreadTokenUsageUpdatedData


class ThreadCompactedEvent(CodexBaseModel):
    """Thread compacted event."""

    event_type: Literal["thread/compacted"] = "thread/compacted"
    data: ThreadCompactedData


# ============================================================================
# Turn lifecycle events
# ============================================================================


class TurnStartedEvent(CodexBaseModel):
    """Turn started event."""

    event_type: Literal["turn/started"] = "turn/started"
    data: TurnStartedData


class TurnCompletedEvent(CodexBaseModel):
    """Turn completed event."""

    event_type: Literal["turn/completed"] = "turn/completed"
    data: TurnCompletedData


class TurnErrorEvent(CodexBaseModel):
    """Turn error event."""

    event_type: Literal["turn/error"] = "turn/error"
    data: TurnErrorData


class TurnDiffUpdatedEvent(CodexBaseModel):
    """Turn diff updated event."""

    event_type: Literal["turn/diff/updated"] = "turn/diff/updated"
    data: TurnDiffUpdatedData


class TurnPlanUpdatedEvent(CodexBaseModel):
    """Turn plan updated event."""

    event_type: Literal["turn/plan/updated"] = "turn/plan/updated"
    data: TurnPlanUpdatedData


# ============================================================================
# Item lifecycle events
# ============================================================================


class ItemStartedEvent(CodexBaseModel):
    """Item started event."""

    event_type: Literal["item/started"] = "item/started"
    data: ItemStartedData


class ItemCompletedEvent(CodexBaseModel):
    """Item completed event."""

    event_type: Literal["item/completed"] = "item/completed"
    data: ItemCompletedData


class RawResponseItemCompletedEvent(CodexBaseModel):
    """Raw response item completed event."""

    event_type: Literal["rawResponseItem/completed"] = "rawResponseItem/completed"
    data: RawResponseItemCompletedData


# ============================================================================
# Item delta events - Agent messages
# ============================================================================


class AgentMessageDeltaEvent(CodexBaseModel):
    """Agent message delta event (streaming text)."""

    event_type: Literal["item/agentMessage/delta"] = "item/agentMessage/delta"
    data: AgentMessageDeltaData


# ============================================================================
# Item delta events - Plan
# ============================================================================


class PlanDeltaEvent(CodexBaseModel):
    """Plan delta event (streaming plan text)."""

    event_type: Literal["item/plan/delta"] = "item/plan/delta"
    data: PlanDeltaData


# ============================================================================
# Item delta events - Reasoning
# ============================================================================


class ReasoningSummaryTextDeltaEvent(CodexBaseModel):
    """Reasoning summary text delta event."""

    event_type: Literal["item/reasoning/summaryTextDelta"] = "item/reasoning/summaryTextDelta"
    data: ReasoningSummaryTextDeltaData


class ReasoningSummaryPartAddedEvent(CodexBaseModel):
    """Reasoning summary part added event."""

    event_type: Literal["item/reasoning/summaryPartAdded"] = "item/reasoning/summaryPartAdded"
    data: ReasoningSummaryPartAddedData


class ReasoningTextDeltaEvent(CodexBaseModel):
    """Reasoning text delta event."""

    event_type: Literal["item/reasoning/textDelta"] = "item/reasoning/textDelta"
    data: ReasoningTextDeltaData


# ============================================================================
# Item delta events - Command execution
# ============================================================================


class CommandExecutionOutputDeltaEvent(CodexBaseModel):
    """Command execution output delta event."""

    event_type: Literal["item/commandExecution/outputDelta"] = "item/commandExecution/outputDelta"
    data: CommandExecutionOutputDeltaData


class CommandExecutionTerminalInteractionEvent(CodexBaseModel):
    """Command execution terminal interaction event."""

    event_type: Literal["item/commandExecution/terminalInteraction"] = (
        "item/commandExecution/terminalInteraction"
    )
    data: CommandExecutionTerminalInteractionData


# ============================================================================
# Item delta events - File changes
# ============================================================================


class FileChangeOutputDeltaEvent(CodexBaseModel):
    """File change output delta event."""

    event_type: Literal["item/fileChange/outputDelta"] = "item/fileChange/outputDelta"
    data: FileChangeOutputDeltaData


# ============================================================================
# Item delta events - MCP tool calls
# ============================================================================


class McpToolCallProgressEvent(CodexBaseModel):
    """MCP tool call progress event."""

    event_type: Literal["item/mcpToolCall/progress"] = "item/mcpToolCall/progress"
    data: McpToolCallProgressData


# ============================================================================
# MCP OAuth events
# ============================================================================


class McpServerOAuthLoginCompletedEvent(CodexBaseModel):
    """MCP server OAuth login completed event."""

    event_type: Literal["mcpServer/oauthLogin/completed"] = "mcpServer/oauthLogin/completed"
    data: McpServerOAuthLoginCompletedData


# ============================================================================
# Account/Auth events
# ============================================================================


class AccountUpdatedEvent(CodexBaseModel):
    """Account updated event."""

    event_type: Literal["account/updated"] = "account/updated"
    data: AccountUpdatedData


class AccountRateLimitsUpdatedEvent(CodexBaseModel):
    """Account rate limits updated event."""

    event_type: Literal["account/rateLimits/updated"] = "account/rateLimits/updated"
    data: AccountRateLimitsUpdatedData


class AccountLoginCompletedEvent(CodexBaseModel):
    """Account login completed event."""

    event_type: Literal["account/login/completed"] = "account/login/completed"
    data: AccountLoginCompletedData


class AuthStatusChangeEvent(CodexBaseModel):
    """Auth status change event (legacy v1)."""

    event_type: Literal["authStatusChange"] = "authStatusChange"
    data: AuthStatusChangeData


class LoginChatGptCompleteEvent(CodexBaseModel):
    """Login ChatGPT complete event (legacy v1)."""

    event_type: Literal["loginChatGptComplete"] = "loginChatGptComplete"
    data: LoginChatGptCompleteData


# ============================================================================
# System events
# ============================================================================


class SessionConfiguredEvent(CodexBaseModel):
    """Session configured event."""

    event_type: Literal["sessionConfigured"] = "sessionConfigured"
    data: SessionConfiguredData


class DeprecationNoticeEvent(CodexBaseModel):
    """Deprecation notice event."""

    event_type: Literal["deprecationNotice"] = "deprecationNotice"
    data: DeprecationNoticeData


class WindowsWorldWritableWarningEvent(CodexBaseModel):
    """Windows world writable warning event."""

    event_type: Literal["windows/worldWritableWarning"] = "windows/worldWritableWarning"
    data: WindowsWorldWritableWarningData


# ============================================================================
# New events
# ============================================================================


class ModelReroutedEvent(CodexBaseModel):
    """Model rerouted event."""

    event_type: Literal["model/rerouted"] = "model/rerouted"
    data: ModelReroutedData


class ConfigWarningEvent(CodexBaseModel):
    """Config warning event."""

    event_type: Literal["configWarning"] = "configWarning"
    data: ConfigWarningData


class AppListUpdatedEvent(CodexBaseModel):
    """App list updated event."""

    event_type: Literal["app/list/updated"] = "app/list/updated"
    data: AppListUpdatedData


class ContextCompactedEvent(CodexBaseModel):
    """Context compacted event (alias for ThreadCompactedEvent with turnId)."""

    event_type: Literal["thread/compacted/v2"] = "thread/compacted/v2"
    data: ContextCompactedData


class ServerRequestResolvedEvent(CodexBaseModel):
    """Server request resolved event."""

    event_type: Literal["serverRequest/resolved"] = "serverRequest/resolved"
    data: ServerRequestResolvedData


# ============================================================================
# Discriminated union of all event types
# ============================================================================


CodexEvent = Annotated[
    # Error events
    ErrorEvent
    # Thread lifecycle
    | ThreadStartedEvent
    | ThreadStatusChangedEvent
    | ThreadArchivedEvent
    | ThreadUnarchivedEvent
    | ThreadNameUpdatedEvent
    | ThreadTokenUsageUpdatedEvent
    | ThreadCompactedEvent
    # Turn lifecycle
    | TurnStartedEvent
    | TurnCompletedEvent
    | TurnErrorEvent
    | TurnDiffUpdatedEvent
    | TurnPlanUpdatedEvent
    # Item lifecycle
    | ItemStartedEvent
    | ItemCompletedEvent
    | RawResponseItemCompletedEvent
    # Item deltas - agent messages
    | AgentMessageDeltaEvent
    # Item deltas - plan
    | PlanDeltaEvent
    # Item deltas - reasoning
    | ReasoningSummaryTextDeltaEvent
    | ReasoningSummaryPartAddedEvent
    | ReasoningTextDeltaEvent
    # Item deltas - command execution
    | CommandExecutionOutputDeltaEvent
    | CommandExecutionTerminalInteractionEvent
    # Item deltas - file changes
    | FileChangeOutputDeltaEvent
    # Item deltas - MCP tool calls
    | McpToolCallProgressEvent
    # MCP OAuth
    | McpServerOAuthLoginCompletedEvent
    # Account/Auth events
    | AccountUpdatedEvent
    | AccountRateLimitsUpdatedEvent
    | AccountLoginCompletedEvent
    | AuthStatusChangeEvent
    | LoginChatGptCompleteEvent
    # System events
    | SessionConfiguredEvent
    | DeprecationNoticeEvent
    | WindowsWorldWritableWarningEvent
    # New events
    | ModelReroutedEvent
    | ConfigWarningEvent
    | AppListUpdatedEvent
    | ContextCompactedEvent
    | ServerRequestResolvedEvent,
    Field(discriminator="event_type"),
]


# TypeAdapter for parsing events
_codex_event_adapter: TypeAdapter[CodexEvent] = TypeAdapter(CodexEvent)


# ============================================================================
# Event type literals (for external use)
# ============================================================================


EventType = Literal[
    # Error events
    "error",
    # Thread lifecycle
    "thread/started",
    "thread/status/changed",
    "thread/archived",
    "thread/unarchived",
    "thread/name/updated",
    "thread/tokenUsage/updated",
    "thread/compacted",
    # Turn lifecycle
    "turn/started",
    "turn/completed",
    "turn/error",
    "turn/diff/updated",
    "turn/plan/updated",
    # Item lifecycle
    "item/started",
    "item/completed",
    "rawResponseItem/completed",
    # Item deltas - agent messages
    "item/agentMessage/delta",
    # Item deltas - plan
    "item/plan/delta",
    # Item deltas - reasoning
    "item/reasoning/summaryTextDelta",
    "item/reasoning/summaryPartAdded",
    "item/reasoning/textDelta",
    # Item deltas - command execution
    "item/commandExecution/outputDelta",
    "item/commandExecution/terminalInteraction",
    # Item deltas - file changes
    "item/fileChange/outputDelta",
    # Item deltas - MCP tool calls
    "item/mcpToolCall/progress",
    # MCP OAuth
    "mcpServer/oauthLogin/completed",
    # Account/Auth events
    "account/updated",
    "account/rateLimits/updated",
    "account/login/completed",
    "authStatusChange",
    "loginChatGptComplete",
    # System events
    "sessionConfigured",
    "deprecationNotice",
    "windows/worldWritableWarning",
    # New events
    "model/rerouted",
    "configWarning",
    "app/list/updated",
    "thread/compacted/v2",
    "serverRequest/resolved",
]


# ============================================================================
# Factory function for creating events from JSON-RPC notifications
# ============================================================================


def parse_codex_event(method: str, params: dict[str, Any] | None) -> CodexEvent | None:
    """Create a CodexEvent from a JSON-RPC notification.

    Uses the TypeAdapter with discriminator for type-safe parsing of known events.
    Returns None for legacy codex/event/* methods (duplicates of V2 events).

    Args:
        method: The JSON-RPC notification method (event type)
        params: The notification parameters (event data)

    Returns:
        A typed CodexEvent instance, or None for legacy events to skip

    Raises:
        ValueError: If the event type is unknown (add a new model for it)
    """
    # Skip legacy V1 events - they duplicate V2 events in a different format
    if method.startswith("codex/event/"):
        return None

    event_data = {"event_type": method, "data": params or {}}
    return _codex_event_adapter.validate_python(event_data)


# ============================================================================
# Type-safe delta extraction
# ============================================================================


# Type alias for all delta events
DeltaEvent = (
    AgentMessageDeltaEvent
    | PlanDeltaEvent
    | ReasoningTextDeltaEvent
    | ReasoningSummaryTextDeltaEvent
    | CommandExecutionOutputDeltaEvent
    | FileChangeOutputDeltaEvent
)


def get_text_delta(event: CodexEvent) -> str:
    """Extract text delta from a delta event.

    Type-safe extraction that only works on events with delta content.

    Args:
        event: Any CodexEvent

    Returns:
        The delta text if this is a delta event, empty string otherwise
    """
    match event:
        case (
            AgentMessageDeltaEvent(data=data)
            | PlanDeltaEvent(data=data)
            | ReasoningTextDeltaEvent(data=data)
            | ReasoningSummaryTextDeltaEvent(data=data)
            | CommandExecutionOutputDeltaEvent(data=data)
            | FileChangeOutputDeltaEvent(data=data)
        ):
            return data.delta
        case _:
            return ""


def is_delta_event(event: CodexEvent) -> bool:
    """Check if this is a delta event (streaming content)."""
    return isinstance(
        event,
        AgentMessageDeltaEvent
        | PlanDeltaEvent
        | ReasoningTextDeltaEvent
        | ReasoningSummaryTextDeltaEvent
        | CommandExecutionOutputDeltaEvent
        | FileChangeOutputDeltaEvent,
    )


def is_completed_event(event: CodexEvent) -> bool:
    """Check if this is a completion event."""
    return isinstance(
        event,
        TurnCompletedEvent
        | ItemCompletedEvent
        | RawResponseItemCompletedEvent
        | McpServerOAuthLoginCompletedEvent
        | AccountLoginCompletedEvent
        | LoginChatGptCompleteEvent,
    )


def is_error_event(event: CodexEvent) -> bool:
    """Check if this is an error event."""
    return isinstance(event, ErrorEvent | TurnErrorEvent)
